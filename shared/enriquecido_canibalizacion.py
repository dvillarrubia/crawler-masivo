"""Estados y filtro del modulo `canibalizacion` del analisis enriquecido
(#18, #169 punto 3, decision 175).

**La unidad es un PAR de paginas que se reparten una busqueda, no la URL ni
el grupo entero.** La entrada es el aviso `canibalizacion_por_consulta` de la
decision 157 (`analysis/oportunidades_gsc.py`): una busqueda y las 2 a 5 URLs
que se reparten sus clics. Se juzga por pares porque un grupo de tres puede
tener dos paginas sustitutas y una que no (en Lopesan, «anfi del mar»: la
ficha del hotel y dos posts que entre si son el mismo), y porque «fusionar»
tiene que decir QUE dos paginas. Un grupo de dos es un par; uno de tres, tres.

- `pares_canibalizacion` (el `filtrar_urls` del modulo, `por_grupo`) devuelve
  ids de PAR (`id_de_par(aviso, k)`), no de URL: de los avisos del job con mas
  clics en juego (como mucho `MAX_GRUPOS` busquedas), los pares con alguna
  pagina en el alcance y **en el mismo idioma**. Dos paginas en idiomas
  distintos que se reparten una busqueda no son una canibalizacion que se
  arregle fusionando o diferenciando, sino segmentacion internacional
  (hreflang): no se pregunta, y el aviso de la 157
  se queda como esta (preguntarlo daria un «no son sustitutas» que rebajaria
  el aviso como reparto legitimo, y eso no se puede afirmar).
- `estados_canibalizacion` (su `construir_estados`) da un `Estado` por par,
  con `ambito="consulta"` y `url_id` = la URL menor del par (estable entre
  censos). En `decisions` el par se reconoce por la `clave` (hash del estado)
  o por `estado_enviado.busqueda` + las rutas de `estado_enviado.paginas`.

Ni este fichero ni el modulo llaman a nadie: solo leen la BD.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from itertools import combinations
from urllib.parse import urlsplit

from shared.decisiones import Estado

TIPO_AVISO = "canibalizacion_por_consulta"
# Busquedas que se preguntan como mucho, las de mas clics en juego primero
# (todos sus pares en el mismo idioma). Medido el 24-oct-2026 en Lopesan
# (censo 1e7ea651): 459 busquedas; las 300 primeras llevan 26.017 de sus
# 27.993 clics repartidos (el 93 %) y la ultima tiene 16. Una lista de 459 no
# la revisa nadie.
MAX_GRUPOS = 300
# Caracteres del contenido principal POR PAGINA. El contenido medio de esas
# paginas son 4.777 caracteres: sin recorte, un par costaria diez veces mas y
# lo que decide si son sustitutas esta en titulo, H1 y el primer parrafo.
CARACTERES_CONTENIDO = 500
# La regla de la 157 exige un 20 % de los clics a cada URL: no puede haber mas
# de cinco. El tope es defensivo, por si alguien baja ese minimo.
MAX_PAGINAS = 5
# Un aviso de 5 paginas da C(5,2) = 10 pares; el id de un par es
# `aviso * PARES_POR_AVISO + k`, que cabe de sobra.
PARES_POR_AVISO = 16


def id_de_par(issue_id: int, k: int) -> int:
    return int(issue_id) * PARES_POR_AVISO + k


def deshacer_par(id_par: int) -> tuple[int, int]:
    return divmod(int(id_par), PARES_POR_AVISO)


def _trozos(lista: list, n: int = 500):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def redondear(n) -> int | None:
    """Dos cifras significativas: 874 -> 870, 9.299 -> 9.300, 16 -> 16. El
    modelo no necesita el clic exacto, y un recrawl con un trafico parecido da
    el MISMO estado y sale de la cache (decision 84; #169 pide tramos)."""
    if n is None:
        return None
    n = float(n)
    if n < 100:
        return int(round(n))
    paso = 10 ** (int(math.floor(math.log10(n))) - 1)
    return int(round(n / paso) * paso)


def _urls_de_aviso(details) -> list[dict]:
    return [u for u in ((details or {}).get("urls") or []) if isinstance(u, dict) and u.get("url")][:MAX_PAGINAS]


def pares_de_aviso(details) -> list[tuple[int, dict, dict]]:
    """(k, pagina, pagina) de un aviso, en el orden del aviso: `k` es estable
    mientras el aviso no cambie (y un re-analisis le cambia el id)."""
    return [(k, a, b) for k, (a, b) in enumerate(combinations(_urls_de_aviso(details), 2))]


def _hash_url(url: str) -> str:
    # `urls.url_hash` es el SHA-256 de la URL ya normalizada que se guarda en
    # `urls.url` (`compute_url_hash`; comprobado en produccion: 0 distintas de
    # 5.578 en Lopesan y de una muestra del 1 % de toda la tabla).
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _ids_por_url(session, job_id, urls: set[str]) -> dict[str, int]:
    """Por `(job_id, url_hash)`, el indice unico de `urls`: `urls.url` no tiene
    indice y un `url IN (...)` recorre todas las filas del job. Lo que no case
    por hash (filas antiguas o de prueba) se busca por URL, solo eso."""
    from shared.models import Url

    jid = _uuid(job_id)
    salida: dict[str, int] = {}
    por_hash = {_hash_url(u): u for u in urls}
    for trozo in _trozos(sorted(por_hash)):
        for uid, url in session.query(Url.id, Url.url).filter(
                Url.job_id == jid, Url.url_hash.in_(trozo)):
            if url in urls:
                salida[url] = uid
    faltan = sorted(u for u in urls if u not in salida)
    for trozo in _trozos(faltan):
        for uid, url in session.query(Url.id, Url.url).filter(Url.job_id == jid, Url.url.in_(trozo)):
            salida[url] = uid
    return salida


def _idioma_de_ruta(url: str) -> str:
    from shared.plantillas import es_idioma

    for tramo in [t for t in (urlsplit(url).path or "/").split("/") if t][:2]:
        if es_idioma(tramo):
            return tramo
    return ""


def _idiomas(session, url_de_id: dict[int, str]) -> dict[int, str]:
    """El idioma principal (`es`, `en`) de cada URL: `<html lang>`; si no lo
    hay (censos de antes de la columna), la anotacion hreflang de la propia
    URL; y si tampoco, el tramo de idioma de la ruta (`/en/`, `/blog/de/`).
    '' = no se sabe."""
    from shared.models import Hreflang, HtmlMeta

    salida: dict[int, str] = {}
    ids = sorted(url_de_id)
    for trozo in _trozos(ids):
        for uid, lang in session.query(HtmlMeta.url_id, HtmlMeta.html_lang).filter(
                HtmlMeta.url_id.in_(trozo)):
            if lang and lang.strip():
                salida[uid] = lang
        faltan = [u for u in trozo if u not in salida]
        if faltan:
            for uid, lang, href in session.query(Hreflang.url_id, Hreflang.lang, Hreflang.href).filter(
                    Hreflang.url_id.in_(faltan)):
                if href == url_de_id[uid] and lang and lang.lower() != "x-default":
                    salida.setdefault(uid, lang)
    for uid, url in url_de_id.items():
        if uid not in salida:
            salida[uid] = _idioma_de_ruta(url)
    return {u: (v or "").strip().lower().replace("_", "-").split("-")[0] for u, v in salida.items()}


def mismo_idioma(a: str, b: str) -> bool:
    """Sin saber el de alguna, se pregunta: no hay base para dejarlo fuera."""
    return not a or not b or a == b


def pares_canibalizacion(session, job_id, url_ids: list[int]) -> list[int]:
    """Ids de PAR (modulo `por_grupo`): de los avisos del job, por clics en
    juego (o impresiones, si no tiene clics) y como mucho `MAX_GRUPOS`
    busquedas, los pares con ALGUNA pagina en el alcance (con una carpeta, una
    dentro; por defecto, una indexable: la otra puede ser una canonicalizada)
    y en el mismo idioma."""
    from shared.models import Issue

    if not url_ids:
        return []
    avisos = session.query(Issue.id, Issue.details).filter(
        Issue.job_id == _uuid(job_id), Issue.issue_type == TIPO_AVISO).all()
    if not avisos:
        return []
    ids = _ids_por_url(session, job_id, {u["url"] for _, d in avisos for u in _urls_de_aviso(d)})
    idioma = _idiomas(session, {uid: url for url, uid in ids.items()})
    dentro = set(url_ids)
    elegidos = []
    for iid, d in avisos:
        pares = []
        for k, a, b in pares_de_aviso(d):
            ia, ib = ids.get(a["url"]), ids.get(b["url"])
            if ia not in dentro and ib not in dentro:
                continue
            if not mismo_idioma(idioma.get(ia, ""), idioma.get(ib, "")):
                continue
            pares.append(id_de_par(iid, k))
        if pares:
            elegidos.append(((-(int((d or {}).get("clics") or 0)), -(int((d or {}).get("impresiones") or 0)),
                              str((d or {}).get("consulta") or ""), iid), pares))
    elegidos.sort(key=lambda x: x[0])
    return [p for _, pares in elegidos[:MAX_GRUPOS] for p in pares]


def _ruta(url: str | None, host: str | None = None) -> str | None:
    """La ruta (con la query): con y sin www son el MISMO estado (decision
    169). Una URL de otro host que el de la pagina va entera."""
    if not url:
        return None
    partes = urlsplit(url)
    if host is not None and partes.hostname and partes.hostname != host:
        return url
    ruta = partes.path or "/"
    return ruta + ("?" + partes.query if partes.query else "")


def pagina_del_estado(u: dict, *, titulo: str = "", h1: str = "", contenido: str = "",
                      no_indexable: str | None = None, canonica: str | None = None) -> dict:
    """Una pagina del estado (pura: la usa tambien la medicion de coste).

    - `parte_pct`: su parte de los clics de la busqueda, o de las impresiones
      si la busqueda no tiene clics (`reparto_por`), en tramos de 5 %.
    - clics e impresiones a dos cifras significativas y la posicion entera:
      un recrawl con un trafico parecido sale de cache (#169).
    - `no_indexable`: por que no es indexable (`Canonicalised`, `Noindex`...)
      o None; `canonica`: la ruta a la que apunta su canonical cuando apunta
      a OTRA URL. Sin esto Jev recomendaria canonicalizar lo que ya lo esta.
    """
    host = urlsplit(u["url"]).hostname
    return {
        "ruta": _ruta(u["url"]),
        "parte_pct": int(round(float(u.get("parte") or 0) * 20) * 5),
        "clics": redondear(u.get("clics")),
        "impresiones": redondear(u.get("impresiones")),
        "posicion": (max(1, int(round(float(u["posicion"])))) if u.get("posicion") is not None else None),
        "no_indexable": no_indexable,
        "canonica": _ruta(canonica, host) if canonica and canonica != u["url"] else None,
        "titulo": titulo or "", "h1": h1 or "", "contenido": contenido or "",
    }


def estado_del_par(details: dict, a: dict, b: dict) -> dict:
    """Las dos paginas ORDENADAS POR RUTA, no por parte: en un reparto casi al
    50 % el orden por parte cambia de un recrawl a otro con las mismas cifras
    redondeadas, y con el la clave del estado y la cache (decision 175).
    `otras_paginas`: cuantas URLs mas se reparten la busqueda (las partes del
    par no tienen por que sumar 100)."""
    return {"busqueda": str(details.get("consulta") or ""),
            "reparto_por": details.get("reparto_por") or "clics",
            "otras_paginas": max(0, len(_urls_de_aviso(details)) - 2),
            "paginas": sorted([a, b], key=lambda p: (p["ruta"] or "", p["titulo"]))}


def estados_canibalizacion(session, ids_par: list[int]) -> list[Estado]:
    """Un estado por par, en el orden recibido (ver `estado_del_par` y
    `pagina_del_estado`). Un par cuyo aviso ya no existe (el censo se
    re-analizo a mitad) no da estado."""
    from sqlalchemy import func

    from shared.models import Heading, HtmlMeta, Issue, PageContent, Url

    pedidos = [deshacer_par(p) for p in ids_par]
    avisos = {}
    for trozo in _trozos(sorted({iid for iid, _ in pedidos})):
        for iid, job_id, d in session.query(Issue.id, Issue.job_id, Issue.details).filter(Issue.id.in_(trozo)):
            avisos[iid] = (job_id, d or {})
    # Las URLs de cada aviso, por job (un aviso solo nombra URLs de su censo).
    por_job: dict = {}
    for job_id, d in avisos.values():
        por_job.setdefault(job_id, set()).update(u["url"] for u in _urls_de_aviso(d))
    ids: dict = {}
    for job_id, urls in por_job.items():
        ids[job_id] = _ids_por_url(session, job_id, urls)
    todos = sorted({uid for m in ids.values() for uid in m.values()})
    titulos: dict[int, str] = {}
    canonica: dict[int, str | None] = {}
    h1: dict[int, str] = {}
    contenido: dict[int, str] = {}
    indexab: dict[int, tuple] = {}
    for trozo in _trozos(todos):
        for uid, t, c in session.query(HtmlMeta.url_id, HtmlMeta.title, HtmlMeta.canonical_href).filter(
                HtmlMeta.url_id.in_(trozo)):
            titulos[uid] = t or ""
            canonica[uid] = c
        for uid, indexable, motivo in session.query(Url.id, Url.indexable, Url.indexability_status).filter(
                Url.id.in_(trozo)):
            indexab[uid] = (indexable, motivo)
        for uid, texto in (session.query(Heading.url_id, Heading.text)
                           .filter(Heading.url_id.in_(trozo), Heading.tag == "h1",
                                   Heading.oculto.isnot(True))
                           .order_by(Heading.url_id, Heading.position)):
            h1.setdefault(uid, texto or "")
        for uid, c in session.query(
                PageContent.url_id,
                func.substr(PageContent.content_text, 1, CARACTERES_CONTENIDO)).filter(
                PageContent.url_id.in_(trozo)):
            contenido[uid] = c or ""

    def pagina(job_id, u):
        uid = ids.get(job_id, {}).get(u["url"])
        indexable, motivo = indexab.get(uid, (u.get("indexable"), u.get("estado")))
        no_idx = None if indexable is not False else (motivo or "no indexable")
        return uid, pagina_del_estado(
            u, titulo=titulos.get(uid, ""), h1=h1.get(uid, ""), contenido=contenido.get(uid, ""),
            no_indexable=no_idx, canonica=canonica.get(uid) if no_idx == "Canonicalised" else None)

    estados = []
    for iid, k in pedidos:
        if iid not in avisos:
            continue
        job_id, d = avisos[iid]
        pares = {kk: (a, b) for kk, a, b in pares_de_aviso(d)}
        if k not in pares:
            continue
        a, b = pares[k]
        (ia, pa), (ib, pb) = pagina(job_id, a), pagina(job_id, b)
        # Donde cuelga: la URL menor del par, estable entre censos.
        ancla = ia if a["url"] <= b["url"] else ib
        estados.append(Estado(datos=estado_del_par(d, pa, pb), ambito="consulta",
                              url_id=ancla if ancla is not None else (ia or ib)))
    return estados


def efecto_en_el_aviso(sustitutas, recomendacion) -> dict | None:
    """Lo que el juicio de UN PAR haria con el aviso de la 157 (#169: «rebaja
    o confirma la incidencia»). **Apagado mientras las preguntas no tengan
    umbral**: solo actua con decisiones `decidido`, y sin umbral calibrado
    (#8) ninguna lo es (decision 84), asi que hoy siempre devuelve None. Nadie
    lo llama aun: emitir o rebajar avisos desde un modulo esta pendiente
    (decision 85). Cuando se encienda, un aviso se confirma si ALGUNO de sus
    pares sale sustituto, y se rebaja solo si TODOS sus pares se preguntaron y
    salieron que no: los pares en idiomas distintos no se preguntan, y con
    ellos el aviso no se puede rebajar.

    - sustitutas «no» decidido -> rebajar: es un reparto legitimo.
    - sustitutas «si» decidido -> confirmar, con la recomendacion si tambien
      esta decidida (si no, la recomendacion queda sin decir).
    """
    if sustitutas is None or getattr(sustitutas, "estado", None) != "decidido":
        return None
    if sustitutas.respuesta == "false":
        return {"efecto": "rebajar", "motivo": "reparto_legitimo"}
    if sustitutas.respuesta != "true":
        return None
    salida = {"efecto": "confirmar", "recomendacion": None}
    if recomendacion is not None and getattr(recomendacion, "estado", None) == "decidido":
        salida["recomendacion"] = recomendacion.respuesta
    return salida
