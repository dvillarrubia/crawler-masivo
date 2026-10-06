"""El CSV principal: que ninguna columna se quede sin valor por descuido.

Anadir una columna a ``CSV_COLUMNS`` y olvidar el valor en ``_csv_row`` (o al
reves) desplaza TODAS las columnas a partir de ahi sin romper nada: el fichero
se abre igual, con los titulos pegados a los datos equivocados. Esto lo pilla.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("fastapi")

from api.routers.results import CSV_COLUMNS, _csv_row, _val  # noqa: E402


def _url_falsa(**extra):
    base = dict(
        url="https://e.com/a", host="e.com", path="/a", scheme="https",
        is_internal=True, crawl_depth=1, status_code=200, status_group="2xx",
        status_text="OK", content_type="text/html", content_length=100,
        transfer_size=100, response_time_ms=12.5, is_html=True,
        resource_type="html", redirect_url=None, redirect_type=None,
        indexable=True, indexability_status="Indexable", url_length=18,
        folder_depth=1, word_count=500, content_word_count=320, text_ratio=20.0,
        last_modified=None,
        http_version="HTTP/2", inlinks_count=3, unique_inlinks_count=2,
        outlinks_count=10, external_outlinks_count=1, pagerank=1.5,
        pagerank_score=62, pagerank_raw=0.000123,
        in_sitemap=True, blocked_by_robots=None, body_hash="abc",
        last_crawled_at=None, near_duplicate_count=0, closest_similarity=None,
        html_meta=None, page_content=None, security=None,
    )
    base.update(extra)
    return types.SimpleNamespace(**base)


def test_fila_tiene_tantos_campos_como_columnas():
    assert len(_csv_row(_url_falsa())) == len(CSV_COLUMNS)


def test_fila_completa_tambien_cuadra():
    meta = types.SimpleNamespace(
        title="t", title_len=1, title_pixel_width=10, meta_description="d",
        meta_description_len=1, meta_description_pixel_width=10,
        meta_robots="index", x_robots_tag=None, canonical_href="https://e.com/a",
        canonical_header=None, meta_keywords=None, rel_next=None, rel_prev=None,
        meta_refresh=None, has_meta_outside_head=False, og_title="o",
        og_description=None, og_image=None, og_type="article",
        twitter_card="summary", twitter_title=None, twitter_description=None,
    )
    pc = types.SimpleNamespace(content_text="x" * 900, content_length=900)
    sec = types.SimpleNamespace(
        is_https=True, has_hsts=True, has_csp=False, has_mixed_content=False,
    )
    fila = _csv_row(
        _url_falsa(html_meta=meta, page_content=pc, security=sec),
        {"h1_count": 1, "h1_1": "H", "h2_count": 0, "hreflang_count": 2,
         "hreflang_langs": "es|en", "sd_count": 1, "sd_types": "Article",
         "sd_status": "ok", "img_count": 4, "img_sin_alt": 1},
    )
    assert len(fila) == len(CSV_COLUMNS)
    datos = dict(zip(CSV_COLUMNS, fila))
    assert datos["h1_1"] == "H"
    assert datos["hreflang_langs"] == "es|en"
    assert datos["structured_data_types"] == "Article"
    assert datos["images_missing_alt"] == "1"


def test_el_texto_va_recortado_y_la_columna_lo_dice():
    pc = types.SimpleNamespace(content_text="x" * 900, content_length=900)
    fila = dict(zip(CSV_COLUMNS, _csv_row(_url_falsa(page_content=pc))))
    # El nombre de la columna avisa del recorte; la longitud sigue siendo la real.
    assert "content_text_first_500" in CSV_COLUMNS
    assert len(fila["content_text_first_500"]) == 500
    assert fila["content_char_count"] == "900"


def test_la_escala_que_hay_que_leer_va_en_el_csv():
    """0-10 aplasta el 97,8% del sitio en 0,00xx; 0-100 logaritmica, no."""
    fila = dict(zip(CSV_COLUMNS, _csv_row(
        _url_falsa(), {"pagerank_fiable": False})))
    assert fila["pagerank_score"] == "62"
    assert fila["pagerank_raw"] == "0.000123"
    # Y el aviso viaja en cada fila: quien ordena por PageRank en una hoja de
    # calculo no abre el endpoint del job.
    assert fila["pagerank_fiable"] == "False"


def test_sin_tablas_hijas_las_columnas_salen_vacias_no_desplazadas():
    fila = dict(zip(CSV_COLUMNS, _csv_row(_url_falsa())))
    assert fila["title"] == ""
    assert fila["is_https"] == ""
    assert fila["url"] == "https://e.com/a"
    assert fila["h1_count"] == "0"


def test_val_no_confunde_cero_con_vacio():
    assert _val(0) == "0"
    assert _val(False) == "False"
    assert _val(None) == ""
