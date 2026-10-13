"""Lectura del Crawl-delay de robots.txt (R6 de #25, decision 81).

El ritmo real contra un servidor se mide en `test_spider_rastreo.py`; aqui la
parte pura: que valor se aplica y cuando no se aplica ninguno.
"""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("protego")

from scrapy.robotstxt import ProtegoRobotParser  # noqa: E402

from seo_crawler.middlewares import (  # noqa: E402
    CRAWL_DELAY_MAX_SEGUNDOS,
    crawl_delay_aplicable,
)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"


def _rp(texto: str):
    return ProtegoRobotParser(texto.encode(), None)


def test_lee_el_crawl_delay_del_grupo_generico():
    assert crawl_delay_aplicable(_rp("User-agent: *\nCrawl-delay: 2.5\n"), UA) == 2.5


def test_un_crawl_delay_enorme_se_acota():
    """3600 s por peticion no es cortesia: es no poder auditar el sitio."""
    rp = _rp("User-agent: *\nCrawl-delay: 3600\n")
    assert crawl_delay_aplicable(rp, UA) == CRAWL_DELAY_MAX_SEGUNDOS


def test_el_grupo_de_otro_bot_no_nos_frena():
    rp = _rp("User-agent: Bingbot\nCrawl-delay: 5\n\nUser-agent: *\nDisallow:\n")
    assert crawl_delay_aplicable(rp, UA) is None


@pytest.mark.parametrize("valor", ["0", "-3", "abc", "nan", "inf"])
def test_un_valor_ilegible_no_se_convierte_en_retraso(valor):
    rp = _rp(f"User-agent: *\nCrawl-delay: {valor}\n")
    assert crawl_delay_aplicable(rp, UA) is None


def test_sin_crawl_delay_no_hay_retraso():
    assert crawl_delay_aplicable(_rp("User-agent: *\nDisallow: /x\n"), UA) is None


def test_un_parser_sin_crawl_delay_no_revienta():
    """`RobotParser.crawl_delay` existe desde Scrapy 2.18."""

    class Viejo:
        def allowed(self, url, ua):
            return True

    assert crawl_delay_aplicable(Viejo(), UA) is None


def test_el_middleware_no_se_activa_sin_robotstxt_obey():
    from scrapy.exceptions import NotConfigured
    from scrapy.utils.test import get_crawler

    from seo_crawler.middlewares import RobotsCrawlDelayMiddleware

    crawler = get_crawler(settings_dict={"ROBOTSTXT_OBEY": False})
    with pytest.raises(NotConfigured):
        RobotsCrawlDelayMiddleware.from_crawler(crawler)
