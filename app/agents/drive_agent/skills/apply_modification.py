"""apply_modification — the ONLY skill that writes to Drive.

Gate-only entry: dispatched by pending_drive_handler with
ctx.payload == the staged pending dict, and ONLY after the user replied with
an explicit affirmative. Before writing it re-fetches the file's
headRevisionId and aborts if it differs from the previewed revision, so a
file edited (by the user or anyone) between preview and confirmation is never
silently clobbered.
"""
import logging

from app.agents.drive_agent.skill_context import SkillContext, SkillResult
from app.agents.drive_agent.skills.base import DriveSkill
from app.agents.drive_agent._shared.drive_client import (
    DriveNotConnected,
    get_drive_refresh_token,
)
from app.services import google_drive

logger = logging.getLogger(__name__)


class ApplyModificationSkill(DriveSkill):
    name = "apply_modification"

    def execute(self, ctx: SkillContext) -> SkillResult:
        p = ctx.payload or {}
        op = p.get("op")
        file_name = p.get("file_name") or ""
        anchor_id = p.get("spreadsheet_id") or p.get("file_id")

        if not op or not anchor_id:
            return SkillResult(success=False, error_message="modify_failed")

        try:
            token = get_drive_refresh_token(ctx.user)
        except DriveNotConnected:
            return SkillResult(success=False, error_message="drive_not_connected")

        # --- Revision guard: never write over a file that moved underneath us.
        try:
            meta = google_drive.get_file_meta(token, anchor_id)
        except Exception as exc:
            logger.exception("apply_modification: meta refetch failed: %s", exc)
            return SkillResult(success=False, error_message="modify_failed")

        if meta.get("headRevisionId") != p.get("expected_revision"):
            logger.info(
                "apply_modification: revision drift on %s (expected %s, got %s) — aborting write",
                anchor_id, p.get("expected_revision"), meta.get("headRevisionId"),
            )
            return SkillResult(
                success=True,
                data={"type": "drive_modify_revision_conflict",
                      "file_name": file_name},
            )

        # --- Apply exactly the staged change.
        try:
            if op == "set_cell":
                google_drive.update_sheet_cell(
                    token, p["spreadsheet_id"], p["sheet_name"],
                    p["a1"], p["new_value"],
                )
            elif op == "replace_text":
                mime = p.get("mime_type", "")
                if mime == google_drive.GOOGLE_DOC:
                    google_drive.doc_replace_text(
                        token, p["file_id"], p["find"], p.get("replace", ""),
                    )
                else:
                    google_drive.overwrite_text_file(
                        token, p["file_id"], p["new_content"], mime,
                    )
            elif op == "append_text":
                mime = p.get("mime_type", "")
                if mime == google_drive.GOOGLE_DOC:
                    google_drive.doc_append_text(
                        token, p["file_id"], p["appended"],
                    )
                else:
                    google_drive.overwrite_text_file(
                        token, p["file_id"], p["new_content"], mime,
                    )
            else:
                return SkillResult(success=False, error_message="modify_failed")
        except Exception as exc:
            logger.exception("apply_modification: write failed on %s: %s",
                             anchor_id, exc)
            return SkillResult(success=False, error_message="modify_failed")

        return SkillResult(
            success=True,
            data={"type": "drive_modify_applied", "file_name": file_name},
        )
