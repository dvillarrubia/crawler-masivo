"""Avisos sobre lo que el HTML declara de si mismo (decision 108).

Viewport, ``<html lang>``, charset, la forma del canonical y de la og:image,
Open Graph y la redireccion por JavaScript. Los datos los extrae el spider en
``html_meta`` y las reglas viven en ``shared/metadatos_html.py``, que el spider
y esto comparten (decisiones 23, 39 y 64).

**Casi todo es de plantilla o de servidor**, y se agrupa (decisiones 36, 53,
64 y 73): una fila por (host, forma de la ruta) —o por host, lo que es del
servidor— con ``paginas_afectadas`` y ejemplos, anclada en la pagina de URL
MENOR del grupo, no en la de id menor, que depende del orden de rastreo
(decision 88). Un sitio sin viewport no son 30.000 avisos: es una plantilla.
La plantilla es `shared.plantillas.firma_de_ruta`.

**Lo que no se midio no se afirma** (decisiones 67 y 89): un censo rastreado
antes de estas columnas las tiene a NULL, y NULL no es «falta».
``html_meta.viewport_count`` es la marca de «medido» del viewport y del lang, y
``charset_meta`` la del charset y la redireccion por JavaScript, que solo se
miden sin render. ``og_ausente`` es la excepcion: ``og_title`` y ``og_image`` se
guardan desde siempre.

**Severidad por consecuencia** (64). Nada de esto saca una pagina del indice:

- ``viewport_ausente`` / ``viewport_incorrecto``, ``warning``: el movil pinta
  la pagina a 980 px y la encoge. Es como se ve en un movil, no si se indexa.
- ``texto_mal_codificado``, ``warning``: el titulo o el texto llevan caracteres
  sustituidos (U+FFFD), y eso es lo que acaba en el indice y en el snippet.
- ``canonical_con_fragmento``: ``warning`` si, quitado el fragmento, apunta a
  OTRA pagina (el enrutado con `#/`: Google consolida en la portada); ``info``
  si es la propia pagina.
- El resto, ``info``: ``charset_incoherente`` y ``charset_ausente`` (por host),
  ``html_lang_ausente`` y ``html_lang_no_coincide_hreflang`` (Google no usa el
  ``lang`` para decidir el idioma), ``canonical_relativo`` (Google lo admite),
  ``og_ausente`` y ``og_image_relativa`` (redes sociales, no posicionamiento) y
  ``redireccion_por_javascript`` (sin render: el destino no se ha seguido).

Solo HTML interno 2xx con respuesta valida (103), y los de contenido solo sobre
indexables, como titulos y descripciones (47).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import or_, select

from shared.indexabilidad import clave_url, misma_url
from shared.metadatos_html import (
    CON_FRAGMENTO,
    FORMAS_RELATIVAS,
    charset_de_content_type,
    diagnostico_charset,
    forma_de_href,
    idioma_principal,
    motivo_viewport,
)
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)

MUESTRA = 5

# Los tipos que emite este modulo. Cada uno lleva su ficha y su nombre en el
# frontend (`incidencias.js`) y su ambito en el explorador (`app.js`); lo
# vigila `tests/test_incidencias_html_frontend.py`.
TIPOS = (
    "viewport_ausente",
    "viewport_incorrecto",
    "html_lang_ausente",
    "html_lang_no_coincide_hreflang",
    "texto_mal_codificado",
    "charset_incoherente",
    "charset_ausente",
    "canonical_relativo",
    "canonical_con_fragmento",
    "og_ausente",
    "og_image_relativa",
    "redireccion_por_javascript",
)

_META_REFRESH = "Redirect (meta refresh)"


def _plantilla(url: str) -> str:
    try:
        return firma_de_ruta(urlparse(url).path)
    except ValueError:
        return firma_de_ruta("")


def _clave_segura(url: str | None):
    if not url:
        return None
    try:
        return clave_url(url)
    except ValueError:
        return None


class _Grupos:
    """Acumula paginas por clave y emite una fila por grupo."""

    def __init__(self) -> None:
        self._paginas: dict[tuple, list[tuple[str, int]]] = defaultdict(list)

    def anotar(self, clave: tuple, url: str, url_id: int) -> None:
        self._paginas[clave].append((url, url_id))

    def emitir(self, analizador, tipo: str, severidad, detalle) -> int:
        """`severidad` y `detalle` son funciones de la clave."""
        from analysis.analyzer import _clave_de_grupo

        for clave, paginas in self._paginas.items():
            paginas.sort()
            datos = {
                **detalle(clave),
                "paginas_afectadas": len(paginas),
                "paginas_ejemplo": [u for u, _ in paginas[:MUESTRA]],
            }
            analizador._add_issue(
                paginas[0][1], tipo, severidad(clave), datos,
                group_key=_clave_de_grupo("|".join(str(p) for p in clave)),
            )
        return len(self._paginas)


def avisar_declaraciones_html(analizador) -> None:
    """Los avisos de la decision 108. Va despues de `analyze_indexability`:
    los de contenido necesitan `Url.indexable` escrito."""
    from analysis.analyzer import _respuesta_valida
    from shared.models import Hreflang, HtmlMeta, Url

    s = analizador.session
    job_id = analizador.job_id
    filas = s.execute(
        select(
            Url.id, Url.url, Url.host, Url.indexable, Url.indexability_status,
            Url.content_type, Url.content_word_count,
            HtmlMeta.viewport, HtmlMeta.viewport_count, HtmlMeta.html_lang,
            HtmlMeta.canonical_crudo, HtmlMeta.canonical_href,
            HtmlMeta.og_title, HtmlMeta.og_image, HtmlMeta.og_image_forma,
            HtmlMeta.redireccion_js, HtmlMeta.charset_meta, HtmlMeta.charset_bom,
            HtmlMeta.cuerpo_solo_ascii, HtmlMeta.texto_mal_codificado,
        )
        .join(HtmlMeta, HtmlMeta.url_id == Url.id)
        .where(
            Url.job_id == job_id,
            Url.is_html.is_(True),
            Url.is_internal.is_(True),
            Url.status_code >= 200,
            Url.status_code < 300,
            _respuesta_valida(),
            # Una meta refresh con destino es una redireccion, no la pagina.
            or_(Url.indexability_status.is_(None),
                Url.indexability_status != _META_REFRESH),
        )
    ).all()

    sin_viewport, viewport_mal = _Grupos(), _Grupos()
    sin_lang = _Grupos()
    mal_codificado, incoherente, ausente = _Grupos(), _Grupos(), _Grupos()
    canon_relativo, canon_fragmento = _Grupos(), _Grupos()
    sin_og, og_relativa = _Grupos(), _Grupos()
    varios_viewports: dict[tuple, int] = defaultdict(int)
    lang_vacio: dict[tuple, int] = defaultdict(int)
    lang_de: dict[int, tuple[str, str, str, str]] = {}  # url_id -> (url, host, plantilla, lang)
    puentes: list[tuple] = []

    for f in filas:
        host = (f.host or "").lower()
        plantilla = _plantilla(f.url)
        indexable = f.indexable is not False

        # -- Viewport y lang: solo indexables y solo si se midio ----------
        if indexable and f.viewport_count is not None:
            if f.viewport_count == 0:
                sin_viewport.anotar((host, plantilla), f.url, f.id)
            else:
                motivo = motivo_viewport(f.viewport)
                if motivo:
                    viewport_mal.anotar((host, plantilla, motivo), f.url, f.id)
                    if f.viewport_count > 1:
                        varios_viewports[(host, plantilla, motivo)] += 1
            if not f.html_lang:
                sin_lang.anotar((host, plantilla), f.url, f.id)
                if f.html_lang == "":
                    lang_vacio[(host, plantilla)] += 1
            else:
                lang_de[f.id] = (f.url, host, plantilla, f.html_lang)

        # -- Charset: del servidor o de la plantilla ------------------------
        if f.charset_meta is not None:
            cabecera = charset_de_content_type(f.content_type)
            estado, manda = diagnostico_charset(cabecera, f.charset_meta, f.charset_bom)
            declarado = (cabecera or "", f.charset_meta or "", f.charset_bom or "")
            if f.texto_mal_codificado:
                # La consecuencia es el aviso; la causa va en el detalle (36).
                mal_codificado.anotar((host, plantilla) + declarado, f.url, f.id)
            elif estado == "incoherente":
                incoherente.anotar((host, manda) + declarado, f.url, f.id)
            elif estado == "ausente" and f.cuerpo_solo_ascii is False:
                ausente.anotar((host,), f.url, f.id)

        # -- Forma del canonical y de la og:image ---------------------------
        forma = forma_de_href(f.canonical_crudo)
        if forma in FORMAS_RELATIVAS:
            canon_relativo.anotar((host, plantilla, forma), f.url, f.id)
        elif forma == CON_FRAGMENTO:
            a_si_misma = misma_url(f.canonical_href, f.url)
            canon_fragmento.anotar((host, plantilla, a_si_misma), f.url, f.id)
        if indexable and f.og_title is None and f.og_image is None:
            sin_og.anotar((host, plantilla), f.url, f.id)
        if f.og_image_forma in FORMAS_RELATIVAS:
            og_relativa.anotar((host, plantilla, f.og_image_forma), f.url, f.id)

        # -- Redireccion por JavaScript: por pagina -------------------------
        if f.redireccion_js:
            puentes.append((f.id, f.url, f.redireccion_js, f.content_word_count))

    sin_viewport.emitir(analizador, "viewport_ausente", lambda k: "warning",
                        lambda k: {"plantilla": k[1],
                                   "rastreo_con_render": analizador._render_js})
    viewport_mal.emitir(analizador, "viewport_incorrecto", lambda k: "warning",
                        lambda k: {"plantilla": k[1], "motivo": k[2],
                                   **({"con_varios_viewports": varios_viewports[k]}
                                      if varios_viewports.get(k) else {})})
    sin_lang.emitir(analizador, "html_lang_ausente", lambda k: "info",
                    lambda k: {"plantilla": k[1],
                               **({"atributo_vacio": lang_vacio[k]}
                                  if lang_vacio.get(k) else {})})
    mal_codificado.emitir(analizador, "texto_mal_codificado", lambda k: "warning",
                          lambda k: {"plantilla": k[1], "charset_cabecera": k[2] or None,
                                     "charset_meta": k[3] or None,
                                     "charset_bom": k[4] or None})
    incoherente.emitir(analizador, "charset_incoherente", lambda k: "info",
                       lambda k: {"manda": k[1], "charset_cabecera": k[2] or None,
                                  "charset_meta": k[3] or None,
                                  "charset_bom": k[4] or None})
    ausente.emitir(analizador, "charset_ausente", lambda k: "info", lambda k: {})
    canon_relativo.emitir(analizador, "canonical_relativo", lambda k: "info",
                          lambda k: {"plantilla": k[1], "forma": k[2]})
    canon_fragmento.emitir(analizador, "canonical_con_fragmento",
                           lambda k: "info" if k[2] else "warning",
                           lambda k: {"plantilla": k[1], "apunta_a_si_misma": k[2]})
    sin_og.emitir(analizador, "og_ausente", lambda k: "info",
                  lambda k: {"plantilla": k[1]})
    og_relativa.emitir(analizador, "og_image_relativa", lambda k: "info",
                       lambda k: {"plantilla": k[1], "forma": k[2]})

    _lang_frente_a_hreflang(analizador, lang_de, Hreflang, Url)
    _redirecciones_js(analizador, puentes, Url)
    analizador._flush_issues()


def _lang_frente_a_hreflang(analizador, lang_de, Hreflang, Url) -> None:
    """``html_lang_no_coincide_hreflang``: el idioma del ``lang`` no es el del
    codigo con que la pagina se anota A SI MISMA en su hreflang.

    Solo el idioma principal (`es` y `es-ES` coinciden; `es-MX` frente a
    `es-ES` no es un hallazgo), sin `x-default`, y solo hreflang del head: el
    del body Google lo ignora. Si se anota a si misma con varios codigos y
    alguno coincide, no se dice nada. Lee `hreflang` filtrado por el job, ni
    `links` ni nada por pareja de paginas.
    """
    if not lang_de:
        return
    propios: dict[int, set[str]] = defaultdict(set)
    for url_id, lang, href in analizador.session.execute(
        select(Hreflang.url_id, Hreflang.lang, Hreflang.href)
        .join(Url, Url.id == Hreflang.url_id)
        .where(Url.job_id == analizador.job_id, Hreflang.en_body.isnot(True))
    ):
        if url_id not in lang_de or not lang or lang.strip().lower() == "x-default":
            continue
        clave = _clave_segura(href)
        if clave is not None and clave == _clave_segura(lang_de[url_id][0]):
            principal = idioma_principal(lang)
            if principal:
                propios[url_id].add(principal)
    grupos = _Grupos()
    for url_id, codigos in propios.items():
        url, host, plantilla, html_lang = lang_de[url_id]
        principal = idioma_principal(html_lang)
        if principal and principal not in codigos:
            grupos.anotar((host, plantilla, principal, ",".join(sorted(codigos))),
                          url, url_id)
    grupos.emitir(analizador, "html_lang_no_coincide_hreflang", lambda k: "info",
                  lambda k: {"plantilla": k[1], "html_lang": k[2], "hreflang": k[3]})


def _redirecciones_js(analizador, puentes, Url) -> None:
    """``redireccion_por_javascript``, una por pagina: cada pagina puente es su
    propio hallazgo. El destino no se ha seguido (no es un enlace ni un salto,
    y no gasta ``max_urls``); si el rastreo llego a el por otro camino, se dice
    que responde."""
    if not puentes:
        return
    destinos = {d for _i, _u, d, _p in puentes}
    estado: dict[Any, int | None] = {}
    lista = sorted(destinos)
    for i in range(0, len(lista), 500):
        for url, codigo in analizador.session.execute(
            select(Url.url, Url.status_code)
            .where(Url.job_id == analizador.job_id, Url.url.in_(lista[i:i + 500]))
        ):
            estado[url] = codigo
    for url_id, _url, destino, palabras in puentes:
        detalle: dict[str, Any] = {"destino": destino, "fuente": "html_crudo",
                                   "palabras": palabras}
        if destino in estado:
            detalle["destino_status"] = estado[destino]
        else:
            detalle["destino_no_comprobado"] = True
        analizador._add_issue(url_id, "redireccion_por_javascript", "info", detalle)
