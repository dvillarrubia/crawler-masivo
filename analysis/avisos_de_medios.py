"""Tarjetas para redes sociales, videos y versiones AMP y movil (decisiones
154, 155 y 156): los restos de #150 que quedaron en #184.

Todo sale de lo que el spider dejo en ``html_meta`` (``twitter_card``,
``videos``, ``amphtml``, ``alternate_movil``), en ``respuestas_sondeadas``
(``uso='og_image'``, con ``ancho`` y ``alto``) y en ``sitemap_entries``
(``videos``). Nada de esto saca una pagina del indice salvo la version movil
rota, y la severidad lo dice (64).

**Agrupado por plantilla** (36, 53, 64, 73), como los avisos de la decision
108: una fila por (host, forma de la ruta, motivo) con ``paginas_afectadas`` y
ejemplos, anclada en la pagina de URL menor (88). La og:image de serie del tema,
la tarjeta de X mal escrita en la cabecera o el reproductor sin sitemap son de
plantilla.

**Lo que no se midio no se afirma** (67, 89):

- una og:image sin fila de sonda (fuera de la muestra, sondas apagadas, censo
  anterior) o con fila pero sin ``ancho`` (formato que no se lee) no es
  «pequena» ni «rota»: no se dice nada;
- ``html_meta.videos`` NULL es un rastreo anterior, y es la marca de «medido»
  de ``amphtml`` y ``alternate_movil``;
- la version AMP o movil que no esta en el censo (no se sigue: no es un
  enlace) no se juzga; solo la que el rastreo encontro por otro camino;
- ``video_sin_sitemap_de_video`` solo en un sitio que TIENE sitemap de video:
  sin el, Google descubre los videos por la pagina y no falta nada.

Solo HTML interno 2xx, respuesta valida, sin las meta refresh con destino e
indexable (47).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import or_, select

from analysis.avisos_de_html import _Grupos, _plantilla
from shared import sondas as S
from shared.indexabilidad import misma_url
from shared.metadatos_html import motivo_og_image_pequena, twitter_card_invalida

logger = logging.getLogger(__name__)

# Los tipos que emite este modulo. Cada uno lleva su ficha y su nombre en el
# frontend (`incidencias.js`) y su ambito en el explorador (`app.js`); lo
# vigila `tests/test_incidencias_medios_frontend.py`.
TIPOS = (
    "og_image_pequena",
    "og_image_rota",
    "twitter_card_invalida",
    "video_sin_sitemap_de_video",
    "amphtml_incorrecto",
    "alternate_movil_incorrecto",
)

_META_REFRESH = "Redirect (meta refresh)"
LOTE = 2000

# Lo que esta mal en la version AMP o movil que la pagina anuncia.
DESTINO_ERROR = "destino_error"
DESTINO_REDIRIGE = "destino_redirige"
SIN_CANONICAL_DE_VUELTA = "destino_sin_canonical_de_vuelta"

# Severidad por consecuencia (64). Una version movil que no responde o que no
# se declara copia de la de escritorio es lo que Google indexa con la
# indexacion mobile-first: `warning`. Una AMP rota, Google la deja de usar y
# sirve la canonica; la que no apunta de vuelta compite con ella.
_SEVERIDAD = {
    ("amphtml_incorrecto", DESTINO_ERROR): "info",
    ("amphtml_incorrecto", DESTINO_REDIRIGE): "info",
    ("amphtml_incorrecto", SIN_CANONICAL_DE_VUELTA): "warning",
    ("alternate_movil_incorrecto", DESTINO_ERROR): "warning",
    ("alternate_movil_incorrecto", DESTINO_REDIRIGE): "info",
    ("alternate_movil_incorrecto", SIN_CANONICAL_DE_VUELTA): "warning",
}


def avisar(az) -> None:
    """Los avisos de las decisiones 154, 155 y 156. Va despues de
    `analyze_indexability` (necesita `Url.indexable`) y de los de la 108."""
    from analysis.analyzer import _respuesta_valida
    from shared.models import HtmlMeta, Url

    filas = az.session.execute(
        select(
            Url.id, Url.url, Url.url_hash, Url.host,
            HtmlMeta.og_image, HtmlMeta.twitter_card, HtmlMeta.videos,
            HtmlMeta.amphtml, HtmlMeta.alternate_movil,
        )
        .join(HtmlMeta, HtmlMeta.url_id == Url.id)
        .where(
            Url.job_id == az.job_id,
            Url.is_html.is_(True),
            Url.is_internal.is_(True),
            Url.status_code >= 200,
            Url.status_code < 300,
            _respuesta_valida(),
            Url.indexable.is_(True),
            or_(Url.indexability_status.is_(None),
                Url.indexability_status != _META_REFRESH),
        )
    ).all()
    if not filas:
        return
    _tarjetas(az, filas)
    _videos(az, filas)
    _versiones(az, filas)
    az._flush_issues()


# ---------------------------------------------------------------------------
# og:image y twitter:card (decision 154)
# ---------------------------------------------------------------------------
def _sondas_og(az, urls: set[str]) -> dict[str, Any]:
    """La fila de sonda de cada og:image, por la URL tal cual se guardo.

    La sonda guarda la URL con la que se pidio, que es `html_meta.og_image`.
    Si la misma imagen se habia pedido antes como recurso embebido (una fila
    por URL), vale su veredicto, sin medida.
    """
    from shared.models import RespuestaSondeada as R

    if not urls:
        return {}
    try:
        with az.session.begin_nested():
            por_url = {}
            lista = sorted(urls)
            for i in range(0, len(lista), LOTE):
                for f in az.session.execute(
                    select(R.url, R.uso, R.veredicto, R.status_code, R.final_status,
                           R.ancho, R.alto)
                    .where(R.job_id == az.job_id, R.url.in_(lista[i:i + LOTE]))
                ):
                    por_url[f.url] = f
            return por_url
    except Exception:  # noqa: BLE001 -- base sin las columnas: no hay medidas
        logger.warning("No se pudieron leer las sondas de la og:image", exc_info=True)
        return {}


def _ya_avisadas(az, urls: list[str]) -> set[str]:
    """Las og:image rotas que ya tienen su aviso: estan en el censo (su
    `4xx_error`) o las carga un `<img>` (`broken_resource`). Un aviso por
    hallazgo (36)."""
    from shared.models import Resource, Url

    dentro: set[str] = set()
    for i in range(0, len(urls), LOTE):
        trozo = urls[i:i + LOTE]
        dentro.update(u for (u,) in az.session.execute(
            select(Url.url).where(Url.job_id == az.job_id, Url.url.in_(trozo))))
        dentro.update(u for (u,) in az.session.execute(
            select(Resource.resource_url).join(Url, Url.id == Resource.url_id)
            .where(Url.job_id == az.job_id, Resource.resource_url.in_(trozo)).distinct()))
    return dentro


def _tarjetas(az, filas) -> None:
    sondas = _sondas_og(az, {f.og_image for f in filas if f.og_image})
    avisadas = _ya_avisadas(az, sorted(u for u, s in sondas.items() if s.veredicto in S.ROTOS))
    pequenas, rotas, tarjeta_mal = _Grupos(), _Grupos(), _Grupos()
    medida_de: dict[tuple, tuple] = {}
    rota_de: dict[tuple, Any] = {}
    for f in filas:
        host = (f.host or "").lower()
        plantilla = _plantilla(f.url)
        if twitter_card_invalida(f.twitter_card):
            tarjeta_mal.anotar((host, plantilla, f.twitter_card.strip()[:60]), f.url, f.id)
        s = sondas.get(f.og_image) if f.og_image else None
        if s is None:
            continue
        if s.veredicto in S.ROTOS:
            if s.url in avisadas:
                continue
            clave = (host, plantilla)
            rotas.anotar(clave, f.url, f.id)
            rota_de.setdefault(clave, s)
            continue
        motivo = motivo_og_image_pequena(s.ancho, s.alto, f.twitter_card)
        if motivo:
            clave = (host, plantilla, motivo)
            pequenas.anotar(clave, f.url, f.id)
            medida_de.setdefault(clave, (f.og_image, s.ancho, s.alto))

    pequenas.emitir(az, "og_image_pequena", lambda k: "info",
                    lambda k: {"plantilla": k[1], "motivo": k[2],
                               "imagen": medida_de[k][0], "ancho": medida_de[k][1],
                               "alto": medida_de[k][2]})
    rotas.emitir(az, "og_image_rota", lambda k: "info",
                 lambda k: {"plantilla": k[1], "imagen": rota_de[k].url,
                            "veredicto": rota_de[k].veredicto,
                            "status_code": rota_de[k].final_status or rota_de[k].status_code})
    tarjeta_mal.emitir(az, "twitter_card_invalida", lambda k: "info",
                       lambda k: {"plantilla": k[1], "valor": k[2]})

    og = [s for s in sondas.values() if s.uso == "og_image"]
    if og:
        az.sondas_resumen["og_image"] = {
            "comprobadas": len(og),
            "medidas": sum(1 for s in og if s.ancho),
            "por_veredicto": dict(Counter(s.veredicto for s in og)),
        }


# ---------------------------------------------------------------------------
# Videos y sitemap de video (decision 155)
# ---------------------------------------------------------------------------
def _con_video(videos) -> bool:
    """Si la pagina tiene un video de verdad (no el fondo animado)."""
    return any(isinstance(v, dict) and not v.get("decorativo") for v in (videos or []))


def _videos(az, filas) -> None:
    from shared.models import SitemapEntry

    con_video = [f for f in filas if _con_video(f.videos)]
    if not con_video:
        return
    try:
        with az.session.begin_nested():
            en_sitemap_de_video = {h for (h,) in az.session.execute(
                select(SitemapEntry.url_hash)
                .where(SitemapEntry.job_id == az.job_id, SitemapEntry.videos > 0)
            )}
    except Exception:  # noqa: BLE001 -- base sin la columna: no se sabe
        logger.warning("No se pudo leer sitemap_entries.videos", exc_info=True)
        return
    # Sin sitemap de video no falta nada: Google descubre el video en la pagina.
    if not en_sitemap_de_video:
        return
    grupos = _Grupos()
    tipos_de: dict[tuple, Counter] = defaultdict(Counter)
    for f in con_video:
        if f.url_hash in en_sitemap_de_video:
            continue
        clave = ((f.host or "").lower(), _plantilla(f.url))
        grupos.anotar(clave, f.url, f.id)
        for v in f.videos:
            if isinstance(v, dict) and not v.get("decorativo"):
                tipos_de[clave][v.get("tipo") or "video"] += 1
    grupos.emitir(az, "video_sin_sitemap_de_video", lambda k: "info",
                  lambda k: {"plantilla": k[1], "videos": dict(tipos_de[k])})


# ---------------------------------------------------------------------------
# Versiones AMP y movil (decision 156)
# ---------------------------------------------------------------------------
def _motivo_destino(ficha, pagina: str) -> str | None:
    if ficha.status_code is None:
        return None  # no se llego a pedir: no se sabe
    if ficha.redirige:
        return DESTINO_REDIRIGE
    if ficha.status_code >= 400:
        return DESTINO_ERROR
    if 200 <= ficha.status_code < 300 and not misma_url(ficha.canonical, pagina):
        return SIN_CANONICAL_DE_VUELTA
    return None


def _versiones(az, filas) -> None:
    anuncian = [f for f in filas if f.videos is not None and (f.amphtml or f.alternate_movil)]
    if not anuncian:
        return
    from analysis.analyzer import _Fichas

    fichas = _Fichas(az.session, az.job_id)
    for tipo, campo in (("amphtml_incorrecto", "amphtml"),
                        ("alternate_movil_incorrecto", "alternate_movil")):
        grupos = _Grupos()
        ejemplo: dict[tuple, tuple] = {}
        for f in anuncian:
            destino = getattr(f, campo)
            if not destino or misma_url(destino, f.url):
                continue
            ficha = fichas.buscar(destino)
            if ficha is None:
                continue  # no esta en el censo: no se juzga
            motivo = _motivo_destino(ficha, f.url)
            if motivo:
                clave = ((f.host or "").lower(), _plantilla(f.url), motivo)
                grupos.anotar(clave, f.url, f.id)
                ejemplo.setdefault(clave, (destino, ficha.status_code, ficha.canonical))
        grupos.emitir(az, tipo, lambda k, t=tipo: _SEVERIDAD[(t, k[2])],
                      lambda k, e=ejemplo: {"plantilla": k[1], "motivo": k[2],
                                            "destino": e[k][0], "destino_status": e[k][1],
                                            "destino_canonical": e[k][2]})
