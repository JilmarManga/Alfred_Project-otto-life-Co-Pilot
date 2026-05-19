"""Deterministic Drive tabular-query path — the fix for the April-2026
incident where `analyze` dropped 2 of 4 clients from a tax-deadline sheet.

Invariants under test:
  - query_resolver returns EVERY matching row/group, in sheet order, never
    samples or summarizes (this is the bug that must never recur).
  - It REFUSES with an explicit code on bad column / bad date / bad spec
    instead of guessing.
  - The Layer-4 skeleton renders the full result deterministically, and the
    warm-wrapper post-check rejects any LLM output that drops an anchor.

Pure unit tests — no network, no LLM, no Firebase (stubbed by conftest).
"""
import app.responder.response_formatter as rf
from app.agents.drive_agent._shared.query_resolver import (
    _fold,
    _resolve_header,
    best_header_guess,
    remap_spec_column,
    resolve_query,
    validate_query_spec,
)

# The exact sheet from the incident (+ 2 rows that must be filtered out).
INCIDENT_GRID = [
    ["Cliente", "Tipo de Impuesto", "Vencimiento", "Estado"],
    ["KDESIGN INGENIERIA Y DISEÑO SAS", "Renta Persona Jurídica", "19/05/2026", "Pendiente"],
    ["RETEKI S.A.S", "Renta Persona Jurídica", "19/05/2026", "Pendiente"],
    ["RETEKI S.A.S", "Conciliación Fiscal Persona Jurídica (presenta con renta)", "19/05/2026", "Pendiente"],
    ["RETEKI S.A.S", "Declaración Anual de Activos en el Exterior P Jurídica", "19/05/2026", "Pendiente"],
    ["RETEKI S.A.S", "Retención en la fuente", "19/05/2026", "Pendiente"],
    ["RETEKI S.A.S", "IVA bimestral", "19/05/2026", "Pendiente"],
    ["INVERSIONES APARICIO AYALA SAS", "Renta Persona Jurídica", "19/05/2026", "Pendiente"],
    ["INVERSIONES APARICIO AYALA SAS", "Planilla de seguridad social", "19/05/2026", "Pendiente"],
    ["COMERCIALIZADORA MHERG SAS", "Información exógena nacional persona jurídica y natural", "19/05/2026", "Pendiente"],
    ["OTRO SA", "Renta Persona Jurídica", "19/05/2026", "Pagado"],       # wrong estado
    ["OTRO SA", "IVA bimestral", "01/06/2026", "Pendiente"],             # wrong date
]

INCIDENT_SPEC = {
    "filters": [
        {"column": "Estado", "op": "eq", "value": "pendiente"},
        {"column": "Vencimiento", "op": "date_eq", "value": "19 de mayo"},
    ],
    "group_by": "Cliente",
    "select": ["Tipo de Impuesto"],
}


def test_incident_returns_all_four_clients_and_nine_rows():
    """The regression test: the exact query that dropped KDESIGN + MHERG."""
    r = resolve_query(INCIDENT_GRID, INCIDENT_SPEC)
    assert r["ok"] is True
    assert r["total_rows"] == 9
    assert r["total_groups"] == 4

    by_key = {g["key"]: g for g in r["groups"]}
    assert set(by_key) == {
        "KDESIGN INGENIERIA Y DISEÑO SAS",
        "RETEKI S.A.S",
        "INVERSIONES APARICIO AYALA SAS",
        "COMERCIALIZADORA MHERG SAS",
    }
    assert by_key["RETEKI S.A.S"]["count"] == 5
    assert by_key["INVERSIONES APARICIO AYALA SAS"]["count"] == 2
    assert by_key["KDESIGN INGENIERIA Y DISEÑO SAS"]["count"] == 1
    assert by_key["COMERCIALIZADORA MHERG SAS"]["count"] == 1
    # Filtered rows must be excluded.
    assert "OTRO SA" not in by_key


def test_group_and_sheet_order_preserved():
    r = resolve_query(INCIDENT_GRID, INCIDENT_SPEC)
    assert [g["key"] for g in r["groups"]][0] == "KDESIGN INGENIERIA Y DISEÑO SAS"
    reteki = next(g for g in r["groups"] if g["key"] == "RETEKI S.A.S")
    assert [row["Tipo de Impuesto"] for row in reteki["rows"]] == [
        "Renta Persona Jurídica",
        "Conciliación Fiscal Persona Jurídica (presenta con renta)",
        "Declaración Anual de Activos en el Exterior P Jurídica",
        "Retención en la fuente",
        "IVA bimestral",
    ]


def test_accent_and_case_insensitive_column_resolution():
    r = resolve_query(INCIDENT_GRID, {
        "filters": [{"column": "estado", "op": "eq", "value": "PENDIENTE"}],
        "group_by": "cliente",
    })
    assert r["ok"] is True
    assert r["total_rows"] == 10  # all Pendiente rows regardless of date


def test_date_eq_matches_slash_format_and_year_optional():
    one = resolve_query(INCIDENT_GRID, {
        "filters": [{"column": "Vencimiento", "op": "date_eq", "value": "19/05/2026"}],
    })
    two = resolve_query(INCIDENT_GRID, {
        "filters": [{"column": "Vencimiento", "op": "date_eq", "value": "19 de mayo"}],
    })
    assert one["ok"] and two["ok"]
    assert one["total_rows"] == two["total_rows"] == 10


# ---- Tier 0: column UNDERSTANDING (plurals / inflection / paraphrase) ---- #

def test_tier0_resolves_inflected_and_paraphrased_columns():
    """The exact failure from the incident: a literal matcher dead-ended on
    'vencimientos' / 'clientes' / 'Fecha de Vencimiento'. Tier 0 resolves
    them; an unknown column still refuses (unique-or-refuse preserved)."""
    H = ["Cliente", "Tipo de Impuesto", "Vencimiento", "Estado"]
    hf = [_fold(h) for h in H]
    assert _resolve_header(hf, "vencimientos") == 2
    assert _resolve_header(hf, "clientes") == 0
    assert _resolve_header(hf, "Fecha de Vencimiento") == 2
    assert _resolve_header(hf, "impuestos") == 1
    # Unchanged guarantees:
    assert _resolve_header(hf, "Vencimiento") == 2      # exact fast path
    assert _resolve_header(hf, "estado") == 3           # accent/case fold
    assert _resolve_header(hf, "NoExiste") == -1        # refuse, never guess


def test_tier0_ambiguous_column_is_refused_not_guessed():
    """Two headers plausibly match → -1 (Tier 2 will clarify, never guess)."""
    H = ["Cliente", "Fecha de emisión", "Fecha de vencimiento", "Estado"]
    hf = [_fold(h) for h in H]
    assert _resolve_header(hf, "fecha") == -1
    # …but the closest-guess helper still SUGGESTS one for the clarify Q.
    assert best_header_guess(H, "fecha") in (
        "Fecha de emisión", "Fecha de vencimiento")


def test_remap_spec_column_rewrites_every_slot():
    spec = {
        "filters": [{"column": "fecha", "op": "date_eq", "value": "19 de mayo"},
                    {"column": "Estado", "op": "eq", "value": "x"}],
        "group_by": "fecha", "select": ["fecha", "Estado"],
        "sort": "fecha", "aggregate": "sum:fecha",
    }
    out = remap_spec_column(spec, "fecha", "Fecha de vencimiento")
    assert out["filters"][0]["column"] == "Fecha de vencimiento"
    assert out["filters"][1]["column"] == "Estado"  # untouched
    assert out["group_by"] == "Fecha de vencimiento"
    assert out["select"] == ["Fecha de vencimiento", "Estado"]
    assert out["sort"] == "Fecha de vencimiento"
    assert out["aggregate"] == "sum:Fecha de vencimiento"
    assert spec["group_by"] == "fecha"  # original not mutated


def test_refuses_unknown_column():
    r = resolve_query(INCIDENT_GRID, {
        "filters": [{"column": "NoExiste", "op": "eq", "value": "x"}],
    })
    assert r == {"ok": False, "error": "query_column_not_found", "detail": "NoExiste"}


def test_refuses_unparseable_date():
    r = resolve_query(INCIDENT_GRID, {
        "filters": [{"column": "Vencimiento", "op": "date_eq", "value": "pronto"}],
    })
    assert r["ok"] is False and r["error"] == "query_bad_date"


def test_no_matching_rows_is_explicit_not_silent():
    r = resolve_query(INCIDENT_GRID, {
        "filters": [{"column": "Estado", "op": "eq", "value": "cancelado"}],
    })
    assert r["ok"] is False and r["error"] == "query_no_rows"


def test_validate_rejects_bad_spec_shapes():
    assert validate_query_spec(None) == "invalid_query_spec"
    assert validate_query_spec({"filters": [{"column": "", "op": "eq", "value": 1}]}) == "invalid_query_spec"
    assert validate_query_spec({"filters": [{"column": "a", "op": "weird", "value": 1}]}) == "invalid_query_spec"
    assert validate_query_spec({"aggregate": "sum:"}) == "invalid_query_spec"
    assert validate_query_spec(INCIDENT_SPEC) is None
    assert validate_query_spec({"aggregate": "count"}) is None


def test_count_and_sum_aggregates():
    grid = [
        ["Cliente", "Monto", "Estado"],
        ["A", "$ 1.000.000", "Pendiente"],
        ["B", "2,500.50", "Pendiente"],
        ["C", "300", "Pagado"],
    ]
    c = resolve_query(grid, {
        "filters": [{"column": "Estado", "op": "eq", "value": "Pendiente"}],
        "aggregate": "count",
    })
    assert c["aggregate"] == {"kind": "count", "value": 2}
    s = resolve_query(grid, {
        "filters": [{"column": "Estado", "op": "eq", "value": "Pendiente"}],
        "aggregate": "sum:Monto",
    })
    assert s["aggregate"]["kind"] == "sum"
    assert s["aggregate"]["value"] == 1002500.5


# ---- Layer-4 completeness guard ------------------------------------------ #

def test_skeleton_renders_every_group_and_row():
    res = resolve_query(INCIDENT_GRID, INCIDENT_SPEC)
    sk = rf._drive_query_skeleton(res, "Prueba Copiloto Abril 2026", "es")
    for name in ("KDESIGN", "RETEKI", "INVERSIONES APARICIO AYALA",
                 "COMERCIALIZADORA MHERG"):
        assert name in sk
    assert sk.count("  - ") == 9  # every row line present


def test_skeleton_compacts_constant_columns_but_keeps_every_anchor():
    """The image-2 production case: a spec with NO `select`, so every column
    projects. The skeleton must NOT repeat constant columns on every row —
    it lifts them to one context line — yet every distinct value still
    appears verbatim so the anchor post-check still holds (no row loss)."""
    no_select = {
        "filters": [
            {"column": "Estado", "op": "eq", "value": "pendiente"},
            {"column": "Vencimiento", "op": "date_eq", "value": "19 de mayo"},
        ],
        "group_by": "Cliente",
    }
    res = resolve_query(INCIDENT_GRID, no_select)
    sk = rf._drive_query_skeleton(res, "Prueba Copiloto Abril 2026", "es")

    # Constant columns are lifted once, never repeated per bullet.
    assert "_Vencimiento: 19/05/2026 · Estado: Pendiente_" in sk
    assert "Estado: Pendiente" not in sk.replace(
        "_Vencimiento: 19/05/2026 · Estado: Pendiente_", "")
    # Group-by column is the header, not echoed in row bodies.
    assert "Cliente:" not in sk
    # Still complete: 4 groups, 9 varying rows, all clients present.
    assert sk.count("  - ") == 9
    for name in ("KDESIGN", "RETEKI", "INVERSIONES APARICIO AYALA",
                 "COMERCIALIZADORA MHERG"):
        assert name in sk
    # Completeness contract intact: every anchor survives verbatim.
    anchors = rf._query_anchors(res)
    folded = rf._fold_text(sk)
    assert all(rf._fold_text(a) in folded for a in anchors)


def test_post_check_rejects_llm_output_that_drops_a_client():
    res = resolve_query(INCIDENT_GRID, INCIDENT_SPEC)
    sk = rf._drive_query_skeleton(res, "F", "es")
    anchors = rf._query_anchors(res)

    # Complete skeleton must satisfy every anchor.
    assert all(rf._fold_text(a) in rf._fold_text(sk) for a in anchors)

    # An LLM reply that silently drops the MHERG client must FAIL the guard
    # (so _warm_wrap_query falls back to the complete skeleton).
    dropped = "\n".join(
        ln for ln in sk.splitlines()
        if "MHERG" not in ln and "exógena" not in ln
    )
    assert not all(rf._fold_text(a) in rf._fold_text(dropped) for a in anchors)
