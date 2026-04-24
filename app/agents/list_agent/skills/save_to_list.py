import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app.agents.list_agent.skill_context import SkillContext, SkillResult
from app.agents.list_agent.skills.base import ListSkill
from app.db.user_context_store import update_user_context
from app.repositories.list_repository import ListRepository

logger = logging.getLogger(__name__)

_DEDUP_WINDOW = timedelta(minutes=10)
_PREVIEW_MAX_CHARS = 60


def _compute_dedup_key(content: str) -> str:
    return hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()


def _content_preview(content: str) -> str:
    if len(content) <= _PREVIEW_MAX_CHARS:
        return content
    return content[: _PREVIEW_MAX_CHARS - 3] + "..."


def _clean_label(raw) -> str | None:
    if raw is None:
        return None
    stripped = str(raw).strip()
    return stripped or None


class SaveToListSkill(ListSkill):
    """Persist an item to a named list.

    Resolves the target list (explicit name, auto-created, or asks the user when
    2–3 lists exist and none was named), enforces the 3-list cap, and applies a
    10-minute sha256 dedup window to suppress retry-loop duplicates.

    Gate bypass: the `_choice` gate step calls `run_skill("save_to_list", ctx)`
    with `ctx.payload = {"resolved_list_name": ..., "item": ..., "label": ...}`
    — payload values win over `ctx.parsed` fields so the skill stays single-path.
    """

    name = "save_to_list"

    def execute(self, ctx: SkillContext) -> SkillResult:
        phone = (ctx.user or {}).get("phone_number")
        if not phone:
            logger.error("SaveToListSkill: missing phone_number in user dict")
            return SkillResult(success=False, error_message="save_failed")

        parsed = ctx.parsed
        lang = (ctx.user or {}).get("language", "es")
        payload = ctx.payload or {}

        # Payload takes precedence (gate bypass path); parsed is the router path.
        resolved_name = payload.get("resolved_list_name") or (parsed.list_name if parsed else None)
        item = payload.get("item") or (parsed.list_item if parsed else None)
        label = _clean_label(payload.get("label") if "label" in payload else (parsed.list_label if parsed else None))

        if not item:
            return SkillResult(success=False, error_message="missing_item")

        existing_lists = ListRepository.get_user_lists(phone)
        existing_names = [lst.get("name") for lst in existing_lists if lst.get("name")]

        target = self._resolve_target(
            phone=phone,
            lang=lang,
            resolved_name=resolved_name,
            existing_lists=existing_lists,
            item=item,
            label=label,
        )
        if isinstance(target, SkillResult):
            # Short-circuit: cap reached, choice needed, or auto-create failed.
            return target

        now_utc = datetime.now(timezone.utc)
        dedup_key = _compute_dedup_key(item)

        if self._is_duplicate(target, dedup_key, now_utc):
            return SkillResult(
                success=True,
                data={
                    "type": "list_saved_deduped",
                    "list_name": target.get("name"),
                    "label": label,
                },
            )

        new_item = {
            "content": item,
            "label": label,
            "created_at": now_utc.isoformat(),
            "dedup_key": dedup_key,
        }
        ok = ListRepository.append_item(target["id"], new_item)
        if not ok:
            return SkillResult(
                success=False,
                error_message="save_failed",
                data={"existing_names": existing_names},
            )

        return SkillResult(
            success=True,
            data={
                "type": "list_saved",
                "list_name": target.get("name"),
                "content_preview": _content_preview(item),
                "label": label,
            },
        )

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _resolve_target(
        self,
        *,
        phone: str,
        lang: str,
        resolved_name,
        existing_lists: list,
        item: str,
        label,
    ):
        """Return the target list dict, or a short-circuit SkillResult.

        Short-circuits:
        - `list_cap_reached` — creating a new list would exceed 3 per user
        - `list_choice_request` — 2–3 lists exist and none was named
        - `save_failed` — auto/new-list creation hit a Firestore error
        """
        if resolved_name:
            key = resolved_name.strip().lower()
            match = next(
                (lst for lst in existing_lists if (lst.get("name_lower") or "") == key),
                None,
            )
            if match:
                return match

            # New-list creation — cap check.
            if len(existing_lists) >= 3:
                return SkillResult(
                    success=False,
                    error_message="list_cap_reached",
                    data={
                        "existing_names": [lst.get("name") for lst in existing_lists if lst.get("name")],
                        "requested_name": resolved_name,
                    },
                )
            new_id = ListRepository.create_list(phone, resolved_name)
            if not new_id:
                return SkillResult(success=False, error_message="save_failed")
            return {
                "id": new_id,
                "name": resolved_name,
                "name_lower": key,
                "items": [],
            }

        # No explicit name — pick by cardinality.
        n = len(existing_lists)
        if n == 0:
            default = "guardados" if lang == "es" else "saved"
            new_id = ListRepository.create_list(phone, default)
            if not new_id:
                return SkillResult(success=False, error_message="save_failed")
            return {
                "id": new_id,
                "name": default,
                "name_lower": default,
                "items": [],
            }
        if n == 1:
            return existing_lists[0]

        # 2 or 3 lists → ask the user. Stash the in-flight item so the gate can
        # finish the save once the user picks a list.
        list_names = [lst.get("name") for lst in existing_lists if lst.get("name")]
        update_user_context(phone, "pending_list", {
            "step": "_choice",
            "item": item,
            "label": label,
            "list_names": list_names,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        return SkillResult(
            success=True,
            data={
                "type": "list_choice_request",
                "list_names": list_names,
                "item": item,
                "label": label,
            },
        )

    def _is_duplicate(self, target: dict, dedup_key: str, now_utc: datetime) -> bool:
        """Return True if an item with the same dedup_key was saved in the last 10 min."""
        window_start = now_utc - _DEDUP_WINDOW
        for existing_item in target.get("items") or []:
            if existing_item.get("dedup_key") != dedup_key:
                continue
            created_raw = existing_item.get("created_at")
            if not created_raw:
                continue
            try:
                created_dt = datetime.fromisoformat(created_raw)
            except (ValueError, TypeError):
                continue
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            if created_dt >= window_start:
                return True
        return False
