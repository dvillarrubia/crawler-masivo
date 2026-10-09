"""El disparo de rastreos programados, EJECUTADO contra base de datos.

Se llama a `_lanzar_programados` de verdad y se lee lo que quedó escrito. Es
la lección de la decisión 74: un test que lee el fuente comprueba que
escribiste algo, no que funcione, y hoy me ha costado un fallo en producción.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.models import Base, Job, Programacion


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


class ColaFalsa:
    """Una cola que solo apunta lo que le echan."""

    def __init__(self):
        self.encolados = []

    def rpush(self, _clave, valor):
        self.encolados.append(valor)


@pytest.fixture()
def entorno(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr("shared.database.SessionLocal", Sesion, raising=False)
    return Sesion


def _programacion(s, *, proxima, nombre="progym semanal", ultimo_job=None,
                  activa=True):
    p = Programacion(
        id=uuid.uuid4(), nombre=nombre, client_id="progym",
        seeds=["https://www.progym.es/"], config={"max_urls": 5000},
        cron="0 3 * * 1", zona_horaria="Europe/Madrid", activa=activa,
        proxima_ejecucion=proxima, ultimo_job_id=ultimo_job)
    s.add(p)
    s.commit()
    return p.id


def _lanzar(cola):
    import worker

    worker._lanzar_programados(cola)


def test_cuando_toca_se_crea_el_job_y_se_encola(entorno):
    s = entorno()
    ahora = datetime.now(timezone.utc)
    pid = _programacion(s, proxima=ahora - timedelta(minutes=1))
    s.close()

    cola = ColaFalsa()
    _lanzar(cola)

    s2 = entorno()
    prog = s2.query(Programacion).filter(Programacion.id == pid).one()
    jobs = s2.query(Job).all()
    assert len(jobs) == 1, "un disparo, un job"
    assert jobs[0].status == "pending"
    assert jobs[0].seeds == ["https://www.progym.es/"]
    assert jobs[0].config == {"max_urls": 5000}
    assert cola.encolados == [str(jobs[0].id)], "y encolado en la cola FIFO"
    assert prog.ultimo_job_id == jobs[0].id
    assert prog.ultima_ejecucion is not None
    s2.close()


def test_el_nombre_del_job_lleva_la_fecha(entorno):
    """Veinte filas llamadas igual no se distinguen en la lista de rastreos, y
    la comparacion entre censos necesita saber cual es cual."""
    s = entorno()
    _programacion(s, proxima=datetime.now(timezone.utc) - timedelta(minutes=1))
    s.close()
    _lanzar(ColaFalsa())
    s2 = entorno()
    nombre = s2.query(Job).one().name
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert nombre.startswith("progym semanal") and hoy in nombre
    s2.close()


def test_la_proxima_se_recalcula_aunque_NO_se_lance(entorno):
    """Si no, la programacion se queda con la hora pasada y vuelve a
    intentarlo en cada vuelta del bucle, cada minuto, para siempre."""
    s = entorno()
    # Hace tres dias: fuera de la ventana, asi que no se lanza.
    pid = _programacion(
        s, proxima=datetime.now(timezone.utc) - timedelta(days=3))
    s.close()

    cola = ColaFalsa()
    _lanzar(cola)

    s2 = entorno()
    prog = s2.query(Programacion).filter(Programacion.id == pid).one()
    assert cola.encolados == [], "no se lanza, ha pasado la ventana"
    assert s2.query(Job).count() == 0
    proxima = prog.proxima_ejecucion
    if proxima.tzinfo is None:
        proxima = proxima.replace(tzinfo=timezone.utc)
    assert proxima > datetime.now(timezone.utc), "la proxima mira al futuro"
    assert "disparo perdido" in (prog.ultimo_motivo or "")
    s2.close()


def test_no_se_encola_encima_del_rastreo_anterior(entorno):
    """Un rastreo semanal que tarda nueve dias encolaria el siguiente encima
    del que corre. En 2 vCPU eso no es ir mas lento: se caen los dos."""
    s = entorno()
    vivo_id = uuid.uuid4()
    s.add(Job(id=vivo_id, name="el que sigue corriendo", status="running",
              seeds=["https://www.progym.es/"], config={}))
    s.commit()
    pid = _programacion(
        s, proxima=datetime.now(timezone.utc) - timedelta(minutes=1),
        ultimo_job=vivo_id)
    s.close()

    cola = ColaFalsa()
    _lanzar(cola)

    s2 = entorno()
    prog = s2.query(Programacion).filter(Programacion.id == pid).one()
    assert cola.encolados == []
    assert s2.query(Job).count() == 1, "sigue habiendo solo el que ya corria"
    assert "sigue sin terminar" in (prog.ultimo_motivo or "")
    assert prog.ultimo_job_id == vivo_id, "no se pisa la referencia al vivo"
    s2.close()


def test_si_el_anterior_YA_termino_si_se_lanza(entorno):
    s = entorno()
    hecho_id = uuid.uuid4()
    s.add(Job(id=hecho_id, name="el de la semana pasada", status="completed",
              seeds=["https://www.progym.es/"], config={}))
    s.commit()
    _programacion(s, proxima=datetime.now(timezone.utc) - timedelta(minutes=1),
                  ultimo_job=hecho_id)
    s.close()

    cola = ColaFalsa()
    _lanzar(cola)
    assert len(cola.encolados) == 1


def test_una_programacion_desactivada_no_se_dispara(entorno):
    s = entorno()
    _programacion(s, proxima=datetime.now(timezone.utc) - timedelta(minutes=1),
                  activa=False)
    s.close()
    cola = ColaFalsa()
    _lanzar(cola)
    assert cola.encolados == []


def test_una_programacion_futura_no_se_toca(entorno):
    s = entorno()
    pid = _programacion(
        s, proxima=datetime.now(timezone.utc) + timedelta(days=1))
    s.close()
    cola = ColaFalsa()
    _lanzar(cola)
    s2 = entorno()
    prog = s2.query(Programacion).filter(Programacion.id == pid).one()
    assert cola.encolados == []
    assert prog.ultimo_motivo is None, "ni se mira: la consulta ya la descarta"
    s2.close()
