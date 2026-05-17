"""Google Drive API service.

Mirrors `google_calendar.py`'s per-user-refresh-token credential pattern.
Reads use Drive export (Docs→text, Sheets→CSV) so analysis works without the
Sheets/Docs APIs; structured edits (Phase 3+) add those APIs on the same
`drive` scope, which Google accepts for Sheets and Docs.

`DriveTokenInvalid` is the Drive analogue of `CalendarTokenInvalid` — raised
when the stored refresh token can no longer be exchanged (revoked/expired) so
callers can clear it and send a reconnect link.
"""
import io
import logging
import os
from typing import List, Optional, Tuple

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"

# A read/analyze cap so we never hand an unbounded blob to the Layer-4 LLM.
MAX_CONTENT_CHARS = 12000


class DriveTokenInvalid(Exception):
    """Stored Drive refresh token can no longer be exchanged for an access
    token (revoked, expired, scope change). Callers should clear the token
    and route the user through the Drive reconnect flow."""


def _credentials(refresh_token: str) -> Credentials:
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
        scopes=SCOPES,
    )


def get_drive_service_for_user(refresh_token: str):
    creds = _credentials(refresh_token)
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        raise DriveTokenInvalid(str(exc)) from exc
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_sheets_service_for_user(refresh_token: str):
    creds = _credentials(refresh_token)
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        raise DriveTokenInvalid(str(exc)) from exc
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_docs_service_for_user(refresh_token: str):
    creds = _credentials(refresh_token)
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        raise DriveTokenInvalid(str(exc)) from exc
    return build("docs", "v1", credentials=creds, cache_discovery=False)


def search_files(refresh_token: str, name_query: str, limit: int = 10) -> List[dict]:
    """Find non-trashed files whose name contains `name_query` (case-insensitive
    in Drive). Returns [{id, name, mimeType, modifiedTime}] newest first."""
    service = get_drive_service_for_user(refresh_token)
    safe = (name_query or "").replace("'", "\\'").strip()
    if not safe:
        return []
    q = f"name contains '{safe}' and trashed = false"
    try:
        resp = service.files().list(
            q=q,
            spaces="drive",
            fields="files(id,name,mimeType,modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=limit,
        ).execute()
    except HttpError as exc:
        logger.exception("Drive search failed: %s", exc)
        raise
    return resp.get("files", [])


def get_file_meta(refresh_token: str, file_id: str) -> dict:
    """File metadata including `headRevisionId` + `modifiedTime` — the
    optimistic-concurrency anchors used before any write."""
    service = get_drive_service_for_user(refresh_token)
    return service.files().get(
        fileId=file_id,
        fields="id,name,mimeType,modifiedTime,headRevisionId",
    ).execute()


def _export_text(service, file_id: str, mime: str) -> str:
    buf = io.BytesIO()
    request = service.files().export_media(fileId=file_id, mimeType=mime)
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8", errors="replace")


def _download_text(service, file_id: str) -> str:
    buf = io.BytesIO()
    request = service.files().get_media(fileId=file_id)
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8", errors="replace")


def list_sheet_tabs(refresh_token: str, spreadsheet_id: str) -> List[str]:
    """Tab/sheet titles in a spreadsheet, in order."""
    service = get_sheets_service_for_user(refresh_token)
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets(properties(title))",
    ).execute()
    return [s["properties"]["title"] for s in meta.get("sheets", [])]


def read_sheet_values(
    refresh_token: str, spreadsheet_id: str, sheet_name: Optional[str] = None,
) -> Tuple[str, List[List[str]]]:
    """Return (resolved_sheet_name, 2-D string grid) for a tab. Defaults to the
    first tab. Cells come back as displayed strings (FORMATTED_VALUE)."""
    tabs = list_sheet_tabs(refresh_token, spreadsheet_id)
    if not tabs:
        return "", []
    target = sheet_name if sheet_name in tabs else tabs[0]
    service = get_sheets_service_for_user(refresh_token)
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=target,
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return target, resp.get("values", [])


def get_content(refresh_token: str, file_id: str, mime_type: str) -> Optional[str]:
    """Return a text rendering of the file for reading/analysis:

      - Google Doc   → exported text/plain
      - Google Sheet → exported text/csv
      - text/*       → raw download

    Returns None for unsupported binary types (image/PDF/etc.) — the caller
    surfaces an `unsupported_file_type` message. Truncated to
    MAX_CONTENT_CHARS so the Layer-4 LLM never gets an unbounded blob.
    """
    service = get_drive_service_for_user(refresh_token)
    if mime_type == GOOGLE_DOC:
        text = _export_text(service, file_id, "text/plain")
    elif mime_type == GOOGLE_SHEET:
        text = _export_text(service, file_id, "text/csv")
    elif mime_type.startswith("text/"):
        text = _download_text(service, file_id)
    else:
        return None
    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS] + "\n…(truncated)"
    return text


# --------------------------------------------------------------------------- #
# Writers. Only ever reached from apply_modification, which only runs after the #
# pending-drive gate received an explicit user confirmation AND the            #
# headRevisionId still matches the previewed revision.                          #
# --------------------------------------------------------------------------- #

def update_sheet_cell(
    refresh_token: str, spreadsheet_id: str, sheet_name: str, a1: str, value: str,
) -> None:
    service = get_sheets_service_for_user(refresh_token)
    rng = f"{sheet_name}!{a1}"
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=rng,
        valueInputOption="USER_ENTERED",
        body={"values": [[value]]},
    ).execute()


def doc_replace_text(refresh_token: str, file_id: str, find: str, replace: str) -> None:
    """Replace text in a Google Doc. propose_modification already verified
    exactly one occurrence in the exported text, so replaceAllText is safe and
    deterministic here."""
    service = get_docs_service_for_user(refresh_token)
    service.documents().batchUpdate(
        documentId=file_id,
        body={"requests": [{
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": True},
                "replaceText": replace,
            }
        }]},
    ).execute()


def doc_append_text(refresh_token: str, file_id: str, text: str) -> None:
    service = get_docs_service_for_user(refresh_token)
    doc = service.documents().get(documentId=file_id).execute()
    content = doc.get("body", {}).get("content", [])
    end_index = 1
    if content:
        end_index = content[-1].get("endIndex", 2) - 1
    service.documents().batchUpdate(
        documentId=file_id,
        body={"requests": [{
            "insertText": {"location": {"index": end_index}, "text": "\n" + text}
        }]},
    ).execute()


def overwrite_text_file(
    refresh_token: str, file_id: str, new_content: str, mime_type: str,
) -> None:
    service = get_drive_service_for_user(refresh_token)
    media = MediaIoBaseUpload(
        io.BytesIO(new_content.encode("utf-8")),
        mimetype=mime_type or "text/plain",
        resumable=False,
    )
    service.files().update(fileId=file_id, media_body=media).execute()
