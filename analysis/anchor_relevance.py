"""
T18 (cierre) — relevancia de anchors contextuales.

Dos señales sobre los anchors de los enlaces contextuales internos:

- ``generic_anchor``          lexical, sin coste: anchors de una stoplist
                              ("clic aquí", "leer más", …), demasiado
                              cortos o puramente numéricos. Se agrega por
                              URL destino (la página que se queda sin
                              señal semántica en sus inlinks).
- ``anchor_target_mismatch``  embedding en runtime: el anchor se embebe
                              como *query* (RETRIEVAL_QUERY, igual que
                              T19) y se compara con el vector de página
                              del destino (``semantic_pages``). Coseno
                              bajo el umbral = el anchor promete algo que
                              la página no es.

Ambos son juicios → issues firmables (patrón T10): nacen ``pending``,
los pending se reemplazan en cada run y las decisiones firmadas
sobreviven.

Pure core (:func:`is_generic_anchor`, :func:`classify_anchors`) + DB
wrapper (:func:`run_anchor_relevance`). El core recibe vectores
inyectados — testeable sin Gemini ni pgvector.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_MISMATCH_THRESHOLD = 0.35
DEFAULT_MAX_ANCHORS = 300
MAX_ANCHOR_EMBED_CHARS = 200

_ISSUE_TYPES = ("generic_anchor", "anchor_target_mismatch")

# Stoplist normalizada (minúsculas, sin acentos). ES + EN.
GENERIC_ANCHORS = {
    "aqui", "click aqui", "clic aqui", "haz clic aqui", "haz click aqui",
    "pincha aqui", "pulsa aqui", "este enlace", "enlace", "link",
    "leer mas", "ver mas", "saber mas", "mas info", "mas informacion",
    "mas detalles", "seguir leyendo", "continuar leyendo", "sigue leyendo",
    "descargar", "descarga", "entrar", "web", "pagina", "articulo",
    "click here", "click", "here", "read more", "more", "learn more",
    "see more", "more info", "more information", "details", "this",
    "this link", "download", "continue reading", "go", "view", "page",
}

_NON_ALNUM_RE = re.compile(r"[^0-9a-z\s]")
_WS_RE = re.compile(r"\s+")


def _normalize_anchor(text: str) -> str:
    """lowercase + sin acentos + sin puntuación + espacios colapsados."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = _NON_ALNUM_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def is_generic_anchor(text: str) -> bool:
    """True si el anchor no aporta señal semántica sobre el destino."""
    norm = _normalize_anchor(text)
    if not norm or len(norm) <= 2:
        return True
    if norm.isdigit():
        return True
    return norm in GENERIC_ANCHORS


@dataclass
class AnchorGroup:
    """Un anchor distinto hacia un destino concreto, agregado."""

    anchor: str
    target_hash: str
    n_links: int = 0
    source_urls: list = field(default_factory=list)  # muestra (≤5)


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def classify_anchors(
    groups: list[AnchorGroup],
    anchor_vec_by_text: dict,
    target_vec_by_hash: dict,
    *,
    mismatch_threshold: float = DEFAULT_MISMATCH_THRESHOLD,
) -> dict:
    """Separa genéricos (lexical) de mismatches (coseno anchor↔destino).

    Los genéricos no necesitan vector. Un grupo no genérico sin vector de
    anchor o de destino se cuenta en ``skipped`` (no se juzga sin datos).
    """
    generic: list[AnchorGroup] = []
    mismatches: list[dict] = []
    skipped = 0

    for g in groups:
        if is_generic_anchor(g.anchor):
            generic.append(g)
            continue
        a_vec = anchor_vec_by_text.get(g.anchor)
        t_vec = target_vec_by_hash.get(g.target_hash)
        if a_vec is None or t_vec is None:
            skipped += 1
            continue
        sim = float(_cosine(a_vec, t_vec))  # float nativo: details va a JSON
        if sim < mismatch_threshold:
            mismatches.append({"group": g, "similarity": round(sim, 4)})

    return {"generic": generic, "mismatches": mismatches, "skipped": skipped}


def run_anchor_relevance(
    session,
    job_id,
    analysis_id,
    backend,
    *,
    mismatch_threshold: float = DEFAULT_MISMATCH_THRESHOLD,
    max_anchors: int = DEFAULT_MAX_ANCHORS,
) -> dict:
    """Agrega los anchors contextuales del job, embebe los no genéricos
    (cap ``max_anchors`` por nº de enlaces) y emite los dos issues
    firmables. Devuelve el informe para la UI.

    Contextual = ``edge_class='contextual'`` si el job clasificó aristas;
    para jobs sin clasificar, ``link_position='content'``.
    """
    from shared.models import Issue, Link, Url
    from shared.semantic_models import SemanticPage

    rows = (
        session.query(
            Link.anchor_text, Link.to_url_hash, Link.edge_class,
            Link.link_position, Url.url,
        )
        .join(Url, Url.id == Link.from_url_id)
        .filter(
            Link.job_id == job_id,
            Link.is_internal.is_(True),
            Link.follow.isnot(False),
            Link.anchor_text.isnot(None),
            Link.anchor_text != "",
        )
        .all()
    )
    grouped: dict[tuple[str, str], AnchorGroup] = {}
    for anchor, target_hash, edge_class, position, source_url in rows:
        contextual = (
            edge_class == "contextual"
            if edge_class is not None
            else position == "content"
        )
        if not contextual:
            continue
        anchor = anchor.strip()
        if not anchor:
            continue
        g = grouped.setdefault(
            (anchor, target_hash), AnchorGroup(anchor=anchor, target_hash=target_hash),
        )
        g.n_links += 1
        if len(g.source_urls) < 5 and source_url not in g.source_urls:
            g.source_urls.append(source_url)

    if not grouped:
        return {"status": "blocked", "reason": "no_contextual_anchors"}
    groups = sorted(grouped.values(), key=lambda g: -g.n_links)

    # Vectores de destino: página del análisis semántico, via url_hash.
    target_rows = (
        session.query(Url.url_hash, Url.id, Url.url, SemanticPage.embedding)
        .join(SemanticPage, SemanticPage.url_id == Url.id)
        .filter(
            SemanticPage.analysis_id == analysis_id,
            SemanticPage.embedding.isnot(None),
        )
        .all()
    )
    if not target_rows:
        return {"status": "blocked", "reason": "no_page_vectors"}
    target_vec_by_hash = {r[0]: tuple(r[3]) for r in target_rows}
    target_info_by_hash = {r[0]: (r[1], r[2]) for r in target_rows}

    # Embebe solo anchors no genéricos con destino vectorizado (coste).
    to_embed: list[str] = []
    seen: set[str] = set()
    for g in groups:
        if is_generic_anchor(g.anchor) or g.anchor in seen:
            continue
        if g.target_hash not in target_vec_by_hash:
            continue
        seen.add(g.anchor)
        to_embed.append(g.anchor[:MAX_ANCHOR_EMBED_CHARS])
        if len(to_embed) >= max_anchors:
            break
    anchor_vec_by_text: dict = {}
    if to_embed:
        vectors = backend.embed_queries(to_embed)
        anchor_vec_by_text = {
            text: tuple(vectors[i]) for i, text in enumerate(to_embed)
        }

    result = classify_anchors(
        groups, anchor_vec_by_text, target_vec_by_hash,
        mismatch_threshold=mismatch_threshold,
    )

    # -- issues firmables (T10: reemplaza pending, respeta decisiones) --
    session.query(Issue).filter(
        Issue.job_id == job_id,
        Issue.issue_type.in_(_ISSUE_TYPES),
        Issue.review_status == "pending",
    ).delete(synchronize_session=False)

    # generic_anchor: agregado por URL destino (para no inundar la cola).
    generic_by_target: dict[str, list[AnchorGroup]] = {}
    for g in result["generic"]:
        generic_by_target.setdefault(g.target_hash, []).append(g)

    # Destinos sin vector también reciben el issue genérico: la señal es
    # lexical. Resolvemos su url_id aparte.
    missing_hashes = [
        h for h in generic_by_target if h not in target_info_by_hash
    ]
    if missing_hashes:
        for uid, url, url_hash in session.query(
            Url.id, Url.url, Url.url_hash,
        ).filter(Url.job_id == job_id, Url.url_hash.in_(missing_hashes)).all():
            target_info_by_hash[url_hash] = (uid, url)

    n_generic = 0
    generic_report = []
    for target_hash, gs in generic_by_target.items():
        info = target_info_by_hash.get(target_hash)
        if info is None:
            continue
        url_id, url = info
        anchors = sorted(gs, key=lambda g: -g.n_links)
        total_links = sum(g.n_links for g in gs)
        details = {
            "generic_inlinks": total_links,
            "anchors": [g.anchor for g in anchors[:10]],
            "sources_sample": anchors[0].source_urls[:5],
        }
        session.add(Issue(
            job_id=job_id, url_id=url_id, issue_type="generic_anchor",
            severity="info", review_status="pending", details=details,
        ))
        n_generic += 1
        generic_report.append({"target_url": url, **details})

    n_mismatch = 0
    mismatch_report = []
    for m in result["mismatches"]:
        g = m["group"]
        info = target_info_by_hash.get(g.target_hash)
        if info is None:
            continue
        url_id, url = info
        details = {
            "anchor": g.anchor,
            "similarity": m["similarity"],
            "n_links": g.n_links,
            "sources_sample": g.source_urls,
            "mismatch_threshold": mismatch_threshold,
        }
        session.add(Issue(
            job_id=job_id, url_id=url_id, issue_type="anchor_target_mismatch",
            severity="warning", review_status="pending", details=details,
        ))
        n_mismatch += 1
        mismatch_report.append({"target_url": url, **details})
    session.flush()

    mismatch_report.sort(key=lambda x: x["similarity"])
    generic_report.sort(key=lambda x: -x["generic_inlinks"])
    logger.info(
        "T18 anchors job %s: %d grupos, %d embebidos, %d generic (por "
        "destino), %d mismatches", job_id, len(groups), len(to_embed),
        n_generic, n_mismatch,
    )
    return {
        "status": "ok",
        "summary": {
            "anchor_groups": len(groups),
            "embedded": len(to_embed),
            "generic_targets": n_generic,
            "mismatches": n_mismatch,
            "skipped_no_vector": result["skipped"],
            "params": {
                "mismatch_threshold": mismatch_threshold,
                "max_anchors": max_anchors,
            },
        },
        "generic": generic_report[:50],
        "mismatches": mismatch_report[:50],
    }
