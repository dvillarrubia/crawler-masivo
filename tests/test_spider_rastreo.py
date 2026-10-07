"""SeoSpider real contra un sitio local: redirecciones, meta refresh, sitemaps
y filtros que antes perdian paginas sin avisar (issue #25, aristas de #24).

El sitio y el arnes estan en ``spider_harness.py``. Se lanza en un subproceso
porque el reactor de Twisted no se puede reiniciar dentro del mismo proceso.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


def _rastrear(**extra):
    entrada = {
        "seeds": ["http://localhost:{port}/"],
        "config": {"max_depth": 2, "exclude_patterns": ["*/tag/*"]},
        **extra,
    }
    proc = subprocess.run(
        [sys.executable, HARNESS, json.dumps(entrada)],
        capture_output=True, text=True, timeout=120,
    )
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = {i["url"]: i for i in items if i["_tipo"] == "PageItem"}
    enlaces = [i for i in items if i["_tipo"] == "LinkItem"]
    return paginas, enlaces, proc.stderr


@pytest.fixture(scope="module")
def rastreo():
    return _rastrear()


@pytest.fixture(scope="module")
def rastreo_navegador():
    """La redireccion la sigue quien descarga, como con render JS."""
    return _rastrear(simular_navegador=True)


def test_semilla_que_redirige_a_otro_host_sigue_rastreando(rastreo):
    paginas, _, _ = rastreo
    semilla = paginas["http://SEMILLA/"]
    assert semilla["status_code"] == 301
    assert semilla["redirect_url"] == "http://OTRO/home"
    # El destino conserva la profundidad: una redireccion no es un clic
    assert paginas["http://OTRO/home"]["crawl_depth"] == 0
    assert paginas["http://OTRO/home"]["is_internal"] is True
    assert "http://OTRO/a" in paginas


def test_redireccion_a_url_ya_vista_queda_registrada(rastreo):
    paginas, _, _ = rastreo
    vieja = paginas["http://OTRO/a-old"]
    assert vieja["status_code"] == 301
    assert vieja["redirect_url"] == "http://OTRO/a"
    assert vieja["resource_type"] == "redirect"


def test_meta_refresh_no_revienta_parse(rastreo):
    paginas, _, _ = rastreo
    mr = paginas["http://OTRO/mr"]
    assert mr["status_code"] == 200
    assert mr["redirect_url"] == "http://OTRO/mr-dest"
    assert mr["indexability_status"] == "Redirect (meta refresh)"
    assert "http://OTRO/mr-dest" in paginas


def test_href_malformado_no_anula_la_pagina(rastreo):
    paginas, enlaces, _ = rastreo
    assert any(e["to_url"] == "http://OTRO/after-bad" for e in enlaces)
    assert "http://OTRO/after-bad" in paginas


def test_exclude_en_formato_glob_no_tumba_el_rastreo(rastreo):
    paginas, _, stderr = rastreo
    assert "http://OTRO/tag/x" not in paginas
    assert "http://OTRO/a" in paginas
    assert "*/tag/*" in stderr  # se avisa del patron que no es regex


def test_rel_con_comas_es_nofollow(rastreo):
    paginas, enlaces, _ = rastreo
    (nf,) = [e for e in enlaces if e["to_url"] == "http://OTRO/nf"]
    assert nf["follow"] is False
    assert "http://OTRO/nf" not in paginas


def test_urls_del_sitemap_entran_a_profundidad_1(rastreo):
    paginas, _, stderr = rastreo
    assert paginas["http://SEMILLA/solo-sitemap"]["crawl_depth"] == 1
    # Con max_depth=2 su enlace se rastrea; antes entraba a profundidad 3
    assert paginas["http://SEMILLA/nivel2"]["crawl_depth"] == 2
    assert "Sitemap incompleto" not in stderr


def test_pagina_final_de_redireccion_no_sale_canonicalizada(rastreo):
    paginas, _, _ = rastreo
    assert paginas["http://OTRO/home"]["indexability_status"] == "Indexable"


# ---------------------------------------------------------------------------
# Rama de la cadena: redirecciones seguidas fuera del spider (render JS)
# ---------------------------------------------------------------------------

def test_navegador_registra_el_salto_de_la_semilla(rastreo_navegador):
    paginas, _, _ = rastreo_navegador
    semilla = paginas["http://SEMILLA/"]
    assert semilla["status_code"] == 301
    assert semilla["redirect_url"] == "http://OTRO/home"


def test_navegador_semilla_a_otro_host_sigue_rastreando(rastreo_navegador):
    paginas, _, stderr = rastreo_navegador
    assert paginas["http://OTRO/home"]["is_internal"] is True
    assert "http://OTRO/a" in paginas
    assert "redirige a otro host" in stderr


def test_navegador_canonical_se_compara_con_la_url_final(rastreo_navegador):
    paginas, _, _ = rastreo_navegador
    # Antes se comparaba con la URL pedida (la semilla) y salia Canonicalised
    assert paginas["http://OTRO/home"]["indexability_status"] == "Indexable"


def test_navegador_meta_refresh_seguida_queda_como_salto(rastreo_navegador):
    paginas, _, _ = rastreo_navegador
    mr = paginas["http://OTRO/mr"]
    assert mr["status_code"] == 200
    assert mr["redirect_url"] == "http://OTRO/mr-dest"
    assert mr["indexability_status"] == "Redirect (meta refresh)"


# ---------------------------------------------------------------------------
# robots.txt (R6 de #25)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rastreo_robots():
    """robots.txt prohibe /privado/, que /home enlaza."""
    return _rastrear(robots_bloquea_privado=True)


@pytest.fixture(scope="module")
def rastreo_robots_auditoria():
    return _rastrear(robots_bloquea_privado=True, robots_mode="audit")


def test_la_url_que_robots_prohibe_se_guarda(rastreo_robots):
    """Como hace Screaming Frog. No se pide —robots.txt se respeta— pero existe
    y el sitio la enlaza: o es un bloqueo por error sobre contenido que deberia
    posicionar, o es intencionado y son enlaces gastando presupuesto de rastreo.
    Antes el IgnoreRequest se descartaba y la URL no aparecia en ningun sitio.
    """
    paginas, _, _ = rastreo_robots
    bloqueada = paginas["http://OTRO/privado/x"]
    assert bloqueada["blocked_by_robots"] is True
    assert bloqueada["indexability_status"] == "Blocked by robots.txt"
    # NULL, no 0: no se llego a pedir. Un 0 en el CSV se lee como "respondio 0".
    assert bloqueada["status_code"] is None
    assert bloqueada["status_group"] == "blocked"


def test_el_bloqueo_de_una_url_no_para_el_resto(rastreo_robots):
    paginas, _, _ = rastreo_robots
    assert "http://OTRO/publica-tras-privada" in paginas
    assert "http://OTRO/a" in paginas


def test_el_modo_auditoria_rastrea_y_marca(rastreo_robots_auditoria):
    """Reproducido antes del arreglo: `RobotsAuditMiddleware` no definia
    `self._stats`, asi que la PRIMERA peticion moria con AttributeError y el
    rastreo se quedaba a cero paginas hasta que el vigilante lo mataba por
    estancamiento."""
    paginas, _, _ = rastreo_robots_auditoria
    assert len(paginas) > 5
    # En auditoria si se pide: el informe dice que esta bloqueada, pero el dato
    # de la pagina esta, que es para lo que existe el modo.
    bloqueada = paginas["http://OTRO/privado/x"]
    assert bloqueada["blocked_by_robots"] is True
    assert bloqueada["status_code"] == 200
    assert paginas["http://OTRO/home"]["blocked_by_robots"] is False
