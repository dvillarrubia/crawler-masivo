"""El adaptador WordPress en el spider de verdad (decision 161), contra un
WordPress falso servido en local (ninguna peticion sale a la red).

- Lee la REST API paginando por `X-WP-TotalPages`, con dos peticiones a la vez
  como mucho, sin pedir `/wp/v2/users` ni los tipos internos del nucleo.
- Lo leido son `EntidadesAdaptadorItem`, nunca paginas del censo: ni la REST
  API ni las sondas (pagina N+1, paginas de adjunto) salen como `PageItem`.
- Si la REST API esta cerrada o robots.txt la prohibe, se anota y el rastreo
  sigue igual. Apagado o en modo lista, no se pide nada.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
J = {"Content-Type": "application/json; charset=UTF-8", "_retardo_ms": "120"}
S = "http://localhost:{port}"


def _lista(datos, total, paginas):
    return [200, {**J, "X-WP-Total": str(total), "X-WP-TotalPages": str(paginas)},
            json.dumps(datos)]


def _wordpress(**cambios):
    rutas = {
        "/wp-json/": [200, J, json.dumps({
            "name": "Blog", "home": S, "show_on_front": "posts",
            "namespaces": ["oembed/1.0", "wp/v2", "yoast/v1"]})],
        "/wp-json/wp/v2/types": [200, J, json.dumps({
            "post": {"rest_base": "posts", "rest_namespace": "wp/v2"},
            "page": {"rest_base": "pages", "rest_namespace": "wp/v2"},
            "attachment": {"rest_base": "media", "rest_namespace": "wp/v2"},
            "wp_block": {"rest_base": "blocks", "rest_namespace": "wp/v2"},
            "wp_font_face": {"rest_base": "font-families/(?P<font_family_id>[\\d]+)/font-faces"},
        })],
        "/wp-json/wp/v2/taxonomies": [200, J, json.dumps({
            "category": {"rest_base": "categories", "types": ["post"]},
            "post_tag": {"rest_base": "tags", "types": ["post"]},
            "nav_menu": {"rest_base": "menus", "types": ["nav_menu_item"]},
        })],
        "/wp-json/wp/v2/posts": _lista([
            {"id": 1, "link": f"{S}/entrada-1/", "status": "publish"},
            {"id": 2, "link": f"{S}/entrada-2/", "status": "publish"}], 3, 2),
        "/wp-json/wp/v2/posts?page=2": _lista([
            {"id": 3, "link": f"{S}/entrada-3/", "status": "publish"}], 3, 2),
        "/wp-json/wp/v2/pages": _lista([{"id": 9, "link": f"{S}/sobre/", "status": "publish"}], 1, 1),
        "/wp-json/wp/v2/media": _lista([
            {"id": 7, "link": f"{S}/entrada-1/foto/", "post": 1, "mime_type": "image/jpeg",
             "source_url": f"{S}/wp-content/uploads/foto.jpg"}], 1, 1),
        "/wp-json/wp/v2/categories": _lista([
            {"id": 3, "link": f"{S}/category/noticias/", "count": 2, "parent": 0}], 1, 1),
        "/wp-json/wp/v2/tags": _lista([{"id": 5, "link": f"{S}/tag/una/", "count": 1}], 1, 1),
        # Sondas: la home tiene 3 entradas -> /page/4/ no puede existir.
        "/page/4/": [200, {}, '<html><head><title>Blog</title></head><body>'
                     '<a href="/page/5/">Siguiente</a></body></html>'],
        "/category/noticias/page/3/": [404, {}, "<html><body>No</body></html>"],
        "/entrada-1/foto/": [200, {}, "<html><head><title>foto</title></head>"
                             "<body><img src='/wp-content/uploads/foto.jpg'></body></html>"],
    }
    rutas.update(cambios)
    return rutas


def _rastrear(rutas, config=None, **extra):
    entrada = {"seeds": ["http://localhost:{port}/"], "config": config or {"max_depth": 2},
               "rutas": rutas, "ver_job": True,
               "vigilar_concurrencia": ["/wp-json", "/page/", "/category/noticias/page",
                                        "/tag/una/page", "/entrada-1/foto"], **extra}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    peticiones = json.loads(proc.stdout.split("@@PETICIONES@@", 1)[1].split("\n", 1)[0])
    job = json.loads(proc.stdout.split("@@JOB@@", 1)[1].split("\n", 1)[0])
    conc = json.loads(proc.stdout.split("@@CONCURRENCIA@@", 1)[1].split("\n", 1)[0])
    resumenes = [a["adapters"]["wordpress"] for a in job if "adapters" in a]
    return items, [p[2] for p in peticiones], (resumenes[-1] if resumenes else None), conc


def _entidades(items):
    salida = []
    for i in items:
        if i["_tipo"] == "EntidadesAdaptadorItem":
            assert i["adaptador"] == "wordpress"
            salida.extend(i["entidades"])
    return salida


def test_lee_la_rest_api_entera_y_sondea_sin_tocar_el_censo():
    items, pedidas, resumen, conc = _rastrear(_wordpress())
    entidades = _entidades(items)
    por_tipo: dict[str, list] = {}
    for e in entidades:
        por_tipo.setdefault(e["tipo"], []).append(e)
    assert sorted(e["url"] for e in por_tipo["post"]) == [
        "http://SEMILLA/entrada-1/", "http://SEMILLA/entrada-2/", "http://SEMILLA/entrada-3/"], \
        "la pagina 2 se pide por X-WP-TotalPages"
    assert {e["tipo"] for e in entidades} >= {"post", "page", "attachment", "category",
                                              "post_tag", "sonda_paginacion", "sonda_adjunto"}
    post1 = next(e for e in por_tipo["post"] if e["id_externo"] == "1")
    assert post1["en_alcance"] is True and post1["url_hash"] and post1["url_hash_alt"]
    assert por_tipo["category"][0]["cuenta"] == 2

    sondas = {e["url"]: e for e in por_tipo["sonda_paginacion"]}
    home = sondas["http://SEMILLA/page/4/"]
    assert home["status_code"] == 200 and home["datos"]["enlaza_a_la_siguiente"] is True
    assert sondas["http://SEMILLA/category/noticias/page/3/"]["status_code"] == 404
    assert "http://SEMILLA/tag/una/page/2/" in sondas
    (adj,) = por_tipo["sonda_adjunto"]
    assert adj["status_code"] == 200 and "html" in adj["datos"]["content_type"]

    # Nada de esto es una pagina del censo.
    paginas = [i["url"] for i in items if i["_tipo"] == "PageItem"]
    assert not [u for u in paginas if "wp-json" in u or "/page/4/" in u or "/foto/" in u]
    assert "http://OTRO/home" in paginas, "el rastreo sigue como siempre"

    # Ni usuarios ni tipos internos; dos a la vez como mucho.
    assert not [p for p in pedidas if "/users" in p or "/blocks" in p or "font-" in p
                or "/menus" in p]
    assert 1 <= conc["max"] <= 2, conc

    assert resumen["detectado"] is True and resumen["rest"] == "abierta"
    assert resumen["plugin_seo"] == "Yoast SEO"
    assert resumen["tipos"]["post"]["total"] == 3 and resumen["tipos"]["post"]["leidas"] == 3
    assert resumen["cortada"] is None
    assert resumen["sondas"] == {"paginacion": 3, "adjuntos": 1}


def test_con_la_rest_api_cerrada_se_anota_y_el_rastreo_sigue():
    items, pedidas, resumen, _ = _rastrear(_wordpress(**{
        "/wp-json/": [401, J, json.dumps({"code": "rest_login_required"})]}))
    assert _entidades(items) == []
    assert resumen["detectado"] is False and resumen["rest"] == "cerrada"
    assert resumen["codigo"] == 401
    assert "http://OTRO/home" in [i["url"] for i in items if i["_tipo"] == "PageItem"]
    assert not [p for p in pedidas if p.startswith("/wp-json/wp/v2")]


def test_si_robots_txt_la_prohibe_no_se_pide():
    items, pedidas, resumen, _ = _rastrear(
        _wordpress(**{"/robots.txt": [200, {"Content-Type": "text/plain"},
                                      "User-agent: *\nDisallow: /wp-json/\n"]}),
        robots_obey=True)
    assert not [p for p in pedidas if p.startswith("/wp-json")]
    assert resumen["rest"] == "bloqueada_por_robots"
    assert _entidades(items) == []


def test_un_429_corta_la_lectura_sin_reintentar_en_bucle():
    items, pedidas, resumen, _ = _rastrear(_wordpress(**{
        "/wp-json/wp/v2/posts": [429, {**J}, "{}"]}), settings={"RETRY_TIMES": 0})
    assert resumen["cortada"] == "429"
    assert pedidas.count("/wp-json/wp/v2/posts?per_page=100&page=1&_fields="
                         "id,link,status,type,modified_gmt") <= 1


@pytest.mark.parametrize("config", [
    {"max_depth": 2, "adapters": {"wordpress": {"modo": "off"}}},
    {"max_depth": 0},  # modo lista (decision 128)
])
def test_apagado_o_en_modo_lista_no_se_pide_nada(config):
    items, pedidas, resumen, _ = _rastrear(_wordpress(), config=config)
    assert not [p for p in pedidas if p.startswith("/wp-json")]
    assert _entidades(items) == [] and resumen is None
