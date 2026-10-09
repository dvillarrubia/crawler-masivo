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
    import importlib

    import crawler.worker as w
    import shared.version as v

    assert hasattr(w, "CRAWLER_VERSION")
    # Se lee del entorno, que es lo que el Dockerfile rellena con el SHA. Y se
    # lee en `shared/version.py`, porque la escriben DOS: el worker al empezar
    # el rastreo y el analizador al terminar su pasada. Por eso hay que
    # recargar ese modulo y no solo el del worker.
    monkeypatch.setenv("CRAWLER_VERSION", "abc1234")
    try:
        importlib.reload(v)
        recargado = importlib.reload(w)
        assert recargado.CRAWLER_VERSION == "abc1234"
        assert v.VERSION == "abc1234", "el analizador lee la misma constante"
    finally:
        monkeypatch.delenv("CRAWLER_VERSION", raising=False)
        importlib.reload(v)
        importlib.reload(w)


def test_sin_sello_queda_dev():
    import importlib
    import os

    import crawler.worker as w
    import shared.version as v

    previo = os.environ.pop("CRAWLER_VERSION", None)
    try:
        importlib.reload(v)
        recargado = importlib.reload(w)
        assert recargado.CRAWLER_VERSION == "dev", (
            "en local, sin argumento de construccion, tiene que quedar 'dev' "
            "y no una cadena vacia que parezca una version"
        )
    finally:
        if previo is not None:
            os.environ["CRAWLER_VERSION"] = previo
        importlib.reload(v)
        importlib.reload(w)


def test_el_modelo_tiene_las_dos_columnas_de_version():
    """Con que se RASTREO y con que se ANALIZO son dos preguntas distintas.

    Un re-analisis cambia las cifras de un censo sin que cambie el sitio ni el
    rastreo: medido en penguin, un censo de julio con 959.633 incidencias
    re-analizado con el codigo de octubre. Comparar dos censos analizados con
    codigo distinto sin decirlo atribuye al cliente un cambio que es nuestro.
    """
    from shared.models import Job

    assert "crawler_version" in Job.__table__.columns
    assert "analisis_version" in Job.__table__.columns


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


def test_la_comparacion_automatica_se_EJECUTA_sin_reventar(monkeypatch, tmp_path):
    """Se llama a la funcion de verdad, no se lee su fuente.

    La primera version reventaba en produccion con `NameError: name
    'SessionLocal' is not defined` —el worker importa esa fabrica DENTRO de
    cada funcion que la usa, y a esta se me olvido—, y el test que tenia
    comprobaba el ORDEN de las llamadas leyendo el codigo con `inspect`. Pasaba
    perfectamente mientras la funcion no llegaba a la segunda linea.

    Es el mismo error que ya cometi con `_sellar_version` del analizador en la
    misma tarde. Un test que mira el fuente comprueba que escribiste algo, no
    que funcione.
    """
    import uuid
    from datetime import datetime

    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    import worker
    from shared.models import Base, HtmlMeta, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint_worker(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr("shared.database.SessionLocal", Sesion, raising=False)

    s = Sesion()

    def censo(nombre, dia, canonical_fuera):
        j = Job(id=uuid.uuid4(), name=nombre, status="completed",
                finish_reason="finished", seeds=["https://x.com/"], config={},
                started_at=datetime(2026, 1, dia), crawler_version="abc1234")
        s.add(j)
        s.flush()
        for i in range(30):
            u = Url(job_id=j.id, url=f"https://x.com/p{i}", url_hash=f"{nombre}{i}",
                    is_internal=True, is_html=True, status_code=200,
                    indexability_status="Indexable", word_count=400)
            s.add(u)
            s.flush()
            s.add(HtmlMeta(url_id=u.id, title="T", canonical_href=(
                f"https://pruebas.ajeno.com/p{i}" if canonical_fuera
                else f"https://x.com/p{i}")))
        s.commit()
        return j.id

    censo("el anterior", 1, False)
    jid = censo("el nuevo", 5, True)
    s.close()

    # Se pasa el UUID y no la cadena que usa produccion: el tipo `Uuid` de
    # SQLAlchemy acepta una cadena contra Postgres y la rechaza contra SQLite.
    # Es un detalle del dialecto, no de lo que se prueba aqui, que es si la
    # funcion llega al final y guarda algo.
    worker._comparar_con_el_censo_anterior(jid)

    s2 = Sesion()
    guardado = s2.query(Job).filter(Job.id == jid).one().comparacion
    assert guardado is not None, "no se ha guardado nada: la funcion no llego al final"
    assert guardado["nombre_anterior"] == "el anterior"
    criticas = [a for a in guardado["alertas"] if a["severidad"] == "critical"]
    assert [a["regla"] for a in criticas] == ["canonical_a_otro_host"]
    assert criticas[0]["paginas"] == 30
    s2.close()


def test_si_la_comparacion_falla_el_rastreo_no_se_pierde(monkeypatch, caplog):
    """Best-effort de verdad: que reviente no puede tumbar nada.

    Y tiene que cubrir TODA la funcion, no solo el cuerpo: el fallo real de
    produccion estuvo en la linea que montaba la sesion, que estaba FUERA del
    try.
    """
    import logging

    import worker

    def explota(*a, **kw):
        raise RuntimeError("la base de datos no responde")

    monkeypatch.setattr("shared.database.SessionLocal", explota, raising=False)
    with caplog.at_level(logging.ERROR):
        worker._comparar_con_el_censo_anterior("da39a3ee-5e6b-4b0d-3255-bfef95601890")
    assert any("fallo al comparar" in r.message for r in caplog.records)


def test_el_worker_avisa_fuera_de_las_alertas_criticas(monkeypatch, tmp_path):
    """Se EJECUTA la comparacion y se mira que el aviso sale, con que sale.

    El otro test de esto mira el fuente para comprobar el ORDEN (que se filtra
    antes de avisar); este comprueba que la llamada ocurre de verdad y con los
    datos buenos. Decision 74: mirar el fuente no puede sustituir a ejecutar.
    """
    import uuid
    from datetime import datetime

    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    import worker
    from shared.models import Base, HtmlMeta, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint_aviso(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/avisos.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr("shared.database.SessionLocal", Sesion, raising=False)

    avisos = []
    monkeypatch.setattr(
        "shared.avisos.avisar_de_alertas",
        lambda job_id, nombre, alertas: avisos.append((nombre, alertas)))

    s = Sesion()

    def censo(nombre, dia, fuera):
        j = Job(id=uuid.uuid4(), name=nombre, status="completed",
                finish_reason="finished", seeds=["https://x.com/"], config={},
                started_at=datetime(2026, 3, dia), crawler_version="abc1234")
        s.add(j)
        s.flush()
        for i in range(30):
            u = Url(job_id=j.id, url=f"https://x.com/p{i}", url_hash=f"{nombre}{i}",
                    is_internal=True, is_html=True, status_code=200,
                    indexability_status="Indexable", word_count=400)
            s.add(u)
            s.flush()
            s.add(HtmlMeta(url_id=u.id, title=f"T{i}", canonical_href=(
                f"https://pruebas.ajeno.com/p{i}" if fuera
                else f"https://x.com/p{i}")))
        s.commit()
        return j.id

    censo("el anterior", 1, False)
    jid = censo("el nuevo", 5, True)
    s.close()

    worker._comparar_con_el_censo_anterior(jid)

    assert len(avisos) == 1, "un rastreo con alertas criticas avisa una vez"
    nombre, alertas = avisos[0]
    assert nombre == "el nuevo"
    assert [a["regla"] for a in alertas] == ["canonical_a_otro_host"]
    assert all(a["severidad"] == "critical" for a in alertas), (
        "solo las criticas: un aviso semanal con cosas que no hay que mirar "
        "se deja de leer")


def test_sin_alertas_criticas_no_se_avisa_fuera(monkeypatch, tmp_path):
    import uuid
    from datetime import datetime

    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    import worker
    from shared.models import Base, HtmlMeta, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint_sin(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/sin.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr("shared.database.SessionLocal", Sesion, raising=False)
    avisos = []
    monkeypatch.setattr("shared.avisos.avisar_de_alertas",
                        lambda *a: avisos.append(a))

    s = Sesion()
    ids = []
    for nombre, dia in [("anterior", 1), ("nuevo", 5)]:
        j = Job(id=uuid.uuid4(), name=nombre, status="completed",
                finish_reason="finished", seeds=["https://x.com/"], config={},
                started_at=datetime(2026, 4, dia), crawler_version="abc1234")
        s.add(j)
        s.flush()
        for i in range(30):
            u = Url(job_id=j.id, url=f"https://x.com/p{i}", url_hash=f"{nombre}{i}",
                    is_internal=True, is_html=True, status_code=200,
                    indexability_status="Indexable", word_count=400)
            s.add(u)
            s.flush()
            s.add(HtmlMeta(url_id=u.id, title=f"T{i}",
                           canonical_href=f"https://x.com/p{i}"))
        ids.append(j.id)
    s.commit()
    s.close()

    worker._comparar_con_el_censo_anterior(ids[1])
    assert avisos == [], "sin nada grave no se molesta a nadie"
