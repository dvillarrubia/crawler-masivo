"""Soft 404 determinista en el analisis (decision 105).

Las reglas puras viven en `shared/soft404.py`; aqui se cruzan con el censo y
se emiten las incidencias. Se juzgan las paginas 200, HTML e internas.

Dos tipos, para que lo dudoso no se lea como afirmado:

- `soft_404`: seguro. La severidad es la consecuencia (decision 64):
  `error` si es indexable y esta en el sitemap (Search Console lo archivaba
  asi: «La URL enviada parece un soft 404»); `warning` si es indexable; `info`
  si ya esta fuera del indice por noindex o canonical — Google no la va a
  indexar de todos modos, y lo que queda es el enlace roto disfrazado: el
  sitio enlaza una pagina de error que responde 200. Medido en www.uoc.edu:
  las 19 paginas de error con 200 del censo eran todas canonicalizadas o
  noindex; quitarlas del analisis las escondia.
- `soft_404_candidato`: `info`, con `details.candidato_llm = true` y los
  motivos. Es lo que la fase con modelo (#11) tiene que mirar; aqui no se
  afirma.

Un aviso por hallazgo (decision 36): una pagina con varias senales lleva UNA
incidencia con todas; la seguras tapan a las dudosas; y una pagina que ya es
`soft_404` no lleva ademas las incidencias de su contenido (`TAPADAS`:
contenido escaso, titulo/description/h1 que faltan o se repiten, duplicados),
que son el mismo hallazgo visto peor y mandan a arreglar otra cosa («haz cada
titulo distinto» en una pagina que tiene que responder 404).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any

from sqlalchemy import String, cast, delete, func, or_, select, update

from analysis import near_duplicates as nd
from shared import soft404 as s4
from shared.descarga_cruda import TITULOS_DE_BLOQUEO
from shared.respuestas import NO_VALIDAS
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)

# Con menos coincidencias que esto, una huella no se tacha de «plantilla»:
# dos paginas de error en un sitio de tres paginas son dos paginas de error.
MIN_COINCIDENCIAS_AMBIGUAS = 3
MUESTRA = 20
_PREFIJO_TEXTO = 1500
_LOTE = 5000
# Para `structured_data`, lotes mas pequenos: con 5.000 ids el planificador
# estima ~144.000 bloques (29 por URL) y prefiere recorrer la tabla ENTERA en
# cada lote; con 1.000 va por el indice de `url_id` (medido con EXPLAIN en
# penguin y Druni contra la base local de 1,6 M bloques).
_LOTE_SD = 1000
# Un titular de error SOLO afirma con menos palabras que esto (lo mismo que
# exige MinHash para medir parecido: por debajo no hay contenido que diga otra
# cosa), o si el titulo es el de la pagina de error que dio la sonda. Entre
# esto y `min_word_count` cabe una entrada de glosario «Error 404» de 150
# palabras, y eso es candidato, no error.
MAX_PALABRAS_SOLO_TITULO = nd.MIN_PALABRAS

# Lo que una pagina `soft_404` deja de llevar: describe su contenido, y su
# contenido es el de una pagina de error. Las de forma de URL, enlazado,
# sitemap y directivas se quedan: dicen algo de la URL, no de lo que sirve.
TAPADAS = (
    "low_word_count",
    "title_missing", "title_too_short", "title_too_long", "title_duplicate", "title_multiple",
    "description_missing", "description_too_short", "description_too_long",
    "description_duplicate", "description_multiple",
    # `h1_vacio` (decision 121) dice lo mismo que `h1_missing`: sin titular.
    "h1_missing", "h1_vacio", "h1_multiple", "h1_duplicate",
    "duplicate_content", "near_duplicate_content",
)
# Las que agrupan paginas por `group_key`: si al quitar las soft 404 un grupo se
# queda con una sola pagina, ya no hay duplicado y esa fila tambien sobra.
_TAPADAS_DE_GRUPO = ("title_duplicate", "description_duplicate", "h1_duplicate",
                     "duplicate_content")


def _umbral(t: dict, clave: str, defecto):
    v = t.get(clave)
    return defecto if v is None else v


def _margen(palabras: int) -> int:
    """Diferencia de palabras con la que una Jaccard de 0,9 aun es posible.

    Con un 10% de diferencia de tamano la Jaccard ya no llega a 0,9; el margen
    es mas ancho a proposito (25% + 5) para no perder nada por el eco de la URL.
    """
    return max(10, int(0.25 * palabras) + 5)


def _cerca(a: int, b: int) -> bool:
    return abs(a - b) <= _margen(max(a, b))


def _sondas_utiles(sondas: list[dict]) -> list[dict]:
    """Las sondas cuya respuesta es la pagina de error del sitio.

    200 (el soft 404 de serie) o 404/410 con cuerpo (la pagina de error que el
    sitio sirve bien, y que otras URLs pueden servir con 200). Una sonda que
    redirige no deja huella: el destino es una pagina real —muchas veces la
    home— y tomarla por «la pagina de error» marcaria la home.
    """
    utiles = []
    for s in sondas or []:
        if s.get("redirige_a") or s.get("error"):
            continue
        if s.get("status") not in (200, 404, 410):
            continue
        if not (s.get("texto") or s.get("titulo")):
            continue
        # La clasificacion del spider (decision 103), que ve codigo, cabeceras
        # y cuerpo; el titulo de abajo queda para las sondas anteriores.
        if s.get("response_class") in NO_VALIDAS:
            continue
        # Un muro de WAF con 200 no es la pagina de error del sitio: con su
        # huella, cada pagina bloqueada del censo saldria como soft 404, y eso
        # es un bloqueo (decisiones 62 y 82), no un hallazgo del sitio.
        titulo = (s.get("titulo") or "").strip().lower()
        if any(t in titulo for t in TITULOS_DE_BLOQUEO):
            continue
        utiles.append(s)
    return utiles


def _huella_de_sonda(s: dict):
    """La huella con la que se compara una sonda.

    El texto de la sonda se guarda recortado (`s4.MAX_TEXTO_SONDA`); el hash y
    el recuento, sobre el texto ENTERO (`huella_hash`, `huella_palabras`). Las
    sondas anteriores a esos campos con el texto recortado no pueden casar por
    igualdad —el hash seria el de un trozo— y se comparan por parecido del
    principio, como las paginas (`recortada`).
    """
    h = s4.huella(s.get("texto"), s.get("url"), s.get("titulo"))
    texto = s.get("texto") or ""
    recortada = bool(s.get("texto_recortado")) or len(texto) >= s4.MAX_TEXTO_SONDA
    if "huella_hash" in s:
        h = replace(h, hash=s.get("huella_hash"),
                    palabras=int(s.get("huella_palabras") or h.palabras))
    elif recortada:
        h = replace(h, hash=None, palabras=max(h.palabras, int(s.get("palabras") or 0)))
    return h, recortada


class _Componentes:
    """Union-find sobre los indices de las sondas."""

    def __init__(self, n: int) -> None:
        self.padre = list(range(n))

    def raiz(self, i: int) -> int:
        while self.padre[i] != i:
            self.padre[i] = self.padre[self.padre[i]]
            i = self.padre[i]
        return i

    def unir(self, a: int, b: int) -> None:
        ra, rb = self.raiz(a), self.raiz(b)
        if ra != rb:
            self.padre[rb] = ra


def analizar(an) -> dict[str, Any]:
    """Emite las incidencias de soft 404 del job de `an` y devuelve el resumen."""
    from shared.models import HtmlMeta, Heading, Issue, Job, PageContent, StructuredData, Url

    session = an.session
    job = session.query(Job).filter(Job.id == an.job_id).one_or_none()
    t = ((job.config or {}).get("analysis_thresholds") or {}) if job else {}
    similitud_min = float(_umbral(t, "soft404_similitud", nd.UMBRAL_SIMILITUD))
    min_palabras = int(_umbral(t, "soft404_min_palabras_igualdad", 5))
    max_prop = float(_umbral(t, "soft404_max_proporcion_huella", 0.5))
    min_word_count = an.min_word_count

    previo = (getattr(job, "soft404", None) or {}) if job else {}
    sondas = list(previo.get("sondas") or []) if isinstance(previo, dict) else []

    base = (
        Url.job_id == an.job_id, Url.is_html.is_(True), Url.is_internal.is_(True),
        Url.status_code == 200,
        # Un muro servido con 200 no es la pagina (decision 103): su titulo de
        # «Just a moment...» no es un titular de error del sitio.
        or_(Url.response_class.is_(None), Url.response_class.notin_(sorted(NO_VALIDAS))),
    )

    # -- Las sondas: su huella y el titulo de la pagina de error ------------------
    utiles = _sondas_utiles(sondas)
    todas: list[dict] = []
    por_host: dict[str, list[dict]] = defaultdict(list)
    titulos_de_error: dict[str, set[str]] = defaultdict(set)
    for s in utiles:
        s = dict(s)
        s["_i"] = len(todas)
        s["_huella"], s["_recortada"] = _huella_de_sonda(s)
        s["_firma"] = nd.firma(s["_huella"].texto)
        s["_coinciden"] = []
        s["_portada"] = False
        host = (s.get("host") or "").lower()
        todas.append(s)
        por_host[host].append(s)
        if s["_huella"].titulo:
            titulos_de_error[host].add(s["_huella"].titulo)

    # -- Pasada 1: titulo, h1, principio del contenido ------------------------
    sub = select(Url.id).where(*base).scalar_subquery()
    h1s: dict[int, str] = {}
    for url_id, texto in session.execute(
        select(Heading.url_id, Heading.text)
        .where(Heading.url_id.in_(sub), Heading.tag == "h1",
               or_(Heading.oculto.is_(None), Heading.oculto.is_(False)))
        .order_by(Heading.url_id, Heading.position)
    ):
        if url_id not in h1s and texto:
            h1s[url_id] = texto

    info: dict[int, dict] = {}
    seguras: dict[int, list[dict]] = defaultdict(list)
    dudosas: dict[int, list[dict]] = defaultdict(list)
    # Por lotes y sin guardar el principio del texto: con `.all()` y el prefijo
    # de 1.500 caracteres eran +251 MB en Druni (56.544 paginas), dentro del
    # worker que comparte memoria con Chromium.
    consulta_1 = (
        select(Url.id, Url.url, Url.host, Url.path, Url.content_word_count, Url.in_sitemap,
               Url.indexable,
               HtmlMeta.title, func.substr(PageContent.content_text, 1, _PREFIJO_TEXTO))
        .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
        .outerjoin(PageContent, PageContent.url_id == Url.id)
        .where(*base)
        .execution_options(yield_per=2000)
    )
    for url_id, url, host, path, cwc, in_sitemap, indexable, titulo, prefijo in session.execute(consulta_1):
        host = (host or "").lower()
        info[url_id] = {"url": url, "host": host, "path": path or "/",
                        "cwc": cwc, "in_sitemap": in_sitemap, "titulo": titulo,
                        "indexable": bool(indexable)}
        h1 = h1s.get(url_id)
        # Palabras propias; sin recuento (censos anteriores a la columna), las
        # del principio guardado, y si el principio esta lleno no se sabe
        # cuantas hay despues: al menos `min_word_count`.
        if cwc is not None:
            palabras = cwc
        else:
            palabras = len((prefijo or "").split())
            if len(prefijo or "") >= _PREFIJO_TEXTO:
                palabras = max(palabras, min_word_count)
        r = s4.senal_titulo(titulo, h1)
        if r is not None:
            fuerza, campo, frase = r
            senal = {"senal": "titulo_de_error", "campo": campo, "frase": frase,
                     "valor": (h1 if campo == "h1" else titulo or "")[:200]}
            # Una pagina de error no tiene contenido propio. Con un titular de
            # error y `min_word_count` palabras o mas, es un articulo que habla
            # de un error, no un error: candidato. Y por debajo, el titular
            # solo no basta salvo que no haya contenido que diga otra cosa o
            # que el titulo sea el de la pagina de error del sitio (la sonda).
            if fuerza == "seguro":
                if palabras >= min_word_count:
                    fuerza = "dudoso"
                    senal["content_word_count"] = palabras
                elif palabras >= MAX_PALABRAS_SOLO_TITULO:
                    if titulo and s4.quitar_eco(titulo, url) in titulos_de_error.get(host, ()):
                        senal["titulo_de_la_sonda"] = True
                    else:
                        fuerza = "dudoso"
                        senal["content_word_count"] = palabras
            (seguras if fuerza == "seguro" else dudosas)[url_id].append(senal)
        frase = s4.frase_sin_resultados(titulo, h1, prefijo)
        if frase:
            if s4.es_busqueda(url):
                # Una busqueda vacia no tiene mas que la frase. Con contenido
                # de verdad, la frase es otra cosa: «sin resultados
                # concluyentes» en un texto, «Marca X (0 resultados)» en las
                # facetas de una busqueda que si los tiene.
                if palabras < min_word_count or s4.frase_sin_resultados(titulo, h1):
                    seguras[url_id].append({"senal": "busqueda_sin_resultados", "frase": frase})
            elif cwc is not None and cwc < min_word_count:
                # Un listado que dice «no hay productos» sin ser el buscador:
                # una categoria vacia es un soft 404 de manual, pero la frase
                # tambien sale en un filtro sin resultados dentro de una pagina
                # con contenido. Con poco texto propio, candidato; sin eso, nada.
                dudosas[url_id].append({"senal": "listado_vacio", "frase": frase,
                                        "content_word_count": cwc})
    if not info:
        return _guardar(session, job, {"paginas_juzgadas": 0, "sondas": len(sondas)})
    ids = sorted(info)

    # -- Pasada 2: la huella de las sondas -------------------------------------
    alcance_plantilla: dict[tuple[str, str], int] = defaultdict(int)
    alcance_host: dict[str, int] = defaultdict(int)
    for d in info.values():
        d["plantilla"] = firma_de_ruta(d["path"])
        alcance_host[d["host"]] += 1
        alcance_plantilla[(d["host"], d["plantilla"])] += 1

    if por_host:
        # Solo se lee el texto entero de las paginas cuyo tamano puede casar
        # con alguna sonda: una Jaccard de 0,9 no es posible con un 25% de
        # diferencia de palabras, y la igualdad exacta menos. Sin recuento
        # (rastreos anteriores a la columna) se leen todas.
        condiciones = [Url.content_word_count.is_(None)]
        for s in todas:
            p = s["_huella"].palabras
            margen = _margen(p)
            condiciones.append(Url.content_word_count.between(max(0, p - margen), p + margen))
        consulta = (
            select(Url.id, PageContent.content_text)
            .join(PageContent, PageContent.url_id == Url.id)
            .where(*base, func.lower(Url.host).in_(list(por_host)), or_(*condiciones))
            .execution_options(yield_per=1000)
        )
        for url_id, texto in session.execute(consulta):
            d = info.get(url_id)
            if d is None:
                continue
            candidatas = [s for s in por_host.get(d["host"], [])
                          if s.get("plantilla") in (None, d["plantilla"])]
            if not candidatas:
                continue
            h = s4.huella(texto, d["url"], d["titulo"])
            firmas: dict[bool, Any] = {}
            for s in candidatas:
                metodo = s4.misma_huella(s["_huella"], h, min_palabras)
                parecido = 1.0 if metodo else None
                if metodo is None and s["_firma"] is not None and _cerca(s["_huella"].palabras, h.palabras):
                    # Con la sonda recortada se compara el MISMO principio de
                    # la pagina: el texto entero de una pagina de error larga
                    # no se parece a sus primeros 4.000 caracteres.
                    rec = s["_recortada"]
                    if rec not in firmas:
                        firmas[rec] = nd.firma(
                            s4.huella((texto or "")[:s4.MAX_TEXTO_SONDA], d["url"]).texto
                            if rec else h.texto)
                    if firmas[rec] is not None:
                        v = nd.similitud(s["_firma"], firmas[rec])
                        if v >= similitud_min:
                            metodo, parecido = "minhash", round(v, 3)
                if metodo is None:
                    continue
                if s4.es_portada(d["path"]):
                    # La sonda devuelve lo mismo que la portada: el sitio sirve
                    # la home para lo que no existe. La home no es la pagina de
                    # error, y las demas que casan son alias o URLs rotas: no
                    # se distingue sin mirar.
                    s["_portada"] = True
                    continue
                s["_coinciden"].append((url_id, metodo, parecido))

    # Las sondas que casan con las MISMAS paginas, o con la misma huella, son
    # la misma pagina de error vista desde dos sitios (la de la raiz y la de
    # una plantilla). Se juzgan juntas: si una de ellas ve que la huella es la
    # de una plantilla, ninguna la afirma. Antes cada sonda se medía contra su
    # alcance: la de la raiz contra el host entero, y un esqueleto de SPA que
    # era el 100% de su seccion pero el 40% del host salia afirmado en todas.
    comp = _Componentes(len(todas))
    primera_por_pagina: dict[int, int] = {}
    primera_por_hash: dict[tuple[str, str], int] = {}
    for s in todas:
        if s["_huella"].hash:
            k = ((s.get("host") or "").lower(), s["_huella"].hash)
            if k in primera_por_hash:
                comp.unir(primera_por_hash[k], s["_i"])
            else:
                primera_por_hash[k] = s["_i"]
        for url_id, _, _ in s["_coinciden"]:
            if url_id in primera_por_pagina:
                comp.unir(primera_por_pagina[url_id], s["_i"])
            else:
                primera_por_pagina[url_id] = s["_i"]
    componentes: dict[int, list[dict]] = defaultdict(list)
    for s in todas:
        componentes[comp.raiz(s["_i"])].append(s)

    resumen_sondas = []
    grupos = []
    for miembros in componentes.values():
        host = (miembros[0].get("host") or "").lower()
        paginas: dict[int, tuple[dict, str, float | None]] = {}
        for s in miembros:
            for url_id, metodo, parecido in s["_coinciden"]:
                paginas.setdefault(url_id, (s, metodo, parecido))
        portada = any(s["_portada"] for s in miembros)
        # La huella es la de una plantilla si cubre mas de `max_prop` del host
        # o de CUALQUIER forma de ruta en la que caen sus paginas.
        plantillas_tomadas = []
        por_plantilla = Counter(info[u]["plantilla"] for u in paginas)
        for plantilla, n in sorted(por_plantilla.items()):
            alcance = alcance_plantilla[(host, plantilla)]
            if n >= MIN_COINCIDENCIAS_AMBIGUAS and alcance and n / alcance > max_prop:
                plantillas_tomadas.append({"plantilla": plantilla, "coincidencias": n,
                                           "alcance": alcance,
                                           "proporcion": round(n / alcance, 3)})
        prop_host = len(paginas) / alcance_host[host] if alcance_host[host] else 0.0
        del_host = len(paginas) >= MIN_COINCIDENCIAS_AMBIGUAS and prop_host > max_prop
        ambigua = portada or del_host or bool(plantillas_tomadas)
        for s in miembros:
            coinciden = s["_coinciden"]
            alcance = (alcance_host[host] if s.get("plantilla") is None
                       else alcance_plantilla[(host, s["plantilla"])])
            resumen_sondas.append({
                "url": s.get("url"), "status": s.get("status"), "plantilla": s.get("plantilla"),
                "coincidencias": len(coinciden), "alcance": alcance,
                "proporcion": round(len(coinciden) / alcance, 3) if alcance else 0.0,
                "ambigua": ambigua, "devuelve_portada": s["_portada"],
                "misma_huella_que": [o.get("url") for o in miembros if o is not s] or None,
            })
        if not paginas and not portada:
            continue
        if ambigua:
            grupos.append({"miembros": miembros, "paginas": paginas, "portada": portada,
                           "plantillas": plantillas_tomadas, "del_host": del_host,
                           "proporcion_host": round(prop_host, 3)})
            continue
        for url_id, (s, metodo, parecido) in paginas.items():
            seguras[url_id].append({
                "senal": "huella_de_la_sonda", "metodo": metodo, "similitud": parecido,
                "sonda": s.get("url"), "sonda_status": s.get("status"),
                "sonda_origen": s.get("origen"),
            })

    # -- Productos descatalogados ------------------------------------------------
    # Google NO trata un producto agotado como soft 404 por si solo: pide
    # mantener la pagina si es util y marcar la disponibilidad. Agotado
    # (OutOfStock, SoldOut) no se emite. Descatalogado (Discontinued) es la
    # pagina de algo que ya no volvera, y Google si puede acabar tratandola
    # como soft 404 si no ofrece nada mas: candidato, nunca afirmado.
    # Por lotes de ids del censo y no con una subconsulta: con `IN (select)`
    # Postgres recorria `structured_data` ENTERA —la de todos los censos de la
    # instalacion— convirtiendo cada JSON a texto (9,4-12,4 s en Druni y
    # 8,9-11,2 s en penguin con 1,6 M bloques en la tabla, y creciendo con
    # ella); por lotes va por el indice de `url_id` y el filtro de texto solo
    # mira los bloques de este censo: 3,1 s en Druni y 3,4-3,5 s en penguin,
    # con las mismas filas.
    # El `OFFSET 0` es la barrera: sin ella el planificador vuelve a empujar el
    # filtro de texto al recorrido de la tabla (lo estima muy selectivo). Y un
    # `lower()` con tres LIKE en vez de tres ILIKE: la mitad de tiempo por
    # bloque (0,32 s frente a 0,64-0,74 s en 20.806 bloques de Druni).
    disponibilidad: dict[int, set[str]] = defaultdict(set)
    for i in range(0, len(ids), _LOTE_SD):
        bloques = (
            select(StructuredData.url_id, StructuredData.raw,
                   func.lower(cast(StructuredData.raw, String)).label("t"))
            .where(StructuredData.url_id.in_(ids[i:i + _LOTE_SD]))
            .offset(0).subquery()
        )
        for url_id, raw in session.execute(
            select(bloques.c.url_id, bloques.c.raw)
            .where(or_(bloques.c.t.like("%discontinued%"), bloques.c.t.like("%outofstock%"),
                       bloques.c.t.like("%soldout%")))
        ):
            disponibilidad[url_id] |= s4.disponibilidades(raw)
    agotados = 0
    for url_id, valores in disponibilidad.items():
        if url_id not in info or not valores:
            continue
        if valores <= {"discontinued"}:
            dudosas[url_id].append({"senal": "producto_descatalogado",
                                    "disponibilidad": sorted(valores)})
        elif valores <= {"outofstock", "soldout", "discontinued"}:
            agotados += 1

    # -- Emitir ---------------------------------------------------------------------
    from analysis.analyzer import _clave_de_grupo

    cuenta: dict[str, int] = defaultdict(int)
    for url_id, lista in seguras.items():
        d = info[url_id]
        for x in lista:
            cuenta[x["senal"]] += 1
        severidad = ("info" if not d["indexable"]
                     else "error" if d["in_sitemap"] else "warning")
        detalles = {"senales": lista, "en_sitemap": bool(d["in_sitemap"]),
                    "indexable": d["indexable"]}
        if dudosas.get(url_id):
            detalles["indicios"] = dudosas[url_id]
        an._add_issue(url_id, "soft_404", severidad, detalles)

    # Candidatos: uno por pagina, con todos sus motivos. Lo dudoso individual
    # solo en indexables: en una pagina que ya esta fuera del indice, un
    # indicio sin certeza no lleva a ninguna decision.
    candidatos: dict[int, dict] = {}
    claves_candidato: dict[int, str | None] = {}
    for url_id, lista in dudosas.items():
        if url_id in seguras or not info[url_id]["indexable"]:
            continue
        for x in lista:
            cuenta["candidato_" + x["senal"]] += 1
        candidatos[url_id] = {"candidato_llm": True, "motivos": list(lista)}
    agrupados = 0
    for g in grupos:
        # Las que ya se afirman por otra senal no se cuentan otra vez.
        paginas = sorted(u for u in g["paginas"] if u not in seguras)
        if not paginas:
            continue
        sondas_g = [s.get("url") for s in g["miembros"]]
        primera = g["miembros"][0]
        motivo = {"senal": "sonda_devuelve_portada" if g["portada"] else "huella_de_plantilla",
                  "sonda": primera.get("url"), "sonda_status": primera.get("status"),
                  "sondas": sondas_g, "plantilla": primera.get("plantilla"),
                  "plantillas_cubiertas": g["plantillas"],
                  "proporcion_host": g["proporcion_host"]}
        # Una incidencia por hallazgo, en una pagina que no tenga ya la suya.
        rep = next((u for u in paginas if u not in candidatos), paginas[0])
        d = candidatos.setdefault(rep, {"candidato_llm": True, "motivos": []})
        d["motivos"].append(motivo)
        d.update({"paginas_afectadas": len(paginas),
                  "alcance": alcance_host[(primera.get("host") or "").lower()],
                  "paginas_ejemplo": [info[u]["url"] for u in paginas[:MUESTRA]]})
        claves = sorted(str(s.get("clave") or s.get("url")) for s in g["miembros"])
        claves_candidato[rep] = _clave_de_grupo("sonda|" + "|".join(claves))
        agrupados += 1
    cuenta["candidato_agrupado"] += agrupados
    if not agrupados:
        cuenta.pop("candidato_agrupado")
    for url_id, detalles in candidatos.items():
        an._add_issue(url_id, "soft_404_candidato", "info", detalles,
                      group_key=claves_candidato.get(url_id))
    an._flush_issues()

    tapadas = _tapar(session, Issue, an.job_id, sorted(seguras))

    resumen = {
        "paginas_juzgadas": len(info),
        "soft_404": len(seguras),
        "soft_404_indexables": sum(1 for u in seguras if info[u]["indexable"]),
        "soft_404_en_sitemap": sum(1 for u in seguras if info[u]["in_sitemap"] and info[u]["indexable"]),
        "candidatos": len(candidatos),
        "por_senal": dict(sorted(cuenta.items())),
        "low_word_count_tapadas": tapadas.get("low_word_count", 0),
        "incidencias_tapadas": dict(sorted(tapadas.items())),
        "productos_agotados_no_emitidos": agotados,
        "sondas": len(sondas),
        "sondas_con_huella": len(utiles),
        "detalle_sondas": resumen_sondas,
    }
    return _guardar(session, job, resumen)


def _tapar(session, Issue, job_id, paginas: list[int]) -> dict[str, int]:
    """Quita de las `soft_404` las incidencias de su contenido (`TAPADAS`).

    Y los grupos de duplicados que se quedan con una sola pagina: si un titulo
    lo compartian una pagina real y tres de error, al quitar las de error la
    real ya no tiene a nadie con quien duplicarse.
    """
    cuenta: Counter = Counter()
    if not paginas:
        return {}
    grupos: set[tuple[str, str]] = set()
    for i in range(0, len(paginas), _LOTE):
        lote = paginas[i:i + _LOTE]
        grupos |= {(tipo, clave) for tipo, clave in session.execute(
            select(Issue.issue_type, Issue.group_key).where(
                Issue.job_id == job_id, Issue.issue_type.in_(_TAPADAS_DE_GRUPO),
                Issue.url_id.in_(lote), Issue.group_key.is_not(None)).distinct())}
        for tipo, n in session.execute(
            select(Issue.issue_type, func.count()).where(
                Issue.job_id == job_id, Issue.issue_type.in_(TAPADAS),
                Issue.url_id.in_(lote)).group_by(Issue.issue_type)):
            cuenta[tipo] += n
        session.execute(delete(Issue).where(
            Issue.job_id == job_id, Issue.issue_type.in_(TAPADAS), Issue.url_id.in_(lote)))
    for tipo, clave in sorted(grupos):
        quedan = session.execute(select(func.count()).where(
            Issue.job_id == job_id, Issue.issue_type == tipo, Issue.group_key == clave)).scalar()
        if quedan == 1:
            session.execute(delete(Issue).where(
                Issue.job_id == job_id, Issue.issue_type == tipo, Issue.group_key == clave))
            cuenta[f"{tipo}_sin_grupo"] += 1
    session.flush()
    return dict(cuenta)


def _guardar(session, job, resumen: dict) -> dict:
    if job is not None:
        from shared.models import Job

        previo = job.soft404 if isinstance(job.soft404, dict) else {}
        nuevo = {**previo, "resumen": resumen}
        session.execute(update(Job).where(Job.id == job.id).values(soft404=nuevo))
        session.flush()
    return resumen
