"""Perfiles de dispositivo con los que se pide una pagina (decision 119).

Un perfil es lo que tiene que ir JUNTO para que el sitio vea un dispositivo
coherente: la cadena de User-Agent, la huella TLS de curl_cffi sin render y,
con render, el contexto de Playwright (viewport, `is_mobile`, `has_touch`).
Un UA de Android con la huella TLS de un Chrome de escritorio, o con
`navigator.userAgent` de escritorio y una ventana de 1280 px, es un
dispositivo que no existe: un WAF lo ve, y un sitio con servido dinamico
puede decidir por cualquiera de las tres cosas.

Vive en `shared/` porque lo leen el spider (para pedir), el analizador y la
comparacion (para saber con que perfil se rastreo un censo). La cadena de
Googlebot smartphone es la MISMA que el preset del formulario
(`UA_PRESETS.googlebot_mobile` en `frontend/app.js`); un test lo comprueba.
"""

from __future__ import annotations

import re

# La estable de Chrome que Google pone en `Chrome/W.X.Y.Z` (decision 98). La
# misma constante que `CHROME_EVERGREEN` de `frontend/app.js`.
CHROME_EVERGREEN = "155.0.8059.84"

# developers.google.com/search/docs/crawling-indexing/google-common-crawlers
UA_GOOGLEBOT_SMARTPHONE = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{CHROME_EVERGREEN} Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
# Huellas TLS de curl_cffi por perfil. La de Android existe desde curl_cffi
# 0.8 (`chrome131_android`); si la instalada no la tiene, el spider se queda
# con la del job y lo dice en el log (ver `seo_crawler.sonda_paridad`).
IMPERSONATE_MOVIL = "chrome131_android"

# ---------------------------------------------------------------------------
# Huella TLS y User-Agent: van JUNTOS (decision 132)
# ---------------------------------------------------------------------------
# Los perfiles de `curl_cffi.requests.BrowserType` en la 0.16.3 (la que fija
# `crawler/requirements.txt`), copiados a mano porque la imagen de la API no
# lleva curl_cffi y tiene que validar `JobConfig.impersonate` igual. Un test en
# la imagen del crawler comprueba que todos existen en el curl_cffi instalado.
# Los alias sin version (`chrome`, `safari`...) no estan a proposito: se mueven
# solos al actualizar curl_cffi, y entonces la huella deja de casar con el UA.
PERFILES_IMPERSONATE = frozenset({
    "chrome99", "chrome100", "chrome101", "chrome104", "chrome107", "chrome110",
    "chrome116", "chrome119", "chrome120", "chrome123", "chrome124", "chrome131",
    "chrome133a", "chrome136", "chrome142", "chrome145", "chrome146", "chrome150",
    "chrome99_android", "chrome131_android",
    "edge99", "edge101",
    "firefox133", "firefox135", "firefox144", "firefox147",
    "safari153", "safari155", "safari15_3", "safari15_5", "safari170", "safari17_0",
    "safari180", "safari18_0", "safari184", "safari260", "safari2601",
    "safari172_ios", "safari17_2_ios", "safari180_ios", "safari18_0_ios",
    "safari184_ios", "safari260_ios",
    "tor145",
})

# El perfil de serie para todo lo que no va por Chromium. Medido desde fuera de
# produccion contra los tres sitios con WAF (decision 132): igual que chrome124
# en los tres; lo que si separa es el UA, no la version de la huella.
IMPERSONATE_POR_DEFECTO = "chrome150"

_RE_PERFIL = re.compile(r"^(chrome|edge|firefox|safari|tor)(\d+(?:_\d+)*)(a)?(_android|_ios)?$")


def _version_mayor(familia: str, digitos: str) -> int:
    """`150` -> 150, `17_0` -> 17, `184` -> 18, `2601` -> 26 (Safari)."""
    if "_" in digitos:
        return int(digitos.split("_")[0])
    if familia == "safari" and len(digitos) >= 3:
        return int(digitos[:2])
    return int(digitos)


def familia_de_huella(perfil: str | None) -> tuple[str, int] | None:
    """`(familia, version mayor)` de un perfil de curl_cffi, o None.

    La familia distingue movil: `chrome_android`, `safari_ios`.
    """
    m = _RE_PERFIL.match((perfil or "").strip().lower())
    if not m:
        return None
    familia, digitos, _a, sufijo = m.groups()
    if sufijo == "_android":
        familia = "chrome_android"
    elif sufijo == "_ios":
        familia = "safari_ios"
    return familia, _version_mayor(m.group(1), digitos)


def _subversion_safari(perfil: str) -> str:
    digitos = _RE_PERFIL.match(perfil).group(2)
    if "_" in digitos:
        return ".".join(digitos.split("_")[:2])
    return f"{digitos[:2]}.{digitos[2] if len(digitos) > 2 else 0}"


def ua_para_huella(perfil: str | None) -> str | None:
    """La cadena de User-Agent de un navegador que tendria ESA huella TLS.

    Escritorio en Windows: un Chrome de macOS con la cadena por defecto de
    curl_cffi es justo lo que bloquea el Cloudflare de progym (decision 132).
    None si el perfil no es de los que se saben describir (Tor).
    """
    fv = familia_de_huella(perfil)
    if not fv:
        return None
    familia, v = fv
    if familia == "chrome":
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36")
    if familia == "chrome_android":
        return ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{v}.0.0.0 Mobile Safari/537.36")
    if familia == "edge":
        return ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36 Edg/{v}.0.0.0")
    if familia == "firefox":
        return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{v}.0) "
                f"Gecko/20100101 Firefox/{v}.0")
    if familia == "safari":
        return ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                f"(KHTML, like Gecko) Version/{_subversion_safari(perfil)} Safari/605.1.15")
    if familia == "safari_ios":
        return ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                f"(KHTML, like Gecko) Version/{_subversion_safari(perfil)} Mobile/15E148 "
                "Safari/604.1")
    return None


# Un UA de bot no dice ser un navegador: con el, la huella no delata nada que
# el propio UA no diga ya (y el WAF que verifica bots mira la IP, decision 98).
_RE_BOT = re.compile(r"compatible;|bot\b|crawler|spider", re.I)


def navegador_de_ua(ua: str | None) -> tuple[str, int] | None:
    """`(familia, version mayor)` del navegador que dice ser un UA, o None.

    None para un bot o para un UA que no se reconoce.
    """
    if not ua or _RE_BOT.search(ua):
        return None
    if m := re.search(r"\bEdg/(\d+)", ua):
        return "edge", int(m.group(1))
    if m := re.search(r"\bFirefox/(\d+)", ua):
        return "firefox", int(m.group(1))
    if m := re.search(r"\bChrome/(\d+)", ua):
        return ("chrome_android" if es_ua_movil(ua) else "chrome"), int(m.group(1))
    if m := re.search(r"\bVersion/(\d+).*Safari/", ua):
        return ("safari_ios" if es_ua_movil(ua) else "safari"), int(m.group(1))
    return None


def huella_para_ua(ua: str | None) -> str | None:
    """El perfil soportado que mejor casa con el navegador que dice ser `ua`.

    La misma familia y la version exacta si existe; si no, la mas cercana
    (a igual distancia, la mas nueva). None para un bot o un UA desconocido.
    """
    nv = navegador_de_ua(ua)
    if not nv:
        return None
    familia, v = nv
    candidatos = [(abs(fv[1] - v), -fv[1], p) for p in PERFILES_IMPERSONATE
                  if (fv := familia_de_huella(p)) and fv[0] == familia
                  and not p.endswith("a")]
    return min(candidatos)[2] if candidatos else None


def incoherencia_ua_huella(ua: str | None, perfil: str | None) -> str | None:
    """Por que un UA y una huella TLS no pueden ser el mismo navegador, o None.

    Un Chrome 124 de UA con la huella (y el `sec-ch-ua`) de un 150, o un Firefox
    con la huella de Chrome, es un navegador que no existe. Un bot no se mira.
    """
    nv, fv = navegador_de_ua(ua), familia_de_huella(perfil)
    if not nv or not fv:
        return None
    if nv[0] != fv[0]:
        return f"el UA dice {nv[0]} {nv[1]} y la huella TLS es de {fv[0]} ({perfil})"
    if nv[1] != fv[1] and nv[0] in ("chrome", "chrome_android", "edge", "firefox"):
        return f"el UA dice {nv[0]} {nv[1]} y la huella TLS es de la {fv[1]} ({perfil})"
    return None


_PLATAFORMAS = (("Windows", '"Windows"'), ("Android", '"Android"'), ("CrOS", '"Chrome OS"'),
                ("Macintosh", '"macOS"'), ("Mac OS X", '"macOS"'), ("Linux", '"Linux"'))


def cabeceras_de_huella(perfil: str | None, ua: str | None) -> dict[str, str]:
    """Las cabeceras que tienen que ir con `ua` para que casen con la huella.

    curl_cffi manda las de su perfil, y el `sec-ch-ua-platform` de los Chrome de
    escritorio es `"macOS"`: con un UA de Windows (el de serie desde siempre)
    el navegador decia dos sistemas a la vez. Solo para Chrome y Edge, que son
    los que mandan Client Hints.
    """
    cab: dict[str, str] = {}
    if ua:
        cab["User-Agent"] = ua
    fv = familia_de_huella(perfil)
    if ua and fv and fv[0] in ("chrome", "chrome_android", "edge"):
        for marca, valor in _PLATAFORMAS:
            if marca in ua:
                cab["Sec-Ch-Ua-Platform"] = valor
                break
    return cab


# El UA de serie es el del navegador de la huella de serie: si se sube una, se
# sube la otra, porque salen de la misma constante.
UA_POR_DEFECTO = ua_para_huella(IMPERSONATE_POR_DEFECTO)
UA_CHROME_ESCRITORIO = UA_POR_DEFECTO
# Un Chrome de Android sin token de bot: el que se usa por defecto para la
# paridad. Con el de Googlebot desde nuestra IP, un WAF que verifica bots lo
# bloquea o le sirve otra cosa (decision 98), y se mediria el WAF. Lleva la
# version de SU huella (`IMPERSONATE_MOVIL`), no la evergreen: un Chrome 155 de
# UA con el `sec-ch-ua` de un 131 no existe (decision 132).
UA_CHROME_ANDROID = ua_para_huella(IMPERSONATE_MOVIL)

UA_MOVIL_POR_NOMBRE = {
    "chrome_android": UA_CHROME_ANDROID,
    "googlebot_smartphone": UA_GOOGLEBOT_SMARTPHONE,
}


# Contexto de Playwright de un movil tipo Pixel 7 (412x915 CSS px, DPR 2,625),
# el tamano que usa Lighthouse para movil. Sin `user_agent`: lo pone quien
# construye el contexto, con la cadena del perfil.
VIEWPORT_MOVIL = {
    "viewport": {"width": 412, "height": 915},
    "device_scale_factor": 2.625,
    "is_mobile": True,
    "has_touch": True,
}
VIEWPORT_ESCRITORIO = {"viewport": {"width": 1280, "height": 720}}

# Lo que dice que un UA es de movil. `Mobile` lo llevan Chrome de Android,
# Safari de iPhone y Googlebot smartphone; `Android` sin `Mobile` es una
# tablet, que Google trata como escritorio.
_RE_MOVIL = re.compile(r"\b(Mobile|iPhone|iPod)\b|Android.*Mobile", re.I)


def es_ua_movil(ua: str | None) -> bool:
    """True si la cadena de User-Agent es la de un movil."""
    return bool(ua) and bool(_RE_MOVIL.search(ua or ""))


def perfil_de_ua(ua: str | None) -> str:
    """`movil` o `escritorio`: con que perfil se rastreo un censo.

    Sin UA en la config es el de serie del crawler, que es de escritorio.
    """
    return "movil" if es_ua_movil(ua) else "escritorio"


def ua_de_perfil_alternativo(perfil: str, ua_movil: str | None = None) -> str:
    """El UA con el que se pide el perfil `perfil` cuando NO es el del censo.

    Lo usan la paridad (decision 119) y las capturas (173), que comparten el
    contexto de Playwright con nombre (`perfil_movil` / `perfil_escritorio`):
    se crea con los argumentos de la primera peticion que lo nombra, asi que
    las dos tienen que construirlo igual. `ua_movil` es
    `crawl_behavior.paridad_movil_ua`.
    """
    if perfil == "movil":
        return UA_MOVIL_POR_NOMBRE.get(ua_movil or "chrome_android",
                                       UA_MOVIL_POR_NOMBRE["chrome_android"])
    return UA_CHROME_ESCRITORIO


def contexto_playwright(ua: str, movil: bool, base: dict | None = None) -> dict:
    """Argumentos de `browser.new_context` para un perfil.

    `base` son los del contexto del rastreo (locale, JavaScript): se conservan
    para que lo unico que cambie sea el dispositivo.
    """
    ctx = {k: v for k, v in (base or {}).items()
           if k not in ("viewport", "device_scale_factor", "is_mobile", "has_touch",
                        "user_agent")}
    ctx.update(VIEWPORT_MOVIL if movil else VIEWPORT_ESCRITORIO)
    ctx["user_agent"] = ua
    return ctx


__all__ = [
    "CHROME_EVERGREEN", "UA_GOOGLEBOT_SMARTPHONE", "UA_CHROME_ANDROID",
    "UA_CHROME_ESCRITORIO", "UA_MOVIL_POR_NOMBRE", "IMPERSONATE_MOVIL",
    "VIEWPORT_MOVIL", "VIEWPORT_ESCRITORIO", "es_ua_movil", "perfil_de_ua",
    "contexto_playwright", "PERFILES_IMPERSONATE", "IMPERSONATE_POR_DEFECTO",
    "UA_POR_DEFECTO", "familia_de_huella", "ua_para_huella", "navegador_de_ua",
    "huella_para_ua", "incoherencia_ua_huella", "cabeceras_de_huella",
    "ua_de_perfil_alternativo",
]
