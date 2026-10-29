"""El cancel de Redis se mira como mucho una vez por segundo, y una vez visto
se recuerda (decision 222)."""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")


class _Redis:
    def __init__(self):
        self.lecturas = 0
        self.cancel = False

    def get(self, clave):
        self.lecturas += 1
        return "1" if self.cancel and clave.endswith(":cancel") else None


def test_el_cancel_se_mira_una_vez_por_segundo_y_se_recuerda(monkeypatch):
    from seo_crawler.spiders import seo_spider

    reloj = [1000.0]
    monkeypatch.setattr(seo_spider.time, "monotonic", lambda: reloj[0])
    sp = seo_spider.SeoSpider(job_id="j")
    sp._redis = r = _Redis()

    assert not any(sp._should_cancel() for _ in range(100))
    assert r.lecturas == 1
    r.cancel = True
    assert sp._should_cancel() is False  # dentro del mismo segundo
    reloj[0] += 1.0
    assert sp._should_cancel() is True
    assert r.lecturas == 2
    r.cancel = False
    assert all(sp._should_cancel() for _ in range(10))
    assert r.lecturas == 2
