"""analyze_file — fetch content + carry the user's question for the Layer-4
LLM to answer/summarize. The skill itself never calls an LLM."""
from app.agents.drive_agent.skill_context import SkillContext, SkillResult
from app.agents.drive_agent.skills.base import DriveSkill
from app.agents.drive_agent._shared.drive_client import (
    DriveNotConnected,
    get_drive_refresh_token,
    resolve_file,
)
from app.services import google_drive


def _file_ref(ctx: SkillContext) -> str:
    if ctx.payload.get("file_ref"):
        return ctx.payload["file_ref"]
    return getattr(ctx.parsed, "drive_file_ref", None) or ""


class AnalyzeFileSkill(DriveSkill):
    name = "analyze_file"

    def execute(self, ctx: SkillContext) -> SkillResult:
        try:
            token = get_drive_refresh_token(ctx.user)
        except DriveNotConnected:
            return SkillResult(success=False, error_message="drive_not_connected")

        ref = _file_ref(ctx)
        if not ref:
            return SkillResult(success=False, error_message="missing_file_ref")

        status, files = resolve_file(token, ref)
        if status == "not_found":
            return SkillResult(
                success=False, error_message="file_not_found",
                data={"requested_name": ref},
            )
        if status == "ambiguous":
            return SkillResult(
                success=True,
                data={
                    "type": "drive_file_choice",
                    "intent": "analyze",
                    "requested_name": ref,
                    "candidates": [
                        {"id": f["id"], "name": f.get("name"),
                         "mimeType": f.get("mimeType")}
                        for f in files
                    ],
                },
            )

        f = files[0]
        content = google_drive.get_content(token, f["id"], f.get("mimeType", ""))
        if content is None:
            return SkillResult(
                success=False, error_message="unsupported_file_type",
                data={"file_name": f.get("name")},
            )
        return SkillResult(
            success=True,
            data={
                "type": "drive_analyze",
                "file_name": f.get("name"),
                "mime_type": f.get("mimeType"),
                "content": content,
                "question": ctx.inbound_text,
            },
        )
