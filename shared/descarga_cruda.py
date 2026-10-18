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

# Los titulos de muro y el criterio entero viven en `shared/respuestas.py`, que
# es tambien lo que usa el spider para clasificar cada respuesta: un solo
# «esto es un muro» para el rastreo y para las herramientas que lo controlan
# (decisiones 62, 82 y 103). Se reexporta el nombre porque lo importan los
# scripts.
from shared.respuestas import NO_VALIDAS, TITULOS_DE_BLOQUEO, clasificar_respuesta  # noqa: E402,F401


class NoEsLaPagina(RuntimeError):
    """La respuesta no es la pagina: un codigo de error o un muro de un WAF.

    Se distingue a proposito de un fallo de red. Estas herramientas existen
    para avisar de que se ha perdido algo (contenido, enlaces), asi que
    confundir "el sitio no me deja verla" con "no esta" es su peor fallo
    posible: dan la alarma sobre un rastreo que esta bien.
    """


def huella_y_cabeceras(impersonate: str | None = None,
                       user_agent: str | None = None) -> tuple[str, dict[str, str]]:
    """El perfil TLS y las cabeceras con las que bajan las herramientas.

    Sin UA, curl_cffi manda el SUYO, que para Chrome es el de macOS, y con ese
    el Cloudflare de progym responde 403 a cualquier version de la huella: el
    comprobador veia el muro y el rastreo (UA de Windows) la pagina (decision
    132). Por eso va siempre el UA, y el que casa con la huella.
    """
    from shared.perfiles import IMPERSONATE_POR_DEFECTO, cabeceras_de_huella, ua_para_huella

    perfil = impersonate or os.getenv("IMPERSONATE") or IMPERSONATE_POR_DEFECTO
    ua = user_agent or os.getenv("USER_AGENT") or ua_para_huella(perfil)
    return perfil, cabeceras_de_huella(perfil, ua)


def descargar_crudo(url: str, timeout: int = 40, *, impersonate: str | None = None,
                    user_agent: str | None = None) -> str:
    """HTML crudo de `url` con curl_cffi y la huella TLS de Chrome.

    Lanza `NoEsLaPagina` si el codigo no es 200 o si `clasificar_respuesta`
    dice que la respuesta es un muro (titulo o marcador de desafio sin
    contenido, captcha, cuerpo binario). "Checklist de seguridad web" es un
    articulo y pasa, y una pagina sin `<title>` no se tira por eso.
    """
    from curl_cffi import requests as cr
    perfil, cabeceras = huella_y_cabeceras(impersonate, user_agent)
    r = cr.get(url, impersonate=perfil, headers=cabeceras, timeout=timeout)
    if r.status_code != 200:
        raise NoEsLaPagina(f"HTTP {r.status_code}")
    cuerpo = getattr(r, "content", None)
    if not isinstance(cuerpo, (bytes, bytearray)):
        cuerpo = (r.text or "").encode("utf-8")
    clase, motivo = clasificar_respuesta(
        r.status_code, dict(getattr(r, "headers", None) or {}), bytes(cuerpo), url=url)
    if clase in NO_VALIDAS:
        raise NoEsLaPagina(f"muro del WAF ({clase}: {motivo})")
    return r.text
