"""Provider-agnostic text utilities: chunking + numeric helpers.

These functions have no dependency on any embedding model and are reused
by every embedding backend (chunking inputs, normalising outputs).
"""
from __future__ import annotations

import re

import numpy as np


# T17.6: Spanish abbreviations whose trailing period must NOT end a
# sentence. Deliberately conservative — "etc.", "no." and "D." often DO end
# sentences, so they are not protected.
_PROTECTED_ABBREVIATIONS = re.compile(
    r"\b(?:"
    r"Sr|Sra|Srta|Dr|Dra|Dña|Prof|Lic|Ing|"      # honorifics
    r"núm|nro|pág|págs|art|cap|vol|fig|ej|"      # references
    r"aprox|dpto|depto|avda|tfno|tel|"           # misc
    r"Ud|Uds|Vd|Vds|EE|UU"                       # usted / EE. UU.
    r")\.",
    re.IGNORECASE,
)
_ABBR_SENTINEL = "\x00"


def _split_sentences(text: str) -> list[str]:
    """Sentence splitting on `.!?` boundaries.

    T17.6: periods belonging to common Spanish abbreviations (Sr., núm.,
    pág., EE. UU., p. ej. ...) are protected before the split so they no
    longer produce bogus sentence cuts.
    """
    protected = _PROTECTED_ABBREVIATIONS.sub(
        lambda m: m.group(0)[:-1] + _ABBR_SENTINEL, text
    )
    parts = re.split(r"(?<=[.!?])\s+", protected)
    return [
        s.replace(_ABBR_SENTINEL, ".").strip()
        for s in parts
        if s.strip()
    ]


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks by paragraph boundaries, with sentence fallback.

    Returns non-empty chunks of roughly *size* tokens (word-based
    approximation). Paragraphs longer than *size* are subdivided into
    sentences first so no individual chunk grows unboundedly large.
    """
    if not text or not text.strip():
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    raw_units = paragraphs if len(paragraphs) > 1 else _split_sentences(text)

    units: list[str] = []
    for unit in raw_units:
        if len(unit.split()) > size:
            units.extend(_split_sentences(unit))
        else:
            units.append(unit)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for unit in units:
        unit_len = len(unit.split())
        if current_len + unit_len > size and current:
            chunks.append(" ".join(current))
            overlap_words: list[str] = []
            overlap_count = 0
            for part in reversed(current):
                wc = len(part.split())
                if overlap_count + wc > overlap:
                    break
                overlap_words.insert(0, part)
                overlap_count += wc
            current = overlap_words
            current_len = overlap_count
        current.append(unit)
        current_len += unit_len

    if current:
        chunks.append(" ".join(current))

    return [c for c in chunks if len(c.split()) >= 10]


# ---------------------------------------------------------------------------
# Semantic chunking (T11)
# ---------------------------------------------------------------------------

def _normalize_ws(text: str) -> str:
    """Whitespace normalization used for chunk offsets: every run of
    whitespace collapses to one space, ends trimmed. Chunk offsets are
    relative to THIS normalized body (documented invariant:
    ``chunk["text"] == normalize(body)[char_start:char_end]``).
    """
    return re.sub(r"\s+", " ", text or "").strip()


def semantic_chunk_text(
    text: str,
    headings: list[dict] | None = None,
    embed_fn=None,
    *,
    window: int = 3,
    cut_percentile: float = 90.0,
    min_words: int = 80,
    max_words: int = 500,
    chunk_embedding_mode: str = "aggregate",
) -> list[dict]:
    """Split *text* at semantic boundaries (T11).

    1. Hard boundaries first: the page's H2/H3 headings (found by literal
       match in the normalized body) are mandatory cuts, and each chunk
       carries its ``heading_path`` (H1 > H2 > H3).
    2. Within each section: sentences → sliding windows of *window*
       sentences → embeddings via *embed_fn* → cosine distance between
       consecutive windows → cut where distance exceeds the page's own
       *cut_percentile*, respecting ``min_words``/``max_words``.
    3. Chunk embedding: ``aggregate`` = L2-normalized mean of the chunk's
       window embeddings (ZERO extra API calls — reuses step 2);
       ``reembed`` = second pass embedding every full chunk (better
       fidelity, roughly doubles API cost).

    *embed_fn*: callable ``list[str] -> array-like (n, dim)``. When None,
    only heading/max-size cuts apply and embeddings are None.

    Returns dicts: position, heading_path, text, word_count, char_start,
    char_end, embedding (list | None).
    """
    body = _normalize_ws(text)
    if not body:
        return []

    # ---- 1. Hard boundaries from headings ---------------------------------
    boundaries: list[tuple[int, str, str]] = []  # (offset, tag, heading_text)
    cursor = 0
    for h in headings or []:
        h_text = _normalize_ws(h.get("text") or "")
        tag = (h.get("tag") or "").lower()
        if not h_text or tag not in ("h1", "h2", "h3"):
            continue
        idx = body.find(h_text, cursor)
        if idx == -1:
            continue
        boundaries.append((idx, tag, h_text))
        cursor = idx + len(h_text)

    sections: list[tuple[int, int, str | None]] = []  # (start, end, heading_path)
    path: dict[str, str] = {}

    def _current_path() -> str | None:
        parts = [path[t] for t in ("h1", "h2", "h3") if t in path]
        return " > ".join(parts) if parts else None

    prev = 0
    prev_path = None
    for idx, tag, h_text in boundaries:
        if idx > prev:
            sections.append((prev, idx, prev_path))
        path[tag] = h_text
        if tag == "h1":
            path.pop("h2", None)
            path.pop("h3", None)
        elif tag == "h2":
            path.pop("h3", None)
        prev = idx
        prev_path = _current_path()
    sections.append((prev, len(body), prev_path))

    # ---- 2. Sentence windows + embedding cuts per section -----------------
    all_chunks: list[dict] = []

    for sec_start, sec_end, heading_path in sections:
        sec_text = body[sec_start:sec_end]
        sentences = _split_sentences(sec_text)
        if not sentences:
            continue

        # sentence offsets within the normalized body
        sent_spans: list[tuple[int, int]] = []
        cur = sec_start
        for s in sentences:
            i = body.find(s, cur)
            if i == -1:
                i = cur
            sent_spans.append((i, i + len(s)))
            cur = i + len(s)

        cut_after: set[int] = set()  # sentence index after which we cut
        window_embs: list = []
        if embed_fn is not None and len(sentences) > window:
            windows = [
                " ".join(sentences[i:i + window])
                for i in range(len(sentences) - window + 1)
            ]
            window_embs = np.asarray(embed_fn(windows), dtype=float)
            window_embs = l2_normalize(window_embs)
            if len(window_embs) >= 2:
                sims = np.sum(window_embs[:-1] * window_embs[1:], axis=1)
                dists = 1.0 - sims
                threshold = float(np.percentile(dists, cut_percentile))
                for i, d in enumerate(dists):
                    if d >= threshold and d > 0:
                        # window i vs i+1 diverge → cut after the last
                        # sentence fully inside window i
                        cut_after.add(i + window - 1)

        # build chunks respecting min/max sizes
        chunks_sent: list[list[int]] = []
        current: list[int] = []
        current_words = 0
        for si, s in enumerate(sentences):
            wc = len(s.split())
            current.append(si)
            current_words += wc
            boundary = si in cut_after
            if (boundary and current_words >= min_words) or current_words >= max_words:
                chunks_sent.append(current)
                current, current_words = [], 0
        if current:
            if chunks_sent and sum(
                len(sentences[i].split()) for i in current
            ) < min_words:
                chunks_sent[-1].extend(current)
            else:
                chunks_sent.append(current)

        for sent_idxs in chunks_sent:
            start = sent_spans[sent_idxs[0]][0]
            end = sent_spans[sent_idxs[-1]][1]
            chunk_text_val = body[start:end]

            embedding = None
            if len(window_embs) > 0 and chunk_embedding_mode == "aggregate":
                member_windows = [
                    w for w in range(len(window_embs))
                    if sent_idxs[0] <= w <= sent_idxs[-1]
                    and w < len(window_embs)
                ]
                if member_windows:
                    agg = np.mean(window_embs[member_windows], axis=0)
                    embedding = l2_normalize(agg).tolist()

            all_chunks.append({
                "heading_path": heading_path,
                "text": chunk_text_val,
                "word_count": len(chunk_text_val.split()),
                "char_start": start,
                "char_end": end,
                "embedding": embedding,
            })

    # ---- 3. Optional re-embed pass -----------------------------------------
    if embed_fn is not None and chunk_embedding_mode == "reembed" and all_chunks:
        embs = np.asarray(embed_fn([c["text"] for c in all_chunks]), dtype=float)
        embs = l2_normalize(embs)
        for c, e in zip(all_chunks, embs):
            c["embedding"] = e.tolist()

    for pos, c in enumerate(all_chunks):
        c["position"] = pos
    return all_chunks


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalize a vector or each row of a 2D matrix. Safe for zero rows."""
    if vec.ndim == 1:
        n = float(np.linalg.norm(vec))
        return vec if n == 0 else vec / n
    norms = np.linalg.norm(vec, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vec / norms
