"""Paso 00: gold set para anotación manual + evaluación F1.

Muestrea 50 URLs (estratificado por segmento si existe) + 200 queries
(top impresiones) y genera un CSV de anotación. Tras anotar, `evaluate`
calcula F1 por tipo de entidad comparando contra lo extraído.

Gate go/no-go del castellano (brief): si F1 < 0,75 en los tipos
resolubles, PARAR y decidir (adapter de fine-tuning vs descarte).
"""
from __future__ import annotations

import csv
import logging
import random
from pathlib import Path

F1_GATE = 0.75
N_URLS = 50
N_QUERIES = 200

logger = logging.getLogger(__name__)


def sample_for_annotation(session, job_id, output_path: str, *,
                          seed: int = 42) -> dict:
    """CSV con dos secciones (unit=url|query). Columnas de anotación:
    entidad_1..entidad_5 con formato `tipo: texto` (a mano)."""
    from sqlalchemy import func

    from shared.models import PageContent, Url, UrlSegment
    from shared.semantic_models import GscQueryData

    rng = random.Random(seed)

    urls = (
        session.query(Url.id, Url.url, UrlSegment.segment_id)
        .join(PageContent, PageContent.url_id == Url.id)
        .outerjoin(UrlSegment, UrlSegment.url_id == Url.id)
        .filter(Url.job_id == job_id, Url.is_internal.is_(True),
                Url.is_html.is_(True), Url.status_code == 200)
        .all()
    )
    by_segment: dict = {}
    for uid, url, seg in urls:
        by_segment.setdefault(seg, []).append((uid, url))
    sampled: list[tuple[int, str]] = []
    if by_segment:
        per_seg = max(1, N_URLS // len(by_segment))
        for seg, items in by_segment.items():
            rng.shuffle(items)
            sampled.extend(items[:per_seg])
        rest = [x for items in by_segment.values() for x in items if x not in sampled]
        rng.shuffle(rest)
        sampled.extend(rest[: N_URLS - len(sampled)])
    sampled = sampled[:N_URLS]

    queries = (
        session.query(GscQueryData.query,
                      func.sum(GscQueryData.impressions).label("imprs"))
        .filter(GscQueryData.job_id == job_id)
        .group_by(GscQueryData.query)
        .order_by(func.sum(GscQueryData.impressions).desc())
        .limit(N_QUERIES)
        .all()
    )

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["unit", "id", "texto",
                    "entidad_1", "entidad_2", "entidad_3", "entidad_4", "entidad_5",
                    "funnel (TOFU/MOFU/BOFU)", "tipo_pagina"])
        for uid, url in sampled:
            w.writerow(["url", uid, url, "", "", "", "", "", "", ""])
        for q, _ in queries:
            w.writerow(["query", "", q, "", "", "", "", "", "", ""])
    logger.info("Gold set: %d URLs + %d queries → %s",
                len(sampled), len(queries), path)
    return {"urls": len(sampled), "queries": len(queries), "path": str(path)}


def evaluate(session, job_id, annotated_csv: str) -> dict:
    """F1 por tipo de entidad (match por texto normalizado). Devuelve el
    veredicto del gate para los tipos resolubles."""
    from analysis.entities.extraction import normalize_entity_text
    from shared.entity_models import GlinerPageEntity, GlinerQueryEntity

    gold: dict[str, set[tuple[str, str]]] = {"url": set(), "query": set()}
    keys: dict[str, list] = {"url": [], "query": []}
    with open(annotated_csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            unit = row["unit"].strip()
            key = row["id"].strip() if unit == "url" else row["texto"].strip()
            keys[unit].append(key)
            for col in ("entidad_1", "entidad_2", "entidad_3", "entidad_4", "entidad_5"):
                val = (row.get(col) or "").strip()
                if not val or ":" not in val:
                    continue
                etype, text = val.split(":", 1)
                gold[unit].add((key, f"{etype.strip()}|{normalize_entity_text(text)}"))

    pred: dict[str, set[tuple[str, str]]] = {"url": set(), "query": set()}
    url_ids = [int(k) for k in keys["url"] if k]
    if url_ids:
        for m in session.query(GlinerPageEntity).filter(
                GlinerPageEntity.job_id == job_id,
                GlinerPageEntity.url_id.in_(url_ids)):
            pred["url"].add((str(m.url_id), f"{m.entity_type}|{m.entity_text}"))
    if keys["query"]:
        for m in session.query(GlinerQueryEntity).filter(
                GlinerQueryEntity.job_id == job_id,
                GlinerQueryEntity.query.in_(keys["query"])):
            pred["query"].add((m.query, f"{m.entity_type}|{m.entity_text}"))

    by_type: dict[str, dict[str, int]] = {}
    for unit in ("url", "query"):
        for key, tagged in gold[unit]:
            etype = tagged.split("|", 1)[0]
            d = by_type.setdefault(etype, {"tp": 0, "fp": 0, "fn": 0})
            if (key, tagged) in pred[unit]:
                d["tp"] += 1
            else:
                d["fn"] += 1
        for key, tagged in pred[unit]:
            etype = tagged.split("|", 1)[0]
            d = by_type.setdefault(etype, {"tp": 0, "fp": 0, "fn": 0})
            if (key, tagged) not in gold[unit]:
                d["fp"] += 1

    out = {}
    for etype, d in by_type.items():
        p = d["tp"] / (d["tp"] + d["fp"]) if d["tp"] + d["fp"] else 0.0
        r = d["tp"] / (d["tp"] + d["fn"]) if d["tp"] + d["fn"] else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        out[etype] = {"precision": round(p, 4), "recall": round(r, 4),
                      "f1": round(f1, 4), **d}
    return out


def gate_verdict(f1_by_type: dict, resoluble_types: list[str]) -> dict:
    """Go/no-go: F1 medio de los tipos resolubles contra el umbral 0,75."""
    scores = [f1_by_type[t]["f1"] for t in resoluble_types if t in f1_by_type]
    mean_f1 = round(sum(scores) / len(scores), 4) if scores else 0.0
    return {
        "mean_f1_resolubles": mean_f1,
        "gate": F1_GATE,
        "go": mean_f1 >= F1_GATE,
        "veredicto": ("GO — el modelo base rinde en castellano" if mean_f1 >= F1_GATE
                      else "NO-GO — parar y decidir: adapter de fine-tuning vs descarte"),
    }
