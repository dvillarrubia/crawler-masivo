"""
PostgreSQL persistence pipeline.

Items are accumulated in memory and flushed to the database in batches
for performance.  On ``close_spider`` any remaining items are flushed.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from scrapy import Spider

from seo_crawler.extractors import compute_url_hash
from seo_crawler.items import (
    CapturaAlternativaItem,
    ContentItem,
    CustomExtractionItem,
    EnlacesNoRastreablesItem,
    HeadingItem,
    HreflangItem,
    HtmlMetaItem,
    LinkItem,
    PageItem,
    ResourceItem,
    RespuestaSondeadaItem,
    SecurityItem,
    StructuredDataItem,
)

logger = logging.getLogger(__name__)


class PostgresPipeline:
    """
    Batched persistence pipeline for all SEO items.

    * ``PageItem`` and ``HtmlMetaItem`` are upserted individually (they
      carry the primary dedup key ``job_id + url_hash``).
    * ``SecurityItem`` is upserted individually (linked to Url by url_hash).
    * ``LinkItem``, ``HeadingItem``, ``HreflangItem``, ``StructuredDataItem``,
      and ``ResourceItem`` are accumulated and bulk-inserted every
      ``PIPELINE_BATCH_SIZE`` items.
    """

    def __init__(self, batch_size: int = 200, commit_every: int = 50):
        self.batch_size = batch_size
        self.commit_every = commit_every
        self.session = None
        self._buffer: list[tuple[type, dict]] = []
        self._url_id_cache: dict[str, int] = {}  # url_hash -> Url.id
        self._pages_committed: int = 0
        self._last_job_update: float = 0.0
        # url_ids whose stale child rows were already cleared this run, per
        # child type. Prevents a later flush batch from deleting rows an
        # earlier batch inserted for the same page (pages with >batch_size
        # links/resources span multiple flushes).
        self._deleted_child_url_ids: dict[type, set[int]] = {}
        self._uncommitted: int = 0  # escrituras flusheadas sin commit
        # Extraccion a medida (decision 104): url_id -> filas de la pagina. Se
        # reemplaza la pagina ENTERA en cada escritura (DELETE + INSERT en la
        # misma transaccion), sin el «solo la primera vez» de los hijos: un
        # item por pagina lleva todas sus reglas. Si llegan dos de la misma
        # URL en el lote, gana el ultimo.
        self._custom_buffer: dict[int, tuple[str, list[dict]]] = {}
        # Enlaces no rastreables (decision 106): igual, la pagina entera.
        self._enr_buffer: dict[int, tuple[str, list[dict]]] = {}
        # Si su tabla existe. None = sin mirar; False se vuelve a mirar cada
        # minuto (la crea el `create_all` de la API, que en un despliegue
        # arranca DESPUES de que el worker pueda estar rastreando).
        self._enr_tabla: bool | None = None
        self._enr_mirada: float = 0.0
        # Sondas de respuesta (decisiones 123 y 124): url_hash -> fila. No
        # cuelgan de una pagina: se insertan por (job_id, url_hash) y gana la
        # ultima. Misma comprobacion de que la tabla existe.
        self._rs_buffer: dict[tuple[str, str], dict] = {}
        self._rs_tabla: bool | None = None
        self._rs_mirada: float = 0.0

    @classmethod
    def from_crawler(cls, crawler):
        batch_size = crawler.settings.getint("PIPELINE_BATCH_SIZE", 200)
        commit_every = crawler.settings.getint("PIPELINE_COMMIT_EVERY", 50)
        return cls(batch_size=batch_size, commit_every=commit_every)

    # ------------------------------------------------------------------
    # Commit batching
    # ------------------------------------------------------------------
    def _maybe_commit(self):
        """Commit agrupado: cada ``commit_every`` escrituras, no por item."""
        self._uncommitted += 1
        if self._uncommitted >= self.commit_every:
            self.session.commit()
            self._uncommitted = 0

    def _rollback(self):
        """Rollback + invalidar cache de ids no persistidos."""
        self.session.rollback()
        if self._uncommitted:
            # Las filas flusheadas sin commit se pierden con el rollback:
            # el cache puede contener ids que ya no existen.
            self._url_id_cache.clear()
            self._uncommitted = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def open_spider(self, spider: Spider):
        from shared.database import SessionLocal
        from shared.models import Url

        self.session = SessionLocal()
        # El contador arranca en lo que YA hay guardado de este job, no en
        # cero. Cada reanudacion abre un proceso de Scrapy nuevo con su propia
        # tuberia, y al cerrar escribia su cuenta local en
        # `jobs.total_urls_crawled`: un rastreo con 34.704 filas reales se
        # reportaba como 802 (las del ultimo tramo) y /stats devolvia las dos
        # cifras a la vez, contradiciendose.
        try:
            self._pages_committed = (
                self.session.query(Url).filter(Url.job_id == spider.job_id).count()
            )
        except Exception:
            self._pages_committed = 0
            logger.debug("No se pudo leer el contador previo del job")
        logger.info(
            "PostgresPipeline: database session opened (%d URLs ya guardadas)",
            self._pages_committed,
        )

    def close_spider(self, spider: Spider):
        self._flush(spider)
        try:
            self.session.commit()  # commit final de escrituras pendientes
            self._uncommitted = 0
        except Exception:
            self._rollback()
            logger.exception("Final commit failed on close_spider")
        self._update_job_counter(spider, force=True)
        if self.session:
            self.session.close()
            logger.info(
                "PostgresPipeline: session closed, %d URLs del job guardadas",
                self._pages_committed,
            )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def process_item(self, item, spider: Spider):
        if isinstance(item, PageItem):
            self._handle_page(item, spider)
        elif isinstance(item, HtmlMetaItem):
            self._handle_html_meta(item, spider)
        elif isinstance(item, ContentItem):
            self._handle_content(item, spider)
        elif isinstance(item, SecurityItem):
            self._handle_security(item, spider)
        elif isinstance(item, CustomExtractionItem):
            self._handle_custom(item, spider)
        elif isinstance(item, EnlacesNoRastreablesItem):
            self._handle_no_rastreables(item)
        elif isinstance(item, CapturaAlternativaItem):
            self._handle_captura(item)
        elif isinstance(item, RespuestaSondeadaItem):
            self._handle_sondeada(item)
        elif isinstance(item, (LinkItem, HeadingItem, HreflangItem,
                               StructuredDataItem, ResourceItem)):
            self._buffer.append((type(item), dict(item)))
            if len(self._buffer) >= self.batch_size:
                self._flush(spider)
        return item

    # ------------------------------------------------------------------
    # PageItem handling (upsert)
    # ------------------------------------------------------------------
    def _handle_page(self, item: PageItem, spider: Spider):
        from shared.models import Url

        data = dict(item)
        url_hash = data["url_hash"]
        job_id = data["job_id"]

        try:
            existing = (
                self.session.query(Url)
                .filter(Url.job_id == job_id, Url.url_hash == url_hash)
                .first()
            )
            if existing:
                # Update mutable fields
                for field in (
                    "status_code", "status_group", "content_type",
                    "content_length", "response_time_ms", "ttfb_ms",
                    "content_encoding", "is_html",
                    "resource_type", "redirect_url", "body_hash",
                    # New Screaming Frog fields
                    "url_length", "folder_depth", "word_count",
                    "content_word_count", "content_hash", "html_bytes_servidos",
                    "text_ratio", "redirect_type", "status_text",
                    "last_modified", "http_version", "transfer_size",
                    "indexability_status", "blocked_by_robots",
                    "response_class",
                ):
                    setattr(existing, field, data.get(field))
                # crawl_depth NO se sobrescribe sin mas: se conserva el MINIMO.
                # Una URL se alcanza por varios caminos y Screaming Frog reporta
                # la profundidad mas somera a la que se encontro. Pisarla dejaba
                # la home (semilla, depth 0) con la profundidad del ultimo enlace
                # que la redescubria, y ninguna fila acababa con depth 0.
                new_depth = data.get("crawl_depth")
                if new_depth is not None and (
                    existing.crawl_depth is None or new_depth < existing.crawl_depth
                ):
                    existing.crawl_depth = new_depth
                existing.last_crawled_at = datetime.now(timezone.utc)
                self.session.flush()
                url_id = existing.id
                # Una ficha que ahora da 404 o 301 no puede conservar su precio
                # de antes (decision 104): sin HTML 2xx no hay reglas que
                # aplicar, asi que no llega ningun item que reemplace las filas.
                # Sin reglas en el job no hay filas que borrar: nos ahorramos un
                # DELETE por cada redireccion o 404 re-visitado.
                status = data.get("status_code") or 0
                if getattr(spider, "_reglas_a_medida", None) and not (
                        data.get("is_html") and 200 <= status < 300):
                    self._borrar_custom(url_id)
                # Lo mismo con los enlaces no rastreables (decision 106): el
                # item solo llega con HTML 2xx, y una pagina que ahora falla no
                # puede conservar los de cuando respondia.
                if not (data.get("is_html") and 200 <= status < 300):
                    self._borrar_no_rastreables(url_id)
            else:
                url_obj = Url(
                    job_id=job_id,
                    url=data["url"],
                    url_hash=url_hash,
                    host=data.get("host"),
                    path=data.get("path"),
                    scheme=data.get("scheme"),
                    is_internal=data.get("is_internal", True),
                    crawl_depth=data.get("crawl_depth"),
                    content_type=data.get("content_type"),
                    content_length=data.get("content_length"),
                    status_code=data.get("status_code"),
                    status_group=data.get("status_group"),
                    response_time_ms=data.get("response_time_ms"),
                    ttfb_ms=data.get("ttfb_ms"),
                    content_encoding=data.get("content_encoding"),
                    is_html=data.get("is_html", False),
                    resource_type=data.get("resource_type"),
                    redirect_url=data.get("redirect_url"),
                    body_hash=data.get("body_hash"),
                    # New Screaming Frog fields
                    url_length=data.get("url_length"),
                    folder_depth=data.get("folder_depth"),
                    word_count=data.get("word_count"),
                    content_word_count=data.get("content_word_count"),
                    content_hash=data.get("content_hash"),
                    html_bytes_servidos=data.get("html_bytes_servidos"),
                    text_ratio=data.get("text_ratio"),
                    redirect_type=data.get("redirect_type"),
                    status_text=data.get("status_text"),
                    last_modified=data.get("last_modified"),
                    http_version=data.get("http_version"),
                    transfer_size=data.get("transfer_size"),
                    indexability_status=data.get("indexability_status"),
                    blocked_by_robots=data.get("blocked_by_robots"),
                    response_class=data.get("response_class"),
                )
                self.session.add(url_obj)
                self.session.flush()
                url_id = url_obj.id
                # Solo las URLs nuevas suman: re-rastrear una pagina ya
                # guardada no la convierte en dos.
                self._pages_committed += 1

            self._url_id_cache[url_hash] = url_id
            self._maybe_commit()
            self._update_job_counter(spider)
        except Exception:
            self._rollback()
            logger.exception("Failed to persist PageItem for %s", data.get("url"))

    # ------------------------------------------------------------------
    # HtmlMetaItem handling (upsert linked to Url)
    # ------------------------------------------------------------------
    def _handle_html_meta(self, item: HtmlMetaItem, spider: Spider):
        from shared.models import HtmlMeta

        data = dict(item)
        url_hash = data.pop("url_hash")
        job_id = data.pop("job_id")
        url_id = self._resolve_url_id(url_hash, job_id)
        if url_id is None:
            logger.debug("No url_id for HtmlMetaItem url_hash=%s, skipping", url_hash)
            return

        try:
            existing = self.session.query(HtmlMeta).filter(HtmlMeta.url_id == url_id).first()
            if existing:
                for key, val in data.items():
                    setattr(existing, key, val)
            else:
                meta_obj = HtmlMeta(url_id=url_id, **data)
                self.session.add(meta_obj)
            self.session.flush()
            self._maybe_commit()
        except Exception:
            self._rollback()
            logger.exception("Failed to persist HtmlMetaItem for url_id=%s", url_id)

    # ------------------------------------------------------------------
    # ContentItem handling (upsert linked to Url)
    # ------------------------------------------------------------------
    def _handle_content(self, item: ContentItem, spider: Spider):
        from shared.models import PageContent

        data = dict(item)
        url_hash = data.pop("url_hash")
        job_id = data.pop("job_id")
        url_id = self._resolve_url_id(url_hash, job_id)
        if url_id is None:
            logger.debug("No url_id for ContentItem url_hash=%s, skipping", url_hash)
            return

        try:
            existing = self.session.query(PageContent).filter(PageContent.url_id == url_id).first()
            if existing:
                for key, val in data.items():
                    setattr(existing, key, val)
            else:
                content_obj = PageContent(url_id=url_id, **data)
                self.session.add(content_obj)
            self.session.flush()
            self._maybe_commit()
        except Exception:
            self._rollback()
            logger.exception("Failed to persist ContentItem for url_id=%s", url_id)

    # ------------------------------------------------------------------
    # CustomExtractionItem (decision 104)
    # ------------------------------------------------------------------
    def _handle_custom(self, item: CustomExtractionItem, spider: Spider):
        url_id = self._resolve_url_id(item.get("url_hash"), item.get("job_id"))
        if url_id is None:
            logger.debug("No url_id for CustomExtractionItem, skipping")
            return
        self._custom_buffer[url_id] = (item.get("job_id"), list(item.get("filas") or []))
        if len(self._custom_buffer) >= self.batch_size:
            self._flush_custom()

    def _borrar_custom(self, url_id: int) -> None:
        from shared.models import CustomExtraction

        self._custom_buffer.pop(url_id, None)
        self.session.query(CustomExtraction).filter(
            CustomExtraction.url_id == url_id).delete(synchronize_session=False)

    def _flush_custom(self) -> None:
        if not self._custom_buffer:
            return
        from shared.models import CustomExtraction

        lote = self._custom_buffer
        self._custom_buffer = {}
        filas = []
        for url_id, (job_id, de_la_pagina) in lote.items():
            for f in de_la_pagina:
                filas.append({
                    "job_id": job_id, "url_id": url_id,
                    "nombre": f["nombre"], "clase": f["clase"], "estado": f["estado"],
                    "n": f.get("n"), "valor": f.get("valor"),
                    "valor_clave": f.get("valor_clave"), "valor_num": f.get("valor_num"),
                    "valores": f.get("valores"), "truncado": bool(f.get("truncado")),
                    "render": f.get("render"), "detalle": (f.get("detalle") or None),
                })
        try:
            self.session.query(CustomExtraction).filter(
                CustomExtraction.url_id.in_(list(lote))).delete(synchronize_session=False)
            if filas:
                self.session.bulk_insert_mappings(CustomExtraction, filas)
            self.session.commit()
            self._uncommitted = 0
        except Exception:
            self._rollback()
            logger.exception("Fallo al guardar la extraccion a medida de %d paginas", len(lote))

    # ------------------------------------------------------------------
    # EnlacesNoRastreablesItem (decision 106)
    # ------------------------------------------------------------------
    def _tabla_no_rastreables(self) -> bool:
        """Si existe `enlaces_no_rastreables`. Sin ella, el DELETE fallaba, el
        rollback se llevaba todas las paginas escritas y sin confirmar del lote
        (hasta `commit_every`) y la cache de ids, y se repetia en cada lote:
        medido con la tuberia real, 1 de 100 paginas guardadas y 60 de 6.000
        enlaces. Y como algo se guardaba, el job no salia `persistence_failed`
        (decision 28)."""
        ahora = time.monotonic()
        if self._enr_tabla is None or (not self._enr_tabla and ahora - self._enr_mirada > 60):
            self._enr_mirada = ahora
            antes = self._enr_tabla
            try:
                from sqlalchemy import inspect

                from shared.models import EnlaceNoRastreable

                self._enr_tabla = inspect(self.session.get_bind()).has_table(
                    EnlaceNoRastreable.__tablename__)
            except Exception:
                self._enr_tabla = False
            if not self._enr_tabla and antes is None:
                logger.warning(
                    "Sin tabla enlaces_no_rastreables: no se guardan los enlaces no "
                    "rastreables hasta que exista (la crea la API al arrancar; "
                    "decision 106). El resto del rastreo sigue igual.")
        return bool(self._enr_tabla)

    def _borrar_no_rastreables(self, url_id: int) -> None:
        self._enr_buffer.pop(url_id, None)
        if not self._tabla_no_rastreables():
            return
        from shared.models import EnlaceNoRastreable

        try:
            with self.session.begin_nested():
                self.session.query(EnlaceNoRastreable).filter(
                    EnlaceNoRastreable.from_url_id == url_id).delete(
                        synchronize_session=False)
        except Exception:
            logger.exception("No se pudieron borrar los enlaces no rastreables de url_id=%s",
                             url_id)

    def _handle_no_rastreables(self, item: EnlacesNoRastreablesItem):
        if not self._tabla_no_rastreables():
            return
        url_id = self._resolve_url_id(item.get("url_hash"), item.get("job_id"))
        if url_id is None:
            return
        self._enr_buffer[url_id] = (item.get("job_id"), list(item.get("filas") or []))
        if len(self._enr_buffer) >= self.batch_size:
            self._flush_no_rastreables()

    def _flush_no_rastreables(self) -> None:
        if not self._enr_buffer:
            return
        from shared.models import EnlaceNoRastreable

        lote = self._enr_buffer
        self._enr_buffer = {}
        columnas = ("crawlability", "etiqueta", "href", "destino", "destino_interno",
                    "destino_hash", "anchor_text", "link_position")
        filas = [
            {"job_id": job_id, "from_url_id": url_id,
             "repeticiones": f.get("repeticiones") or 1,
             **{c: (f.get(c).replace("\x00", "") if isinstance(f.get(c), str) else f.get(c))
                for c in columnas}}
            for url_id, (job_id, de_la_pagina) in lote.items()
            for f in de_la_pagina
        ]
        # En un SAVEPOINT: si falla, se pierden ESTAS filas y no las paginas
        # escritas y sin confirmar que comparten la transaccion.
        try:
            with self.session.begin_nested():
                self.session.query(EnlaceNoRastreable).filter(
                    EnlaceNoRastreable.from_url_id.in_(list(lote))).delete(
                        synchronize_session=False)
                if filas:
                    self.session.bulk_insert_mappings(EnlaceNoRastreable, filas)
        except Exception:
            logger.exception("Fallo al guardar los enlaces no rastreables de %d paginas",
                             len(lote))
            return
        try:
            self.session.commit()
            self._uncommitted = 0
        except Exception:
            self._rollback()
            logger.exception("Fallo al confirmar los enlaces no rastreables de %d paginas",
                             len(lote))

    # ------------------------------------------------------------------
    # CapturaAlternativaItem (decision 119)
    # ------------------------------------------------------------------
    def _handle_captura(self, item: CapturaAlternativaItem) -> None:
        """Reemplaza la captura de la URL con ese perfil, en un SAVEPOINT.

        Son pocas (una muestra por plantilla) y llegan sueltas: se escriben al
        momento. Sin tabla (la crea la API al arrancar, como la de la decision
        106) o si falla, se pierde ESTA fila y no las paginas sin confirmar
        que comparten la transaccion.
        """
        url_id = self._resolve_url_id(item.get("url_hash"), item.get("job_id"))
        if url_id is None:
            logger.debug("Captura alternativa sin url_id, se descarta")
            return
        from shared.models import CapturaAlternativa

        datos = {k: item.get(k) for k in (
            "perfil", "modo", "estado", "plantilla", "user_agent", "status_code",
            "response_class", "redirige_a", "vary_base", "vary", "medidas",
            "diferencias", "volatiles", "detalle")}
        try:
            with self.session.begin_nested():
                self.session.query(CapturaAlternativa).filter(
                    CapturaAlternativa.url_id == url_id,
                    CapturaAlternativa.perfil == datos["perfil"],
                ).delete(synchronize_session=False)
                self.session.add(CapturaAlternativa(
                    job_id=item.get("job_id"), url_id=url_id, **datos))
        except Exception:
            logger.warning("No se pudo guardar la captura %s de url_id=%s",
                           datos.get("perfil"), url_id, exc_info=True)
            return
        self._maybe_commit()

    # ------------------------------------------------------------------
    # RespuestaSondeadaItem (decisiones 123 y 124)
    # ------------------------------------------------------------------
    def _tabla_sondeadas(self) -> bool:
        """Si existe `respuestas_sondeadas` (la crea la API al arrancar). Sin
        ella las sondas se pierden, pero el rastreo sigue igual: mismo motivo
        que `_tabla_no_rastreables`."""
        ahora = time.monotonic()
        if self._rs_tabla is None or (not self._rs_tabla and ahora - self._rs_mirada > 60):
            self._rs_mirada = ahora
            antes = self._rs_tabla
            try:
                from sqlalchemy import inspect

                from shared.models import RespuestaSondeada

                self._rs_tabla = inspect(self.session.get_bind()).has_table(
                    RespuestaSondeada.__tablename__)
            except Exception:
                self._rs_tabla = False
            if not self._rs_tabla and antes is None:
                logger.warning(
                    "Sin tabla respuestas_sondeadas: no se guardan las sondas de enlaces "
                    "externos ni de recursos hasta que exista (decision 123).")
        return bool(self._rs_tabla)

    _RS_COLUMNAS = ("url", "host", "uso", "interno", "metodo", "status_code", "final_url",
                    "final_status", "saltos", "veredicto", "response_class", "host_caido",
                    "content_type", "content_length", "error")

    def _handle_sondeada(self, item: RespuestaSondeadaItem):
        fila = {c: item.get(c) for c in self._RS_COLUMNAS}
        for c in ("url", "final_url", "error", "content_type", "host"):
            if isinstance(fila.get(c), str):
                fila[c] = fila[c].replace("\x00", "")
        if fila.get("content_type"):
            fila["content_type"] = fila["content_type"][:255]
        if fila.get("host"):
            fila["host"] = fila["host"][:255]
        import uuid

        job_id = item.get("job_id")
        try:
            job_id = uuid.UUID(str(job_id))
        except ValueError:
            pass
        clave = (str(job_id), item.get("url_hash"))
        self._rs_buffer[clave] = {"job_id": job_id, "url_hash": item.get("url_hash"),
                                  "fecha": datetime.now(timezone.utc), **fila}
        if len(self._rs_buffer) >= self.batch_size:
            self._flush_sondeadas()

    def _flush_sondeadas(self) -> None:
        if not self._rs_buffer or not self._tabla_sondeadas():
            return
        from shared.models import RespuestaSondeada

        lote = list(self._rs_buffer.values())
        self._rs_buffer = {}
        tabla = RespuestaSondeada.__table__
        try:
            with self.session.begin_nested():
                if self.session.get_bind().dialect.name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert

                    sentencia = insert(tabla).values(lote)
                    sentencia = sentencia.on_conflict_do_update(
                        index_elements=["job_id", "url_hash"],
                        set_={c: sentencia.excluded[c] for c in self._RS_COLUMNAS + ("fecha",)})
                    self.session.execute(sentencia)
                else:
                    for job_id in {f["job_id"] for f in lote}:
                        self.session.query(RespuestaSondeada).filter(
                            RespuestaSondeada.job_id == job_id,
                            RespuestaSondeada.url_hash.in_(
                                [f["url_hash"] for f in lote if f["job_id"] == job_id]),
                        ).delete(synchronize_session=False)
                    self.session.execute(tabla.insert(), lote)
        except Exception:
            logger.exception("Fallo al guardar %d respuestas sondeadas", len(lote))
            return
        try:
            self.session.commit()
            self._uncommitted = 0
        except Exception:
            self._rollback()
            logger.exception("Fallo al confirmar %d respuestas sondeadas", len(lote))

    # ------------------------------------------------------------------
    # SecurityItem handling (upsert linked to Url)
    # ------------------------------------------------------------------
    def _handle_security(self, item: SecurityItem, spider: Spider):
        from shared.models import SecurityHeaders

        data = dict(item)
        url_hash = data.pop("url_hash")
        job_id = data.pop("job_id")
        url_id = self._resolve_url_id(url_hash, job_id)
        if url_id is None:
            logger.debug("No url_id for SecurityItem url_hash=%s, skipping", url_hash)
            return

        try:
            existing = (
                self.session.query(SecurityHeaders)
                .filter(SecurityHeaders.url_id == url_id)
                .first()
            )
            if existing:
                for key, val in data.items():
                    setattr(existing, key, val)
            else:
                sec_obj = SecurityHeaders(url_id=url_id, **data)
                self.session.add(sec_obj)
            self.session.flush()
            self._maybe_commit()
        except Exception:
            self._rollback()
            logger.exception("Failed to persist SecurityItem for url_id=%s", url_id)

    # ------------------------------------------------------------------
    # Batch flush for child items
    # ------------------------------------------------------------------
    def _flush(self, spider: Spider):
        self._flush_custom()
        self._flush_no_rastreables()
        self._flush_sondeadas()
        if not self._buffer:
            return

        from shared.models import Heading, Hreflang, Link, Resource, StructuredData

        item_type_map = {
            LinkItem: self._make_link,
            HeadingItem: self._make_heading,
            HreflangItem: self._make_hreflang,
            StructuredDataItem: self._make_structured_data,
            ResourceItem: self._make_resource,
        }

        # Collect url_ids per child type so we can delete stale rows
        # before inserting (prevents duplicates on re-crawl).
        url_ids_by_type: dict[type, set[int]] = {}

        objects: list = []
        for item_cls, data in self._buffer:
            factory = item_type_map.get(item_cls)
            if factory:
                obj = factory(data)
                if obj is not None:
                    objects.append(obj)
                    # Track url_id for pre-insert cleanup
                    if item_cls == LinkItem:
                        uid = getattr(obj, "from_url_id", None)
                    else:
                        uid = getattr(obj, "url_id", None)
                    if uid is not None:
                        url_ids_by_type.setdefault(item_cls, set()).add(uid)

        self._buffer.clear()

        if not objects:
            return

        try:
            # Delete existing child rows for the same url_ids to avoid
            # duplicates when a URL is re-crawled (e.g. via redirect).
            _child_model_map = {
                HeadingItem: (Heading, "url_id"),
                HreflangItem: (Hreflang, "url_id"),
                StructuredDataItem: (StructuredData, "url_id"),
                ResourceItem: (Resource, "url_id"),
                LinkItem: (Link, "from_url_id"),
            }
            # Marcado diferido: los url_id no se dan por limpiados hasta que
            # el commit de abajo tiene exito. Si falla, _rollback() deshace el
            # DELETE, y el set no debe afirmar que ya se hizo — si no, un lote
            # posterior de la misma pagina se saltaria la limpieza y dejaria
            # las filas duplicadas que este mecanismo evita.
            pending_deletes: list[tuple[set[int], set[int]]] = []
            for item_cls, uid_set in url_ids_by_type.items():
                entry = _child_model_map.get(item_cls)
                if entry and uid_set:
                    model, fk_col = entry
                    # Only clear stale rows the first time we see a url_id this
                    # run; later batches for the same page must append, not wipe
                    # what earlier batches inserted.
                    already = self._deleted_child_url_ids.setdefault(item_cls, set())
                    to_delete = uid_set - already
                    if to_delete:
                        self.session.query(model).filter(
                            getattr(model, fk_col).in_(to_delete)
                        ).delete(synchronize_session=False)
                        pending_deletes.append((already, to_delete))

            self.session.bulk_save_objects(objects)
            self.session.commit()
            self._uncommitted = 0
            for already, to_delete in pending_deletes:
                already.update(to_delete)
            logger.debug("Flushed %d child items to database", len(objects))
        except Exception:
            self._rollback()
            logger.exception("Batch flush failed for %d items", len(objects))

    # ------------------------------------------------------------------
    # Factory helpers (dict -> ORM object)
    # ------------------------------------------------------------------
    def _make_link(self, data: dict):
        from shared.models import Link

        from_url_hash = data.get("from_url_hash")
        job_id = data.get("job_id")
        from_url_id = self._resolve_url_id(from_url_hash, job_id)
        if from_url_id is None:
            return None
        return Link(
            job_id=job_id,
            from_url_id=from_url_id,
            to_url=data.get("to_url"),
            to_url_hash=data.get("to_url_hash"),
            anchor_text=data.get("anchor_text"),
            rel=data.get("rel"),
            is_internal=data.get("is_internal", True),
            link_position=data.get("link_position"),
            # New Screaming Frog fields
            follow=data.get("follow", True),
            target=data.get("target"),
            alt_text=data.get("alt_text"),
            link_type=data.get("link_type", "hyperlink"),
            repeticiones=data.get("repeticiones") or 1,
        )

    def _make_heading(self, data: dict):
        from shared.models import Heading

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return Heading(
            url_id=url_id,
            tag=data.get("tag"),
            position=data.get("position", 0),
            text=data.get("text"),
            oculto=data.get("oculto"),
        )

    def _make_hreflang(self, data: dict):
        from shared.models import Hreflang

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return Hreflang(
            url_id=url_id,
            lang=data.get("lang"),
            href=data.get("href"),
            en_body=data.get("en_body"),
            origen=data.get("origen"),
        )

    def _make_structured_data(self, data: dict):
        from shared.models import StructuredData

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return StructuredData(
            url_id=url_id,
            raw=data.get("raw"),
            format=data.get("format"),
            schema_type=data.get("schema_type"),
            json_invalido=(data.get("json_invalido") or "")[:200] or None,
        )

    def _make_resource(self, data: dict):
        from shared.models import Resource

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return Resource(
            url_id=url_id,
            resource_url=data.get("resource_url"),
            resource_type=data.get("resource_type"),
            alt_text=data.get("alt_text"),
            # New Screaming Frog fields
            width=data.get("width"),
            height=data.get("height"),
            is_mixed_content=data.get("is_mixed_content", False),
        )

    # ------------------------------------------------------------------
    # URL-ID resolution
    # ------------------------------------------------------------------
    def _resolve_url_id(self, url_hash: str | None, job_id: str | None) -> int | None:
        """Look up the Url.id for a given url_hash, using an in-memory cache."""
        if not url_hash or not job_id:
            return None

        cached = self._url_id_cache.get(url_hash)
        if cached is not None:
            return cached

        from shared.models import Url

        row = (
            self.session.query(Url.id)
            .filter(Url.job_id == job_id, Url.url_hash == url_hash)
            .first()
        )
        if row:
            self._url_id_cache[url_hash] = row[0]
            return row[0]
        return None

    # ------------------------------------------------------------------
    # Job counter
    # ------------------------------------------------------------------
    def _update_job_counter(self, spider: Spider, force: bool = False):
        """Refresca job.total_urls_crawled (cada 5 s o al cerrar)."""
        now = time.monotonic()
        if not force and (now - self._last_job_update) < 5.0:
            return
        self._last_job_update = now

        from shared.models import Job

        try:
            job = self.session.query(Job).filter(Job.id == spider.job_id).first()
            if job:
                job.total_urls_crawled = self._pages_committed
                self.session.commit()
                self._uncommitted = 0
        except Exception:
            self._rollback()
            logger.debug("Failed to update job counter")
