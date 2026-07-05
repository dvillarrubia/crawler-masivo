"""Paso 03: gate de resolución de menciones a catálogo (tres zonas).

- Embeddings: **API directa de Gemini** (`gemini-embedding-001@768`, MRL,
  L2 explícita — a 768d Gemini NO normaliza, invariante del contrato).
  Task type `SEMANTIC_SIMILARITY` en AMBOS lados (comparación simétrica
  nombre-de-entidad ↔ entrada-de-catálogo), nunca mezclado con los
  espacios 1024d del resto del repo.
- Zona alta (≥ high): entity_id asignado, resolved_by='cosine'.
- Zona baja (< low): queda sin resolver (señal).
- Zona gris: juez LLM (Gemini Flash vía google-genai, inyectable) con la
  mención en contexto + top-3 candidatos; responde id o ninguno.
- Umbrales del schema.yaml del cliente (PROVISIONALES hasta calibrar por
  barrido contra el gold set; el 0,92 histórico no se hereda).
"""
from __future__ import annotations

import logging

from analysis.entities.extraction import slugify
from analysis.entities.schema_config import ExtractionSchema

logger = logging.getLogger(__name__)

GRAY_TOP_K = 3


# ---------------------------------------------------------------------------
# Embedder real (inyectable; los tests usan un fake con la misma firma)
# ---------------------------------------------------------------------------
class GeminiEntityEmbedder:
    """768d + L2, task SEMANTIC_SIMILARITY. API directa (no OpenRouter:
    no sirve embeddings, como señala el brief)."""

    MODEL = "gemini-embedding-001"
    DIM = 768

    def __init__(self, api_key: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)

    def embed(self, texts: list[str]):
        import numpy as np
        from google.genai import types

        if not texts:
            return np.zeros((0, self.DIM), dtype="float32")
        config = types.EmbedContentConfig(
            task_type="SEMANTIC_SIMILARITY", output_dimensionality=self.DIM,
        )
        out: list[list[float]] = []
        for start in range(0, len(texts), 100):
            resp = self._client.models.embed_content(
                model=self.MODEL, contents=texts[start:start + 100], config=config,
            )
            out.extend(list(e.values) for e in resp.embeddings)
        m = np.asarray(out, dtype="float32")
        norms = np.linalg.norm(m, axis=1)
        norms[norms == 0] = 1.0
        return m / norms[:, None]  # L2 obligatoria a 768d (contrato §1.1)


class GeminiFlashJudge:
    """Juez de zona gris: recibe mención+contexto+candidatos, responde
    entity_id o None. Solo juicio lingüístico (invariante del brief)."""

    MODEL = "gemini-flash-latest"

    def __init__(self, api_key: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)

    def judge(self, entity_text: str, context: str,
              candidates: list[tuple[str, str]]) -> str | None:
        listado = "\n".join(f"- {eid}: {name}" for eid, name in candidates)
        prompt = (
            "Eres un anotador de entidades. Mención extraída: "
            f"«{entity_text}»\nContexto: «{context[:400]}»\n"
            f"Candidatos del catálogo:\n{listado}\n\n"
            "Responde SOLO con el id del candidato que es la misma entidad, "
            "o la palabra ninguno."
        )
        resp = self._client.models.generate_content(model=self.MODEL, contents=prompt)
        answer = (resp.text or "").strip().split()[0] if resp.text else ""
        valid = {eid for eid, _ in candidates}
        return answer if answer in valid else None


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
def seed_catalog_from_crawl(session, client_id: str, job_id,
                            schema: ExtractionSchema, *,
                            min_weight: float = 6.0,
                            max_entries: int = 500) -> int:
    """Catálogo `generado`: siembra desde las menciones resolubles más
    pesadas del propio crawl (title/H1 pesan x3). El naming humano/LLM y
    la validación se hacen sobre esta semilla; entity_id = local:{slug}
    (convención del contrato, is_linked=false)."""
    from sqlalchemy import func

    from shared.entity_models import EntityCatalog, GlinerPageEntity

    rows = (
        session.query(
            GlinerPageEntity.entity_text,
            GlinerPageEntity.entity_type,
            func.sum(GlinerPageEntity.frequency).label("freq"),
        )
        .filter(GlinerPageEntity.job_id == job_id,
                GlinerPageEntity.kind == "resoluble")
        .group_by(GlinerPageEntity.entity_text, GlinerPageEntity.entity_type)
        .having(func.sum(GlinerPageEntity.frequency) >= 2)
        .order_by(func.sum(GlinerPageEntity.frequency).desc())
        .limit(max_entries)
        .all()
    )
    n = 0
    for text, etype, _freq in rows:
        eid = f"local:{slugify(text)}"
        if session.get(EntityCatalog, (client_id, eid)) is None:
            session.add(EntityCatalog(
                client_id=client_id, entity_id=eid, name=text,
                entity_type=etype, source="generado", is_linked=False,
            ))
            n += 1
    session.flush()
    return n


def embed_catalog(session, client_id: str, embedder) -> int:
    """Embebe (una vez) las entradas del catálogo sin vector."""
    from shared.entity_models import EntityCatalog

    pending = (
        session.query(EntityCatalog)
        .filter(EntityCatalog.client_id == client_id,
                EntityCatalog.embedding.is_(None))
        .all()
    )
    if not pending:
        return 0
    vectors = embedder.embed([p.name for p in pending])
    for row, vec in zip(pending, vectors):
        row.embedding = [float(x) for x in vec]
    session.flush()
    return len(pending)


# ---------------------------------------------------------------------------
# Gate de tres zonas
# ---------------------------------------------------------------------------
def resolve_job(session, job_id, client_id: str, schema: ExtractionSchema,
                embedder, judge=None) -> dict:
    """Resuelve las menciones resolubles del job (páginas y queries)."""
    import numpy as np

    from shared.entity_models import (
        EntityCatalog, GlinerPageEntity, GlinerQueryEntity,
    )

    catalog = (
        session.query(EntityCatalog)
        .filter(EntityCatalog.client_id == client_id,
                EntityCatalog.embedding.isnot(None))
        .all()
    )
    if not catalog:
        return {"status": "blocked", "reason": "empty_catalog"}
    cat_mat = np.asarray([list(c.embedding) for c in catalog], dtype="float32")
    norms = np.linalg.norm(cat_mat, axis=1)
    norms[norms == 0] = 1.0
    cat_mat = cat_mat / norms[:, None]

    # menciones únicas sin resolver (páginas + queries comparten gate)
    page_rows = (
        session.query(GlinerPageEntity)
        .filter(GlinerPageEntity.job_id == job_id,
                GlinerPageEntity.kind == "resoluble",
                GlinerPageEntity.entity_id.is_(None))
        .all()
    )
    query_rows = (
        session.query(GlinerQueryEntity)
        .filter(GlinerQueryEntity.job_id == job_id,
                GlinerQueryEntity.kind == "resoluble",
                GlinerQueryEntity.entity_id.is_(None))
        .all()
    )
    texts = sorted({r.entity_text for r in page_rows} |
                   {r.entity_text for r in query_rows})
    if not texts:
        return {"status": "ok", "resolved_cosine": 0, "resolved_llm": 0,
                "unresolved": 0, "gray_judged": 0}

    vectors = embedder.embed(texts)
    sims = vectors @ cat_mat.T  # ambos L2 → coseno

    decision: dict[str, tuple[str | None, str | None, float]] = {}
    n_cos = n_llm = n_gray = 0
    for i, text in enumerate(texts):
        row_sims = sims[i]
        best = int(row_sims.argmax())
        score = float(row_sims[best])
        if score >= schema.high_threshold:
            decision[text] = (catalog[best].entity_id, "cosine", round(score, 4))
            n_cos += 1
        elif score < schema.low_threshold:
            decision[text] = (None, None, round(score, 4))
        else:
            n_gray += 1
            eid = None
            if judge is not None:
                top = np.argsort(-row_sims)[:GRAY_TOP_K]
                candidates = [(catalog[j].entity_id, catalog[j].name) for j in top]
                eid = judge.judge(text, text, candidates)
            if eid:
                decision[text] = (eid, "llm", round(score, 4))
                n_llm += 1
            else:
                decision[text] = (None, None, round(score, 4))

    for rows in (page_rows, query_rows):
        for r in rows:
            eid, by, score = decision.get(r.entity_text, (None, None, None))
            r.entity_id = eid
            r.resolved_by = by
            r.resolution_score = score
    session.flush()

    unresolved = sum(1 for v in decision.values() if v[0] is None)
    logger.info("Resolución job %s: %d únicas → %d coseno, %d llm, %d sin "
                "resolver (gris evaluada: %d)", job_id, len(texts), n_cos,
                n_llm, unresolved, n_gray)
    return {"status": "ok", "resolved_cosine": n_cos, "resolved_llm": n_llm,
            "unresolved": unresolved, "gray_judged": n_gray}
