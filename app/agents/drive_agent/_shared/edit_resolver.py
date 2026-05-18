"""Deterministic resolution of a structured edit spec against file content.

This is the heart of the safety model: the LLM only ever produces the spec
(Layer 1). Everything here is pure, deterministic Python — it never invents
content, never guesses, and refuses (no_match / multiple_matches) instead of
picking when a locator is not unambiguous. Shared by propose_modification
(preview) and apply_modification (re-validate before write).
"""
from typing import Any, Dict, List, Optional

_ALLOWED_OPS = {"set_cell", "replace_text", "append_text"}


def _norm(v: Any) -> str:
    return str(v if v is not None else "").strip().lower()


def col_letter(idx: int) -> str:
    """0 → A, 25 → Z, 26 → AA (spreadsheet column letters)."""
    s = ""
    n = idx
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def validate_edit_spec(spec: Optional[dict]) -> Optional[str]:
    """Return an error code if the spec is structurally invalid, else None.
    Never trust the LLM's spec shape — this gate runs before anything reads
    the file."""
    if not isinstance(spec, dict):
        return "invalid_edit_spec"
    op = spec.get("op")
    if op not in _ALLOWED_OPS:
        return "invalid_edit_spec"
    if op == "set_cell":
        for k in ("locator_column", "locator_value", "target_column"):
            if not str(spec.get(k) or "").strip():
                return "invalid_edit_spec"
        if "new_value" not in spec:
            return "invalid_edit_spec"
    elif op == "replace_text":
        if not str(spec.get("find") or "").strip():
            return "invalid_edit_spec"
        if "replace" not in spec:
            return "invalid_edit_spec"
    elif op == "append_text":
        if not str(spec.get("text") or "").strip():
            return "invalid_edit_spec"
    return None


def resolve_sheet_edit(values: List[List[str]], spec: dict) -> Dict[str, Any]:
    """Resolve a set_cell spec against a sheet grid (row 0 = headers).

    Returns {"ok": True, ...} with the exact target cell, or
    {"ok": False, "error": <code>, "detail": ...}. Codes:
    edit_no_headers / edit_column_not_found / edit_no_match /
    edit_multiple_matches.
    """
    if not values:
        return {"ok": False, "error": "edit_no_headers"}
    headers = [_norm(h) for h in values[0]]
    loc_key = _norm(spec["locator_column"])
    tgt_key = _norm(spec["target_column"])
    try:
        loc_idx = headers.index(loc_key)
    except ValueError:
        return {"ok": False, "error": "edit_column_not_found",
                "detail": spec["locator_column"]}
    try:
        tgt_idx = headers.index(tgt_key)
    except ValueError:
        return {"ok": False, "error": "edit_column_not_found",
                "detail": spec["target_column"]}

    want = _norm(spec["locator_value"])
    matches: List[int] = []
    for r in range(1, len(values)):
        row = values[r]
        cell = row[loc_idx] if loc_idx < len(row) else ""
        if _norm(cell) == want:
            matches.append(r)

    if not matches:
        return {"ok": False, "error": "edit_no_match",
                "detail": str(spec["locator_value"])}
    if len(matches) > 1:
        return {"ok": False, "error": "edit_multiple_matches",
                "detail": len(matches)}

    r = matches[0]
    row = values[r]
    old_value = row[tgt_idx] if tgt_idx < len(row) else ""
    new_value = str(spec["new_value"])
    sheet_row_number = r + 1  # 1-based; header occupies row 1
    return {
        "ok": True,
        "op": "set_cell",
        "a1_row": sheet_row_number,
        "a1_col": col_letter(tgt_idx),
        "a1": f"{col_letter(tgt_idx)}{sheet_row_number}",
        "old_value": old_value,
        "new_value": new_value,
        "locator_label": f"{spec['locator_column']} = {spec['locator_value']}",
        "target_column": spec["target_column"],
    }


def resolve_text_edit(content: str, spec: dict) -> Dict[str, Any]:
    """Resolve a replace_text / append_text spec against text content.

    replace_text requires EXACTLY one occurrence (deterministic; refuses on
    0 or >1). Returns the full proposed `new_content` for the writer.
    """
    op = spec["op"]
    if op == "append_text":
        text = str(spec["text"])
        sep = "" if (not content or content.endswith("\n")) else "\n"
        return {
            "ok": True,
            "op": "append_text",
            "appended": text,
            "new_content": content + sep + text,
        }

    # replace_text
    find = str(spec["find"])
    replace = str(spec.get("replace", ""))
    count = content.count(find)
    if count == 0:
        return {"ok": False, "error": "edit_no_match", "detail": find}
    if count > 1:
        return {"ok": False, "error": "edit_multiple_matches", "detail": count}
    return {
        "ok": True,
        "op": "replace_text",
        "old_snippet": find,
        "new_snippet": replace,
        "new_content": content.replace(find, replace, 1),
    }
