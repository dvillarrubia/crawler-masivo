"""Backup export/import logic for crawl jobs.

Export: streams a ZIP containing NDJSON files for each table.
Import: reads a ZIP, remaps FK ids, inserts in a single transaction.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile

from sqlalchemy import select
from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import DateTime
from sqlalchemy.orm import Session

from shared.database import SessionLocal
from shared.models import (
    Job,
    Url,
    HtmlMeta,
    Heading,
    Link,
    Hreflang,
    StructuredData,
    Resource,
    SecurityHeaders,
    PageContent,
    Issue,
)

logger = logging.getLogger(__name__)

FORMAT_VERSION = "1"


class ConflictError(Exception):
    """Raised when a job with the same ID already exists."""
    pass

# Tables in export order.  key → (model, fk_field, has_job_id)
_CHILD_TABLES_1TO1: list[tuple[str, type, bool]] = [
    ("html_meta", HtmlMeta, False),
    ("security_headers", SecurityHeaders, False),
    ("page_content", PageContent, False),
]

_CHILD_TABLES_1TON: list[tuple[str, type, bool]] = [
    ("headings", Heading, False),
    ("hreflang", Hreflang, False),
    ("structured_data", StructuredData, False),
    ("resources", Resource, False),
]

# These have job_id AND a url FK
_CHILD_TABLES_JOB: list[tuple[str, type, str]] = [
    ("issues", Issue, "url_id"),
    ("links", Link, "from_url_id"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def _row_to_dict(row: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    """Convert a SQLAlchemy model instance to a plain dict using table columns."""
    exclude = exclude or set()
    d: dict[str, Any] = {}
    for col in row.__table__.columns:
        if col.name in exclude:
            continue
        val = getattr(row, col.name)
        d[col.name] = val
    return d


def _fila_cruda_a_dict(fila, columnas) -> dict[str, Any]:
    """Igual que `_row_to_dict` pero desde una fila de Core.

    Leer con `select(tabla)` en vez de `query(Modelo)` evita construir un
    objeto del ORM por fila —con su mapa de identidad— para algo que solo se
    va a serializar y tirar. En un censo con 1,6 millones de enlaces eso es la
    diferencia entre que el pico de memoria crezca con el censo o no.
    """
    m = fila._mapping
    return {c: m[c] for c in columnas}


def _ndjson_line(d: dict[str, Any]) -> str:
    return json.dumps(d, default=_json_default, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _iter_table(
    job_id: uuid.UUID,
    model: type,
    filter_col: str,
    batch_size: int = 1000,
) -> Generator[dict[str, Any], None, int]:
    """Keyset-paginate a table, yielding dicts.  Returns row count."""
    last_id = 0
    count = 0
    pk_col = getattr(model, "id", None)
    # 1:1 tables use url_id as PK
    if pk_col is None:
        pk_col = model.url_id

    while True:
        session = SessionLocal()
        try:
            tabla = model.__table__
            columnas = [c.name for c in tabla.columns]
            clave = tabla.c[pk_col.key]
            rows = session.execute(
                select(tabla)
                .where(tabla.c[filter_col] == job_id, clave > last_id)
                .order_by(clave)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            for row in rows:
                yield _fila_cruda_a_dict(row, columnas)
                count += 1
                last_id = row._mapping[pk_col.key]
        finally:
            session.close()
    return count


def _iter_urls(
    job_id: uuid.UUID, batch_size: int = 1000,
) -> Generator[dict[str, Any], None, int]:
    last_id = 0
    count = 0
    while True:
        session = SessionLocal()
        try:
            tabla = Url.__table__
            columnas = [c.name for c in tabla.columns]
            rows = session.execute(
                select(tabla)
                .where(tabla.c.job_id == job_id, tabla.c.id > last_id)
                .order_by(tabla.c.id)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            for row in rows:
                yield _fila_cruda_a_dict(row, columnas)
                count += 1
                last_id = row.id
        finally:
            session.close()
    return count


def _iter_child_by_url(
    job_id: uuid.UUID,
    model: type,
    batch_size: int = 1000,
) -> Generator[dict[str, Any], None, int]:
    """Iterate child table rows joined via urls.job_id."""
    last_id = 0
    count = 0

    pk_col = getattr(model, "id", None)
    if pk_col is None:
        pk_col = model.url_id

    while True:
        session = SessionLocal()
        try:
            tabla = model.__table__
            columnas = [c.name for c in tabla.columns]
            clave = tabla.c[pk_col.key]
            rows = session.execute(
                select(tabla)
                .join(Url.__table__, tabla.c.url_id == Url.__table__.c.id)
                .where(Url.__table__.c.job_id == job_id, clave > last_id)
                .order_by(clave)
                .limit(batch_size)
            ).all()
            if not rows:
                break
            for row in rows:
                yield _fila_cruda_a_dict(row, columnas)
                count += 1
                last_id = row._mapping[pk_col.key]
        finally:
            session.close()
    return count


class _SalidaPorTrozos:
    """Destino de solo escritura que va soltando lo escrito por trozos.

    `zipfile` necesita `write`, `flush` y `tell`. Al NO tener `seek` se da
    cuenta de que el destino no es navegable y escribe los descriptores de
    datos correspondientes, que es justo lo que permite ir sirviendo el ZIP
    mientras se genera en vez de montarlo entero en memoria.
    """

    def __init__(self) -> None:
        self._trozos: list[bytes] = []
        self._escrito = 0
        self.pendiente = 0

    def write(self, datos) -> int:
        datos = bytes(datos)
        self._trozos.append(datos)
        self._escrito += len(datos)
        self.pendiente += len(datos)
        return len(datos)

    def tell(self) -> int:
        return self._escrito

    def flush(self) -> None:  # pragma: no cover - lo pide zipfile
        pass

    def recoger(self) -> bytes:
        if not self._trozos:
            return b""
        datos = b"".join(self._trozos)
        self._trozos.clear()
        self.pendiente = 0
        return datos


# Cuanto se acumula antes de soltar un trozo al cliente. Con 1 MB el pico de
# memoria no depende del tamano del censo.
_TROZO = 1 << 20


def stream_backup_zip(
    job_id: uuid.UUID,
    include_content: bool = True,
) -> Generator[bytes, None, None]:
    """Genera el ZIP del censo **de verdad por trozos**.

    Antes se llamaba "stream" pero no lo era: cada tabla se acumulaba entera en
    una lista de strings, se unia en otro string y todo iba a un `BytesIO` que
    solo se soltaba al final, asi que el pico de memoria era el censo entero
    varias veces. Con 87.570 URLs y sus enlaces eso no cabe en un VPS que
    comparte 8 GB con otros cuatro servicios: el backup del censo de CST
    (12.112 URLs) ya tardaba 253 s y 168 MB, y el de penguin no era abordable.

    Ahora cada fila se escribe directamente en la entrada del ZIP y se suelta
    un trozo cada `_TROZO` bytes. Las consultas ya iban por lotes de 1.000.
    """
    row_counts: dict[str, int] = {}

    # El job, en una sesion corta y aparte.
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        if job is None:
            return
        job_dict = _row_to_dict(job)
        job_name = job.name
        job_status = job.status
    finally:
        session.close()

    salida = _SalidaPorTrozos()

    def _tablas():
        yield "urls", _iter_urls(job_id)
        for fname, model, _has_job in _CHILD_TABLES_1TO1:
            if fname == "page_content" and not include_content:
                continue
            yield fname, _iter_child_by_url(job_id, model)
        for fname, model, _has_job in _CHILD_TABLES_1TON:
            yield fname, _iter_child_by_url(job_id, model)
        for fname, model, _url_fk in _CHILD_TABLES_JOB:
            yield fname, _iter_table(job_id, model, "job_id")

    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "job.json",
            json.dumps(job_dict, default=_json_default, ensure_ascii=False, indent=2),
        )

        for fname, filas in _tablas():
            count = 0
            with zf.open(f"{fname}.jsonl", "w") as fh:
                for d in filas:
                    fh.write(_ndjson_line(d).encode("utf-8"))
                    count += 1
                    if salida.pendiente >= _TROZO:
                        yield salida.recoger()
            row_counts[fname] = count
            if salida.pendiente:
                yield salida.recoger()

        manifest = {
            "format_version": FORMAT_VERSION,
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": str(job_id),
            "job_name": job_name,
            "job_status": job_status,
            "row_counts": row_counts,
            "has_page_content": include_content,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    # Lo que quede: el directorio central del ZIP lo escribe el `close()` del
    # `with`, asi que este ultimo trozo no es opcional.
    resto = salida.recoger()
    if resto:
        yield resto


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def _parse_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        # Handle ISO format with or without timezone
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    return None


def _parse_uuid(val: Any) -> uuid.UUID | None:
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def import_backup_zip(
    file_obj: Any,
    preserve_job_id: bool,
    db: Session,
) -> dict[str, Any]:
    """Import a backup ZIP into the database.

    Returns a dict matching ImportResponse schema.
    """
    warnings: list[str] = []
    rows_imported: dict[str, int] = {}
    rows_skipped: dict[str, int] = {}

    try:
        zf = zipfile.ZipFile(file_obj, "r")
    except zipfile.BadZipFile:
        raise ValueError("Archivo ZIP invalido")

    with zf:
        # Read manifest
        if "manifest.json" not in zf.namelist():
            raise ValueError("Falta manifest.json en el ZIP")
        manifest = json.loads(zf.read("manifest.json"))

        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError(
                f"Version de formato no soportada: {manifest.get('format_version')}"
            )

        original_job_id = uuid.UUID(manifest["job_id"])

        # Read job
        if "job.json" not in zf.namelist():
            raise ValueError("Falta job.json en el ZIP")
        job_data = json.loads(zf.read("job.json"))

        # Determine new job ID
        if preserve_job_id:
            new_job_id = original_job_id
            existing = db.query(Job).filter(Job.id == new_job_id).first()
            if existing:
                raise ConflictError(f"Ya existe un job con id {new_job_id}")
        else:
            new_job_id = uuid.uuid4()

        # Insert job
        job = Job(
            id=new_job_id,
            name=job_data.get("name", "Imported job"),
            client_id=job_data.get("client_id"),
            owner_id=job_data.get("owner_id"),
            status=job_data.get("status", "completed"),
            seeds=job_data.get("seeds", []),
            config=job_data.get("config", {}),
            total_urls_discovered=job_data.get("total_urls_discovered", 0),
            total_urls_crawled=job_data.get("total_urls_crawled", 0),
            total_urls_failed=job_data.get("total_urls_failed", 0),
            created_at=_parse_datetime(job_data.get("created_at")),
            started_at=_parse_datetime(job_data.get("started_at")),
            completed_at=_parse_datetime(job_data.get("completed_at")),
        )
        db.add(job)
        db.flush()

        # Process urls.jsonl — build old_id → new_id remap
        old_to_new_url: dict[int, int] = {}
        url_count = 0
        url_skipped = 0

        if "urls.jsonl" in zf.namelist():
            url_lines = zf.read("urls.jsonl").decode("utf-8").splitlines()
            batch: list[dict[str, Any]] = []

            for lineno, line in enumerate(url_lines, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    warnings.append(f"urls.jsonl linea {lineno}: JSON invalido, omitida")
                    url_skipped += 1
                    continue

                old_id = d.get("id")
                batch.append((old_id, d))

                if len(batch) >= 500:
                    _insert_url_batch(batch, new_job_id, old_to_new_url, db)
                    url_count += len(batch)
                    batch = []

            if batch:
                _insert_url_batch(batch, new_job_id, old_to_new_url, db)
                url_count += len(batch)

        rows_imported["urls"] = url_count
        rows_skipped["urls"] = url_skipped

        # Process 1:1 child tables
        for fname, model, _has_job in _CHILD_TABLES_1TO1:
            jsonl_name = f"{fname}.jsonl"
            if jsonl_name not in zf.namelist():
                continue
            imported, skipped = _import_child_table(
                zf, jsonl_name, model, old_to_new_url, None, "url_id", db, warnings,
            )
            rows_imported[fname] = imported
            if skipped:
                rows_skipped[fname] = skipped

        # Process 1:N child tables
        for fname, model, _has_job in _CHILD_TABLES_1TON:
            jsonl_name = f"{fname}.jsonl"
            if jsonl_name not in zf.namelist():
                continue
            imported, skipped = _import_child_table(
                zf, jsonl_name, model, old_to_new_url, None, "url_id", db, warnings,
            )
            rows_imported[fname] = imported
            if skipped:
                rows_skipped[fname] = skipped

        # Process tables with job_id (issues, links)
        for fname, model, url_fk_field in _CHILD_TABLES_JOB:
            jsonl_name = f"{fname}.jsonl"
            if jsonl_name not in zf.namelist():
                continue
            imported, skipped = _import_child_table(
                zf, jsonl_name, model, old_to_new_url, new_job_id, url_fk_field, db, warnings,
            )
            rows_imported[fname] = imported
            if skipped:
                rows_skipped[fname] = skipped

        db.commit()

    return {
        "new_job_id": new_job_id,
        "original_job_id": original_job_id,
        "rows_imported": rows_imported,
        "rows_skipped": rows_skipped,
        "warnings": warnings,
    }


def _insert_url_batch(
    batch: list[tuple[int, dict[str, Any]]],
    new_job_id: uuid.UUID,
    old_to_new: dict[int, int],
    db: Session,
) -> None:
    """Insert a batch of URL rows and populate the old→new id mapping."""
    for old_id, d in batch:
        url = Url(
            job_id=new_job_id,
            url=d["url"],
            url_hash=d["url_hash"],
            host=d.get("host"),
            path=d.get("path"),
            scheme=d.get("scheme"),
            is_internal=d.get("is_internal"),
            crawl_depth=d.get("crawl_depth"),
            content_type=d.get("content_type"),
            content_length=d.get("content_length"),
            status_code=d.get("status_code"),
            status_group=d.get("status_group"),
            response_time_ms=d.get("response_time_ms"),
            is_html=d.get("is_html"),
            resource_type=d.get("resource_type"),
            redirect_url=d.get("redirect_url"),
            indexable=d.get("indexable"),
            body_hash=d.get("body_hash"),
            first_seen_at=_parse_datetime(d.get("first_seen_at")),
            last_crawled_at=_parse_datetime(d.get("last_crawled_at")),
            url_length=d.get("url_length"),
            folder_depth=d.get("folder_depth"),
            word_count=d.get("word_count"),
            text_ratio=d.get("text_ratio"),
            redirect_type=d.get("redirect_type"),
            status_text=d.get("status_text"),
            last_modified=d.get("last_modified"),
            http_version=d.get("http_version"),
            transfer_size=d.get("transfer_size"),
            indexability_status=d.get("indexability_status"),
            inlinks_count=d.get("inlinks_count", 0),
            outlinks_count=d.get("outlinks_count", 0),
            external_outlinks_count=d.get("external_outlinks_count", 0),
            unique_inlinks_count=d.get("unique_inlinks_count", 0),
            pagerank=d.get("pagerank"),
            in_sitemap=d.get("in_sitemap"),
        )
        db.add(url)
        db.flush()
        if old_id is not None:
            old_to_new[old_id] = url.id


def _import_child_table(
    zf: zipfile.ZipFile,
    jsonl_name: str,
    model: type,
    old_to_new_url: dict[int, int],
    new_job_id: uuid.UUID | None,
    url_fk_field: str,
    db: Session,
    warnings: list[str],
) -> tuple[int, int]:
    """Import rows from a child NDJSON file. Returns (imported, skipped)."""
    imported = 0
    skipped = 0
    unmapped_fk = 0  # filas cuyo url FK no existe en el remap old→new
    lines = zf.read(jsonl_name).decode("utf-8").splitlines()

    # Determine columns from model, excluding auto-increment PKs
    table_cols = {c.name for c in model.__table__.columns}
    # Exclude autoincrement id columns
    pk_cols = {c.name for c in model.__table__.primary_key.columns}
    has_auto_id = "id" in pk_cols and model.__table__.c.id.autoincrement

    batch: list[dict[str, Any]] = []

    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"{jsonl_name} linea {lineno}: JSON invalido, omitida")
            skipped += 1
            continue

        # Remap url FK
        old_url_id = d.get(url_fk_field)
        if old_url_id is None:
            warnings.append(f"{jsonl_name} linea {lineno}: falta {url_fk_field}, omitida")
            skipped += 1
            continue
        new_url_id = old_to_new_url.get(old_url_id)
        if new_url_id is None:
            skipped += 1
            unmapped_fk += 1
            continue
        d[url_fk_field] = new_url_id

        # Remap job_id if applicable
        if new_job_id is not None and "job_id" in table_cols:
            d["job_id"] = new_job_id

        # Strip autoincrement id
        if has_auto_id:
            d.pop("id", None)

        # Parse datetime fields
        for col in model.__table__.columns:
            if col.name in d and isinstance(col.type, (DateTime,)):
                val = d.get(col.name)
                if isinstance(val, str):
                    d[col.name] = _parse_datetime(val)

        # Keep only known columns
        row_data = {k: v for k, v in d.items() if k in table_cols and (not has_auto_id or k != "id")}

        batch.append(row_data)

        if len(batch) >= 1000:
            db.execute(model.__table__.insert(), batch)
            imported += len(batch)
            batch = []

    if batch:
        db.execute(model.__table__.insert(), batch)
        imported += len(batch)

    if unmapped_fk:
        # Aviso agregado (uno por tabla) para no inflar warnings fila a fila
        msg = (
            f"{jsonl_name}: {unmapped_fk} fila(s) omitida(s) — "
            f"{url_fk_field} no remapeable (URL ausente del backup)"
        )
        warnings.append(msg)
        logger.warning("Backup import: %s", msg)

    return imported, skipped
