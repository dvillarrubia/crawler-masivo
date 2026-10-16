"""Soft 404 determinista: reglas puras que comparten el spider, el analizador y
el script de sondas (decision 105).

Un soft 404 es una pagina que responde **200** pero es una pagina de error: el
servidor dice «aqui hay algo» y lo que sirve es «esto no existe». Google la
detecta por su cuenta, la saca del indice y la cuenta en Search Console como
«Soft 404»; si ademas estaba en el sitemap, como «La URL enviada parece un soft
404», que la herramienta archivaba como **error**. Para el sitio, cada enlace
interno a una de estas paginas es un enlace roto disfrazado.

Aqui no se decide nada que necesite un modelo. Tres familias de senales:

1. **La huella de la sonda.** Al empezar el rastreo se pide una URL que no
   puede existir (`mangle_path`) por host y por plantilla de ruta. Si el sitio
   responde 200 —o 404 con su pagina de error—, lo que sirve ES su pagina de
   error, y cualquier pagina del censo con el mismo contenido tambien lo es.
2. **El titular dice que es un error**: «Página no encontrada», «404», «Not
   found»… en es/en/ca/eu/fr/de/pt/it (`senal_titulo`).
3. **Una busqueda interna sin resultados** (`es_busqueda` +
   `frase_sin_resultados`). Google la pone de ejemplo de soft 404 en su
   documentacion.

Lo que no llega a afirmarse queda como CANDIDATO para la fase con modelo
(#11): un titular que solo contiene la frase («Cómo arreglar el error 404»),
un listado vacio sin URL de busqueda, un producto descatalogado.

Vive en `shared/` porque la huella tiene que calcularse igual en el spider
(sonda), en el script que sondea censos ya hechos y en el analizador (paginas):
dos copias de «como se normaliza un texto» acaban discrepando (decisiones 23 y
39). Sin dependencias fuera de la biblioteca estandar: la imagen de la API
copia `shared/`.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Sonda
# ---------------------------------------------------------------------------

# Una URL de la que el sitio no puede tener pagina. Determinista por job y por
# clave: reanudar el rastreo o repetir la sonda pide la MISMA URL, y en los
# logs del servidor del cliente se reconoce como una sola cosa. Sin palabras:
# un buscador interno que salta con cualquier slug desconocido («/no-existe» ->
# busqueda de «no existe») daria un listado, no la pagina de error.
_PREFIJO_TOKEN = "zz"


def token_sonda(job_id: str, clave: str) -> str:
    h = hashlib.sha1(f"{job_id}|{clave}".encode("utf-8")).hexdigest()[:14]
    return f"{_PREFIJO_TOKEN}{h}"


def mangle_path(url: str, token: str) -> str:
    """La URL hermana de `url` que no existe: el ultimo tramo cambiado por `token`.

    Conserva lo que hace que el servidor la enrute a la MISMA plantilla: los
    tramos padre, la extension del ultimo (`.html`, `.php`) y la barra final.
    Quita la query y el fragmento, que no son de la plantilla. La raiz da
    `/<token>`: la pagina de error generica del host.
    """
    partes = urlsplit(url)
    ruta = partes.path or "/"
    barra = ruta.endswith("/") and ruta != "/"
    tramos = [t for t in ruta.split("/") if t]
    if not tramos:
        nueva = f"/{token}"
    else:
        ultimo = tramos[-1]
        _, ext = posixpath.splitext(ultimo)
        # Solo una extension de verdad: `v1.2-guia` no es un fichero `.2-guia`.
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext or ""):
            ext = ""
        tramos[-1] = token + ext
        nueva = "/" + "/".join(tramos) + ("/" if barra and not ext else "")
    return urlunsplit((partes.scheme, partes.netloc, nueva, "", ""))


# ---------------------------------------------------------------------------
# Normalizacion y huella
# ---------------------------------------------------------------------------

_APOSTROFES = str.maketrans({"’": "'", "‘": "'", "´": "'", "`": "'"})
_NO_PALABRA = re.compile(r"[^\w']+", re.UNICODE)


def normalizar(texto: str | None) -> str:
    """Minusculas, NFKC, apostrofes unificados y espacios colapsados."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKC", texto).translate(_APOSTROFES).lower()
    return " ".join(t.split())


def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto)
                   if not unicodedata.combining(c))


def _plano(texto: str | None) -> str:
    """Para casar frases: sin acentos ni puntuacion (salvo el apostrofe)."""
    return " ".join(_NO_PALABRA.sub(" ", sin_acentos(normalizar(texto))).split())


def _ecos(url: str) -> list[str]:
    """Las formas en que una pagina de error repite la URL que se le pidio.

    «La página /es/hoteles/zz1234 no existe»: sin quitarlo, dos paginas de
    error identicas tienen textos distintos, uno por URL. Solo trozos de 4
    caracteres o mas: quitar «es» de un texto no es quitar un eco.
    """
    partes = urlsplit(url)
    ruta = partes.path or ""
    ultimo = [t for t in ruta.split("/") if t][-1:] or [""]
    candidatos = {
        url, unquote(url), ruta, unquote(ruta), partes.netloc + ruta,
        ultimo[0], unquote(ultimo[0]),
        posixpath.splitext(unquote(ultimo[0]))[0].replace("-", " ").replace("_", " "),
    }
    return sorted((normalizar(c) for c in candidatos if c and len(c) >= 4),
                  key=len, reverse=True)


def quitar_eco(texto: str | None, url: str | None) -> str:
    t = normalizar(texto)
    if not t or not url:
        return t
    for eco in _ecos(url):
        if eco:
            t = t.replace(eco, " ")
    return " ".join(t.split())


@dataclass(frozen=True)
class Huella:
    """Lo que identifica el contenido de una respuesta, sin el eco de su URL."""

    hash: str | None        # SHA-256 del contenido normalizado; None si vacio
    palabras: int
    texto: str              # contenido normalizado (para MinHash)
    titulo: str             # titulo normalizado sin eco


def huella(texto: str | None, url: str | None, titulo: str | None = None) -> Huella:
    t = quitar_eco(texto, url)
    palabras = len([p for p in _NO_PALABRA.split(t) if p])
    return Huella(
        hash=hashlib.sha256(t.encode("utf-8")).hexdigest() if t else None,
        palabras=palabras,
        texto=t,
        titulo=quitar_eco(titulo, url),
    )


def misma_huella(sonda: Huella, pagina: Huella, min_palabras: int) -> str | None:
    """Igualdad exacta de contenido; devuelve el metodo o None.

    Por debajo de `min_palabras` el contenido solo no basta —dos paginas con
    una linea igual coinciden por casualidad— y se exige tambien el titulo.
    Sin contenido no se afirma nada: un vacio es igual a otro vacio.
    """
    if not sonda.hash or sonda.hash != pagina.hash:
        return None
    if sonda.palabras >= min_palabras:
        return "contenido_identico"
    if sonda.titulo and sonda.titulo == pagina.titulo:
        return "contenido_y_titulo_identicos"
    return None


# ---------------------------------------------------------------------------
# El titular dice que es un error
# ---------------------------------------------------------------------------

# Frases de pagina de error, ya sin acentos ni puntuacion (`_plano`). Al menos
# es/en/ca/eu/fr/de/pt/it. Solo frases que en un titular significan «esto no
# existe»: «error» a secas no (hay articulos sobre errores) y «oops» tampoco.
FRASES_ERROR: tuple[str, ...] = tuple(sorted({
    # es
    "pagina no encontrada", "no se ha encontrado la pagina", "no se encontro la pagina",
    "la pagina no existe", "esta pagina no existe", "la pagina que buscas no existe",
    "la pagina solicitada no existe", "pagina no disponible", "contenido no encontrado",
    "no encontrado", "no encontrada", "error 404",
    # en
    "page not found", "not found", "404 not found", "page cannot be found",
    "page could not be found", "this page does not exist", "this page doesn't exist",
    "page does not exist", "the page you requested could not be found",
    "nothing found", "error 404", "404 error", "page not available", "notfound",
    # ca
    "pagina no trobada", "no s'ha trobat la pagina", "la pagina no existeix",
    "no trobat", "no trobada", "error 404",
    # eu
    "orria ez da aurkitu", "orrialdea ez da aurkitu", "ez da aurkitu", "ez da orria aurkitu",
    # fr
    "page non trouvee", "page introuvable", "introuvable", "erreur 404",
    "cette page n'existe pas", "la page demandee n'existe pas",
    # de
    "seite nicht gefunden", "nicht gefunden", "fehler 404", "seite existiert nicht",
    "diese seite existiert nicht",
    # pt
    "pagina nao encontrada", "nao encontrada", "nao encontrado", "erro 404",
    "esta pagina nao existe",
    # it
    "pagina non trovata", "non trovata", "non trovato", "errore 404",
    "la pagina non esiste", "pagina non esistente",
}, key=len, reverse=True))

_RE_404_SOLO = re.compile(r"(error |erreur |fehler |erro |errore )?404( error| erreur| fehler)?")
# Separadores de MARCA en el titulo: «Página no encontrada | Marca», «404 -
# Not found». Los dos puntos NO: separan titulo y subtitulo de un articulo, y
# partiendo por ellos «Inauguración JJOO 2012: ERROR 404» (un post sobre la
# ceremonia, en blogs.uoc.edu) daba un segmento «error 404» y salia seguro.
_RE_SEPARADOR = re.compile(r"\s*[|–—·»]\s*|\s+-\s+|\s+/\s+")


# Una frase sin «página»/«page»/«404» no dice de QUE no se encontro nada.
_NUCLEO = frozenset({"pagina", "page", "seite", "orria", "orrialdea", "404", "contenido"})
_RELLENO = frozenset({"404", "error", "erreur", "fehler", "erro", "errore", "oops", "ups",
                      "vaya", "sorry", "lo", "sentimos", "ay"})


_RE_ALGUNA_ERROR = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(f) for f in FRASES_ERROR) + r"|404)(?!\w)")


def _es_corta(frase: str) -> bool:
    return not (_NUCLEO & set(frase.split()))


def _contiene(texto: str, frase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(frase)}(?!\w)", texto) is not None


def _fuerza_de_segmento(segmento: str) -> tuple[str, str] | None:
    s = _plano(segmento)
    if not s:
        return None
    if _RE_404_SOLO.fullmatch(s):
        return "seguro", s
    if not _RE_ALGUNA_ERROR.search(s):
        return None  # lo normal: un filtro barato antes del bucle
    for frase in FRASES_ERROR:
        if _contiene(s, frase):
            if _es_corta(frase):
                # «No encontrado» a secas tambien es el final de un titulo de
                # libro («Paraíso no encontrado»): solo es seguro si el
                # segmento no dice nada mas que la frase y relleno de error.
                resto = [w for w in s.split() if w not in _RELLENO]
                return ("seguro" if " ".join(resto) == frase else "dudoso"), frase
            # El segmento ES la frase, o la frase con una interjeccion
            # («¡Ups! Página no encontrada»): seguro. Si la frase es solo una
            # parte de un titular largo («Cómo arreglar el error 404 en
            # WordPress»), es un articulo sobre errores, no un error: dudoso.
            extra = len(s.split()) - len(frase.split())
            return ("seguro" if extra <= 2 else "dudoso"), frase
    if re.search(r"(?<!\w)404(?!\w)", s):
        return "dudoso", "404"
    return None


def senal_titulo(titulo: str | None, h1: str | None) -> tuple[str, str, str] | None:
    """(«seguro»|«dudoso», campo, frase) si el titulo o el h1 dicen «error».

    El titulo se mira entero y por segmentos (lo que separan `|`, `-`…)
    porque la marca va pegada: «Página no encontrada | Lopesan» es un error de
    pagina, con la marca al lado. El h1 no lleva marca: se mira entero.
    """
    mejor: tuple[str, str, str] | None = None
    for campo, valor in (("h1", h1), ("title", titulo)):
        if not valor:
            continue
        segmentos = [valor] if campo == "h1" else [valor, *_RE_SEPARADOR.split(valor)]
        for segmento in segmentos:
            r = _fuerza_de_segmento(segmento)
            if r is None:
                continue
            if r[0] == "seguro":
                return r[0], campo, r[1]
            mejor = mejor or (r[0], campo, r[1])
    return mejor


# ---------------------------------------------------------------------------
# Busqueda interna sin resultados
# ---------------------------------------------------------------------------

PARAMETROS_BUSQUEDA = frozenset({
    "s", "q", "query", "search", "search_query", "searchterm", "buscar", "busqueda",
    "keyword", "keywords", "k", "term", "termino", "cerca", "recherche", "suche",
    "pesquisa", "ricerca", "bilatu",
})
TRAMOS_BUSQUEDA = frozenset({
    "search", "buscar", "busqueda", "buscador", "busca", "cerca", "cercar", "recherche",
    "suche", "pesquisa", "ricerca", "bilatu", "bilaketa", "catalogsearch", "resultados",
    "search-results",
})

FRASES_SIN_RESULTADOS: tuple[str, ...] = tuple(sorted({
    # es
    "no se han encontrado resultados", "no se encontraron resultados", "no hay resultados",
    "sin resultados", "0 resultados", "ningun resultado", "no hemos encontrado resultados",
    "no se ha encontrado ningun resultado", "no hay productos que coincidan",
    "no se han encontrado productos", "no hemos encontrado ningun",
    # en
    "no results found", "no results for", "0 results", "nothing found", "did not match any",
    "no products were found", "no matches found", "your search returned no results",
    "no search results", "no results",
    # ca
    "no s'han trobat resultats", "cap resultat", "sense resultats", "0 resultats",
    "no s'ha trobat cap resultat",
    # eu
    "ez da emaitzarik aurkitu", "ez dago emaitzarik", "emaitzarik ez",
    # fr
    "aucun resultat", "0 resultat", "aucun produit ne correspond", "aucun resultat trouve",
    # de
    "keine ergebnisse", "keine treffer", "0 ergebnisse", "keine produkte gefunden",
    # pt
    "nenhum resultado", "sem resultados", "nenhum produto encontrado",
    # it
    "nessun risultato", "0 risultati", "nessun prodotto trovato",
}, key=len, reverse=True))


def es_busqueda(url: str) -> bool:
    """True si la URL es una pagina de resultados del buscador interno."""
    partes = urlsplit(url)
    for clave, valor in parse_qsl(partes.query, keep_blank_values=True):
        if clave.lower() in PARAMETROS_BUSQUEDA:
            return True
    tramos = [t.lower() for t in (partes.path or "").split("/") if t]
    return any(t in TRAMOS_BUSQUEDA for t in tramos)


# Una sola alternancia, de la frase mas larga a la mas corta: con un
# `re.search` por frase eran 60 busquedas sobre 1.500 caracteres por pagina.
_RE_SIN_RESULTADOS = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(f) for f in FRASES_SIN_RESULTADOS) + r")(?!\w)")


def frase_sin_resultados(*textos: str | None) -> str | None:
    for texto in textos:
        p = _plano(texto)
        if not p:
            continue
        m = _RE_SIN_RESULTADOS.search(p)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Disponibilidad en los datos estructurados
# ---------------------------------------------------------------------------

def disponibilidades(raw) -> set[str]:
    """Valores de `availability` en un bloque de datos estructurados.

    `https://schema.org/Discontinued`, `http://schema.org/OutOfStock`,
    `schema:InStock` y `Discontinued` a secas se reducen al nombre en
    minusculas.
    """
    vistos: set[str] = set()

    def _recorrer(nodo) -> None:
        if isinstance(nodo, dict):
            for k, v in nodo.items():
                if isinstance(k, str) and k.rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower() == "availability":
                    for valor in (v if isinstance(v, list) else [v]):
                        if isinstance(valor, dict):
                            valor = valor.get("@id") or valor.get("@value") or valor.get("value")
                        if isinstance(valor, str) and valor.strip():
                            vistos.add(re.split(r"[/:#]", valor.strip())[-1].lower())
                else:
                    _recorrer(v)
        elif isinstance(nodo, list):
            for v in nodo:
                _recorrer(v)

    _recorrer(raw)
    return vistos


def es_portada(path: str | None) -> bool:
    """La raiz del host o la de un idioma (`/`, `/es`, `/pt-br/`).

    Una portada nunca se afirma como soft 404 por huella: si la sonda devuelve
    lo mismo que la portada, el sitio sirve la home para lo que no existe, y la
    home no es la pagina de error.
    """
    tramos = [t for t in (path or "").split("/") if t]
    if not tramos:
        return True
    if len(tramos) == 1:
        from shared.plantillas import es_idioma

        return es_idioma(tramos[0])
    return False
