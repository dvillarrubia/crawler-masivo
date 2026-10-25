"""Coherencia del title y del marcado con el contenido (#16, #17; decision 177).

Tres modulos del analisis enriquecido, cada uno con UNA pregunta (decision 169:
una pregunta = un hallazgo):

- `title_contenido`: si el title dice de que va el contenido (score de cuatro
  niveles, `title_describe_contenido`).
- `title_generico`: si el title, quitada la marca, no identifica la pagina
  (`title_es_generico`). Lo que ya dice la regla `title_generico` (la lista
  cerrada de placeholders de la decision 121) no se pregunta.
- `schema_visible`: si el texto visible CONTRADICE un valor del marcado
  (precio, valoracion, fecha de un evento u oferta, disponibilidad;
  `schema_contradice_contenido`). Solo la contradiccion: que un valor no
  aparezca no se puede afirmar, porque lo que hay guardado es el contenido
  principal (`page_content.content_text`), no la pagina entera. Por eso
  tampoco van el autor ni la fecha de publicacion: el extractor de contenido
  quita la firma, y lo unico que se encontraria es el mismo nombre (nunca uno
  distinto), o sea nada que pueda salir «contradice».

Aqui viven los filtros (que URLs pregunta cada modulo, con su muestra por
plantilla) y los estados; las preguntas estan en `shared/preguntas/coherencia.py`
y el registro en `shared/enriquecido.py`. Las funciones de texto son puras y se
prueban sin base de datos. Este modulo no importa la capa (`shared.decisiones`)
hasta construir un `Estado`, y solo lo usa el analisis enriquecido.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Iterable
from urllib.parse import urlsplit

# --- Cuantas URLs se preguntan ------------------------------------------------
# Por debajo de estas palabras propias no hay contenido con el que comparar el
# title: eso es el soft 404 (decision 163), que usa el mismo corte.
CONTENIDO_MINIMO_PALABRAS = 50
# Muestra por plantilla de la alineacion title/contenido. Medido el 24-oct-2026
# con el estimador (decision 177): en Lopesan deja 1.661 de 3.062 candidatas
# (0,050 $ en vez de 0,093 $; recorta sobre todo los tres blogs, de 564 entradas
# cada uno) y en Penguin 3.695 de 27.385 (0,11 $ en vez de 0,83 $). Un fallo de
# plantilla sale igual con 100 ejemplos.
TITLE_POR_PLANTILLA = 100
# Un title generico es corto: quitada la marca, como mucho estas palabras.
# «Detalle noticia», «SAT», «| Penguin Libros ES». Uno de cinco palabras o mas ya
# nombra algo (y si no, es otro problema: un title mal escrito, no generico).
GENERICO_MAX_PALABRAS = 4
# Titles DISTINTOS por plantilla en `title_generico`. Sin esto, las 30.000
# fichas de libro de Penguin, todas de 2-4 palabras.
GENERICO_POR_PLANTILLA = 50
# Y de cada title, hasta estas URLs (las de menor url_hash). El estado lleva la
# ruta (revision de #242), asi que cada URL es una llamada: «SAT» en 67 fichas
# de ciudad se juzga igual con 3, y el resto lo dice `title_duplicate`.
GENERICO_URLS_POR_TITLE = 3
# Lo que se ensena de la ruta: «/sats/a-coruna-100» o «/productos/safira.html»
# dicen que es la pagina; mas alla de esto es un slug o parametros.
MAX_RUTA = 120
# Muestra por plantilla de `schema_visible`: el marcado lo pinta la plantilla, y
# un campo mal cableado sale en todas sus paginas.
SCHEMA_POR_PLANTILLA = 10
# Cuantas candidatas de una plantilla se miran como mucho para llenar su
# muestra de schema (las que no tienen nada visible con lo que comparar no
# cuentan): acota el trabajo del filtro en plantillas de 50.000 fichas.
SCHEMA_INTENTOS = 4

# --- Lo que entra en el estado de `schema_visible` ----------------------------
# Campos verificables contra lo visible. El nombre o el titular no: eso es la
# pregunta del title. Las fechas de un evento o de validez de una oferta si:
# «reserva hasta el 31 de octubre» frente a un `validThrough` de septiembre es
# el caso tipico. El autor y `datePublished` no (ver arriba): la firma no esta
# en `content_text`, y una fecha cualquiera del articulo no es la suya.
CAMPOS_PRECIO = ("price", "lowPrice", "highPrice", "priceCurrency", "priceRange")
CAMPOS_VALORACION = ("ratingValue", "reviewCount", "ratingCount")
CAMPOS_FECHA = ("startDate", "endDate", "validThrough")
CAMPOS_DISPONIBILIDAD = ("availability",)
MAX_HECHOS = 12
MAX_VALOR = 80
MAX_TEXTO_VISIBLE = 1000
VENTANA = 90
# Un valor que es una lista larga es un listado (el `datePublished` de las 36
# entradas de la portada de un blog), no un dato de ESTA pagina.
MAX_LISTA = 3
FORMATOS_SCHEMA = ("jsonld", "microdata")


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------
def muestra_por_plantilla(filas: Iterable[tuple[str, str, Any]], k: int) -> list[Any]:
    """De (plantilla, orden, id), hasta `k` ids por plantilla, los de menor
    `orden` (el `url_hash`: un SHA-256, reparto estable y la misma muestra si se
    repite, que entonces sale de cache)."""
    por: dict[str, list[tuple[str, Any]]] = {}
    for plantilla, orden, ident in filas:
        por.setdefault(plantilla, []).append((orden or "", ident))
    salida = []
    for plantilla in sorted(por):
        salida.extend(i for _, i in sorted(por[plantilla], key=lambda x: x[0])[:k])
    return salida


def sitio(host: str | None) -> str:
    h = (host or "").lower()
    return h[4:] if h.startswith("www.") else h


def es_portada(path: str | None) -> bool:
    """La home, tambien la de un idioma (`/es/`, `/pt-br`). Su title con la
    marca sola es su title logico: «que dice la portada de la empresa» es otro
    hallazgo (#199), no un title generico."""
    from shared.plantillas import es_idioma

    tramos = [t for t in (path or "/").strip("/").split("/") if t]
    return not tramos or (len(tramos) == 1 and es_idioma(tramos[0]))


def palabras_sin_marca(titulo: str, marcas: frozenset[str]) -> int:
    from shared.titulos import segmentos

    return sum(len(s.split()) for s in segmentos(titulo) if s not in marcas)


def solo_marca(titulo: str, marcas: frozenset[str]) -> bool:
    """El title es la marca y nada mas («Saunier Duval», «| Penguin Libros ES»):
    generico fuera de la portada y «solo el tema general» por construccion, asi
    que no se pregunta. Con dos segmentos distintos que pasan los dos por marca
    («Detalle | Acme» cuando casi todo el sitio se titula asi) no se sabe cual
    es la marca (la regla 121 dice lo mismo) y si se pregunta."""
    from shared.titulos import segmentos

    segs = set(segmentos(titulo))
    return len(segs) == 1 and next(iter(segs)) in marcas


def _tipo(item: dict) -> str:
    t = item.get("@type") or item.get("type") or "?"
    if isinstance(t, (list, tuple)):
        t = ", ".join(str(x) for x in t[:2])
    t = str(t)
    return t.rsplit("/", 1)[-1] or "?"


def _escalar(v: Any) -> str | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        v = " ".join(v.split())
        return v[:MAX_VALOR] if v else None
    return None


CAMPOS = CAMPOS_PRECIO + CAMPOS_VALORACION + CAMPOS_FECHA + CAMPOS_DISPONIBILIDAD


def hechos_schema(items: Iterable[dict]) -> list[dict]:
    """Los valores verificables de los bloques de marcado de una pagina:
    `[{"campo": "price", "ruta": "Offer.price", "valor": "116€", "nodo": "Offer",
    "grupo": 0}, ...]`.

    Se recorre cada bloque hasta cuatro niveles (`Product.offers.price`,
    `Hotel.starRating.ratingValue`). La disponibilidad se acorta a su nombre
    (`InStock`). Los valores de un mismo objeto (una oferta: su precio, su moneda
    y su disponibilidad) comparten `grupo`, porque van juntos: aplanados, una
    ficha con una talla agotada a 13,99 y otras en stock a 14,99 se leia como
    «price 13.99, price 14.99, availability InStock, availability OutOfStock» y
    Jev no sabia cual iba con cual (decision 185). Sin objetos repetidos con
    los mismos valores escritos igual (la oferta de los microdatos con `20.19€`
    no es la del JSON-LD con `20.19`: fundirlas se midio peor, ver la 185) y
    como mucho `MAX_HECHOS` objetos.
    """
    salida: list[dict] = []
    vistos: set = set()
    grupos = 0

    def anadir_objeto(nodo: str, valores: list[tuple[str, str]]):
        nonlocal grupos
        if not valores or grupos >= MAX_HECHOS:
            return
        # Un objeto de varios campos es el mismo aunque se declare en otra
        # ruta (la oferta del JSON-LD y la de los microdatos); un valor suelto
        # no (la nota de una resena no es la nota media).
        firma = (("objeto", frozenset(valores)) if len(valores) > 1
                 else (f"{nodo}.{valores[0][0]}", valores[0][1]))
        if firma in vistos:
            return
        vistos.add(firma)
        for campo, valor in valores:
            salida.append({"campo": campo, "ruta": f"{nodo}.{campo}", "valor": valor,
                           "nodo": nodo, "grupo": grupos})
        grupos += 1

    def recorrer(nodo: Any, ruta: str, nivel: int):
        if nivel > 4 or grupos >= MAX_HECHOS:
            return
        if isinstance(nodo, (list, tuple)):
            for x in list(nodo)[:5]:
                recorrer(x, ruta, nivel)
            return
        if not isinstance(nodo, dict):
            return
        propios: list[tuple[str, str]] = []
        hijos: list[tuple[Any, str]] = []
        for clave, valor in nodo.items():
            if clave.startswith("@"):
                continue
            if clave in CAMPOS:
                if isinstance(valor, (list, tuple)):
                    if len(valor) > MAX_LISTA:
                        continue
                    valores = [_escalar(x) for x in valor]
                else:
                    valores = [_escalar(valor)]
                for v in valores:
                    if v:
                        if clave == "availability":
                            v = v.rstrip("/").rsplit("/", 1)[-1]
                        if (clave, v) not in propios:
                            propios.append((clave, v))
            elif isinstance(valor, (dict, list, tuple)):
                hijos.append((valor, f"{ruta}.{clave}"))
        anadir_objeto(ruta, propios)
        for valor, sub in hijos:
            recorrer(valor, sub, nivel + 1)

    def bloques(item: Any):
        # Un `@graph` (el JSON-LD de Yoast o de La Salle) son varios bloques.
        if isinstance(item, dict) and isinstance(item.get("@graph"), list):
            for x in item["@graph"]:
                yield from bloques(x)
        elif isinstance(item, dict):
            yield item

    for item in items:
        for b in bloques(item):
            recorrer(b, _tipo(b), 1)
    return salida


_MESES = (
    "enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre diciembre "
    "january february march april may june july august september october november december "
    "januar februar märz maerz mai juni juli oktober dezember "
    "janvier février fevrier mars avril juin juillet août aout septembre octobre novembre décembre "
    "gennaio febbraio aprile maggio giugno luglio settembre ottobre dicembre "
    "janeiro fevereiro março marco maio junho julho setembro outubro dezembro "
    "gener febrer abril maig juny juliol agost setembre octubre novembre desembre"
).split()
_RE_MESES = "|".join(sorted({re.escape(m) for m in _MESES}, key=len, reverse=True))
PATRONES = {
    "precio": re.compile(
        r"(?:[€$£]\s?\d[\d.,]*|\d[\d.,]*\s?(?:€|eur\b|euros?\b|usd\b|\$|£))", re.I),
    "valoracion": re.compile(
        r"(?:\b\d(?:[.,]\d{1,2})?\s?(?:/|de|of|von|sur|su)\s?(?:5|10)\b"
        r"|\b\d(?:[.,]\d)?\s?(?:estrellas|stars?|sterne|étoiles|stelle|★)"
        r"|\b\d[\d.]*\s?(?:opiniones|reseñas|valoraciones|reviews|bewertungen|avis))", re.I),
    "fecha": re.compile(
        rf"(?:\b\d{{1,2}}[./-]\d{{1,2}}[./-]\d{{2,4}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b"
        rf"|\b\d{{1,2}}\.?\s(?:de\s)?(?:{_RE_MESES})\b(?:\s(?:de\s)?\d{{4}})?"
        rf"|\b(?:{_RE_MESES})\s\d{{1,2}}(?:,\s?\d{{4}})?)", re.I),
    "disponibilidad": re.compile(
        r"\b(?:agotado|sin stock|disponible|en stock|sold out|out of stock|in stock|available|"
        r"ausverkauft|verfügbar|épuisé|disponible|esaurito|disponibile|esgotado|esgotat)\b", re.I),
}
_CLASE = {**{c: "precio" for c in CAMPOS_PRECIO}, **{c: "valoracion" for c in CAMPOS_VALORACION},
          **{c: "fecha" for c in CAMPOS_FECHA}, "availability": "disponibilidad"}
_RE_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")


def _patron_de_valor(hecho: dict) -> re.Pattern | None:
    """Lo que se busca en el texto para un valor concreto: sus numeros (un
    precio o una valoracion, con la coma o el punto cambiados). Las fechas no:
    «10» y «31» casan en cualquier sitio, y para eso esta el patron generico."""
    campo, valor = hecho["campo"], hecho["valor"]
    if _CLASE.get(campo) not in ("precio", "valoracion"):
        return None
    numeros = []
    for n in _RE_NUMERO.findall(valor):
        # Un «3» suelto (las estrellas de un hotel) casa en cualquier parte:
        # para eso esta el patron generico («3 estrellas», «4,5/5»).
        if len(n) < 2:
            continue
        numeros.append(re.escape(n))
        if "." in n or "," in n:
            numeros.append(re.escape(n.translate(str.maketrans(".,", ",."))))
    if not numeros:
        return None
    return re.compile(rf"(?<![\d.,])(?:{'|'.join(numeros)})(?![\d])")


def fragmentos_visibles(texto: str | None, hechos: list[dict]) -> str:
    """Los trozos del texto visible donde podria estar cada valor del marcado:
    alrededor de sus numeros, y de cualquier precio,
    valoracion, fecha o disponibilidad si el marcado declara uno de esa clase.
    Vacio = no hay nada visible con lo que comparar, y la pagina no se pregunta.

    El presupuesto de `MAX_TEXTO_VISIBLE` caracteres se reparte por prioridad:
    primero las ventanas de lo que casa con un valor declarado y despues las
    genericas, cada una entera o recortada alrededor de su coincidencia, nunca
    cortando el resultado al final. Un corte final se comia la cola del texto, y
    con ella el precio declarado cuando salia detras de otros precios: Jev veia
    `Offer.price: 178` frente a «99 / 120 / 135 / 150» y contestaba que
    contradice (revision de #242). Se devuelven en el orden del texto, unidos
    con « … ».
    """
    if not texto or not hechos:
        return ""
    tramos: list[tuple[int, int]] = []
    for h in hechos:
        p = _patron_de_valor(h)
        if p:
            tramos += [(m.start(), m.end()) for m in p.finditer(texto)][:3]
    clases = {_CLASE[h["campo"]] for h in hechos if h["campo"] in _CLASE}
    genericos: list[tuple[int, int]] = []
    for c in sorted(clases):
        genericos += [(m.start(), m.end()) for m in PATRONES[c].finditer(texto)][:4]
    if not tramos and not genericos:
        return ""
    sep = " … "

    def fundir(ventanas: list[list[int]]) -> list[list[int]]:
        salida: list[list[int]] = []
        for a, b in sorted(ventanas):
            if salida and a <= salida[-1][1]:
                salida[-1][1] = max(salida[-1][1], b)
            else:
                salida.append([a, b])
        return salida

    def largo(ventanas: list[list[int]]) -> int:
        # Cota superior del resultado: normalizar espacios solo acorta.
        f = fundir(ventanas)
        return sum(b - a for a, b in f) + len(sep) * max(0, len(f) - 1)

    elegidas: list[list[int]] = []
    for a, b in tramos + genericos:
        va, vb = max(0, a - VENTANA), min(len(texto), b + VENTANA)
        if largo(elegidas + [[va, vb]]) > MAX_TEXTO_VISIBLE:
            # Recortada alrededor de su coincidencia hasta lo que quede; si no
            # cabe ni la coincidencia con algo de contexto, fuera.
            holgura = MAX_TEXTO_VISIBLE - largo(elegidas + [[a, b]])
            if holgura < 20:
                continue
            va, vb = max(0, a - holgura // 2), min(len(texto), b + holgura // 2)
            if largo(elegidas + [[va, vb]]) > MAX_TEXTO_VISIBLE:
                continue
        elegidas.append([va, vb])
    fundidas = fundir(elegidas)
    trozos = [" ".join(texto[a:b].split()) for a, b in fundidas]
    return sep.join(t for t in trozos if t)


# ---------------------------------------------------------------------------
# Lectura de la base de datos
# ---------------------------------------------------------------------------
def _trozos(lista: list, n: int = 500):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def _plantillas(session, job_id, url_ids: list[int]) -> dict[int, dict]:
    """Por URL: host, ruta, url_hash, palabras y su plantilla (con el host
    delante, como `extraer`: dos sitios con /blog/:slug son dos plantillas). Las
    reglas de plantilla son las del cliente (`config.templates`)."""
    from shared.models import Job, Url
    from shared.plantillas import plantilla_de, reglas_de_plantilla

    job = session.get(Job, _uuid(job_id))
    reglas = reglas_de_plantilla(((job.config if job else None) or {}).get("templates"))
    datos: dict[int, dict] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, url, host, path, url_hash, palabras in session.query(
                Url.id, Url.url, Url.host, Url.path, Url.url_hash, Url.content_word_count).filter(
                Url.id.in_(trozo)):
            host = host or urlsplit(url or "").hostname or ""
            datos[uid] = {"host": host, "path": path or "/", "url_hash": url_hash or "",
                          "palabras": palabras,
                          "plantilla": f"{host}|{plantilla_de(path or '/', reglas)}"}
    return datos


def _titulos(session, url_ids: list[int]) -> dict[int, str]:
    from shared.models import HtmlMeta

    salida: dict[int, str] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, t in session.query(HtmlMeta.url_id, HtmlMeta.title).filter(
                HtmlMeta.url_id.in_(trozo)):
            salida[uid] = (t or "").strip()
    return salida


def _marcas(session, job_id) -> dict[str, frozenset[str]]:
    """La marca de cada host, como la calcula la regla `title_generico`: de los
    titles de las indexables del job, no solo de las del alcance."""
    from shared.models import HtmlMeta, Url
    from shared.titulos import marcas_del_host

    por_host: dict[str, list[str]] = {}
    for host, t in (session.query(Url.host, HtmlMeta.title)
                    .join(HtmlMeta, HtmlMeta.url_id == Url.id)
                    .filter(Url.job_id == _uuid(job_id), Url.is_internal.is_(True),
                            Url.is_html.is_(True), Url.status_code == 200,
                            Url.indexable.is_(True))):
        if (t or "").strip():
            por_host.setdefault(host or "", []).append(t.strip())
    return {h: marcas_del_host(ts) for h, ts in por_host.items()}


def _con_issue(session, job_id, url_ids: list[int], tipo: str) -> set[int]:
    from shared.models import Issue

    salida: set[int] = set()
    for trozo in _trozos(list(url_ids)):
        salida |= {uid for (uid,) in session.query(Issue.url_id).filter(
            Issue.job_id == _uuid(job_id), Issue.issue_type == tipo, Issue.url_id.in_(trozo))}
    return salida


def _con_contenido(session, url_ids: list[int]) -> set[int]:
    from sqlalchemy import func

    from shared.models import PageContent

    salida: set[int] = set()
    for trozo in _trozos(list(url_ids)):
        salida |= {uid for (uid,) in session.query(PageContent.url_id).filter(
            PageContent.url_id.in_(trozo), func.length(PageContent.content_text) > 0)}
    return salida


# ---------------------------------------------------------------------------
# title_contenido
# ---------------------------------------------------------------------------
def candidatos_title_contenido(session, job_id, url_ids: list[int]) -> list[int]:
    """Las URLs cuyo title se compara con su contenido.

    Fuera lo que ya resuelve una regla: sin title (`title_missing`), el
    placeholder de `title_generico` (decision 121) y los duplicados exactos
    (`title_duplicate`: ese title hay que cambiarlo igual, describa o no su
    pagina). Fuera tambien lo que no tiene contenido con el que comparar: sin
    `content_text` o con menos de `CONTENIDO_MINIMO_PALABRAS` palabras propias,
    ni el title que es solo la marca (`solo_marca`). Del resto, `TITLE_POR_PLANTILLA` por plantilla.
    """
    from shared.titulos import es_title_generico

    if not url_ids:
        return []
    info = _plantillas(session, job_id, url_ids)
    titulos = _titulos(session, url_ids)
    marcas = _marcas(session, job_id)
    duplicados = _con_issue(session, job_id, url_ids, "title_duplicate")
    con_texto = _con_contenido(session, url_ids)
    filas = []
    for uid in url_ids:
        d, t = info.get(uid), titulos.get(uid, "")
        if not d or not t or uid in duplicados or uid not in con_texto:
            continue
        if d["palabras"] is not None and d["palabras"] < CONTENIDO_MINIMO_PALABRAS:
            continue
        m = marcas.get(d["host"], frozenset())
        if es_title_generico(t, m) or solo_marca(t, m):
            continue
        filas.append((d["plantilla"], d["url_hash"], uid))
    elegidas = set(muestra_por_plantilla(filas, TITLE_POR_PLANTILLA))
    return [u for u in url_ids if u in elegidas]


def estados_title_contenido(session, url_ids: list[int]):
    """Title, primer h1 visible y el principio del contenido principal: el
    estado de `pagina_legal`, que es justo lo que hay que comparar."""
    from shared.enriquecido import estados_titulo_h1_contenido

    return estados_titulo_h1_contenido(session, url_ids)


# ---------------------------------------------------------------------------
# title_generico
# ---------------------------------------------------------------------------
def candidatos_title_generico(session, job_id, url_ids: list[int]) -> list[int]:
    """Las URLs cuyo title puede no identificar la pagina: con title, que no es
    ya un placeholder de la regla, que no es la portada y que, quitada la marca,
    tiene como mucho `GENERICO_MAX_PALABRAS` palabras. El que es solo la marca
    es generico por definicion (`solo_marca`): no se pregunta.

    Los duplicados exactos SI entran: que 40 paginas digan «SAT» lo dice
    `title_duplicate`, no si «SAT» dice de que va cada una. Por plantilla se
    cogen `GENERICO_POR_PLANTILLA` titles distintos y de cada uno hasta
    `GENERICO_URLS_POR_TITLE` URLs (el estado lleva la ruta: cada una es una
    llamada).
    """
    from shared.titulos import es_title_generico

    if not url_ids:
        return []
    info = _plantillas(session, job_id, url_ids)
    titulos = _titulos(session, url_ids)
    marcas = _marcas(session, job_id)
    grupos: dict[tuple[str, tuple[str, str]], list[tuple[str, int]]] = {}
    for uid in url_ids:
        d, t = info.get(uid), titulos.get(uid, "")
        if not d or not t or es_portada(d["path"]):
            continue
        m = marcas.get(d["host"], frozenset())
        if es_title_generico(t, m):
            continue
        if solo_marca(t, m) or palabras_sin_marca(t, m) > GENERICO_MAX_PALABRAS:
            continue
        clave = (sitio(d["host"]), t.lower())
        grupos.setdefault((d["plantilla"], clave), []).append((d["url_hash"], uid))
    for g in grupos.values():
        g.sort()
    elegidos = muestra_por_plantilla(
        ((p, g[0][0], (p, clave)) for (p, clave), g in grupos.items()), GENERICO_POR_PLANTILLA)
    elegidas = {uid for k in elegidos for _, uid in grupos[k][:GENERICO_URLS_POR_TITLE]}
    return [u for u in url_ids if u in elegidas]


def ruta_visible(path: str | None) -> str:
    """La ruta como la leeria una persona: sin codificar y corta."""
    from urllib.parse import unquote

    r = unquote(path or "/")
    return r if len(r) <= MAX_RUTA else r[:MAX_RUTA] + "…"


def estados_title_generico(session, url_ids: list[int]):
    """El title, el dominio y la ruta. Solo con el title la definicion no
    separaba los casos (revision de #242): «SAT» es correcto en la pagina del
    servicio y generico en la de A Coruña (/sats/a-coruna-100), y «SafiraAqua» o
    «Culpa» parecen un codigo hasta que la ruta dice que son un producto o un
    libro. Sin el contenido: si el title lo describe es `title_contenido`."""
    from shared.decisiones import Estado
    from shared.models import Url

    titulos = _titulos(session, url_ids)
    hosts: dict[int, str] = {}
    rutas: dict[int, str] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, host, url, path in session.query(Url.id, Url.host, Url.url, Url.path).filter(
                Url.id.in_(trozo)):
            hosts[uid] = host or urlsplit(url or "").hostname or ""
            rutas[uid] = path or urlsplit(url or "").path or "/"
    return [Estado(datos={"titulo": titulos.get(u, ""), "sitio": sitio(hosts.get(u)),
                          "ruta": ruta_visible(rutas.get(u))}, url_id=u)
            for u in url_ids]


# ---------------------------------------------------------------------------
# schema_visible
# ---------------------------------------------------------------------------
def _con_marcado(session, url_ids: list[int]) -> set[int]:
    from shared.models import StructuredData

    salida: set[int] = set()
    for trozo in _trozos(list(url_ids)):
        salida |= {uid for (uid,) in session.query(StructuredData.url_id).filter(
            StructuredData.url_id.in_(trozo), StructuredData.format.in_(FORMATOS_SCHEMA)).distinct()}
    return salida


def datos_schema(session, url_ids: list[int]) -> dict[int, dict]:
    """Por URL: title, h1, los valores verificables del marcado (JSON-LD y
    microdatos; el RDFa que hay en produccion es FOAF/SIOC de Drupal, sin
    precios ni valoraciones), los fragmentos del contenido donde mirar y el
    texto que sigue al h1 (`tras_h1`, decision 187: en una ficha, la caja de
    compra; vacio en lo rastreado antes)."""
    from shared.models import Heading, PageContent, StructuredData

    items: dict[int, list] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, raw in (session.query(StructuredData.url_id, StructuredData.raw)
                         .filter(StructuredData.url_id.in_(trozo),
                                 StructuredData.format.in_(FORMATOS_SCHEMA))
                         .order_by(StructuredData.url_id, StructuredData.id)):
            if isinstance(raw, dict):
                items.setdefault(uid, []).append(raw)
            elif isinstance(raw, list):
                items.setdefault(uid, []).extend(x for x in raw if isinstance(x, dict))
    hechos = {u: hechos_schema(v) for u, v in items.items()}
    con_hechos = [u for u in url_ids if hechos.get(u)]
    titulos = _titulos(session, con_hechos)
    h1: dict[int, str] = {}
    textos: dict[int, str] = {}
    tras_h1: dict[int, str] = {}
    for trozo in _trozos(con_hechos):
        for uid, texto in (session.query(Heading.url_id, Heading.text)
                           .filter(Heading.url_id.in_(trozo), Heading.tag == "h1",
                                   Heading.oculto.isnot(True))
                           .order_by(Heading.url_id, Heading.position)):
            h1.setdefault(uid, texto or "")
        for uid, texto, tras in session.query(
                PageContent.url_id, PageContent.content_text, PageContent.texto_tras_h1).filter(
                PageContent.url_id.in_(trozo)):
            textos[uid] = texto or ""
            # Una linea por bloque, unidas con « / » como se leen.
            tras_h1[uid] = " / ".join(" ".join(ln.split()) for ln in (tras or "").splitlines()
                                      if ln.strip())
    return {u: {"titulo": titulos.get(u, ""), "h1": h1.get(u, ""), "hechos": hechos[u],
                "texto_visible": fragmentos_visibles(textos.get(u), hechos[u]),
                "tras_h1": tras_h1.get(u, "")}
            for u in con_hechos}


def candidatos_schema(session, job_id, url_ids: list[int]) -> list[int]:
    """Las URLs con algun valor verificable en el marcado Y algo visible con lo
    que compararlo. Sin lo segundo no se puede afirmar nada (lo guardado es el
    contenido principal: el precio del lateral no esta), y
    no se pregunta. `SCHEMA_POR_PLANTILLA` por plantilla, mirando como mucho
    `SCHEMA_INTENTOS` veces esa cifra de candidatas en cada una."""
    if not url_ids:
        return []
    marcadas = _con_marcado(session, url_ids)
    if not marcadas:
        return []
    info = _plantillas(session, job_id, [u for u in url_ids if u in marcadas])
    por: dict[str, list[tuple[str, int]]] = {}
    for uid, d in info.items():
        por.setdefault(d["plantilla"], []).append((d["url_hash"], uid))
    elegidas: set[int] = set()
    for plantilla in sorted(por):
        cola = [u for _, u in sorted(por[plantilla])][:SCHEMA_POR_PLANTILLA * SCHEMA_INTENTOS]
        dentro = 0
        for trozo in _trozos(cola, SCHEMA_POR_PLANTILLA):
            datos = datos_schema(session, trozo)
            for u in trozo:
                d = datos.get(u, {})
                if dentro < SCHEMA_POR_PLANTILLA and (d.get("texto_visible") or d.get("tras_h1")):
                    elegidas.add(u)
                    dentro += 1
            if dentro >= SCHEMA_POR_PLANTILLA:
                break
    return [u for u in url_ids if u in elegidas]


def lineas_marcado(hechos: list[dict]) -> list[str]:
    """Una linea por objeto del marcado: «Tipo.campo: valor» si tiene un solo
    valor (como antes de la decision 185) y «Tipo.ruta: campo valor, campo
    valor» si tiene varios, para que el precio y la disponibilidad de cada
    oferta se lean juntos."""
    por: dict[int, list[dict]] = {}
    for h in hechos:
        por.setdefault(h.get("grupo", id(h)), []).append(h)
    lineas = []
    for hs in por.values():
        if len(hs) == 1:
            lineas.append(f"{hs[0]['ruta']}: {hs[0]['valor']}")
        else:
            lineas.append(f"{hs[0]['nodo']}: " + ", ".join(f"{h['campo']} {h['valor']}" for h in hs))
    return lineas


def estados_schema(session, url_ids: list[int]):
    """Title y h1 (de que cosa habla la pagina), los valores del marcado por
    objeto (`lineas_marcado`), el texto que sigue al h1 (decision 187; v6,
    decision 188) y los fragmentos visibles. Nunca el JSON-LD entero."""
    from shared.decisiones import Estado

    datos = datos_schema(session, url_ids)
    salida = []
    for u in url_ids:
        d = datos.get(u) or {"titulo": "", "h1": "", "hechos": [], "texto_visible": "",
                             "tras_h1": ""}
        salida.append(Estado(datos={
            "titulo": d["titulo"], "h1": d["h1"],
            "marcado": lineas_marcado(d["hechos"]),
            "tras_h1": d["tras_h1"],
            "texto_visible": d["texto_visible"]}, url_id=u))
    return salida


__all__ = [
    "candidatos_title_contenido", "estados_title_contenido", "candidatos_title_generico",
    "estados_title_generico", "candidatos_schema", "estados_schema", "hechos_schema", "lineas_marcado",
    "fragmentos_visibles", "muestra_por_plantilla", "datos_schema", "es_portada",
    "palabras_sin_marca", "sitio",
]
