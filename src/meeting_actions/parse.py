from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

from meeting_actions.dates import find_due, parse_due
from meeting_actions.models import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_OPEN,
    STATUS_PROGRESS,
    UNASSIGNED,
    ActionItem,
    Decision,
    MeetingResult,
    OpenQuestion,
)
from meeting_actions.owners import OwnerResolver, fold

BULLET = re.compile(r"^\s*(?:[-*•▪·]|\d{1,2}[.)])\s+")
CHECKBOX = re.compile(r"^\[(?P<mark>[ xX✓✔])\]\s*")
MD_HEADING = re.compile(r"^\s{0,3}(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
PLAIN_HEADING = re.compile(r"^\s*(?P<title>[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ /]{3,}):?\s*$")

ACTION_WORDS = (
    r"acci[oó]n(?:es)?(?:\s+a\s+tomar)?|action(?:\s+items?)?|a\.?i\.?|tareas?|por\s+hacer|"
    r"seguimientos?|compromisos?|siguientes?\s+pasos?|next\s+steps?|pendientes?"
)
DECISION_WORDS = (
    r"decisi[oó]n(?:es)?|acuerdos?|se\s+acord[oó]|se\s+decidi[oó]|"
    r"qued[oó]\s+(?:acordado|definido|aprobado)|resoluci[oó]n(?:es)?"
)
QUESTION_WORDS = (
    r"preguntas?(?:\s+abiertas?)?|dudas?|puntos?\s+abiertos?|abiertos?|por\s+definir|"
    r"pendientes?\s+de\s+definir|open\s+questions?|tbd|consultas?|a\s+definir|sin\s+resolver"
)


def _marker(words: str) -> re.Pattern[str]:
    return re.compile(
        rf"^\[?(?P<m>{words})\]?(?P<paren>\s*[\(\[][^)\]]*[\)\]])?\s*(?::|[-–—](?=\s)|(?<=\])|\.(?=\s)|$)\s*(?P<rest>.*)$",
        re.IGNORECASE,
    )


ACTION_RE = _marker(ACTION_WORDS)
DECISION_RE = _marker(DECISION_WORDS)
QUESTION_RE = _marker(QUESTION_WORDS)
TODO_RE = re.compile(
    r"^(?P<m>todo|fixme)(?:\s*[\(\[](?P<paren>[^)\]]*)[\)\]])?\s*[:\-–]?\s*(?P<rest>.*)$", re.IGNORECASE
)

DECISION_NATURAL = re.compile(
    r"^(?:se\s+acord[oó]|se\s+decidi[oó]|se\s+aprob[oó]|qued[oó]\s+(?:acordado|definido|aprobado)|"
    r"el\s+comit[eé]\s+aprob[oó]|el\s+equipo\s+(?:acord[oó]|decidi[oó]))\s+.+$",
    re.IGNORECASE,
)

NAME = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,2}"
NAT_COMMIT = re.compile(
    rf"^(?P<who>{NAME}(?:\s+(?:y|e)\s+{NAME})?)\s+"
    r"(?:se\s+encargar[áa]|se\s+compromete|se\s+hace\s+cargo|tomar[áa]|deber[áa]|debe|va\s+a|"
    r"ser[áa]\s+responsable|quedar[áa]\s+a\s+cargo|llevar[áa])\s+.+$"
)
NAT_FUTURE = re.compile(rf"^(?P<who>{NAME})\s+[a-záéíóúñ]+(?:ará|erá|irá)\s+.+$")
NAT_MENTION = re.compile(r"^@(?P<who>[\wáéíóúñ.]+)[:,]?\s+.+$", re.IGNORECASE)
NAT_NAMED_INF = re.compile(rf"^(?P<who>{NAME})\s*[:\-–]\s*(?P<what>[a-záéíóúñ]+(?:ar|er|ir)\b.*)$")
NAT_NEED = re.compile(
    r"^(?:hay\s+que|se\s+debe(?:n)?|es\s+necesario|necesitamos|debemos|tenemos\s+que)\s+.+$",
    re.IGNORECASE,
)
NOT_NAMES = {"el", "la", "los", "las", "un", "una", "se", "esto", "eso", "ello", "todo", "hay", "no", "si"}

META_DATE = re.compile(r"^(?:fecha|date)\s*:\s*(?P<v>.+)$", re.IGNORECASE)
META_ATT = re.compile(r"^(?:asistentes|participantes|presentes|attendees)\s*:\s*(?P<v>.*)$", re.IGNORECASE)
META_TITLE = re.compile(r"^(?:reuni[oó]n|t[ií]tulo|tema|asunto)\s*:\s*(?P<v>.+)$", re.IGNORECASE)
META_IGNORE = re.compile(
    r"^(?:hora|lugar|facilitador[a]?|redact[oó]|moderador[a]?|acta|pr[oó]xima\s+reuni[oó]n)\s*:", re.IGNORECASE
)

FIELD_KEYS = {
    "owner": r"responsables?|resp\.?|owner|asignad[oa](?:\s+a)?|a\s+cargo|encargad[oa]|duen[oa]",
    "due": r"fecha\s+l[ií]mite|fecha|plazo|due(?:\s+date)?|vence|deadline|entrega",
    "status": r"estado|status",
    "priority": r"prioridad|prio|priority",
}
FIELD_RE = re.compile(
    r"(?<![\wáéíóúñ])(?:"
    + "|".join(f"(?P<{k}>{v})" for k, v in FIELD_KEYS.items())
    + r")\s*[:=]\s*",
    re.IGNORECASE,
)
FIELD_LINE = re.compile(r"^\s*(?:" + "|".join(FIELD_KEYS.values()) + r")\s*[:=]", re.IGNORECASE)

IMPLICIT_OWNER = re.compile(
    r"(?:a\s+cargo\s+de|asignad[oa]\s+a|responsable\s+(?:ser[áa]|es)|encargad[oa]\s+(?:es|ser[áa]))\s+(?P<who>"
    + NAME
    + r"(?:\s+(?:y|e)\s+"
    + NAME
    + r")?)"
)

STATUS_TAG = re.compile(
    r"\s*[\(\[]\s*(?P<s>hech[oa]|completad[oa]|terminad[oa]|done|en\s+curso|en\s+progreso|bloquead[oa])\s*[\)\]]"
    r"|\s*[✔✅]"
    r"|\s+[-–—]\s*(?:hech[oa]|completad[oa]|terminad[oa])\b.*$",
    re.IGNORECASE,
)
STATUS_DONE_RE = re.compile(r"\b(?:hech[oa]|completad[oa]|terminad[oa]|cerrad[oa]|entregad[oa]|done)\b", re.IGNORECASE)
STATUS_PROGRESS_RE = re.compile(r"\b(?:en\s+curso|en\s+progreso|en\s+proceso|avanzando|wip|iniciad[oa])\b", re.IGNORECASE)
STATUS_BLOCKED_RE = re.compile(r"\b(?:bloquead[oa]|bloqueo|impedid[oa]|a\s+la\s+espera\s+de)\b", re.IGNORECASE)

SECTION_KEYS = [
    ("attendees", r"asistentes|participantes|presentes|attendees"),
    ("decision", r"decisi|acuerdo|resolucion"),
    ("question", r"pregunta|duda|abiert|por definir|open question|sin resolver"),
    ("action", r"accion|tarea|compromiso|pendiente|siguientes? pasos?|next step|action|por hacer|seguimiento"),
]


def section_kind(title: str) -> str:
    f = fold(title)
    for kind, pattern in SECTION_KEYS:
        if re.search(pattern, f):
            return kind
    return "other"


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|__", "", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" |;,-–—:")
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text[:1].upper() + text[1:] if text else text


def extract_fields(text: str) -> tuple[str, dict[str, str]]:
    """Separa campos `clave: valor` (responsable, fecha, estado, prioridad) del texto libre."""
    fields: dict[str, str] = {}
    matches = list(FIELD_RE.finditer(text))
    if not matches:
        return text, fields
    pieces: list[str] = [text[: matches[0].start()]]
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        value = text[m.end():end]
        stop = re.search(r"[|;)\]]", value)
        rest = ""
        if stop:
            if value[stop.start()] in "|;":
                rest = value[stop.end():]
            else:
                rest = value[stop.start():]
            value = value[: stop.start()]
        value = value.strip(" ,-–—")
        kind = next(k for k in FIELD_KEYS if m.group(k))
        fields.setdefault(kind, value)
        pieces.append(" " + rest)
    return "".join(pieces).strip(" |;"), fields


def _detect_status(text: str, fields: dict[str, str], checked: bool) -> tuple[str, str]:
    """Devuelve (estado, texto sin etiquetas de estado)."""
    status = STATUS_OPEN
    if checked:
        status = STATUS_DONE
    tag = STATUS_TAG.search(text)
    if tag:
        label = fold(tag["s"] or "done")
        if label.startswith(("hech", "complet", "termin", "done")):
            status = STATUS_DONE
        elif label.startswith("bloq"):
            status = STATUS_BLOCKED
        else:
            status = STATUS_PROGRESS
        text = STATUS_TAG.sub("", text)
    if status == STATUS_OPEN:
        probe = fields.get("status") or ""
        if probe:
            if STATUS_BLOCKED_RE.search(probe):
                status = STATUS_BLOCKED
            elif STATUS_DONE_RE.search(probe):
                status = STATUS_DONE
            elif STATUS_PROGRESS_RE.search(probe):
                status = STATUS_PROGRESS
    if status == STATUS_OPEN:
        if STATUS_BLOCKED_RE.search(text):
            status = STATUS_BLOCKED
        elif STATUS_PROGRESS_RE.search(text):
            status = STATUS_PROGRESS
    return status, text


def _split_attendees(value: str) -> list[str]:
    parts = re.split(r"[;,](?![^()]*\))", value)
    return [p.strip(" -•*") for p in parts if p.strip(" -•*")]


class _Parser:
    def __init__(self, resolver: OwnerResolver, ref: date):
        self.resolver = resolver
        self.ref = ref

    def owner_from(self, text: str, fields: dict[str, str], paren: str = "") -> tuple[str, str, str, bool]:
        """Devuelve (texto sin marcas, owner_raw, owner canonico, explicito)."""
        if fields.get("owner"):
            raw = fields["owner"]
            return text, raw, self.resolver.resolve(raw), True
        if paren and paren.strip():
            raw = paren.strip()
            resolved = self.resolver.resolve(raw)
            if resolved != UNASSIGNED:
                return text, raw, resolved, True
        m = re.search(r"(?<![\w@])@(?P<who>[\wáéíóúñ.]+)", text)
        if m:
            raw = m["who"].rstrip(".")
            return (text[: m.start()] + text[m.end():]), raw, self.resolver.resolve(raw), True
        m = IMPLICIT_OWNER.search(text)
        if m:
            return text, m["who"], self.resolver.resolve(m["who"]), True
        tokens = text.split()
        for n in (3, 2, 1):
            cand = " ".join(tokens[:n]).strip(" :,-–")
            hit = self.resolver.lookup(cand) if cand else None
            if hit and cand[:1].isupper():
                rest = re.sub(r"^[:\-–,]\s*", "", " ".join(tokens[n:]))
                verbal = re.match(r"^(?:se\s+\w+|\w+(?:ará|erá|irá|ó|a|e))\b", rest, re.IGNORECASE)
                return (text if verbal else (rest or text)), cand, hit, False
        for par in re.finditer(r"\(([^()]+)\)", text):
            hit = self.resolver.lookup(par[1])
            if hit:
                return text[: par.start()] + text[par.end():], par[1], hit, True
        tail = re.search(
            r"\s[-–—]\s*(?P<who>[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)?)\s*[.]?$", text
        )
        if tail:
            hit = self.resolver.lookup(tail["who"])
            if hit:
                return text[: tail.start()], tail["who"], hit, True
        return text, "", UNASSIGNED, False

    def build_action(
        self,
        rest: str,
        *,
        marker: str,
        base: float,
        line_no: int,
        section: str,
        paren: str = "",
        checked: bool = False,
        owner_hint: str = "",
    ) -> ActionItem | None:
        text, fields = extract_fields(rest)
        if owner_hint:
            fields.setdefault("owner", owner_hint)
        text, owner_raw, owner, explicit = self.owner_from(text, fields, paren)
        status, text = _detect_status(text, fields, checked)
        due_iso, due_raw = None, ""
        if fields.get("due"):
            due_iso = parse_due(fields["due"], self.ref)
            due_raw = fields["due"]
        if due_iso is None:
            found = find_due(text, self.ref)
            if found is None and fields.get("owner"):
                found = find_due(fields["owner"], self.ref)
            if found:
                due_iso, due_raw = found
        text = _clean(text)
        if len(text) < 4:
            return None
        conf = base
        if owner != UNASSIGNED:
            conf += 0.15 if explicit else 0.1
        if due_iso:
            conf += 0.1
        if len(text.split()) < 3:
            conf -= 0.15
        return ActionItem(
            text=text,
            owner=owner,
            owner_raw=owner_raw,
            due_date=due_iso,
            due_raw=due_raw,
            status=status,
            confidence=round(max(0.05, min(conf, 0.99)), 2),
            line_no=line_no,
            marker=marker,
            section=section,
            priority=(fields.get("priority") or "").strip().lower(),
        )


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", fold(value)).strip("-")
    return value[:40] or "acta"


def _dedupe_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", fold(text))[:70]


def date_from_name(name: str) -> str | None:
    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", name)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


def _apply_fields(parser: _Parser, last: ActionItem | Decision | OpenQuestion, fields: dict[str, str]) -> None:
    if fields.get("owner"):
        owner = parser.resolver.resolve(fields["owner"])
        if isinstance(last, ActionItem):
            last.owner, last.owner_raw = owner, fields["owner"]
            if owner != UNASSIGNED:
                last.confidence = round(min(0.99, last.confidence + 0.15), 2)
        else:
            last.owner = owner if owner != UNASSIGNED else ""
    if not isinstance(last, ActionItem):
        return
    if fields.get("due"):
        iso = parse_due(fields["due"], parser.ref)
        if iso:
            if not last.due_date:
                last.confidence = round(min(0.99, last.confidence + 0.1), 2)
            last.due_date, last.due_raw = iso, fields["due"]
    if fields.get("status"):
        last.status, _ = _detect_status("", fields, False)
    if fields.get("priority"):
        last.priority = fields["priority"].strip().lower()


def _classify(
    body: str, section: str, is_bullet: bool, is_checkbox: bool, resolver: OwnerResolver
) -> tuple[str | None, str, str, str, float, str]:
    """Devuelve (tipo, texto, marcador, parentesis, confianza base, responsable sugerido)."""
    stripped = body.strip()
    if not stripped:
        return None, "", "", "", 0.0, ""

    m = DECISION_RE.match(stripped)
    if m:
        if not m["rest"].strip():
            return "section", "", "decision", "", 0.0, ""
        return "decision", m["rest"], fold(m["m"]), (m["paren"] or "").strip("()[] "), 0.75, ""
    m = QUESTION_RE.match(stripped)
    if m:
        if not m["rest"].strip():
            return "section", "", "question", "", 0.0, ""
        return "question", m["rest"], fold(m["m"]), (m["paren"] or "").strip("()[] "), 0.7, ""
    m = TODO_RE.match(stripped)
    if m and (m["rest"].strip() or m["paren"]):
        return "action", m["rest"], "todo", (m["paren"] or "").strip(), 0.7, ""
    m = ACTION_RE.match(stripped)
    if m:
        marker = fold(m["m"])
        if not m["rest"].strip():
            return "section", "", "action", "", 0.0, ""
        base = 0.6 if marker.startswith("pendiente") else 0.7
        return "action", m["rest"], marker, (m["paren"] or "").strip("()[] "), base, ""

    if is_checkbox:
        return "action", stripped, "checkbox", "", 0.65, ""
    if DECISION_NATURAL.match(stripped):
        return "decision", stripped, "natural", "", 0.6, ""
    if stripped.startswith("¿") and stripped.endswith("?"):
        return "question", stripped, "interrogacion", "", 0.55, ""

    if section == "decision" and is_bullet:
        return "decision", stripped, "section", "", 0.6, ""
    if section == "question" and is_bullet:
        return "question", stripped, "section", "", 0.6, ""

    m = NAT_COMMIT.match(stripped)
    if m and m["who"].split()[0].lower() not in NOT_NAMES:
        return "action", stripped, "natural", "", 0.55, m["who"]
    m = NAT_MENTION.match(stripped)
    if m:
        return "action", stripped, "mencion", "", 0.6, m["who"]
    m = NAT_NAMED_INF.match(stripped)
    if m and (section == "action" or is_bullet or resolver.lookup(m["who"])):
        return "action", m["what"], "nombre", "", 0.6, m["who"]
    m = NAT_FUTURE.match(stripped)
    if m and m["who"].split()[0].lower() not in NOT_NAMES and (
        is_bullet or section == "action" or resolver.lookup(m["who"])
    ):
        return "action", stripped, "natural", "", 0.5, m["who"]
    if NAT_NEED.match(stripped):
        return "action", stripped, "necesidad", "", 0.4, ""

    if section == "action" and is_bullet:
        return "action", stripped, "section", "", 0.6, ""
    return None, "", "", "", 0.0, ""


def parse_meeting(
    text: str,
    source: str = "pegado",
    resolver: OwnerResolver | None = None,
    reference_date: date | None = None,
) -> MeetingResult:
    """Extrae acciones, decisiones y preguntas abiertas de un acta en texto plano o markdown."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    resolver = resolver or OwnerResolver()
    stem = Path(source).stem if source and source != "pegado" else ""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]

    title = ""
    meeting_date: str | None = None
    raw_attendees: list[str] = []
    lines = text.split("\n")

    in_att = False
    for raw in lines:
        s = re.sub(r"\*\*|__", "", raw).strip()
        body = BULLET.sub("", s, count=1) if BULLET.match(s) else s
        h = MD_HEADING.match(raw) or PLAIN_HEADING.match(raw)
        if h:
            in_att = section_kind(h["title"]) == "attendees"
            continue
        ma = META_ATT.match(body)
        if ma:
            if ma["v"].strip():
                raw_attendees.extend(_split_attendees(ma["v"]))
                in_att = False
            else:
                in_att = True
            continue
        if in_att:
            if BULLET.match(s):
                raw_attendees.append(body)
            elif s:
                in_att = False
    attendees = list(dict.fromkeys(resolver.add_attendees(raw_attendees)))

    for raw in lines:
        s = re.sub(r"\*\*|__", "", raw).strip()
        mt = META_DATE.match(s)
        if mt:
            meeting_date = parse_due(mt["v"], date.today())
            break
    if meeting_date is None and stem:
        meeting_date = date_from_name(stem)
    if meeting_date is None and reference_date is not None:
        meeting_date = reference_date.isoformat()
    ref = date.fromisoformat(meeting_date) if meeting_date else date.today()
    parser = _Parser(resolver, ref)

    actions: list[ActionItem] = []
    decisions: list[Decision] = []
    questions: list[OpenQuestion] = []
    warnings: list[str] = []
    last: ActionItem | Decision | OpenQuestion | None = None
    last_indent = 0
    section = "other"
    seen_item = False
    collecting_att = False

    for no, raw in enumerate(lines, start=1):
        if not raw.strip():
            last = None
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = re.sub(r"\*\*|__", "", raw).strip()

        h = MD_HEADING.match(raw)
        if h:
            kind = section_kind(h["title"])
            if kind == "other" and not title and len(h["hashes"]) <= 2 and not seen_item:
                title = _clean(h["title"])
            section = kind
            collecting_att = kind == "attendees"
            last = None
            continue
        ph = PLAIN_HEADING.match(raw)
        if ph and not BULLET.match(raw):
            section = section_kind(ph["title"])
            collecting_att = section == "attendees"
            last = None
            continue

        is_bullet = bool(BULLET.match(stripped))
        body = BULLET.sub("", stripped, count=1) if is_bullet else stripped

        if collecting_att and is_bullet:
            continue
        collecting_att = False
        if META_IGNORE.match(body):
            continue
        ma = META_ATT.match(body)
        if ma:
            collecting_att = not ma["v"].strip()
            continue
        mm = META_TITLE.match(body)
        if mm and not seen_item:
            title = title or _clean(mm["v"])
            continue
        if META_DATE.match(body) and not seen_item and last is None:
            continue

        if FIELD_LINE.match(body) and last is not None:
            _, fields = extract_fields(body)
            _apply_fields(parser, last, fields)
            continue

        checked = False
        cb = CHECKBOX.match(body)
        if cb:
            checked = cb["mark"] in "xX✓✔"
            body = body[cb.end():]

        kind, rest, marker, paren, base, owner_hint = _classify(body, section, is_bullet, bool(cb), resolver)
        if kind is None and not title and not seen_item and not is_bullet and len(body) <= 100 and no <= 3:
            letters = [c for c in body if c.isalpha()]
            shouting = bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.6
            title = _clean(body.capitalize() if shouting else body)
            continue
        if kind is None:
            if last is not None and indent > last_indent and not is_bullet:
                last.text = _clean(f"{last.text} {body}")
            else:
                last = None
            continue
        if kind == "section":
            section = marker
            last = None
            continue

        seen_item = True
        last_indent = indent
        if kind == "action":
            act = parser.build_action(
                rest,
                marker=marker,
                base=base,
                line_no=no,
                section=section,
                paren=paren,
                checked=checked,
                owner_hint=owner_hint,
            )
            if act is None:
                last = None
                continue
            existing = next((a for a in actions if _dedupe_key(a.text) == _dedupe_key(act.text)), None)
            if existing is None:
                actions.append(act)
                last = act
            else:
                if act.confidence > existing.confidence:
                    actions[actions.index(existing)] = act
                    existing = act
                last = existing
        elif kind == "decision":
            text_, fields = extract_fields(rest)
            text_ = _clean(text_)
            if len(text_) < 4:
                last = None
                continue
            dec = Decision(text=text_, line_no=no, confidence=base)
            if fields.get("owner"):
                dec.owner = resolver.resolve(fields["owner"])
            if _dedupe_key(text_) not in {_dedupe_key(d.text) for d in decisions}:
                decisions.append(dec)
            last = dec
        elif kind == "question":
            text_, fields = extract_fields(rest)
            text_, _, owner_, explicit_ = parser.owner_from(text_, fields, paren)
            text_ = _clean(text_)
            if len(text_) < 4:
                last = None
                continue
            q = OpenQuestion(text=text_, line_no=no, confidence=base)
            if owner_ != UNASSIGNED and explicit_:
                q.owner = owner_
            if _dedupe_key(text_) not in {_dedupe_key(x.text) for x in questions}:
                questions.append(q)
            last = q

    if not actions:
        warnings.append("No se detectaron acciones; revisa si el acta usa marcadores como 'Acción:' o 'TODO'.")
    unassigned = sum(1 for a in actions if not a.assigned)
    if unassigned:
        warnings.append(f"{unassigned} acción(es) sin responsable.")
    undated = sum(1 for a in actions if not a.due_date and a.status != STATUS_DONE)
    if undated:
        warnings.append(f"{undated} acción(es) abiertas sin fecha.")

    base_id = _slug(stem or title)
    return MeetingResult(
        meeting_id=f"{base_id}-{digest}",
        source=source,
        title=title or stem.replace("_", " ").replace("-", " ").strip() or "Reunión sin título",
        meeting_date=meeting_date,
        attendees=attendees,
        actions=actions,
        decisions=decisions,
        questions=questions,
        warnings=warnings,
    )
