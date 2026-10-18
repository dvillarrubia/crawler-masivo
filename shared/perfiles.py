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
# Un Chrome de Android sin token de bot: el que se usa por defecto para la
# paridad. Con el de Googlebot desde nuestra IP, un WAF que verifica bots lo
# bloquea o le sirve otra cosa (decision 98), y se mediria el WAF.
UA_CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_EVERGREEN} Mobile Safari/537.36"
)
UA_CHROME_ESCRITORIO = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

UA_MOVIL_POR_NOMBRE = {
    "chrome_android": UA_CHROME_ANDROID,
    "googlebot_smartphone": UA_GOOGLEBOT_SMARTPHONE,
}

# Huellas TLS de curl_cffi por perfil. La de Android existe desde curl_cffi
# 0.8 (`chrome131_android`); si la instalada no la tiene, el spider se queda
# con la del job y lo dice en el log (ver `seo_crawler.sonda_paridad`).
IMPERSONATE_MOVIL = "chrome131_android"

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
    "contexto_playwright",
]
