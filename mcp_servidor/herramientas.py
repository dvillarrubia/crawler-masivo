"""Herramientas de solo lectura sobre la API del crawler (Ola 6.5, decision 148).

Funciones normales que llaman a la API con el token (decision 134) y devuelven
lo justo para que un asistente (Claude Code, Cowork) conteste preguntas como
«¿que ha cambiado en Lopesan?» o «¿que arreglo primero?» sin abrir la
interfaz. Ninguna escribe nada: lanzar, borrar o cambiar no esta aqui.

El cliente HTTP se inyecta (`Api`), asi que se prueban sin red. El servidor
MCP (`servidor.py`) solo las registra.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

URL_POR_DEFECTO = "https://crawler-masivo.srv817047.hstgr.cloud"


def _token() -> str:
    t = os.getenv("CRAWLER_API_TOKEN")
    if t:
        return t.strip()
    ruta = Path(os.getenv("CRAWLER_API_TOKEN_FILE", "~/.config/crawler-masivo/api_token")).expanduser()
    return ruta.read_text().strip()


class Api:
    def __init__(self, base: str | None = None, token: str | None = None,
                 transporte: httpx.BaseTransport | None = None):
        self.base = (base or os.getenv("CRAWLER_API_URL") or URL_POR_DEFECTO).rstrip("/")
        self._c = httpx.Client(base_url=self.base, transport=transporte, timeout=120,
                               headers={"Authorization": f"Bearer {token or _token()}"})

    def get(self, ruta: str, **params) -> Any:
        r = self._c.get("/api" + ruta, params={k: v for k, v in params.items() if v not in (None, "")})
        if r.status_code == 404:
            raise ValueError(f"No existe: {ruta}")
        r.raise_for_status()
        return r.json()


def _cliente_id(api: Api, cliente: str) -> str:
    """Acepta el id, el slug o el nombre (sin distinguir mayusculas)."""
    clientes = api.get("/clients")
    c = cliente.strip().lower()
    for x in clientes:
        if c in (str(x["id"]).lower(), x["slug"].lower(), x["nombre"].lower()):
            return x["id"]
    raise ValueError(f"No hay ningún cliente «{cliente}». Hay: " + ", ".join(x["slug"] for x in clientes))


def clientes(api: Api) -> list[dict]:
    """Cada cliente con su último censo, tendencia de errores y alertas abiertas."""
    panel = api.get("/clients/panel")
    salida = []
    for c in panel["clientes"]:
        u = c.get("ultimo") or {}
        salida.append({
            "cliente": c["nombre"], "slug": c["slug"], "rastreos": c.get("n_rastreos"),
            "ultimo_censo": {"id": u.get("id"), "nombre": u.get("name"), "fecha": u.get("created_at"),
                             "paginas_html": u.get("paginas_html"), "indexables": u.get("indexables"),
                             "errores": u.get("errores"), "alertas": u.get("alertas")} if u else None,
            "tendencia_errores": (c.get("evolucion") or {}).get("tendencia"),
            "alertas_abiertas": c.get("incidentes_vivos", 0),
        })
    return salida


def rastreos(api: Api, cliente: str | None = None, limite: int = 10) -> list[dict]:
    params = {"page_size": max(1, min(50, limite))}
    if cliente:
        params["client_id"] = _cliente_id(api, cliente)
    return [{"id": j["id"], "nombre": j["name"], "estado": j["status"], "motivo": j.get("finish_reason"),
             "urls": j.get("total_urls_crawled"), "creado": j.get("created_at"),
             "terminado": j.get("completed_at")} for j in api.get("/jobs", **params)["items"]]


def _ultimo_censo(api: Api, cliente: str) -> str:
    for c in clientes(api):
        if cliente.strip().lower() in (c["slug"].lower(), c["cliente"].lower()) and c["ultimo_censo"]:
            return c["ultimo_censo"]["id"]
    raise ValueError(f"«{cliente}» no tiene ningún censo completado")


def informe(api: Api, rastreo: str | None = None, cliente: str | None = None) -> dict:
    """El informe de un censo (nota, cifras, cambios, qué arreglar, tráfico de
    Google, buscadores de IA y fiabilidad). Sin `rastreo`, el último censo del
    cliente."""
    jid = rastreo or _ultimo_censo(api, cliente or "")
    inf = api.get(f"/jobs/{jid}/informe")
    for p in inf.get("prioridades") or []:
        for k in ("por_segmento", "urls_denominador", "pct_paginas"):
            p.pop(k, None)
    return inf


def problemas(api: Api, rastreo: str, orden: str = "prioridad", gravedad: str | None = None,
              limite: int = 30) -> list[dict]:
    """Los tipos de incidencia del censo, por prioridad (gravedad x importancia
    de las paginas) o por gravedad, con cuantas paginas y un ejemplo."""
    filas = api.get(f"/jobs/{rastreo}/issues/resumen", orden=orden, ejemplos=2)
    if gravedad:
        filas = [f for f in filas if f["severity"] == gravedad]
    return [{"tipo": f["issue_type"], "gravedad": f["severity"], "paginas": f["paginas"],
             "incidencias": f["incidencias"], "prioridad": f.get("prioridad"),
             "ejemplos": [e["url"] for e in f.get("ejemplos") or []]} for f in filas[:max(1, limite)]]


def urls(api: Api, rastreo: str, filtros: dict | None = None, orden: str | None = None,
         descendente: bool = True, limite: int = 50) -> dict:
    """URLs del censo con los filtros del Explorador (`status_group`,
    `issue_type`, `indexable`, `<columna>_gte`...), ordenadas por una columna."""
    params = dict(filtros or {})
    params.update(page_size=max(1, min(200, limite)), ligero="true")
    if orden:
        params.update(sort_by=orden, sort_dir="desc" if descendente else "asc")
    r = api.get(f"/jobs/{rastreo}/urls", **params)
    campos = ("url", "status_code", "indexable", "indexability_status", "title", "inlinks_count",
              "pagerank_score", "click_depth", "gsc_clics", "gsc_impresiones", "gsc_posicion")
    return {"total": r.get("total"), "urls": [{k: u.get(k) for k in campos if k in u} for u in r["items"]]}


def comparar(api: Api, rastreo: str, con: str) -> dict:
    """Qué ha cambiado entre dos censos del mismo sitio: alertas y cifras."""
    d = api.get(f"/jobs/{rastreo}/diff/{con}")
    return {k: d.get(k) for k in ("comparable", "concluyente", "motivo", "alertas", "resumen",
                                  "avisos", "incidencias") if k in d}


def alertas(api: Api, cliente: str, cerradas: bool = False) -> list[dict]:
    """Las alertas con estado del cliente (abiertas o, con `cerradas`, las ya resueltas)."""
    cid = _cliente_id(api, cliente)
    return [{"titulo": i["titulo"], "estado": i["estado"], "gravedad": i["severidad"],
             "serie": i.get("nombre_serie"), "desde": i.get("abierto_en"), "veces": i.get("veces"),
             "cerrada": i.get("cerrado_en")}
            for i in api.get(f"/clients/{cid}/incidentes", estado="cerrados" if cerradas else "vivos")]


def search_console(api: Api, cliente: str) -> list[dict]:
    """Estado de Search Console del cliente: propiedad, días guardados, últimos
    28 días e inspección de URLs."""
    return api.get(f"/clients/{_cliente_id(api, cliente)}/gsc")
