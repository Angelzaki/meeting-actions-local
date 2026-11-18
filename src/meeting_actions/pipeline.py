from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from meeting_actions.linker import KBIndex, link_result, load_kb
from meeting_actions.models import MeetingResult
from meeting_actions.owners import OwnerResolver
from meeting_actions.parse import parse_meeting
from meeting_actions.report import write_reports
from meeting_actions.store import Store

MEETING_SUFFIXES = {".md", ".txt"}


@dataclass
class Config:
    kb_dir: Path | None = field(default_factory=lambda: _env_path("MEETING_KB_DIR", "samples/kb"))
    results_dir: Path = field(default_factory=lambda: _env_path("MEETING_RESULTS_DIR", "results") or Path("results"))
    db_path: Path | None = field(default_factory=lambda: _env_path("MEETING_DB", None))
    aliases_file: Path | None = field(default_factory=lambda: _env_path("MEETING_ALIASES", None))
    meetings_dir: Path = field(default_factory=lambda: _env_path("MEETING_SAMPLES", "samples/meetings") or Path("samples/meetings"))

    def __post_init__(self) -> None:
        if self.db_path is None:
            self.db_path = self.results_dir / "history.db"


def _env_path(name: str, default: str | None) -> Path | None:
    value = os.environ.get(name, default)
    return Path(value) if value else None


def load_aliases(path: Path | None) -> dict[str, str]:
    """JSON simple {"alias": "Nombre Canonico"} para apodos que el acta no declara."""
    if not path or not Path(path).is_file():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def list_meeting_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in MEETING_SUFFIXES)


class Pipeline:
    def __init__(self, config: Config | None = None, kb: KBIndex | None = None, use_kb: bool = True):
        self.config = config or Config()
        self.aliases = load_aliases(self.config.aliases_file)
        self.kb = kb if kb is not None else (load_kb(self.config.kb_dir) if use_kb else None)
        self.store = Store(self.config.db_path)  # type: ignore[arg-type]

    @property
    def kb_docs(self) -> int:
        return len(self.kb.docs) if self.kb else 0

    def analyze_text(
        self,
        text: str,
        source: str = "pegado",
        reference_date: date | None = None,
        record: bool = True,
        as_of: date | None = None,
        link_kb: bool = True,
    ) -> MeetingResult:
        resolver = OwnerResolver(self.aliases)
        result = parse_meeting(text, source=source, resolver=resolver, reference_date=reference_date)
        if link_kb:
            link_result(result, self.kb)
        if record:
            self.store.record_run(result, kb_docs=self.kb_docs, as_of=as_of)
        return result

    def analyze_file(self, path: str | Path, record: bool = True, as_of: date | None = None) -> MeetingResult:
        p = Path(path)
        return self.analyze_text(
            p.read_text(encoding="utf-8", errors="replace"), source=p.name, record=record, as_of=as_of
        )

    def analyze_batch(
        self, directory: str | Path | None = None, record: bool = True, as_of: date | None = None
    ) -> list[MeetingResult]:
        files = list_meeting_files(Path(directory or self.config.meetings_dir))
        return [self.analyze_file(f, record=record, as_of=as_of) for f in files]

    def report(
        self, results: list[MeetingResult], as_of: date | None = None, out_dir: str | Path | None = None
    ) -> tuple[Path, Path]:
        paths = write_reports(results, out_dir or self.config.results_dir, as_of)
        self.store.log("report", json.dumps({"md": str(paths[0]), "meetings": len(results)}, ensure_ascii=False))
        return paths
