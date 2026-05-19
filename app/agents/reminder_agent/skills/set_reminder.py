import logging
from datetime import datetime, timezone

from app.agents.reminder_agent._shared.time_resolver import resolve_fire_at
from app.agents.reminder_agent.skill_context import SkillContext, SkillResult
from app.agents.reminder_agent.skills.base import ReminderSkill
from app.db.user_context_store import update_user_context
from app.repositories.user_reminder_repository import UserReminderRepository

logger = logging.getLogger(__name__)


class SetReminderSkill(ReminderSkill):
    """Create a personal reminder.

    Three outcomes:
    - ambiguous reminder-vs-event (and not force_set) → stash
      `pending_reminder.awaiting_reminder_or_event`, return `reminder_or_event`.
    - no time-of-day resolvable → stash
      `pending_reminder.awaiting_time_of_day`, return `reminder_need_time`.
    - resolved → persist (status="scheduled"), return `reminder_set`.

    Gate bypass: payload values win over `ctx.parsed` (SaveToListSkill
    convention) so the gate can finish the create single-path.
    """

    name = "set_reminder"

    def execute(self, ctx: SkillContext) -> SkillResult:
        phone = (ctx.user or {}).get("phone_number")
        if not phone:
            logger.error("SetReminderSkill: missing phone_number in user dict")
            return SkillResult(success=False, error_message="reminder_create_failed")

        parsed = ctx.parsed
        payload = ctx.payload or {}
        lang = (ctx.user or {}).get("language", "es")
        tz_name = (ctx.user or {}).get("timezone") or "UTC"

        def pick(key, parsed_attr):
            if key in payload:
                return payload.get(key)
            return getattr(parsed, parsed_attr) if parsed else None

        reminder_text = pick("reminder_text", "reminder_text")
        reminder_time = pick("reminder_time", "reminder_time")
        reminder_period = pick("reminder_period", "reminder_period")
        force_set = bool(payload.get("force_set"))
        ambiguous = bool(payload.get("ambiguous"))

        if not reminder_text or not str(reminder_text).strip():
            return SkillResult(success=False, error_message="reminder_missing_text")
        reminder_text = str(reminder_text).strip()

        now_utc = datetime.now(timezone.utc)

        # Reminder vs calendar-event ambiguity → confirm before doing anything.
        if ambiguous and not force_set:
            update_user_context(phone, "pending_reminder", {
                "step": "awaiting_reminder_or_event",
                "original_parsed": parsed,
                "reminder_text": reminder_text,
                "created_at": now_utc.isoformat(),
            })
            return SkillResult(
                success=True,
                data={"type": "reminder_or_event", "reminder_text": reminder_text},
            )

        fire_at_iso, status = resolve_fire_at(
            reminder_time=reminder_time,
            reminder_period=reminder_period,
            tz_name=tz_name,
            now_utc=now_utc,
        )

        if status == "needs_time_of_day":
            update_user_context(phone, "pending_reminder", {
                "step": "awaiting_time_of_day",
                "reminder_text": reminder_text,
                "reminder_time": reminder_time,  # date carrier (may be None)
                "created_at": now_utc.isoformat(),
            })
            return SkillResult(
                success=True,
                data={"type": "reminder_need_time", "reminder_text": reminder_text},
            )

        try:
            UserReminderRepository.create(
                user_phone_number=phone,
                reminder_text=reminder_text,
                fire_at_iso=fire_at_iso,
                lang=lang,
                tz=tz_name,
            )
        except Exception as exc:
            logger.exception("SetReminderSkill: create failed: %s", exc)
            return SkillResult(success=False, error_message="reminder_create_failed")

        return SkillResult(
            success=True,
            data={
                "type": "reminder_set",
                "reminder_text": reminder_text,
                "fire_at": fire_at_iso,
            },
        )
