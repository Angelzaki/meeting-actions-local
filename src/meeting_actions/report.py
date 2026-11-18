from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from meeting_actions.models import STATUS_DONE, UNASSIGNED, MeetingResult

STATUS_LABEL = {"abierta": "abierta", "en_curso": "en curso", "hecha": "hecha", "bloqueada": "bloqueada"}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def build_data(results: list[MeetingResult], as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    board: dict[str, list[dict[str, Any]]] = {}
    overdue: list[dict[str, Any]] = []
    for r in results:
        for a in r.actions:
            row = a.to_dict(as_of)
            row["meeting"] = r.title
            row["meeting_id"] = r.meeting_id
            board.setdefault(a.owner, []).append(row)
            if a.is_overdue(as_of):
                overdue.append(row)
    board = dict(sorted(board.items(), key=lambda kv: (kv[0] == UNASSIGNED, kv[0])))
    all_actions = [a for r in results for a in r.actions]
    totals = {
        "meetings": len(results),
        "actions": len(all_actions),
        "assigned": sum(1 for a in all_actions if a.assigned),
        "with_due": sum(1 for a in all_actions if a.due_date),
        "done": sum(1 for a in all_actions if a.status == STATUS_DONE),
        "overdue": len(overdue),
        "linked": sum(1 for a in all_actions if a.links),
        "decisions": sum(len(r.decisions) for r in results),
        "questions": sum(len(r.questions) for r in results),
        "avg_confidence": round(sum(a.confidence for a in all_actions) / len(all_actions), 2) if all_actions else 0.0,
    }
    return {
        "as_of": as_of.isoformat(),
        "totals": totals,
        "board": board,
        "overdue": overdue,
        "meetings": [r.to_dict(as_of) for r in results],
    }


def _links_md(links: list[Any]) -> str:
    return "; ".join(f"`{lk.citation}`" for lk in links)


def render_markdown(results: list[MeetingResult], as_of: date | None = None) -> str:
    data = build_data(results, as_of)
    t = data["totals"]
    out: list[str] = ["# Reporte de acciones de reuniones", ""]
    out.append(f"Corte: **{data['as_of']}** · Reuniones: **{t['meetings']}** · Generado localmente, sin LLM.")
    out += ["", "## Resumen", "", "| Métrica | Valor |", "|---|---|"]
    out += [
        f"| Acciones | {t['actions']} |",
        f"| Con responsable | {t['assigned']} |",
        f"| Con fecha | {t['with_due']} |",
        f"| Hechas | {t['done']} |",
        f"| Vencidas | {t['overdue']} |",
        f"| Con cita a KB | {t['linked']} |",
        f"| Decisiones | {t['decisions']} |",
        f"| Preguntas abiertas | {t['questions']} |",
        f"| Confianza media | {t['avg_confidence']} |",
        "",
        "## Tablero por responsable",
        "",
    ]
    for owner, rows in data["board"].items():
        out += [f"### {owner} ({len(rows)})", "", "| Acción | Fecha | Estado | Conf. | Reunión | KB |", "|---|---|---|---|---|---|"]
        for a in sorted(rows, key=lambda x: (x["due_date"] or "9999", x["text"])):
            flag = " ⚠ vencida" if a.get("overdue") else ""
            kb = "; ".join(f"`{lk['citation']}`" for lk in a["links"])
            out.append(
                f"| {_cell(a['text'])} | {a['due_date'] or '—'}{flag} | {STATUS_LABEL.get(a['status'], a['status'])} "
                f"| {a['confidence']:.2f} | {_cell(a['meeting'])} | {kb} |"
            )
        out.append("")
    if data["overdue"]:
        out += ["## Vencidas", ""]
        for a in sorted(data["overdue"], key=lambda x: x["due_date"]):
            out.append(f"- **{a['owner']}** · {a['text']} (vencía {a['due_date']}, {a['meeting']})")
        out.append("")

    out += ["## Detalle por reunión", ""]
    for r in results:
        out.append(f"### {r.title}")
        meta = [f"fuente `{r.source}`"]
        if r.meeting_date:
            meta.insert(0, r.meeting_date)
        if r.attendees:
            meta.append("asistentes: " + ", ".join(r.attendees))
        out += [" · ".join(meta), ""]
        if r.decisions:
            out.append("**Decisiones**")
            out.append("")
            for d in r.decisions:
                cite = f" — KB: {_links_md(d.links)}" if d.links else ""
                out.append(f"- {d.text}{cite}")
            out.append("")
        if r.actions:
            out += ["**Acciones**", "", "| # | Acción | Responsable | Fecha | Estado | Conf. |", "|---|---|---|---|---|---|"]
            for i, a in enumerate(r.actions, 1):
                out.append(
                    f"| {i} | {_cell(a.text)} | {a.owner} | {a.due_date or '—'} | "
                    f"{STATUS_LABEL.get(a.status, a.status)} | {a.confidence:.2f} |"
                )
            out.append("")
            linked = [(i, a) for i, a in enumerate(r.actions, 1) if a.links]
            if linked:
                out += ["**Evidencia en KB**", ""]
                for i, a in linked:
                    for lk in a.links:
                        out.append(f"- Acción {i} → `{lk.citation}` ({lk.title}, cobertura {lk.coverage:.2f}): «{lk.snippet}»")
                out.append("")
        if r.questions:
            out += ["**Preguntas abiertas**", ""]
            for q in r.questions:
                who = f" ({q.owner})" if q.owner else ""
                cite = f" — KB: {_links_md(q.links)}" if q.links else ""
                out.append(f"- {q.text}{who}{cite}")
            out.append("")
        for w in r.warnings:
            out.append(f"> {w}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_reports(
    results: list[MeetingResult], out_dir: str | Path, as_of: date | None = None, stem: str = "meeting_report"
) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{stem}.md"
    json_path = out / f"{stem}.json"
    md_path.write_text(render_markdown(results, as_of), encoding="utf-8")
    json_path.write_text(json.dumps(build_data(results, as_of), ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path
