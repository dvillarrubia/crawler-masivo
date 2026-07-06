"""Lógica de los verbos de negocio del crawler (B1).

Separada del servidor MCP a propósito: aquí solo se depende de httpx, así
la lógica es testeable sin el SDK `mcp`. `server.py` registra estas
funciones como herramientas MCP.

Capa FINA sobre la REST existente (una sola fuente de verdad): no toca la
base de datos ni Redis; llama a la API por HTTP.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

API_URL = os.getenv("CRAWLER_API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("CRAWLER_API_TIMEOUT", "60"))


def _headers() -> dict:
    """Añade la API key del proyecto si está configurada (CRAWLER_API_KEY).
    Sin ella, funciona igual cuando la API tiene la auth desactivada."""
    key = os.getenv("CRAWLER_API_KEY", "").strip()
    return {"X-API-Key": key} if key else {}


# ---------------------------------------------------------------------------
# HTTP helpers — todo error se traduce a un dict legible (nunca excepción cruda)
# ---------------------------------------------------------------------------
def _get(path: str, params: dict | None = None) -> Any:
    try:
        with httpx.Client(timeout=TIMEOUT, headers=_headers()) as c:
            r = c.get(f"{API_URL}{path}",
                      params={k: v for k, v in (params or {}).items() if v is not None})
        if r.status_code >= 400:
            return {"error": f"{r.status_code}: {_detail(r)}"}
        return r.json()
    except httpx.HTTPError as e:
        return {"error": f"No se pudo contactar con la API ({API_URL}): {e}"}


def _post(path: str, body: dict) -> Any:
    try:
        with httpx.Client(timeout=TIMEOUT, headers=_headers()) as c:
            r = c.post(f"{API_URL}{path}", json=body)
        if r.status_code >= 400:
            return {"error": f"{r.status_code}: {_detail(r)}"}
        return r.json()
    except httpx.HTTPError as e:
        return {"error": f"No se pudo contactar con la API ({API_URL}): {e}"}


def _patch(path: str) -> Any:
    try:
        with httpx.Client(timeout=TIMEOUT, headers=_headers()) as c:
            r = c.patch(f"{API_URL}{path}")
        if r.status_code >= 400:
            return {"error": f"{r.status_code}: {_detail(r)}"}
        return r.json() if r.content else {"ok": True}
    except httpx.HTTPError as e:
        return {"error": str(e)}


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
        return d if isinstance(d, str) else str(d)
    except Exception:
        return r.text[:200]


# ---------------------------------------------------------------------------
# Verbos
# ---------------------------------------------------------------------------
def listar_proyectos() -> dict:
    """Lista los proyectos (clientes) que tienen rastreos, con cuántos tiene
    cada uno. Un proyecto agrupa los rastreos de un mismo sitio/cliente."""
    data = _get("/api/jobs", {"page_size": 100})
    if "error" in data:
        return data
    counts: dict[str, int] = {}
    for j in data.get("items", []):
        cid = j.get("client_id") or "(sin proyecto)"
        counts[cid] = counts.get(cid, 0) + 1
    return {"proyectos": [{"proyecto": k, "rastreos": v}
                          for k, v in sorted(counts.items())]}


def listar_rastreos(proyecto: str | None = None, estado: str | None = None,
                    limite: int = 20) -> dict:
    """Lista rastreos (jobs), del más reciente al más antiguo.

    proyecto: filtra por client_id. estado: pending|running|completed|failed|
    cancelled. Devuelve id, nombre, estado, fecha y totales de URLs."""
    data = _get("/api/jobs", {"client_id": proyecto, "status": estado,
                              "page_size": max(1, min(limite, 100))})
    if "error" in data:
        return data
    return {"rastreos": [{
        "job_id": j["id"], "nombre": j["name"], "proyecto": j.get("client_id"),
        "estado": j["status"], "creado": j.get("created_at"),
        "urls_rastreadas": j.get("total_urls_crawled"),
        "urls_fallidas": j.get("total_urls_failed"),
    } for j in data.get("items", [])]}


def lanzar_rastreo(url: str, nombre: str | None = None,
                   proyecto: str | None = None, max_urls: int = 5000,
                   max_prof: int = 5, render_js: bool = False) -> dict:
    """Lanza un rastreo nuevo de un sitio y devuelve su job_id.

    url: semilla (debe empezar por http:// o https://). nombre: etiqueta
    legible (por defecto la del dominio). proyecto: client_id para agrupar.
    max_urls / max_prof: límites del rastreo. render_js: ejecutar JavaScript
    (más lento y pesado; solo si el sitio lo necesita). El rastreo corre en
    segundo plano: usa estado_rastreo(job_id) para seguirlo."""
    if not url.startswith(("http://", "https://")):
        return {"error": "La URL debe empezar por http:// o https://"}
    from urllib.parse import urlparse
    host = urlparse(url).netloc or url
    body = {
        "name": nombre or f"Rastreo {host}",
        "seeds": [url],
        "client_id": proyecto,
        "config": {"max_urls": max_urls, "max_depth": max_prof,
                   "render_js": render_js},
    }
    data = _post("/api/jobs", body)
    if "error" in data:
        return data
    return {"job_id": data.get("id"), "nombre": data.get("name"),
            "estado": data.get("status"),
            "nota": "Rastreo encolado. Sigue el avance con estado_rastreo(job_id)."}


def estado_rastreo(job_id: str) -> dict:
    """Estado y avance en vivo de un rastreo: estado del job y, si está en
    marcha, cuántas URLs lleva rastreadas (desde Redis)."""
    job = _get(f"/api/jobs/{job_id}")
    if "error" in job:
        return job
    prog = _get(f"/api/jobs/{job_id}/progress")
    out = {
        "job_id": job_id, "nombre": job.get("name"), "estado": job.get("status"),
        "creado": job.get("created_at"), "iniciado": job.get("started_at"),
        "completado": job.get("completed_at"),
        "urls_rastreadas": job.get("total_urls_crawled"),
        "urls_fallidas": job.get("total_urls_failed"),
    }
    if isinstance(prog, dict) and "error" not in prog:
        out["progreso_vivo"] = prog
    return out


def resumen_rastreo(job_id: str) -> dict:
    """Resumen ejecutivo de un rastreo completado: totales de URLs, reparto
    por código de estado, incidencias por severidad, hosts principales y
    latencia. La foto de "cómo está el sitio"."""
    s = _get(f"/api/jobs/{job_id}/stats")
    if "error" in s:
        return s
    by_sev: dict[str, int] = {}
    for it in s.get("issues_by_type", []):
        by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + it["count"]
    return {
        "job_id": job_id,
        "urls_totales": s.get("total_urls"),
        "urls_rastreadas": s.get("total_urls_crawled"),
        "urls_fallidas": s.get("total_urls_failed"),
        "internas": s.get("internal_count"), "externas": s.get("external_count"),
        "por_codigo_estado": s.get("urls_by_status_group"),
        "incidencias_por_severidad": by_sev,
        "tipos_de_incidencia_distintos": len(s.get("issues_by_type", [])),
        "hosts_principales": (s.get("top_hosts") or [])[:8],
        "latencia": s.get("latency"),
    }


# Etiquetas breves en castellano para los tipos más comunes (el resto se
# devuelve con su nombre técnico, que el agente ya sabe interpretar).
_LABELS = {
    "4xx_error": "Errores 4xx", "5xx_error": "Errores 5xx",
    "redirect_chain": "Cadenas de redirección", "slow_page": "Páginas lentas",
    "title_missing": "Sin title", "title_duplicate": "Titles duplicados",
    "description_missing": "Sin meta description", "h1_missing": "Sin H1",
    "image_missing_alt": "Imágenes sin alt", "low_word_count": "Poco contenido",
    "duplicate_content": "Contenido duplicado", "orphan_page": "Páginas huérfanas",
    "noindex_page": "Páginas noindex", "canonical_missing": "Sin canonical",
    "structured_data_error": "Datos estructurados con errores",
    "hreflang_missing_return": "Hreflang sin retorno",
    "excessive_click_depth": "Demasiada profundidad de clic",
    "missing_csp": "Sin cabecera CSP", "http_url": "URLs en HTTP",
}


def top_incidencias(job_id: str, limite: int = 15) -> dict:
    """Las incidencias SEO más frecuentes del rastreo, ordenadas por volumen,
    con su severidad. Para saber por dónde empezar a arreglar."""
    s = _get(f"/api/jobs/{job_id}/stats")
    if "error" in s:
        return s
    rows = sorted(s.get("issues_by_type", []), key=lambda x: -x["count"])[:limite]
    return {"incidencias": [{
        "tipo": r["issue_type"],
        "nombre": _LABELS.get(r["issue_type"], r["issue_type"]),
        "severidad": r["severity"], "afectadas": r["count"],
    } for r in rows]}


def buscar_urls(job_id: str, contiene: str | None = None,
                grupo_estado: str | None = None, limite: int = 25) -> dict:
    """Busca URLs del rastreo. contiene: subcadena en la URL o el title.
    grupo_estado: 2xx|3xx|4xx|5xx. Devuelve URL, código, indexabilidad,
    PageRank y palabras."""
    data = _get(f"/api/jobs/{job_id}/urls", {
        "search": contiene, "status_group": grupo_estado,
        "page_size": max(1, min(limite, 100))})
    if "error" in data:
        return data
    return {"total": data.get("total"), "urls": [{
        "url_id": u.get("id"), "url": u.get("url"),
        "codigo": u.get("status_code"), "indexable": u.get("indexable"),
        "pagerank": u.get("pagerank"), "palabras": u.get("word_count"),
    } for u in data.get("items", [])]}


def detalle_url(job_id: str, url_id: int) -> dict:
    """Ficha completa de una URL: metadatos, enlaces, recursos, datos
    estructurados, seguridad e incidencias que tiene. Usa el url_id que
    devuelve buscar_urls."""
    return _get(f"/api/jobs/{job_id}/urls/{url_id}")


def preguntar_a_los_datos(job_id: str, pregunta: str) -> dict:
    """Reúne un DOSSIER compacto de un rastreo para responder una pregunta
    sobre él (resumen + top incidencias + páginas más importantes por
    PageRank + resumen de Search Console si hay). Devuelve los datos; la
    respuesta la elabora el propio agente con ellos.

    Nota: es un recolector de contexto, no un motor de respuestas — por eso
    no inventa nada: solo trae lo que hay en el rastreo."""
    resumen = resumen_rastreo(job_id)
    if "error" in resumen:
        return resumen
    top = top_incidencias(job_id, 12)
    top_pages = _get(f"/api/jobs/{job_id}/urls",
                     {"sort_by": "pagerank", "sort_dir": "desc",
                      "is_internal": "true", "page_size": 10})
    paginas = []
    if isinstance(top_pages, dict) and "error" not in top_pages:
        paginas = [{"url": u.get("url"), "pagerank": u.get("pagerank"),
                    "codigo": u.get("status_code"),
                    "clics_gsc": u.get("gsc_clicks")}
                   for u in top_pages.get("items", [])]
    gsc = None
    sem = _get(f"/api/jobs/{job_id}/semantic/results")
    if isinstance(sem, dict) and "error" not in sem:
        gsc = sem.get("gsc_summary")
    return {
        "pregunta": pregunta,
        "instruccion": ("Responde la pregunta del usuario usando SOLO estos "
                        "datos del rastreo; si algo no está, dilo."),
        "resumen": resumen,
        "top_incidencias": top.get("incidencias"),
        "paginas_mas_importantes": paginas,
        "search_console": gsc,
    }


def cancelar_rastreo(job_id: str) -> dict:
    """Cancela un rastreo en curso."""
    return _patch(f"/api/jobs/{job_id}/cancel")


# Lista de verbos expuestos (la usa server.py para registrarlos en MCP).
VERBS = [
    listar_proyectos, listar_rastreos, lanzar_rastreo, estado_rastreo,
    resumen_rastreo, top_incidencias, buscar_urls, detalle_url,
    preguntar_a_los_datos, cancelar_rastreo,
]
