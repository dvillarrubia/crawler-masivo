"""Paso 04: joins deterministas y doble salida (issues consola + Excel/JSON).

Cuatro checks (fase 0 §4, códigos aprobados):

- entity_query_mismatch  determinista (review_status NULL), warning
- entity_coverage_gap    determinista, warning (anclado a la home)
- entity_cannibalization FIRMABLE (pending), warning
- funnel_mismatch        FIRMABLE (pending), info

Prioridad determinista: impresiones × posición media × confianza.
Vocabulario cerrado de acción: onpage, crear_contenido, consolidar,
diferenciar, desoptimizar, enlazar.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from analysis.entities.extraction import FIELD_WEIGHT

logger = logging.getLogger(__name__)

DETERMINISTIC_TYPES = ("entity_query_mismatch", "entity_coverage_gap")
SIGNABLE_TYPES = ("entity_cannibalization", "funnel_mismatch")


# Cuando no existe medición (p. ej. una entidad sin posición conocida),
# el factor es NEUTRO (1.0): la prioridad degrada a impresiones×confianza.
# Nunca se inventa una "posición" — cazado en la auditoría anti-datos-falsos.
_FACTOR_NEUTRO = 1.0


def _prioridad(impressions: int, position: float | None, confidence: float | None) -> float:
    return round(
        (impressions or 0)
        * (position if position is not None else _FACTOR_NEUTRO)
        * (confidence if confidence is not None else _FACTOR_NEUTRO),
        2,
    )


def build_report(session, job_id, client_id: str) -> dict:
    """Calcula los cuatro cruces en memoria. Puro respecto a escritura:
    no toca `issues` (eso lo hace :func:`write_outputs`)."""
    from shared.entity_models import (
        EntityCatalog, GlinerPageEntity, GlinerPageLabel,
        GlinerQueryEntity, GlinerQueryLabel,
    )
    from shared.models import Issue, Url
    from shared.semantic_models import GscQueryData

    # -- nombres del catálogo -------------------------------------------
    cat_name = {
        c.entity_id: c.name
        for c in session.query(EntityCatalog).filter(EntityCatalog.client_id == client_id)
    }

    # -- por URL: entidades presentes + primaria + labels ------------------
    urls = {
        u.id: u for u in session.query(Url).filter(
            Url.job_id == job_id, Url.is_internal.is_(True), Url.is_html.is_(True))
    }
    present: dict[int, set[str]] = defaultdict(set)
    weight_by_url: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    confidence_by_url: dict[int, dict[str, float]] = defaultdict(dict)
    for m in session.query(GlinerPageEntity).filter(
            GlinerPageEntity.job_id == job_id,
            GlinerPageEntity.entity_id.isnot(None)):
        present[m.url_id].add(m.entity_id)
        weight_by_url[m.url_id][m.entity_id] += (
            (m.frequency or 1) * FIELD_WEIGHT.get(m.source_field, 1.0))
        prev = confidence_by_url[m.url_id].get(m.entity_id, 0.0)
        confidence_by_url[m.url_id][m.entity_id] = max(prev, m.confidence or 0.0)
    primary: dict[int, str] = {}
    for url_id, weights in weight_by_url.items():
        primary[url_id] = max(weights, key=weights.get)

    labels: dict[tuple[int, str], tuple[str, float]] = {}
    for l in session.query(GlinerPageLabel).filter(GlinerPageLabel.job_id == job_id):
        labels[(l.url_id, l.label_type)] = (l.label, l.confidence or 0.0)

    # -- por query: entidad + funnel + demanda + URL que rankea -----------
    q_entity: dict[str, tuple[str, float]] = {}
    for qe in session.query(GlinerQueryEntity).filter(
            GlinerQueryEntity.job_id == job_id,
            GlinerQueryEntity.entity_id.isnot(None)):
        cur = q_entity.get(qe.query)
        if cur is None or (qe.confidence or 0) > cur[1]:
            q_entity[qe.query] = (qe.entity_id, qe.confidence or 0.0)
    q_funnel: dict[str, str] = {
        l.query: l.label
        for l in session.query(GlinerQueryLabel).filter(
            GlinerQueryLabel.job_id == job_id,
            GlinerQueryLabel.label_type == "funnel")
    }
    demand: dict[str, dict] = {}
    for row in session.query(GscQueryData).filter(GscQueryData.job_id == job_id):
        d = demand.setdefault(row.query, {
            "impressions": 0, "clicks": 0, "pos_w": 0.0, "by_url": {},
        })
        d["impressions"] += row.impressions or 0
        d["clicks"] += row.clicks or 0
        if row.position is not None:
            d["pos_w"] += row.position * (row.impressions or 0)
        if row.url_id is not None:
            u = d["by_url"].setdefault(row.url_id, {"clicks": 0, "impressions": 0})
            u["clicks"] += row.clicks or 0
            u["impressions"] += row.impressions or 0

    def _ranking_url(q: str) -> int | None:
        by_url = demand.get(q, {}).get("by_url") or {}
        if not by_url:
            return None
        return max(by_url.items(), key=lambda kv: (kv[1]["clicks"], kv[1]["impressions"]))[0]

    def _avg_pos(q: str) -> float | None:
        d = demand.get(q)
        if not d or not d["impressions"]:
            return None
        return round(d["pos_w"] / d["impressions"], 2)

    # entidades cubiertas en alguna página (el gap tiene precedencia sobre
    # el mismatch: si NADIE cubre la entidad, lo accionable es crear
    # contenido, no retocar el on-page de la página que rankea de rebote)
    covered_anywhere: set[str] = set()
    for eids in present.values():
        covered_anywhere |= eids

    # -- 1. mismatch entidad-query -----------------------------------------
    mismatches = []
    for q, (eid, conf) in q_entity.items():
        r_url = _ranking_url(q)
        if r_url is None or r_url not in urls:
            continue
        if eid in present.get(r_url, set()):
            continue
        if eid not in covered_anywhere:
            continue  # gap global: lo recoge entity_coverage_gap
        d = demand.get(q, {})
        mismatches.append({
            "url_id": r_url, "url": urls[r_url].url, "query": q,
            "entity_id": eid, "entity": cat_name.get(eid, eid),
            "impressions": d.get("impressions", 0), "clicks": d.get("clicks", 0),
            "position": _avg_pos(q), "confidence": round(conf, 4),
            "entidades_presentes": sorted(
                cat_name.get(e, e) for e in list(present.get(r_url, set()))[:5]),
            "accion": "onpage",
            "prioridad": _prioridad(d.get("impressions", 0), _avg_pos(q), conf),
        })

    # -- 2. gaps de cobertura ----------------------------------------------
    # posición y confianza REALES por entidad (media ponderada de sus
    # queries / máxima confianza de extracción) — nada de constantes.
    demand_by_entity: dict[str, dict] = defaultdict(
        lambda: {"impressions": 0, "clicks": 0, "queries": [],
                 "pos_w": 0.0, "conf": 0.0})
    for q, (eid, conf) in q_entity.items():
        d = demand.get(q, {})
        e = demand_by_entity[eid]
        e["impressions"] += d.get("impressions", 0)
        e["clicks"] += d.get("clicks", 0)
        e["queries"].append(q)
        e["pos_w"] += d.get("pos_w", 0.0)
        e["conf"] = max(e["conf"], conf or 0.0)

    def _entity_pos(eid: str) -> float | None:
        e = demand_by_entity.get(eid)
        if not e or not e["impressions"] or not e["pos_w"]:
            return None
        return round(e["pos_w"] / e["impressions"], 2)
    gaps = []
    home_id = None
    home_candidates = [u for u in urls.values() if u.click_depth == 0]
    if not home_candidates:
        home_candidates = sorted(
            (u for u in urls.values() if u.crawl_depth is not None),
            key=lambda u: u.crawl_depth)
    if home_candidates:
        home_id = home_candidates[0].id
    for eid, e in demand_by_entity.items():
        if eid in covered_anywhere:
            continue
        gaps.append({
            "url_id": home_id, "entity_id": eid,
            "entity": cat_name.get(eid, eid),
            "impressions": e["impressions"], "clicks": e["clicks"],
            "position": _entity_pos(eid),
            "queries": sorted(e["queries"])[:10],
            "accion": "crear_contenido",
            "prioridad": _prioridad(e["impressions"], _entity_pos(eid), e["conf"]),
        })

    # -- 3. canibalización por entidad primaria + banda funnel --------------
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for url_id, eid in primary.items():
        funnel = labels.get((url_id, "funnel"), (None, 0))[0]
        if funnel:
            groups[(eid, funnel)].append(url_id)
    converge_ids = {
        i.url_id for i in session.query(Issue).filter(
            Issue.job_id == job_id,
            Issue.issue_type == "semantic_cannibalization")
    }
    cannibal = []
    for (eid, funnel), members in groups.items():
        if len(members) < 2:
            continue
        dominant = max(members, key=lambda uid: urls[uid].pagerank or 0.0)
        same_tipo = len({
            labels.get((uid, "tipo_pagina"), (None, 0))[0] for uid in members
        }) == 1
        for uid in members:
            if uid == dominant:
                continue
            cannibal.append({
                "url_id": uid, "url": urls[uid].url,
                "entity_id": eid, "entity": cat_name.get(eid, eid),
                "funnel": funnel, "dominant_url": urls[dominant].url,
                "n_urls": len(members),
                "converge_embeddings": uid in converge_ids,
                "accion": "consolidar" if same_tipo else "diferenciar",
                "prioridad": _prioridad(
                    demand_by_entity.get(eid, {}).get("impressions", 0),
                    _entity_pos(eid),
                    confidence_by_url.get(uid, {}).get(eid)),
            })

    # -- 4. circuito funnel roto --------------------------------------------
    funnel_issues = []
    queries_by_url: dict[int, list[str]] = defaultdict(list)
    for q in q_funnel:
        r = _ranking_url(q)
        if r is not None:
            queries_by_url[r].append(q)
    for url_id, qs in queries_by_url.items():
        page_funnel = labels.get((url_id, "funnel"), (None, 0))[0]
        if page_funnel not in ("TOFU", "BOFU") or url_id not in urls:
            continue
        opposite = "BOFU" if page_funnel == "TOFU" else "TOFU"
        wrong = [q for q in qs if q_funnel.get(q) == opposite]
        if not wrong:
            continue
        imprs = sum(demand.get(q, {}).get("impressions", 0) for q in wrong)
        # posición media REAL de las queries mal encajadas
        pos_w = sum(demand.get(q, {}).get("pos_w", 0.0) for q in wrong)
        avg_pos = round(pos_w / imprs, 2) if imprs and pos_w else None
        page_label_conf = labels.get((url_id, "funnel"), (None, None))[1]
        funnel_issues.append({
            "url_id": url_id, "url": urls[url_id].url,
            "page_funnel": page_funnel, "query_funnel": opposite,
            "queries": sorted(wrong)[:10], "n_queries": len(wrong),
            "impressions": imprs, "position": avg_pos,
            "accion": "crear_contenido" if page_funnel == "BOFU" else "enlazar",
            "prioridad": _prioridad(imprs, avg_pos, page_label_conf),
        })

    return {"mismatches": mismatches, "gaps": gaps, "cannibalization": cannibal,
            "funnel_mismatches": funnel_issues}


def write_outputs(session, job_id, report: dict, *,
                  output_dir: str | None = None) -> dict:
    """Escribe los issues (patrón T10) y, si se pide, Excel + JSON."""
    from shared.models import Issue

    # deterministas: son hechos → se regeneran enteros
    session.query(Issue).filter(
        Issue.job_id == job_id,
        Issue.issue_type.in_(DETERMINISTIC_TYPES),
    ).delete(synchronize_session=False)
    # firmables: solo se reemplazan los pending (decisiones sobreviven)
    session.query(Issue).filter(
        Issue.job_id == job_id,
        Issue.issue_type.in_(SIGNABLE_TYPES),
        Issue.review_status == "pending",
    ).delete(synchronize_session=False)

    issues_json = []

    def _add(url_id, issue_type, severity, review_status, details):
        if url_id is None:
            return
        session.add(Issue(job_id=job_id, url_id=url_id, issue_type=issue_type,
                          severity=severity, review_status=review_status,
                          details=details))
        issues_json.append({
            "job_id": str(job_id), "url_id": url_id, "issue_type": issue_type,
            "severity": severity, "review_status": review_status,
            "details": details,
        })

    for m in report["mismatches"]:
        _add(m["url_id"], "entity_query_mismatch", "warning", None,
             {k: v for k, v in m.items() if k not in ("url_id",)})
    for g in report["gaps"]:
        _add(g["url_id"], "entity_coverage_gap", "warning", None,
             {k: v for k, v in g.items() if k not in ("url_id",)})
    for c in report["cannibalization"]:
        _add(c["url_id"], "entity_cannibalization", "warning", "pending",
             {k: v for k, v in c.items() if k not in ("url_id",)})
    for f in report["funnel_mismatches"]:
        _add(f["url_id"], "funnel_mismatch", "info", "pending",
             {k: v for k, v in f.items() if k not in ("url_id",)})
    session.flush()

    files = {}
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_path = out / f"checks_{job_id}.json"
        json_path.write_text(
            json.dumps(issues_json, ensure_ascii=False, indent=2), encoding="utf-8")
        files["json"] = str(json_path)
        files["excel"] = _write_excel(out / f"informe_{job_id}.xlsx", report)

    counts = {
        "entity_query_mismatch": len(report["mismatches"]),
        "entity_coverage_gap": len(report["gaps"]),
        "entity_cannibalization": len(report["cannibalization"]),
        "funnel_mismatch": len(report["funnel_mismatches"]),
    }
    logger.info("Informe entidades job %s: %s", job_id, counts)
    return {"issues": counts, "files": files}


def _write_excel(path: Path, report: dict) -> str | None:
    """Excel de 3 pestañas (mismatch, gaps, canibalización). Fallback CSV."""
    sheets = {
        "mismatch": (report["mismatches"],
                     ["query", "entity", "url", "impressions", "clicks",
                      "position", "confidence", "accion", "prioridad"]),
        "gaps": (report["gaps"],
                 ["entity", "impressions", "clicks", "queries", "accion", "prioridad"]),
        "canibalizacion": (report["cannibalization"],
                           ["entity", "funnel", "url", "dominant_url", "n_urls",
                            "converge_embeddings", "accion", "prioridad"]),
    }
    try:
        from openpyxl import Workbook

        wb = Workbook()
        wb.remove(wb.active)
        for name, (rows, cols) in sheets.items():
            ws = wb.create_sheet(name)
            ws.append(cols)
            for r in sorted(rows, key=lambda x: -x.get("prioridad", 0)):
                ws.append([
                    ", ".join(r[c]) if isinstance(r.get(c), list) else r.get(c)
                    for c in cols
                ])
        wb.save(path)
        return str(path)
    except ImportError:
        import csv

        for name, (rows, cols) in sheets.items():
            p = path.with_name(f"{path.stem}_{name}.csv")
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                for r in sorted(rows, key=lambda x: -x.get("prioridad", 0)):
                    w.writerow({c: (", ".join(r[c]) if isinstance(r.get(c), list)
                                    else r.get(c)) for c in cols})
        logger.warning("openpyxl no disponible: informe en CSVs junto a %s", path)
        return None
