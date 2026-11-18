from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from meeting_actions.models import MeetingResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    meeting_id TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    meeting_date TEXT,
    n_actions INTEGER NOT NULL,
    n_assigned INTEGER NOT NULL,
    n_decisions INTEGER NOT NULL,
    n_questions INTEGER NOT NULL,
    n_linked INTEGER NOT NULL,
    kb_docs INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS action_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    owner TEXT NOT NULL,
    text TEXT NOT NULL,
    due_date TEXT,
    status TEXT NOT NULL,
    confidence REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_owner ON action_rows(owner);
CREATE INDEX IF NOT EXISTS idx_runs_meeting ON runs(meeting_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def log(self, event: str, detail: str = "") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO audit (ts, event, detail) VALUES (?, ?, ?)", (_now(), event, detail))

    def record_run(self, result: MeetingResult, kb_docs: int = 0, as_of=None) -> int:
        payload = json.dumps(result.to_dict(as_of), ensure_ascii=False)
        stats = result.to_dict(as_of)["stats"]
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO runs (ts, meeting_id, source, title, meeting_date, n_actions, n_assigned,
                   n_decisions, n_questions, n_linked, kb_docs, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _now(), result.meeting_id, result.source, result.title, result.meeting_date,
                    stats["actions"], stats["assigned"], stats["decisions"], stats["questions"],
                    stats["linked"], kb_docs, payload,
                ),
            )
            run_id = int(cur.lastrowid)
            c.executemany(
                "INSERT INTO action_rows (run_id, owner, text, due_date, status, confidence) VALUES (?,?,?,?,?,?)",
                [(run_id, a.owner, a.text, a.due_date, a.status, a.confidence) for a in result.actions],
            )
            c.execute(
                "INSERT INTO audit (ts, event, detail) VALUES (?, ?, ?)",
                (
                    _now(),
                    "parse",
                    json.dumps(
                        {"run_id": run_id, "source": result.source, "actions": stats["actions"],
                         "decisions": stats["decisions"], "questions": stats["questions"], "kb_docs": kb_docs},
                        ensure_ascii=False,
                    ),
                ),
            )
        return run_id

    def history(self, limit: int = 20, source: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, ts, meeting_id, source, title, meeting_date, n_actions, n_assigned, n_decisions, "
            "n_questions, n_linked, kb_docs FROM runs"
        )
        args: list[Any] = []
        if source:
            sql += " WHERE source LIKE ?"
            args.append(f"%{source}%")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def latest_per_meeting(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT payload FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY source) ORDER BY id"
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def owner_load(self) -> list[dict[str, Any]]:
        """Acciones abiertas por responsable, usando la ultima corrida de cada fuente."""
        with self._conn() as c:
            rows = c.execute(
                """SELECT owner, COUNT(*) AS total,
                          SUM(CASE WHEN status != 'hecha' THEN 1 ELSE 0 END) AS open_items
                   FROM action_rows
                   WHERE run_id IN (SELECT MAX(id) FROM runs GROUP BY source)
                   GROUP BY owner ORDER BY open_items DESC, owner"""
            ).fetchall()
        return [dict(r) for r in rows]

    def audit_events(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]

    def clear(self) -> None:
        with self._conn() as c:
            c.executescript("DELETE FROM action_rows; DELETE FROM runs; DELETE FROM audit;")
