"""Que peticiones se abortan en el navegador y, sobre todo, cuales NO."""

from __future__ import annotations

import pytest

from crawler.seo_crawler.blocklist import debe_abortarse


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google-analytics.com/analytics.js",
        "https://www.googletagmanager.com/gtm.js?id=GTM-XXXX",
        "https://static.hotjar.com/c/hotjar-123.js",
        "https://connect.facebook.net/en_US/fbevents.js",
        "https://securepubads.g.doubleclick.net/tag/js/gpt.js",
        "https://www.clarity.ms/tag/abc",
        "https://cdn.taboola.com/libtrc/loader.js",
    ],
)
def test_analitica_y_publicidad_se_abortan(url):
    assert debe_abortarse("script", url) is True


@pytest.mark.parametrize(
    "url",
    [
        # El sitio del cliente, aunque el nombre se parezca
        "https://www.midominio.com/ads/campanas.js",
        "https://ads.midominio.com/app.js",
        "https://www.midominio.com/analytics-para-empresas",
        # Un dominio que solo TERMINA parecido no cuenta
        "https://notdoubleclick.net/app.js",
        "https://doubleclick.net.midominio.com/app.js",
        # Recursos legitimos de terceros
        "https://cdn.jsdelivr.net/npm/vue@3/dist/vue.js",
        "https://fonts.googleapis.com/css2?family=Inter",
    ],
)
def test_no_se_aborta_lo_que_puede_llevar_contenido(url):
    assert debe_abortarse("script", url) is False


def test_subdominios_del_dominio_bloqueado_tambien_caen():
    assert debe_abortarse("xhr", "https://region1.analytics.google.com/g/collect")


def test_el_documento_principal_nunca_se_aborta():
    """Ni aunque la propia pagina viva en un dominio de la lista."""
    assert debe_abortarse("document", "https://www.google-analytics.com/") is False


def test_tipos_pesados_se_abortan_venga_de_donde_venga():
    assert debe_abortarse("image", "https://www.midominio.com/foto.jpg") is True
    assert debe_abortarse("font", "https://www.midominio.com/f.woff2") is True


def test_css_y_js_del_sitio_pasan():
    assert debe_abortarse("stylesheet", "https://www.midominio.com/a.css") is False
    assert debe_abortarse("script", "https://www.midominio.com/app.js") is False


def test_url_ausente_o_rara_no_rompe():
    assert debe_abortarse("script", None) is False
    assert debe_abortarse("script", "no-es-una-url") is False
    assert debe_abortarse(None, "https://www.midominio.com/") is False
