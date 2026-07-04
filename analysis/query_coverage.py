"""
T19 — query→passage coverage (needs T9 GSC query rows + T11 chunks).

Answers, per GSC query: "does any persisted passage of the site actually
cover this demand?" by crossing runtime query embeddings
(RETRIEVAL_QUERY) against the analysis' persisted chunk embeddings
(``semantic_chunks``, T11).

Pure core (:func:`compute_coverage`) + DB wrapper
(:func:`run_query_coverage`). The core is vector-agnostic (plain
lists/arrays in, dict out) so it is unit-testable without Gemini or
pgvector; the wrapper aggregates ``gsc_query_data``, embeds the queries
through the analysis' Gemini account, persists ``query_embeddings`` and
emits three SIGNABLE issue types (T10 pattern — born ``pending``,
nothing auto-accepts):

- ``passage_gap``      query with demand but no chunk over the coverage
                       threshold (issue on the URL that ranks for it).
- ``buried_passage``   the covering chunk sits deep in its page
                       (``position >= buried_min_position``).
- ``orphan_chunk``     chunks matching no query at all, aggregated per
                       URL (content answering no measured demand).

Scale note: the exact path is a blocked matrix product (queries are
capped, chunks stream in blocks). On Postgres with very large chunk sets
(> ``HNSW_PAIR_THRESHOLD`` query×chunk pairs) the per-query best chunk
is resolved through the pgvector HNSW index instead
(``ix_semantic_chunks_embedding``); orphan detection then only sees the
retrieved pairs and is reported as approximate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_SIM_THRESHOLD = 0.60
DEFAULT_BURIED_MIN_POSITION = 5
DEFAULT_ORPHAN_THRESHOLD = 0.50
DEFAULT_MAX_QUERIES = 200
DEFAULT_MIN_IMPRESSIONS = 10

# Above this many query×chunk pairs the Postgres path goes through the
# HNSW index (top-K per query) instead of the exact matrix product.
HNSW_PAIR_THRESHOLD = 20_000_000
HNSW_TOP_K = 20

_ISSUE_TYPES = ("passage_gap", "buried_passage", "orphan_chunk")


@dataclass(frozen=True)
class QueryVector:
    """One aggregated GSC query as the coverage core sees it."""

    query: str
    vector: tuple
    clicks: int
    impressions: int
    position: float | None
    ranking_url_id: int | None


@dataclass(frozen=True)
class ChunkVector:
    """One persisted passage (T11)."""

    chunk_id: int
    url_id: int
    position: int
    vector: tuple
    heading_path: str | None = None


def compute_coverage(
    queries: list[QueryVector],
    chunks: list[ChunkVector],
    *,
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    buried_min_position: int = DEFAULT_BURIED_MIN_POSITION,
    orphan_threshold: float = DEFAULT_ORPHAN_THRESHOLD,
    block_size: int = 2048,
) -> dict:
    """Exact query×chunk coverage on injected vectors.

    Both sides are (re-)L2-normalized so cosine = dot product. Returns
    ``{"per_query": [...], "orphan_chunk_ids": [...], "chunk_max_sim":
    {chunk_id: float}}`` where each per_query row carries the best chunk,
    its similarity and the derived ``covered`` / ``buried`` flags.
    """
    import numpy as np

    if not queries or not chunks:
        return {"per_query": [], "orphan_chunk_ids": [], "chunk_max_sim": {}}

    def _unit(matrix):
        m = np.asarray(matrix, dtype=np.float32)
        norms = np.linalg.norm(m, axis=1)
        norms = np.where(norms == 0, 1.0, norms)
        return m / norms[:, None]

    q_mat = _unit([q.vector for q in queries])
    best_sim = np.full(len(queries), -1.0, dtype=np.float32)
    best_idx = np.zeros(len(queries), dtype=np.int64)
    chunk_max = np.full(len(chunks), -1.0, dtype=np.float32)

    for start in range(0, len(chunks), block_size):
        block = chunks[start : start + block_size]
        c_mat = _unit([c.vector for c in block])
        sims = q_mat @ c_mat.T  # (nq, nblock)
        blk_best = sims.max(axis=1)
        improved = blk_best > best_sim
        best_idx[improved] = start + sims.argmax(axis=1)[improved]
        best_sim[improved] = blk_best[improved]
        chunk_max[start : start + len(block)] = sims.max(axis=0)

    per_query = []
    for i, q in enumerate(queries):
        best = chunks[int(best_idx[i])]
        sim = float(best_sim[i])
        covered = sim >= sim_threshold
        per_query.append({
            "query": q.query,
            "clicks": q.clicks,
            "impressions": q.impressions,
            "position": q.position,
            "ranking_url_id": q.ranking_url_id,
            "best_similarity": round(sim, 4),
            "best_chunk_id": best.chunk_id,
            "best_chunk_url_id": best.url_id,
            "best_chunk_position": best.position,
            "best_chunk_heading": best.heading_path,
            "covered": covered,
            "buried": covered and best.position >= buried_min_position,
        })

    orphan_ids = [
        c.chunk_id for i, c in enumerate(chunks)
        if float(chunk_max[i]) < orphan_threshold
    ]
    return {
        "per_query": per_query,
        "orphan_chunk_ids": orphan_ids,
        "chunk_max_sim": {
            c.chunk_id: round(float(chunk_max[i]), 4)
            for i, c in enumerate(chunks)
        },
    }


def _coverage_via_hnsw(
    session,
    analysis_id,
    queries: list[QueryVector],
    q_vectors,
    chunk_by_id: dict[int, ChunkVector],
    *,
    sim_threshold: float,
    buried_min_position: int,
    orphan_threshold: float,
) -> dict:
    """Postgres scale path: per-query top-K through the HNSW index.

    Orphan detection only sees retrieved pairs (approximate — a chunk
    outside every query's top-K counts as orphan even if some query
    matches it weakly). The caller marks the result accordingly.
    """
    from shared.semantic_models import SemanticChunk

    per_query = []
    seen_max: dict[int, float] = {}
    for q, vec in zip(queries, q_vectors):
        rows = (
            session.query(
                SemanticChunk.id,
                SemanticChunk.embedding.cosine_distance(list(vec)).label("dist"),
            )
            .filter(
                SemanticChunk.analysis_id == analysis_id,
                SemanticChunk.embedding.isnot(None),
            )
            .order_by("dist")
            .limit(HNSW_TOP_K)
            .all()
        )
        if not rows:
            continue
        for cid, dist in rows:
            sim = 1.0 - float(dist)
            if sim > seen_max.get(cid, -1.0):
                seen_max[cid] = sim
        best_id, best_dist = rows[0]
        best = chunk_by_id[best_id]
        sim = 1.0 - float(best_dist)
        covered = sim >= sim_threshold
        per_query.append({
            "query": q.query,
            "clicks": q.clicks,
            "impressions": q.impressions,
            "position": q.position,
            "ranking_url_id": q.ranking_url_id,
            "best_similarity": round(sim, 4),
            "best_chunk_id": best.chunk_id,
            "best_chunk_url_id": best.url_id,
            "best_chunk_position": best.position,
            "best_chunk_heading": best.heading_path,
            "covered": covered,
            "buried": covered and best.position >= buried_min_position,
        })

    orphan_ids = [
        cid for cid in chunk_by_id
        if seen_max.get(cid, -1.0) < orphan_threshold
    ]
    return {
        "per_query": per_query,
        "orphan_chunk_ids": orphan_ids,
        "chunk_max_sim": {k: round(v, 4) for k, v in seen_max.items()},
        "orphan_approximate": True,
    }


def run_query_coverage(
    session,
    job_id,
    analysis_id,
    backend,
    *,
    max_queries: int = DEFAULT_MAX_QUERIES,
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS,
    sim_threshold: float = DEFAULT_SIM_THRESHOLD,
    buried_min_position: int = DEFAULT_BURIED_MIN_POSITION,
    orphan_threshold: float = DEFAULT_ORPHAN_THRESHOLD,
) -> dict:
    """Aggregate GSC queries, embed them, persist ``query_embeddings``,
    emit the three signable issue types and return the coverage report.

    Replaces the job's previous query embeddings and its PENDING T19
    issues (signed/rejected decisions survive — T10 hard rule).
    """
    from shared.models import Issue, Url
    from shared.semantic_models import GscQueryData, QueryEmbedding, SemanticChunk

    # -- 1. aggregate demand per query ---------------------------------
    q_rows = (
        session.query(GscQueryData)
        .filter(GscQueryData.job_id == job_id)
        .all()
    )
    if not q_rows:
        return {"status": "blocked", "reason": "no_gsc_query_data"}

    agg: dict[str, dict] = {}
    for r in q_rows:
        a = agg.setdefault(r.query, {
            "clicks": 0, "impressions": 0, "pos_weight": 0.0,
            "by_url": {},
        })
        a["clicks"] += r.clicks or 0
        a["impressions"] += r.impressions or 0
        if r.position is not None:
            a["pos_weight"] += (r.position or 0.0) * (r.impressions or 0)
        u = a["by_url"].setdefault(r.url_id, {"clicks": 0, "impressions": 0})
        u["clicks"] += r.clicks or 0
        u["impressions"] += r.impressions or 0

    candidates = [
        (q, a) for q, a in agg.items() if a["impressions"] >= min_impressions
    ]
    candidates.sort(key=lambda x: -x[1]["impressions"])
    candidates = candidates[:max_queries]
    if not candidates:
        return {"status": "blocked", "reason": "no_queries_over_threshold"}

    queries = []
    for q, a in candidates:
        ranking_url_id = max(
            a["by_url"].items(),
            key=lambda kv: (kv[1]["clicks"], kv[1]["impressions"]),
        )[0]
        queries.append(QueryVector(
            query=q,
            vector=(),  # filled after embedding
            clicks=a["clicks"],
            impressions=a["impressions"],
            position=(
                round(a["pos_weight"] / a["impressions"], 2)
                if a["impressions"] else None
            ),
            ranking_url_id=ranking_url_id,
        ))

    # -- 2. load chunks (T11) -------------------------------------------
    chunk_rows = (
        session.query(
            SemanticChunk.id, SemanticChunk.url_id, SemanticChunk.position,
            SemanticChunk.embedding, SemanticChunk.heading_path,
        )
        .filter(
            SemanticChunk.analysis_id == analysis_id,
            SemanticChunk.embedding.isnot(None),
        )
        .all()
    )
    if not chunk_rows:
        return {"status": "blocked", "reason": "no_chunks"}
    chunks = [
        ChunkVector(
            chunk_id=r[0], url_id=r[1], position=r[2],
            vector=tuple(r[3]), heading_path=r[4],
        )
        for r in chunk_rows
    ]

    # -- 3. embed queries (RETRIEVAL_QUERY, batched) --------------------
    q_vectors = backend.embed_queries([q.query for q in queries])
    queries = [
        QueryVector(
            query=q.query, vector=tuple(q_vectors[i]), clicks=q.clicks,
            impressions=q.impressions, position=q.position,
            ranking_url_id=q.ranking_url_id,
        )
        for i, q in enumerate(queries)
    ]

    # -- 4. coverage matrix ---------------------------------------------
    dialect = session.get_bind().dialect.name
    use_hnsw = (
        dialect.startswith("postgres")
        and len(queries) * len(chunks) > HNSW_PAIR_THRESHOLD
    )
    if use_hnsw:
        result = _coverage_via_hnsw(
            session, analysis_id, queries, q_vectors,
            {c.chunk_id: c for c in chunks},
            sim_threshold=sim_threshold,
            buried_min_position=buried_min_position,
            orphan_threshold=orphan_threshold,
        )
    else:
        result = compute_coverage(
            queries, chunks,
            sim_threshold=sim_threshold,
            buried_min_position=buried_min_position,
            orphan_threshold=orphan_threshold,
        )

    # -- 5. persist query embeddings (replace per job) ------------------
    session.query(QueryEmbedding).filter(
        QueryEmbedding.job_id == job_id,
    ).delete()
    row_by_query = {r["query"]: r for r in result["per_query"]}
    for q in queries:
        r = row_by_query.get(q.query)
        session.add(QueryEmbedding(
            job_id=job_id,
            analysis_id=analysis_id,
            query=q.query,
            embedding=list(q.vector),
            clicks=q.clicks,
            impressions=q.impressions,
            position=q.position,
            ranking_url_id=q.ranking_url_id,
            best_similarity=r["best_similarity"] if r else None,
            best_chunk_id=r["best_chunk_id"] if r else None,
            covered=r["covered"] if r else None,
            buried=r["buried"] if r else None,
        ))

    # -- 6. signable issues (T10: replace pending, keep decisions) ------
    session.query(Issue).filter(
        Issue.job_id == job_id,
        Issue.issue_type.in_(_ISSUE_TYPES),
        Issue.review_status == "pending",
    ).delete(synchronize_session=False)

    url_ids = (
        {r["ranking_url_id"] for r in result["per_query"] if r["ranking_url_id"]}
        | {r["best_chunk_url_id"] for r in result["per_query"]}
        | {c.url_id for c in chunks}
    )
    url_by_id = dict(
        session.query(Url.id, Url.url).filter(Url.id.in_(url_ids)).all()
    )

    n_gap = n_buried = 0
    for r in result["per_query"]:
        if not r["covered"] and r["ranking_url_id"]:
            session.add(Issue(
                job_id=job_id,
                url_id=r["ranking_url_id"],
                issue_type="passage_gap",
                severity="warning",
                review_status="pending",
                details={
                    "query": r["query"],
                    "impressions": r["impressions"],
                    "clicks": r["clicks"],
                    "best_similarity": r["best_similarity"],
                    "best_passage_url": url_by_id.get(r["best_chunk_url_id"]),
                    "sim_threshold": sim_threshold,
                },
            ))
            n_gap += 1
        elif r["buried"]:
            session.add(Issue(
                job_id=job_id,
                url_id=r["best_chunk_url_id"],
                issue_type="buried_passage",
                severity="warning",
                review_status="pending",
                details={
                    "query": r["query"],
                    "impressions": r["impressions"],
                    "similarity": r["best_similarity"],
                    "chunk_position": r["best_chunk_position"],
                    "heading_path": r["best_chunk_heading"],
                },
            ))
            n_buried += 1

    orphan_set = set(result["orphan_chunk_ids"])
    orphans_by_url: dict[int, list[int]] = {}
    chunks_by_url: dict[int, int] = {}
    for c in chunks:
        chunks_by_url[c.url_id] = chunks_by_url.get(c.url_id, 0) + 1
        if c.chunk_id in orphan_set:
            orphans_by_url.setdefault(c.url_id, []).append(c.position)
    n_orphan_issues = 0
    for url_id, positions in orphans_by_url.items():
        session.add(Issue(
            job_id=job_id,
            url_id=url_id,
            issue_type="orphan_chunk",
            severity="info",
            review_status="pending",
            details={
                "orphan_chunks": len(positions),
                "total_chunks": chunks_by_url[url_id],
                "positions": sorted(positions)[:10],
                "orphan_threshold": orphan_threshold,
                "approximate": bool(result.get("orphan_approximate")),
            },
        ))
        n_orphan_issues += 1
    session.flush()

    covered = sum(1 for r in result["per_query"] if r["covered"])
    summary = {
        "queries_total": len(agg),
        "queries_analyzed": len(result["per_query"]),
        "covered": covered,
        "coverage_ratio": (
            round(covered / len(result["per_query"]), 4)
            if result["per_query"] else 0.0
        ),
        "gaps": n_gap,
        "buried": n_buried,
        "chunks_total": len(chunks),
        "orphan_chunks": len(orphan_set),
        "orphan_approximate": bool(result.get("orphan_approximate")),
        "params": {
            "max_queries": max_queries,
            "min_impressions": min_impressions,
            "sim_threshold": sim_threshold,
            "buried_min_position": buried_min_position,
            "orphan_threshold": orphan_threshold,
        },
    }
    logger.info(
        "T19 coverage job %s: %d queries, %d gaps, %d buried, %d orphan-chunk "
        "issues (hnsw=%s)", job_id, len(result["per_query"]), n_gap, n_buried,
        n_orphan_issues, use_hnsw,
    )
    return {
        "status": "ok",
        "summary": summary,
        "queries": [
            {
                **{k: v for k, v in r.items() if k != "best_chunk_url_id"},
                "ranking_url": url_by_id.get(r["ranking_url_id"]),
                "best_chunk_url": url_by_id.get(r["best_chunk_url_id"]),
            }
            for r in result["per_query"]
        ],
        "issues_written": {
            "passage_gap": n_gap,
            "buried_passage": n_buried,
            "orphan_chunk": n_orphan_issues,
        },
    }
