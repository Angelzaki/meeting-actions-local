from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meeting_actions.cli import main as cli_main
from meeting_actions.dates import find_due, parse_due
from meeting_actions.linker import KBIndex, chunk_document, link_result, load_kb, tokenize
from meeting_actions.models import STATUS_BLOCKED, STATUS_DONE, STATUS_PROGRESS, UNASSIGNED
from meeting_actions.owners import OwnerResolver
from meeting_actions.parse import extract_fields, parse_meeting
from meeting_actions.pipeline import Config, Pipeline
from meeting_actions.report import build_data, render_markdown
from meeting_actions.store import Store
from meeting_actions.web import create_app

ROOT = Path(__file__).resolve().parents[1]
MEETINGS = ROOT / "samples" / "meetings"
KB_DIR = ROOT / "samples" / "kb"
AS_OF = date(2026, 11, 17)


@pytest.fixture
def pipe(tmp_path):
    cfg = Config(kb_dir=KB_DIR, results_dir=tmp_path / "results", meetings_dir=MEETINGS)
    return Pipeline(cfg)


@pytest.fixture(scope="module")
def results():
    kb = load_kb(KB_DIR)
    out = {}
    for f in sorted(MEETINGS.iterdir()):
        r = parse_meeting(f.read_text(encoding="utf-8"), source=f.name)
        link_result(r, kb)
        out[f.name] = r
    return out


# ---------- samples ----------

EXPECTED = {
    "2026-11-02_planificacion-sprint.md": dict(actions=6, assigned=5, decisions=3, questions=2),
    "2026-11-04_postmortem-facturacion.md": dict(actions=7, assigned=4, decisions=2, questions=2),
    "2026-11-06_comite-soporte.txt": dict(actions=7, assigned=6, decisions=3, questions=3),
    "2026-11-09_revision-seguridad.md": dict(actions=6, assigned=5, decisions=3, questions=3),
    "2026-11-12_kickoff-migracion-erp.txt": dict(actions=8, assigned=5, decisions=3, questions=2),
}


def test_sample_files_exist():
    assert len(list(MEETINGS.iterdir())) >= 5
    assert len(list(KB_DIR.glob("*.txt"))) >= 5


@pytest.mark.parametrize("name,exp", EXPECTED.items())
def test_sample_counts_at_least(results, name, exp):
    r = results[name]
    assert len(r.actions) >= exp["actions"]
    assert sum(1 for a in r.actions if a.assigned) >= exp["assigned"]
    assert len(r.decisions) >= exp["decisions"]
    assert len(r.questions) >= exp["questions"]


def test_total_actions_across_samples(results):
    total = sum(len(r.actions) for r in results.values())
    assigned = sum(1 for r in results.values() for a in r.actions if a.assigned)
    assert total >= 30
    assert assigned / total >= 0.65


def test_sample_titles_and_dates(results):
    r = results["2026-11-06_comite-soporte.txt"]
    assert r.title.lower().startswith("comité de soporte")
    assert r.meeting_date == "2026-11-06"
    assert results["2026-11-09_revision-seguridad.md"].meeting_date == "2026-11-09"


def test_attendees_normalized(results):
    r = results["2026-11-09_revision-seguridad.md"]
    assert "Sofía Landa" in r.attendees
    assert all("(" not in a and "CISO" not in a for a in r.attendees)


def test_structured_fields_in_sprint_notes(results):
    acts = {a.text: a for a in results["2026-11-02_planificacion-sprint.md"].actions}
    conc = acts["Preparar el script de conciliación bancaria con el formato nuevo del banco"]
    assert conc.owner == "Carlos Ruiz"
    assert conc.due_date == "2026-11-06"
    assert conc.confidence >= 0.9


def test_alias_initials_resolve_to_full_name(results):
    perms = next(a for a in results["2026-11-02_planificacion-sprint.md"].actions if "permisos" in a.text)
    assert perms.owner == "Diego Salinas"
    assert perms.due_date == "2026-11-12"


def test_nickname_in_parentheses_resolves(results):
    alertas = next(a for a in results["2026-11-02_planificacion-sprint.md"].actions if "alertas" in a.text)
    assert alertas.owner == "Mariano Quispe"


def test_multiline_fields_attach_to_previous_action(results):
    doc = next(a for a in results["2026-11-02_planificacion-sprint.md"].actions if "reembolsos" in a.text)
    assert doc.owner == "Rosa Vidal"
    assert doc.due_date == "2026-11-06"


def test_unassigned_action_kept_with_lower_confidence(results):
    demo = next(a for a in results["2026-11-02_planificacion-sprint.md"].actions if "demo" in a.text)
    assert demo.owner == UNASSIGNED
    assert demo.due_date is None
    assert demo.priority == "alta"
    assert demo.confidence < 0.8


def test_checkbox_done_and_todo_owner(results):
    acts = results["2026-11-04_postmortem-facturacion.md"].actions
    paused = next(a for a in acts if "Pausar" in a.text)
    assert paused.status == STATUS_DONE
    assert paused.owner == "Jorge Atencio"
    todo = next(a for a in acts if "regresión" in a.text)
    assert todo.owner == "Carlos Ruiz"
    assert todo.due_date == "2026-11-11"
    assert todo.marker == "todo"


def test_status_tags_extracted_from_text(results):
    acts = results["2026-11-04_postmortem-facturacion.md"].actions
    comunicar = next(a for a in acts if "Comunicar" in a.text)
    assert comunicar.status == STATUS_PROGRESS
    assert "en curso" not in comunicar.text.lower()
    assert comunicar.due_date == "2026-11-06"
    assert comunicar.owner == "Valeria Montes"


def test_blocked_and_done_in_kickoff(results):
    acts = results["2026-11-12_kickoff-migracion-erp.txt"].actions
    assert next(a for a in acts if "contrato" in a.text).status == STATUS_BLOCKED
    assert next(a for a in acts if "canal" in a.text).status == STATUS_DONE


def test_natural_language_commitments(results):
    acts = results["2026-11-06_comite-soporte.txt"].actions
    ana = next(a for a in acts if "restablecimiento" in a.text)
    assert ana.owner == "Ana Torres"
    luis = next(a for a in acts if "fuera de SLA" in a.text)
    assert (luis.owner, luis.due_date) == ("Luis Benavides", "2026-11-11")
    pamela = next(a for a in acts if "plantilla" in a.text)
    assert (pamela.owner, pamela.due_date) == ("Pamela Rojas", "2026-11-09")


def test_mention_and_named_infinitive(results):
    acts = results["2026-11-06_comite-soporte.txt"].actions
    hugo = [a for a in acts if a.owner == "Hugo Cáceres"]
    assert len(hugo) == 2
    assert any(a.due_date == "2026-11-30" for a in hugo)


def test_need_phrases_are_low_confidence(results):
    hay_que = next(a for a in results["2026-11-06_comite-soporte.txt"].actions if a.text.startswith("Hay que"))
    assert hay_que.owner == UNASSIGNED
    assert hay_que.confidence <= 0.5


def test_relative_dates_follow_meeting_date(results):
    acts = results["2026-11-12_kickoff-migracion-erp.txt"].actions
    inventario = next(a for a in acts if "inventario" in a.text)
    assert inventario.due_date == "2026-11-13"
    mapeo = next(a for a in acts if "mapeo" in a.text)
    assert mapeo.due_date == "2026-11-26"


def test_decisions_include_natural_and_marked(results):
    texts = [d.text for d in results["2026-11-06_comite-soporte.txt"].decisions]
    assert any(t.startswith("Se acordó que los tickets de seguridad") for t in texts)
    assert any("reportes de SLA" in t for t in texts)


def test_questions_with_inline_owner(results):
    qs = results["2026-11-02_planificacion-sprint.md"].questions
    owned = [q for q in qs if q.owner]
    assert owned and owned[0].owner == "Carlos Ruiz"
    assert "responsable" not in owned[0].text.lower()


def test_pendiente_de_definir_is_question(results):
    qs = [q.text for q in results["2026-11-06_comite-soporte.txt"].questions]
    assert any("guardia durante el feriado" in q for q in qs)


def test_no_duplicate_actions(results):
    for r in results.values():
        keys = [a.text.lower() for a in r.actions]
        assert len(keys) == len(set(keys))


# ---------- parsing units ----------

def test_markers_accept_accents_and_variants():
    text = "Fecha: 2026-11-03\nAsistentes: Ana Ruiz, Beto Soto\n\nAccion: Mandar contrato | resp: Beto | fecha: 05/11\nDecisión: usar plantilla única\n"
    r = parse_meeting(text, source="x.md")
    assert len(r.actions) == 1 and r.actions[0].owner == "Beto Soto"
    assert r.actions[0].due_date == "2026-11-05"
    assert [d.text for d in r.decisions] == ["Usar plantilla única"]


def test_inline_parenthetical_fields():
    r = parse_meeting("Fecha: 2026-11-03\nAcción: Cerrar el informe (resp: Luz, fecha: 2026-11-08)\n", source="x.md")
    a = r.actions[0]
    assert a.owner == "Luz" and a.due_date == "2026-11-08"
    assert a.text == "Cerrar el informe"


def test_empty_or_irrelevant_text_has_warning():
    r = parse_meeting("Solo conversamos del clima.\nNada más.\n", source="x.md")
    assert not r.actions and not r.decisions
    assert any("No se detectaron acciones" in w for w in r.warnings)


def test_extract_fields_keeps_text_and_values():
    text, fields = extract_fields("Enviar acta | responsable: Pedro, fecha: viernes | prioridad: alta")
    assert text.strip() == "Enviar acta"
    assert fields == {"owner": "Pedro", "due": "viernes", "priority": "alta"}


def test_meeting_date_from_filename_when_missing():
    r = parse_meeting("Acción: Preparar demo del lunes | responsable: Eva", source="2026-03-10_demo.txt")
    assert r.meeting_date == "2026-03-10"


def test_crlf_and_bom_input():
    raw = "\ufeffFecha: 2026-11-03\r\nAcción: Revisar contrato | responsable: Mia | fecha: 2026-11-04\r\n"
    r = parse_meeting(raw, source="x.md")
    assert r.actions[0].owner == "Mia" and r.meeting_date == "2026-11-03"


# ---------- dates ----------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-11-20", "2026-11-20"),
        ("20/11/2026", "2026-11-20"),
        ("20/11", "2026-11-20"),
        ("20 de noviembre", "2026-11-20"),
        ("5 de enero de 2027", "2027-01-05"),
        ("mañana", "2026-11-18"),
        ("pasado mañana", "2026-11-19"),
        ("viernes", "2026-11-20"),
        ("próximo lunes", "2026-11-23"),
        ("fin de mes", "2026-11-30"),
        ("en 2 semanas", "2026-12-01"),
        ("en 3 días hábiles", "2026-11-20"),
        ("próxima semana", "2026-11-27"),
        ("sin fecha", None),
    ],
)
def test_parse_due_values(text, expected):
    assert parse_due(text, AS_OF) == expected


def test_free_text_dates_need_cues_for_weekdays():
    assert find_due("Reunión del lunes con proveedores", AS_OF) is None
    assert find_due("Enviar propuesta el viernes", AS_OF)[0] == "2026-11-20"


def test_invalid_dates_are_ignored():
    assert parse_due("31/02/2026", AS_OF) is None
    assert parse_due("2026-13-40", AS_OF) is None


# ---------- owners ----------

def test_owner_resolver_aliases_and_ambiguity():
    r = OwnerResolver()
    r.add_attendees(["Carlos Ruiz (CR)", "Carla Ruiz", "Lucía Paredes"])
    assert r.resolve("CR") == "Carlos Ruiz"
    assert r.resolve("Lucia") == "Lucía Paredes"
    assert r.resolve("@lucía") == "Lucía Paredes"
    assert r.resolve("Ruiz") == "Ruiz"
    assert r.resolve("Carlos") == "Carlos Ruiz"


@pytest.mark.parametrize("raw", ["", "pendiente", "TBD", "por definir", "-"])
def test_owner_placeholders_unassigned(raw):
    assert OwnerResolver().resolve(raw) == UNASSIGNED


def test_owner_group_and_multiple():
    r = OwnerResolver()
    r.add_attendees(["Ana Díaz", "Beto Soto"])
    assert r.resolve("todos") == "Equipo"
    assert r.resolve("Ana y Beto") == "Ana Díaz"
    assert r.resolve("Ing. Mario Vega") == "Mario Vega"


def test_custom_aliases_file_style():
    r = OwnerResolver({"cr": "Carlos Ruiz"})
    assert r.resolve("CR") == "Carlos Ruiz"


# ---------- linker ----------

def test_tokenize_folds_accents_and_stems():
    assert tokenize("Conciliación Bancaria") == tokenize("conciliacion bancaria")
    assert tokenize("respaldos") == tokenize("respaldo")


def test_kb_loading_and_chunks():
    kb = load_kb(KB_DIR)
    assert kb is not None and len(kb.docs) >= 5
    assert load_kb(KB_DIR / "no-existe") is None
    assert chunk_document("a.txt", "Titulo\n\nPrimer parrafo largo con contenido util para pruebas de chunks.")


def test_links_have_citations_and_snippets(results):
    mfa = next(a for a in results["2026-11-09_revision-seguridad.md"].actions if "MFA" in a.text)
    assert mfa.links
    top = mfa.links[0]
    assert top.doc == "SOP-004-accesos-privilegiados.txt"
    assert top.citation.startswith("SOP-004") and "#" in top.citation
    assert top.snippet and top.coverage >= 0.4 and top.terms


def test_decisions_and_questions_can_link(results):
    r = results["2026-11-04_postmortem-facturacion.md"]
    assert any(d.links for d in r.decisions)
    assert any(q.links for q in r.questions)


def test_unrelated_text_does_not_link():
    kb = load_kb(KB_DIR)
    assert kb.search("Organizar el almuerzo de fin de año con el equipo") == []


def test_empty_kb_is_safe():
    assert KBIndex([]).search("lo que sea") == []


# ---------- pipeline, store, report ----------

def test_pipeline_records_history_and_audit(pipe):
    results = pipe.analyze_batch(as_of=AS_OF)
    assert len(results) == 5
    hist = pipe.store.history()
    assert len(hist) == 5 and hist[0]["n_actions"] >= 6
    events = pipe.store.audit_events()
    assert sum(1 for e in events if e["event"] == "parse") == 5
    run = pipe.store.get_run(hist[0]["id"])
    assert run["stats"]["actions"] == hist[0]["n_actions"]


def test_owner_load_uses_latest_run_per_source(pipe):
    pipe.analyze_batch(as_of=AS_OF)
    pipe.analyze_batch(as_of=AS_OF)
    load = {row["owner"]: row for row in pipe.store.owner_load()}
    assert load["Carlos Ruiz"]["total"] >= 4
    assert len(pipe.store.history(limit=50)) == 10


def test_no_record_leaves_history_empty(pipe):
    pipe.analyze_batch(record=False)
    assert pipe.store.history() == []


def test_report_files_written(pipe, tmp_path):
    results = pipe.analyze_batch(record=False)
    md, js = pipe.report(results, AS_OF, tmp_path / "out")
    assert md.name == "meeting_report.md" and js.name == "meeting_report.json"
    text = md.read_text(encoding="utf-8")
    assert "## Tablero por responsable" in text and "Carlos Ruiz" in text and "SOP-" in text
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["totals"]["actions"] >= 30 and data["totals"]["meetings"] == 5
    assert data["totals"]["overdue"] > 0
    assert UNASSIGNED in data["board"]


def test_overdue_respects_status_and_cutoff(results):
    data_late = build_data(list(results.values()), date(2026, 12, 31))
    data_early = build_data(list(results.values()), date(2026, 10, 1))
    assert data_late["totals"]["overdue"] > data_early["totals"]["overdue"] == 0
    assert all(a["status"] != STATUS_DONE for a in data_late["overdue"])


def test_markdown_escapes_pipes():
    r = parse_meeting("Acción: Ajustar regla A | B para el cierre | responsable: Ana", source="x.md")
    assert "Ajustar regla A" in render_markdown([r], AS_OF)


def test_store_clear_and_filter(tmp_path):
    store = Store(tmp_path / "h.db")
    r = parse_meeting("Acción: Hacer algo importante | responsable: Eva", source="a.md")
    store.record_run(r)
    assert store.history(source="a.md") and not store.history(source="zzz")
    store.clear()
    assert store.history() == []


def test_json_roundtrip_has_stats(results):
    d = results["2026-11-09_revision-seguridad.md"].to_dict(AS_OF)
    assert d["stats"]["actions"] == len(d["actions"])
    json.dumps(d, ensure_ascii=False)


# ---------- CLI ----------

def test_cli_parse_json(capsys, tmp_path):
    code = cli_main([
        "parse", str(MEETINGS / "2026-11-09_revision-seguridad.md"), "--json", "--no-save",
        "--kb-dir", str(KB_DIR), "--results-dir", str(tmp_path), "--as-of", "2026-11-17",
    ])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["actions"]) >= 6


def test_cli_batch_report_history(tmp_path):
    base = ["--kb-dir", str(KB_DIR), "--results-dir", str(tmp_path), "--as-of", "2026-11-17"]
    assert cli_main(["batch", str(MEETINGS), "--report", *base]) == 0
    assert (tmp_path / "meeting_report.md").is_file() and (tmp_path / "meeting_report.json").is_file()
    assert cli_main(["history", "--owners", *base]) == 0
    assert cli_main(["history", "--audit", *base]) == 0
    assert cli_main(["history", *base]) == 0


def test_cli_missing_file_returns_error(tmp_path):
    assert cli_main(["parse", str(tmp_path / "nada.md"), "--results-dir", str(tmp_path), "--no-kb"]) == 2


# ---------- web ----------

@pytest.fixture
def client(pipe):
    return TestClient(create_app(pipe))


def test_web_index_and_health(client):
    page = client.get("/")
    assert page.status_code == 200 and "meeting-actions-local" in page.text
    assert "2026-11-02_planificacion-sprint.md" in page.text
    assert "Hecho en Lima" in page.text
    assert client.get("/api/health").json()["samples"] == 5


def test_web_analyze_pasted_notes(client):
    body = {"text": "Fecha: 2026-11-03\nAsistentes: Ana Ruiz, Beto Soto\nAcción: Enviar el contrato firmado | responsable: Ana | fecha: 2026-11-06\n"}
    res = client.post("/api/analyze", json=body).json()
    assert res["stats"]["actions"] == 1
    assert res["board"]["Ana Ruiz"][0]["due_date"] == "2026-11-06"
    assert "# Reporte" in res["markdown"]
    assert client.get("/api/history").json()["runs"][0]["source"] == "pegado"


def test_web_analyze_without_saving_or_kb(client):
    body = {"text": "Acción: Activar MFA en cuentas de administrador | responsable: Ana", "save": False, "link_kb": False}
    res = client.post("/api/analyze", json=body).json()
    assert res["actions"][0]["links"] == []
    assert client.get("/api/history").json()["runs"] == []


def test_web_rejects_empty_text(client):
    assert client.post("/api/analyze", json={"text": ""}).status_code == 422


def test_web_samples_and_runs(client):
    name = "2026-11-09_revision-seguridad.md"
    res = client.post(f"/api/samples/{name}").json()
    assert res["stats"]["actions"] >= 6
    assert "Revisión mensual" in client.get(f"/api/samples/{name}/text").text
    run_id = client.get("/api/history").json()["runs"][0]["id"]
    assert client.get(f"/api/runs/{run_id}").json()["meeting_id"] == res["meeting_id"]
    assert client.get("/api/runs/9999").status_code == 404
    assert client.post("/api/samples/../secreto.txt").status_code in (404, 405)
    assert client.get("/api/audit").json()["events"]
