"""analyze_file — fetch content + carry the user's question for the Layer-4
LLM to answer/summarize. The skill itself never calls an LLM."""
from app.agents.drive_agent.skill_context import SkillContext, SkillResult
from app.agents.drive_agent.skills.base import DriveSkill
from app.agents.drive_agent._shared.drive_client import (
    DriveNotConnected,
    get_drive_refresh_token,
    resolve_file,
)
from app.agents.drive_agent._shared.query_resolver import (
    best_header_guess,
    resolve_query,
    validate_query_spec,
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
                    # Carry the query spec so the re-dispatch after the user
                    # picks a file stays on the deterministic path (the
                    # re-dispatch passes parsed=None).
                    "query_spec": (
                        getattr(ctx.parsed, "drive_query", None)
                        if ctx.parsed else None
                    ),
                    "candidates": [
                        {"id": f["id"], "name": f.get("name"),
                         "mimeType": f.get("mimeType")}
                        for f in files
                    ],
                },
            )

        f = files[0]
        mime = f.get("mimeType", "")

        # Structured tabular query → deterministic path. The LLM (Layer 1)
        # only produced the spec; row selection here is arithmetic, never an
        # LLM judgement call, so it can never drop/sample/reorder rows.
        # payload wins: a gate re-dispatch (file-choice/file-ref) passes the
        # spec here because it has no ParsedMessage.
        spec = ctx.payload.get("query_spec") if ctx.payload else None
        if spec is None and ctx.parsed is not None:
            spec = getattr(ctx.parsed, "drive_query", None)
        if spec:
            grid = google_drive.get_grid(token, f["id"], mime)
            if grid is not None:  # tabular file — resolve deterministically
                spec_error = validate_query_spec(spec)
                if spec_error:
                    return SkillResult(success=False, error_message=spec_error)
                resolved = resolve_query(grid, spec)
                if not resolved.get("ok"):
                    err = resolved.get("error", "invalid_query_spec")
                    # Tier 2: a column the user named couldn't be uniquely
                    # resolved (after Tier 0). Never dead-end — ask ONE
                    # concrete question listing the file's REAL headers and
                    # let the pending-drive gate re-run the deterministic
                    # engine on the corrected spec.
                    if err == "query_column_not_found":
                        headers = (
                            [str(h).strip() for h in grid[0]]
                            if grid and grid[0] else []
                        )
                        failed = resolved.get("detail") or ""
                        return SkillResult(
                            success=True,
                            data={
                                "type": "drive_clarify_column",
                                "file_ref": f.get("name"),
                                "failed_column": failed,
                                "headers": headers,
                                "suggested_header": best_header_guess(
                                    headers, failed),
                                "query_spec": spec,
                                "question": ctx.inbound_text,
                            },
                        )
                    return SkillResult(
                        success=False,
                        error_message=err,
                        data={"detail": resolved.get("detail")},
                    )
                return SkillResult(
                    success=True,
                    data={
                        "type": "drive_query_result",
                        "file_name": f.get("name"),
                        "result": resolved,
                        "question": ctx.inbound_text,
                    },
                )
            # Non-tabular file (a Doc/prose) — fall through to free analyze.

        content = google_drive.get_content(token, f["id"], mime)
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
