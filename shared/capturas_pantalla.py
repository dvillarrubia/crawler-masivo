"""Capturas de pantalla por muestra de plantilla (decision 173, Ola 7.4).

Una captura es una imagen JPEG de lo que se ve al abrir la pagina (la primera
pantalla, sin hacer scroll) con un perfil de dispositivo (`shared.perfiles`):
movil a 412x915 o escritorio a 1280x720, a escala CSS. Las toma el spider como
sonda, una muestra por plantilla con tope pequeno, y se guardan en el volumen
de archivo, como el Parquet de enlaces (decisiones 95 y 150):

    <ARCHIVO_DIR>/capturas/<job_id>/<url_hash>.<perfil>.jpg
    <ARCHIVO_DIR>/capturas/<job_id>/<url_hash>.<perfil>.json   (metadatos)

Sin tabla en Postgres: son decenas de ficheros por censo y lo unico que se
pregunta de ellos es «que capturas tiene esta URL» (la ficha, por `url_hash`)
y «cuanto ocupan» (el borrado). Un fichero de metadatos por imagen, y no un
indice comun, para que escribir una no sea leer-modificar-escribir otra cosa.

Vive en `shared/` porque escribe el spider (crawler), lee la API y borra
`borrar_job` (API). Nada de aqui lanza hacia fuera salvo `guardar`, que el
spider llama dentro de su propio `try` (best-effort: una captura que no se
guarda no puede tumbar el rastreo).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PERFILES = ("escritorio", "movil")
# Tope duro por censo, aunque la config pida mas: `capturas_muestra` (50) por
# los dos perfiles. Una captura movil a escala CSS ronda 50-150 kB.
MAX_POR_JOB = 100
# Una imagen mayor que esto no es una primera pantalla en JPEG: algo ha ido mal
# (o es un PNG a pagina completa) y no se guarda.
MAX_BYTES = 2 * 1024 * 1024

_RE_HASH = re.compile(r"^[0-9a-f]{16,64}$")


def directorio_base() -> Path:
    return Path(os.getenv("ARCHIVO_DIR", "/data/archivo")) / "capturas"


def _uuid(job_id) -> uuid.UUID:
    return job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))


def directorio(job_id) -> Path:
    # Por `uuid.UUID`: un `job_id` que no lo sea no puede salirse del directorio.
    return directorio_base() / str(_uuid(job_id))


def _nombre(url_hash: str, perfil: str) -> str:
    if not _RE_HASH.match(url_hash or "") or perfil not in PERFILES:
        raise ValueError(f"captura no valida: {url_hash!r} {perfil!r}")
    return f"{url_hash}.{perfil}"


def ruta_imagen(job_id, url_hash: str, perfil: str) -> Path:
    return directorio(job_id) / f"{_nombre(url_hash, perfil)}.jpg"


def es_jpeg(datos: bytes | None) -> bool:
    return bool(datos) and datos[:3] == b"\xff\xd8\xff"


def guardar(job_id, url_hash: str, perfil: str, imagen: bytes, meta: dict[str, Any]) -> Path:
    """Escribe la imagen y sus metadatos. Lanza si no se puede (lo llama el
    spider dentro de su `try`). Reemplaza la anterior de esa URL y perfil.

    Escritura atomica (temporal + `replace`): la API puede estar leyendo la
    carpeta mientras el rastreo escribe, y un JPEG a medias se veria roto.
    """
    if not es_jpeg(imagen):
        raise ValueError("la captura no es un JPEG")
    if len(imagen) > MAX_BYTES:
        raise ValueError(f"captura de {len(imagen)} bytes, por encima del tope")
    base = directorio(job_id)
    base.mkdir(parents=True, exist_ok=True)
    nombre = _nombre(url_hash, perfil)
    destino = base / f"{nombre}.jpg"
    tmp = base / f".{nombre}.jpg.tmp"
    tmp.write_bytes(imagen)
    tmp.replace(destino)
    datos = {**meta, "perfil": perfil, "url_hash": url_hash, "bytes": len(imagen),
             "capturada_at": datetime.now(timezone.utc).isoformat()}
    tmpj = base / f".{nombre}.json.tmp"
    tmpj.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    tmpj.replace(base / f"{nombre}.json")
    return destino


def listar(job_id) -> list[dict[str, Any]]:
    """Los metadatos de todas las capturas del censo. Lista vacia si no hay o
    si la carpeta no se puede leer: es un dato opcional del censo."""
    try:
        d = directorio(job_id)
        if not d.is_dir():
            return []
        salida = []
        for f in sorted(d.glob("*.json")):
            if f.name.startswith("."):
                continue
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # Sin la imagen no hay captura: un metadato huerfano no se ensena.
            if (d / f"{f.stem}.jpg").is_file():
                salida.append(meta)
        return salida
    except (OSError, ValueError):
        return []


def de_url(job_id, url_hash: str | None) -> list[dict[str, Any]]:
    """Las capturas de una URL, una por perfil, en orden fijo."""
    if not url_hash:
        return []
    salida = []
    for perfil in PERFILES:
        try:
            img = ruta_imagen(job_id, url_hash, perfil)
            if not img.is_file():
                continue
            meta = json.loads(img.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        salida.append(meta)
    return salida


def cuantas_por_plantilla(job_id) -> dict[tuple[str, str], int]:
    """(plantilla, perfil) -> capturas ya guardadas. Al reanudar, lo ya
    capturado cuenta: si no, cada reanudacion volveria a gastar la muestra."""
    cuenta: dict[tuple[str, str], int] = {}
    for m in listar(job_id):
        k = (m.get("plantilla") or "", m.get("perfil") or "")
        cuenta[k] = cuenta.get(k, 0) + 1
    return cuenta


def bytes_en_disco(job_id) -> int:
    """Lo que ocupan las capturas del censo, MEDIDO. 0 si no tiene; nunca lanza
    (si la carpeta no se puede leer, 0 y un aviso en el log: lo usa la
    estimacion de espacio, que no puede impedir borrar)."""
    try:
        d = directorio(job_id)
        if not d.is_dir():
            return 0
        return sum(f.stat().st_size for f in d.iterdir() if f.is_file())
    except (OSError, ValueError):
        logger.warning("No se pudo medir la carpeta de capturas de %s", job_id, exc_info=True)
        return 0


def resumen_del_directorio() -> dict[str, Any]:
    """Cuanto ocupan TODAS las capturas, para el panel de espacio. Cuenta la
    carpeta y no los jobs: una que se quedo sin job tambien ocupa. `None` si
    no se puede leer, que no es lo mismo que vacia."""
    try:
        d = directorio_base()
        if not d.exists():
            return {"capturas_bytes": 0, "capturas_jobs": 0}
        total, jobs = 0, 0
        for sub in d.iterdir():
            if sub.is_dir():
                jobs += 1
                total += sum(f.stat().st_size for f in sub.iterdir() if f.is_file())
        return {"capturas_bytes": total, "capturas_jobs": jobs}
    except OSError:
        return {"capturas_bytes": None, "capturas_jobs": None}


def borrar(job_id) -> None:
    """La carpeta de un job que se borra. Nunca lanza: lo llama `borrar_job`
    despues del commit, y el job ya no existe."""
    try:
        shutil.rmtree(directorio(job_id), ignore_errors=False)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.exception("No se pudo borrar la carpeta de capturas de %s", job_id)


__all__ = [
    "PERFILES", "MAX_POR_JOB", "MAX_BYTES", "directorio_base", "directorio", "ruta_imagen",
    "es_jpeg", "guardar", "listar", "de_url", "cuantas_por_plantilla", "bytes_en_disco",
    "resumen_del_directorio", "borrar",
]
