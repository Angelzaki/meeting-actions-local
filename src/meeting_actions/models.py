from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

STATUS_OPEN = "abierta"
STATUS_PROGRESS = "en_curso"
STATUS_DONE = "hecha"
STATUS_BLOCKED = "bloqueada"
STATUSES = (STATUS_OPEN, STATUS_PROGRESS, STATUS_DONE, STATUS_BLOCKED)

UNASSIGNED = "Sin asignar"


@dataclass
class KBLink:
    doc: str
    title: str
    chunk: int
    score: float
    coverage: float
    snippet: str
    terms: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        return f"{self.doc}#{self.chunk}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["citation"] = self.citation
        return d


@dataclass
class ActionItem:
    text: str
    owner: str = UNASSIGNED
    owner_raw: str = ""
    due_date: str | None = None
    due_raw: str = ""
    status: str = STATUS_OPEN
    confidence: float = 0.5
    line_no: int = 0
    marker: str = ""
    section: str = ""
    priority: str = ""
    links: list[KBLink] = field(default_factory=list)

    @property
    def assigned(self) -> bool:
        return self.owner != UNASSIGNED

    def is_overdue(self, as_of: date) -> bool:
        if not self.due_date or self.status == STATUS_DONE:
            return False
        return date.fromisoformat(self.due_date) < as_of

    def to_dict(self, as_of: date | None = None) -> dict[str, Any]:
        d = asdict(self)
        d["links"] = [lk.to_dict() for lk in self.links]
        d["assigned"] = self.assigned
        if as_of is not None:
            d["overdue"] = self.is_overdue(as_of)
        return d


@dataclass
class Decision:
    text: str
    line_no: int = 0
    owner: str = ""
    confidence: float = 0.6
    links: list[KBLink] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["links"] = [lk.to_dict() for lk in self.links]
        return d


@dataclass
class OpenQuestion:
    text: str
    line_no: int = 0
    owner: str = ""
    confidence: float = 0.6
    links: list[KBLink] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["links"] = [lk.to_dict() for lk in self.links]
        return d


@dataclass
class MeetingResult:
    meeting_id: str
    source: str
    title: str = ""
    meeting_date: str | None = None
    attendees: list[str] = field(default_factory=list)
    actions: list[ActionItem] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    questions: list[OpenQuestion] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def by_owner(self) -> dict[str, list[ActionItem]]:
        board: dict[str, list[ActionItem]] = {}
        for a in self.actions:
            board.setdefault(a.owner, []).append(a)
        return dict(sorted(board.items(), key=lambda kv: (kv[0] == UNASSIGNED, kv[0])))

    def to_dict(self, as_of: date | None = None) -> dict[str, Any]:
        return {
            "meeting_id": self.meeting_id,
            "source": self.source,
            "title": self.title,
            "meeting_date": self.meeting_date,
            "attendees": self.attendees,
            "actions": [a.to_dict(as_of) for a in self.actions],
            "decisions": [d.to_dict() for d in self.decisions],
            "questions": [q.to_dict() for q in self.questions],
            "warnings": self.warnings,
            "stats": {
                "actions": len(self.actions),
                "assigned": sum(1 for a in self.actions if a.assigned),
                "with_due": sum(1 for a in self.actions if a.due_date),
                "decisions": len(self.decisions),
                "questions": len(self.questions),
                "linked": sum(1 for a in self.actions if a.links),
            },
        }
