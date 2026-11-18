from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from meeting_actions.models import KBLink, MeetingResult
from meeting_actions.owners import fold

STOPWORDS = set(
    """
    a al algo ante antes aqui asi aun cada como con contra cual cuando de del desde donde dos el ella ellos en entre
    era es esa ese eso esta estas este esto estos fue ha han hasta hay la las le les lo los mas me mi muy no nos nuestro
    o otra otro para pero por porque que quien se sea ser si sin sobre su sus tambien tiene tienen todo todos tu un una
    uno unos va y ya
    enviar envia revisar revision preparar hacer realizar definir confirmar coordinar validar actualizar compartir
    agendar programar llevar generar acordar pendiente accion tarea semana proxima lunes martes miercoles jueves viernes
    sabado domingo hoy manana equipo reunion debe deben debera
    """.split()
)

SUFFIXES = (
    "aciones", "acion", "amientos", "amiento", "mente", "idades", "idad", "adores", "ador", "ando", "iendo",
    "aran", "eran", "aron", "ieron", "ado", "ada", "ido", "ida", "ar", "er", "ir", "es", "s",
)


def stem(token: str) -> str:
    for suf in SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            token = token[: -len(suf)]
            break
    if len(token) >= 5 and token[-1] in "aoe":
        token = token[:-1]
    return token


def tokenize(text: str, drop: set[str] | None = None) -> list[str]:
    out = []
    for tok in re.findall(r"[a-z0-9]+", fold(text)):
        if tok in STOPWORDS or (drop and tok in drop):
            continue
        if len(tok) < 3 and not tok.isdigit():
            continue
        out.append(stem(tok))
    return out


@dataclass
class Chunk:
    doc: str
    title: str
    index: int
    text: str
    tokens: Counter
    length: int


def _split_paragraph(par: str, limit: int = 420) -> list[str]:
    if len(par) <= limit:
        return [par]
    sentences = re.split(r"(?<=[.;:])\s+", par)
    chunks, cur = [], ""
    for sent in sentences:
        if cur and len(cur) + len(sent) > limit:
            chunks.append(cur.strip())
            cur = ""
        cur += sent + " "
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def chunk_document(name: str, text: str) -> list[Chunk]:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    title = next((ln.strip("# ").strip() for ln in lines if ln.strip()), name)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    merged: list[str] = []
    for p in paragraphs:
        if merged and len(merged[-1]) < 60:
            merged[-1] = f"{merged[-1]}\n{p}"
        else:
            merged.append(p)
    chunks: list[Chunk] = []
    for par in merged:
        for piece in _split_paragraph(par):
            toks = tokenize(piece)
            if not toks:
                continue
            chunks.append(Chunk(name, title, len(chunks) + 1, piece, Counter(toks), len(toks)))
    return chunks


class KBIndex:
    """Indice BM25 en memoria sobre fragmentos de SOP/KB (sin tildes, con stems livianos)."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.avg_len = (sum(c.length for c in chunks) / len(chunks)) if chunks else 0.0
        df: Counter = Counter()
        for c in chunks:
            df.update(c.tokens.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @property
    def empty(self) -> bool:
        return not self.chunks

    @property
    def docs(self) -> list[str]:
        return sorted({c.doc for c in self.chunks})

    def search(
        self,
        query: str,
        top_k: int = 2,
        min_terms: int = 2,
        min_coverage: float = 0.4,
        drop: set[str] | None = None,
    ) -> list[KBLink]:
        q_tokens = list(dict.fromkeys(tokenize(query, drop)))
        known = [t for t in q_tokens if t in self.idf]
        if self.empty or len(known) < 1:
            return []
        q_total = sum(self.idf[t] for t in known) or 1.0
        scored: list[tuple[float, float, Chunk, list[str]]] = []
        for c in self.chunks:
            matched = [t for t in known if t in c.tokens]
            if len(matched) < min_terms:
                continue
            score = 0.0
            for t in matched:
                tf = c.tokens[t]
                denom = tf + self.k1 * (1 - self.b + self.b * c.length / (self.avg_len or 1))
                score += self.idf[t] * tf * (self.k1 + 1) / denom
            coverage = sum(self.idf[t] for t in matched) / q_total
            if coverage < min_coverage:
                continue
            scored.append((score, coverage, c, matched))
        scored.sort(key=lambda r: (-r[0], r[2].doc, r[2].index))
        links: list[KBLink] = []
        seen_docs: set[str] = set()
        for score, coverage, c, matched in scored:
            if c.doc in seen_docs:
                continue
            seen_docs.add(c.doc)
            links.append(
                KBLink(
                    doc=c.doc,
                    title=c.title,
                    chunk=c.index,
                    score=round(score, 3),
                    coverage=round(coverage, 3),
                    snippet=_snippet(c.text, set(matched)),
                    terms=matched,
                )
            )
            if len(links) >= top_k:
                break
        return links


def _snippet(text: str, terms: set[str], width: int = 220) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.;:])\s+", flat)
    best = max(sentences, key=lambda s: len(terms & set(tokenize(s))))
    best = best.strip()
    return best if len(best) <= width else best[: width - 1].rstrip() + "…"


def load_kb(directory: str | Path | None) -> KBIndex | None:
    """Carga *.txt y *.md de un directorio. Devuelve None si no existe o esta vacio."""
    if not directory:
        return None
    root = Path(directory)
    if not root.is_dir():
        return None
    chunks: list[Chunk] = []
    for path in sorted(list(root.glob("*.txt")) + list(root.glob("*.md"))):
        chunks.extend(chunk_document(path.name, path.read_text(encoding="utf-8", errors="replace")))
    return KBIndex(chunks) if chunks else None


def link_result(result: MeetingResult, index: KBIndex | None, top_k: int = 2) -> MeetingResult:
    """Adjunta citas del KB a acciones, decisiones y preguntas con evidencia lexica suficiente."""
    if index is None or index.empty:
        return result
    names = {t for name in [*result.attendees, *(a.owner for a in result.actions)] for t in re.findall(r"[a-z0-9]+", fold(name))}
    for a in result.actions:
        a.links = index.search(a.text, top_k=top_k, drop=names)
    for d in result.decisions:
        d.links = index.search(d.text, top_k=top_k, drop=names)
    for q in result.questions:
        q.links = index.search(q.text, top_k=top_k, drop=names)
    return result
