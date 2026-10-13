"""Result endpoints: urls, issues, links, stats, CSV export for a job."""

from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, exists, func, or_
from sqlalchemy.orm import Session, joinedload, subqueryload

from api.backup import stream_backup_zip

from shared.database import get_session
from shared.models import (
    Job, Url, Issue, Link, HtmlMeta, PageContent,
    Heading, Hreflang, StructuredData, Resource, SecurityHeaders,
)

from api.schemas import (
    TopeDeNota,
    CategoryInsight,
    HeadingResponse,
    HreflangResponse,
    HostCount,
    InsightsResponse,
    IssueResponse,
    IssueTypeCount,
    JobStats,
    LinkResponse,
    PaginatedResponse,
    Recommendation,
    ResourceResponse,
    ResourceTypeCount,
    SecurityHeadersResponse,
    StatusGroupCount,
    StructuredDataResponse,
    UrlDetailResponse,
    UrlResponse,
)

router = APIRouter(prefix="/api/jobs/{job_id}", tags=["results"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_job_or_404(job_id: uuid.UUID, db: Session) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _paginate(
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls
# ---------------------------------------------------------------------------
SORT_COLUMNS = {
    "url": Url.url,
    "status_code": Url.status_code,
    "word_count": Url.word_count,
    "content_word_count": Url.content_word_count,
    "response_time_ms": Url.response_time_ms,
    "content_length": Url.content_length,
    "transfer_size": Url.transfer_size,
    "crawl_depth": Url.crawl_depth,
    "click_depth": Url.click_depth,
    "inlinks_count": Url.inlinks_count,
    "outlinks_count": Url.outlinks_count,
    "external_outlinks_count": Url.external_outlinks_count,
    "unique_inlinks_count": Url.unique_inlinks_count,
    "pagerank": Url.pagerank,
    "pagerank_score": Url.pagerank_score,
    "pagerank_raw": Url.pagerank_raw,
    "near_duplicate_count": Url.near_duplicate_count,
    "url_length": Url.url_length,
    "folder_depth": Url.folder_depth,
    "text_ratio": Url.text_ratio,
    "host": Url.host,
    "last_crawled_at": Url.last_crawled_at,
    "title": HtmlMeta.title,
    "title_len": HtmlMeta.title_len,
    "meta_description": HtmlMeta.meta_description,
    "meta_description_len": HtmlMeta.meta_description_len,
    # Se ofrecian en la interfaz como ordenables y el servidor los ignoraba
    # (docs/INVESTIGACION_EXPLORADOR.md): el clic no hacia nada.
    "closest_similarity": Url.closest_similarity,
    "title_pixel_width": HtmlMeta.title_pixel_width,
    "meta_description_pixel_width": HtmlMeta.meta_description_pixel_width,
}
# Ordenar por una de estas exige unir con html_meta.
_SORT_META = {"title", "title_len", "meta_description", "meta_description_len",
              "title_pixel_width", "meta_description_pixel_width"}

# Numeric range filters mapped to (model_column, requires_html_meta_join)
_RANGE_FILTERS: dict[str, tuple[Any, bool]] = {
    "pagerank": (Url.pagerank, False),
    "pagerank_score": (Url.pagerank_score, False),
    "pagerank_raw": (Url.pagerank_raw, False),
    "near_duplicate_count": (Url.near_duplicate_count, False),
    "closest_similarity": (Url.closest_similarity, False),
    "content_length": (Url.content_length, False),
    "transfer_size": (Url.transfer_size, False),
    "word_count": (Url.word_count, False),
    "content_word_count": (Url.content_word_count, False),
    "response_time_ms": (Url.response_time_ms, False),
    "inlinks_count": (Url.inlinks_count, False),
    "outlinks_count": (Url.outlinks_count, False),
    "external_outlinks_count": (Url.external_outlinks_count, False),
    "unique_inlinks_count": (Url.unique_inlinks_count, False),
    "url_length": (Url.url_length, False),
    "folder_depth": (Url.folder_depth, False),
    "text_ratio": (Url.text_ratio, False),
    "crawl_depth": (Url.crawl_depth, False),
    "click_depth": (Url.click_depth, False),
    "status_code": (Url.status_code, False),
    "redirect_type": (Url.redirect_type, False),
    "title_len": (HtmlMeta.title_len, True),
    "meta_description_len": (HtmlMeta.meta_description_len, True),
    # Google corta por pixeles, no por caracteres (decision 54).
    "title_pixel_width": (HtmlMeta.title_pixel_width, True),
    "meta_description_pixel_width": (HtmlMeta.meta_description_pixel_width, True),
}

# String "contains" filters mapped to (model_column, requires_html_meta_join).
# Each entry exposes a `<key>` query parameter (e.g. ?host_contains=…).
_STRING_CONTAINS_FILTERS: dict[str, tuple[Any, bool]] = {
    "host_contains":                 (Url.host,                False),
    "content_type_contains":         (Url.content_type,        False),
    "status_text_contains":          (Url.status_text,         False),
    "redirect_url_contains":         (Url.redirect_url,        False),
    "indexability_status_contains":  (Url.indexability_status, False),
    "http_version_contains":         (Url.http_version,        False),
    "last_modified_contains":        (Url.last_modified,       False),
    "title_contains":                (HtmlMeta.title,                True),
    "description_contains":          (HtmlMeta.meta_description,     True),
    "canonical_contains":            (HtmlMeta.canonical_href,       True),
    "meta_robots_contains":          (HtmlMeta.meta_robots,          True),
    "og_title_contains":             (HtmlMeta.og_title,             True),
    "og_image_contains":             (HtmlMeta.og_image,             True),
    "og_type_contains":              (HtmlMeta.og_type,              True),
    "og_url_contains":               (HtmlMeta.og_url,               True),
    "twitter_card_contains":         (HtmlMeta.twitter_card,         True),
    # La URL tiene su propio filtro: compartia `search` con el buscador de
    # arriba y uno pisaba al otro sin avisar.
    "url_contains":                  (Url.url,                       False),
    "x_robots_tag_contains":         (HtmlMeta.x_robots_tag,         True),
    "canonical_header_contains":     (HtmlMeta.canonical_header,     True),
    "meta_refresh_contains":         (HtmlMeta.meta_refresh,         True),
}

# Campos que se pueden pedir vacios o con valor: `?vacio=title,canonical_href`
# o `?con_valor=...`. «Sin title» o «sin canonical» no se podian pedir con un
# «contiene».
_VACIO_FILTERS: dict[str, tuple[Any, bool]] = {
    "title":            (HtmlMeta.title,            True),
    "meta_description": (HtmlMeta.meta_description, True),
    "canonical_href":   (HtmlMeta.canonical_href,   True),
    "meta_robots":      (HtmlMeta.meta_robots,      True),
    "x_robots_tag":     (HtmlMeta.x_robots_tag,     True),
    "og_title":         (HtmlMeta.og_title,         True),
    "og_image":         (HtmlMeta.og_image,         True),
    "redirect_url":     (Url.redirect_url,          False),
    "last_modified":    (Url.last_modified,         False),
}


_TIPOS_NO_ENLACE = ("redirect", "canonical")


def _origenes(db: Session, job_id: uuid.UUID, hashes: list[str]) -> dict[str, tuple[str, int]]:
    """Para cada destino (url_hash): la pagina que mas veces lo enlaza y
    cuantas paginas distintas lo enlazan. Usa `ix_links_to_hash (job_id,
    to_url_hash)`: nunca recorre la tabla de enlaces entera (decision 37)."""
    if not hashes:
        return {}
    filas = (
        db.query(Link.to_url_hash, Url.url, func.count(Link.id))
        .join(Url, Url.id == Link.from_url_id)
        .filter(Link.job_id == job_id, Link.to_url_hash.in_(hashes),
                or_(Link.link_type.is_(None), Link.link_type.notin_(_TIPOS_NO_ENLACE)))
        .group_by(Link.to_url_hash, Url.url)
        .all()
    )
    mejor: dict[str, tuple[str, int]] = {}
    distintos: dict[str, int] = {}
    for h, origen, n in filas:
        distintos[h] = distintos.get(h, 0) + 1
        if h not in mejor or n > mejor[h][1] or (n == mejor[h][1] and origen < mejor[h][0]):
            mejor[h] = (origen, n)
    return {h: (mejor[h][0], distintos[h]) for h in mejor}


def _h1s(db: Session, ids: list[int]) -> dict[int, tuple[str | None, int]]:
    """Primer H1 visible y cuantos H1 visibles tiene cada URL."""
    if not ids:
        return {}
    salida: dict[int, tuple[str | None, int]] = {}
    for uid, texto in (
        db.query(Heading.url_id, Heading.text)
        .filter(Heading.url_id.in_(ids), Heading.tag == "h1", Heading.oculto.isnot(True))
        .order_by(Heading.url_id, Heading.position)
    ):
        primero, n = salida.get(uid, (None, 0))
        salida[uid] = (primero if primero is not None else texto, n + 1)
    return salida


def _referencia(db: Session, job_id: uuid.UUID, con: str | None) -> uuid.UUID | None:
    """El censo contra el que se compara: `?con=` o el de la comparacion
    automatica que guardo el worker (decision 71)."""
    if con:
        try:
            return uuid.UUID(con)
        except ValueError:
            return None
    job = db.get(Job, job_id)
    comp = job.comparacion if job is not None and isinstance(job.comparacion, dict) else {}
    try:
        return uuid.UUID(comp["comparado_con"]) if comp.get("comparado_con") else None
    except (ValueError, TypeError):
        return None


def _cambios(db: Session, ref: uuid.UUID | None, urls: list) -> dict[int, str | None]:
    """Que cambio cada URL frente al mismo `url_hash` en el censo `ref`.

    Mismos campos y criterio que la comparacion de censos (decision 67):
    codigo, indexabilidad, titulo, canonical y palabras si cambian mas de un
    20% sobre el MAYOR de los dos (si no, A->B y B->A dirian cosas distintas).
    """
    if ref is None or not urls:
        return {}
    antes = {
        u.url_hash: u for u in db.query(Url).options(joinedload(Url.html_meta))
        .filter(Url.job_id == ref, Url.url_hash.in_([u.url_hash for u in urls]))
    }
    salida: dict[int, str | None] = {}
    for u in urls:
        a = antes.get(u.url_hash)
        if a is None:
            salida[u.id] = "nueva"
            continue
        partes = []
        if a.status_code != u.status_code:
            partes.append(f"{a.status_code or '—'}→{u.status_code or '—'}")
        if a.indexable != u.indexable and None not in (a.indexable, u.indexable):
            partes.append("entra en el índice" if u.indexable else "sale del índice")
        ma, mu = a.html_meta, u.html_meta
        if ma is not None and mu is not None:
            if (ma.title or "").strip() != (mu.title or "").strip():
                partes.append("título")
            if (ma.canonical_href or "") != (mu.canonical_href or ""):
                partes.append("canonical")
        pa, pu = a.content_word_count or a.word_count, u.content_word_count or u.word_count
        if pa is not None and pu is not None and max(pa, pu) and abs(pa - pu) / max(pa, pu) > 0.2:
            partes.append(f"palabras {pa}→{pu}")
        salida[u.id] = "; ".join(partes) or None
    return salida


def _anotar_uso(qp, *, csv: bool = False) -> None:
    """Cuenta la consulta si viene del Explorador (`?vista=explorador`).
    Nunca falla: ver api/uso.py."""
    if qp.get("vista") != "explorador":
        return
    try:
        from api import uso
        from api.dependencies import get_redis

        uso.anotar(get_redis(), qp, csv=csv)
    except Exception:  # noqa: BLE001
        pass


def _clausula_contiene(col_expr, valor):
    """`a|b` -> contiene a O b; `!a` -> no contiene a (o esta vacio).
    Se pueden mezclar: `!null|undefined` no es valido; la negacion vale para
    toda la expresion: `!a|b` = no contiene ni a ni b."""
    if valor in (None, ""):
        return None
    valor = str(valor)
    negar = valor.startswith("!")
    if negar:
        valor = valor[1:]
    partes = [p for p in (x.strip() for x in valor.split("|")) if p]
    if not partes:
        return None
    contiene = or_(*[col_expr.ilike(f"%{p}%") for p in partes])
    return or_(col_expr.is_(None), ~contiene) if negar else contiene


def _urls_filtradas(db: Session, job_id: uuid.UUID, qp, *, unir_meta: bool = False) -> Any:
    """Las URLs de un job con los filtros del Explorador aplicados.

    `unir_meta`: une con html_meta aunque ningun filtro lo pida (para ordenar
    por titulo o leer sus columnas en el CSV).

    La usan el listado (`/urls`) y el CSV de la vista (`/urls/export`): dos
    filtrados escritos por separado acabarian exportando otra lista que la que
    se ve en pantalla.
    """
    def _bool(v):
        return None if v in (None, "") else str(v).lower() in ("1", "true", "si", "sí")

    q = db.query(Url).filter(Url.job_id == job_id)
    clauses: list = []
    necesita_meta = False

    # 1) Rangos numericos: <col>_gte / <col>_lte
    for col_name, (col_expr, needs_meta) in _RANGE_FILTERS.items():
        gte_raw = qp.get(f"{col_name}_gte")
        lte_raw = qp.get(f"{col_name}_lte")
        if (gte_raw in (None, "")) and (lte_raw in (None, "")):
            continue
        try:
            if gte_raw not in (None, ""):
                clauses.append(col_expr >= float(gte_raw))
            if lte_raw not in (None, ""):
                clauses.append(col_expr <= float(lte_raw))
        except ValueError:
            continue  # numero mal escrito: se ignora
        necesita_meta |= needs_meta

    # 2) «Contiene». `a|b` = contiene a o b; `!a` = no contiene a (#131).
    #    Sin expresiones regulares: SQLite (los tests) no las tiene y en un
    #    filtro de tabla bastan estas dos.
    for param_name, (col_expr, needs_meta) in _STRING_CONTAINS_FILTERS.items():
        clausula = _clausula_contiene(col_expr, qp.get(param_name))
        if clausula is None:
            continue
        clauses.append(clausula)
        necesita_meta |= needs_meta

    # 3) Vacio / con valor
    for param, vacio in (("vacio", True), ("con_valor", False)):
        for campo in (qp.get(param) or "").split(","):
            campo = campo.strip()
            if campo not in _VACIO_FILTERS:
                continue
            col_expr, needs_meta = _VACIO_FILTERS[campo]
            clauses.append(or_(col_expr.is_(None), func.trim(col_expr) == "") if vacio
                           else (col_expr.isnot(None) & (func.trim(col_expr) != "")))
            necesita_meta |= needs_meta

    search = qp.get("search")
    if search or necesita_meta or unir_meta:
        q = q.outerjoin(HtmlMeta, Url.id == HtmlMeta.url_id)

    for campo, col in (("status_group", Url.status_group), ("resource_type", Url.resource_type)):
        if qp.get(campo) not in (None, ""):
            q = q.filter(col == qp.get(campo))
    for campo, col in (("is_internal", Url.is_internal), ("indexable", Url.indexable),
                       ("in_sitemap", Url.in_sitemap)):
        v = _bool(qp.get(campo))
        if v is not None:
            q = q.filter(col == v)
    # Varios codigos a la vez: `301,302`.
    if qp.get("status_code") not in (None, ""):
        try:
            codigos = [int(c) for c in str(qp.get("status_code")).split(",") if c.strip()]
            q = q.filter(Url.status_code.in_(codigos))
        except ValueError:
            pass

    for clause in clauses:
        q = q.filter(clause)

    # Buscador de arriba: URL o titulo
    if search:
        pattern = f"%{search}%"
        q = q.filter(or_(Url.url.ilike(pattern), HtmlMeta.title.ilike(pattern)))

    # URLs con alguna incidencia de esos tipos (lista separada por comas: las
    # vistas por ambito piden varios a la vez) y/o de esa gravedad.
    tipos = [t.strip() for t in (qp.get("issue_type") or "").split(",") if t.strip()]
    severidad = qp.get("severity")
    if tipos or severidad:
        sub = db.query(Issue.url_id).filter(Issue.job_id == job_id)
        if tipos:
            sub = sub.filter(Issue.issue_type.in_(tipos))
        if severidad:
            sub = sub.filter(Issue.severity == severidad)
        q = q.filter(Url.id.in_(sub))

    # Una plantilla (forma de ruta, `shared.plantillas.firma_de_ruta`). La
    # forma se calcula en Python —no hay forma de escribirla en SQL sin atarse a
    # Postgres—, sobre lo que ya ha filtrado lo demas.
    plantilla = qp.get("plantilla")
    if plantilla:
        from shared.plantillas import firma_de_ruta

        ids = [i for i, ruta in q.with_entities(Url.id, Url.path).all() if firma_de_ruta(ruta or "/") == plantilla]
        q = q.filter(Url.id.in_(ids or [-1]))
    return q


@router.get("/urls", response_model=PaginatedResponse[UrlResponse])
def list_urls(
    job_id: uuid.UUID,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_group: str | None = Query(None),
    is_internal: bool | None = Query(None),
    resource_type: str | None = Query(None),
    search: str | None = Query(None),
    sort_by: str | None = Query(None),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    indexable: bool | None = Query(None),
    status_code: str | None = Query(None, description="Uno o varios codigos: 301,302"),
    issue_type: str | None = Query(None, description="Uno o varios tipos separados por comas"),
    severity: str | None = Query(None),
    in_sitemap: bool | None = Query(None),
    ligero: bool = Query(False, description="Sin page_content: es el 89% del peso y la tabla no lo usa"),
    origenes: bool = Query(False, description="Anade enlazada_desde y origenes a cada fila"),
    h1: bool = Query(False, description="Anade el primer H1 visible y cuantos hay"),
    cambios: bool = Query(False, description="Anade que cambio frente al censo de referencia"),
    con: str | None = Query(None, description="Censo de referencia para `cambios` (por defecto, el de la comparacion automatica)"),
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)
    _anotar_uso(request.query_params)

    q = _urls_filtradas(db, job_id, request.query_params, unir_meta=sort_by in _SORT_META)

    total = q.count()

    # Sorting
    if sort_by and sort_by in SORT_COLUMNS:
        col = SORT_COLUMNS[sort_by]
        if sort_dir == "desc":
            q = q.order_by(col.desc().nullslast())
        else:
            q = q.order_by(col.asc().nullsfirst())
    else:
        q = q.order_by(Url.id)

    # `ligero`: el Explorador no pinta el texto de cada pagina y el panel lo
    # pide aparte; con el, una pagina de 100 filas eran 1,6 MB (89% texto).
    # Ni se carga ni se toca la relacion (tocarla la cargaria fila a fila).
    opciones = [joinedload(Url.html_meta)]
    if not ligero:
        opciones.append(joinedload(Url.page_content))
    rows = (
        q.options(*opciones)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    if ligero:
        campos = [f for f in UrlResponse.model_fields if f != "page_content"]
        items = [UrlResponse.model_validate({f: getattr(r, f, None) for f in campos}, from_attributes=True)
                 for r in rows]
    else:
        items = [UrlResponse.model_validate(r) for r in rows]
    if origenes:
        o = _origenes(db, job_id, [r.url_hash for r in rows])
        for it, r in zip(items, rows):
            it.enlazada_desde, it.origenes = o.get(r.url_hash, (None, 0))
    if h1:
        hs = _h1s(db, [r.id for r in rows])
        for it, r in zip(items, rows):
            it.h1, it.h1_count = hs.get(r.id, (None, 0))
    if cambios:
        cs = _cambios(db, _referencia(db, job_id, con), rows)
        for it, r in zip(items, rows):
            it.cambio = cs.get(r.id)

    return _paginate(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls/plantillas  -- la vista agrupada por plantilla
# ---------------------------------------------------------------------------
class _Plantilla(BaseModel):
    plantilla: str
    urls: int
    indexables: int = 0
    por_estado: dict[str, int] = {}
    con_errores: int = 0
    palabras_mediana: float | None = None
    pagerank_mediana: float | None = None
    clics_mediana: float | None = None
    inlinks: int = 0
    tramos_repetidos: list[dict] = []   # [{tramo, veces}], los que se repiten
    ejemplos: list[str] = []


class _VistaPorPlantilla(BaseModel):
    plantillas: list[_Plantilla]
    # Ultimos tramos que se repiten en TODA la vista, crucen o no de plantilla:
    # `null` aparecia en /es/null, /en/corporate/null y /es/corporativa/null.
    tramos_repetidos: list[dict] = []
    urls: int = 0


def _mediana(valores: list) -> float | None:
    v = sorted(x for x in valores if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return float(v[m]) if len(v) % 2 else (v[m - 1] + v[m]) / 2


@router.get("/urls/plantillas", response_model=_VistaPorPlantilla)
def urls_por_plantilla(
    job_id: uuid.UUID,
    request: Request,
    limite: int = Query(300, ge=1, le=2000),
    db: Session = Depends(get_session),
):
    """Las URLs de la vista agrupadas por la forma de su ruta.

    En SEO tecnico casi nada se arregla URL a URL: se arregla una plantilla. En
    Lopesan, `/de/null`, `/en/null`, `/es/null`… salian como doce 404 sueltos
    y son un solo fallo (docs/INVESTIGACION_EXPLORADOR.md). La forma normaliza
    el ultimo tramo, asi que cada grupo dice ademas que ultimo tramo se repite
    (`null` × 9): es lo que delata el fallo.
    """
    from collections import Counter, defaultdict

    from shared.plantillas import firma_de_ruta

    _get_job_or_404(job_id, db)
    _anotar_uso(request.query_params)
    q = _urls_filtradas(db, job_id, request.query_params)
    filas = q.with_entities(
        Url.id, Url.url, Url.path, Url.status_group, Url.indexable, Url.content_word_count,
        Url.word_count, Url.pagerank_score, Url.click_depth, Url.inlinks_count,
    ).all()
    con_error = {
        uid for (uid,) in db.query(Issue.url_id)
        .filter(Issue.job_id == job_id, Issue.severity == "error").distinct()
    } if filas else set()

    def ultimo(f):
        return (f.path or "/").rstrip("/").rsplit("/", 1)[-1] or "/"

    def repetidos(cuenta: Counter, n: int, minimo: int = 2) -> list[dict]:
        # Solo dicen algo si se repiten: en una plantilla sana cada pagina
        # tiene su slug.
        return [{"tramo": t, "veces": v} for t, v in cuenta.most_common(n) if v >= minimo]

    grupos: dict[str, list] = defaultdict(list)
    for f in filas:
        grupos[firma_de_ruta(f.path or "/")].append(f)

    salida = []
    for forma, fs in grupos.items():
        salida.append(_Plantilla(
            plantilla=forma, urls=len(fs),
            indexables=sum(1 for f in fs if f.indexable),
            por_estado=dict(Counter(f.status_group or "?" for f in fs)),
            con_errores=sum(1 for f in fs if f.id in con_error),
            palabras_mediana=_mediana([f.content_word_count if f.content_word_count is not None else f.word_count for f in fs]),
            pagerank_mediana=_mediana([f.pagerank_score for f in fs]),
            clics_mediana=_mediana([f.click_depth for f in fs]),
            inlinks=sum(f.inlinks_count or 0 for f in fs),
            tramos_repetidos=repetidos(Counter(ultimo(f) for f in fs), 3),
            ejemplos=[f.url for f in fs[:3]],
        ))
    salida.sort(key=lambda p: (-p.urls, p.plantilla))
    return _VistaPorPlantilla(
        plantillas=salida[:limite],
        tramos_repetidos=repetidos(Counter(ultimo(f) for f in filas), 8, minimo=3),
        urls=len(filas),
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls/export  -- CSV de LA VISTA del Explorador
# ---------------------------------------------------------------------------
def _m(campo):
    return lambda u: getattr(u.html_meta, campo, None) if u.html_meta else None


def _si_no(v):
    return "" if v is None else ("si" if v else "no")


# Las columnas del Explorador (mismas claves que EXP_COLUMN_DEFS en app.js):
# (cabecera, valor). Lo que no esta aqui no se exporta en vez de inventarse.
COLUMNAS_VISTA: dict[str, tuple[str, Any]] = {
    "url": ("URL", lambda u: u.url),
    "status_code": ("Estado", lambda u: u.status_code),
    "status_text": ("Texto estado", lambda u: u.status_text),
    "content_type": ("Tipo", lambda u: (u.content_type or "").split(";")[0] or None),
    "content_length": ("Tamaño (bytes)", lambda u: u.content_length),
    "transfer_size": ("Transferido (bytes)", lambda u: u.transfer_size),
    "response_time_ms": ("Tiempo (ms)", lambda u: u.response_time_ms),
    "click_depth": ("Clics desde home", lambda u: u.click_depth),
    "crawl_depth": ("Prof. rastreo", lambda u: u.crawl_depth),
    "folder_depth": ("Niveles", lambda u: u.folder_depth),
    "word_count": ("Palabras", lambda u: u.word_count),
    "content_word_count": ("Palabras contenido", lambda u: u.content_word_count),
    "text_ratio": ("% texto", lambda u: u.text_ratio),
    "url_length": ("Long. URL", lambda u: u.url_length),
    "inlinks_count": ("Inlinks", lambda u: u.inlinks_count),
    "unique_inlinks_count": ("Inlinks únicos", lambda u: u.unique_inlinks_count),
    "outlinks_count": ("Outlinks", lambda u: u.outlinks_count),
    "external_outlinks_count": ("Outlinks ext.", lambda u: u.external_outlinks_count),
    "pagerank_score": ("PageRank (0-100)", lambda u: u.pagerank_score),
    "pagerank_raw": ("PageRank (x media)", lambda u: u.pagerank_raw),
    "pagerank": ("PageRank (0-10, antiguo)", lambda u: u.pagerank),
    "near_duplicate_count": ("Casi duplicadas", lambda u: u.near_duplicate_count),
    "closest_similarity": ("Similitud máx.", lambda u: u.closest_similarity),
    "redirect_url": ("Redirige a", lambda u: u.redirect_url),
    "redirect_type": ("Cód. redir.", lambda u: u.redirect_type),
    "indexable": ("Indexable", lambda u: _si_no(u.indexable)),
    "indexability_status": ("Indexabilidad", lambda u: u.indexability_status),
    "is_internal": ("Interna", lambda u: _si_no(u.is_internal)),
    "in_sitemap": ("En el sitemap", lambda u: _si_no(u.in_sitemap)),
    "host": ("Host", lambda u: u.host),
    "title": ("Título", _m("title")),
    "title_len": ("Long. título", _m("title_len")),
    "title_pixel_width": ("Título (px)", _m("title_pixel_width")),
    "meta_description": ("Descripción", _m("meta_description")),
    "meta_description_len": ("Long. desc.", _m("meta_description_len")),
    "meta_description_pixel_width": ("Descripción (px)", _m("meta_description_pixel_width")),
    "canonical_href": ("Canonical", _m("canonical_href")),
    "canonical_header": ("Canonical (cabecera)", _m("canonical_header")),
    "meta_robots": ("Meta robots", _m("meta_robots")),
    "x_robots_tag": ("X-Robots-Tag", _m("x_robots_tag")),
    "meta_refresh": ("Meta refresh", _m("meta_refresh")),
    "og_title": ("OG title", _m("og_title")),
    "og_image": ("OG image", _m("og_image")),
    "og_type": ("OG type", _m("og_type")),
    "og_url": ("OG url", _m("og_url")),
    "twitter_card": ("Twitter card", _m("twitter_card")),
    "http_version": ("HTTP", lambda u: u.http_version),
    "last_modified": ("Last-Modified", lambda u: u.last_modified),
    "last_crawled_at": ("Último rastreo", lambda u: u.last_crawled_at.isoformat() if u.last_crawled_at else None),
    # Calculadas por tramo con `_origenes` (no son columnas de `urls`).
    "enlazada_desde": ("Enlazada desde", lambda u: getattr(u, "_origen", (None, 0))[0]),
    "origenes": ("Páginas que la enlazan", lambda u: getattr(u, "_origen", (None, 0))[1]),
    "h1": ("H1", lambda u: getattr(u, "_h1", (None, 0))[0]),
    "h1_count": ("Nº de H1", lambda u: getattr(u, "_h1", (None, 0))[1]),
    "cambio": ("Cambio frente al anterior", lambda u: getattr(u, "_cambio", None)),
}


@router.get("/urls/export")
def export_vista(
    job_id: uuid.UUID,
    request: Request,
    columnas: str = Query("url,status_code,title,indexable",
                          description="Claves de columna del Explorador, separadas por comas"),
    sort_by: str | None = Query(None),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_session),
):
    """Lo que se ve en el Explorador, como CSV: mismos filtros (los de
    `/urls`), mismo orden y solo las columnas visibles.

    El boton CSV del Explorador llamaba a `/export`, el censo entero con 83
    columnas: quien filtraba «4xx con enlaces entrantes» para mandarselo a
    desarrollo recibia todas las URLs (docs/INVESTIGACION_EXPLORADOR.md).

    Como `/export`, sesion nueva por ventana de 1.000 filas (decision 5): se
    sacan primero los ids en el orden de la vista y luego se leen por tramos.
    """
    _get_job_or_404(job_id, db)
    _anotar_uso(request.query_params, csv=True)
    claves = [c for c in (x.strip() for x in columnas.split(",")) if c in COLUMNAS_VISTA]
    if not claves:
        raise HTTPException(status_code=422, detail="Ninguna columna exportable: " + columnas)

    q = _urls_filtradas(db, job_id, request.query_params, unir_meta=sort_by in _SORT_META)
    if sort_by in SORT_COLUMNS:
        col = SORT_COLUMNS[sort_by]
        q = q.order_by(col.desc().nullslast() if sort_dir == "desc" else col.asc().nullsfirst(), Url.id)
    else:
        q = q.order_by(Url.id)
    ids = [i for (i,) in q.with_entities(Url.id).all()]
    ref = _referencia(db, job_id, request.query_params.get("con")) if "cambio" in claves else None
    # Sesiones por tramo sobre el MISMO motor que la peticion (en los tests,
    # SQLite): SessionLocal iria siempre a la BD configurada.
    from sqlalchemy.orm import sessionmaker
    Sesion = sessionmaker(bind=db.get_bind())

    def filas():
        buf = io.StringIO()
        w = csv.writer(buf)
        buf.write("\ufeff")  # BOM: Excel abre el UTF-8 con las tildes bien
        w.writerow([COLUMNAS_VISTA[c][0] for c in claves])
        yield buf.getvalue()
        for i in range(0, len(ids), 1000):
            tramo = ids[i:i + 1000]
            with Sesion() as s:
                por_id = {u.id: u for u in s.query(Url).options(joinedload(Url.html_meta))
                          .filter(Url.id.in_(tramo)).all()}
                if {"enlazada_desde", "origenes"} & set(claves):
                    o = _origenes(s, job_id, [u.url_hash for u in por_id.values()])
                    for u in por_id.values():
                        u._origen = o.get(u.url_hash, (None, 0))
                if "cambio" in claves:
                    cs = _cambios(s, ref, list(por_id.values()))
                    for u in por_id.values():
                        u._cambio = cs.get(u.id)
                if {"h1", "h1_count"} & set(claves):
                    hs = _h1s(s, list(por_id))
                    for u in por_id.values():
                        u._h1 = hs.get(u.id, (None, 0))
                buf = io.StringIO()
                w = csv.writer(buf)
                for uid in tramo:
                    u = por_id.get(uid)
                    if u is not None:
                        w.writerow(["" if (v := COLUMNAS_VISTA[c][1](u)) is None else v for c in claves])
                yield buf.getvalue()

    nombre = f"explorador-{str(job_id)[:8]}-{len(ids)}-urls.csv"
    return StreamingResponse(filas(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls/enlaces-entrantes  -- de donde se enlaza la vista
# ---------------------------------------------------------------------------
@router.get("/urls/enlaces-entrantes")
def export_enlaces_entrantes(
    job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
):
    """Todos los enlaces que apuntan a las URLs de la vista, como CSV: destino,
    su codigo, origen, ancla, posicion y follow. Es la lista que se le pasa a
    quien tiene que corregir los enlaces a 404 o a redirecciones (#131).

    Se recorre por tramos de 500 destinos con el indice (job_id, to_url_hash).
    """
    _get_job_or_404(job_id, db)
    _anotar_uso(request.query_params, csv=True)
    destinos = _urls_filtradas(db, job_id, request.query_params).with_entities(
        Url.url_hash, Url.url, Url.status_code).order_by(Url.id).all()
    from sqlalchemy.orm import aliased, sessionmaker

    Sesion = sessionmaker(bind=db.get_bind())
    Origen = aliased(Url)

    def filas():
        buf = io.StringIO()
        w = csv.writer(buf)
        buf.write("\ufeff")
        w.writerow(["Destino", "Estado destino", "Origen", "Estado origen", "Ancla", "Posición", "Follow", "Tipo"])
        yield buf.getvalue()
        for i in range(0, len(destinos), 500):
            tramo = {h: (u, c) for h, u, c in destinos[i:i + 500]}
            with Sesion() as s:
                enlaces = (
                    s.query(Link.to_url_hash, Origen.url, Origen.status_code, Link.anchor_text,
                            Link.link_position, Link.follow, Link.link_type)
                    .join(Origen, Origen.id == Link.from_url_id)
                    .filter(Link.job_id == job_id, Link.to_url_hash.in_(list(tramo)),
                            or_(Link.link_type.is_(None), Link.link_type.notin_(_TIPOS_NO_ENLACE)))
                    .order_by(Link.to_url_hash, Origen.url)
                    .all()
                )
            buf = io.StringIO()
            w = csv.writer(buf)
            for h, origen, cod_origen, ancla, pos, follow, tipo in enlaces:
                destino, cod = tramo[h]
                w.writerow([destino, cod, origen, cod_origen, ancla or "", pos or "",
                            "si" if follow else "no", tipo or ""])
            yield buf.getvalue()

    nombre = f"enlaces-entrantes-{str(job_id)[:8]}-{len(destinos)}-destinos.csv"
    return StreamingResponse(filas(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls/{url_id}  -- full detail for a single URL
# ---------------------------------------------------------------------------
@router.get("/urls/{url_id}", response_model=UrlDetailResponse)
def get_url_detail(
    job_id: uuid.UUID,
    url_id: int,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    url_obj = (
        db.query(Url)
        .options(
            joinedload(Url.html_meta),
            joinedload(Url.page_content),
            joinedload(Url.security),
            subqueryload(Url.headings),
            subqueryload(Url.hreflangs),
            subqueryload(Url.structured_data),
            subqueryload(Url.resources),
            subqueryload(Url.issues),
        )
        .filter(Url.id == url_id, Url.job_id == job_id)
        .first()
    )
    if url_obj is None:
        raise HTTPException(status_code=404, detail="URL not found")

    # Build outlinks using a join to get from_url string directly
    from sqlalchemy.orm import aliased
    UrlAlias = aliased(Url)

    outlink_rows = (
        db.query(Link, UrlAlias.url.label("src_url"))
        .outerjoin(UrlAlias, Link.from_url_id == UrlAlias.id)
        .filter(Link.from_url_id == url_id)
        .order_by(Link.id)
        .limit(200)
        .all()
    )

    # Build inlinks using to_url_hash match
    inlink_rows = (
        db.query(Link, UrlAlias.url.label("src_url"))
        .outerjoin(UrlAlias, Link.from_url_id == UrlAlias.id)
        .filter(Link.job_id == job_id, Link.to_url_hash == url_obj.url_hash)
        .order_by(Link.id)
        .limit(200)
        .all()
    )

    # Serialize
    resp = UrlDetailResponse.model_validate(url_obj)
    resp.headings = [HeadingResponse.model_validate(h) for h in url_obj.headings]
    resp.resources = [ResourceResponse.model_validate(r) for r in url_obj.resources]
    resp.hreflangs = [HreflangResponse.model_validate(h) for h in url_obj.hreflangs]
    resp.structured_data = [StructuredDataResponse.model_validate(s) for s in url_obj.structured_data]
    resp.security = SecurityHeadersResponse.model_validate(url_obj.security) if url_obj.security else None
    resp.issues = [IssueResponse.model_validate(i) for i in url_obj.issues]

    out_items = []
    for link_obj, src_url in outlink_rows:
        item = LinkResponse.model_validate(link_obj)
        item.from_url = src_url
        out_items.append(item)
    resp.outlinks = out_items

    in_items = []
    for link_obj, src_url in inlink_rows:
        item = LinkResponse.model_validate(link_obj)
        item.from_url = src_url
        in_items.append(item)
    resp.inlinks = in_items

    return resp


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/issues
# ---------------------------------------------------------------------------
@router.get("/issues", response_model=PaginatedResponse[IssueResponse])
def list_issues(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    severity: str | None = Query(None),
    issue_type: str | None = Query(None),
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    q = db.query(Issue).filter(Issue.job_id == job_id)

    if severity is not None:
        q = q.filter(Issue.severity == severity)
    if issue_type is not None:
        q = q.filter(Issue.issue_type == issue_type)

    total = q.count()

    # Por gravedad y luego por tipo: ordenado por `id` salian los `info`
    # (noindex) por delante de los avisos y los errores quedaban repartidos
    # en el orden en que los escribio el analisis (UX-04).
    gravedad = case(
        (Issue.severity == "error", 0),
        (Issue.severity == "warning", 1),
        (Issue.severity == "info", 2),
        else_=3,
    )
    rows = (
        q.options(joinedload(Issue.url_rel))
        .order_by(gravedad, Issue.issue_type, Issue.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for r in rows:
        resp = IssueResponse.model_validate(r)
        if r.url_rel:
            resp.url = r.url_rel.url
        items.append(resp)

    return _paginate(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/issues/resumen — una fila por tipo de incidencia
# ---------------------------------------------------------------------------
class _EjemploIssue(BaseModel):
    url_id: int
    url: str | None = None


class _ResumenIssue(BaseModel):
    issue_type: str
    severity: str
    incidencias: int
    paginas: int
    ejemplos: list[_EjemploIssue] = []


@router.get("/issues/resumen", response_model=list[_ResumenIssue])
def resumen_issues(
    job_id: uuid.UUID,
    ejemplos: int = Query(3, ge=0, le=10),
    db: Session = Depends(get_session),
):
    """Lo que enseña la vista agrupada de Problemas (UX-04): cuántas
    incidencias y cuántas PÁGINAS distintas de cada tipo, de más grave a menos.

    Las páginas van aparte de las incidencias porque no son lo mismo: un
    hreflang sin retorno emite una incidencia por idioma, y una imagen sin alt
    se cuenta una vez aunque esté en mil páginas (decisión 64).
    """
    _get_job_or_404(job_id, db)

    gravedad = case(
        (Issue.severity == "error", 0),
        (Issue.severity == "warning", 1),
        (Issue.severity == "info", 2),
        else_=3,
    )
    filas = (
        db.query(
            Issue.issue_type, Issue.severity,
            func.count(Issue.id), func.count(func.distinct(Issue.url_id)),
        )
        .filter(Issue.job_id == job_id)
        .group_by(Issue.issue_type, Issue.severity)
        .order_by(gravedad, func.count(func.distinct(Issue.url_id)).desc(), Issue.issue_type)
        .all()
    )

    resultado = []
    for tipo, sev, n, paginas in filas:
        muestra = []
        if ejemplos:
            # Una consulta corta por tipo, por el indice (job_id, issue_type):
            # son unas decenas de tipos, y un ROW_NUMBER sobre todo el censo
            # (841.901 incidencias en el mayor) ordenaria filas que no se piden.
            # Paginas DISTINTAS: un hreflang sin retorno emite una incidencia
            # por idioma, y tres ejemplos de la misma URL no ensenan nada (y
            # repetian la clave del x-for de la interfaz).
            vistos: set[int] = set()
            for uid, u in (
                db.query(Issue.url_id, Url.url)
                .join(Url, Url.id == Issue.url_id)
                .filter(Issue.job_id == job_id, Issue.issue_type == tipo, Issue.severity == sev)
                .order_by(Issue.id)
                .limit(ejemplos * 5)
                .all()
            ):
                if uid in vistos:
                    continue
                vistos.add(uid)
                muestra.append(_EjemploIssue(url_id=uid, url=u))
                if len(muestra) == ejemplos:
                    break
        resultado.append(_ResumenIssue(
            issue_type=tipo, severity=sev, incidencias=n, paginas=paginas, ejemplos=muestra,
        ))
    return resultado


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/issues/urls — URLs affected by specific issue types
# ---------------------------------------------------------------------------
class _AffectedUrl(BaseModel):
    id: int
    url: str
    status_code: int | None = None


@router.get("/issues/urls", response_model=list[_AffectedUrl])
def list_issue_urls(
    job_id: uuid.UUID,
    issue_type: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    """Return distinct URLs affected by a given issue type."""
    _get_job_or_404(job_id, db)

    rows = (
        db.query(Url.id, Url.url, Url.status_code)
        .join(Issue, Issue.url_id == Url.id)
        .filter(Issue.job_id == job_id, Issue.issue_type == issue_type)
        .distinct()
        .order_by(Url.id)
        .limit(limit)
        .all()
    )
    return [_AffectedUrl(id=r.id, url=r.url, status_code=r.status_code) for r in rows]


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/links
# ---------------------------------------------------------------------------
@router.get("/links", response_model=PaginatedResponse[LinkResponse])
def list_links(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    q = db.query(Link).filter(Link.job_id == job_id)

    total = q.count()

    # Ordenado por destino y no por `id`: con `ORDER BY id` Postgres recorre
    # la clave primaria de la tabla ENTERA (todos los rastreos, 187 M filas en
    # local) filtrando por job, y la primera pagina de Lopesan tardaba 2 min
    # 02 s — la pestana se quedaba en «0 enlaces · Cargando...». Con
    # `(to_url_hash, id)` sirve el indice `ix_links_to_hash (job_id,
    # to_url_hash)`: 0,3 ms. De paso, los enlaces a un mismo destino salen
    # juntos, que es como se leen.
    rows = (
        q.options(joinedload(Link.from_url_rel))
        .order_by(Link.to_url_hash, Link.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for r in rows:
        resp = LinkResponse.model_validate(r)
        if r.from_url_rel:
            resp.from_url = r.from_url_rel.url
        items.append(resp)

    return _paginate(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/stats
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/diff/{otro_id}  --  que cambio entre dos censos
# ---------------------------------------------------------------------------
@router.get("/diff/{otro_id}")
def get_diff(
    job_id: uuid.UUID,
    otro_id: uuid.UUID,
    map_host: str | None = None,
    db: Session = Depends(get_session),
):
    """Que cambio entre este censo y otro del mismo sitio.

    `map_host=pre.x.com=x.com` reescribe el host del primero, para comparar
    preproduccion contra produccion; sin el, dos origenes distintos se
    rechazan en vez de devolver "ha desaparecido el sitio entero" (#32).
    """
    from shared.comparacion import (
        _truncado, comparar_censos, comparar_incidencias,
        incidencias_de_censo, paginas_de_censo, resumen_de_job,
    )

    a = _get_job_or_404(job_id, db)
    b = _get_job_or_404(otro_id, db)

    mapa: dict[str, str] = {}
    if map_host:
        for par in map_host.split(","):
            if "=" in par:
                origen, destino = par.split("=", 1)
                mapa[origen.strip().lower()] = destino.strip().lower()

    resultado = comparar_censos(
        paginas_de_censo(db, job_id),
        paginas_de_censo(db, otro_id),
        job_a=resumen_de_job(a),
        job_b=resumen_de_job(b),
        mapa_hosts=mapa,
    )
    # Que se arreglo, que aparecio y que sigue. Comparar paginas no contesta
    # "que arreglo el cliente desde la auditoria anterior", que es para lo que
    # se mira un seguimiento mensual (#32).
    if resultado.get("comparable"):
        from shared.comparacion import _aplicar_mapa

        def _aplicar(u):
            return _aplicar_mapa(u, mapa)

        antes, ahora = resultado["antes"], resultado["ahora"]
        # Las paginas vienen ordenadas por fecha, asi que hay que mirar CUAL de
        # los dos jobs es el anterior, no el primer parametro.
        viejo_id = job_id if antes.get("fecha") == _resumen_fecha(a) else otro_id
        nuevo_id = otro_id if viejo_id == job_id else job_id
        from shared.comparacion import _norm

        resultado["incidencias"] = comparar_incidencias(
            incidencias_de_censo(db, viejo_id),
            incidencias_de_censo(db, nuevo_id),
            mapa_hosts=mapa,
            puede_afirmar_resueltas=not _truncado(
                antes.get("status"), antes.get("finish_reason")),
            # Una incidencia cuya pagina ya no esta en el censo no esta
            # resuelta: no se sabe.
            urls_del_nuevo={_norm(_aplicar(p["url"]))
                            for p in paginas_de_censo(db, nuevo_id)},
            # Dos censos analizados con codigo distinto no se comparan por
            # incidencias: un hallazgo puede salir "resuelto" solo porque la
            # regla que lo emitia ha cambiado.
            analisis_a=antes.get("analisis_version"),
            analisis_b=ahora.get("analisis_version"),
        )

    # `antes` y `ahora` los pone la comparacion, por FECHA: aqui se asignaban
    # por el orden de los parametros, asi que elegir el censo viejo en el
    # desplegable daba la vuelta al informe entero.
    return resultado


def _resumen_fecha(job) -> str | None:
    return job.started_at.isoformat() if job.started_at else None


@router.get("/stats", response_model=JobStats)
def get_stats(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = _get_job_or_404(job_id, db)

    # Total URL count for this job
    total_urls = db.query(func.count(Url.id)).filter(Url.job_id == job_id).scalar() or 0

    # URLs by status group
    status_rows = (
        db.query(Url.status_group, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.status_group.isnot(None))
        .group_by(Url.status_group)
        .all()
    )
    urls_by_status = [
        StatusGroupCount(status_group=sg, count=c) for sg, c in status_rows
    ]

    # Issues by type + severity
    issue_rows = (
        db.query(Issue.issue_type, Issue.severity, func.count(Issue.id))
        .filter(Issue.job_id == job_id)
        .group_by(Issue.issue_type, Issue.severity)
        .all()
    )
    issues_by_type = [
        IssueTypeCount(issue_type=it, severity=sev, count=c)
        for it, sev, c in issue_rows
    ]

    # Top 20 hosts
    host_rows = (
        db.query(Url.host, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.host.isnot(None))
        .group_by(Url.host)
        .order_by(func.count(Url.id).desc())
        .limit(20)
        .all()
    )
    top_hosts = [HostCount(host=h, count=c) for h, c in host_rows]

    # URLs by resource_type
    rt_rows = (
        db.query(Url.resource_type, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.resource_type.isnot(None))
        .group_by(Url.resource_type)
        .all()
    )
    urls_by_rt = [
        ResourceTypeCount(resource_type=rt, count=c) for rt, c in rt_rows
    ]

    # Internal / external counts
    internal_count = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True,
    ).scalar() or 0
    external_count = total_urls - internal_count

    # Paginas HTML internas con 2xx y cuantas son indexables. `indexable` NULL
    # (rastreo sin analizar) no cuenta en ninguno de los dos lados.
    paginas_html, indexables, no_indexables = db.query(
        func.count(Url.id),
        func.count(Url.id).filter(Url.indexable.is_(True)),
        func.count(Url.id).filter(Url.indexable.is_(False)),
    ).filter(
        Url.job_id == job_id, Url.is_internal.is_(True), Url.is_html.is_(True),
        Url.status_group == "2xx",
    ).one()

    click_depth_calculada = db.query(
        exists().where(Url.job_id == job_id, Url.click_depth.isnot(None))
    ).scalar()

    return JobStats(
        job_id=job.id,
        total_urls=total_urls,
        # En un rastreo terminado la verdad son las filas guardadas: el
        # contador de la tuberia se escribia por proceso y los rastreos
        # reanudados antes del arreglo se quedaron con el ultimo tramo (802
        # frente a 34.704 reales). `total_urls` ya esta contado aqui arriba.
        total_urls_crawled=(
            max(job.total_urls_crawled or 0, total_urls)
            if job.status in ("completed", "failed", "cancelled", "stalled")
            else job.total_urls_crawled
        ),
        total_urls_failed=job.total_urls_failed,
        urls_by_status_group=urls_by_status,
        issues_by_type=issues_by_type,
        top_hosts=top_hosts,
        urls_by_resource_type=urls_by_rt,
        internal_count=internal_count,
        external_count=external_count,
        paginas_html=paginas_html or 0,
        indexables=indexables or 0,
        no_indexables=no_indexables or 0,
        click_depth_calculada=bool(click_depth_calculada),
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/export  --  stream CSV of all URLs + metadata
# ---------------------------------------------------------------------------
CSV_COLUMNS = [
    "url",
    "host",
    "path",
    "scheme",
    "is_internal",
    "crawl_depth",
    "click_depth",
    "status_code",
    "status_group",
    "status_text",
    "content_type",
    "content_length",
    "transfer_size",
    "response_time_ms",
    "is_html",
    "resource_type",
    "redirect_url",
    "redirect_type",
    "indexable",
    "indexability_status",
    "url_length",
    "folder_depth",
    "word_count",
    "content_word_count",
    "text_ratio",
    "last_modified",
    "http_version",
    "inlinks_count",
    "unique_inlinks_count",
    "outlinks_count",
    "external_outlinks_count",
    "pagerank",
    # La que hay que leer: 0-100 logaritmica. La de 0-10 se queda por
    # compatibilidad, pero aplasta el 97,8% del sitio en 0,00xx.
    "pagerank_score",
    "pagerank_raw",
    # False = hay plantillas que montan enlaces con JavaScript y el rastreo fue
    # sin render: el grafo esta incompleto y estas cifras salen de un grafo
    # parcial. Vacio = no se llego a comprobar.
    "pagerank_fiable",
    "in_sitemap",
    "near_duplicate_count",
    "closest_similarity",
    "title",
    "title_len",
    "title_pixel_width",
    "meta_description",
    "meta_description_len",
    "meta_description_pixel_width",
    "meta_robots",
    "x_robots_tag",
    "canonical_href",
    "canonical_header",
    "meta_keywords",
    "rel_next",
    "rel_prev",
    "meta_refresh",
    "has_meta_outside_head",
    # Encabezados: lo primero que mira cualquier auditoria y lo unico que
    # obligaba a abrir el backup ZIP para verlo.
    "h1_count",
    "h1_1",
    "h1_2",
    "h2_count",
    "h2_1",
    "h2_2",
    # Social
    "og_title",
    "og_description",
    "og_image",
    "og_type",
    "twitter_card",
    "twitter_title",
    "twitter_description",
    # Internacionalizacion
    "hreflang_count",
    "hreflang_langs",
    # Datos estructurados
    "structured_data_count",
    "structured_data_types",
    "structured_data_status",
    # Imagenes
    "images_count",
    "images_missing_alt",
    # Seguridad
    "is_https",
    "has_hsts",
    "has_csp",
    "has_mixed_content",
    # Otros
    "blocked_by_robots",
    "body_hash",
    "content_hash",
    "last_crawled_at",
    "content_text_first_500",
    "content_char_count",
]


def _val(v) -> str:
    """Format a value for CSV output."""
    if v is None:
        return ""
    return str(v)


def _csv_row(url_obj: Url, extras: dict[str, Any] | None = None) -> list[str]:
    """Build a flat CSV row from a Url + optional HtmlMeta + PageContent.

    *extras* trae lo agregado por lote (encabezados, hreflang, datos
    estructurados, imagenes): recorrerlo por relacion ORM haria una consulta
    por URL y por tabla.
    """
    meta: HtmlMeta | None = url_obj.html_meta
    pc: PageContent | None = url_obj.page_content
    sec: SecurityHeaders | None = url_obj.security
    ex = extras or {}

    # El texto va recortado a 500 caracteres a proposito (el CSV completo es
    # /content/export). La columna lo dice en el nombre: antes se llamaba
    # "content_text" y al lado iba la longitud COMPLETA, asi que parecia el
    # texto entero recortado por Excel.
    content_text_val = ""
    if pc and pc.content_text:
        content_text_val = pc.content_text[:500]

    return [
        _val(url_obj.url),
        _val(url_obj.host),
        _val(url_obj.path),
        _val(url_obj.scheme),
        _val(url_obj.is_internal),
        _val(url_obj.crawl_depth),
        _val(url_obj.click_depth),
        _val(url_obj.status_code),
        _val(url_obj.status_group),
        _val(url_obj.status_text),
        _val(url_obj.content_type),
        _val(url_obj.content_length),
        _val(url_obj.transfer_size),
        _val(url_obj.response_time_ms),
        _val(url_obj.is_html),
        _val(url_obj.resource_type),
        _val(url_obj.redirect_url),
        _val(url_obj.redirect_type),
        _val(url_obj.indexable),
        _val(url_obj.indexability_status),
        _val(url_obj.url_length),
        _val(url_obj.folder_depth),
        _val(url_obj.word_count),
        _val(url_obj.content_word_count),
        _val(url_obj.text_ratio),
        _val(url_obj.last_modified),
        _val(url_obj.http_version),
        _val(url_obj.inlinks_count),
        _val(url_obj.unique_inlinks_count),
        _val(url_obj.outlinks_count),
        _val(url_obj.external_outlinks_count),
        _val(url_obj.pagerank),
        _val(url_obj.pagerank_score),
        _val(url_obj.pagerank_raw),
        _val(ex.get("pagerank_fiable")),
        _val(url_obj.in_sitemap),
        _val(url_obj.near_duplicate_count),
        _val(url_obj.closest_similarity),
        # HtmlMeta fields
        _val(meta.title) if meta else "",
        _val(meta.title_len) if meta else "",
        _val(meta.title_pixel_width) if meta else "",
        _val(meta.meta_description) if meta else "",
        _val(meta.meta_description_len) if meta else "",
        _val(meta.meta_description_pixel_width) if meta else "",
        _val(meta.meta_robots) if meta else "",
        _val(meta.x_robots_tag) if meta else "",
        _val(meta.canonical_href) if meta else "",
        _val(meta.canonical_header) if meta else "",
        _val(meta.meta_keywords) if meta else "",
        _val(meta.rel_next) if meta else "",
        _val(meta.rel_prev) if meta else "",
        _val(meta.meta_refresh) if meta else "",
        _val(meta.has_meta_outside_head) if meta else "",
        # Encabezados
        _val(ex.get("h1_count", 0)),
        _val(ex.get("h1_1")),
        _val(ex.get("h1_2")),
        _val(ex.get("h2_count", 0)),
        _val(ex.get("h2_1")),
        _val(ex.get("h2_2")),
        # Social
        _val(meta.og_title) if meta else "",
        _val(meta.og_description) if meta else "",
        _val(meta.og_image) if meta else "",
        _val(meta.og_type) if meta else "",
        _val(meta.twitter_card) if meta else "",
        _val(meta.twitter_title) if meta else "",
        _val(meta.twitter_description) if meta else "",
        # Hreflang
        _val(ex.get("hreflang_count", 0)),
        _val(ex.get("hreflang_langs")),
        # Datos estructurados
        _val(ex.get("sd_count", 0)),
        _val(ex.get("sd_types")),
        _val(ex.get("sd_status")),
        # Imagenes
        _val(ex.get("img_count", 0)),
        _val(ex.get("img_sin_alt", 0)),
        # Seguridad
        _val(sec.is_https) if sec else "",
        _val(sec.has_hsts) if sec else "",
        _val(sec.has_csp) if sec else "",
        _val(sec.has_mixed_content) if sec else "",
        # Otros
        _val(url_obj.blocked_by_robots),
        _val(url_obj.body_hash),
        _val(url_obj.content_hash),
        _val(url_obj.last_crawled_at.isoformat() if url_obj.last_crawled_at else None),
        # PageContent fields
        content_text_val,
        _val(pc.content_length) if pc else "",
    ]


_ORDEN_VALIDACION = {"error": 0, "warning": 1, "ok": 2}


def _extras_por_lote(session: Session, url_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Agrega encabezados, hreflang, datos estructurados e imagenes del lote.

    Cuatro consultas por ventana de 1.000 URLs en lugar de cuatro por URL:
    recorrerlo por relaciones ORM son 4.000 consultas por ventana y convierte
    un export de 30.000 paginas en una espera de minutos.
    """
    extras: dict[int, dict[str, Any]] = {uid: {} for uid in url_ids}
    if not url_ids:
        return extras

    # Encabezados: solo h1/h2 y en orden de documento; guardamos los dos
    # primeros de cada uno, como hace Screaming Frog.
    filas = (
        session.query(Heading.url_id, Heading.tag, Heading.text)
        .filter(Heading.url_id.in_(url_ids), Heading.tag.in_(("h1", "h2")),
                # Los que no se pintan no son el titular de la pagina: el CSV
                # daria el clon movil como h1_1 segun el orden del DOM.
                Heading.oculto.isnot(True))
        .order_by(Heading.url_id, Heading.position)
        .all()
    )
    for url_id, tag, texto in filas:
        d = extras[url_id]
        clave_n = f"{tag}_count"
        d[clave_n] = d.get(clave_n, 0) + 1
        if d[clave_n] <= 2:
            d[f"{tag}_{d[clave_n]}"] = texto

    for url_id, n_hl, langs in (
        session.query(
            Hreflang.url_id,
            func.count(Hreflang.id),
            func.string_agg(Hreflang.lang.distinct(), "|"),
        )
        .filter(Hreflang.url_id.in_(url_ids))
        .group_by(Hreflang.url_id)
        .all()
    ):
        extras[url_id].update(hreflang_count=n_hl, hreflang_langs=langs)

    for url_id, n_sd, tipos, estados in (
        session.query(
            StructuredData.url_id,
            func.count(StructuredData.id),
            func.string_agg(StructuredData.schema_type.distinct(), "|"),
            func.string_agg(StructuredData.validation_status.distinct(), "|"),
        )
        .filter(StructuredData.url_id.in_(url_ids))
        .group_by(StructuredData.url_id)
        .all()
    ):
        peor = None
        if estados:
            peor = sorted(estados.split("|"), key=lambda e: _ORDEN_VALIDACION.get(e, 9))[0]
        extras[url_id].update(sd_count=n_sd, sd_types=tipos, sd_status=peor)

    for url_id, n_img, sin_alt in (
        session.query(
            Resource.url_id,
            func.count(Resource.id),
            func.count(Resource.id).filter(
                or_(Resource.alt_text.is_(None), Resource.alt_text == "")
            ),
        )
        .filter(Resource.url_id.in_(url_ids), Resource.resource_type == "image")
        .group_by(Resource.url_id)
        .all()
    ):
        extras[url_id].update(img_count=n_img, img_sin_alt=sin_alt)

    return extras


def _pagerank_fiable(session: Session, job_id: uuid.UUID):
    """Si el grafo de enlaces del job es fiable (A7 de #24).

    Va en cada fila del CSV a proposito: quien ordena por PageRank en una hoja
    de calculo no abre el endpoint del job, y sin esto se lleva unas cifras
    calculadas sobre un grafo incompleto sin saberlo.
    """
    # OJO con el orden: el analisis escribe `pagerank_resumen` ANTES de que el
    # worker lance la comprobacion de render, asi que en un rastreo recien
    # terminado la clave existe con valor None. Mirar solo si la clave esta
    # dejaba la columna VACIA en todas las filas del CSV aunque el `js_check`
    # dijera luego que si: medido en el censo de progym, 4.320 filas sin el
    # aviso con `js_check.grafo_fiable = true`. Un None es "aun no se sabe", no
    # una respuesta.
    resumen = session.query(Job.pagerank_resumen).filter(Job.id == job_id).scalar()
    if isinstance(resumen, dict) and resumen.get("grafo_fiable") is not None:
        return resumen["grafo_fiable"]
    js_check = session.query(Job.js_check).filter(Job.id == job_id).scalar()
    if isinstance(js_check, dict) and js_check.get("grafo_fiable") is not None:
        return js_check["grafo_fiable"]
    # Con `render_js` la comprobacion no llega a correr —existe justamente para
    # los rastreos SIN render—, asi que no hay js_check que mirar y la columna
    # se quedaba vacia tambien ahi. Un rastreo que renderiza ya ha visto los
    # enlaces que monta el JavaScript, que es lo unico que esta columna dice.
    config = session.query(Job.config).filter(Job.id == job_id).scalar()
    if isinstance(config, dict) and config.get("render_js"):
        return True
    return None


def _stream_csv(job_id: uuid.UUID):
    """Generator that yields CSV content in chunks using windowed queries."""
    # Each chunk opens its own session so we do not hold one long transaction.
    from shared.database import SessionLocal

    batch_size = 1000
    last_id = 0

    # Header row. Prefix a UTF-8 BOM so Excel opens the file with the correct
    # encoding (otherwise accented/Spanish characters render as mojibake).
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    yield "﻿" + buf.getvalue()

    while True:
        session = SessionLocal()
        try:
            rows = (
                session.query(Url)
                .options(
                    joinedload(Url.html_meta),
                    joinedload(Url.page_content),
                    joinedload(Url.security),
                )
                .filter(Url.job_id == job_id, Url.id > last_id)
                .order_by(Url.id)
                .limit(batch_size)
                .all()
            )

            if not rows:
                break

            extras = _extras_por_lote(session, [r.id for r in rows])
            fiable = _pagerank_fiable(session, job_id)
            for datos in extras.values():
                datos["pagerank_fiable"] = fiable

            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow(_csv_row(row, extras.get(row.id)))
                last_id = row.id

            yield buf.getvalue()
        finally:
            session.close()


@router.get("/export")
def export_csv(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    return StreamingResponse(
        _stream_csv(job_id),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=job_{job_id}_urls.csv",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/links/export  --  stream CSV of all links
# ---------------------------------------------------------------------------
LINKS_CSV_COLUMNS = [
    "from_url",
    "to_url",
    "anchor_text",
    "link_type",
    "link_position",
    "rel",
    "follow",
    "target",
    "is_internal",
    "alt_text",
]


def _links_csv_row(link_obj: Link) -> list[str]:
    """Build a flat CSV row from a Link (+ resolved source URL)."""
    from_url = link_obj.from_url_rel.url if link_obj.from_url_rel else ""
    return [
        _val(from_url),
        _val(link_obj.to_url),
        _val(link_obj.anchor_text),
        _val(link_obj.link_type),
        _val(link_obj.link_position),
        _val(link_obj.rel),
        _val(link_obj.follow),
        _val(link_obj.target),
        _val(link_obj.is_internal),
        _val(link_obj.alt_text),
    ]


def _stream_links_csv(job_id: uuid.UUID):
    """Generator that yields the link graph as CSV in windowed chunks."""
    from shared.database import SessionLocal

    batch_size = 1000
    last_id = 0

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(LINKS_CSV_COLUMNS)
    yield "﻿" + buf.getvalue()

    while True:
        session = SessionLocal()
        try:
            rows = (
                session.query(Link)
                .options(joinedload(Link.from_url_rel))
                .filter(Link.job_id == job_id, Link.id > last_id)
                .order_by(Link.id)
                .limit(batch_size)
                .all()
            )

            if not rows:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow(_links_csv_row(row))
                last_id = row.id

            yield buf.getvalue()
        finally:
            session.close()


@router.get("/links/export")
def export_links_csv(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    return StreamingResponse(
        _stream_links_csv(job_id),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=job_{job_id}_links.csv",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls/{url_id}/raw-html  --  el HTML tal cual
# ---------------------------------------------------------------------------
@router.get("/urls/{url_id}/raw-html", response_class=PlainTextResponse)
def get_raw_html(
    job_id: uuid.UUID,
    url_id: int,
    db: Session = Depends(get_session),
):
    """HTML guardado de una URL, si el job se lanzo con `store_raw_html`.

    Endpoint aparte y no un campo del detalle de la URL a proposito: son 170 kB
    de media por pagina, y el detalle lo pide la interfaz cada vez que se abre
    una fila. Devolverlo ahi dentro convertiria una ficha en una descarga.
    """
    _get_job_or_404(job_id, db)
    fila = (
        db.query(PageContent.raw_html)
        .join(Url, Url.id == PageContent.url_id)
        .filter(Url.job_id == job_id, Url.id == url_id)
        .first()
    )
    if fila is None:
        raise HTTPException(status_code=404, detail="URL no encontrada en este rastreo")
    if not fila[0]:
        raise HTTPException(
            status_code=404,
            detail=(
                "Sin HTML guardado: el rastreo no se lanzo con "
                "extraction.store_raw_html"
            ),
        )
    return fila[0]


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/content/export  --  stream CSV of page content
# ---------------------------------------------------------------------------
CONTENT_CSV_COLUMNS = [
    "url",
    "status_code",
    "title",
    "meta_description",
    "word_count",
    "content_word_count",
    "content_char_count",
    "content_text",
    "content_markdown",
]


def _content_csv_row(url_obj: Url) -> list[str]:
    """Build a flat CSV row with the full extracted page content."""
    meta: HtmlMeta | None = url_obj.html_meta
    pc: PageContent | None = url_obj.page_content
    return [
        _val(url_obj.url),
        _val(url_obj.status_code),
        _val(meta.title) if meta else "",
        _val(meta.meta_description) if meta else "",
        _val(url_obj.word_count),
        _val(url_obj.content_word_count),
        _val(pc.content_length) if pc else "",
        _val(pc.content_text) if pc else "",
        _val(pc.content_markdown) if pc else "",
    ]


def _stream_content_csv(job_id: uuid.UUID):
    """Generator that yields full page content as CSV in windowed chunks.

    Only URLs that actually have extracted content are included.
    """
    from shared.database import SessionLocal

    batch_size = 500  # smaller window: rows carry full page text
    last_id = 0

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CONTENT_CSV_COLUMNS)
    yield "﻿" + buf.getvalue()

    while True:
        session = SessionLocal()
        try:
            rows = (
                session.query(Url)
                .join(PageContent, PageContent.url_id == Url.id)
                .options(joinedload(Url.html_meta), joinedload(Url.page_content))
                .filter(Url.job_id == job_id, Url.id > last_id)
                .order_by(Url.id)
                .limit(batch_size)
                .all()
            )

            if not rows:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow(_content_csv_row(row))
                last_id = row.id

            yield buf.getvalue()
        finally:
            session.close()


@router.get("/content/export")
def export_content_csv(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    return StreamingResponse(
        _stream_content_csv(job_id),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=job_{job_id}_content.csv",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/backup  --  full backup as ZIP with NDJSON
# ---------------------------------------------------------------------------
@router.get("/backup")
def export_backup(
    job_id: uuid.UUID,
    include_content: bool = Query(True),
    db: Session = Depends(get_session),
):
    job = _get_job_or_404(job_id, db)

    if job.status in ("pending", "running", "analyzing"):
        raise HTTPException(
            status_code=409,
            detail="No se puede exportar un job que aun esta en ejecucion",
        )

    return StreamingResponse(
        stream_backup_zip(job_id, include_content),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="backup_{job_id}.zip"',
        },
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/insights  --  SEO insights report
# ---------------------------------------------------------------------------
def _safe_pct(numerator: int, denominator: int) -> float:
    """Return percentage (0-100) or 0 if denominator is 0."""
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _clamp_score(val: float) -> int:
    return max(0, min(100, round(val)))


def _html_2xx(job_id: uuid.UUID, db: Session) -> int:
    """Paginas HTML internas que responden 2xx.

    Es el denominador de todo lo que depende del contenido: solo de ellas se
    extraen title, h1 y texto, y solo ellas pueden ser indexables. Dividir
    tambien por saltos 3xx, 404 y PDFs hundia los porcentajes.
    """
    return db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
        Url.status_code >= 200, Url.status_code < 300,
    ).scalar() or 0


def _paginas_con(job_id: uuid.UUID, db: Session, tipos: list[str]) -> int:
    """PAGINAS distintas con alguna de esas incidencias.

    Sumar incidencias contaba dos veces una pagina con `title_too_long` y
    `title_duplicate`, y `pct_thin` podia pasar del 100%.
    """
    return db.query(func.count(func.distinct(Issue.url_id))).filter(
        Issue.job_id == job_id, Issue.issue_type.in_(tipos),
    ).scalar() or 0


def _calc_crawlability(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Crawlability: status codes, indexability, redirects."""
    total = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True,
    ).scalar() or 0

    status_counts: dict[str, int] = {}
    rows = (
        db.query(Url.status_group, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.is_internal == True)
        .group_by(Url.status_group)
        .all()
    )
    for sg, c in rows:
        if sg:
            status_counts[sg] = c

    ok_2xx = status_counts.get("2xx", 0)
    redirects_3xx = status_counts.get("3xx", 0)
    errors_4xx = status_counts.get("4xx", 0)
    errors_5xx = status_counts.get("5xx", 0)

    indexable_count = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.indexable == True,
    ).scalar() or 0

    # Indexable solo puede serlo una pagina HTML que responde 2xx; en saltos,
    # errores y PDFs `indexable` es NULL y contaban como "no indexables".
    candidatas = _html_2xx(job_id, db)
    pct_2xx = _safe_pct(ok_2xx, total)
    pct_indexable = _safe_pct(indexable_count, candidatas)
    pct_redirects = _safe_pct(redirects_3xx, total)
    pct_4xx = _safe_pct(errors_4xx, total)
    pct_5xx = _safe_pct(errors_5xx, total)

    # Score: base from 2xx%, penalized by errors
    score = pct_2xx * 0.5 + pct_indexable * 0.3 + max(0, 100 - pct_4xx * 5 - pct_5xx * 10) * 0.2

    recs: list[Recommendation] = []
    if errors_4xx > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir errores 4xx",
            description=f"Hay {errors_4xx} URLs internas que devuelven errores 4xx (no encontrado). Revisa los enlaces rotos y corrige o elimina las referencias.",
            affected_count=errors_4xx,
            url_filter={"status_group": "4xx", "is_internal": "true"},
        ))
    if errors_5xx > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir errores 5xx",
            description=f"Hay {errors_5xx} URLs internas con errores de servidor (5xx). Investiga los problemas del servidor para estas paginas.",
            affected_count=errors_5xx,
            url_filter={"status_group": "5xx", "is_internal": "true"},
        ))
    if pct_redirects > 10:
        recs.append(Recommendation(
            priority="media", title="Reducir redirecciones",
            description=f"El {pct_redirects}% de las URLs internas son redirecciones. Actualiza los enlaces para apuntar directamente a las URLs finales.",
            affected_count=redirects_3xx,
            url_filter={"status_group": "3xx", "is_internal": "true"},
        ))
    non_indexable = candidatas - indexable_count
    if candidatas > 0 and pct_indexable < 80:
        recs.append(Recommendation(
            priority="media", title="Mejorar indexabilidad",
            description=f"Solo el {pct_indexable}% de las paginas HTML que responden 200 son indexables. Revisa las directivas noindex y canonicals para asegurar que las paginas importantes sean rastreables.",
            affected_count=non_indexable,
            url_filter={"indexable": "false", "is_internal": "true"},
        ))

    return CategoryInsight(
        key="crawlability", name="Rastreabilidad", icon="🔍",
        score=_clamp_score(score),
        metrics={
            "total_internal": total, "html_2xx": candidatas,
            "pct_2xx": pct_2xx, "pct_indexable": pct_indexable,
            "pct_redirects": pct_redirects, "pct_4xx": pct_4xx, "pct_5xx": pct_5xx,
            "errors_4xx": errors_4xx, "errors_5xx": errors_5xx, "redirects_3xx": redirects_3xx,
        },
        recommendations=recs,
    )


def _calc_content(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Content quality: titles, descriptions, word count, thin content."""
    total_html = _html_2xx(job_id, db)

    # Issue counts for content-related issues
    content_issue_types = [
        "title_missing", "title_too_short", "title_too_long", "title_duplicate",
        "description_missing", "description_too_short", "description_too_long", "description_duplicate",
        "h1_missing", "h1_multiple", "h1_duplicate", "low_word_count",
        "very_low_text_ratio", "low_text_ratio", "duplicate_content",
    ]
    issue_rows = (
        db.query(Issue.issue_type, func.count(Issue.id))
        .filter(Issue.job_id == job_id, Issue.issue_type.in_(content_issue_types))
        .group_by(Issue.issue_type)
        .all()
    )
    issue_map: dict[str, int] = {it: c for it, c in issue_rows}

    title_missing = issue_map.get("title_missing", 0)
    title_problems = _paginas_con(job_id, db, ["title_missing", "title_too_short", "title_too_long", "title_duplicate"])
    desc_missing = issue_map.get("description_missing", 0)
    desc_problems = _paginas_con(job_id, db, ["description_missing", "description_too_short", "description_too_long", "description_duplicate"])
    h1_problems = _paginas_con(job_id, db, ["h1_missing", "h1_multiple", "h1_duplicate"])
    thin_content = _paginas_con(job_id, db, ["low_word_count", "very_low_text_ratio", "low_text_ratio"])

    # Average word count
    avg_wc = float(db.query(func.avg(Url.word_count)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
        Url.status_code >= 200, Url.status_code < 300, Url.word_count.isnot(None),
    ).scalar() or 0)

    pct_title_ok = _safe_pct(max(0, total_html - title_problems), total_html)
    pct_desc_ok = _safe_pct(max(0, total_html - desc_problems), total_html)
    pct_h1_ok = _safe_pct(max(0, total_html - h1_problems), total_html)
    pct_thin = _safe_pct(thin_content, total_html)

    score = pct_title_ok * 0.3 + pct_desc_ok * 0.25 + pct_h1_ok * 0.2 + max(0, 100 - pct_thin * 2) * 0.15 + min(100, avg_wc / 3) * 0.1

    recs: list[Recommendation] = []
    if title_missing > 0:
        recs.append(Recommendation(
            priority="alta", title="Agregar titulos faltantes",
            description=f"Hay {title_missing} paginas sin etiqueta title. El titulo es crucial para el posicionamiento y el CTR en los resultados de busqueda.",
            affected_count=title_missing,
            issue_types=["title_missing"],
        ))
    if title_problems - title_missing > 0:
        recs.append(Recommendation(
            priority="media", title="Optimizar titulos",
            description=f"Hay {title_problems - title_missing} paginas con titulos problematicos (demasiado cortos, largos o duplicados). Optimiza cada titulo para que sea unico, tenga al menos 10 caracteres y no pase de 580 pixeles de ancho (que es por donde Google lo corta).",
            affected_count=title_problems - title_missing,
            issue_types=["title_too_short", "title_too_long", "title_duplicate"],
        ))
    if desc_missing > 0:
        recs.append(Recommendation(
            priority="alta", title="Agregar meta descripciones",
            description=f"Hay {desc_missing} paginas sin meta descripcion. Las descripciones ayudan a mejorar el CTR en los resultados de busqueda.",
            affected_count=desc_missing,
            issue_types=["description_missing"],
        ))
    if h1_problems > 0:
        recs.append(Recommendation(
            priority="media", title="Corregir encabezados H1",
            description=f"Hay {h1_problems} paginas con problemas en el H1 (faltante, multiples o duplicados). Cada pagina debe tener exactamente un H1 unico.",
            affected_count=h1_problems,
            issue_types=["h1_missing", "h1_multiple", "h1_duplicate"],
        ))
    if thin_content > 0:
        recs.append(Recommendation(
            priority="media", title="Mejorar contenido delgado",
            description=f"Hay {thin_content} paginas con contenido escaso (pocas palabras o ratio de texto bajo). Amplia el contenido de estas paginas para aportar mas valor.",
            affected_count=thin_content,
            issue_types=["low_word_count", "very_low_text_ratio", "low_text_ratio"],
        ))

    return CategoryInsight(
        key="content", name="Contenido", icon="📝",
        score=_clamp_score(score),
        metrics={
            "total_html": total_html, "pct_title_ok": pct_title_ok, "pct_desc_ok": pct_desc_ok,
            "pct_h1_ok": pct_h1_ok, "avg_word_count": round(avg_wc),
            "pct_thin": pct_thin, "thin_count": thin_content,
            "title_problems": title_problems, "desc_problems": desc_problems,
        },
        recommendations=recs,
    )


def _calc_links(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Links: orphan pages, inlinks distribution, nofollow, broken links."""
    # Huerfana solo puede serlo una pagina 200 (ver analyze_links)
    total_internal = _html_2xx(job_id, db)

    # Orphan pages (0 inlinks)
    orphan_count = db.query(func.count(Issue.id)).filter(
        Issue.job_id == job_id, Issue.issue_type == "orphan_page",
    ).scalar() or 0

    # Average inlinks
    avg_inlinks = float(db.query(func.avg(Url.inlinks_count)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
    ).scalar() or 0)

    # Nofollow internal links
    total_internal_links = db.query(func.count(Link.id)).filter(
        Link.job_id == job_id, Link.is_internal == True,
    ).scalar() or 0
    nofollow_links = db.query(func.count(Link.id)).filter(
        Link.job_id == job_id, Link.is_internal == True, Link.follow == False,
    ).scalar() or 0

    # Broken links (4xx/5xx issue counts)
    broken_link_issues = db.query(func.count(Issue.id)).filter(
        Issue.job_id == job_id, Issue.issue_type.in_(["4xx_error", "5xx_error"]),
    ).scalar() or 0

    # High outlinks
    high_outlinks = db.query(func.count(Issue.id)).filter(
        Issue.job_id == job_id, Issue.issue_type == "high_outlink_count",
    ).scalar() or 0

    pct_orphan = _safe_pct(orphan_count, total_internal)
    pct_nofollow = _safe_pct(nofollow_links, total_internal_links)

    score = (
        max(0, 100 - pct_orphan * 3) * 0.35
        + min(100, avg_inlinks * 10) * 0.25
        + max(0, 100 - pct_nofollow * 2) * 0.15
        + max(0, 100 - _safe_pct(broken_link_issues, total_internal) * 5) * 0.25
    )

    recs: list[Recommendation] = []
    if orphan_count > 0:
        recs.append(Recommendation(
            priority="alta", title="Enlazar paginas huerfanas",
            description=f"Hay {orphan_count} paginas sin enlaces internos que apunten a ellas. Agrega enlaces desde paginas relevantes para que los buscadores las descubran.",
            affected_count=orphan_count,
            issue_types=["orphan_page"],
        ))
    if broken_link_issues > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir enlaces rotos",
            description=f"Se detectaron {broken_link_issues} paginas con errores HTTP. Corrige o elimina los enlaces que apuntan a estas paginas.",
            affected_count=broken_link_issues,
            issue_types=["4xx_error", "5xx_error"],
        ))
    if high_outlinks > 0:
        recs.append(Recommendation(
            priority="baja", title="Reducir enlaces salientes excesivos",
            description=f"Hay {high_outlinks} paginas con demasiados enlaces salientes, lo que puede diluir la autoridad de enlace.",
            affected_count=high_outlinks,
            issue_types=["high_outlink_count"],
        ))

    return CategoryInsight(
        key="links", name="Enlaces", icon="🔗",
        score=_clamp_score(score),
        metrics={
            "total_internal_pages": total_internal, "orphan_count": orphan_count,
            "pct_orphan": pct_orphan, "avg_inlinks": round(avg_inlinks, 1),
            "total_internal_links": total_internal_links,
            "nofollow_links": nofollow_links, "pct_nofollow": pct_nofollow,
            "broken_link_issues": broken_link_issues, "high_outlinks": high_outlinks,
        },
        recommendations=recs,
    )


def _calc_security(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Security: HTTPS, HSTS, CSP, mixed content."""
    # Count security header stats across all internal HTML pages
    total_with_sec = db.query(func.count(SecurityHeaders.url_id)).join(
        Url, SecurityHeaders.url_id == Url.id,
    ).filter(Url.job_id == job_id, Url.is_internal == True).scalar() or 0

    if total_with_sec == 0:
        return CategoryInsight(
            key="security", name="Seguridad", icon="🔒", score=0,
            metrics={"total_checked": 0, "pct_https": 0, "pct_hsts": 0, "pct_csp": 0, "pct_mixed": 0},
            recommendations=[Recommendation(
                priority="baja", title="Sin datos de seguridad",
                description="No se encontraron cabeceras de seguridad en las paginas rastreadas.",
                affected_count=0,
            )],
        )

    agg = db.query(
        func.sum(case((SecurityHeaders.is_https == True, 1), else_=0)).label("https_count"),
        func.sum(case((SecurityHeaders.has_hsts == True, 1), else_=0)).label("hsts_count"),
        func.sum(case((SecurityHeaders.has_csp == True, 1), else_=0)).label("csp_count"),
        func.sum(case((SecurityHeaders.has_mixed_content == True, 1), else_=0)).label("mixed_count"),
        func.sum(case((SecurityHeaders.has_x_content_type_options == True, 1), else_=0)).label("xcto_count"),
        func.sum(case((SecurityHeaders.has_x_frame_options == True, 1), else_=0)).label("xfo_count"),
    ).join(Url, SecurityHeaders.url_id == Url.id).filter(
        Url.job_id == job_id, Url.is_internal == True,
    ).first()

    https_count = agg.https_count or 0
    hsts_count = agg.hsts_count or 0
    csp_count = agg.csp_count or 0
    mixed_count = agg.mixed_count or 0
    xcto_count = agg.xcto_count or 0
    xfo_count = agg.xfo_count or 0

    pct_https = _safe_pct(https_count, total_with_sec)
    pct_hsts = _safe_pct(hsts_count, total_with_sec)
    pct_csp = _safe_pct(csp_count, total_with_sec)
    pct_mixed = _safe_pct(mixed_count, total_with_sec)

    score = pct_https * 0.35 + pct_hsts * 0.2 + pct_csp * 0.15 + max(0, 100 - pct_mixed * 5) * 0.15 + _safe_pct(xcto_count, total_with_sec) * 0.075 + _safe_pct(xfo_count, total_with_sec) * 0.075

    recs: list[Recommendation] = []
    non_https = total_with_sec - https_count
    if non_https > 0:
        recs.append(Recommendation(
            priority="alta", title="Migrar a HTTPS",
            description=f"Hay {non_https} paginas servidas sin HTTPS. HTTPS es fundamental para la seguridad y es un factor de posicionamiento.",
            affected_count=non_https,
            issue_types=["http_url"],
        ))
    if mixed_count > 0:
        recs.append(Recommendation(
            priority="alta", title="Eliminar contenido mixto",
            description=f"Hay {mixed_count} paginas con contenido mixto (recursos HTTP en paginas HTTPS). Actualiza todas las referencias a HTTPS.",
            affected_count=mixed_count,
            issue_types=["mixed_content"],
        ))
    non_hsts = total_with_sec - hsts_count
    if non_hsts > 0 and https_count > 0:
        recs.append(Recommendation(
            priority="media", title="Implementar HSTS",
            description=f"Hay {non_hsts} paginas sin la cabecera HSTS. HSTS fuerza conexiones seguras y previene ataques de downgrade.",
            affected_count=non_hsts,
            issue_types=["missing_hsts"],
        ))
    non_csp = total_with_sec - csp_count
    if non_csp > 0:
        recs.append(Recommendation(
            priority="baja", title="Agregar Content-Security-Policy",
            description=f"Hay {non_csp} paginas sin politica CSP. CSP protege contra ataques XSS e inyeccion de datos.",
            affected_count=non_csp,
            issue_types=["missing_csp"],
        ))

    return CategoryInsight(
        key="security", name="Seguridad", icon="🔒",
        score=_clamp_score(score),
        metrics={
            "total_checked": total_with_sec, "https_count": https_count,
            "pct_https": pct_https, "pct_hsts": pct_hsts, "pct_csp": pct_csp,
            "pct_mixed": pct_mixed, "mixed_count": mixed_count,
        },
        recommendations=recs,
    )


def _calc_structured_data(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Structured data: pages with SD, types, validation errors."""
    total_html = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
    ).scalar() or 0

    # Pages that have at least one structured data item
    pages_with_sd = db.query(func.count(func.distinct(StructuredData.url_id))).join(
        Url, StructuredData.url_id == Url.id,
    ).filter(Url.job_id == job_id, Url.is_internal == True).scalar() or 0

    # Schema types used
    type_rows = (
        db.query(StructuredData.schema_type, func.count(StructuredData.id))
        .join(Url, StructuredData.url_id == Url.id)
        .filter(Url.job_id == job_id, StructuredData.schema_type.isnot(None))
        .group_by(StructuredData.schema_type)
        .order_by(func.count(StructuredData.id).desc())
        .limit(10)
        .all()
    )
    types_used = {t: c for t, c in type_rows}

    # Validation errors
    sd_errors = db.query(func.count(StructuredData.id)).join(
        Url, StructuredData.url_id == Url.id,
    ).filter(
        Url.job_id == job_id, StructuredData.validation_status == "error",
    ).scalar() or 0

    total_sd = db.query(func.count(StructuredData.id)).join(
        Url, StructuredData.url_id == Url.id,
    ).filter(Url.job_id == job_id).scalar() or 0

    pct_with_sd = _safe_pct(pages_with_sd, total_html)
    pct_errors = _safe_pct(sd_errors, total_sd)

    score = min(100, pct_with_sd * 1.2) * 0.6 + max(0, 100 - pct_errors * 3) * 0.4

    recs: list[Recommendation] = []
    pages_without = total_html - pages_with_sd
    if pages_without > 0 and pct_with_sd < 50:
        recs.append(Recommendation(
            priority="media", title="Agregar datos estructurados",
            description=f"Hay {pages_without} paginas HTML sin datos estructurados. Agrega Schema.org (JSON-LD) para mejorar la visibilidad en resultados de busqueda.",
            affected_count=pages_without,
        ))
    if sd_errors > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir errores de validacion",
            description=f"Hay {sd_errors} bloques de datos estructurados con errores de validacion. Corrigelos para que los buscadores los interpreten correctamente.",
            affected_count=sd_errors,
            issue_types=["structured_data_error"],
        ))

    return CategoryInsight(
        key="structured_data", name="Datos Estructurados", icon="📊",
        score=_clamp_score(score),
        metrics={
            "total_html": total_html, "pages_with_sd": pages_with_sd,
            "pct_with_sd": pct_with_sd, "types_used": types_used,
            "total_sd_blocks": total_sd, "sd_errors": sd_errors, "pct_errors": pct_errors,
        },
        recommendations=recs,
    )


def _calc_i18n(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Internationalization: hreflang tags, return tags, valid langs."""
    total_hreflang = db.query(func.count(Hreflang.id)).join(
        Url, Hreflang.url_id == Url.id,
    ).filter(Url.job_id == job_id).scalar() or 0

    if total_hreflang == 0:
        return CategoryInsight(
            key="i18n", name="Internacionalizacion", icon="🌐", score=100,
            metrics={"total_hreflang": 0, "languages": [], "pct_return_ok": 0, "pct_lang_valid": 0},
            recommendations=[],
        )

    # Languages detected
    lang_rows = (
        db.query(Hreflang.lang, func.count(Hreflang.id))
        .join(Url, Hreflang.url_id == Url.id)
        .filter(Url.job_id == job_id)
        .group_by(Hreflang.lang)
        .all()
    )
    languages = [lang for lang, _ in lang_rows]

    # Return tags / lang valid. NULL es "sin verificar" (autorreferencia,
    # x-default, destino no rastreado, o un analisis anterior), NO un fallo:
    # antes `missing_return = total - ok` los contaba como "sin retorno", y un
    # cluster de 3 idiomas perfecto con x-default salia al ~50% con una
    # recomendacion de prioridad alta. En un censo sin verificar (Lopesan,
    # 17.228 hreflang con NULL) daba nota 0 y dos recomendaciones falsas.
    def _cuenta(col, valor):
        return db.query(func.count(Hreflang.id)).join(
            Url, Hreflang.url_id == Url.id,
        ).filter(Url.job_id == job_id, col.is_(valor)).scalar() or 0

    return_ok = _cuenta(Hreflang.return_tag_ok, True)
    missing_return = _cuenta(Hreflang.return_tag_ok, False)
    lang_valid = _cuenta(Hreflang.lang_valid, True)
    invalid_lang = _cuenta(Hreflang.lang_valid, False)
    return_unknown = total_hreflang - return_ok - missing_return
    lang_unknown = total_hreflang - lang_valid - invalid_lang

    pct_return_ok = _safe_pct(return_ok, return_ok + missing_return)
    pct_lang_valid = _safe_pct(lang_valid, lang_valid + invalid_lang)

    # La nota solo con lo verificado. Sin nada verificado no hay nota que dar:
    # se deja en 100 (nada que corregir que sepamos) y se avisa abajo.
    partes = []
    if return_ok + missing_return:
        partes.append(pct_return_ok)
    if lang_valid + invalid_lang:
        partes.append(pct_lang_valid)
    score = sum(partes) / len(partes) if partes else 100

    recs: list[Recommendation] = []
    if missing_return > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir etiquetas hreflang sin retorno",
            description=f"Hay {missing_return} etiquetas hreflang cuya pagina de destino no enlaza de vuelta. Cada hreflang debe tener una referencia reciproca.",
            affected_count=missing_return,
            issue_types=["hreflang_missing_return"],
        ))
    if invalid_lang > 0:
        recs.append(Recommendation(
            priority="media", title="Corregir codigos de idioma invalidos",
            description=f"Hay {invalid_lang} etiquetas hreflang con codigos de idioma no validos. Usa codigos ISO 639-1 (ej: es, en, fr).",
            affected_count=invalid_lang,
            issue_types=["hreflang_invalid_lang"],
        ))
    if not partes:
        recs.append(Recommendation(
            priority="baja", title="Hreflang sin verificar",
            description=f"Las {total_hreflang} etiquetas hreflang no se han podido verificar (retorno e idioma). Relanza el analisis del job para comprobarlas.",
            affected_count=total_hreflang,
        ))

    return CategoryInsight(
        key="i18n", name="Internacionalizacion", icon="🌐",
        score=_clamp_score(score),
        metrics={
            "total_hreflang": total_hreflang, "languages": languages,
            "pct_return_ok": pct_return_ok, "pct_lang_valid": pct_lang_valid,
            "return_missing": missing_return, "return_unknown": return_unknown,
            "invalid_lang": invalid_lang, "lang_unknown": lang_unknown,
        },
        recommendations=recs,
    )


@router.get("/insights", response_model=InsightsResponse)
def get_insights(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    categories = [
        _calc_crawlability(job_id, db),
        _calc_content(job_id, db),
        _calc_links(job_id, db),
        _calc_security(job_id, db),
        _calc_structured_data(job_id, db),
        _calc_i18n(job_id, db),
    ]

    # Weighted average: crawlability 25%, content 25%, links 20%, security 15%, SD 10%, i18n 5%
    weights = [0.25, 0.25, 0.20, 0.15, 0.10, 0.05]
    media = _clamp_score(sum(c.score * w for c, w in zip(categories, weights)))
    tope = _tope_por_errores(job_id, db)
    nota = min(media, tope.tope) if tope else media

    return InsightsResponse(
        job_id=job_id,
        overall_score=nota,
        overall_score_sin_tope=media,
        tope=tope if tope and tope.tope < media else None,
        categories=categories,
        generated_at=datetime.now(timezone.utc),
    )


# Topes de la nota global (UX-05, decision 92). Una media ponderada no puede
# decir «Bueno» de un censo con errores abiertos: el de Lopesan sacaba 88 con
# 2.367 paginas canonicalizadas a otro dominio, la mitad del sitio fuera del
# indice. Con cualquier error, como mucho «Mejorable»; si afecta al 10% de las
# URLs internas o mas, como mucho «Critico».
TOPE_CON_ERRORES = 79
TOPE_CON_ERRORES_EXTENDIDOS = 49
UMBRAL_ERRORES_EXTENDIDOS = 10.0


def _tope_por_errores(job_id: uuid.UUID, db: Session) -> TopeDeNota | None:
    paginas = db.query(func.count(func.distinct(Issue.url_id))).filter(
        Issue.job_id == job_id, Issue.severity == "error",
    ).scalar() or 0
    if not paginas:
        return None
    internas = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True,
    ).scalar() or 0
    pct = _safe_pct(paginas, internas)
    if pct >= UMBRAL_ERRORES_EXTENDIDOS:
        return TopeDeNota(
            tope=TOPE_CON_ERRORES_EXTENDIDOS, paginas_con_error=paginas, de=internas, pct=pct,
            motivo=f"El {pct}% de las URLs internas tiene algun error: la nota no puede pasar de {TOPE_CON_ERRORES_EXTENDIDOS}.",
        )
    return TopeDeNota(
        tope=TOPE_CON_ERRORES, paginas_con_error=paginas, de=internas, pct=pct,
        motivo=f"Hay {paginas} URLs con algun error: la nota no puede pasar de {TOPE_CON_ERRORES} mientras sigan abiertos.",
    )
