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

# Un destino dentro de un trozo de JavaScript. Primero las formas que navegan
# sin duda (`location = ...`, `location.href = ...`, `location.assign(...)`,
# `window.open(...)`); si no hay ninguna, la primera cadena que tiene forma de
# ruta o de URL. Una cadena cualquiera (`'products'`, `'menu'`) NO es un
# destino: el ejemplo de Google `goTo('products')` no dice a donde va, y
# adivinarlo seria afirmar lo que no se sabe.
_RE_NAVEGA = re.compile(
    r"""(?:location(?:\.href)?\s*=\s*|location\.(?:assign|replace)\s*\(\s*|"""
    r"""window\.open\s*\(\s*|navigate(?:ByUrl)?\s*\(\s*)(['"`])(?P<u>[^'"`\n]{1,400})\1""",
    re.IGNORECASE,
)
_RE_CADENA_RUTA = re.compile(
    r"""(['"`])(?P<u>(?:https?://|/|\./|\.\./)[^'"`\s]{0,400})\1""",
    re.IGNORECASE,
)

# Parametros de campana de Google Analytics. Un enlace INTERNO con ellos
# reinicia la sesion en Analytics y abre una URL con parametros que Google
# puede rastrear como otra pagina.
RE_UTM = re.compile(r"[?&]utm_[a-z]+=", re.IGNORECASE)


def destino_en_script(codigo: str | None) -> str | None:
    """La URL a la que navega un trozo de JavaScript, si se ve; si no, None.

    >>> destino_en_script("window.location.href='/productos'")
    '/productos'
    >>> destino_en_script("goTo('products')") is None
    True
    """
    if not codigo:
        return None
    m = _RE_NAVEGA.search(codigo) or _RE_CADENA_RUTA.search(codigo)
    if not m:
        return None
    u = m.group("u").strip()
    # `//` suelto o `/` solo no dicen nada; `/*` es un comentario.
    if u in ("/", "//") or u.startswith("/*") or u.startswith("//*"):
        return None
    return u


def es_fragmento_como_ruta(href: str) -> bool:
    """``#/productos`` o ``#!/productos``: una ruta de SPA en el fragmento."""
    return href.startswith("#/") or href.startswith("#!")


def tiene_utm(url: str | None) -> bool:
    return bool(url) and bool(RE_UTM.search(url))
