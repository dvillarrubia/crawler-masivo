"""Search Console por busqueda y los sitemaps que conoce Google (decision
153, #166).

La descarga de `shared/gsc.py` es por pagina y dia. Las oportunidades piden
mas: que busquedas trae cada pagina (canibalizacion, fuentes que posicionan
por lo mismo, la curva de CTR por posicion del propio sitio), y si Google lee
los sitemaps. Por dispositivo NO: la curva por pagina la deforman los enlaces
de sitio (en Lopesan, CTR mediano del 0,1 % en posicion 2 en escritorio) y sin
la busqueda no se pueden quitar las de marca. Va aparte para no tocar la serie de paginas, y con su propia
serie por tipo: la primera vez se rellena hacia atras los mismos dias
iniciales, aunque las paginas ya esten bajadas.

- por dia, `dimensions=[page, query]`, con el mismo tope de
  50.000 filas y el mismo solape de tres dias que las paginas;
- un dia se reescribe entero en su transaccion;
- los sitemaps, una vez por vuelta: los que Google ya no tiene se borran.

Se lanza en el mismo hilo del worker, despues de la descarga de paginas, una
vez al dia. Nunca lanza: el error queda en `gsc_estado.detalle.motivo`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from shared import gsc as G

logger = logging.getLogger(__name__)



def dias_guardados() -> int:
    """Cuanto se guardan las busquedas. Pesan: ~6 MB al dia
    en Lopesan (558 MB los 90 dias). El analisis usa 90 dias hasta el censo;
    180 dejan re-analizar los censos del trimestre anterior."""
    import os

    try:
        return max(90, int(os.getenv("SEARCH_CONSOLE_DETALLE_DIAS", "180")))
    except ValueError:
        return 180


SERIES = {
    # serie: (dimensiones, columna de la segunda dimension)
    "consultas": (["page", "query"], "consulta"),
}


def toca(estado, ahora: datetime) -> bool:
    """Una vez al dia, despues de una descarga de paginas que fue bien."""
    if estado is None or estado.estado != "ok":
        return False
    detalle = estado.detalle or {}
    intento = _fecha(detalle.get("intento"))
    if detalle.get("motivo") and intento and ahora - intento < G.REINTENTAR_ERROR:
        return False
    ultimo = detalle.get("ultimo")
    if not ultimo:
        return True
    try:
        return datetime.fromisoformat(ultimo).date() < ahora.date()
    except ValueError:
        return True


def descargar_dia(consultar: Callable[[str, dict], dict], propiedad: str, dia: date,
                  dimensiones: list[str]) -> tuple[list[dict], bool]:
    filas: list[dict] = []
    inicio = 0
    while True:
        resp = consultar(propiedad, {
            "startDate": dia.isoformat(), "endDate": dia.isoformat(),
            "dimensions": dimensiones, "type": "web", "dataState": "final",
            "rowLimit": G.FILAS_POR_PAGINA, "startRow": inicio,
        })
        lote = resp.get("rows") or []
        filas.extend(lote)
        if len(lote) < G.FILAS_POR_PAGINA:
            return filas, False
        inicio += G.FILAS_POR_PAGINA
        if inicio >= G.TOPE_FILAS_DIA:
            return filas, True


def _agrupar(filas: list[dict]) -> dict[tuple[str, str], dict[str, Any]]:
    """Por (url_clave, segunda dimension), sumando las URLs que `clave_url`
    iguala, como en las paginas."""
    vistos: dict[tuple[str, str], dict[str, Any]] = {}
    for f in filas:
        claves = f.get("keys") or []
        if len(claves) < 2 or not claves[0] or claves[1] is None:
            continue
        k = (G.clave_de_url(claves[0]), str(claves[1]))
        v = vistos.setdefault(k, {"clics": 0, "impresiones": 0, "pos_x_imp": 0.0})
        imp = int(f.get("impressions") or 0)
        v["clics"] += int(f.get("clicks") or 0)
        v["impresiones"] += imp
        v["pos_x_imp"] += float(f.get("position") or 0) * imp
    return vistos


def _fecha(texto: str | None) -> datetime | None:
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sitemaps(db, cliente_id, propiedad: str, consultor, ahora: datetime) -> int:
    from shared.models import GscSitemap

    lista = consultor.sitemaps(propiedad) if hasattr(consultor, "sitemaps") else []
    vistos = set()
    for sm in lista:
        path = str(sm.get("path") or "").strip()
        if not path or path in vistos:
            continue
        vistos.add(path)
        fila = (db.query(GscSitemap)
                .filter(GscSitemap.cliente_id == cliente_id, GscSitemap.propiedad == propiedad,
                        GscSitemap.path == path).one_or_none())
        if fila is None:
            fila = GscSitemap(cliente_id=cliente_id, propiedad=propiedad, path=path)
            db.add(fila)
        fila.enviado = _fecha(sm.get("lastSubmitted"))
        fila.descargado = _fecha(sm.get("lastDownloaded"))
        fila.pendiente = sm.get("isPending")
        fila.es_indice = sm.get("isSitemapsIndex")
        fila.avisos = int(sm.get("warnings") or 0)
        fila.errores = int(sm.get("errors") or 0)
        fila.enviadas = [{"type": c.get("type"), "submitted": int(c.get("submitted") or 0)}
                         for c in (sm.get("contents") or [])]
        fila.visto_en = ahora
    q = db.query(GscSitemap).filter(GscSitemap.cliente_id == cliente_id, GscSitemap.propiedad == propiedad)
    if vistos:
        q = q.filter(GscSitemap.path.notin_(vistos))
    q.delete(synchronize_session=False)
    db.commit()
    return len(vistos)


def sincronizar(db, cliente, integracion: dict, *, hoy: date | None = None,
                ahora: datetime | None = None, consultor: Callable[[dict], Any] | None = None):
    """Rellena lo que falta de cada serie y los sitemaps. Devuelve el estado."""
    from shared.credenciales import CredencialNoDisponible, credencial
    from shared.models import GscConsulta

    modelos = {"consultas": GscConsulta}
    ahora = ahora or datetime.now(timezone.utc)
    hoy = hoy or ahora.date()
    propiedad = str(((integracion or {}).get("parametros") or {}).get("propiedad") or "").strip()
    if not propiedad:
        return None
    e = G._estado(db, cliente.id, propiedad)
    detalle = dict(e.detalle or {})
    detalle["intento"] = ahora.isoformat()
    cuenta = None
    try:
        info = G.leer_llave(credencial(integracion))
        cuenta = info.get("client_email")
        c = consultor(info) if consultor else G.Consultor(info)
        hasta = hoy - timedelta(days=G.RETRASO_DIAS)
        for serie, (dimensiones, columna) in SERIES.items():
            st = dict(detalle.get(serie) or {})
            ultimo = date.fromisoformat(st["ultimo_dia"]) if st.get("ultimo_dia") else None
            desde = (ultimo - timedelta(days=G.SOLAPE_DIAS) if ultimo
                     else hasta - timedelta(days=G.dias_iniciales() - 1))
            truncados = set(st.get("truncados") or [])
            modelo = modelos[serie]
            dia = desde
            while dia <= hasta:
                filas, truncado = descargar_dia(c.consultar, propiedad, dia, dimensiones)
                (db.query(modelo).filter(modelo.cliente_id == cliente.id, modelo.propiedad == propiedad,
                                         modelo.fecha == dia).delete(synchronize_session=False))
                db.bulk_insert_mappings(modelo, [
                    {"cliente_id": cliente.id, "propiedad": propiedad, "fecha": dia, "url_clave": clave,
                     columna: segunda, "clics": v["clics"], "impresiones": v["impresiones"],
                     "posicion": round(v["pos_x_imp"] / v["impresiones"], 2) if v["impresiones"] else None}
                    for (clave, segunda), v in _agrupar(filas).items()])
                (truncados.add if truncado else truncados.discard)(dia.isoformat())
                st["ultimo_dia"] = max(st.get("ultimo_dia") or dia.isoformat(), dia.isoformat())
                st["primer_dia"] = min(st.get("primer_dia") or dia.isoformat(), dia.isoformat())
                st["truncados"] = sorted(truncados)
                detalle[serie] = st
                e.detalle = dict(detalle)
                db.commit()
                dia += timedelta(days=1)
        corte = hoy - timedelta(days=dias_guardados())
        for serie, modelo in modelos.items():
            (db.query(modelo).filter(modelo.cliente_id == cliente.id, modelo.propiedad == propiedad,
                                     modelo.fecha < corte).delete(synchronize_session=False))
            st = detalle.get(serie) or {}
            if st.get("primer_dia") and st["primer_dia"] < corte.isoformat():
                detalle[serie] = {**st, "primer_dia": corte.isoformat(),
                                  "truncados": [d for d in st.get("truncados") or [] if d >= corte.isoformat()]}
        db.commit()
        detalle["sitemaps"] = _sitemaps(db, cliente.id, propiedad, c, ahora)
        detalle["ultimo"], detalle["motivo"] = ahora.isoformat(), None
    except CredencialNoDisponible as exc:
        db.rollback()
        detalle["motivo"] = f"Falta la llave: {exc}"
        detalle["ultimo"] = ahora.isoformat()
    except G.LlaveNoValida as exc:
        db.rollback()
        detalle["motivo"], detalle["ultimo"] = str(exc), ahora.isoformat()
    except G.GscError as exc:
        db.rollback()
        # Sin marcar `ultimo`: se reintenta en la siguiente vuelta. Lo ya bajado
        # se queda, cada dia va en su transaccion.
        detalle["motivo"] = G.traducir(exc.status, str(exc), cuenta, propiedad)
        logger.warning("Search Console (detalle) de %s (%s): %s", getattr(cliente, "slug", "?"),
                       propiedad, detalle["motivo"])
    except Exception as exc:  # noqa: BLE001 -- la base, un dato raro: que no vuelva a pedirlo cada minuto
        db.rollback()
        detalle["motivo"] = f"Error al guardar las búsquedas: {type(exc).__name__}: {str(exc)[:200]}"
        logger.exception("Search Console (detalle) de %s (%s): fallo", getattr(cliente, "slug", "?"), propiedad)
    e = G._estado(db, cliente.id, propiedad)
    e.detalle = dict(detalle)
    db.commit()
    return e
