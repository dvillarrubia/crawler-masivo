"""Structured-data validation helpers.

Pure functions (no SQLAlchemy / DB imports) that validate a structured-data
block extracted by the crawler. Kept separate from ``analyzer.py`` so they can
be unit-tested in isolation.

Validation is deliberately conservative: only genuinely mandatory fields for
common Google rich-result types are checked, and any unexpected shape is
treated as valid, so it never produces false positives on real markup.
"""

from __future__ import annotations

# Minimum required properties for common schema.org types used in Google
# rich results. Kept small on purpose — only truly mandatory fields.
# Requisitos de resultado enriquecido por tipo, segun lo que documenta Google.
#
# Criterio SEO, que es de donde sale la forma de esta tabla: faltar una
# propiedad OBLIGATORIA no es un aviso de estilo, es que la pagina NO sale como
# resultado enriquecido — el marcado esta ahi y no sirve de nada. Faltar una
# RECOMENDADA si es un aviso: sale, pero peor (sin precio, sin estrellas, sin
# imagen). Son dos hallazgos distintos y antes los dos eran "warning".
#
# `uno_de` son grupos de los que basta con uno: un Product necesita precio,
# resena o valoracion —cualquiera de los tres—, y esa es la regla que de verdad
# falla en los sitios. La tabla anterior pedia solo `name`, que no falta nunca:
# por eso 459.510 bloques de un censo salian todos "ok".
#
# Deliberadamente conservadora: solo tipos con requisitos claros y solo
# PRESENCIA del campo, sin validar formatos internos, para no inventar
# problemas.
REQUISITOS: dict[str, dict[str, list]] = {
    "product": {
        "obligatorias": ["name"],
        "uno_de": [["offers", "review", "aggregaterating"]],
        "recomendadas": ["image", "brand", "description"],
    },
    # Google no documenta NINGUNA propiedad obligatoria para Article: todas son
    # recomendadas. Pedir `headline` como obligatoria convertia en error algo
    # que no impide el resultado enriquecido.
    "article": {
        "obligatorias": [],
        "recomendadas": ["headline", "image", "datepublished", "author",
                         "datemodified"],
    },
    "recipe": {
        "obligatorias": ["name", "image"],
        "recomendadas": ["recipeingredient", "recipeinstructions",
                         "aggregaterating", "author", "datepublished"],
    },
    "faqpage": {"obligatorias": ["mainentity"]},
    "qapage": {"obligatorias": ["mainentity"]},
    "howto": {
        "obligatorias": ["name", "step"],
        "recomendadas": ["image", "totaltime", "tool", "supply"],
    },
    "breadcrumblist": {"obligatorias": ["itemlistelement"]},
    "organization": {
        "obligatorias": ["name"],
        "recomendadas": ["url", "logo", "sameas", "contactpoint"],
    },
    "localbusiness": {
        "obligatorias": ["name", "address"],
        "recomendadas": ["telephone", "openinghours", "geo", "pricerange", "image"],
    },
    "event": {
        "obligatorias": ["name", "startdate", "location"],
        "recomendadas": ["enddate", "offers", "image", "description", "performer"],
    },
    "jobposting": {
        "obligatorias": ["title", "description", "dateposted",
                         "hiringorganization", "joblocation"],
        "recomendadas": ["basesalary", "employmenttype", "validthrough"],
    },
    "review": {
        "obligatorias": ["itemreviewed", "reviewrating", "author"],
        "recomendadas": ["datepublished", "reviewbody"],
    },
    "videoobject": {
        "obligatorias": ["name", "description", "thumbnailurl", "uploaddate"],
        "recomendadas": ["duration", "contenturl", "embedurl"],
    },
    "course": {
        "obligatorias": ["name", "description", "provider"],
        "recomendadas": ["offers", "hascourseinstance"],
    },
    "softwareapplication": {
        "obligatorias": ["name", "offers", "aggregaterating"],
        "recomendadas": ["operatingsystem", "applicationcategory"],
    },
    "person": {"obligatorias": ["name"]},
}

# Subtipos que comparten requisitos con un tipo base.
ALIAS_TIPOS: dict[str, str] = {
    "newsarticle": "article", "blogposting": "article",
    "techarticle": "article", "scholarlyarticle": "article",
    "restaurant": "localbusiness", "store": "localbusiness",
    "professionalservice": "localbusiness", "dentist": "localbusiness",
    "medicalbusiness": "localbusiness", "foodestablishment": "localbusiness",
    "onlinestore": "organization", "corporation": "organization",
    "ngo": "organization", "educationalorganization": "organization",
    "musicevent": "event", "sportsevent": "event", "theaterevent": "event",
    "businessevent": "event", "educationevent": "event",
    "individualproduct": "product", "productmodel": "product",
    "mobileapplication": "softwareapplication",
    "webapplication": "softwareapplication",
}


def requisitos_de(tipo: str) -> dict[str, list] | None:
    """Requisitos del tipo (o de su tipo base si es un subtipo conocido)."""
    clave = (tipo or "").rsplit("/", 1)[-1].lstrip("@").lower()
    return REQUISITOS.get(ALIAS_TIPOS.get(clave, clave))


def sd_item_types(item: dict) -> list[str]:
    """Return the declared ``@type``(s) of a structured-data item."""
    t = item.get("@type", item.get("type"))
    if t is None:
        return []
    if isinstance(t, list):
        return [str(x) for x in t if x]
    return [str(t)]


def _tiene_valor(item: dict, prop: str) -> bool:
    """La propiedad esta Y lleva algo dentro.

    `itemListElement: []` o `name: ""` es lo mismo que no estar: Google no
    genera resultado enriquecido con una lista vacia, y mirar solo la clave
    daba "ok" a una miga de pan sin ningun eslabon.
    """
    for clave, valor in item.items():
        if clave.lower() != prop:
            continue
        if valor is None:
            return False
        if isinstance(valor, (str, list, tuple, dict)) and len(valor) == 0:
            return False
        if isinstance(valor, str) and not valor.strip():
            return False
        return True
    return False


def _es_referencia(item: dict) -> bool:
    """Nodo que solo apunta a otro (`{"@id": ...}`): no es una entidad.

    En el `@graph` de Yoast estan por todas partes y salian como un bloque
    "sin @type", que es un error inventado.
    """
    claves = {k for k in item.keys() if k != "@context"}
    return bool(claves) and claves <= {"@id", "id"}


# Vocabularios que un lector de RDFa encuentra en cualquier pagina y que no
# son datos estructurados: los atributos de accesibilidad (`role`, `rel`) y el
# Open Graph, que ya se extrae a `html_meta.og_*`.
_VOCABULARIOS_AJENOS = (
    "http://www.w3.org/1999/xhtml/vocab#",
    "http://ogp.me/ns#",
    "http://purl.org/dc/terms/",
)


def _solo_vocabularios_ajenos(claves: set[str]) -> bool:
    """True si TODAS las propiedades (sin contar `@id`) son de esos vocabularios."""
    propias = {k for k in claves if k not in ("@id", "id")}
    return bool(propias) and all(
        k.startswith(_VOCABULARIOS_AJENOS) for k in propias
    )


# Propiedades que el ANIDAMIENTO ya resuelve. Una `Review` dentro de un
# `Product` no necesita `itemReviewed`: lo reseñado es el producto que la
# contiene, y Google documenta que no hay que repetirlo. Validarla como si
# estuviera suelta producia 675 errores falsos en un censo de 4.785 paginas de
# comercio electronico — justo las fichas de producto con resenas, que es donde
# el marcado esta BIEN.
IMPLICITAS_AL_ANIDAR: dict[str, set[str]] = {
    "review": {"itemreviewed"},
    "aggregaterating": {"itemreviewed"},
}


def _entidades_anidadas(item: dict, profundidad: int = 0):
    """Entidades con `@type` dentro de otra (`WebPage.mainEntity`, `itemListElement`).

    Un Product dentro de un WebPage es el producto de la pagina y sus
    requisitos son los mismos; no validarlo dejaba fuera a los sitios que
    anidan (Shopify y los constructores de paginas lo hacen). Se limita la
    profundidad para que un `@graph` enorme no se vuelva cuadratico.
    """
    if profundidad >= 3:
        return
    for valor in item.values():
        candidatos = valor if isinstance(valor, list) else [valor]
        for candidato in candidatos:
            if not isinstance(candidato, dict) or _es_referencia(candidato):
                continue
            if sd_item_types(candidato) and requisitos_de(sd_item_types(candidato)[0]):
                yield candidato
            yield from _entidades_anidadas(candidato, profundidad + 1)


def validate_sd_item(
    item: dict, incluir_anidadas: bool = True, anidada: bool = False
) -> list[tuple[str, str]]:
    """Problemas de una entidad, como ``(nivel, mensaje)``.

    *nivel* es ``"error"`` (sin esto no hay resultado enriquecido) o
    ``"warning"`` (sale, pero peor).

    `anidada` cambia dos cosas, y las dos salen de medir en un censo real:
    no se exige lo que el anidamiento ya resuelve (`IMPLICITAS_AL_ANIDAR`) y no
    se avisa de las propiedades RECOMENDADAS. Una `Organization` puesta como
    marca de un producto no necesita logo ni perfiles sociales: lo recomendado
    pesa en la entidad principal de la pagina, que es la de primer nivel.
    """
    if _es_referencia(item):
        return []
    if not sd_item_types(item):
        # Un bloque vacio (`{}` o `{"@graph": []}`, que es lo que deja un Yoast
        # sin nada que declarar) no declara nada, pero tampoco es un marcado
        # roto: decir "sin @type" era inventarse un error.
        resto = {k for k in item.keys() if k != "@context"}
        if not resto or (resto == {"@graph"} and not item.get("@graph")):
            return []
        # Un nodo que solo tiene propiedades de vocabularios que NO son
        # schema.org no es marcado roto: es lo que un lector de RDFa saca de
        # una pagina normal. `role="alert"` deja
        # `http://www.w3.org/1999/xhtml/vocab#role` y un `<meta
        # property="og:title">` deja `http://ogp.me/ns#title` —y el Open Graph
        # ya se extrae aparte, a `html_meta.og_*`—.
        #
        # Medido al re-analizar el censo de CST (junio, extractor anterior):
        # **88.838 "errores" de los que 81.023 eran atributos `role` y 7.815
        # Open Graph**. El 100% de los errores de datos estructurados de ese
        # informe, y su nota bajaba de 100 a 60 por ellos. El extractor de hoy
        # ya no los guarda, pero los censos viejos los llevan dentro y
        # re-analizarlos los convertia en un informe falso.
        if _solo_vocabularios_ajenos(resto):
            return []
        return [("error", "sin @type: el bloque no identifica ninguna entidad")]

    problemas: list[tuple[str, str]] = []
    for tipo in sd_item_types(item):
        reqs = requisitos_de(tipo)
        if not reqs:
            continue
        clave = (tipo or "").rsplit("/", 1)[-1].lstrip("@").lower()
        implicitas = IMPLICITAS_AL_ANIDAR.get(clave, set()) if anidada else set()
        for prop in reqs.get("obligatorias", []):
            if prop in implicitas:
                continue
            if not _tiene_valor(item, prop):
                problemas.append((
                    "error",
                    f"{tipo}: falta la propiedad obligatoria '{prop}'; sin ella "
                    f"no sale como resultado enriquecido",
                ))
        for grupo in reqs.get("uno_de", []):
            if not any(_tiene_valor(item, p) for p in grupo):
                problemas.append((
                    "error",
                    f"{tipo}: hace falta al menos una de {', '.join(grupo)}; "
                    f"sin ninguna no sale como resultado enriquecido",
                ))
        if anidada:
            continue
        for prop in reqs.get("recomendadas", []):
            if not _tiene_valor(item, prop):
                problemas.append((
                    "warning",
                    f"{tipo}: falta la propiedad recomendada '{prop}'; sale "
                    f"como resultado enriquecido, pero mas pobre",
                ))
    if incluir_anidadas:
        vistos = set(problemas)
        for anidada in _entidades_anidadas(item):
            for problema in validate_sd_item(
                anidada, incluir_anidadas=False, anidada=True
            ):
                if problema not in vistos:
                    vistos.add(problema)
                    problemas.append(problema)
    return problemas


def validate_structured_data(raw) -> tuple[str, list[str]]:
    """Validate a structured-data block.

    Returns ``(status, issues)`` where *status* is ``"ok"``, ``"warning"``
    (present but missing a required property), or ``"error"`` (missing
    ``@type`` entirely). Handles JSON-LD ``@graph`` containers and bare
    lists. Any unexpected shape is treated as ``"ok"`` (no false positives).
    """
    items: list[dict] = []
    if isinstance(raw, dict):
        graph = raw.get("@graph")
        if isinstance(graph, list) and graph:
            items = [g for g in graph if isinstance(g, dict)]
        else:
            items = [raw]
    elif isinstance(raw, list):
        items = [g for g in raw if isinstance(g, dict)]
    else:
        return ("ok", [])

    problemas: list[tuple[str, str]] = []
    for it in items:
        problemas += validate_sd_item(it)
    if not problemas:
        return ("ok", [])
    # Manda el peor: un bloque con una obligatoria que falta es un error
    # aunque tambien le falten recomendadas.
    estado = "error" if any(n == "error" for n, _ in problemas) else "warning"
    return (estado, [m for _, m in problemas])
