"""Topes de URLs por subdominio o carpeta (Ola 8, decision 172).

Tres capas, cada una con su forma de fallar:

- las reglas (`shared/topes_carpeta.py`): que cupo le toca a cada URL, y que
  una regla mal escrita se rechace en vez de ignorarse;
- el spider de verdad con el arnes: lo que no cabe NO se pide (no basta con no
  guardarlo), las semillas se piden siempre, el sitemap y las redirecciones
  pasan por el mismo cupo, y el recuento de lo dejado va a `jobs.topes_carpeta`;
- el worker: con un tope cortado y la frontera agotada, `finish_reason` es
  `tope_por_carpeta`, que es truncado para todo lo que afirma ausencias.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from shared.topes_carpeta import (
    MOTIVO,
    ClavesCompactas,
    Presupuesto,
    compilar,
    corto,
    describir,
    grupos_de,
)

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


# --- Reglas -------------------------------------------------------------------

def _grupos(url, reglas):
    return [(r.clave, g) for r, g in grupos_de(url, compilar(reglas))]


def test_la_regla_de_ruta_mas_concreta_gana_y_el_host_se_suma():
    reglas = {"blogs.uoc.edu/*": 2000, "/blog/": 500, "*": 300}
    assert _grupos("https://blogs.uoc.edu/economia/post-1", reglas) == [
        ("*", "blogs.uoc.edu/economia/"), ("blogs.uoc.edu/*", "blogs.uoc.edu/*")]
    # El prefijo antes que el comodin; `/blog` es la portada de `/blog/`.
    assert _grupos("https://www.blogs.uoc.edu/blog", reglas)[0] == ("/blog/", "blogs.uoc.edu/blog/")
    # Una pagina suelta de la raiz no esta en ninguna carpeta: solo el host.
    assert _grupos("https://blogs.uoc.edu/contacto", reglas) == [
        ("blogs.uoc.edu/*", "blogs.uoc.edu/*")]
    # El prefijo sin host vale en cada host por separado.
    assert _grupos("https://otro.es/blog/x", reglas) == [("/blog/", "otro.es/blog/")]


def test_entre_prefijos_el_mas_largo_y_a_igual_longitud_el_del_host():
    reglas = {"/blog/": 10, "/blog/2024/": 3, "x.com/blog/": 7, "x.com/*/": 1}
    assert _grupos("https://x.com/blog/2024/a", reglas)[0][0] == "/blog/2024/"
    assert _grupos("https://x.com/blog/a", reglas)[0][0] == "x.com/blog/"
    assert _grupos("https://y.com/blog/a", reglas)[0][0] == "/blog/"
    assert _grupos("https://x.com/tienda/a", reglas)[0] == ("x.com/*/", "x.com/tienda/")
    assert _grupos("https://y.com/tienda/a", reglas) == []


def test_una_regla_mal_escrita_se_rechaza_con_su_motivo():
    with pytest.raises(ValueError) as e:
        compilar({"blog/": 10, "/a/*/b": 5, "/x/": 0, "*": True, "/": 3})
    texto = str(e.value)
    for clave in ("'blog/'", "'/a/*/b'", "'/x/'", "'*'", "'/'"):
        assert clave in texto
    # `null` es la herencia quitando una regla: no es un error.
    assert [r.clave for r in compilar({"/blog/": None, "*": 3})] == ["*"]


def test_reglas_que_se_aceptaban_y_no_hacian_nada():
    # Un host con puerto no casaba nunca (la URL se compara por `hostname`).
    with pytest.raises(ValueError, match="sin puerto"):
        compilar({"blogs.x.com:8080/*": 5})
    # `"/*/"` sin host es `"*"`: con las dos, una se ignoraba en silencio.
    with pytest.raises(ValueError, match="misma regla"):
        compilar({"*": 300, "/*/": 2})
    assert [r.clave for r in compilar({"/*/": 2})] == ["*"]


def test_jobconfig_valida_los_topes_y_los_hereda_por_regla():
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig
    from shared.clientes import fusionar_config

    with pytest.raises(Exception, match="no es un host"):
        JobConfig(topes_por_carpeta={"blog/": 10})
    cliente = {"topes_por_carpeta": {"*": 300, "/blog/": 500}}
    perfil = {"topes_por_carpeta": {"/blog/": 50}}
    job = {"topes_por_carpeta": {"*": None}}
    final = fusionar_config(cliente, perfil, job)
    assert final["topes_por_carpeta"] == {"/blog/": 50}
    assert JobConfig(**final).topes_por_carpeta == {"/blog/": 50}


def test_la_herencia_reconoce_la_misma_regla_con_otra_grafia():
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig
    from shared.clientes import fusionar_config

    cliente = {"topes_por_carpeta": {"blogs.x.com": 2000, "*": 300}}
    # El perfil la sobrescribe aunque la escriba de otra forma.
    final = fusionar_config(cliente, {"topes_por_carpeta": {"blogs.x.com/*": 500}})
    assert final["topes_por_carpeta"] == {"*": 300, "blogs.x.com/*": 500}
    assert JobConfig(**final).topes_por_carpeta["blogs.x.com/*"] == 500
    # Y un `null` con otra grafia la quita de verdad.
    final = fusionar_config(cliente, {"topes_por_carpeta": {"https://www.blogs.x.com/": None}})
    assert final["topes_por_carpeta"] == {"*": 300}
    # Dos grafias en la MISMA capa siguen siendo un error.
    final = fusionar_config({"topes_por_carpeta": {"blogs.x.com": 1, "blogs.x.com/*": 2}})
    with pytest.raises(Exception, match="misma regla"):
        JobConfig(**final)


def test_claves_compactas_es_un_conjunto_exacto():
    import random

    c = ClavesCompactas()
    azar = random.Random(7)
    claves = [azar.getrandbits(64) for _ in range(20_000)] + [0, 1, 2**64 - 1]
    for k in claves:
        c.add(k)
        c.add(k)
    assert len(c) == len(set(claves))
    assert all(k in c for k in claves)
    assert sum(azar.getrandbits(64) in c for _ in range(20_000)) == 0
    # Mucho menos que un `set` de ints (unos 70 bytes por clave).
    assert len(c._tabla) * 8 / len(c) < 30


def test_el_presupuesto_cuenta_una_vez_cada_url_dejada():
    p = Presupuesto(compilar({"/blog/": 2, "a.com/*": 3}))
    assert [p.admitir(f"https://a.com/blog/{i}", i) for i in range(4)] == [True, True, False, False]
    # La misma URL vista desde otra pagina no cuenta dos veces.
    assert p.admitir("https://a.com/blog/3", 3) is False
    # Ya admitida: se deja pasar (el dupefilter decide) sin gastar cupo.
    assert p.admitir("https://a.com/blog/0", 0) is True
    # El cupo del host se llena con lo de fuera del blog.
    assert p.admitir("https://a.com/x", 10) is True
    assert p.admitir("https://a.com/y", 11) is False
    r = p.resumen()
    assert r["sin_rastrear"] == 3 and r["sin_rastrear_minimo"] is True
    assert {c["grupo"]: c["sin_rastrear"] for c in r["cortados"]} == {
        "a.com/blog/": 2, "a.com/*": 1}


def test_corto_y_describir():
    config = {"topes_por_carpeta": {"*": 2}}
    p = Presupuesto(compilar(config["topes_por_carpeta"]))
    for i in range(3):
        p.admitir(f"https://a.com/lleno/{i}", i)
    p.admitir("https://a.com/holgado/1", 50)
    assert corto(p.resumen())
    assert "1 parte" in describir(p.resumen())
    # Sin cortes no hay nada que callar.
    assert not corto({"sin_rastrear": 0, "cortados": []}) and not corto(None)
    assert describir({"sin_rastrear": 0}) is None
    # Un resumen ilegible: no se sabe, y no se afirma.
    assert corto({"sin_rastrear": "?"}) and corto(["raro"])


def test_la_lista_recortada_lo_dice_en_su_regla(monkeypatch):
    import shared.topes_carpeta as tc

    monkeypatch.setattr(tc, "MAX_GRUPOS_GUARDADOS", 1)
    config = {"topes_por_carpeta": {"*": 1}}
    p = Presupuesto(compilar(config["topes_por_carpeta"]))
    for i, carpeta in enumerate(("a", "a", "b", "b", "c")):
        p.admitir(f"https://x.com/{carpeta}/{i}", i)
    r = p.resumen()
    assert len(r["cortados"]) == 1 and r["reglas"][0]["lista_completa"] is False
    assert r["reglas"][0]["grupos_cortados"] == 2


def test_tope_por_carpeta_es_truncado_para_la_comparacion():
    from shared.comparacion import MOTIVOS_TRUNCADO

    assert MOTIVO in MOTIVOS_TRUNCADO


# --- Worker ---------------------------------------------------------------------

def test_el_worker_marca_tope_por_carpeta_solo_si_la_frontera_se_agoto():
    pytest.importorskip("scrapy")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crawler"))
    from types import SimpleNamespace

    from worker import _con_topes

    cortado = SimpleNamespace(topes_carpeta={"sin_rastrear": 4})
    assert _con_topes(cortado, "finished") == MOTIVO
    assert _con_topes(cortado, "max_urls_reached") == "max_urls_reached"
    assert _con_topes(cortado, "max_runtime_reached") == "max_runtime_reached"
    assert _con_topes(SimpleNamespace(topes_carpeta={"sin_rastrear": 0}), "finished") == "finished"
    assert _con_topes(SimpleNamespace(topes_carpeta=None), "finished") == "finished"


# --- Spider real con el arnes ---------------------------------------------------

def _rutas():
    def html(cuerpo):
        return f"<html><head><title>t</title></head><body>{cuerpo}</body></html>"

    enlaces = "".join(f'<a href="/blog/{i}">b{i}</a>' for i in range(6))
    enlaces += "".join(f'<a href="/docs/{i}">d{i}</a>' for i in range(5))
    enlaces += '<a href="/contacto">c</a><a href="/salto">s</a>'
    rutas = {
        "/t-home": [200, {}, html(enlaces)],
        "/contacto": [200, {}, html("<p>c</p>")],
        # Una redireccion a una carpeta llena: el destino tampoco se pide.
        "/salto": [301, {"Location": "/blog/99"}, ""],
        "/blog/99": [200, {}, html("<p>tras el salto</p>")],
        "/robots.txt": [200, {"Content-Type": "text/plain"},
                        "User-agent: *\nSitemap: http://127.0.0.1:{port}/sm.xml\n"],
        "/sm.xml": [200, {"Content-Type": "application/xml"},
                    '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + "".join(f"<url><loc>http://127.0.0.1:{{port}}/blog/s{i}</loc></url>"
                              for i in range(3))
                    + "</urlset>"],
    }
    for i in range(6):
        rutas[f"/blog/{i}"] = [200, {}, html("<p>b</p>")]
    for i in range(3):
        rutas[f"/blog/s{i}"] = [200, {}, html("<p>s</p>")]
    for i in range(5):
        rutas[f"/docs/{i}"] = [200, {}, html("<p>d</p>")]
    return rutas


def _rastrear(config, seeds=("http://127.0.0.1:{port}/t-home",)):
    pytest.importorskip("scrapy")
    entrada = {"seeds": list(seeds), "config": config, "rutas": _rutas(), "ver_job": True}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    peticiones = json.loads(proc.stdout.split("@@PETICIONES@@", 1)[1].split("\n", 1)[0])
    job = json.loads(proc.stdout.split("@@JOB@@", 1)[1].split("\n", 1)[0])
    paginas = {i["url"].split("OTRO", 1)[-1] for i in items if i["_tipo"] == "PageItem"}
    topes = next((a["topes_carpeta"] for a in job if "topes_carpeta" in a), None)
    return paginas, peticiones, topes


def _rutas_pedidas(peticiones):
    # Sin las sondas de soft 404 (`/blog/zz<hex>`, decision 105): no son URLs
    # del censo ni gastan cupo.
    return {ruta.split("?")[0] for _t, _host, ruta in peticiones if "/zz" not in ruta}


def test_el_spider_no_pide_lo_que_no_cabe_y_lo_cuenta():
    paginas, peticiones, topes = _rastrear(
        {"max_depth": 2, "use_sitemap": True, "topes_por_carpeta": {"/blog/": 2, "*": 3}})
    blog = {p for p in paginas if p.startswith("/blog/")}
    docs = {p for p in paginas if p.startswith("/docs/")}
    assert len(blog) == 2, paginas
    assert len(docs) == 3, paginas
    # Lo de fuera de cualquier cupo se rastrea entero.
    assert {"/t-home", "/contacto", "/salto"} <= paginas
    # No basta con no guardarlo: no se pide.
    pedidas = _rutas_pedidas(peticiones)
    assert len({p for p in pedidas if p.startswith("/blog/")}) == 2, pedidas
    assert len({p for p in pedidas if p.startswith("/docs/")}) == 3, pedidas
    # 6 enlaces + 3 del sitemap + 1 destino de salto = 10 en el blog; 5 en docs.
    assert topes is not None
    por_grupo = {c["grupo"].split("/", 1)[1]: c for c in topes["cortados"]}
    assert por_grupo["blog/"]["rastreadas"] == 2
    assert por_grupo["blog/"]["sin_rastrear"] == 8
    assert por_grupo["docs/"]["rastreadas"] == 3
    assert por_grupo["docs/"]["sin_rastrear"] == 2
    assert topes["sin_rastrear"] == 10


def test_una_semilla_se_pide_aunque_su_cupo_este_lleno():
    paginas, _, topes = _rastrear(
        {"max_depth": 1, "use_sitemap": False, "topes_por_carpeta": {"/docs/": 1}},
        seeds=("http://127.0.0.1:{port}/t-home", "http://127.0.0.1:{port}/docs/0",
               "http://127.0.0.1:{port}/docs/1"))
    assert {"/docs/0", "/docs/1"} <= paginas
    assert {p for p in paginas if p.startswith("/docs/")} == {"/docs/0", "/docs/1"}
    assert topes["cortados"][0]["rastreadas"] == 2


def test_sin_topes_ni_resumen_ni_cambio():
    paginas, _, topes = _rastrear({"max_depth": 2, "use_sitemap": False})
    assert len({p for p in paginas if p.startswith("/blog/")}) == 7
    assert topes is None


def test_con_topes_que_no_se_llenan_no_hay_corte():
    _, _, topes = _rastrear({"max_depth": 2, "use_sitemap": False,
                             "topes_por_carpeta": {"/docs/": 50}})
    assert topes["sin_rastrear"] == 0 and topes["cortados"] == []
    assert topes["reglas"][0]["rastreadas"] == 5
