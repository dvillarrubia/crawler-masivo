"""Lo que el worker saca al log de la salida de Scrapy."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crawler"))
try:
    import redis  # noqa: F401
except ImportError:  # solo existe en la imagen del crawler
    sys.modules["redis"] = types.ModuleType("redis")

from worker import resumir_stderr  # noqa: E402

# Salida real de un censo cuya BD no tenia una columna del modelo
STDERR = """\
2026-09-28 11:03:01 [scrapy.core.engine] INFO: Spider opened
2026-09-28 11:03:02 [seo_crawler.pipelines] ERROR: Failed to persist PageItem for https://e.com/
Traceback (most recent call last):
  File "/app/crawler/seo_crawler/pipelines.py", line 205, in _process_page
    self.session.flush()
psycopg2.errors.UndefinedColumn: column "near_duplicate_count" of relation "urls" does not exist
LINE 1: ...
        ^

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/crawler/seo_crawler/pipelines.py", line 205, in _process_page
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column "near_duplicate_count" of relation "urls" does not exist
LINE 1: ...
[SQL: INSERT INTO urls (...)]
(Background on this error at: https://sqlalche.me/e/21/f405)
2026-09-28 11:03:02 [seo_crawler.pipelines] ERROR: Failed to persist PageItem for https://e.com/a
Traceback (most recent call last):
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) column "near_duplicate_count" of relation "urls" does not exist
2026-09-28 11:03:03 [seo_crawler.spiders.seo_spider] WARNING: Request failed [timeout]: https://e.com/b (took too long)
2026-09-28 11:03:03 [scrapy.core.scraper] ERROR: Error downloading <GET https://e.com/c>
2026-09-28 11:03:04 [py.warnings] WARNING: /usr/lib/seo_crawler/x.py:1: ScrapyDeprecationWarning: algo
"""


def test_resumir_stderr_saca_los_errores_del_pipeline_con_su_causa():
    avisos, errores = resumir_stderr(STDERR)
    (clave,) = errores
    assert clave == "Failed to persist PageItem for https"
    assert len(errores[clave]) == 2
    # La causa es la ultima linea del traceback, no la primera excepcion
    assert errores[clave][0].endswith(
        '-> sqlalchemy.exc.ProgrammingError: (psycopg2.errors.UndefinedColumn) '
        'column "near_duplicate_count" of relation "urls" does not exist'
    )


def test_resumir_stderr_mantiene_los_avisos_y_deja_fuera_el_ruido():
    avisos, _ = resumir_stderr(STDERR)
    assert list(avisos) == ["Request failed [timeout]"]


def test_resumir_stderr_error_sin_traceback():
    _, errores = resumir_stderr(
        "2026-09-28 11:03:02 [seo_crawler.pipelines] ERROR: Batch flush failed for 3 items\n"
        "2026-09-28 11:03:03 [seo_crawler.pipelines] INFO: ok\n"
    )
    assert errores == {"Batch flush failed for 3 items": ["Batch flush failed for 3 items"]}
