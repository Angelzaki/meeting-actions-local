from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from meeting_actions.owners import fold

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
WEEKDAYS = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6,
}
_MONTH_RE = "|".join(MONTHS)
_DAY_RE = "|".join(WEEKDAYS)
_CUE = (
    r"(?:para el|para|antes del|antes de|hasta el|hasta|a mas tardar el|a mas tardar|"
    r"limite el|limite|vence el|vence|plazo|entrega el|entrega|el proximo|proximo|este|(?<![a-z])el)\s+"
)


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _next_weekday(ref: date, target: int, allow_today: bool = False) -> date:
    delta = (target - ref.weekday()) % 7
    if delta == 0 and not allow_today:
        delta = 7
    return ref + timedelta(days=delta)


def _add_business_days(ref: date, n: int) -> date:
    cur = ref
    while n > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur


def _year_for(ref: date, month: int, day: int) -> date | None:
    cand = _safe(ref.year, month, day)
    if cand is None:
        return None
    if (ref - cand).days > 270:
        return _safe(ref.year + 1, month, day)
    return cand


def _absolute(f: str, ref: date) -> tuple[date, tuple[int, int]] | None:
    m = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", f)
    if m:
        d = _safe(int(m[1]), int(m[2]), int(m[3]))
        if d:
            return d, m.span()
    m = re.search(r"(?<![\d/])(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})(?![\d/])", f)
    if m:
        y = int(m[3])
        y = y + 2000 if y < 100 else y
        d = _safe(y, int(m[2]), int(m[1]))
        if d:
            return d, m.span()
    m = re.search(rf"(?<!\d)(\d{{1,2}})\s+(?:de\s+)?({_MONTH_RE})(?:\s+(?:de|del)\s+(\d{{4}}))?", f)
    if m:
        year = int(m[3]) if m[3] else None
        d = _safe(year, MONTHS[m[2]], int(m[1])) if year else _year_for(ref, MONTHS[m[2]], int(m[1]))
        if d:
            return d, m.span()
    m = re.search(r"(?<![\d/-])(\d{1,2})/(\d{1,2})(?![\d/])", f)
    if m:
        d = _year_for(ref, int(m[2]), int(m[1]))
        if d:
            return d, m.span()
    return None


def _relative(f: str, ref: date, strict_cue: bool) -> tuple[date, tuple[int, int]] | None:
    m = re.search(r"\bpasado manana\b", f)
    if m:
        return ref + timedelta(days=2), m.span()
    m = re.search(r"\bmanana\b", f)
    if m:
        return ref + timedelta(days=1), m.span()
    m = re.search(r"\b(?:fin|fines|final) de(?:l)? mes\b", f)
    if m:
        return _safe(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1]), m.span()  # type: ignore[return-value]
    m = re.search(r"\b(?:fin|fines|final) de(?:l)? (?:ano|anio)\b", f)
    if m:
        return date(ref.year, 12, 31), m.span()
    m = re.search(r"\b(?:fin|fines|final) de(?:l)? trimestre\b", f)
    if m:
        q_end = ((ref.month - 1) // 3 + 1) * 3
        return date(ref.year, q_end, calendar.monthrange(ref.year, q_end)[1]), m.span()
    m = re.search(r"\b(?:fin|fines|final) de(?:l)? (?:la )?semana\b|\besta semana\b", f)
    if m:
        return ref + timedelta(days=(4 - ref.weekday()) % 7), m.span()
    m = re.search(r"\b(?:la )?(?:proxima semana|semana proxima|semana que viene)\b", f)
    if m:
        monday = ref + timedelta(days=7 - ref.weekday())
        return monday + timedelta(days=4), m.span()
    m = re.search(r"\ben (\d{1,2}|un|una|dos|tres|cuatro|cinco) (dia|dias|semana|semanas|mes|meses)(?: (habiles))?\b", f)
    if m:
        words = {"un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
        n = words.get(m[1]) or int(m[1])
        unit = m[2]
        if unit.startswith("dia"):
            d = _add_business_days(ref, n) if m[3] else ref + timedelta(days=n)
        elif unit.startswith("semana"):
            d = ref + timedelta(weeks=n)
        else:
            month_index = ref.month - 1 + n
            year, month = ref.year + month_index // 12, month_index % 12 + 1
            d = _safe(year, month, min(ref.day, calendar.monthrange(year, month)[1]))  # type: ignore[assignment]
        return d, m.span()
    pattern = rf"(?:{_CUE})?(?P<wd>{_DAY_RE})\b" if not strict_cue else rf"{_CUE}(?P<wd>{_DAY_RE})\b"
    m = re.search(pattern, f)
    if m:
        allow_today = bool(re.search(r"\beste\b", m[0]))
        return _next_weekday(ref, WEEKDAYS[m["wd"]], allow_today), m.span()
    m = re.search(r"\b(?:hoy|eod|cierre del dia|fin del dia)\b", f)
    if m and not strict_cue:
        return ref, m.span()
    return None


def find_due(text: str, ref: date, bare: bool = False) -> tuple[str, str] | None:
    """Busca una fecha en texto libre. `bare=True` acepta valores sueltos (campo `fecha:`)."""
    folded = fold(text)
    hit = _absolute(folded, ref)
    if hit is None:
        hit = _relative(folded, ref, strict_cue=not bare)
    if hit is None:
        return None
    d, (a, b) = hit
    return d.isoformat(), text[a:b].strip()


def parse_due(value: str, ref: date) -> str | None:
    found = find_due(value, ref, bare=True)
    return found[0] if found else None


def parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    found = find_due(value, date.today(), bare=True)
    return date.fromisoformat(found[0]) if found else None
