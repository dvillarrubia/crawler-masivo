"""El trafico de Google entre dos censos, agrupado por lo que cambio en esas
paginas (decision 158, #167).

La comparacion de censos (decisiones 67-93) dice que cambio en cada pagina.
Con Search Console (decision 141) se puede decir ademas que paso con su
trafico: «han caido un 60 % los clics de 40 fichas, y en las 40 cambio el
canonical entre el rastreo del 3 y el del 10». Reglas:

- **Dos ventanas iguales** que acaban en la fecha de cada censo menos el
  retraso de los datos (2 dias), tan largas como los dias entre los dos
  censos, de 7 a 28 (en semanas enteras, para que el dia de la semana no
  sesgue): con 28 fijos, dos censos semanales compartian 21 dias de ventana y
  la caida se diluia. Con menos de 7 dias entre medias no se compara. Si los datos aun no llegan al final
  de la ventana del nuevo, la comparacion ESPERA (`pendiente`) y la completa
  el worker cuando llegan: nunca con dias incompletos. Con un dia recortado en
  el tope de filas en cualquiera de las dos, no se compara.
- **Caida** de una pagina: al menos el 50 % y 20 clics perdidos; **subida**, lo
  mismo hacia arriba (la otra mitad del seguimiento: «+X clics donde se arreglo
  el canonical»).
- **Se agrupan por lo que cambio en esa URL** entre los dos censos: status,
  redireccion o error nuevos, noindex, canonical, title, H1, contenido, sale
  del sitemap, pierde enlaces o PageRank, desaparece del censo. Una pagina con
  dos cambios esta en los dos grupos. Lo que no cambio en nada va a «sin
  cambio tecnico en el rastreo».
- **El trafico que se MUEVE no se pierde**: si la pagina que cae esta
  canonicalizada a otra URL, o es la misma con o sin barra final, y esa otra
  sube, es un trasvase. Medido en Lopesan (14 al 21-oct): las fichas con barra
  final, canonicalizadas a la de sin barra desde antes, bajaron (92 a 4 clics)
  mientras subia la buena (38 a 121): sin esto salia «8 paginas perdieron 290
  clics sin cambio tecnico», cuando era Google haciendo caso al canonical.
- **Nunca se afirma causalidad**: «coincide con», no «por». Si la caida es de
  todo el sitio, se dice que es general. Si hay datos del ano anterior y el
  mismo periodo cayo lo mismo, se dice que es estacional.
- **Rupturas de serie conocidas** de Search Console: si una ventana las cruza,
  se avisa (afectan a las impresiones; los clics, que son lo que se compara,
  no).

Los textos se redactan AQUI (`textos`), una sola vez: los usan la ficha del
rastreo y el aviso de Slack, para que digan lo mismo.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

VENTANA_DIAS = 28
VENTANA_MIN_DIAS = 7
CAIDA_MIN_PARTE = 0.5
CAIDA_MIN_CLICS = 20
# Cambio del total del sitio a partir del que una caida es «general».
GENERAL_PARTE = 0.3
# El mismo periodo del ano anterior «cayo lo mismo» si la diferencia entre las
# dos variaciones no pasa de esto (en tanto por uno).
ESTACIONAL_MARGEN = 0.15
MAX_EJEMPLOS = 5
# Cambio de palabras que cuenta como «cambio el contenido».
CONTENIDO_PARTE = 0.3
CONTENIDO_MIN_PALABRAS = 50
PAGERANK_MIN_PUNTOS = 10

# Rupturas de serie de Search Console (docs/INVESTIGACION_SEARCH_CONSOLE.md).
RUPTURAS = (
    {"fecha": "2025-09-12", "metrica": "impresiones",
     "texto": "Google dejó de servir 100 resultados por página (num=100): cayeron las impresiones de "
              "escritorio y subió la posición media sin que el sitio cambiara."},
    {"fecha": "2026-04-28", "metrica": "impresiones",
     "texto": "Google corrigió el error que inflaba las impresiones desde mayo de 2025: la caída de "
              "impresiones de ese día no es real."},
)

CAMBIOS = {
    "desaparece": "desaparece del rastreo",
    "redireccion_nueva": "ahora redirige",
    "error_nuevo": "ahora da error",
    "status": "cambia el código de respuesta",
    "noindex": "cambia la indexabilidad",
    "canonical": "cambia el canonical",
    "title": "cambia el title",
    "h1": "cambia el H1",
    "contenido": "cambia mucho el contenido",
    "sale_del_sitemap": "sale del sitemap",
    "pierde_enlaces": "pierde enlaces internos",
    "pierde_pagerank": "pierde PageRank interno",
    "trasvase": "el tráfico pasa a su canonical o a la misma URL con o sin barra final, que sube",
    "recibe_trasvase": "recibe el tráfico de su variante canonicalizada o con o sin barra final",
    "sin_cambio": "sin cambio técnico en el rastreo",
}


def dia_del_censo(job) -> date:
    t = getattr(job, "started_at", None) or getattr(job, "created_at", None) or datetime.now(timezone.utc)
    return t.date()


def dias_de_ventana(job_a, job_b) -> int:
    """Los dias de cada ventana: los que hay entre los dos censos, en semanas
    enteras, de 7 a 28. 0 si estan a menos de 7."""
    entre = (dia_del_censo(job_b) - dia_del_censo(job_a)).days
    if entre < VENTANA_MIN_DIAS:
        return 0
    return min(VENTANA_DIAS, entre - entre % 7)


def ventana(job, dias: int = VENTANA_DIAS, retraso: int | None = None) -> tuple[date, date]:
    from shared.gsc import RETRASO_DIAS

    fin = dia_del_censo(job) - timedelta(days=RETRASO_DIAS if retraso is None else retraso)
    return fin - timedelta(days=dias - 1), fin


def _clics(db, cliente_id, desde: date, hasta: date) -> dict[str, int]:
    from sqlalchemy import func, select

    from shared.models import GscPagina as P

    return {k: int(c or 0) for k, c in db.execute(
        select(P.url_clave, func.sum(P.clics))
        .where(P.cliente_id == cliente_id, P.fecha >= desde, P.fecha <= hasta)
        .group_by(P.url_clave))}


def _norm_texto(v) -> str:
    return " ".join(str(v or "").split()).lower()


def cambios_de_pagina(pa: dict | None, pb: dict | None) -> list[str]:
    """Lo que cambio en una URL entre el censo anterior (pa) y el nuevo (pb)."""
    from shared.comparacion import _canonical_a_otra, _norm, _pierde_inlinks

    if pa is None:
        return []
    if pb is None:
        return ["desaparece"]
    salida = []
    sa, sb = pa.get("status_code"), pb.get("status_code")
    if sa != sb:
        if sb is not None and 300 <= sb < 400:
            salida.append("redireccion_nueva")
        elif sb is not None and sb >= 400:
            salida.append("error_nuevo")
        else:
            salida.append("status")
    if (pa.get("indexability_status") or "") != (pb.get("indexability_status") or "") and "status" not in salida \
            and "redireccion_nueva" not in salida and "error_nuevo" not in salida:
        salida.append("noindex")
    # Autocanonica y sin canonical son lo mismo para Google: solo cuenta a donde
    # apunta si es OTRA URL.
    ca, cb = _canonical_a_otra(pa), _canonical_a_otra(pb)
    if (_norm(ca) if ca else None) != (_norm(cb) if cb else None):
        salida.append("canonical")
    if _norm_texto(pa.get("title")) != _norm_texto(pb.get("title")):
        salida.append("title")
    if _norm_texto(pa.get("h1")) != _norm_texto(pb.get("h1")):
        salida.append("h1")
    wa, wb = pa.get("word_count"), pb.get("word_count")
    if wa and wb is not None and abs(wb - wa) >= CONTENIDO_MIN_PALABRAS and abs(wb - wa) / wa >= CONTENIDO_PARTE:
        salida.append("contenido")
    if pa.get("in_sitemap") is True and pb.get("in_sitemap") is False:
        salida.append("sale_del_sitemap")
    if _pierde_inlinks(pa.get("inlinks"), pb.get("inlinks")):
        salida.append("pierde_enlaces")
    ra, rb = pa.get("pagerank_score"), pb.get("pagerank_score")
    if ra is not None and rb is not None and ra - rb >= PAGERANK_MIN_PUNTOS:
        salida.append("pierde_pagerank")
    return salida


def _destinos(pagina: dict | None) -> list[str]:
    """Las claves de URL a las que se consolidaria el trafico de una pagina: su
    canonical si es otra URL, y la misma ruta con o sin barra final."""
    from shared.comparacion import _canonical_a_otra
    from shared.gsc import clave_de_url

    if not pagina or not pagina.get("url"):
        return []
    salida = []
    canon = _canonical_a_otra(pagina)
    if canon:
        salida.append(clave_de_url(canon))
    p = urlsplit(pagina["url"])
    ruta = p.path or "/"
    if ruta != "/":
        otra = ruta[:-1] if ruta.endswith("/") else ruta + "/"
        salida.append(clave_de_url(p._replace(path=otra).geturl()))
    return salida


def _cubre(db, cliente_id, desde: date, hasta: date) -> tuple[bool, str | None, bool]:
    """(cubre, motivo si no, falta solo el final -> pendiente)."""
    from shared.models import GscEstado

    estados = db.query(GscEstado).filter(GscEstado.cliente_id == cliente_id).all()
    if not estados:
        return False, None, False
    primero = min((e.primer_dia for e in estados if e.primer_dia), default=None)
    ultimo = max((e.ultimo_dia for e in estados if e.ultimo_dia), default=None)
    if ultimo is None or ultimo < hasta:
        return False, (f"Search Console aún no tiene datos hasta el {hasta.isoformat()}: el tráfico se "
                       "compara cuando lleguen."), True
    if primero is None or primero > desde:
        return False, (f"Los datos de Search Console empiezan el {primero.isoformat() if primero else '—'}, "
                       f"después del principio de la ventana del rastreo anterior ({desde.isoformat()})."), False
    recortados = sorted(d for e in estados for d in (e.dias_truncados or [])
                        if desde.isoformat() <= d <= hasta.isoformat())
    if recortados:
        return False, (f"Google recortó {len(recortados)} día(s) en el tope de filas dentro de las "
                       "ventanas: sin todas las páginas no se compara el tráfico."), False
    return True, None, False


def comparar_trafico(db, job_a, job_b, paginas_a: list[dict], paginas_b: list[dict]) -> dict[str, Any] | None:
    """El trafico de las paginas entre dos censos. None si el cliente no tiene
    Search Console; con `pendiente` si los datos aun no llegan."""
    from shared.gsc import clave_de_url
    from shared.plantillas import firma_de_ruta

    cliente_id = getattr(job_b, "cliente_id", None)
    if not cliente_id:
        return None
    dias = dias_de_ventana(job_a, job_b)
    if not dias:
        entre = (dia_del_censo(job_b) - dia_del_censo(job_a)).days
        return {"comparable": False, "pendiente": False,
                "motivo": (f"Los dos rastreos están a {entre} día(s): el tráfico de Google se compara con "
                           f"al menos {VENTANA_MIN_DIAS} días entre medias.")}
    ini_a, fin_a = ventana(job_a, dias)
    ini_b, fin_b = ventana(job_b, dias)
    base = {"dias": dias, "ventana_anterior": [ini_a.isoformat(), fin_a.isoformat()],
            "ventana_nueva": [ini_b.isoformat(), fin_b.isoformat()]}
    cubre, motivo, pendiente = _cubre(db, cliente_id, ini_a, fin_b)
    if not cubre:
        if motivo is None:
            return None
        return {**base, "comparable": False, "pendiente": pendiente, "motivo": motivo}

    clics_a = _clics(db, cliente_id, ini_a, fin_a)
    clics_b = _clics(db, cliente_id, ini_b, fin_b)
    total_a, total_b = sum(clics_a.values()), sum(clics_b.values())
    por_clave_a = {clave_de_url(p["url"]): p for p in paginas_a if p.get("url")}
    por_clave_b = {clave_de_url(p["url"]): p for p in paginas_b if p.get("url")}

    grupos: dict[str, dict[str, dict[str, Any]]] = {"caidas": {}, "subidas": {}}
    n = {"caidas": 0, "subidas": 0}
    sentidos: dict[str, str] = {}
    for clave in set(por_clave_a) | set(por_clave_b):
        ca, cb = clics_a.get(clave, 0), clics_b.get(clave, 0)
        dif = cb - ca
        if dif <= -CAIDA_MIN_CLICS and ca and -dif / ca >= CAIDA_MIN_PARTE:
            sentidos[clave] = "caidas"
        elif dif >= CAIDA_MIN_CLICS and (ca == 0 or dif / ca >= CAIDA_MIN_PARTE):
            sentidos[clave] = "subidas"
    # Trasvases: la que cae y la URL a la que se consolida, si esa sube.
    trasvase, recibe = set(), set()
    for clave, sentido in sentidos.items():
        if sentido != "caidas":
            continue
        for destino in _destinos(por_clave_b.get(clave) or por_clave_a.get(clave)):
            if clics_b.get(destino, 0) > clics_a.get(destino, 0):
                trasvase.add(clave)
                recibe.add(destino)
                break
    for clave, sentido in sentidos.items():
        ca, cb = clics_a.get(clave, 0), clics_b.get(clave, 0)
        dif = cb - ca
        n[sentido] += 1
        pa, pb = por_clave_a.get(clave), por_clave_b.get(clave)
        url = (pb or pa)["url"]
        if clave in trasvase:
            cambios = ["trasvase"]
        else:
            cambios = cambios_de_pagina(pa, pb) or (["recibe_trasvase"] if clave in recibe else ["sin_cambio"])
        for cambio in cambios:
            g = grupos[sentido].setdefault(cambio, {"paginas": 0, "clics_antes": 0, "clics_despues": 0,
                                                    "ejemplos": [], "plantillas": defaultdict(int)})
            g["paginas"] += 1
            g["clics_antes"] += ca
            g["clics_despues"] += cb
            g["ejemplos"].append((abs(dif), url, ca, cb))
            g["plantillas"][firma_de_ruta(urlsplit(url).path or "/")] += 1

    def salida(g_sentido: dict) -> list[dict]:
        filas = []
        for cambio, g in g_sentido.items():
            g["ejemplos"].sort(key=lambda e: (-e[0], e[1]))
            filas.append({
                "cambio": cambio, "texto": CAMBIOS[cambio], "paginas": g["paginas"],
                "clics_antes": g["clics_antes"], "clics_despues": g["clics_despues"],
                "diferencia": g["clics_despues"] - g["clics_antes"],
                "ejemplos": [{"url": u, "clics_antes": a, "clics_despues": b} for _, u, a, b in g["ejemplos"][:MAX_EJEMPLOS]],
                "plantillas": [p for p, _ in sorted(g["plantillas"].items(), key=lambda kv: (-kv[1], kv[0]))[:3]],
            })
        # Lo que tiene causa tecnica primero; «sin cambio» al final.
        filas.sort(key=lambda f: (f["cambio"] == "sin_cambio", f["diferencia"] if f["diferencia"] < 0 else -f["diferencia"]))
        return filas

    variacion = (total_b - total_a) / total_a if total_a else None
    r: dict[str, Any] = {
        **base, "comparable": True, "pendiente": False,
        "clics_anterior": total_a, "clics_nuevo": total_b,
        "variacion": round(variacion, 3) if variacion is not None else None,
        "general": bool(variacion is not None and variacion <= -GENERAL_PARTE),
        "paginas_con_caida": n["caidas"], "paginas_con_subida": n["subidas"],
        "caidas": salida(grupos["caidas"]), "subidas": salida(grupos["subidas"]),
        "rupturas": [x for x in RUPTURAS if ini_a.isoformat() <= x["fecha"] <= fin_b.isoformat()],
        "umbral": {"parte": CAIDA_MIN_PARTE, "clics": CAIDA_MIN_CLICS},
    }
    r["estacional"] = _estacional(db, cliente_id, ini_a, fin_a, ini_b, fin_b, variacion)
    r["textos"] = textos(r)
    return r


def _estacional(db, cliente_id, ini_a, fin_a, ini_b, fin_b, variacion) -> dict | None:
    """Lo mismo un ano antes, si hay datos. None si no se puede saber."""
    from shared.models import GscEstado

    if variacion is None:
        return None
    hace_un_ano = timedelta(days=364)  # mismo dia de la semana
    primero = min((e.primer_dia for e in db.query(GscEstado).filter(GscEstado.cliente_id == cliente_id)
                   if e.primer_dia), default=None)
    if primero is None or primero > ini_a - hace_un_ano:
        return None
    a = sum(_clics(db, cliente_id, ini_a - hace_un_ano, fin_a - hace_un_ano).values())
    b = sum(_clics(db, cliente_id, ini_b - hace_un_ano, fin_b - hace_un_ano).values())
    if not a:
        return None
    antes = (b - a) / a
    return {"variacion_ano_anterior": round(antes, 3),
            "igual": abs(antes - variacion) <= ESTACIONAL_MARGEN and (antes < 0) == (variacion < 0)}


def _num(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def _fecha(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m")


def textos(r: dict[str, Any]) -> list[str]:
    """Las frases del trafico entre censos: las mismas en la interfaz y en
    Slack. Nunca dicen «por»: dicen «coincide con»."""
    if not r.get("comparable"):
        return [r["motivo"]] if r.get("motivo") else []
    va, vn = r["ventana_anterior"], r["ventana_nueva"]
    lineas = []
    var = r.get("variacion")
    total = (f"Clics de Google: {_num(r['clics_anterior'])} del {_fecha(va[0])} al {_fecha(va[1])} y "
             f"{_num(r['clics_nuevo'])} del {_fecha(vn[0])} al {_fecha(vn[1])}"
             + (f" ({'+' if var > 0 else ''}{round(100 * var)} %)." if var is not None else "."))
    lineas.append(total)
    est = r.get("estacional") or {}
    if r.get("general"):
        lineas.append("La caída es general en todo el sitio, no de unas páginas concretas"
                      + (f"; el mismo periodo del año anterior cayó un {round(-100 * est['variacion_ano_anterior'])} %: "
                         "parece estacional." if est.get("igual") else
                         ": puede ser demanda, competencia, la página de resultados o un cambio de Google."))
    elif est.get("igual") and var is not None and var < 0:
        lineas.append("El mismo periodo del año anterior varió lo mismo: parece estacional.")
    for g in r.get("caidas") or []:
        if g["cambio"] == "trasvase":
            lineas.append(f"{_num(g['paginas'])} páginas bajaron {_num(-g['diferencia'])} clics que pasan a su "
                          "canonical o a la misma URL con o sin barra final, que sube: el tráfico se mueve, no "
                          "se pierde.")
        elif g["cambio"] == "sin_cambio":
            lineas.append(f"{_num(g['paginas'])} páginas perdieron {_num(-g['diferencia'])} clics sin cambio "
                          "técnico en el rastreo: puede ser demanda, competencia o la página de resultados.")
        else:
            lineas.append(f"{_num(g['paginas'])} páginas perdieron {_num(-g['diferencia'])} clics; coincide con "
                          f"que en ellas {g['texto']} entre los dos rastreos.")
    for g in r.get("subidas") or []:
        if g["cambio"] in ("sin_cambio", "recibe_trasvase"):
            continue
        lineas.append(f"{_num(g['paginas'])} páginas ganaron {_num(g['diferencia'])} clics; coincide con que en "
                      f"ellas {g['texto']}.")
    for x in r.get("rupturas") or []:
        dia = datetime.fromisoformat(x["fecha"]).strftime("%d/%m/%Y")
        lineas.append(f"Ojo, las ventanas cruzan una ruptura de Search Console ({dia}): {x['texto']}")
    return lineas


def completar_pendientes(db, cliente_id, limite: int = 10) -> int:
    """Rehace la comparacion de trafico de los censos que la dejaron
    `pendiente` porque los datos aun no habian llegado. La llama el worker
    despues de cada descarga de Search Console. Nunca lanza."""
    try:
        from sqlalchemy.orm.attributes import flag_modified

        from shared.comparacion import paginas_de_censo
        from shared.models import Job

        hechos = 0
        jobs = (db.query(Job).filter(Job.cliente_id == cliente_id, Job.comparacion.isnot(None))
                .order_by(Job.created_at.desc()).limit(limite).all())
        for job in jobs:
            comp = job.comparacion if isinstance(job.comparacion, dict) else {}
            t = comp.get("trafico")
            if not (isinstance(t, dict) and t.get("pendiente")) or not comp.get("comparado_con"):
                continue
            import uuid

            anterior = db.get(Job, uuid.UUID(str(comp["comparado_con"])))
            if anterior is None:
                continue
            nuevo = comparar_trafico(db, anterior, job, paginas_de_censo(db, anterior.id),
                                     paginas_de_censo(db, job.id))
            if nuevo is None or nuevo.get("pendiente"):
                continue
            job.comparacion = {**comp, "trafico": nuevo}
            flag_modified(job, "comparacion")
            db.commit()
            hechos += 1
        return hechos
    except Exception:
        db.rollback()
        logger.exception("No se pudo completar el trafico pendiente del cliente %s", cliente_id)
        return 0
