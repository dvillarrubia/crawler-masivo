"""
Regresión de la auditoría hostil: bombas de sitemap.

- gzip bomb (pocos KB → GB) reventaba la memoria del worker.
- documento gigante descargado entero a memoria.
Ahora ambos se cortan con un ValueError acotado (que la ingesta captura
y loguea sin abortar el crawl).
"""

from __future__ import annotations

import zlib

import pytest


def _gzip(data: bytes) -> bytes:
    co = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    return co.compress(data) + co.flush()


def test_safe_gunzip_normal():
    from seo_crawler.sitemap_ingest import _maybe_gunzip

    original = b"<urlset><url><loc>http://x/a</loc></url></urlset>"
    out = _maybe_gunzip("http://x/sitemap.xml.gz", _gzip(original))
    assert out == original


def test_safe_gunzip_bomb_rejected():
    from seo_crawler.sitemap_ingest import MAX_DOC_BYTES, _maybe_gunzip

    # ~5 KB comprimido que descomprime muy por encima del tope
    bomb = _gzip(b"A" * (MAX_DOC_BYTES + 10 * 1024 * 1024))
    assert len(bomb) < 200_000  # la bomba en sí es minúscula
    with pytest.raises(ValueError, match="gzip bomb"):
        _maybe_gunzip("http://x/bomba.gz", bomb)


def test_maybe_gunzip_plain_passthrough():
    from seo_crawler.sitemap_ingest import _maybe_gunzip

    plain = b"<urlset></urlset>"
    assert _maybe_gunzip("http://x/sitemap.xml", plain) == plain
