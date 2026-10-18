"""Lo que se juzga de un sitemap (decision 126): lastmod y limites de Google."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.sitemaps import LIMITE_BYTES, es_futuro, leer_lastmod, motivos_de_limite


@pytest.mark.parametrize("valor,esperado", [
    ("2026-10-18", datetime(2026, 10, 18, tzinfo=timezone.utc)),
    ("2026-10", datetime(2026, 10, 1, tzinfo=timezone.utc)),
    ("2027", datetime(2027, 1, 1, tzinfo=timezone.utc)),
    ("2026-10-18T10:00:00Z", datetime(2026, 10, 18, 10, tzinfo=timezone.utc)),
    ("2026-10-18T10:00+02:00", datetime(2026, 10, 18, 8, tzinfo=timezone.utc)),
    ("2026-10-18T10:00:00.123-05:00", datetime(2026, 10, 18, 15, tzinfo=timezone.utc)),
    ("2026-10-18 10:00:00", datetime(2026, 10, 18, 10, tzinfo=timezone.utc)),
    ("18/10/2026", None),
    ("2026-02-30", None),
    ("", None),
    (None, None),
])
def test_leer_lastmod(valor, esperado):
    assert leer_lastmod(valor) == esperado


def test_el_futuro_lleva_un_dia_de_margen():
    leido = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
    # «Mañana» sin hora (00:00 UTC del 19) cabe en el margen: un sitio en
    # UTC+14 puede escribirlo sin mentir.
    assert es_futuro(leer_lastmod("2026-10-19"), leido) is False
    assert es_futuro(leer_lastmod("2026-10-19T10:00:00Z"), leido) is False
    assert es_futuro(leer_lastmod("2026-10-19T10:00:01Z"), leido) is True
    assert es_futuro(leer_lastmod("2026-10-20"), leido) is True
    assert es_futuro(None, leido) is False
    # Una lectura sin zona se toma como UTC.
    assert es_futuro(leer_lastmod("2026-10-20"), leido.replace(tzinfo=None)) is True


def test_los_limites_son_los_de_google():
    assert motivos_de_limite(50_000, 1000) == []
    assert motivos_de_limite(50_001, 1000) == ["urls"]
    assert motivos_de_limite(10, LIMITE_BYTES + 1) == ["bytes"]
    assert motivos_de_limite(60_000, LIMITE_BYTES * 2) == ["urls", "bytes"]
    assert motivos_de_limite(None, None) == []
