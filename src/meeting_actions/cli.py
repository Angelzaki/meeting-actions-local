from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table

from meeting_actions.models import MeetingResult
from meeting_actions.pipeline import Config, Pipeline
from meeting_actions.report import build_data

console = Console()

STATUS_STYLE = {"abierta": "yellow", "en_curso": "cyan", "hecha": "green", "bloqueada": "red"}


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--kb-dir", type=Path, help="carpeta con SOP/KB (.txt/.md); por defecto samples/kb")
    p.add_argument("--no-kb", action="store_true", help="no enlazar acciones con el KB")
    p.add_argument("--results-dir", type=Path, help="carpeta de salida; por defecto results/")
    p.add_argument("--db", type=Path, help="ruta del SQLite de historial")
    p.add_argument("--aliases", type=Path, help="JSON {alias: nombre} para normalizar responsables")
    p.add_argument("--as-of", help="fecha de corte YYYY-MM-DD para marcar vencidas")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="meeting-actions", description="Actas de reunión a acciones, 100% local")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse", help="analiza un acta")
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true", help="imprime el resultado como JSON")
    p.add_argument("--no-save", action="store_true", help="no registrar en el historial")
    _common(p)

    p = sub.add_parser("batch", help="analiza todas las actas de una carpeta")
    p.add_argument("dir", type=Path, nargs="?", help="por defecto samples/meetings")
    p.add_argument("--report", action="store_true", help="genera también results/meeting_report.{md,json}")
    p.add_argument("--no-save", action="store_true")
    _common(p)

    p = sub.add_parser("report", help="genera results/meeting_report.md y .json")
    p.add_argument("dir", type=Path, nargs="?")
    p.add_argument("--out", type=Path, help="carpeta de salida")
    _common(p)

    p = sub.add_parser("serve", help="levanta la UI web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8030)
    _common(p)

    p = sub.add_parser("history", help="historial de corridas en SQLite")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--source", help="filtra por nombre de archivo")
    p.add_argument("--owners", action="store_true", help="carga de acciones abiertas por responsable")
    p.add_argument("--audit", action="store_true", help="muestra la bitácora de eventos")
    _common(p)
    return ap


def _pipeline(args: argparse.Namespace) -> Pipeline:
    cfg = Config()
    if args.kb_dir:
        cfg.kb_dir = args.kb_dir
    if args.results_dir:
        cfg.results_dir = args.results_dir
        if not args.db:
            cfg.db_path = args.results_dir / "history.db"
    if args.db:
        cfg.db_path = args.db
    if args.aliases:
        cfg.aliases_file = args.aliases
    return Pipeline(cfg, use_kb=not args.no_kb)


def _as_of(args: argparse.Namespace) -> date | None:
    return date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else None


def _print_result(r: MeetingResult, as_of: date) -> None:
    head = f"[bold]{r.title}[/bold]  [dim]{r.source} · {r.meeting_date or 'sin fecha'}[/dim]"
    console.print(head)
    if r.attendees:
        console.print(f"[dim]Asistentes: {', '.join(r.attendees)}[/dim]")
    if r.decisions:
        console.print("\n[bold]Decisiones[/bold]")
        for d in r.decisions:
            console.print(f"  • {d.text}")
    table = Table(title="Acciones", show_lines=False)
    for col in ("#", "Acción", "Responsable", "Fecha", "Estado", "Conf.", "KB"):
        table.add_column(col, overflow="fold")
    for i, a in enumerate(r.actions, 1):
        due = a.due_date or "—"
        if a.is_overdue(as_of):
            due = f"[red]{due} (vencida)[/red]"
        owner = a.owner if a.assigned else f"[dim]{a.owner}[/dim]"
        status = f"[{STATUS_STYLE.get(a.status, 'white')}]{a.status}[/]"
        table.add_row(str(i), a.text, owner, due, status, f"{a.confidence:.2f}", ", ".join(l.citation for l in a.links))
    console.print(table)
    if r.questions:
        console.print("[bold]Preguntas abiertas[/bold]")
        for q in r.questions:
            console.print(f"  ? {q.text}")
    for w in r.warnings:
        console.print(f"[yellow]· {w}[/yellow]")


def _print_summary(results: list[MeetingResult], as_of: date) -> None:
    t = build_data(results, as_of)["totals"]
    table = Table(title=f"Lote · corte {as_of}")
    for col in ("Reunión", "Acciones", "Con resp.", "Decisiones", "Preguntas", "Con KB"):
        table.add_column(col)
    for r in results:
        s = r.to_dict(as_of)["stats"]
        table.add_row(r.title, str(s["actions"]), str(s["assigned"]), str(s["decisions"]), str(s["questions"]), str(s["linked"]))
    table.add_section()
    table.add_row("[bold]Total[/bold]", str(t["actions"]), str(t["assigned"]), str(t["decisions"]), str(t["questions"]), str(t["linked"]))
    console.print(table)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = _as_of(args) or date.today()

    if args.cmd == "serve":
        import uvicorn

        from meeting_actions.web import create_app

        console.print(f"UI en [bold]http://{args.host}:{args.port}[/bold]")
        uvicorn.run(create_app(_pipeline(args)), host=args.host, port=args.port, log_level="info")
        return 0

    pipe = _pipeline(args)

    if args.cmd == "parse":
        if not args.file.is_file():
            console.print(f"[red]No existe {args.file}[/red]")
            return 2
        r = pipe.analyze_file(args.file, record=not args.no_save, as_of=as_of)
        if args.json:
            sys.stdout.write(json.dumps(r.to_dict(as_of), ensure_ascii=False, indent=2) + "\n")
        else:
            _print_result(r, as_of)
        return 0

    if args.cmd in ("batch", "report"):
        record = args.cmd == "batch" and not args.no_save
        results = pipe.analyze_batch(args.dir, record=record, as_of=as_of)
        if not results:
            console.print("[red]No se encontraron actas (.md/.txt).[/red]")
            return 2
        _print_summary(results, as_of)
        if args.cmd == "report" or args.report:
            md, js = pipe.report(results, as_of, getattr(args, "out", None))
            console.print(f"Reporte: [bold]{md}[/bold]\nJSON:    [bold]{js}[/bold]")
        return 0

    if args.cmd == "history":
        if args.owners:
            table = Table(title="Carga por responsable (última corrida de cada acta)")
            for col in ("Responsable", "Acciones", "Abiertas"):
                table.add_column(col)
            for row in pipe.store.owner_load():
                table.add_row(row["owner"], str(row["total"]), str(row["open_items"]))
            console.print(table)
            return 0
        if args.audit:
            for ev in pipe.store.audit_events(args.limit):
                console.print(f"[dim]{ev['ts']}[/dim] [bold]{ev['event']}[/bold] {ev['detail']}")
            return 0
        rows = pipe.store.history(args.limit, args.source)
        if not rows:
            console.print("Historial vacío. Ejecuta `meeting-actions batch`.")
            return 0
        table = Table(title="Historial")
        for col in ("Run", "Fecha UTC", "Fuente", "Acta", "Acc.", "Resp.", "Dec.", "Preg.", "KB"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                str(r["id"]), r["ts"], r["source"], r["title"], str(r["n_actions"]), str(r["n_assigned"]),
                str(r["n_decisions"]), str(r["n_questions"]), str(r["n_linked"]),
            )
        console.print(table)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
