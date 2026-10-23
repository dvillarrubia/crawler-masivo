"""Videos, AMP, version movil y tarjetas sociales (decisiones 154, 155 y 156):
los extractores puros y las reglas de `shared/metadatos_html.py`, caso a caso."""

from __future__ import annotations

from parsel import Selector

from seo_crawler.extractors import extract_meta, extract_videos, video_de_url
from seo_crawler.sitemaps import leer_sitemap
from shared.metadatos_html import (
    OG_PEQUENA,
    TWITTER_GRANDE_PEQUENA,
    es_media_movil,
    motivo_og_image_pequena,
    twitter_card_invalida,
)

PAGINA = "https://x.com/a/b"


def _sel(head: str = "", body: str = "") -> Selector:
    return Selector(text=f"<html><head>{head}</head><body>{body}</body></html>")


# -- Videos -------------------------------------------------------------------

def test_video_de_url_reconoce_youtube_y_vimeo():
    assert video_de_url("https://www.youtube.com/embed/dQw4w9WgXcQ?rel=0") == ("youtube", "dQw4w9WgXcQ")
    assert video_de_url("//www.youtube-nocookie.com/embed/abcDEF12345") == ("youtube", "abcDEF12345")
    assert video_de_url("https://youtu.be/abcDEF12345") == ("youtube", "abcDEF12345")
    assert video_de_url("https://player.vimeo.com/video/76979871?h=1") == ("vimeo", "76979871")
    assert video_de_url("https://www.youtube.com/channel/UCxyz") is None
    assert video_de_url("https://vimeo.com/76979871") is None  # la pagina, no el reproductor
    assert video_de_url(None) is None


def test_extract_videos_propios_incrustados_y_diferidos():
    body = """
      <video controls src="/v/a.mp4" poster="/p.jpg"></video>
      <video controls><source src="/v/b.webm" type="video/webm"></video>
      <iframe src="https://www.youtube.com/embed/AAAAAAAAAAA"></iframe>
      <iframe src="about:blank" data-src="https://player.vimeo.com/video/123456"></iframe>
      <lite-youtube videoid="BBBBBBBBBBB"></lite-youtube>
      <iframe src="https://www.google.com/maps/embed?pb=x"></iframe>
    """
    v = extract_videos(_sel(body=body), PAGINA)
    assert [(x["tipo"], x["id"]) for x in v] == [
        ("video", None), ("video", None), ("youtube", "AAAAAAAAAAA"),
        ("vimeo", "123456"), ("youtube", "BBBBBBBBBBB")]
    assert v[0]["src"] == "https://x.com/v/a.mp4"
    assert v[1]["src"] == "https://x.com/v/b.webm"
    assert v[3]["src"] == "https://player.vimeo.com/video/123456"
    assert not any(x["decorativo"] for x in v)


def test_el_fondo_animado_es_decorativo():
    v = extract_videos(_sel(body='<video autoplay muted loop playsinline src="/bg.mp4"></video>'), PAGINA)
    assert v == [{"tipo": "video", "id": None, "src": "https://x.com/bg.mp4", "decorativo": True}]
    # Con controles se puede ver y parar: es un video.
    v = extract_videos(_sel(body='<video autoplay muted controls src="/x.mp4"></video>'), PAGINA)
    assert v[0]["decorativo"] is False


def test_noscript_cuenta_una_vez_y_template_no():
    body = """
      <div class="lazy" data-id="CCCCCCCCCCC"></div>
      <noscript><iframe src="https://www.youtube.com/embed/CCCCCCCCCCC"></iframe></noscript>
      <iframe data-src="https://www.youtube.com/embed/CCCCCCCCCCC"></iframe>
      <template><iframe src="https://www.youtube.com/embed/DDDDDDDDDDD"></iframe></template>
    """
    v = extract_videos(_sel(body=body), PAGINA)
    assert [(x["tipo"], x["id"]) for x in v] == [("youtube", "CCCCCCCCCCC")]


def test_sin_videos_es_lista_vacia_y_con_muchos_se_corta():
    assert extract_videos(_sel(body="<p>texto</p>"), PAGINA) == []
    muchos = "".join(f'<iframe src="https://player.vimeo.com/video/{i}"></iframe>' for i in range(40))
    assert len(extract_videos(_sel(body=muchos), PAGINA)) == 20


def test_video_sin_src_cuenta_igual():
    # Los reproductores por MSE ponen el src con JavaScript.
    v = extract_videos(_sel(body='<video controls></video><video controls></video>'), PAGINA)
    assert len(v) == 2 and all(x["src"] is None for x in v)


# -- AMP y version movil --------------------------------------------------------

def test_amphtml_resuelto_y_solo_del_head():
    m = extract_meta(_sel('<link rel="amphtml" href="/amp/b">'), base_url=PAGINA)
    assert m["amphtml"] == "https://x.com/amp/b"
    m = extract_meta(_sel('<link rel="AmpHTML" Href="https://amp.x.com/a/b">'), base_url=PAGINA)
    assert m["amphtml"] == "https://amp.x.com/a/b"
    m = extract_meta(_sel(body='<link rel="amphtml" href="/amp/b">'), base_url=PAGINA)
    assert m["amphtml"] is None


def test_alternate_movil_por_media_y_no_el_hreflang():
    head = """
      <link rel="alternate" hreflang="en" media="only screen and (max-width: 640px)" href="/en">
      <link rel="alternate" media="print" href="/print">
      <link rel="alternate" type="application/rss+xml" href="/feed">
      <link rel="alternate" media="only screen and (max-width: 640px)" href="https://m.x.com/a/b">
    """
    m = extract_meta(_sel(head), base_url=PAGINA)
    assert m["alternate_movil"] == "https://m.x.com/a/b"
    m = extract_meta(_sel('<link rel="alternate" media="handheld" href="https://m.x.com/">'),
                     base_url=PAGINA)
    assert m["alternate_movil"] == "https://m.x.com/"
    m = extract_meta(_sel('<link rel="alternate" hreflang="es" href="/es">'), base_url=PAGINA)
    assert m["alternate_movil"] is None and m["amphtml"] is None


def test_es_media_movil():
    assert es_media_movil("only screen and (max-width: 640px)")
    assert es_media_movil("handheld")
    assert es_media_movil("only screen and (max-device-width: 480px)")
    assert not es_media_movil("print")
    assert not es_media_movil("")
    assert not es_media_movil(None)


# -- og:image y twitter:card -----------------------------------------------------

def test_twitter_card_invalida():
    assert not twitter_card_invalida(None)  # sin etiqueta: X usa Open Graph
    for valida in ("summary", "summary_large_image", "app", "player", " Summary_Large_Image "):
        assert not twitter_card_invalida(valida)
    for mala in ("large", "summary-large-image", "photo", ""):
        assert twitter_card_invalida(mala)


def test_og_image_pequena():
    assert motivo_og_image_pequena(150, 150) == OG_PEQUENA
    assert motivo_og_image_pequena(1200, 199) == OG_PEQUENA
    assert motivo_og_image_pequena(200, 200) is None
    assert motivo_og_image_pequena(250, 250, "summary_large_image") == TWITTER_GRANDE_PEQUENA
    assert motivo_og_image_pequena(250, 250, "summary") is None
    assert motivo_og_image_pequena(1200, 630, "summary_large_image") is None
    # Sin medida no se afirma nada.
    assert motivo_og_image_pequena(None, None) is None
    assert motivo_og_image_pequena(0, 630) is None


# -- Sitemap de video -------------------------------------------------------------

def test_el_sitemap_cuenta_los_video_de_cada_entrada():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">
  <url><loc>https://x.com/con-video</loc>
    <video:video><video:thumbnail_loc>https://x.com/t.jpg</video:thumbnail_loc>
      <video:title>T</video:title><video:player_loc>https://x.com/p</video:player_loc></video:video>
    <video:video><video:title>Otro</video:title></video:video>
  </url>
  <url><loc>https://x.com/sin-video</loc></url>
</urlset>"""
    lectura = leer_sitemap(xml, "https://x.com/sitemap.xml")
    assert {e.url: e.videos for e in lectura.entradas} == {
        "https://x.com/con-video": 2, "https://x.com/sin-video": 0}
