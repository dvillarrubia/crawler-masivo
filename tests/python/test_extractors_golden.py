"""
Smoke tests over the golden HTML fixture (Fase 0.1).

These validate the test infrastructure itself and freeze the observable
behaviour of the pure extractors on a representative page. They are NOT
exhaustive per-extractor suites -- those arrive with each T-task.
"""

from __future__ import annotations

BASE_URL = "https://golden.local/pagina-dorada"
ALLOWED_HOSTS = {"golden.local"}


def test_extract_meta(golden_selector):
    from seo_crawler.extractors import extract_meta

    meta = extract_meta(golden_selector)
    # Whitespace collapsed by _clean
    assert meta["title"] == "Página dorada — fixture de pruebas"
    assert meta["title_len"] == len(meta["title"])
    assert meta["meta_description"].startswith("Descripción canónica")
    assert meta["meta_robots"] == "index, follow"
    assert meta["canonical_href"] == "https://golden.local/pagina-dorada"
    assert meta["og_title"] == "Página dorada OG"
    assert meta["og_type"] == "article"
    assert meta["twitter_card"] == "summary_large_image"
    assert meta["rel_next"] == "https://golden.local/pagina-dorada?page=2"
    assert meta["rel_prev"] == "https://golden.local/"


def test_extract_meta_refresh(golden_selector):
    from seo_crawler.extractors import extract_meta_refresh

    # C1: la columna ya existe; T4 parseará delay+URL desde este valor.
    refresh = extract_meta_refresh(golden_selector)
    assert refresh is not None
    assert "5" in refresh
    assert "https://golden.local/destino-refresh" in refresh


def test_extract_headings_skips_invisible(golden_selector):
    from seo_crawler.extractors import extract_headings

    headings = extract_headings(golden_selector)
    assert [(h["tag"], h["text"]) for h in headings] == [
        ("h1", "Título principal de la página dorada"),
        ("h2", "Sección de contenido"),  # whitespace collapsed
        ("h3", "Subsección final"),
    ]
    # positions are consecutive in document order
    assert [h["position"] for h in headings] == [0, 1, 2]


def test_extract_links_positions_and_follow(golden_selector):
    from seo_crawler.extractors import extract_links

    links = extract_links(golden_selector, BASE_URL, ALLOWED_HOSTS)
    by_url = {l["url"]: l for l in links}

    # mailto / javascript are skipped
    assert not any(u.startswith(("mailto:", "javascript:")) for u in by_url)

    # Semántica T17.5.a: elementos semánticos primero y el ancestro MÁS
    # CERCANO gana — un enlace en <nav> dentro de <header> es "nav".
    # Clases solo como fallback (main-navigation → nav).
    assert by_url["https://golden.local/seccion/uno"]["link_position"] == "nav"
    assert by_url["https://golden.local/"]["link_position"] == "header"  # <a> directo en <header>
    assert by_url["https://golden.local/seccion/tres"]["link_position"] == "nav"  # clase main-navigation
    assert by_url["https://golden.local/relacionado"]["link_position"] == "sidebar"
    assert by_url["https://golden.local/legal/privacidad"]["link_position"] == "footer"
    assert by_url["https://golden.local/articulo/enlace-contenido"]["link_position"] == "content"

    # T17.5.b: contexto DOM persistido para T22
    assert by_url["https://golden.local/seccion/uno"]["dom_ancestor"] == "nav"
    assert by_url["https://golden.local/relacionado"]["dom_ancestor"] == "aside"
    assert by_url["https://golden.local/articulo/enlace-contenido"]["dom_ancestor"] == "main"
    assert by_url["https://golden.local/seccion/tres"]["dom_ancestor"] is None
    assert by_url["https://golden.local/seccion/tres"]["dom_container"] == "div.main-navigation"

    # Query params se ordenan al normalizar
    assert "https://golden.local/seccion/dos?a=1&b=2" in by_url

    # follow / rel
    ext = by_url["https://externo.example.org/ref"]
    assert ext["is_internal"] is False
    assert ext["follow"] is False
    assert ext["target"] == "_blank"
    assert by_url["https://golden.local/legal/cookies"]["follow"] is False

    # dedup: el enlace repetido con #fragmento colapsa con el editorial
    assert sum(1 for u in by_url if u.startswith("https://golden.local/articulo/")) == 1

    # tipos de enlace
    assert by_url["https://golden.local/producto/42"]["link_type"] == "image"
    assert by_url["https://golden.local/producto/42"]["alt_text"] == "Producto 42"
    assert by_url["https://golden.local/producto/43"]["link_type"] == "image_text"


def test_extract_hreflang(golden_selector):
    from seo_crawler.extractors import extract_hreflang

    tags = extract_hreflang(golden_selector)
    langs = [t["lang"] for t in tags]
    assert langs == ["es", "en-US", "x-default", "zz-INVALID-!!"]


def test_extract_structured_data(golden_html):
    from seo_crawler.extractors import extract_structured_data

    items = extract_structured_data(golden_html, BASE_URL)
    formats = {i["format"] for i in items}
    assert "jsonld" in formats
    jsonld = [i for i in items if i["format"] == "jsonld"]
    assert any(i["schema_type"] == "Article" for i in jsonld)
    micro = [i for i in items if i["format"] == "microdata"]
    assert any(i["schema_type"] == "Product" for i in micro)


def test_extract_resources_mixed_content(golden_selector):
    from seo_crawler.extractors import extract_resources

    resources = extract_resources(golden_selector, BASE_URL)
    by_url = {r["url"]: r for r in resources}

    mixed = by_url["http://inseguro.golden.local/assets/mixta.png"]
    assert mixed["is_mixed_content"] is True
    assert mixed["width"] == 120 and mixed["height"] == 80

    sin_alt = by_url["https://golden.local/assets/sin-alt.png"]
    assert sin_alt["alt_text"] is None
    assert sin_alt["is_mixed_content"] is False

    assert by_url["https://golden.local/assets/main.css"]["resource_type"] == "css"
    assert by_url["https://golden.local/assets/app.js"]["resource_type"] == "js"


def test_db_fixture_roundtrip(db_session, make_job):
    """La fixture de BD crea todas las tablas y los modelos insertan OK."""
    from shared.models import Url

    job = make_job()
    url = Url(job_id=job.id, url="https://toy.local/", url_hash="a" * 64,
              is_internal=True, is_html=True, status_code=200)
    db_session.add(url)
    db_session.flush()
    assert url.id is not None
    assert db_session.query(Url).filter(Url.job_id == job.id).count() == 1
