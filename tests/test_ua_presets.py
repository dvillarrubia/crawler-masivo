"""Presets de User-Agent del formulario y con qué grupo de robots.txt casan.

Un preset de bot (Googlebot, GPTBot, ClaudeBot...) no solo cambia la cabecera:
robots.txt se evalúa con ese UA, así que el rastreo obedece las reglas del grupo
`User-agent: GPTBot` y no las de un navegador. Este test lo comprueba con el
`RobotsTxtMiddleware` de Scrapy y el parser que usa de verdad (Protego), con
las cadenas tal cual están en `frontend/app.js` (decisión 98).
"""

from __future__ import annotations

import pathlib
import re

import pytest

scrapy = pytest.importorskip("scrapy")

from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware  # noqa: E402
from scrapy.exceptions import IgnoreRequest  # noqa: E402
from scrapy.http import Request  # noqa: E402
from scrapy.robotstxt import ProtegoRobotParser  # noqa: E402
from scrapy.utils.test import get_crawler  # noqa: E402

RAIZ = pathlib.Path(__file__).resolve().parent.parent
APP_JS = (RAIZ / "frontend" / "app.js").read_text(encoding="utf-8")
INDEX = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")


def _objeto_js(nombre: str) -> dict[str, str]:
    inicio = APP_JS.index(f"const {nombre} = {{")
    cuerpo = APP_JS[inicio:APP_JS.index("\n};", inicio)]
    version = re.search(r"const CHROME_EVERGREEN = '([^']+)'", APP_JS).group(1)
    pares = re.findall(r"^\s*([a-z_]+):\s*(['`])(.*?)\2,\s*$", cuerpo, re.M)
    return {k: v.replace("${CHROME_EVERGREEN}", version) for k, _, v in pares}


PRESETS = _objeto_js("UA_PRESETS")
ETIQUETAS = _objeto_js("UA_LABELS")
TOKENS = _objeto_js("UA_TOKEN_ROBOTS")

BOTS_PEDIDOS = {"googlebot_mobile", "bingbot", "gptbot", "oai_searchbot",
                "claudebot", "perplexitybot"}

# Un grupo por bot, cada uno prohibiendo SU ruta, y el comodín prohibiendo otra.
ROBOTS = "\n".join(
    [f"User-agent: {t}\nDisallow: /solo-{t.lower()}\nCrawl-delay: {i + 2}\n"
     for i, t in enumerate(sorted(set(TOKENS.values())))]
    + ["User-agent: *\nDisallow: /todos\nCrawl-delay: 1\n"]
).encode()


def test_los_presets_se_leen_del_frontend():
    assert len(PRESETS) >= 12, "el test no ha sabido leer UA_PRESETS"
    assert BOTS_PEDIDOS <= set(PRESETS)
    assert set(TOKENS) <= set(PRESETS)
    assert "W.X.Y.Z" not in "".join(PRESETS.values())
    assert "${" not in "".join(PRESETS.values())


def test_cada_preset_tiene_etiqueta_y_opcion_en_el_formulario():
    select = INDEX[INDEX.index('x-model="createForm.user_agent_preset"'):]
    select = select[:select.index("</select>")]
    opciones = set(re.findall(r'<option value="([a-z_]+)"', select))
    assert opciones == set(PRESETS) | {"custom"}
    assert set(ETIQUETAS) == set(PRESETS) | {"custom"}


@pytest.mark.parametrize("preset", sorted(TOKENS))
def test_el_token_de_robots_va_en_la_cadena(preset):
    assert TOKENS[preset].lower() in PRESETS[preset].lower()


@pytest.mark.parametrize("preset", ["chrome_win", "chrome_mac", "safari_mac",
                                    "firefox_win", "edge_win"])
def test_un_navegador_no_lleva_token_de_bot(preset):
    assert not any(t.lower() in PRESETS[preset].lower() for t in TOKENS.values())


def _middleware(ua: str):
    crawler = get_crawler(settings_dict={"USER_AGENT": ua, "ROBOTSTXT_OBEY": True})
    mw = RobotsTxtMiddleware.from_crawler(crawler)
    return crawler, mw, ProtegoRobotParser.from_crawler(crawler, ROBOTS)


def _bloqueada(mw, rp, ruta: str, cabecera: str | None = None) -> bool:
    req = Request(f"https://x.com{ruta}")
    if cabecera:
        req.headers[b"User-Agent"] = cabecera
    try:
        mw.process_request_2(rp, req)
        return False
    except IgnoreRequest:
        return True


@pytest.mark.parametrize("preset", sorted(TOKENS))
def test_robots_se_evalua_con_el_grupo_del_bot(preset):
    """El robots de Scrapy corre ANTES que nuestro UA (100 < 400), así que lee
    el `USER_AGENT` que el worker pasa con `-s`. Con la cabecera ya puesta (si
    alguien cambia el orden) tiene que salir lo mismo."""
    ua = PRESETS[preset]
    _, mw, rp = _middleware(ua)
    propio = f"/solo-{TOKENS[preset].lower()}"
    otros = {f"/solo-{t.lower()}" for t in TOKENS.values()} - {propio}
    for cabecera in (None, ua):
        assert _bloqueada(mw, rp, propio, cabecera), "no se aplica su grupo"
        assert not any(_bloqueada(mw, rp, r, cabecera) for r in otros)
        assert not _bloqueada(mw, rp, "/todos", cabecera), (
            "con grupo propio, el comodín no se aplica")


def test_un_navegador_cae_en_el_comodin():
    _, mw, rp = _middleware(PRESETS["chrome_win"])
    assert _bloqueada(mw, rp, "/todos")
    assert not _bloqueada(mw, rp, "/solo-gptbot")


@pytest.mark.parametrize("preset", ["gptbot", "claudebot", "googlebot_mobile"])
def test_el_crawl_delay_sale_del_mismo_grupo(preset):
    from seo_crawler.middlewares import RobotsCrawlDelayMiddleware, crawl_delay_aplicable

    crawler, _, rp = _middleware(PRESETS[preset])
    cd = RobotsCrawlDelayMiddleware(crawler)
    orden = sorted(set(TOKENS.values()))
    esperado = orden.index(TOKENS[preset]) + 2
    assert crawl_delay_aplicable(rp, cd._useragent, 100) == esperado


def test_el_orden_de_middlewares_es_el_que_supone_el_test():
    from scrapy.settings.default_settings import DOWNLOADER_MIDDLEWARES_BASE
    from seo_crawler import settings as s

    robots = DOWNLOADER_MIDDLEWARES_BASE[
        "scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware"]
    assert robots < s.DOWNLOADER_MIDDLEWARES["seo_crawler.middlewares.UserAgentMiddleware"]


def test_un_preset_evergreen_guardado_con_otra_version_sigue_siendo_ese_preset():
    """Subir `CHROME_EVERGREEN` no puede dejar huerfanos los perfiles guardados.

    `presetDeUserAgent` en `app.js` casa los presets que llevan la version de
    Chrome dentro ignorando esa version. Aqui se comprueba con los mismos datos
    que lee la funcion: que la lista de evergreen son EXACTAMENTE los que usan
    la constante (si se anade otro y se olvida la lista, falla) y que, quitada
    la version, ninguno se confunde con otro preset.
    """
    m = re.search(r"const RE_VERSION_CHROME = /(.+)/;", APP_JS)
    assert m, "no se encuentra RE_VERSION_CHROME en app.js"
    patron = re.compile(m.group(1).replace("\\/", "/"))
    lista = re.search(r"const UA_PRESETS_EVERGREEN = \[([^\]]*)\]", APP_JS).group(1)
    evergreen = set(re.findall(r"'([a-z_]+)'", lista))
    inicio = APP_JS.index("const UA_PRESETS = {")
    cuerpo = APP_JS[inicio:APP_JS.index("\n};", inicio)]
    con_constante = set(re.findall(r"^\s*([a-z_]+):.*\$\{CHROME_EVERGREEN\}", cuerpo, re.M))
    assert evergreen == con_constante

    def sin_version(s):
        return patron.sub("Chrome/*", s, count=1)

    for k in evergreen:
        vieja = patron.sub("Chrome/150.0.1.2", PRESETS[k], count=1)
        assert vieja != PRESETS[k]
        candidatos = [p for p in evergreen if sin_version(PRESETS[p]) == sin_version(vieja)]
        assert candidatos == [k]
    # Un preset fijo con otra version de Chrome NO casa: solo se relajan los
    # evergreen, asi que chrome_win y edge_win siguen distinguiendose.
    assert sin_version(PRESETS["chrome_win"]) != sin_version(PRESETS["edge_win"])
    assert "presetDeUserAgent(cfg.user_agent)" in APP_JS
