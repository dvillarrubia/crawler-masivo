"""robots.txt de prueba (Ola 7.3, decision 171): la logica, sin la API.

- `evaluar_lote` da lo mismo que `regla_que_decide` una a una (lo que ya se
  contrasta con Protego en `test_robots_regla.py`);
- casos limite contra Protego, en los dos modos (como Google con el token y
  como nuestro rastreo con el UA entero): comodines, `$`, agrupacion de
  `User-agent`, `Allow` frente a `Disallow` por longitud y en empate, grupos
  repetidos, escapado y lineas raras;
- `diagnostico`: lo que el parser se salta y los `Sitemap`.
"""

from __future__ import annotations

import pytest

from shared.robots_regla import diagnostico, evaluar_lote, regla_que_decide
from shared.robots_txt import LIMITE_BYTES, grupos_como_google

UA_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ROBOTS = """\
# Prueba de casos limite
User-agent: *
Disallow: /*.php$
Disallow: /buscar
Allow: /buscar/ayuda
Disallow: /*?*orden=
Allow: /*?pagina=

User-agent: Googlebot
User-agent: Bingbot
Disallow: /privado
Allow: /privado/
Disallow: /fotos/*.jpg

User-agent: googlebot-image
Disallow: /

User-agent: Google
Disallow: /solo-google-a-secas

User-agent: GPTBot
Disallow: /
User-agent: GPTBot
Allow: /blog/

User-agent: Mozilla
Disallow: /navegadores
"""

# (agente, ruta, permitida, linea) en modo google
CASOS_GOOGLE = [
    ("Googlebot", "/privado", False, 11),          # Disallow /privado
    ("Googlebot", "/privado/x", True, 12),         # Allow /privado/ es mas largo
    ("Googlebot", "/privado-no", False, 11),
    ("Googlebot", "/fotos/a/b.jpg", False, 13),    # * en medio
    ("Googlebot", "/fotos/b.jpg?x=1", False, 13),  # sin $ casa por prefijo
    ("Googlebot", "/index.php", True, None),       # el grupo * no se hereda
    ("Bingbot", "/privado", False, 11),            # dos User-agent, un grupo
    ("Googlebot-Image", "/cualquier", False, 16),  # su propio grupo, no el de Googlebot
    ("Googlebot", "/solo-google-a-secas", True, None),  # «Google» no es Googlebot para Google
    ("GPTBot", "/blog/a", True, 24),               # grupos repetidos se fusionan
    ("GPTBot", "/tienda", False, 22),
    ("CCBot", "/index.php", False, 3),             # $ ancla el final
    ("CCBot", "/index.php5", True, None),
    ("CCBot", "/index.php?x=1", True, None),       # con query ya no acaba en .php
    ("CCBot", "/buscar/ayuda", True, 5),           # Allow mas largo gana
    ("CCBot", "/buscar/otra", False, 4),
    ("CCBot", "/lista?pagina=2&orden=a", True, 7),  # /*?*orden= y /*?pagina= miden 10: gana Allow
    ("CCBot", "/lista?pagina=2", True, 7),
    ("CCBot", "/robots.txt", True, None),          # siempre se puede pedir
]


@pytest.mark.parametrize("agente,ruta,permitida,linea", CASOS_GOOGLE)
def test_casos_limite_como_google(agente, ruta, permitida, linea):
    r = regla_que_decide(ROBOTS, "https://x.com" + ruta, agente)
    assert (r["permitida"], r["linea"]) == (permitida, linea)


def test_empate_de_longitud_gana_allow():
    texto = "User-agent: *\nDisallow: /a*\nAllow: /a$\n"
    # `/a*` y `/a$` miden lo mismo: en empate gana Allow.
    r = regla_que_decide(texto, "https://x.com/a", "Googlebot")
    assert (r["permitida"], r["directiva"], r["linea"]) == (True, "Allow", 3)
    r = regla_que_decide(texto, "https://x.com/ab", "Googlebot")
    assert (r["permitida"], r["linea"]) == (False, 2)


def test_el_grupo_es_la_linea_del_propio_agente():
    # En un grupo con dos User-agent, la linea que nombra al que se prueba.
    r = regla_que_decide(ROBOTS, "https://x.com/privado", "Bingbot")
    assert r["grupo"] == "User-agent: Bingbot" and r["linea_grupo"] == 10
    assert r["texto"] == "Disallow: /privado"


def test_como_rastreador_el_grupo_se_busca_dentro_del_ua():
    """Protego, como Scrapy: con el UA entero de Googlebot aparecen dentro
    `mozilla`, `google` y `googlebot`, y gana el nombre MAS LARGO (decision 98)."""
    r = regla_que_decide(ROBOTS, "https://x.com/privado", UA_GOOGLEBOT, como_google=False)
    assert (r["permitida"], r["linea"]) == (False, 11)
    # Con el UA de Chrome solo aparece Mozilla.
    r = regla_que_decide(ROBOTS, "https://x.com/navegadores", UA_CHROME, como_google=False)
    assert (r["permitida"], r["linea"]) == (False, 27)


def test_lote_igual_que_uno_a_uno():
    urls = ["https://x.com" + c[1] for c in CASOS_GOOGLE] + ["/privado", "privado/x", ""]
    agentes = ["Googlebot", "GPTBot", "CCBot", "Bingbot"]
    lote = evaluar_lote(ROBOTS, urls, agentes)
    assert len(lote) == len(urls) * len(agentes)
    i = 0
    for a in agentes:
        for u in urls:
            esperado = dict(regla_que_decide(ROBOTS, u, a), agente=a, url=u)
            assert lote[i] == esperado
            i += 1


def test_una_ruta_suelta_vale_como_url():
    assert evaluar_lote(ROBOTS, ["/privado"], ["Googlebot"])[0]["permitida"] is False


# -- Contra Protego ------------------------------------------------------------

def test_el_veredicto_es_el_de_protego_en_los_dos_modos():
    protego = pytest.importorskip("protego")
    rutas = sorted({c[1] for c in CASOS_GOOGLE} | {"/", "/blog/", "/navegadores", "/privado/"})
    tokens = ["Googlebot", "Bingbot", "Googlebot-Image", "GPTBot", "CCBot", "Google"]
    for t in tokens:
        p = protego.Protego.parse(grupos_como_google(ROBOTS, t))
        for r in evaluar_lote(ROBOTS, ["https://x.com" + x for x in rutas], [t]):
            assert r["permitida"] == p.can_fetch(r["url"], t), (t, r["url"])
    p = protego.Protego.parse(ROBOTS)
    for ua in (UA_GOOGLEBOT, UA_CHROME, "CCBot/2.0 (https://commoncrawl.org/faq/)"):
        for r in evaluar_lote(ROBOTS, ["https://x.com" + x for x in rutas], [ua],
                              como_google=False):
            assert r["permitida"] == p.can_fetch(r["url"], ua), (ua, r["url"])


# -- Diagnostico ----------------------------------------------------------------

def test_diagnostico_de_lineas_raras():
    texto = (
        "Disallow: /antes-de-todo\n"          # 1: sin grupo
        "Sitemap: https://x.com/s.xml\n"      # 2
        "User-agent: *\n"                     # 3
        "Disallow: /a\n"                      # 4
        "Noindex: /b\n"                       # 5: Google no lo lee desde 2019
        "Crawl-delay: 10\n"                   # 6: se conoce (Google lo ignora, pero no es error)
        "esto no es nada\n"                   # 7
        "Dissallow: /c\n"                     # 8: errata que Protego tolera
        "\n"
        "User-agent: GPTBot\n"                # 10
        "Disallow: /\n"
    )
    d = diagnostico(texto)
    assert d["grupos"] == 2
    assert d["sitemaps"] == [{"linea": 2, "url": "https://x.com/s.xml"}]
    assert [(x["linea"], x["motivo"]) for x in d["ignoradas"]] == [
        (1, "regla_sin_user_agent"), (5, "directiva_desconocida"), (7, "no_es_directiva")]
    assert d["recortado"] is False


def test_diagnostico_recortado_a_500_kib():
    texto = "User-agent: *\n" + "Disallow: /x\n" * (LIMITE_BYTES // 12 + 10)
    assert diagnostico(texto)["recortado"] is True


def test_varios_user_agent_seguidos_son_un_grupo():
    assert diagnostico("User-agent: a\nUser-agent: b\nDisallow: /\nUser-agent: c\nAllow: /\n"
                       )["grupos"] == 2
