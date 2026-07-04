"""
Architecture layer (T22 + T23).

T22 — edge classifier (three passes) + aggregated ``arch_edges``:
1. DOM rule from ``links.dom_ancestor``/``dom_container`` (T17.5.b), with
   per-client selector overrides (``client_selectors``).
2. Statistical sitewide rule: a target linked from more than
   ``sitewide_threshold`` of indexable pages is sitewide → reclassified to
   ``menu`` unless already menu/footer.
3. Template rule: a (container, target) pair repeated across more than
   ``template_threshold`` of the pages of one segment is a ``listado``
   even when it lives in <main> — the automatic related-posts module vs
   the real editorial link.

T23 — real click depth (BFS from the seeds over ALL edges), contextual
counters, section flows and the deterministic ARQ checks (emitted by the
analyzer, not here).

Everything here is post-processing over persisted data: no crawler
changes, safe to re-run (idempotent).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict, deque

from sqlalchemy import delete, select, update

from shared.models import (
    ArchEdge,
    ClientSelector,
    Link,
    SectionFlow,
    Url,
    UrlSegment,
)

logger = logging.getLogger(__name__)

EDGE_CLASSES = (
    "contextual", "listado", "breadcrumb", "paginacion",
    "menu", "footer", "sidebar", "desconocido",
)

# T3.6 / T23: authority weight per edge class (used for section flows).
EDGE_CLASS_WEIGHT = {
    "contextual": 1.0,
    "listado": 0.6,
    "paginacion": 0.4,
    "breadcrumb": 0.3,
    "sidebar": 0.25,
    "menu": 0.2,
    "footer": 0.1,
    "desconocido": 0.5,
}

_BREADCRUMB_RE = re.compile(r"breadcrumb|miga", re.IGNORECASE)
_PAGINATION_RE = re.compile(r"pagination|page-numbers|paginado", re.IGNORECASE)
_LISTING_RE = re.compile(r"related|listing|grid|card|archive|relacionad", re.IGNORECASE)
_SIDEBAR_RE = re.compile(r"sidebar", re.IGNORECASE)

DEFAULT_SITEWIDE_THRESHOLD = 0.8
DEFAULT_TEMPLATE_THRESHOLD = 0.9


def classify_edge_dom(
    dom_ancestor: str | None,
    dom_container: str | None,
    rel: str | None = None,
    client_rules: list[tuple[str, str]] | None = None,
) -> str:
    """Pass 1: DOM rule for one link occurrence."""
    container = dom_container or ""

    # Client-specific selectors win (each CMS has its own class names).
    for edge_class, selector in client_rules or []:
        if selector and selector.lower() in container.lower():
            return edge_class

    ancestor = (dom_ancestor or "").lower()
    rel_tokens = {t.strip().lower() for t in (rel or "").split()}

    if _BREADCRUMB_RE.search(container):
        return "breadcrumb"
    if _PAGINATION_RE.search(container) or rel_tokens & {"prev", "next"}:
        return "paginacion"
    if ancestor in ("nav", "header"):
        return "menu"
    if ancestor == "footer":
        return "footer"
    if ancestor == "aside" or _SIDEBAR_RE.search(container):
        return "sidebar"
    if _LISTING_RE.search(container):
        return "listado"
    if ancestor in ("main", "article"):
        return "contextual"
    return "desconocido"


def classify_edges(
    session,
    job_id,
    client_id: str | None,
    *,
    sitewide_threshold: float = DEFAULT_SITEWIDE_THRESHOLD,
    template_threshold: float = DEFAULT_TEMPLATE_THRESHOLD,
) -> dict:
    """T22: run the three passes, fill ``links.edge_class`` and materialize
    ``arch_edges``. Returns summary counters.
    """
    client_rules: list[tuple[str, str]] = []
    if client_id:
        client_rules = [
            (r.edge_class, r.selector)
            for r in session.execute(
                select(ClientSelector).where(ClientSelector.client_id == client_id)
            ).scalars()
        ]

    # Source page metadata
    url_rows = session.execute(
        select(Url.id, Url.url_hash, Url.indexable, Url.status_code,
               Url.is_internal, Url.is_html)
        .where(Url.job_id == job_id)
    ).all()
    hash_by_id = {r.id: r.url_hash for r in url_rows}
    indexable_pages = {
        r.id for r in url_rows
        if r.is_internal and r.is_html
        and r.status_code is not None and 200 <= r.status_code < 300
        and r.indexable is not False
    }
    n_indexable = max(1, len(indexable_pages))

    # Segment of each page (T12) for the template pass
    seg_by_url = dict(session.execute(
        select(UrlSegment.url_id, UrlSegment.segment_id)
        .where(UrlSegment.job_id == job_id)
    ).all())

    link_rows = session.execute(
        select(Link.id, Link.from_url_id, Link.to_url_hash, Link.rel,
               Link.dom_ancestor, Link.dom_container, Link.anchor_text)
        .where(Link.job_id == job_id, Link.is_internal.is_(True))
    ).all()

    # ---- pass 1: DOM ------------------------------------------------------
    edge_class: dict[int, str] = {}
    sources_per_target: dict[str, set[int]] = defaultdict(set)
    pair_by_segment: dict[tuple, set[int]] = defaultdict(set)  # (seg, container, target) → source pages
    seg_sizes: dict[int, set[int]] = defaultdict(set)

    for r in link_rows:
        edge_class[r.id] = classify_edge_dom(
            r.dom_ancestor, r.dom_container, r.rel, client_rules,
        )
        if r.from_url_id in indexable_pages:
            sources_per_target[r.to_url_hash].add(r.from_url_id)
            seg = seg_by_url.get(r.from_url_id)
            if seg is not None:
                pair_by_segment[(seg, r.dom_container, r.to_url_hash)].add(r.from_url_id)
    for uid in indexable_pages:
        seg = seg_by_url.get(uid)
        if seg is not None:
            seg_sizes[seg].add(uid)

    # ---- pass 2: sitewide -------------------------------------------------
    sitewide_targets = {
        target for target, sources in sources_per_target.items()
        if len(sources) / n_indexable > sitewide_threshold
    }
    for r in link_rows:
        if r.to_url_hash in sitewide_targets and edge_class[r.id] not in ("menu", "footer"):
            edge_class[r.id] = "menu"

    # ---- pass 3: template (within segment) ---------------------------------
    template_pairs = set()
    for (seg, container, target), sources in pair_by_segment.items():
        seg_size = len(seg_sizes.get(seg) or ())
        if seg_size >= 2 and len(sources) / seg_size > template_threshold:
            template_pairs.add((container, target))
    if template_pairs:
        for r in link_rows:
            if (
                (r.dom_container, r.to_url_hash) in template_pairs
                and r.to_url_hash not in sitewide_targets
                and edge_class[r.id] == "contextual"
            ):
                edge_class[r.id] = "listado"

    # ---- persist links.edge_class ------------------------------------------
    by_class: dict[str, list[int]] = defaultdict(list)
    for link_id, cls in edge_class.items():
        by_class[cls].append(link_id)
    for cls, ids in by_class.items():
        for start in range(0, len(ids), 5000):
            session.execute(
                update(Link)
                .where(Link.id.in_(ids[start:start + 5000]))
                .values(edge_class=cls)
            )
    session.flush()

    # ---- materialize arch_edges ---------------------------------------------
    session.execute(delete(ArchEdge).where(ArchEdge.job_id == job_id))

    agg: dict[tuple[str, str, str], dict] = {}
    for r in link_rows:
        cls = edge_class[r.id]
        src_hash = hash_by_id.get(r.from_url_id)
        if src_hash is None:
            continue
        sitewide = r.to_url_hash in sitewide_targets
        key = ("*" if sitewide else src_hash, r.to_url_hash, cls)
        entry = agg.setdefault(key, {"n_pages": set(), "sitewide": sitewide, "anchor": None})
        entry["n_pages"].add(r.from_url_id)
        if entry["anchor"] is None and r.anchor_text:
            entry["anchor"] = r.anchor_text[:256]

    for (src, dst, cls), entry in agg.items():
        session.add(ArchEdge(
            job_id=job_id, source_hash=src, target_hash=dst, edge_class=cls,
            n_pages=len(entry["n_pages"]), sitewide=entry["sitewide"],
            anchor_sample=entry["anchor"],
        ))
    session.flush()

    return {
        "links_classified": len(edge_class),
        "sitewide_targets": len(sitewide_targets),
        "arch_edges": len(agg),
        "by_class": {c: len(ids) for c, ids in by_class.items()},
    }


def compute_click_depth(session, job_id, seed_hashes: set[str]) -> dict[int, int]:
    """T23: BFS from the seeds over ALL internal edges (sitewide included).
    Fills ``urls.click_depth``; returns {url_id: depth}. Indexables left
    without depth are link-orphans (issue emitted by the analyzer).
    """
    url_rows = session.execute(
        select(Url.id, Url.url_hash)
        .where(
            Url.job_id == job_id,
            Url.is_internal.is_(True),
            (Url.status_group.is_(None)) | (Url.status_group != "not_crawled"),
        )
    ).all()
    id_by_hash = {r.url_hash: r.id for r in url_rows}

    adj: dict[int, set[int]] = defaultdict(set)
    for from_id, to_hash in session.execute(
        select(Link.from_url_id, Link.to_url_hash)
        .where(Link.job_id == job_id, Link.is_internal.is_(True))
    ).all():
        to_id = id_by_hash.get(to_hash)
        if to_id is not None:
            adj[from_id].add(to_id)

    depth: dict[int, int] = {}
    queue: deque[int] = deque()
    for h in seed_hashes:
        uid = id_by_hash.get(h)
        if uid is not None and uid not in depth:
            depth[uid] = 0
            queue.append(uid)

    while queue:
        current = queue.popleft()
        for nxt in adj.get(current, ()):
            if nxt not in depth:
                depth[nxt] = depth[current] + 1
                queue.append(nxt)

    session.execute(
        update(Url).where(Url.job_id == job_id).values(click_depth=None)
    )
    for uid, d in depth.items():
        session.execute(
            update(Url).where(Url.id == uid).values(click_depth=d)
        )
    session.flush()
    return depth


def compute_contextual_counters(session, job_id) -> None:
    """T23: in_contextual / out_contextual per URL from classified links."""
    url_rows = session.execute(
        select(Url.id, Url.url_hash).where(Url.job_id == job_id)
    ).all()
    id_by_hash = {r.url_hash: r.id for r in url_rows}

    in_ctx: dict[int, int] = defaultdict(int)
    out_ctx: dict[int, int] = defaultdict(int)
    for from_id, to_hash in session.execute(
        select(Link.from_url_id, Link.to_url_hash)
        .where(
            Link.job_id == job_id,
            Link.is_internal.is_(True),
            Link.edge_class == "contextual",
        )
    ).all():
        out_ctx[from_id] += 1
        to_id = id_by_hash.get(to_hash)
        if to_id is not None:
            in_ctx[to_id] += 1

    session.execute(
        update(Url).where(Url.job_id == job_id)
        .values(in_contextual=0, out_contextual=0)
    )
    for uid, n in in_ctx.items():
        session.execute(update(Url).where(Url.id == uid).values(in_contextual=n))
    for uid, n in out_ctx.items():
        session.execute(update(Url).where(Url.id == uid).values(out_contextual=n))
    session.flush()


def compute_section_flows(session, job_id, damping: float = 0.85) -> float:
    """T23: authority flow segment→segment.

    Per classified edge: ``flow = d × PR(source) × weight / Σ weights out
    of source``. Aggregated into ``section_flows`` (segment id 0 = "(sin
    segmento)"). Returns the total flow mass (conservation invariant: it
    approximates d × Σ PR of pages with outlinks).
    """
    url_rows = session.execute(
        select(Url.id, Url.url_hash, Url.pagerank)
        .where(Url.job_id == job_id, Url.is_internal.is_(True))
    ).all()
    id_by_hash = {r.url_hash: r.id for r in url_rows}
    pr_by_id = {r.id: (r.pagerank or 0.0) for r in url_rows}

    seg_by_url = dict(session.execute(
        select(UrlSegment.url_id, UrlSegment.segment_id)
        .where(UrlSegment.job_id == job_id)
    ).all())

    link_rows = session.execute(
        select(Link.from_url_id, Link.to_url_hash, Link.edge_class)
        .where(
            Link.job_id == job_id,
            Link.is_internal.is_(True),
            Link.edge_class.isnot(None),
        )
    ).all()

    out_weight: dict[int, float] = defaultdict(float)
    for from_id, to_hash, cls in link_rows:
        if to_hash in id_by_hash:
            out_weight[from_id] += EDGE_CLASS_WEIGHT.get(cls, 0.5)

    flows: dict[tuple[int, int], float] = defaultdict(float)
    total = 0.0
    for from_id, to_hash, cls in link_rows:
        to_id = id_by_hash.get(to_hash)
        if to_id is None or out_weight[from_id] <= 0:
            continue
        w = EDGE_CLASS_WEIGHT.get(cls, 0.5)
        flow = damping * pr_by_id.get(from_id, 0.0) * w / out_weight[from_id]
        seg_from = seg_by_url.get(from_id, 0) or 0
        seg_to = seg_by_url.get(to_id, 0) or 0
        flows[(seg_from, seg_to)] += flow
        total += flow

    session.execute(delete(SectionFlow).where(SectionFlow.job_id == job_id))
    for (seg_from, seg_to), flow in flows.items():
        session.add(SectionFlow(
            job_id=job_id, segment_from=seg_from, segment_to=seg_to,
            flow=round(flow, 6),
        ))
    session.flush()
    return total
