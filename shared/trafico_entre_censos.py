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
  redireccion o error nuevos, noindex, canonical, title, H1, contenido (por
  `content_hash` si lo tienen las dos, y si no por palabras: decision 165), sale
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
  todo el sitio, se dice primero que es general, y los grupos van detras como
  detalle, no como causa (decision 165). Si hay datos del ano anterior y el
  mismo periodo cayo lo mismo, se dice que es estacional.
- **Rupturas de serie conocidas** de Search Console: si una ventana las cruza,
  se avisa (afectan a las impresiones; los clics, que son lo que se compara,
  no).

Los textos se redactan AQUI (`textos`), una sola vez: los usan la ficha del
rastreo, la del cliente y el aviso de Slack, para que digan lo mismo. Si la
comparacion espero a los datos, al completarse se avisa otra vez, una sola
(decision 165).
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
# El destino de un trasvase tiene que ganar al menos esta parte de lo que pierde
# la pagina que cae.
TRASVASE_MIN_PARTE = 0.5

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
    "texto_contenido": "cambia el texto del contenido principal",
    "sale_del_sitemap": "sale del sitemap",
    "pierde_enlaces": "pierde enlaces internos",
    "pierde_pagerank": "pierde PageRank interno",
    "trasvase": "el tráfico pasa a su canonical o a la misma URL con o sin barra final, que sube",
    "recibe_trasvase": "recibe el tráfico de su variante canonicalizada o con o sin barra final",
    "sin_dato_anterior": "no estaban en el rastreo anterior, así que no hay con qué comparar",
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


def propiedades_activas(db, cliente_id) -> list[str]:
    """Las propiedades de Search Console que el cliente tiene ACTIVAS: si paso de
    una de prefijo a `sc-domain:`, la vieja sigue en la base y contaria doble."""
    from shared.models import Client, GscEstado

    cliente = db.get(Client, cliente_id)
    activas = {str((i.get("parametros") or {}).get("propiedad") or "").strip()
               for i in (getattr(cliente, "integraciones", None) or [])
               if isinstance(i, dict) and i.get("tipo") == "gsc" and i.get("activa", True)}
    guardadas = {e.propiedad for e in db.query(GscEstado).filter(GscEstado.cliente_id == cliente_id)}
    return sorted(guardadas & activas if activas else guardadas)


def _clics(db, cliente_id, props: list[str], desde: date, hasta: date) -> dict[str, tuple[str, int]]:
    """{clave: (una URL, clics)} en la ventana."""
    from sqlalchemy import func, select

    from shared.models import GscPagina as P

    return {k: (u, int(c or 0)) for k, u, c in db.execute(
        select(P.url_clave, func.min(P.url), func.sum(P.clics))
        .where(P.cliente_id == cliente_id, P.propiedad.in_(props), P.fecha >= desde, P.fecha <= hasta)
        .group_by(P.url_clave))}


def _dias_con_datos(db, cliente_id, propiedad: str, desde: date, hasta: date) -> set[date]:
    from sqlalchemy import select

    from shared.models import GscPagina as P

    return {d for (d,) in db.execute(
        select(P.fecha).where(P.cliente_id == cliente_id, P.propiedad == propiedad,
                              P.fecha >= desde, P.fecha <= hasta).distinct())}


def _norm_texto(v) -> str:
    return " ".join(str(v or "").split()).lower()


def cambios_de_pagina(pa: dict | None, pb: dict | None, *, usar_hash: bool = False) -> list[str]:
    """Lo que cambio en una URL entre el censo anterior (pa) y el nuevo (pb).

    `usar_hash`: si el `content_hash` de los dos censos se puede comparar
    (`hash_comparable`); por defecto no, y el contenido va por palabras."""
    from shared.comparacion import _canonical_a_otra, _norm, _pierde_inlinks

    if pa is None:
        return ["sin_dato_anterior"]
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
    salida.extend(_cambio_de_contenido(pa, pb, usar_hash))
    if pa.get("in_sitemap") is True and pb.get("in_sitemap") is False:
        salida.append("sale_del_sitemap")
    if _pierde_inlinks(pa.get("inlinks"), pb.get("inlinks")):
        salida.append("pierde_enlaces")
    ra, rb = pa.get("pagerank_score"), pb.get("pagerank_score")
    if ra is not None and rb is not None and ra - rb >= PAGERANK_MIN_PUNTOS:
        salida.append("pierde_pagerank")
    return salida


def _cambio_de_contenido(pa: dict, pb: dict, usar_hash: bool = False) -> list[str]:
    """«contenido» (cambia mucho: al menos el 30 % y 50 palabras), «texto_contenido»
    (el mismo numero de palabras, mas o menos, pero otro texto) o nada.

    El hash manda solo con `usar_hash` (los dos censos con el mismo codigo y la
    misma config de extraccion: `hash_comparable`) y `content_hash` en las DOS
    paginas (decision 165): un texto reescrito con las mismas palabras no se
    veia, y dos hashes iguales son el mismo contenido principal. Si no, la regla
    de siempre por palabras: un hash cambia entero con un caracter, y con otra
    version del extractor cambian TODOS; atribuirlo al sitio seria afirmar lo
    que no se sabe (66, 91).
    """
    wa, wb = pa.get("word_count"), pb.get("word_count")
    mucho = bool(wa and wb is not None and abs(wb - wa) >= CONTENIDO_MIN_PALABRAS
                 and abs(wb - wa) / wa >= CONTENIDO_PARTE)
    ha, hb = pa.get("content_hash"), pb.get("content_hash")
    if usar_hash and ha and hb:
        if ha == hb:
            return []
        return ["contenido"] if mucho else ["texto_contenido"]
    return ["contenido"] if mucho else []


def _config_de_extraccion(job) -> tuple:
    """Lo de la config del job que cambia el contenido principal extraido, y
    con el su hash: el render, el perfil de dispositivo (un sitio con servido
    dinamico da a un movil otro HTML: decision 173), el recorte de promos y
    los selectores propios."""
    from shared.perfiles import perfil_de_ua

    cfg = getattr(job, "config", None) or {}
    ext = cfg.get("extraction") or {}
    return (bool(cfg.get("render_js")), perfil_de_ua(cfg.get("user_agent")),
            ext.get("extract_page_content", True) is not False,
            ext.get("strip_promo_blocks", True) is not False,
            tuple(sorted(ext.get("custom_boilerplate_selectors") or [])))


def hash_comparable(job_a, job_b) -> bool:
    """Si el `content_hash` de dos censos se puede comparar: la MISMA version
    del crawler, conocida en los dos (desconocido no es igual, decision 66), y
    la misma config de extraccion. Entre agosto y octubre hubo al menos seis
    cambios de `extract_main_content`: con uno de por medio cambian todos los
    hashes, y cada pagina que cae saldria con «cambia el texto» en vez de «sin
    cambio tecnico»."""
    va, vb = getattr(job_a, "crawler_version", None), getattr(job_b, "crawler_version", None)
    return bool(va and vb and va == vb and _config_de_extraccion(job_a) == _config_de_extraccion(job_b))


def es_de_competidor(db, job) -> bool:
    """Si el rastreo es de un perfil de la competencia: su trafico no esta en
    el Search Console del cliente, y la competencia no avisa (como en el
    worker). Si no se puede leer el perfil, False: lo de siempre."""
    from shared.models import CrawlProfile

    pid = getattr(job, "profile_id", None)
    if not pid:
        return False
    try:
        perfil = db.get(CrawlProfile, pid)
    except Exception:
        return False
    return perfil is not None and getattr(perfil, "rol", None) == "competidor"


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


def _cubre(db, cliente_id, props: list[str], desde: date, hasta: date) -> tuple[bool, str | None, bool]:
    """(cubre, motivo si no, falta solo el final -> pendiente).

    Por DIAS CON FILAS de cada propiedad activa, no por `gsc_estado.ultimo_dia`:
    la descarga avanza ese dia aunque Google lo devuelva vacio (hoy menos 2 casi
    nunca esta cerrado a las 4 UTC), y un dia vacio contado como 0 clics es una
    caida falsa del 14 % en una ventana de 7 dias.
    """
    from shared.models import GscEstado

    if not props:
        return False, None, False
    estados = [e for e in db.query(GscEstado).filter(GscEstado.cliente_id == cliente_id)
               if e.propiedad in props]
    total = (hasta - desde).days + 1
    for prop in props:
        dias = _dias_con_datos(db, cliente_id, prop, desde, hasta)
        if len(dias) == total:
            continue
        ultimo = max(dias, default=None)
        faltan = sorted({desde + timedelta(days=i) for i in range(total)} - dias)
        if ultimo is None or all(d > ultimo for d in faltan):
            return False, (f"Search Console aún no tiene datos hasta el {hasta.isoformat()}: el tráfico se "
                           "compara cuando lleguen."), True
        return False, (f"A los datos de Search Console de {prop} les faltan {_cuantos(len(faltan), 'día', 'días')} "
                       f"dentro de las ventanas (el primero, {faltan[0].isoformat()}): no se compara con días "
                       "incompletos."), False
    recortados = sorted(d for e in estados for d in (e.dias_truncados or [])
                        if desde.isoformat() <= d <= hasta.isoformat())
    if recortados:
        return False, (f"Google recortó {_cuantos(len(recortados), 'día', 'días')} en el tope de filas dentro de "
                       "las ventanas: sin todas las páginas no se compara el tráfico."), False
    return True, None, False


def _cuantos(n: int, uno: str, varios: str) -> str:
    return f"{_num(n)} {uno if n == 1 else varios}"


def comparar_trafico(db, job_a, job_b, paginas_a: list[dict], paginas_b: list[dict],
                     concluyente: bool = True, motivo_no_concluyente: str | None = None) -> dict[str, Any] | None:
    """El trafico de las paginas entre dos censos. None si el cliente no tiene
    Search Console; con `pendiente` si los datos aun no llegan.

    Con una comparacion de paginas NO concluyente (censo truncado, detras de un
    muro, alcances distintos: decisiones 67, 70 y 103) no se afirma nada: una
    pagina que no se alcanzo «desaparece» y una que da 403 por el WAF «ahora da
    error».
    """
    from shared.gsc import clave_de_url
    from shared.plantillas import firma_de_ruta

    cliente_id = getattr(job_b, "cliente_id", None)
    if not cliente_id:
        return None
    # Un competidor no esta en el Search Console del cliente: compararlo con
    # sus propiedades daria «0 clics» de un sitio del que no hay datos.
    if es_de_competidor(db, job_b):
        return None
    props = propiedades_activas(db, cliente_id)
    if not props:
        return None
    if not concluyente:
        return {"comparable": False, "pendiente": False,
                "motivo": ("La comparación de páginas no es concluyente"
                           + (f" ({motivo_no_concluyente})" if motivo_no_concluyente else "")
                           + ": no se afirma nada del tráfico de Google.")}
    dias = dias_de_ventana(job_a, job_b)
    if not dias:
        entre = (dia_del_censo(job_b) - dia_del_censo(job_a)).days
        return {"comparable": False, "pendiente": False,
                "motivo": (f"Los dos rastreos están a {_cuantos(entre, 'día', 'días')}: el tráfico de Google se "
                           f"compara con al menos {VENTANA_MIN_DIAS} días entre medias.")}
    ini_a, fin_a = ventana(job_a, dias)
    ini_b, fin_b = ventana(job_b, dias)
    base = {"dias": dias, "ventana_anterior": [ini_a.isoformat(), fin_a.isoformat()],
            "ventana_nueva": [ini_b.isoformat(), fin_b.isoformat()], "propiedades": props}
    cubre, motivo, pendiente = _cubre(db, cliente_id, props, ini_a, fin_b)
    if not cubre:
        return {**base, "comparable": False, "pendiente": pendiente, "motivo": motivo}

    datos_a = _clics(db, cliente_id, props, ini_a, fin_a)
    datos_b = _clics(db, cliente_id, props, ini_b, fin_b)
    clics_a = {k: c for k, (_, c) in datos_a.items()}
    clics_b = {k: c for k, (_, c) in datos_b.items()}
    por_clave_a = {clave_de_url(p["url"]): p for p in paginas_a if p.get("url")}
    por_clave_b = {clave_de_url(p["url"]): p for p in paginas_b if p.get("url")}
    # El total, de los hosts que se rastrearon: una propiedad `sc-domain:` cubre
    # tambien el blog o otro subdominio que este censo no mira.
    hosts = {(urlsplit(p["url"]).hostname or "").lower() for p in paginas_a + paginas_b if p.get("url")}

    def del_alcance(datos):
        return sum(c for _, (u, c) in datos.items() if (urlsplit(u).hostname or "").lower() in hosts)

    total_a, total_b = del_alcance(datos_a), del_alcance(datos_b)
    por_hash = hash_comparable(job_a, job_b)

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
    # Trasvases: la que cae y la URL a la que se consolida, si esa GANA al menos
    # la mitad de lo que la otra pierde. Con «sube un clic» bastaba para decir
    # que 300 clics perdidos en un 404 «se mueven».
    trasvase, recibe = set(), set()
    for clave, sentido in sentidos.items():
        if sentido != "caidas":
            continue
        perdidos = clics_a.get(clave, 0) - clics_b.get(clave, 0)
        for destino in _destinos(por_clave_b.get(clave) or por_clave_a.get(clave)):
            if clics_b.get(destino, 0) - clics_a.get(destino, 0) >= TRASVASE_MIN_PARTE * perdidos:
                trasvase.add(clave)
                recibe.add(destino)
                break
    for clave, sentido in sentidos.items():
        ca, cb = clics_a.get(clave, 0), clics_b.get(clave, 0)
        dif = cb - ca
        n[sentido] += 1
        pa, pb = por_clave_a.get(clave), por_clave_b.get(clave)
        url = (pb or pa)["url"]
        cambios = cambios_de_pagina(pa, pb, usar_hash=por_hash)
        if clave in trasvase:
            # Lo que la saca del indice se sigue diciendo: un trasvase hacia la
            # buena con la vieja dando error es un dato, no un consuelo.
            cambios = ["trasvase"] + [c for c in cambios if c in ("error_nuevo", "desaparece", "noindex")]
        elif not cambios:
            cambios = ["recibe_trasvase"] if clave in recibe else ["sin_cambio"]
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
        # Lo que tiene causa tecnica primero; lo que no se puede atribuir al final.
        filas.sort(key=lambda f: (f["cambio"] in ("sin_cambio", "sin_dato_anterior"),
                                  f["diferencia"] if f["diferencia"] < 0 else -f["diferencia"]))
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
        # Si el contenido se comparo por su hash o por palabras (decision 165).
        "contenido_por_hash": por_hash,
    }
    r["estacional"] = _estacional(db, cliente_id, props, hosts, ini_a, fin_a, ini_b, fin_b, variacion)
    r["textos"] = textos(r)
    return r


def _estacional(db, cliente_id, props, hosts, ini_a, fin_a, ini_b, fin_b, variacion) -> dict | None:
    """Lo mismo un ano antes, si hay datos de TODOS esos dias. None si no se
    puede saber."""
    if variacion is None:
        return None
    hace_un_ano = timedelta(days=364)  # mismo dia de la semana
    cubre, _, _ = _cubre(db, cliente_id, props, ini_a - hace_un_ano, fin_b - hace_un_ano)
    if not cubre:
        return None

    def total(desde, hasta):
        return sum(c for _, (u, c) in _clics(db, cliente_id, props, desde, hasta).items()
                   if (urlsplit(u).hostname or "").lower() in hosts)

    a = total(ini_a - hace_un_ano, fin_a - hace_un_ano)
    b = total(ini_b - hace_un_ano, fin_b - hace_un_ano)
    if not a:
        return None
    antes = (b - a) / a
    return {"variacion_ano_anterior": round(antes, 3),
            "igual": abs(antes - variacion) <= ESTACIONAL_MARGEN and (antes < 0) == (variacion < 0)}


def _num(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def _fecha(iso: str, con_ano: bool = False) -> str:
    return datetime.fromisoformat(iso).strftime("%d/%m/%Y" if con_ano else "%d/%m")


def textos(r: dict[str, Any]) -> list[str]:
    """Las frases del trafico entre censos: las mismas en la interfaz y en
    Slack. Nunca dicen «por»: dicen «coincide con»."""
    if not r.get("comparable"):
        return [r["motivo"]] if r.get("motivo") else []
    va, vn = r["ventana_anterior"], r["ventana_nueva"]
    ano = va[0][:4] != vn[1][:4]
    lineas = []
    var = r.get("variacion")
    pct = round(100 * var) if var is not None else None
    total = (f"Clics de Google: {_num(r['clics_anterior'])} del {_fecha(va[0], ano)} al {_fecha(va[1], ano)} y "
             f"{_num(r['clics_nuevo'])} del {_fecha(vn[0], ano)} al {_fecha(vn[1], ano)}"
             + (f" ({'+' if pct > 0 else ''}{pct} %)." if pct is not None else "."))
    lineas.append(total)
    est = r.get("estacional") or {}
    if r.get("general"):
        lineas.append("La caída es general en todo el sitio, no de unas páginas concretas"
                      + (f"; el mismo periodo del año anterior cayó un {round(-100 * est['variacion_ano_anterior'])} %: "
                         "parece estacional." if est.get("igual") else
                         ": puede ser demanda, competencia, la página de resultados o un cambio de Google."))
    elif est.get("igual") and var is not None and var < 0:
        lineas.append("El mismo periodo del año anterior varió lo mismo: parece estacional.")
    # Con una caida general, los grupos de paginas son DETALLE, no la causa
    # (decision 165): primero que es de todo el sitio, y los grupos detras de
    # una frase que lo dice. Sin ella, «40 paginas perdieron 300 clics; coincide
    # con que cambia el title» se lee como la explicacion de una caida que
    # tambien tienen las paginas que no cambiaron.
    # Solo si detras va a salir al menos una linea: las subidas sin cambio, de
    # trasvase o sin dato anterior no se escriben.
    subidas_escritas = [g for g in r.get("subidas") or []
                        if g["cambio"] not in ("sin_cambio", "recibe_trasvase", "sin_dato_anterior")]
    if r.get("general") and (r.get("caidas") or subidas_escritas):
        lineas.append("Por páginas, como detalle y no como causa (la caída es de todo el sitio, también de "
                      "las que no cambiaron):")

    def paginas(g):
        return _cuantos(g["paginas"], "página", "páginas")

    def verbo(g, uno, varios):
        return uno if g["paginas"] == 1 else varios

    def clics(n):
        return _cuantos(n, "clic", "clics")

    for g in r.get("caidas") or []:
        if g["cambio"] == "trasvase":
            lineas.append(f"{paginas(g)} {verbo(g, 'bajó', 'bajaron')} {clics(-g['diferencia'])} que pasan a su "
                          "canonical o a la misma URL con o sin barra final, que gana al menos la mitad: el "
                          "tráfico se mueve, no se pierde.")
        elif g["cambio"] == "sin_cambio":
            lineas.append(f"{paginas(g)} {verbo(g, 'perdió', 'perdieron')} {clics(-g['diferencia'])} sin cambio "
                          "técnico en el rastreo: puede ser demanda, competencia o la página de resultados.")
        elif g["cambio"] == "sin_dato_anterior":
            lineas.append(f"{paginas(g)} {verbo(g, 'perdió', 'perdieron')} {clics(-g['diferencia'])} y no "
                          f"{verbo(g, 'estaba', 'estaban')} en el rastreo anterior: no hay con qué comparar.")
        else:
            lineas.append(f"{paginas(g)} {verbo(g, 'perdió', 'perdieron')} {clics(-g['diferencia'])}; coincide "
                          f"con que en {verbo(g, 'ella', 'ellas')} {g['texto']} entre los dos rastreos.")
    for g in subidas_escritas:
        lineas.append(f"{paginas(g)} {verbo(g, 'ganó', 'ganaron')} {clics(g['diferencia'])}; coincide con que "
                      f"en {verbo(g, 'ella', 'ellas')} {g['texto']}.")
    for x in r.get("rupturas") or []:
        dia = datetime.fromisoformat(x["fecha"]).strftime("%d/%m/%Y")
        lineas.append(f"Ojo, las ventanas cruzan una ruptura de Search Console ({dia}): {x['texto']}")
    return lineas


def completar_pendientes(db, cliente_id, limite: int = 10) -> int:
    """Rehace la comparacion de trafico de los censos que la dejaron
    `pendiente` porque los datos aun no habian llegado. La llama el worker
    despues de cada descarga de Search Console. Nunca lanza, y un job raro no
    corta los demas."""
    import uuid

    from sqlalchemy.orm.attributes import flag_modified

    from shared.comparacion import paginas_para_comparar
    from shared.models import Job

    hechos = 0
    try:
        jobs = (db.query(Job).filter(Job.cliente_id == cliente_id, Job.comparacion.isnot(None))
                .order_by(Job.created_at.desc()).limit(limite).all())
    except Exception:
        db.rollback()
        logger.exception("No se pudo leer el trafico pendiente del cliente %s", cliente_id)
        return 0
    props = None
    for job in jobs:
        try:
            comp = job.comparacion if isinstance(job.comparacion, dict) else {}
            t = comp.get("trafico")
            if not (isinstance(t, dict) and t.get("pendiente")) or not comp.get("comparado_con"):
                continue
            anterior = db.get(Job, uuid.UUID(str(comp["comparado_con"])))
            if anterior is None:
                continue
            # Uno de la competencia guardado antes de la decision 165: no se
            # completa (ni se leen sus dos censos en cada descarga).
            if es_de_competidor(db, job):
                continue
            # Antes de leer dos censos enteros, si los datos siguen sin llegar.
            dias = dias_de_ventana(anterior, job)
            if props is None:
                props = propiedades_activas(db, cliente_id)
            if dias and not _cubre(db, cliente_id, props, ventana(anterior, dias)[0], ventana(job, dias)[1])[0]:
                continue
            paginas_a, paginas_b = paginas_para_comparar(db, anterior, job)
            nuevo = comparar_trafico(db, anterior, job, paginas_a, paginas_b,
                                     concluyente=comp.get("concluyente") is not False)
            if nuevo is None or nuevo.get("pendiente"):
                continue
            # Se reclama la fila antes de escribir: con dos propiedades del
            # mismo cliente sincronizando a la vez, las dos llegarian aqui con
            # el job pendiente y el aviso saldria dos veces.
            db.refresh(job, with_for_update=True)
            comp = job.comparacion if isinstance(job.comparacion, dict) else {}
            t = comp.get("trafico")
            if not (isinstance(t, dict) and t.get("pendiente")):
                db.rollback()
                continue
            # `aviso` marca que este job ya paso por el aviso: lo que se avisa
            # es el paso de pendiente a hecho, una vez (decision 165).
            nuevo = {**nuevo, "aviso": {"estado": "por_enviar"}}
            job.comparacion = {**comp, "trafico": nuevo}
            flag_modified(job, "comparacion")
            db.commit()
            hechos += 1
            _avisar_completado(db, job)
        except Exception:
            db.rollback()
            logger.exception("No se pudo completar el trafico pendiente del job %s", getattr(job, "id", "?"))
    return hechos


def _avisar_completado(db, job) -> None:
    """Manda el trafico recien completado al Slack del cliente y deja anotado
    el resultado en `trafico.aviso`. Best-effort: nunca lanza.

    Al acabar el rastreo, el informe de Slack salio con «Search Console aún no
    tiene datos…»; sin esto, la caida de verdad se quedaba en la ficha y no
    llegaba a nadie. Va por el mismo sitio que ese informe (`avisar_de_trafico`:
    rastreos programados, canal del cliente con «informe» activo, nunca de la
    competencia) y con los mismos `textos`.
    """
    try:
        from sqlalchemy.orm.attributes import flag_modified

        from shared.avisos import avisar_de_trafico
        from shared.models import Client, CrawlProfile

        cliente = db.get(Client, job.cliente_id) if getattr(job, "cliente_id", None) else None
        perfil = db.get(CrawlProfile, job.profile_id) if getattr(job, "profile_id", None) else None
        trafico = (job.comparacion or {}).get("trafico") or {}
        enviado = avisar_de_trafico(cliente, job, trafico, perfil)
        comp = dict(job.comparacion or {})
        comp["trafico"] = {**trafico, "aviso": {
            "estado": "enviado" if enviado else "no_enviado",
            "en": datetime.now(timezone.utc).isoformat(timespec="seconds")}}
        job.comparacion = comp
        flag_modified(job, "comparacion")
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("No se pudo avisar del trafico completado del job %s", getattr(job, "id", "?"))
