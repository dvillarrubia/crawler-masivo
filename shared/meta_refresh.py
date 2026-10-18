"""El retardo de una meta refresh, para el analizador (decision 120).

El spider guarda el ``content`` tal cual en ``html_meta.meta_refresh``
(``"5; URL='/dest'"``) y decide si es una redireccion con
``extract_meta_refresh_target``: el destino va en ``urls.redirect_url`` y el
estado en ``indexability_status = 'Redirect (meta refresh)'``. Lo que no se
guarda aparte es el retardo, y de el depende como la trata Google: la
inmediata como una redireccion permanente, la de varios segundos como una
temporal. Vive en ``shared`` porque la imagen del analisis no copia
``crawler/``, y con la forma del ``content`` que acepta la regex del extractor:
los digitos y el punto del principio (``0.5;url=...`` es medio segundo).
"""

from __future__ import annotations

import re

# Lo mismo que el `[\d.]*` del principio de `_REFRESH_URL_RE` en el extractor.
_RE_RETARDO = re.compile(r"^\s*(\d*(?:\.\d*)?)")


def retardo_meta_refresh(content: str | None) -> float | None:
    """Segundos de espera de una meta refresh, o None si no se sabe.

    None cuando no hay ``content`` (un salto registrado con render, que no
    lleva ``html_meta``) o cuando no empieza por un numero: lo que no se sabe
    no se convierte en 0, que diria «inmediata».
    """
    if not content:
        return None
    m = _RE_RETARDO.match(content)
    numero = m.group(1) if m else ""
    if not numero or numero == ".":
        return None
    try:
        return float(numero)
    except ValueError:
        return None


__all__ = ["retardo_meta_refresh"]
