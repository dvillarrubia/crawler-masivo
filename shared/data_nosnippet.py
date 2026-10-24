"""`data-nosnippet`: el texto que la pagina esconde al fragmento (decision 170).

Google documenta (https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag#data-nosnippet-attr,
consultado el 2026-10-24) que el atributo `data-nosnippet`:

- marca partes del HTML que **no se usan como fragmento**, y que las funciones
  de IA de la Busqueda (AI Overviews, AI Mode) se controlan con las mismas
  directivas que el fragmento (decision 137);
- **solo vale en `span`, `div` y `section`**; en otro elemento se ignora;
- es **booleano**: cuenta su presencia, con cualquier valor (`="false"`
  tambien), y el nombre del atributo no distingue mayusculas;
- puede leerse antes o despues del render, y Google pide no ponerlo ni
  quitarlo con JavaScript.

Lo que se mide aqui, sobre el `<body>` que se rastreo (servido o renderizado,
el que haya: no se afirma que Google viera el otro):

- `elementos`: los `span`/`div`/`section` con el atributo que se pintan (uno
  dentro de otro cuenta una vez: el de fuera ya lo cubre);
- `palabras`: las palabras visibles que cubren;
- `h1`: si cubren al menos la mitad del primer `<h1>` que se pinta, el titulo
  visible principal (None si no hay ninguno);
- `principal`: la fraccion de las palabras del contenido principal
  (`extract_main_content`) que cae dentro. Se cuenta por LINEAS del contenido
  principal que aparecen enteras en el texto cubierto, no por palabras sueltas:
  con palabras sueltas un pie largo marcado `data-nosnippet` «cubria» el
  articulo por las palabras comunes. Una linea que mezcla texto cubierto y no
  cubierto no cuenta: se queda corto, nunca se pasa (decision 67). None si no
  hay contenido principal medido;
- `ignorados` y `etiquetas_ignoradas`: el atributo en otros elementos (`p`,
  `article`, `nav`, uno propio...), fuera de lo documentado. No cuentan para
  lo cubierto: no se afirma que escondan nada. La pagina de Google que lo
  documenta lo lleva en `devsite-*` y en un `nav` (medido el 2026-10-24): que
  no se garantice no es que se ignore, y por eso solo se anota.

Sin Scrapy ni nada del crawler: el spider lo calcula y el analizador lee el
umbral de aqui.
"""

from __future__ import annotations

import re
from typing import Any

from shared.texto_visible import _WHITESPACE, _lineas_visibles, _nodo_oculto

ATRIBUTO = "data-nosnippet"
ELEMENTOS_VALIDOS = frozenset({"span", "div", "section"})
# «La mayor parte» del contenido principal.
UMBRAL_PRINCIPAL = 0.5
# Fraccion del H1 que tiene que quedar cubierta.
UMBRAL_H1 = 0.5
MAX_ETIQUETAS = 5
# Palabras que necesita una linea del contenido principal para buscarse como
# subcadena del texto cubierto.
MIN_PALABRAS_SUBCADENA = 4

_PALABRA = re.compile(r"\w+", re.UNICODE)


def _palabras(texto: str) -> int:
    return len(_PALABRA.findall(texto or ""))


def _tiene_atributo(el) -> bool:
    # lxml baja a minusculas los nombres de atributo al parsear HTML; con XML
    # o un DOM serializado raro, se mira tambien sin caja.
    attrib = el.attrib
    if ATRIBUTO in attrib:
        return True
    return any(isinstance(k, str) and k.lower() == ATRIBUTO for k in attrib.keys())


def _oculto_por_ancestro(el) -> bool:
    return any(isinstance(a.tag, str) and _nodo_oculto(a) for a in el.iterancestors())


def _normalizar(texto: str) -> str:
    return _WHITESPACE.sub(" ", texto or "").strip().lower()


def medir(raiz_body, main_content: str | None = None) -> dict[str, Any]:
    """Lo que `data-nosnippet` esconde en este `<body>` (lxml). Ver el modulo."""
    salida: dict[str, Any] = {"elementos": 0, "ignorados": 0}
    if raiz_body is None:
        return salida
    validos = []
    etiquetas_ignoradas: dict[str, int] = {}
    for el in raiz_body.iter():
        if not isinstance(el.tag, str) or not _tiene_atributo(el):
            continue
        tag = el.tag.lower()
        if tag not in ELEMENTOS_VALIDOS:
            etiquetas_ignoradas[tag] = etiquetas_ignoradas.get(tag, 0) + 1
            continue
        validos.append(el)
    if etiquetas_ignoradas:
        salida["ignorados"] = sum(etiquetas_ignoradas.values())
        salida["etiquetas_ignoradas"] = sorted(
            etiquetas_ignoradas, key=lambda t: (-etiquetas_ignoradas[t], t))[:MAX_ETIQUETAS]
    if not validos:
        return salida

    conjunto = set(validos)
    exteriores = [el for el in validos
                  if not any(a in conjunto for a in el.iterancestors())
                  and not _nodo_oculto(el) and not _oculto_por_ancestro(el)]
    lineas: list[str] = []
    for el in exteriores:
        lineas.extend(_lineas_visibles(el))
    normalizadas = [_normalizar(x) for x in lineas]
    cubierto = " \n ".join(normalizadas)
    lineas_cubiertas = set(normalizadas)
    salida["elementos"] = len(exteriores)
    salida["palabras"] = _palabras(cubierto)

    # -- El titulo visible principal: el primer h1 que se pinta -----------
    salida["h1"] = None
    for h1 in raiz_body.iter("h1"):
        if _nodo_oculto(h1) or _oculto_por_ancestro(h1):
            continue
        total = _palabras(" ".join(_lineas_visibles(h1)))
        if not total:
            continue
        if any(a in conjunto for a in h1.iterancestors()):
            dentro = total
        else:
            # Los marcados de dentro del h1 (un `span`), sin contar dos veces
            # uno anidado en otro.
            dentro = sum(_palabras(" ".join(_lineas_visibles(d)))
                         for d in h1.iterdescendants()
                         if d in conjunto and not any(a in conjunto for a in d.iterancestors()))
        salida["h1"] = dentro / total >= UMBRAL_H1
        break

    # -- El contenido principal, por lineas -------------------------------
    salida["principal"] = None
    if main_content and main_content.strip():
        total = cubiertas = 0
        for linea in main_content.splitlines():
            n = _palabras(linea)
            if not n:
                continue
            total += n
            norma = _normalizar(linea)
            # Una linea corta («Inicio», «Ver mas») solo cuenta si es una
            # linea entera de lo cubierto: dentro de un pie largo se
            # encontraria por casualidad.
            if norma and (norma in lineas_cubiertas
                          or (n >= MIN_PALABRAS_SUBCADENA and norma in cubierto)):
                cubiertas += n
        if total:
            salida["principal"] = round(cubiertas / total, 3)
    return salida


def esconde_lo_principal(datos: dict | None) -> str | None:
    """El motivo del aviso si `data-nosnippet` esconde lo principal de la
    pagina: `h1`, `contenido` o `h1_y_contenido`; None si no (o no medido)."""
    if not isinstance(datos, dict) or not datos.get("elementos"):
        return None
    h1 = datos.get("h1") is True
    principal = datos.get("principal")
    contenido = isinstance(principal, (int, float)) and principal >= UMBRAL_PRINCIPAL
    if h1 and contenido:
        return "h1_y_contenido"
    if h1:
        return "h1"
    if contenido:
        return "contenido"
    return None


__all__ = ["ATRIBUTO", "ELEMENTOS_VALIDOS", "UMBRAL_H1", "UMBRAL_PRINCIPAL",
           "esconde_lo_principal", "medir"]
