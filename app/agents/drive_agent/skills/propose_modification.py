"""propose_modification — resolve an explicit edit into a concrete, previewable
change and STAGE it. Never writes. The write only happens in
apply_modification, dispatched by the pending-drive gate after the user
explicitly confirms.
"""
import logging
from datetime import datetime, timezone

from app.agents.drive_agent.skill_context import SkillContext, SkillResult
from app.agents.drive_agent.skills.base import DriveSkill
from app.agents.drive_agent._shared.drive_client import (
    DriveNotConnected,
    get_drive_refresh_token,
    resolve_file,
)
from app.agents.drive_agent._shared.edit_resolver import (
    resolve_sheet_edit,
    resolve_text_edit,
    validate_edit_spec,
)
from app.db.user_context_store import update_user_context
from app.services import google_drive

logger = logging.getLogger(__name__)

_TEXT_OPS = {"replace_text", "append_text"}


def _file_ref(ctx: SkillContext) -> str:
    if ctx.payload.get("file_ref"):
        return ctx.payload["file_ref"]
    return getattr(ctx.parsed, "drive_file_ref", None) or ""


def _edit_spec(ctx: SkillContext) -> dict:
    if ctx.payload.get("edit_spec"):
        return ctx.payload["edit_spec"]
    return getattr(ctx.parsed, "drive_edit", None) or {}


class ProposeModificationSkill(DriveSkill):
    name = "propose_modification"

    def execute(self, ctx: SkillContext) -> SkillResult:
        phone = (ctx.user or {}).get("phone_number") or (ctx.user or {}).get("phone")
        if not phone:
            return SkillResult(success=False, error_message="modify_failed")

        try:
            token = get_drive_refresh_token(ctx.user)
        except DriveNotConnected:
            return SkillResult(success=False, error_message="drive_not_connected")

        spec = _edit_spec(ctx)
        spec_err = validate_edit_spec(spec)
        if spec_err:
            return SkillResult(success=False, error_message=spec_err)

        ref = _file_ref(ctx)
        if not ref:
            return SkillResult(success=False, error_message="missing_file_ref")

        status, files = resolve_file(token, ref)
        if status == "not_found":
            return SkillResult(success=False, error_message="file_not_found",
                               data={"requested_name": ref})
        if status == "ambiguous":
            # Reuse the same disambiguation UX as read/analyze; the gate
            # re-dispatches propose_modification with the chosen file + spec.
            return SkillResult(
                success=True,
                data={
                    "type": "drive_file_choice",
                    "intent": "modify",
                    "requested_name": ref,
                    "edit_spec": spec,
                    "candidates": [
                        {"id": f["id"], "name": f.get("name"),
                         "mimeType": f.get("mimeType")}
                        for f in files
                    ],
                },
            )

        f = files[0]
        file_id = f["id"]
        mime = f.get("mimeType", "")
        op = spec["op"]

        # Op ↔ file-type compatibility (deterministic).
        if op == "set_cell" and mime != google_drive.GOOGLE_SHEET:
            return SkillResult(success=False, error_message="edit_unsupported_for_type",
                               data={"file_name": f.get("name")})
        if op in _TEXT_OPS and not (
            mime == google_drive.GOOGLE_DOC or mime.startswith("text/")
        ):
            return SkillResult(success=False, error_message="edit_unsupported_for_type",
                               data={"file_name": f.get("name")})

        meta = google_drive.get_file_meta(token, file_id)
        expected_revision = meta.get("headRevisionId")

        if op == "set_cell":
            sheet_name, values = google_drive.read_sheet_values(token, file_id)
            resolved = resolve_sheet_edit(values, spec)
            if not resolved.get("ok"):
                return SkillResult(success=False,
                                   error_message=resolved["error"],
                                   data={"detail": resolved.get("detail"),
                                         "file_name": f.get("name")})
            stash = {
                "op": "set_cell",
                "spreadsheet_id": file_id,
                "sheet_name": sheet_name,
                "a1": resolved["a1"],
                "new_value": resolved["new_value"],
            }
            preview = {
                "change_kind": "cell",
                "location": f"{resolved['locator_label']} → {resolved['target_column']}",
                "old_value": resolved["old_value"],
                "new_value": resolved["new_value"],
            }
        else:
            content = google_drive.get_content(token, file_id, mime)
            if content is None:
                return SkillResult(success=False,
                                   error_message="unsupported_file_type",
                                   data={"file_name": f.get("name")})
            resolved = resolve_text_edit(content, spec)
            if not resolved.get("ok"):
                return SkillResult(success=False,
                                   error_message=resolved["error"],
                                   data={"detail": resolved.get("detail"),
                                         "file_name": f.get("name")})
            stash = {
                "op": resolved["op"],
                "file_id": file_id,
                "mime_type": mime,
                "new_content": resolved["new_content"],
                "find": resolved.get("old_snippet"),
                "replace": resolved.get("new_snippet"),
                "appended": resolved.get("appended"),
            }
            if resolved["op"] == "append_text":
                preview = {"change_kind": "append",
                           "old_value": "", "new_value": resolved["appended"]}
            else:
                preview = {"change_kind": "replace",
                           "old_value": resolved["old_snippet"],
                           "new_value": resolved["new_snippet"]}

        pending = {
            "step": "awaiting_modify_confirmation",
            "file_id": file_id,
            "file_name": f.get("name"),
            "mime_type": mime,
            "expected_revision": expected_revision,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **stash,
        }
        update_user_context(phone, "pending_drive", pending)

        return SkillResult(
            success=True,
            data={
                "type": "drive_modify_preview",
                "file_name": f.get("name"),
                **preview,
            },
        )
