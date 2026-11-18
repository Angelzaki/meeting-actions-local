from __future__ import annotations

import re
import unicodedata

from meeting_actions.models import UNASSIGNED

TITLES = {"ing", "dr", "dra", "sr", "sra", "srta", "lic", "mg", "arq", "don", "dona"}
GROUP_WORDS = {"todos", "equipo", "team", "all"}
JOINERS = re.compile(r"\s*(?:,|;|/|\by\b|\be\b|\bcon\b|\+|&)\s*", re.IGNORECASE)
NOT_A_PERSON = {
    "pendiente", "tbd", "por definir", "n/a", "na", "ninguno", "nadie", "sin asignar", "-", "?",
}


def fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[\s.]+", fold(name).strip()) if t and t not in TITLES]


def _display(raw: str) -> str:
    parts = [p for p in re.split(r"\s+", raw.strip()) if p]
    parts = [p for p in parts if fold(p).strip(".") not in TITLES]
    return " ".join(p[:1].upper() + p[1:] if p.islower() else p for p in parts)


class OwnerResolver:
    """Resuelve apodos, iniciales y nombres de pila hacia el nombre canonico de un asistente."""

    def __init__(self, aliases: dict[str, str] | None = None):
        self.canonical: list[str] = []
        self._alias: dict[str, str] = {}
        self._ambiguous: set[str] = set()
        self._explicit: set[str] = set()
        for alias, name in (aliases or {}).items():
            self.add_person(name, [alias])

    def add_person(self, name: str, extra_aliases: list[str] | None = None) -> str:
        display = _display(name)
        if not display:
            return display
        if display not in self.canonical:
            self.canonical.append(display)
        toks = _tokens(display)
        keys = {" ".join(toks)}
        if len(toks) > 1:
            keys.add(toks[0])
            keys.add(toks[-1])
            keys.add("".join(t[0] for t in toks))
            keys.add(f"{toks[0]} {toks[-1][0]}")
        explicit = {" ".join(_tokens(extra)) for extra in extra_aliases or []} - {""}
        for key in explicit:
            current = self._alias.get(key)
            if current and current != display and key in self._explicit:
                self._ambiguous.add(key)
            else:
                self._alias[key] = display
                self._ambiguous.discard(key)
            self._explicit.add(key)
        for key in keys - explicit:
            if not key or key in self._explicit:
                continue
            current = self._alias.get(key)
            if current and current != display:
                self._ambiguous.add(key)
            else:
                self._alias[key] = display
        return display

    def add_attendees(self, attendees: list[str]) -> list[str]:
        names: list[str] = []
        for raw in attendees:
            alias = re.search(r"\(([^)]+)\)", raw)
            name = re.sub(r"\([^)]*\)", "", raw).strip(" -,")
            name = re.split(r"\s+[-–—]\s+", name)[0].strip()
            if name:
                names.append(self.add_person(name, [alias.group(1)] if alias else None))
        return names

    def lookup(self, piece: str) -> str | None:
        """Nombre canonico si `piece` es un asistente o alias conocido; None si no."""
        key = " ".join(_tokens(piece.lstrip("@")))
        if not key or key in self._ambiguous:
            return None
        return self._alias.get(key)

    def resolve(self, raw: str) -> str:
        """Devuelve el nombre canonico del primer responsable mencionado."""
        raw = (raw or "").strip().strip(".:;,()[]")
        if not raw or fold(raw) in NOT_A_PERSON:
            return UNASSIGNED
        for piece in JOINERS.split(raw):
            piece = piece.strip()
            if not piece:
                continue
            if fold(piece) in GROUP_WORDS:
                return "Equipo"
            hit = self.lookup(piece)
            if hit:
                return hit
            display = _display(piece.lstrip("@"))
            if display and fold(display) not in NOT_A_PERSON:
                return display
        return UNASSIGNED

    def find_in_text(self, text: str) -> str | None:
        """Busca un asistente conocido nombrado dentro de un texto libre."""
        folded = fold(text)
        best: tuple[int, str] | None = None
        for key, name in self._alias.items():
            if key in self._ambiguous or len(key) < 3:
                continue
            m = re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", folded)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), name)
        return best[1] if best else None
