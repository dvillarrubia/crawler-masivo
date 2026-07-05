"""Núcleo puro de la extracción: chunking, normalización y agregación.

Sin GLiNER2, sin DB, sin red — todo testeable con fakes (patrón de la
casa: link_suggester / query_coverage).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ~384 tokens ≈ ~290 palabras en castellano; solape para no partir spans.
CHUNK_WORDS = 290
CHUNK_OVERLAP_WORDS = 40

# Peso por campo para la entidad primaria (determinista, sin LLM):
# lo que aparece en title/H1 define la página más que una mención en body.
FIELD_WEIGHT = {"title": 3.0, "h1": 3.0, "body": 1.0}

_WS_RE = re.compile(r"\s+")


def chunk_text(text: str, *, size: int = CHUNK_WORDS,
               overlap: int = CHUNK_OVERLAP_WORDS) -> list[tuple[int, str]]:
    """Trocea por palabras con solape. Devuelve (offset_caracteres, chunk)."""
    words = (text or "").split()
    if not words:
        return []
    if len(words) <= size:
        return [(0, " ".join(words))]
    chunks: list[tuple[int, str]] = []
    step = size - overlap
    # offsets aproximados por reconstrucción (suficiente para spans agregados)
    pos = 0
    joined: list[str] = []
    offsets: list[int] = []
    for w in words:
        offsets.append(pos)
        joined.append(w)
        pos += len(w) + 1
    for start in range(0, len(words), step):
        piece = words[start:start + size]
        if not piece:
            break
        chunks.append((offsets[start], " ".join(piece)))
        if start + size >= len(words):
            break
    return chunks


def normalize_entity_text(text: str) -> str:
    """minúsculas + sin acentos + espacios colapsados — clave de dedup."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return _WS_RE.sub(" ", t).strip()


def slugify(text: str) -> str:
    """`local:{slug}` del contrato Seontology para entidades sin QID."""
    norm = normalize_entity_text(text)
    return re.sub(r"[^a-z0-9]+", "-", norm).strip("-") or "sin-nombre"


@dataclass
class Span:
    """Un span crudo tal y como sale del modelo."""

    text: str
    entity_type: str
    start: int
    end: int
    confidence: float
    source_field: str = "body"


@dataclass
class Mention:
    """Spans agregados por (texto normalizado, tipo) dentro de una URL."""

    entity_text: str        # forma normalizada
    display_text: str       # primera forma vista (para humanos)
    entity_type: str
    source_field: str       # el campo de MÁS peso donde apareció
    frequency: int
    span_start: int | None
    span_end: int | None
    confidence: float       # máxima del grupo
    weight: float           # Σ frequency×FIELD_WEIGHT — para entidad primaria


def aggregate_spans(spans: list[Span]) -> list[Mention]:
    """Deduplica por texto normalizado + tipo y agrega frecuencia/peso."""
    grouped: dict[tuple[str, str], Mention] = {}
    for s in spans:
        norm = normalize_entity_text(s.text)
        if not norm:
            continue
        key = (norm, s.entity_type)
        m = grouped.get(key)
        w = FIELD_WEIGHT.get(s.source_field, 1.0)
        if m is None:
            grouped[key] = Mention(
                entity_text=norm, display_text=s.text.strip(),
                entity_type=s.entity_type, source_field=s.source_field,
                frequency=1, span_start=s.start, span_end=s.end,
                confidence=s.confidence, weight=w,
            )
        else:
            m.frequency += 1
            m.weight += w
            m.confidence = max(m.confidence, s.confidence)
            if FIELD_WEIGHT.get(s.source_field, 1.0) > FIELD_WEIGHT.get(m.source_field, 1.0):
                m.source_field = s.source_field
    return sorted(grouped.values(), key=lambda m: -m.weight)


def primary_entity(mentions: list[Mention], *, resolved_only: bool = True,
                   resolved_ids: dict[tuple[str, str], str] | None = None) -> str | None:
    """Entidad primaria de una URL: mayor peso (frecuencia × campo).

    Determinista, sin LLM (invariante del brief). Si ``resolved_only``,
    solo cuentan menciones con entity_id resuelto (``resolved_ids`` mapea
    (entity_text, entity_type) → entity_id) y devuelve ese entity_id.
    """
    best: tuple[float, str] | None = None
    for m in mentions:
        if resolved_only:
            eid = (resolved_ids or {}).get((m.entity_text, m.entity_type))
            if not eid:
                continue
            candidate = eid
        else:
            candidate = m.entity_text
        if best is None or m.weight > best[0]:
            best = (m.weight, candidate)
    return best[1] if best else None
