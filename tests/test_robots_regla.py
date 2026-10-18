"""La linea de robots.txt que decide (decision 129), contrastada con Protego.

`regla_que_decide` repite el emparejamiento de Protego con el numero de linea
de cada regla. Lo que se comprueba aqui:

- en una tabla de casos (precedencia de la documentacion de Google, `*`, `$`,
  escapado, grupos fusionados, `Allow` en empate, `index.html`...) la linea es
  la que se espera Y el veredicto es el de Protego;
- con miles de robots.txt y URLs generados al azar, el veredicto coincide
  SIEMPRE con el de `EvaluadorRobots` (Protego tras `grupos_como_google`) y,
  con el UA entero, con Protego a secas, que es como decide Scrapy.
"""

from __future__ import annotations

import random

import pytest

protego = pytest.importorskip("protego")
from protego import Protego  # noqa: E402

from shared.robots_regla import regla_que_decide  # noqa: E402
from shared.robots_txt import PROCESADO, EvaluadorRobots, grupos_como_google  # noqa: E402

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

GOOGLE = """\
User-agent: *
Disallow: /privado/
Allow: /privado/publico.html

User-agent: Googlebot
Disallow: /solo-google/      # comentario
Allow: /p
Disallow: /

User-agent: GPTBot
User-agent: CCBot
Disallow: /

User-agent: GPTBot
Allow: /blog/

User-agent: OAI-SearchBot
Disallow: /*.pdf$
Disallow: /fish*
Allow: /fish/salmon
Disallow: /%7Ejoe/
Allow: /*?ok=
Disallow: /*?
Allow: /index.html
"""

# (agente, ruta, linea esperada (None = ninguna casa), permitida)
CASOS = [
    ("Googlebot", "/page", 7, True),            # Allow: /p (mas largo que /)
    ("Googlebot", "/", 8, False),
    ("Googlebot", "/solo-google/x", 6, False),
    ("GPTBot", "/blog/a", 15, True),             # grupos con el mismo nombre se fusionan
    ("GPTBot", "/tienda", 12, False),
    ("CCBot", "/blog/a", 12, False),             # el Allow de /blog/ es solo de GPTBot
    ("OAI-SearchBot", "/doc.pdf", 18, False),
    ("OAI-SearchBot", "/doc.pdf.html", None, True),  # $ ancla al final
    ("OAI-SearchBot", "/fish/salmon/x", 20, True),
    ("OAI-SearchBot", "/fishing", 19, False),
    ("OAI-SearchBot", "/~joe/a", 21, False),     # %7E y ~ son lo mismo
    ("OAI-SearchBot", "/buscar?ok=1", 22, True),  # mas largo gana
    ("OAI-SearchBot", "/buscar?q=1", 23, False),
    ("OAI-SearchBot", "/", 24, True),            # Allow /index.html permite /
    ("ClaudeBot", "/privado/a", 2, False),       # sin grupo propio: el de *
    ("ClaudeBot", "/privado/publico.html", 3, True),
    ("ClaudeBot", "/otra", None, True),
    ("Googlebot", "/robots.txt", None, True),    # robots.txt siempre se puede pedir
]


@pytest.mark.parametrize("agente,ruta,linea,permitida", CASOS)
def test_la_linea_ganadora_y_el_veredicto_de_protego(agente, ruta, linea, permitida):
    url = f"https://x.com{ruta}"
    r = regla_que_decide(GOOGLE, url, agente)
    assert r["linea"] == linea
    assert r["permitida"] is permitida
    ev = EvaluadorRobots(PROCESADO, GOOGLE)
    assert ev.permitido(url, agente) is permitida
    if linea is not None:
        assert r["texto"] == GOOGLE.splitlines()[linea - 1].strip()


def test_allow_gana_en_empate_y_la_linea_es_la_del_allow():
    texto = "User-agent: *\nDisallow: /a\nAllow: /a\n"
    r = regla_que_decide(texto, "https://x.com/a/b", "Googlebot")
    assert (r["permitida"], r["linea"], r["directiva"]) == (True, 3, "Allow")


def test_el_grupo_se_dice_como_esta_escrito():
    texto = "User-agent: *\nAllow: /\n\nUser-agent: googlebot/2.1\nDisallow: /x\n"
    r = regla_que_decide(texto, "https://x.com/x/1", "Googlebot")
    assert (r["grupo"], r["linea_grupo"], r["linea"]) == ("User-agent: googlebot/2.1", 4, 5)


def test_con_el_ua_entero_es_lo_que_decidio_scrapy():
    texto = "User-agent: Mozilla\nDisallow: /m/\n\nUser-agent: *\nDisallow: /todo/\n"
    r = regla_que_decide(texto, "https://x.com/m/1", CHROME, como_google=False)
    assert (r["permitida"], r["linea"]) == (False, 2)
    # Como Googlebot el grupo Mozilla no aplica.
    assert regla_que_decide(texto, "https://x.com/m/1", "Googlebot")["permitida"] is True


def test_sin_grupo_que_aplique_no_hay_linea():
    r = regla_que_decide("User-agent: Bingbot\nDisallow: /\n", "https://x.com/", "GPTBot")
    assert r["grupo"] is None and r["linea"] is None and r["permitida"] is True


# --- Contraste al azar -------------------------------------------------------

_AGENTES = ["*", "Googlebot", "googlebot-news", "GPTBot", "gptbot/1.0", "Google",
            "Mozilla", "OAI-SearchBot", "Claude*", "ClaudeBot", "CCBot"]
_PATRONES = ["/", "/a", "/a/", "/a/b", "/a*", "/*.pdf$", "/*?", "/*?x=", "/a$", "/b/*/c",
             "*.php", "/%7Euser", "/~user", "/caf%C3%A9", "/café", "/index.html",
             "/a/index.html", "/$", "/*", "/**/c", "/a%2Fb", "/x=y", "/x%3Dy",
             "https://x.com/a", "/ñ", "/a b", ""]
_RUTAS = ["/", "/a", "/a/", "/a/b", "/a/b/c", "/ab", "/doc.pdf", "/doc.pdf?x", "/b/z/c",
          "/q?x=1", "/q?", "/index.php", "/~user/x", "/%7euser", "/café", "/caf%C3%A9",
          "/a/index.html", "/a%2Fb", "/a/b?x=y", "/x=y", "/x%3Dy", "/ñ", "/a%20b",
          "/https://x.com/a", "/robots.txt", "/c"]


def _robots_al_azar(rnd: random.Random) -> str:
    lineas = []
    for _ in range(rnd.randint(1, 4)):
        for _ in range(rnd.randint(1, 2)):
            ua = rnd.choice(_AGENTES)
            lineas.append(rnd.choice(["User-agent: ", "user-agent:", "User agent "]) + ua)
        for _ in range(rnd.randint(0, 5)):
            d = rnd.choice(["Allow", "Disallow", "disallow", "Dissallow", "Crawl-delay"])
            v = rnd.choice(_PATRONES) if d != "Crawl-delay" else "5"
            lineas.append(f"{d}: {v}" + rnd.choice(["", "  # c"]))
        if rnd.random() < 0.3:
            lineas.append("Sitemap: https://x.com/s.xml")
        lineas.append("")
    return "\n".join(lineas)


def test_al_azar_el_veredicto_coincide_siempre_con_protego():
    rnd = random.Random(129)
    tokens = ["Googlebot", "GPTBot", "OAI-SearchBot", "ClaudeBot", "CCBot", "PerplexityBot"]
    comparados = 0
    for _ in range(600):
        texto = _robots_al_azar(rnd)
        parsers = {t: Protego.parse(grupos_como_google(texto, t)) for t in tokens}
        crudo = Protego.parse(texto)
        for ruta in rnd.sample(_RUTAS, 8):
            url = f"https://x.com{ruta}"
            for t in tokens:
                esperado = parsers[t].can_fetch(url, t)
                assert regla_que_decide(texto, url, t)["permitida"] is esperado, (texto, url, t)
                comparados += 1
            esperado = crudo.can_fetch(url, CHROME)
            assert regla_que_decide(texto, url, CHROME, como_google=False)["permitida"] is esperado
    assert comparados > 20000
