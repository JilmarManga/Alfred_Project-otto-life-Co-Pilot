"""Deterministic resolution of a structured tabular query against sheet data.

Read-side analogue of `edit_resolver.py`. The LLM (Layer 1) only ever produces
the query spec; everything here is pure, deterministic Python. It NEVER drops,
summarizes, samples, or reorders rows, and it refuses with an explicit error
code instead of guessing when a column cannot be resolved or a date filter is
unparseable. This is what makes a money/business-critical Drive query reliable:
row selection is arithmetic, not an LLM judgement call.

Consumed by analyze_file (Phase 3); rendered completely by Layer 4 (Phase 4).
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional

_ALLOWED_OPS = {"eq", "contains", "date_eq"}

_SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    # English, so an EN-language user's value still resolves.
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _fold(v: Any) -> str:
    """Lower-case, accent-stripped, trimmed — for header/value matching.
    Spanish data has accents ('Conciliación', 'Jurídica'); folding makes
    matching robust without changing what is displayed back to the user."""
    s = str(v if v is not None else "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def col_letter(idx: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA. Same helper shape as edit_resolver."""
    s, n = "", idx
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def _parse_date(s: Any):
    """Return (day, month, year_or_None) or None if unparseable.

    Handles the formats Colombian users and Sheets actually produce:
    dd/mm/yyyy, dd/mm/yy, yyyy-mm-dd, and Spanish/English '19 de mayo
    [de 2026]' / '19 mayo 2026'. A bare day+month with no year yields
    year=None so '19 de mayo' matches a '19/05/2026' cell.
    """
    txt = _fold(s)
    if not txt:
        return None

    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", txt)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        return (d, mo, y) if 1 <= mo <= 12 and 1 <= d <= 31 else None

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", txt)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (d, mo, y) if 1 <= mo <= 12 and 1 <= d <= 31 else None

    # "19 de mayo", "19 de mayo de 2026", "19 mayo 2026"
    m = re.search(
        r"\b(\d{1,2})\s+(?:de\s+)?([a-z]+)(?:\s+(?:de\s+)?(\d{4}))?\b", txt
    )
    if m and m.group(2) in _SPANISH_MONTHS:
        d = int(m.group(1))
        mo = _SPANISH_MONTHS[m.group(2)]
        y = int(m.group(3)) if m.group(3) else None
        return (d, mo, y) if 1 <= d <= 31 else None

    return None


def _to_number(v: Any) -> Optional[float]:
    """Best-effort numeric parse for sum aggregation: strips currency symbols
    and thousands separators ('$ 2.500.000' / '1,250.50' -> float)."""
    s = str(v if v is not None else "").strip()
    if not s:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None
    # Decide decimal separator: if both present, the rightmost is decimal.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Lone comma: decimal if it looks like ',dd', else thousands sep.
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif "." in s:
        # Lone dot(s): a single dot with 1–2 trailing digits is a decimal
        # ('19.99'); anything else ('1.000.000', '1.000') is Latin thousands.
        if not (s.count(".") == 1 and re.search(r"\.\d{1,2}$", s)):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def validate_query_spec(spec: Optional[dict]) -> Optional[str]:
    """Return an error code if the spec is structurally invalid, else None.
    Never trust the LLM's spec shape — runs before the file is touched."""
    if not isinstance(spec, dict):
        return "invalid_query_spec"
    filters = spec.get("filters")
    if filters is not None:
        if not isinstance(filters, list):
            return "invalid_query_spec"
        for f in filters:
            if not isinstance(f, dict):
                return "invalid_query_spec"
            if not str(f.get("column") or "").strip():
                return "invalid_query_spec"
            if f.get("op") not in _ALLOWED_OPS:
                return "invalid_query_spec"
            if "value" not in f:
                return "invalid_query_spec"
    agg = spec.get("aggregate")
    if agg is not None and agg != "count" and not (
        isinstance(agg, str) and agg.startswith("sum:") and agg[4:].strip()
    ):
        return "invalid_query_spec"
    sel = spec.get("select")
    if sel is not None and not isinstance(sel, list):
        return "invalid_query_spec"
    # group_by / sort are optional scalars; resolved against headers below.
    return None


def _resolve_header(headers_folded: List[str], name: Any) -> int:
    """Index of `name` in headers (accent/case-insensitive). -1 if absent.
    Falls back to a unique substring match so 'cliente' resolves a
    'Nombre del Cliente' header — but only when exactly one header
    contains it (ambiguity is refused, never guessed)."""
    key = _fold(name)
    if key in headers_folded:
        return headers_folded.index(key)
    hits = [i for i, h in enumerate(headers_folded) if key and key in h]
    return hits[0] if len(hits) == 1 else -1


def resolve_query(values: List[List[str]], spec: dict) -> Dict[str, Any]:
    """Resolve a query spec against a sheet grid (row 0 = headers).

    Returns {"ok": True, ...} with the COMPLETE result — every matching row,
    every group, in sheet order — or {"ok": False, "error": <code>, ...}.
    Error codes: query_no_headers / query_column_not_found / query_bad_date /
    query_no_rows.
    """
    if not values or not values[0]:
        return {"ok": False, "error": "query_no_headers"}

    display_headers = [str(h).strip() for h in values[0]]
    headers_folded = [_fold(h) for h in display_headers]
    ncols = len(display_headers)

    # Resolve every column the spec references up front; refuse on miss.
    def _col(name: Any) -> Any:
        idx = _resolve_header(headers_folded, name)
        return idx if idx >= 0 else None

    filters = spec.get("filters") or []
    resolved_filters = []
    for f in filters:
        idx = _col(f["column"])
        if idx is None:
            return {"ok": False, "error": "query_column_not_found",
                    "detail": str(f["column"])}
        op = f["op"]
        val = f["value"]
        if op == "date_eq":
            want = _parse_date(val)
            if want is None:
                return {"ok": False, "error": "query_bad_date",
                        "detail": str(val)}
            resolved_filters.append(("date_eq", idx, want))
        elif op == "contains":
            resolved_filters.append(("contains", idx, _fold(val)))
        else:  # eq
            resolved_filters.append(("eq", idx, _fold(val)))

    group_by = spec.get("group_by")
    group_idx = None
    if group_by:
        group_idx = _col(group_by)
        if group_idx is None:
            return {"ok": False, "error": "query_column_not_found",
                    "detail": str(group_by)}

    select = spec.get("select")
    if select:
        sel_idx = []
        for name in select:
            idx = _col(name)
            if idx is None:
                return {"ok": False, "error": "query_column_not_found",
                        "detail": str(name)}
            sel_idx.append(idx)
    else:
        sel_idx = list(range(ncols))

    sort_by = spec.get("sort")
    sort_idx = None
    if sort_by:
        sort_idx = _col(sort_by)
        if sort_idx is None:
            return {"ok": False, "error": "query_column_not_found",
                    "detail": str(sort_by)}

    agg = spec.get("aggregate")
    sum_idx = None
    if isinstance(agg, str) and agg.startswith("sum:"):
        sum_idx = _col(agg[4:].strip())
        if sum_idx is None:
            return {"ok": False, "error": "query_column_not_found",
                    "detail": agg[4:].strip()}

    def _cell(row: List[str], i: int) -> str:
        return str(row[i]).strip() if i < len(row) else ""

    def _passes(row: List[str]) -> bool:
        for kind, idx, want in resolved_filters:
            cell = _cell(row, idx)
            if kind == "eq":
                if _fold(cell) != want:
                    return False
            elif kind == "contains":
                if want not in _fold(cell):
                    return False
            else:  # date_eq
                got = _parse_date(cell)
                if got is None:
                    return False
                wd, wm, wy = want
                gd, gm, gy = got
                if gd != wd or gm != wm:
                    return False
                if wy is not None and gy is not None and gy != wy:
                    return False
        return True

    # Keep sheet order; only skip fully-empty rows.
    matched = [
        r for r in values[1:]
        if any(str(c).strip() for c in r) and _passes(r)
    ]

    if not matched:
        return {"ok": False, "error": "query_no_rows",
                "headers": display_headers}

    if sort_idx is not None:
        parsed = [_parse_date(_cell(r, sort_idx)) for r in matched]
        if all(p is not None for p in parsed):
            order = sorted(
                range(len(matched)),
                key=lambda k: (parsed[k][2] or 0, parsed[k][1], parsed[k][0]),
            )
            matched = [matched[k] for k in order]
        else:
            matched.sort(key=lambda r: _fold(_cell(r, sort_idx)))

    def _row_obj(row: List[str]) -> Dict[str, str]:
        return {display_headers[i]: _cell(row, i) for i in sel_idx}

    result: Dict[str, Any] = {
        "ok": True,
        "headers": [display_headers[i] for i in sel_idx],
        "total_rows": len(matched),
    }

    if sum_idx is not None:
        nums = [_to_number(_cell(r, sum_idx)) for r in matched]
        result["aggregate"] = {
            "kind": "sum",
            "column": display_headers[sum_idx],
            "value": sum(n for n in nums if n is not None),
        }
    elif agg == "count":
        result["aggregate"] = {"kind": "count", "value": len(matched)}
    else:
        result["aggregate"] = None

    if group_idx is not None:
        result["group_by"] = display_headers[group_idx]
        groups: List[Dict[str, Any]] = []
        order: List[str] = []
        bucket: Dict[str, List[List[str]]] = {}
        for r in matched:
            key = _cell(r, group_idx) or "—"
            if key not in bucket:
                bucket[key] = []
                order.append(key)
            bucket[key].append(r)
        for key in order:
            groups.append({
                "key": key,
                "count": len(bucket[key]),
                "rows": [_row_obj(r) for r in bucket[key]],
            })
        result["groups"] = groups
        result["total_groups"] = len(groups)
    else:
        result["group_by"] = None
        result["groups"] = None
        result["rows"] = [_row_obj(r) for r in matched]

    return result
