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
    # FAQPage y HowTo NO estan aqui: Google retiro los dos resultados
    # (`RESULTADOS_RETIRADOS`). QAPage es otro resultado y sigue vivo.
    "qapage": {"obligatorias": ["mainentity"]},
    "breadcrumblist": {"obligatorias": ["itemlistelement"]},
    # Tampoco para Organization: «There are no required properties». El
    # `name` es recomendado como el resto, igual que se hizo con Article
    # (decision 51). Importa por volumen: es el bloque de plantilla que va en
    # todas las paginas, y como «error» un Organization sin nombre tenia el
    # mismo peso en el informe que un Product sin precio.
    "organization": {
        "obligatorias": [],
        "recomendadas": ["name", "url", "logo", "sameas", "contactpoint"],
    },
    # Las recomendadas, las de Google (comprobado el 24-10-2026, #107):
    # openingHoursSpecification y no openingHours, y sin image, que solo la
    # pide el carrusel de restaurantes. department, menu y servesCuisine se
    # quedan fuera: son de tipos concretos y avisarian a cualquier negocio.
    "localbusiness": {
        "obligatorias": ["name", "address"],
        "recomendadas": ["telephone", "url", "openinghoursspecification", "geo", "pricerange", "aggregaterating", "review"],
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
    # `description` es recomendada en la documentacion de video, no
    # obligatoria: pedirla daba error a videos que si salen enriquecidos.
    "videoobject": {
        "obligatorias": ["name", "thumbnailurl", "uploaddate"],
        "recomendadas": ["description", "duration", "contenturl", "embedurl"],
    },
    # `provider` es recomendada (Course list), no obligatoria.
    "course": {
        "obligatorias": ["name", "description"],
        "recomendadas": ["provider", "offers", "hascourseinstance"],
    },
    # Valoracion O resena, como en Product: exigir `aggregateRating` daba
    # error a una app con resenas sueltas, que Google acepta.
    "softwareapplication": {
        "obligatorias": ["name", "offers"],
        "uno_de": [["aggregaterating", "review"]],
        "recomendadas": ["operatingsystem", "applicationcategory"],
    },
    "person": {"obligatorias": ["name"]},
}

# Tipos cuyo resultado enriquecido Google ya NO muestra (decision 97). Pedirles
# propiedades «obligatorias» era mandar al cliente a completar un marcado que
# no da nada en la busqueda: un FAQPage sin `mainEntity` salia como error, con
# el mismo peso que un Product sin precio. Ahora se dice una vez, como aviso
# informativo y con la fuente, y no se exige nada mas. El marcado no es
# incorrecto —sigue siendo schema.org valido y otros buscadores pueden leerlo—,
# asi que no es un error ni pide quitarlo.
#
# Comprobado el 2026-10-16 en el registro de cambios de Search Central:
# - FAQ: limitado a sitios de gobierno y salud con autoridad el 2023-09-14;
#   retirado del todo el 2026-05-07 y su documentacion borrada el 2026-06-15.
# - HowTo: retirado y documentacion borrada el 2023-09-14.
FUENTE_RETIRADAS = "https://developers.google.com/search/updates"
RESULTADOS_RETIRADOS: dict[str, dict[str, str]] = {
    "faqpage": {
        "desde": "2026-05-07",
        "historia": ("limitado a sitios de gobierno y salud con autoridad "
                     "desde el 2023-09-14 y retirado del todo el 2026-05-07"),
        "fuente": FUENTE_RETIRADAS,
    },
    "howto": {
        "desde": "2023-09-14",
        "historia": "retirado el 2023-09-14",
        "fuente": FUENTE_RETIRADAS,
    },
}


def resultado_retirado(tipo: str) -> dict[str, str] | None:
    """Si Google ya no muestra el resultado enriquecido de este tipo."""
    clave = (tipo or "").rsplit("/", 1)[-1].lstrip("@").lower()
    return RESULTADOS_RETIRADOS.get(clave)


def mensaje_retirado(tipo: str, retirado: dict[str, str]) -> str:
    return (
        f"{tipo}: Google ya no muestra este resultado enriquecido "
        f"({retirado['historia']}; fuente: {retirado['fuente']}). El marcado "
        f"no es incorrecto, pero completarlo no da nada en la busqueda"
    )


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
            tipos = sd_item_types(candidato)
            if tipos and (requisitos_de(tipos[0]) or resultado_retirado(tipos[0])):
                yield candidato
            yield from _entidades_anidadas(candidato, profundidad + 1)


# -- Formato de los valores (decision 146) -------------------------------------
#
# Que la propiedad este no basta: un `startDate` «19/10/2026», un `price`
# «19,99 €» o un `priceCurrency` «€» son marcado presente que Google no puede
# leer, y el Rich Results Test los da por invalidos. Solo formatos que Google
# documenta, y con mensajes SIN el valor concreto: el analisis agrupa por
# mensaje (decision 73), y con el valor dentro cada pagina seria un hallazgo.
import re as _re

_FECHA = _re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
_NUMERO = _re.compile(r"^-?\d+(\.\d+)?$")
_ENTERO = _re.compile(r"^\d+$")
_MONEDA = _re.compile(r"^[A-Z]{3}$")
FECHAS = frozenset({"datepublished", "datemodified", "uploaddate", "startdate", "enddate",
                    "dateposted", "validthrough", "pricevaliduntil", "validfrom", "datecreated"})
# Las URLs NO se validan: Google resuelve las relativas contra la de la pagina,
# y avisar de ellas seria inventarse un problema. Solo se evita bajar a los
# objetos de imagen buscando otros campos.
IMAGENES = frozenset({"image", "logo", "thumbnailurl"})
PRECIOS = frozenset({"price", "lowprice", "highprice"})
RECUENTOS = frozenset({"ratingcount", "reviewcount"})
DISPONIBILIDAD = frozenset(x.lower() for x in (
    "InStock", "OutOfStock", "PreOrder", "BackOrder", "Discontinued", "InStoreOnly",
    "LimitedAvailability", "OnlineOnly", "PreSale", "SoldOut", "MadeToOrder", "Reserved"))


def _textos(valor) -> list:
    """Los valores sueltos de una propiedad: una lista se abre, y de un objeto
    (un ImageObject en `image`) se toma su `url` o `contentUrl`."""
    salida = []
    for v in valor if isinstance(valor, list) else [valor]:
        if isinstance(v, dict):
            v = v.get("url") or v.get("contentUrl") or v.get("@id")
        if v is not None and not isinstance(v, (dict, list)):
            salida.append(v)
    return salida


def _problemas_de_valores(item: dict, tipo: str, obligatorias: set[str], ruta: str = "",
                          profundidad: int = 0) -> list[tuple[str, str]]:
    problemas: list[tuple[str, str]] = []
    if profundidad > 3:
        return problemas
    for clave, valor in item.items():
        if clave.startswith("@"):
            continue
        prop = clave.lower()
        nombre = f"{ruta}{clave}"
        if isinstance(valor, (dict, list)) and prop not in IMAGENES:
            for hijo in valor if isinstance(valor, list) else [valor]:
                # Una entidad de resultado enriquecido anidada se valida aparte
                # (`_entidades_anidadas`): aqui se repetiria con otro tipo delante.
                if isinstance(hijo, dict) and not (sd_item_types(hijo) and requisitos_de(sd_item_types(hijo)[0])):
                    problemas += _problemas_de_valores(hijo, tipo, obligatorias, f"{nombre}.", profundidad + 1)
            continue
        for v in _textos(valor):
            texto = str(v).strip()
            if not texto:
                continue
            if prop in FECHAS and not _FECHA.match(texto):
                nivel = "error" if (not ruta and prop in obligatorias) else "warning"
                problemas.append((nivel, f"{tipo}: '{nombre}' no es una fecha ISO 8601 "
                                         f"(AAAA-MM-DD, con hora AAAA-MM-DDThh:mm y zona)"))
            elif prop in PRECIOS and not (isinstance(v, (int, float)) and not isinstance(v, bool)) \
                    and not _NUMERO.match(texto):
                problemas.append(("error", f"{tipo}: '{nombre}' no es un número (sin símbolo de "
                                           f"moneda y con punto decimal: 19.99)"))
            elif prop == "pricecurrency" and not _MONEDA.match(texto):
                problemas.append(("error", f"{tipo}: '{nombre}' no es un código de moneda ISO 4217 "
                                           f"de tres letras (EUR, USD)"))
            elif prop == "ratingvalue" and not (isinstance(v, (int, float)) and not isinstance(v, bool)) \
                    and not _NUMERO.match(texto):
                problemas.append(("error", f"{tipo}: '{nombre}' no es un número"))
            elif prop in RECUENTOS and not (isinstance(v, int) and not isinstance(v, bool)) \
                    and not _ENTERO.match(texto):
                problemas.append(("warning", f"{tipo}: '{nombre}' no es un número entero"))
            elif prop == "availability" and texto.rstrip("/").rsplit("/", 1)[-1].lower() not in DISPONIBILIDAD:
                problemas.append(("warning", f"{tipo}: '{nombre}' no es un valor de "
                                             f"schema.org/ItemAvailability (InStock, OutOfStock...)"))
    return problemas


def validate_sd_item(
    item: dict, incluir_anidadas: bool = True, anidada: bool = False
) -> list[tuple[str, str]]:
    """Problemas de una entidad, como ``(nivel, mensaje)``.

    *nivel* es ``"error"`` (sin esto no hay resultado enriquecido),
    ``"warning"`` (sale, pero peor) o ``"info"`` (es de un tipo cuyo resultado
    Google ya no muestra: `RESULTADOS_RETIRADOS`).

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
        retirado = resultado_retirado(tipo)
        if retirado:
            problemas.append(("info", mensaje_retirado(tipo, retirado)))
            continue
        reqs = requisitos_de(tipo)
        if not reqs:
            continue
        clave = (tipo or "").rsplit("/", 1)[-1].lstrip("@").lower()
        implicitas = IMPLICITAS_AL_ANIDAR.get(clave, set()) if anidada else set()
        problemas += _problemas_de_valores(item, tipo, set(reqs.get("obligatorias", [])))
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


def validate_structured_data(
    raw, json_invalido: str | None = None
) -> tuple[str, list[str]]:
    """El veredicto de un bloque: ``(peor nivel, mensajes)``.

    Envoltorio de `problemas_structured_data`, que es lo que hay que usar
    cuando importa el nivel de CADA mensaje (las incidencias: decision 97).
    """
    problemas = problemas_structured_data(raw, json_invalido=json_invalido)
    if not problemas:
        return ("ok", [])
    # Manda el peor: un bloque con una obligatoria que falta es un error
    # aunque tambien le falten recomendadas.
    niveles = {n for n, _ in problemas}
    estado = next(n for n in ("error", "warning", "info") if n in niveles)
    return (estado, [m for _, m in problemas])


def problemas_structured_data(
    raw, json_invalido: str | None = None
) -> list[tuple[str, str]]:
    """Los problemas de un bloque, como ``(nivel, mensaje)``.

    *nivel* es ``"error"`` (falta ``@type`` o una obligatoria), ``"warning"``
    (falta una recomendada) o ``"info"`` (un tipo cuyo resultado Google
    retiro). Lista vacia = bloque bien. Abre los ``@graph`` de JSON-LD y las
    listas sueltas; una forma inesperada no da problemas (sin falsos
    positivos).

    *json_invalido* es el motivo con que el extractor marco un JSON-LD que no
    es JSON estricto (coma final, comentario). Es un error por si solo: Google
    descarta el bloque ENTERO, asi que da igual que las propiedades esten. Va
    el primero de la lista porque es lo primero que hay que arreglar —hasta
    entonces el resto de avisos son sobre un marcado que Google no ve—, y el
    resto se sigue validando para que al arreglar la coma no aparezca una
    segunda tanda de errores por sorpresa.
    """
    previos: list[tuple[str, str]] = []
    if json_invalido:
        previos.append((
            "error",
            f"JSON-LD no valido: {json_invalido}. Google descarta el bloque "
            f"entero y la pagina no tiene resultado enriquecido por el",
        ))
        if raw is None:
            # Ni un lector permisivo lo pudo leer: no hay nada mas que validar.
            return previos

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
        items = []

    problemas: list[tuple[str, str]] = list(previos)
    for it in items:
        problemas += validate_sd_item(it)
    return problemas
