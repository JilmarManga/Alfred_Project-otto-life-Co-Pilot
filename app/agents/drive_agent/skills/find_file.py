"""find_file — list Drive files matching a name (no content fetched)."""
from app.agents.drive_agent.skill_context import SkillContext, SkillResult
from app.agents.drive_agent.skills.base import DriveSkill
from app.agents.drive_agent._shared.drive_client import (
    DriveNotConnected,
    get_drive_refresh_token,
)
from app.services import google_drive


def _file_ref(ctx: SkillContext) -> str:
    if ctx.payload.get("file_ref"):
        return ctx.payload["file_ref"]
    return getattr(ctx.parsed, "drive_file_ref", None) or ""


class FindFileSkill(DriveSkill):
    name = "find_file"

    def execute(self, ctx: SkillContext) -> SkillResult:
        try:
            token = get_drive_refresh_token(ctx.user)
        except DriveNotConnected:
            return SkillResult(success=False, error_message="drive_not_connected")

        ref = _file_ref(ctx)
        if not ref:
            return SkillResult(success=False, error_message="missing_file_ref")

        files = google_drive.search_files(token, ref)
        if not files:
            return SkillResult(
                success=False, error_message="file_not_found",
                data={"requested_name": ref},
            )
        return SkillResult(
            success=True,
            data={
                "type": "drive_find",
                "requested_name": ref,
                "files": [
                    {"name": f.get("name"), "mimeType": f.get("mimeType"),
                     "modifiedTime": f.get("modifiedTime")}
                    for f in files
                ],
            },
        )
