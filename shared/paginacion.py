"""Reconocer una pagina de una serie paginada por la forma de su URL (decision 122).

Vive en ``shared`` por la regla de siempre (decisiones 23, 39 y 64): lo que
decide que es «la pagina 2 de esta serie» tiene que dar lo mismo en el
analizador, en la API y en lo que venga despues (el spider, si algun dia sigue
la paginacion), y la imagen del analisis solo copia ``shared/`` y ``analysis/``.

Dos clases de marcador:

- **Fuertes**, que cuentan solos: ``?page=N``, ``?pagina=N``, ``?pg=N``,
  ``?paged=N`` (WordPress) y los tramos ``/page/N``, ``/pagina/N`` y
  ``/pag/N``. Nadie usa esos nombres para otra cosa. Tambien el
  ``?e-page-<id>=N`` de Elementor, que lleva el id del widget en el nombre (en
  Lopesan, la paginacion de las 45 categorias del blog).
- **Debiles**, que solo cuentan con evidencia (``debiles=True``, y la
  evidencia la decide quien llama): ``?p=N`` y un ``/N`` final (no detras de
  otro numero: ``/2023/04`` es un archivo por mes). En WordPress,
  ``?p=123`` es el id de una entrada, y en muchas tiendas ``/p/123`` o
  ``/producto/123`` es una ficha: tomarlos por paginacion llenaria el informe de
  «paginas 2+» que son productos.

La **serie** es la URL sin el marcador, con :func:`clave_url` (la misma
igualdad que el canonical, decision 39) y sin la barra final: ``/blog/`` y
``/blog`` son la misma serie, porque la pagina 1 se escribe de las dos formas
y el canonical de la pagina 2 tambien. El resto de parametros SI forma parte de
la serie: ``?orden=precio&page=2`` y ``?orden=fecha&page=2`` son dos listados
distintos, y mezclarlos daria por «canonical a la primera» lo que es un
canonical al otro orden.

``n`` <= 1 es la primera pagina (``?page=1``, ``/page/1/``). Una paginacion que
empieza en 0 (Drupal: ``?page=1`` es la segunda) se lee con una pagina de
retraso: se pierde su pagina 2, pero no se afirma nada falso de la 1.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from shared.indexabilidad import clave_url

PARAMETROS_FUERTES = frozenset(("page", "pagina", "pg", "paged"))
TRAMOS_FUERTES = frozenset(("page", "pagina", "pag"))
PARAMETROS_DEBILES = frozenset(("p",))

_RE_N = re.compile(r"^\d{1,6}$")
# Elementor: `?e-page-1f2a3b=2`, un parametro por widget paginado.
_RE_ELEMENTOR = re.compile(r"^e-page-[0-9a-f]+$", re.IGNORECASE)


class Pagina(NamedTuple):
    """Una pagina de una serie: a que serie pertenece y que numero es.

    ``forma`` dice por que se reconocio (``?page``, ``/page/N``, ``?p``,
    ``/N``) y sirve para agrupar por plantilla: dos listados con la misma ruta
    pero paginados de forma distinta salen de plantillas distintas.
    """

    serie: tuple[str, str, str, str]
    n: int
    forma: str
    debil: bool


def clave_comparable(url: str) -> tuple[str, str, str, str]:
    """:func:`clave_url` sin la barra final de la ruta.

    Para comparar la pagina 1 de una serie y los destinos de la paginacion:
    ``/blog/`` y ``/blog`` dicen la misma pagina en la practica (la de la barra
    redirige a la otra o al reves), y el canonical de la pagina 2 o el enlace
    «siguiente» de una plantilla puede escribir cualquiera de las dos.
    """
    esquema, host, ruta, query = clave_url(url)
    if len(ruta) > 1:
        ruta = ruta.rstrip("/") or "/"
    return (esquema, host, ruta, query)


def _sin_parametro(query: str, nombres: frozenset[str]) -> tuple[str, int, str] | None:
    """(query sin el parametro, N, nombre) si la query lleva uno de *nombres* = N."""
    if not query:
        return None
    partes = query.split("&")
    for i, parte in enumerate(partes):
        nombre, _, valor = parte.partition("=")
        bajo = nombre.lower()
        elementor = nombres is PARAMETROS_FUERTES and _RE_ELEMENTOR.match(bajo)
        if (bajo in nombres or elementor) and _RE_N.match(valor):
            resto = "&".join(partes[:i] + partes[i + 1:])
            return resto, int(valor), "e-page" if elementor else bajo
    return None


def pagina_de(url: str | None, debiles: bool = False) -> Pagina | None:
    """La serie y el numero de *url*, o None si su forma no es de paginacion.

    Con ``debiles=False`` solo se miran los marcadores fuertes. Una URL sin
    marcador devuelve None: puede ser la pagina 1 de una serie, pero eso solo se
    sabe mirando sus paginas 2+ (:func:`es_primera_de`).
    """
    if not url:
        return None
    try:
        esquema, host, ruta, query = clave_url(url)
    except Exception:
        return None

    # 1. Parametro fuerte (?page=2)
    casa = _sin_parametro(query, PARAMETROS_FUERTES)
    if casa is not None:
        resto, n, nombre = casa
        return Pagina(clave_comparable(_rehacer(esquema, host, ruta, resto)), n,
                      f"?{nombre}", False)

    # 2. Tramo fuerte al final de la ruta (/blog/page/2/)
    tramos = ruta.rstrip("/").split("/")
    if (len(tramos) >= 3 and _RE_N.match(tramos[-1])
            and tramos[-2].lower() in TRAMOS_FUERTES):
        base = "/".join(tramos[:-2]) or "/"
        return Pagina(clave_comparable(_rehacer(esquema, host, base, query)),
                      int(tramos[-1]), f"/{tramos[-2].lower()}/N", False)

    if not debiles:
        return None

    # 3. Debiles: ?p=2 y /2 final
    casa = _sin_parametro(query, PARAMETROS_DEBILES)
    if casa is not None:
        resto, n, nombre = casa
        return Pagina(clave_comparable(_rehacer(esquema, host, ruta, resto)), n,
                      f"?{nombre}", True)
    # Un `/N` detras de otro numero es una fecha, no una pagina: los archivos
    # por mes de WordPress (`/2023/04/`) con los meses 2 y 3 en el censo
    # parecian las paginas 4, 2 y 3 de la serie `/2023` (blogs.uoc.edu).
    if len(tramos) >= 3 and _RE_N.match(tramos[-1]) and not _RE_N.match(tramos[-2]):
        base = "/".join(tramos[:-1]) or "/"
        return Pagina(clave_comparable(_rehacer(esquema, host, base, query)),
                      int(tramos[-1]), "/N", True)
    return None


def es_primera_de(url: str | None, serie: tuple[str, str, str, str]) -> bool:
    """¿Es *url* la pagina 1 de *serie*? La URL sin marcador o con N <= 1."""
    if not url:
        return False
    try:
        if clave_comparable(url) == serie:
            return True
    except Exception:
        return False
    p = pagina_de(url, debiles=True)
    return p is not None and p.serie == serie and p.n <= 1


def _rehacer(esquema: str, host: str, ruta: str, query: str) -> str:
    return f"{esquema}://{host}{ruta or '/'}" + (f"?{query}" if query else "")
