"""Enlaces que Google no puede seguir (decision 106).

Google lo documenta en «Make your links crawlable»: solo extrae de forma fiable
la URL de un ``<a>`` con ``href`` que resuelve a una direccion web. Lo demas
—el ``href="javascript:..."``, el ``<a onclick>`` sin ``href``, el ``<a
routerLink>``, el ``<span href>``— puede funcionar para una persona y no existe
para el buscador: ni descubre el destino por ahi ni le pasa autoridad. Y en las
SPA con rutas por fragmento (``#/productos``) tampoco: el fragmento no es una
URL que se pueda pedir.

Aqui vive lo que comparten el extractor (que decide el tipo de cada enlace) y
el analizador (que emite las incidencias): la taxonomia y como sacar un
destino de un trozo de JavaScript. Va en ``shared/`` por lo mismo que la
decision 23: la imagen del analisis solo copia ``shared/`` y ``analysis/``.

Lo que NO es un enlace no rastreable, a proposito:

- ``href="#"`` o ``href=""`` sin ``onclick``: es la forma de un boton (un
  desplegable del menu, un «ver mas») con el manejador puesto desde fuera. No
  se sabe si navega a algun sitio, y marcarlo llenaria el informe con cada
  conmutador de menu del sitio. Lo que no se sabe no se afirma (67, 89).
- ``#seccion``: un ancla dentro de la pagina es lo que es.
- ``mailto:``, ``tel:``, ``data:``: no son paginas.
- Un ``onclick`` (en un ``<a>`` sin href o en otra etiqueta) sin destino
  reconocible: «Leer mas», un ``scrollTo``, abrir un modal. Es un boton, no un
  enlace escondido. El ``href="javascript:..."`` SI se guarda aunque no diga a
  donde va: es un ``href`` que Google intenta resolver y Google lo documenta
  expresamente como no rastreable; sin destino sale como ``info``.
- ``href="#!"`` a secas: el «enlace» de relleno que Materialize y muchos temas
  ponen en los conmutadores del menu. Es el ``#`` con otra grafia, no una ruta.
- Un elemento con ``href`` cuyo nombre lleva guion (``<ion-item href>``,
  ``<sl-button href>``): es un componente web, y suele pintar un ``<a href>``
  de verdad dentro de su shadow DOM, que Google aplana al renderizar. Sin
  renderizar no se sabe, y no se afirma.

**El destino solo se afirma cuando el codigo NAVEGA.** Una cadena con forma de
ruta en un ``onclick`` no es un destino: ``addToCart('/cart/add')``,
``ga('send','event','/promo')``, ``this.src='/b.png'`` o
``$('#m').load('/ajax/modal')`` llevan rutas y no van a ninguna parte. Y un
helper que monta la URL con varios argumentos
(``getDetalleNoticia('/press/noticias/', 'slug')``) tiene como primer argumento
un PREFIJO comun a todas las noticias: tomarlo por destino colapsaba dieciseis
noticias distintas en una URL que no existe. Por eso hay dos preguntas:

- :func:`destino_en_script`: a donde va, solo si se ve una navegacion
  (``location=``, ``location.assign/replace``, ``window.open``, ``goTo``/
  ``navigate``/``redirect`` con la URL como UNICO argumento) con la URL entera
  —sin ``+`` ni ``${}`` detras—. Si no, None.
- :func:`menciona_ruta`: si el codigo lleva una ruta. Solo se usa en un ``<a>``
  sin ``href`` (que tiene forma de enlace), para guardarlo SIN destino
  (``info``); en otra etiqueta, sin navegacion es un boton y no se guarda.
"""

from __future__ import annotations

import re

# Tipos (columna ``crawlability``). El orden es el de la ficha.
JAVASCRIPT_HREF = "javascript_href"            # <a href="javascript:...">
ONCLICK_SIN_HREF = "onclick_sin_href"          # <a onclick> sin href rastreable
A_SIN_HREF = "a_sin_href"                      # <a routerLink|data-href|ng-href>
HREF_FUERA_DE_A = "href_fuera_de_a"            # <span href>, <div href>, <button href>
ONCLICK_EN_OTRA_ETIQUETA = "onclick_en_otra_etiqueta"  # <div onclick="location='/x'">
FRAGMENTO_COMO_RUTA = "fragmento_como_ruta"    # <a href="#/productos">, "#!/x"

TIPOS = (
    JAVASCRIPT_HREF,
    ONCLICK_SIN_HREF,
    A_SIN_HREF,
    HREF_FUERA_DE_A,
    ONCLICK_EN_OTRA_ETIQUETA,
    FRAGMENTO_COMO_RUTA,
)

# Atributos con los que los frameworks ponen el destino en un `<a>` sin `href`.
# `routerLink` es el ejemplo de Google; los demas son la misma idea en AngularJS
# (`ng-href` antes de compilar) y en los «enlaces» montados a mano.
ATRIBUTOS_DE_RUTA = ("routerlink", "ng-href", "data-href", "data-url", "data-link")

# Cuantos enlaces no rastreables se guardan por pagina como mucho. Una ficha
# de listado con un `onclick` por tarjeta son decenas; mas de esto es una
# plantilla que repite lo mismo y la muestra ya lo dice.
MAX_POR_PAGINA = 300

# Lo que se guarda del atributo: un `onclick` puede llevar una funcion entera.
MAX_HREF = 500
MAX_ANCLA = 200

# Un destino dentro de un trozo de JavaScript: solo las formas que navegan sin
# duda. `location = ...`, `location.href = ...`, `location.assign/replace(...)` y
# `window.open(...)` admiten mas argumentos (el `'_blank'` de `window.open`); los
# helpers con nombre de navegar (`goTo`, `navigate`, `redirect`, el
# `router.push` de Vue) solo si la URL es su UNICO argumento, porque con mas
# puede ser un trozo de la URL. El grupo `sig` es lo que va justo detras de la
# cadena: un `+` dice que es un prefijo.
_RE_NAVEGA = re.compile(
    r"""(?:(?:location(?:\.href)?\s*=\s*|location\.(?:assign|replace)\s*\(\s*|"""
    r"""window\.open\s*\(\s*)(?P<q1>['"`])(?P<u1>[^'"`\n]{1,400})(?P=q1)(?P<sig1>\s*\+)?"""
    r"""|\b(?:go_?to|navigate(?:To|ByUrl)?|redirect(?:To)?|router\.push)\s*\(\s*"""
    r"""(?P<q2>['"`])(?P<u2>[^'"`\n]{1,400})(?P=q2)\s*\))""",
    re.IGNORECASE,
)
# Una cadena con forma de ruta o de URL en cualquier sitio del codigo.
_RE_CADENA_RUTA = re.compile(
    r"""(['"`])(?P<u>(?:https?://|/|\./|\.\./)[^'"`\s]{0,400})\1""",
    re.IGNORECASE,
)


def _util(u: str) -> bool:
    # `//` suelto o `/` solo no dicen nada; `/*` es un comentario.
    return not (u in ("/", "//") or u.startswith("/*") or u.startswith("//*"))


# Parametros de campana de Google Analytics. Un enlace INTERNO con ellos
# reinicia la sesion en Analytics y abre una URL con parametros que Google
# puede rastrear como otra pagina.
RE_UTM = re.compile(r"[?&]utm_[a-z]+=", re.IGNORECASE)


def destino_en_script(codigo: str | None) -> str | None:
    """La URL a la que NAVEGA un trozo de JavaScript, si se ve entera; si no, None.

    >>> destino_en_script("window.location.href='/productos'")
    '/productos'
    >>> destino_en_script("goTo('products')") is None
    True
    >>> destino_en_script("addToCart('/cart/add', 12)") is None
    True
    >>> destino_en_script("location.href='/en/' + slug") is None
    True
    """
    if not codigo:
        return None
    for m in _RE_NAVEGA.finditer(codigo):
        if m.group("u1") is not None:
            if m.group("sig1"):
                continue  # `'/en/' + slug`: un prefijo, no la URL
            u = m.group("u1").strip()
        else:
            u = m.group("u2").strip()
        if "${" in u or not _util(u):
            continue
        # «goTo('products')», el ejemplo de Google: una cadena cualquiera
        # tampoco es un destino aunque vaya en una navegacion.
        if not u.lower().startswith(("http://", "https://", "/", "./", "../")):
            continue
        return u
    return None


def menciona_ruta(codigo: str | None) -> bool:
    """Si el codigo navega o lleva una cadena con forma de ruta.

    No dice a donde va: ``getDetalleNoticia('/press/noticias/', slug)`` lleva
    una ruta, y la URL de verdad no se puede saber sin ejecutarlo.
    """
    if not codigo:
        return False
    if _RE_NAVEGA.search(codigo):
        return True
    return any(_util(m.group("u").strip()) for m in _RE_CADENA_RUTA.finditer(codigo))


def es_fragmento_como_ruta(href: str) -> bool:
    """``#/productos`` o ``#!/productos``: una ruta de SPA en el fragmento.

    ``#!`` a secas no: es el ``#`` de relleno de los conmutadores de menu.
    """
    return href.startswith("#/") or (href.startswith("#!") and len(href) > 2)


def tiene_utm(url: str | None) -> bool:
    return bool(url) and bool(RE_UTM.search(url))
