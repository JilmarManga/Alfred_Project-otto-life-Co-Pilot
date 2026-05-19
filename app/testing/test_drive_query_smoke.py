"""End-to-end smoke tests for the deterministic Drive tabular-query path.

Exercises the full pipeline as it runs in production — DriveAgent.execute →
analyze_file skill → query_resolver → format_response (Layer 4) — with only
the true I/O seams stubbed (Drive API + the OpenAI warm-wrapper call). These
are the real use cases a user hits, including the April-2026 incident.

No network, no Firebase (conftest stubs it), no real LLM.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.agents.drive_agent import DriveAgent
from app.db.user_context_store import get_user_context, update_user_context
from app.models.inbound_message import InboundMessage
from app.models.parsed_message import ParsedMessage
from app.responder.response_formatter import format_response

USER = {"phone_number": "+573009998877", "language": "es",
        "google_drive_refresh_token": "enc"}

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOC_MIME = "application/vnd.google-apps.document"

GRID = [
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
    ["YA PAGO SA", "Renta Persona Jurídica", "19/05/2026", "Pagado"],
]
INCIDENT_QUERY = {
    "filters": [
        {"column": "Estado", "op": "eq", "value": "pendiente"},
        {"column": "Vencimiento", "op": "date_eq", "value": "19 de mayo"},
    ],
    "group_by": "Cliente",
    "select": ["Tipo de Impuesto"],
}
ALL_CLIENTS = ["KDESIGN", "RETEKI", "INVERSIONES APARICIO AYALA",
               "COMERCIALIZADORA MHERG"]


def _pm(text, **kw):
    return ParsedMessage(raw_message=text, signals=[], **kw)


def _llm(text):
    """Build a fake OpenAI client whose completion returns `text`."""
    fake = MagicMock()
    fake.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=text))]
    )
    return fake


def _run_analyze(parsed, user=USER, grid=GRID, mime=SHEET_MIME,
                 file_name="Prueba Copiloto Abril 2026", llm_text=None,
                 resolve=("ok", None), content="raw prose content"):
    """Drive analyze through the real agent + real Layer 4, I/O stubbed."""
    files = resolve[1]
    if files is None:
        files = [{"id": "fid", "name": file_name, "mimeType": mime}]
    a = "app.agents.drive_agent.skills.analyze_file"
    with patch(f"{a}.get_drive_refresh_token", return_value="tok"), \
         patch(f"{a}.resolve_file", return_value=(resolve[0], files)), \
         patch(f"{a}.google_drive.get_grid",
               return_value=(grid if mime != DOC_MIME else None)), \
         patch(f"{a}.google_drive.get_content", return_value=content):
        result = DriveAgent().execute(parsed, user)
    with patch("app.responder.response_formatter.openai",
               _llm(llm_text if llm_text is not None
                    else "Aquí tienes 🐙")):
        reply = format_response(result, user)
    return result, reply


# --------------------------------------------------------------------- #
# Use case 1 — the incident: structured query on a native Sheet          #
# --------------------------------------------------------------------- #

def test_incident_native_sheet_returns_every_client():
    parsed = _pm("consulta el archivo y dame los vencimientos pendientes "
                 "del 19 de mayo agrupados por cliente",
                 drive_intent="analyze", drive_file_ref="Prueba Copiloto Abril 2026",
                 drive_query=INCIDENT_QUERY)
    # Faithful LLM wrapper: echoes the skeleton with a warm intro.
    result, reply = _run_analyze(
        parsed, llm_text=None)  # default warm text fails post-check on its own
    assert result.success
    assert result.data["type"] == "drive_query_result"
    # Even with a non-echoing LLM, the post-check forces the COMPLETE skeleton.
    for c in ALL_CLIENTS:
        assert c in reply, f"{c} missing — incident regression!"
    assert reply.count("  - ") == 9


def test_faithful_llm_wrapper_is_used_when_it_preserves_all_data():
    parsed = _pm("x", drive_intent="analyze",
                 drive_file_ref="F", drive_query=INCIDENT_QUERY)
    # First resolve the skeleton to feed a faithful wrapper back.
    from app.agents.drive_agent._shared.query_resolver import resolve_query
    import app.responder.response_formatter as rf
    res = resolve_query(GRID, INCIDENT_QUERY)
    skeleton = rf._drive_query_skeleton(res, "F", "es")
    faithful = "¡Listo Ricardo! 🐙\n\n" + skeleton + "\n\n¡Espero que sirva! 😊"
    _, reply = _run_analyze(parsed, llm_text=faithful)
    assert reply.startswith("¡Listo Ricardo!")
    for c in ALL_CLIENTS:
        assert c in reply


def test_mangled_llm_output_falls_back_to_complete_skeleton():
    parsed = _pm("x", drive_intent="analyze",
                 drive_file_ref="F", drive_query=INCIDENT_QUERY)
    # LLM "helpfully" summarizes and drops two clients — the original bug.
    mangled = ("Aquí tienes algunos: RETEKI S.A.S (Renta Persona Jurídica) "
               "e INVERSIONES APARICIO AYALA SAS, entre otros.")
    _, reply = _run_analyze(parsed, llm_text=mangled)
    assert "entre otros" not in reply  # mangled output rejected
    for c in ALL_CLIENTS:
        assert c in reply  # complete skeleton sent instead


def test_llm_exception_falls_back_to_complete_skeleton():
    parsed = _pm("x", drive_intent="analyze",
                 drive_file_ref="F", drive_query=INCIDENT_QUERY)
    a = "app.agents.drive_agent.skills.analyze_file"
    with patch(f"{a}.get_drive_refresh_token", return_value="tok"), \
         patch(f"{a}.resolve_file",
               return_value=("ok", [{"id": "f", "name": "F", "mimeType": SHEET_MIME}])), \
         patch(f"{a}.google_drive.get_grid", return_value=GRID):
        result = DriveAgent().execute(parsed, USER)
    boom = MagicMock()
    boom.chat.completions.create.side_effect = RuntimeError("openai down")
    with patch("app.responder.response_formatter.openai", boom):
        reply = format_response(result, USER)
    for c in ALL_CLIENTS:
        assert c in reply


# --------------------------------------------------------------------- #
# Use case 2 — .xlsx upload, same query                                  #
# --------------------------------------------------------------------- #

def test_xlsx_tabular_query_works():
    parsed = _pm("x", drive_intent="analyze",
                 drive_file_ref="F", drive_query=INCIDENT_QUERY)
    result, reply = _run_analyze(parsed, mime=XLSX_MIME)
    assert result.data["type"] == "drive_query_result"
    assert result.data["result"]["total_groups"] == 4


# --------------------------------------------------------------------- #
# Use case 3 — query spec but file is prose → free-form analyze fallback #
# --------------------------------------------------------------------- #

def test_query_spec_on_prose_doc_falls_back_to_analyze():
    parsed = _pm("resume y agrupa", drive_intent="analyze",
                 drive_file_ref="Memo", drive_query=INCIDENT_QUERY)
    result, reply = _run_analyze(parsed, mime=DOC_MIME,
                                 llm_text="Resumen del memo 🐙")
    assert result.data["type"] == "drive_analyze"  # NOT the query path
    assert reply == "Resumen del memo 🐙"


def test_free_form_analyze_unchanged_when_no_query_spec():
    parsed = _pm("¿de qué trata este archivo?", drive_intent="analyze",
                 drive_file_ref="Memo")
    result, reply = _run_analyze(parsed, mime=DOC_MIME,
                                 llm_text="Trata sobre ventas 🐙")
    assert result.data["type"] == "drive_analyze"
    assert reply == "Trata sobre ventas 🐙"


# --------------------------------------------------------------------- #
# Use case 4 — resolver refusals surface as friendly, honest copy        #
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("query,marker", [
    ({"filters": [{"column": "NoExiste", "op": "eq", "value": "x"}]},
     "columna"),
    ({"filters": [{"column": "Vencimiento", "op": "date_eq", "value": "algún día"}]},
     "fecha"),
    ({"filters": [{"column": "Estado", "op": "eq", "value": "anulado"}]},
     "no encontré ninguna fila"),
])
def test_resolver_refusals_are_friendly(query, marker):
    parsed = _pm("x", drive_intent="analyze",
                 drive_file_ref="F", drive_query=query)
    result, reply = _run_analyze(parsed)
    assert result.success is False
    assert marker.lower() in reply.lower()
    assert "{" not in reply and "error" not in reply.lower()


def test_invalid_spec_is_friendly():
    parsed = _pm("x", drive_intent="analyze", drive_file_ref="F",
                 drive_query={"filters": [{"column": "", "op": "eq", "value": 1}]})
    result, reply = _run_analyze(parsed)
    assert result.success is False
    assert "claro" in reply.lower() or "filtrar" in reply.lower()


# --------------------------------------------------------------------- #
# Use case 5 — count / sum aggregates end-to-end                         #
# --------------------------------------------------------------------- #

def test_count_aggregate_end_to_end():
    q = {"filters": [{"column": "Estado", "op": "eq", "value": "pendiente"}],
         "group_by": "Cliente", "select": ["Tipo de Impuesto"],
         "aggregate": "count"}
    parsed = _pm("cuántos pendientes por cliente", drive_intent="analyze",
                 drive_file_ref="F", drive_query=q)
    result, reply = _run_analyze(parsed)
    assert result.data["result"]["aggregate"] == {"kind": "count", "value": 9}
    assert "Total: 9" in reply


# --------------------------------------------------------------------- #
# Use case 6 — ambiguous filename keeps the deterministic path           #
#   (threading the spec through the file-choice re-dispatch)             #
# --------------------------------------------------------------------- #

def test_ambiguous_filename_then_choice_stays_deterministic():
    from app.handlers.pending_drive_handler import handle_pending_drive
    phone = USER["phone_number"]
    update_user_context(phone, "pending_drive", None)

    parsed = _pm("analiza el archivo de vencimientos agrupado por cliente",
                 drive_intent="analyze", drive_file_ref="vencimientos",
                 drive_query=INCIDENT_QUERY)
    cands = [{"id": "1", "name": "Vencimientos 2026", "mimeType": SHEET_MIME},
             {"id": "2", "name": "Vencimientos copia", "mimeType": SHEET_MIME}]

    a = "app.agents.drive_agent.skills.analyze_file"
    # 1) Ambiguous → agent stashes pending_drive WITH the query spec.
    with patch(f"{a}.get_drive_refresh_token", return_value="tok"), \
         patch(f"{a}.resolve_file", return_value=("ambiguous", cands)):
        result = DriveAgent().execute(parsed, USER)
    assert result.data["type"] == "drive_file_choice"
    pend = get_user_context(phone)["pending_drive"]
    assert pend["step"] == "awaiting_file_choice"
    assert pend["query_spec"] == INCIDENT_QUERY  # <- the threading fix

    # 2) User picks "1" → re-dispatch must resolve deterministically.
    sent = []
    inbound = InboundMessage(user_phone_number=phone, message_id="m1",
                             text="1", message_type="text")
    with patch(f"{a}.get_drive_refresh_token", return_value="tok"), \
         patch(f"{a}.resolve_file",
               return_value=("ok", [cands[0]])), \
         patch(f"{a}.google_drive.get_grid", return_value=GRID), \
         patch("app.responder.response_formatter.openai", _llm("warm")), \
         patch("app.handlers.pending_drive_handler.send_whatsapp_message",
               side_effect=lambda p, m: sent.append(m)):
        consumed = handle_pending_drive(inbound, USER)

    assert consumed is True
    assert len(sent) == 1
    for c in ALL_CLIENTS:
        assert c in sent[0], f"{c} dropped after file-choice re-dispatch"


# --------------------------------------------------------------------- #
# Use case 7 — not connected → silent connect-link side effect           #
# --------------------------------------------------------------------- #

def test_not_connected_sends_link_and_stays_silent():
    parsed = _pm("x", drive_intent="analyze", drive_file_ref="F",
                 drive_query=INCIDENT_QUERY)
    calls = []
    a = "app.agents.drive_agent.skills.analyze_file"
    with patch(f"{a}.get_drive_refresh_token",
               side_effect=__import__(
                   "app.agents.drive_agent._shared.drive_client",
                   fromlist=["DriveNotConnected"]).DriveNotConnected("no")), \
         patch("app.agents.drive_agent.agent.send_connect_link",
               side_effect=lambda p, l: calls.append(p)):
        result = DriveAgent().execute(parsed, {**USER})
    reply = format_response(result, USER)
    assert calls == [USER["phone_number"]]
    assert reply == ""  # webhook drops empty — agent already DM'd the link
