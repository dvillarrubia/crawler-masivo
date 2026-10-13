"""Descarga del HTML crudo para las herramientas de control, por el MISMO
camino que el rastreo.

La usan `scripts/check_content_quality.py` y `scripts/check_js_templates.py`,
que comparan lo que se rastreo (o el HTML crudo) contra lo que se ve ahora. Si
cada una descarga a su manera, una puede estar comparando la pagina y la otra
el muro de un WAF: `check_js_templates.py` bajaba con `urllib`, sin huella TLS
de navegador, y un WAF que deja pasar al rastreo (curl_cffi con la huella de
Chrome, decision 11) le servia un desafio. El render si llegaba a la pagina, asi
que todos sus enlaces salian "solo con JavaScript" y el grafo "no fiable".

Vive en `shared/` porque la necesitan dos scripts y estos se ejecutan sueltos
dentro de la imagen del crawler; `curl_cffi` y `parsel` se importan dentro de
la funcion para que importar el modulo no los exija (la imagen de la API copia
`shared/` y no los lleva).
"""

from __future__ import annotations

import os

# Titulos con los que un WAF responde 200 a una peticion que ha bloqueado.
# La respuesta trae 5-6 kB de HTML sin una palabra de la pagina, asi que un
# comparador la leia como "aqui se ha perdido todo el contenido" (decision 62).
TITULOS_DE_BLOQUEO = (
    "just a moment",
    "access denied",
    "pardon our interruption",
    "attention required",
    "request unsuccessful",
    "are you a robot",
    "checking your browser",
    "security check",
    "403 forbidden",
)


class NoEsLaPagina(RuntimeError):
    """La respuesta no es la pagina: un codigo de error o un muro de un WAF.

    Se distingue a proposito de un fallo de red. Estas herramientas existen
    para avisar de que se ha perdido algo (contenido, enlaces), asi que
    confundir "el sitio no me deja verla" con "no esta" es su peor fallo
    posible: dan la alarma sobre un rastreo que esta bien.
    """


def descargar_crudo(url: str, timeout: int = 40) -> str:
    """HTML crudo de `url` con curl_cffi y la huella TLS de Chrome.

    Lanza `NoEsLaPagina` si el codigo no es 200 o si el `<title>` es el de un
    muro conocido. El titulo se casa por subcadena pero solo contra muros
    reales: "Checklist de seguridad web" es un articulo y pasa, y una pagina sin
    `<title>` no se tira por eso.
    """
    from curl_cffi import requests as cr
    from parsel import Selector

    r = cr.get(url, impersonate=os.getenv("IMPERSONATE", "chrome124"), timeout=timeout)
    if r.status_code != 200:
        raise NoEsLaPagina(f"HTTP {r.status_code}")
    titulo = (Selector(text=r.text).css("title::text").get() or "").strip().lower()
    if any(t in titulo for t in TITULOS_DE_BLOQUEO):
        raise NoEsLaPagina(f"muro del WAF (titulo: {titulo[:40]!r})")
    return r.text
