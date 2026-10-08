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


# ---------------------------------------------------------------------------
# El vigilante de jobs huerfanos tiene que volver a pasar (#37)
# ---------------------------------------------------------------------------
def test_el_vigilante_vuelve_a_pasar_cuando_toca(monkeypatch):
    """Corria UNA sola vez, al arrancar el worker.

    Por eso no servia para el caso que mas lo necesita: si el contenedor se
    reinicia DENTRO de los primeros `STALE_JOB_MINUTES` de un rastreo —que es
    justo cuando lo pilla un despliegue—, el job recien empezado no llega al
    umbral, no es candidato, y como nadie vuelve a mirar se queda en `running`
    sin nadie detras para siempre.

    Medido: un rastreo lanzado a las 22:22:01 y un despliegue que recreo el
    contenedor a las 22:22:39 dejaron el job colgado 7 h 40 min con 39 URLs y
    sin proceso de Scrapy.
    """
    import crawler.worker as w

    pasadas = []
    monkeypatch.setattr(w, "_recover_stale_jobs", lambda rc: pasadas.append(rc))
    monkeypatch.setattr(w, "RECOVERY_INTERVAL_SECONDS", 300)

    t0 = 1000.0
    ultima = t0
    # Justo despues de arrancar no toca.
    ultima = w._quizas_recuperar("rc", ultima, t0 + 1)
    assert pasadas == [] and ultima == t0
    # Ni a falta de un segundo.
    ultima = w._quizas_recuperar("rc", ultima, t0 + 299)
    assert pasadas == []
    # Al cumplirse el intervalo, si.
    ultima = w._quizas_recuperar("rc", ultima, t0 + 300)
    assert pasadas == ["rc"] and ultima == t0 + 300
    # Y vuelve a pasar al siguiente intervalo, no solo una vez.
    ultima = w._quizas_recuperar("rc", ultima, t0 + 600)
    assert len(pasadas) == 2


def test_el_bucle_del_worker_llama_al_vigilante_periodicamente():
    """Que la llamada este DENTRO del bucle, no solo antes de el."""
    import inspect

    import crawler.worker as w

    fuente = inspect.getsource(w.main) if hasattr(w, "main") else ""
    if not fuente:
        import pathlib
        fuente = pathlib.Path(w.__file__).read_text()
    bucle = fuente[fuente.index("while not _shutdown_event.is_set():"):]
    assert "_quizas_recuperar" in bucle, (
        "el vigilante tiene que correr dentro del bucle; si solo corre al "
        "arrancar, un job huerfano no se recupera nunca"
    )


# ---------------------------------------------------------------------------
# Cada rastreo queda sellado con la version que lo hizo (#32)
# ---------------------------------------------------------------------------
def test_el_job_guarda_con_que_version_se_rastreo(monkeypatch):
    """Sin esto, comparar dos censos del mismo sitio no puede distinguir
    "lo cambio el sitio" de "lo cambiamos nosotros".

    Me paso dos veces el mismo dia: el grafo de Druni bajo de 37,5 a 32,1
    millones de aristas entre dos medidas (era que `analyze_indexability`
    habia materializado 9.771 noindex, no un cambio del sitio), y el censo de
    CST dio 88.838 "errores" de datos estructurados que eran basura guardada
    por un extractor de junio.
    """
    import crawler.worker as w

    assert hasattr(w, "CRAWLER_VERSION")
    # Se lee del entorno, que es lo que el Dockerfile rellena con el SHA.
    monkeypatch.setenv("CRAWLER_VERSION", "abc1234")
    import importlib
    recargado = importlib.reload(w)
    try:
        assert recargado.CRAWLER_VERSION == "abc1234"
    finally:
        monkeypatch.delenv("CRAWLER_VERSION", raising=False)
        importlib.reload(w)


def test_sin_sello_queda_dev():
    import importlib
    import os

    import crawler.worker as w

    previo = os.environ.pop("CRAWLER_VERSION", None)
    try:
        recargado = importlib.reload(w)
        assert recargado.CRAWLER_VERSION == "dev", (
            "en local, sin argumento de construccion, tiene que quedar 'dev' "
            "y no una cadena vacia que parezca una version"
        )
    finally:
        if previo is not None:
            os.environ["CRAWLER_VERSION"] = previo
        importlib.reload(w)


def test_el_modelo_tiene_la_columna():
    from shared.models import Job

    assert "crawler_version" in Job.__table__.columns


def test_la_comparacion_va_despues_de_escribir_el_estado_final():
    """El ORDEN es el contrato, y un comentario no lo sujeta.

    Para la comparacion, un job que no esta `completed` es un censo truncado
    (decision 67). Si la llamada se hace junto a `_comprobar_render_js`, la
    fila todavia dice `analyzing` y el censo de ahora se da por incompleto
    SIEMPRE: nunca se podria afirmar que una pagina ha desaparecido, y la
    ausencia de ese aviso se lee como que no falta ninguna.
    """
    import inspect
    import re

    import worker

    fuente = inspect.getsource(worker._run_job)
    pos_render = fuente.index("_comprobar_render_js(job_id)")
    pos_comparar = fuente.index("_comparar_con_el_censo_anterior(job_id)")
    pos_estado = fuente.index("job.status = final_status")
    assert pos_render < pos_estado < pos_comparar, (
        "la comparacion tiene que ir despues de escribir el estado final"
    )
    # Y solo cuando el rastreo ha ido bien: comparar un job fallido no dice
    # nada y el aviso sonaria como si el sitio hubiera cambiado.
    guarda = fuente[fuente.rindex("\n", 0, pos_comparar - 60):pos_comparar]
    assert re.search(r'final_status == "completed"', guarda)
