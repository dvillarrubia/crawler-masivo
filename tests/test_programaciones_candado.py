"""Disparos con candado, cola de programados y «Lanzar ahora» (decision 209).

EJECUTADO (decision 74): el disparo del worker y la API de verdad, sobre
SQLite; y el candado entre dos sesiones, sobre Postgres de verdad cuando hay
`PAGERANK_TEST_DATABASE_URL` (en SQLite no hay candados de fila que probar).

Lo que se sujeta:

- un disparo programado va a `jobs:programados`; lo lanzado a mano, a
  `jobs:pending`;
- se encola DESPUES del commit, y si Redis falla el job no se queda
  «pending» para siempre bloqueando el disparo siguiente;
- una programacion que revienta no deja sin lanzar a las demas;
- con dos replicas pasando a la vez, un disparo crea UN job;
- «Lanzar ahora» crea el mismo job, no toca la proxima hora, funciona en
  pausa y no lanza encima de un rastreo vivo.
"""

from __future__ import annotations

import os
import threading
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


class Cola:
    """Apunta cada rpush con su clave; con `rota`, revienta como un Redis caido."""

    def __init__(self, rota: bool = False):
        self.rota = rota
        self.encolados: list[tuple[str, str]] = []
        self.al_encolar = None

    def rpush(self, clave, valor):
        if self.al_encolar is not None:
            self.al_encolar(valor)
        if self.rota:
            raise ConnectionError("Redis no responde")
        self.encolados.append((clave, str(valor)))


@pytest.fixture()
def Sesion(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("shared.database.SessionLocal", S, raising=False)
    return S


def _programacion(S, *, nombre="semanal", hace=timedelta(minutes=1), activa=True,
                  ultimo_job=None):
    with S() as s:
        p = Programacion(
            id=uuid.uuid4(), nombre=nombre, client_id="acme",
            seeds=["https://acme.com/"], config={"max_urls": 10},
            cron="0 3 * * 1", zona_horaria="Europe/Madrid", activa=activa,
            proxima_ejecucion=datetime.now(timezone.utc) - hace,
            ultimo_job_id=ultimo_job)
        s.add(p)
        s.commit()
        return p.id


def _lanzar(cola):
    import worker

    worker._lanzar_programados(cola)


def test_un_disparo_programado_va_a_su_cola(Sesion):
    _programacion(Sesion)
    cola = Cola()
    _lanzar(cola)
    with Sesion() as s:
        job = s.query(Job).one()
    assert cola.encolados == [("jobs:programados", str(job.id))]


def test_se_encola_cuando_el_job_ya_esta_guardado(Sesion):
    """La otra replica puede sacar el id de Redis en el acto: tiene que
    encontrarlo en la base de datos."""
    _programacion(Sesion)
    visto = {}

    def _mirar(job_id):
        with Sesion() as otra:
            visto["existe"] = otra.get(Job, uuid.UUID(str(job_id))) is not None

    cola = Cola()
    cola.al_encolar = _mirar
    _lanzar(cola)
    assert visto == {"existe": True}


def test_sin_redis_el_job_no_se_queda_pendiente_para_siempre(Sesion):
    pid = _programacion(Sesion)
    _lanzar(Cola(rota=True))
    with Sesion() as s:
        job = s.query(Job).one()
        prog = s.get(Programacion, pid)
        assert job.status == "failed"
        assert prog.ultimo_motivo.startswith("no se pudo encolar")
    # Y el disparo siguiente no se lo salta por «el anterior sigue vivo»
    with Sesion() as s:
        s.get(Programacion, pid).proxima_ejecucion = (
            datetime.now(timezone.utc) - timedelta(minutes=1))
        s.commit()
    cola = Cola()
    _lanzar(cola)
    assert len(cola.encolados) == 1


def test_una_programacion_que_revienta_no_frena_a_las_demas(Sesion, monkeypatch):
    import shared.programaciones as P

    mala = _programacion(Sesion, nombre="mala", hace=timedelta(minutes=5))
    buena = _programacion(Sesion, nombre="buena", hace=timedelta(minutes=1))
    original = P.resolver_disparo

    def _resolver(db, prog):
        if prog.id == mala:
            raise RuntimeError("config rota")
        return original(db, prog)

    monkeypatch.setattr(P, "resolver_disparo", _resolver)
    cola = Cola()
    _lanzar(cola)
    with Sesion() as s:
        jobs = s.query(Job).all()
        assert [j.programacion_id for j in jobs] == [buena]
        # La mala sigue con su hora pasada: se reintenta en la pasada siguiente
        assert s.get(Programacion, mala).ultimo_job_id is None
    assert len(cola.encolados) == 1


# ---------------------------------------------------------------------------
# «Lanzar ahora»
# ---------------------------------------------------------------------------
@pytest.fixture()
def api(Sesion, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import api.dependencies
    from api.routers import programaciones
    from shared.database import get_session

    cola = Cola()
    monkeypatch.setattr(api.dependencies, "get_redis", lambda: cola)
    app = FastAPI()
    app.include_router(programaciones.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), cola


def test_lanzar_ahora_crea_el_job_y_no_toca_la_proxima(Sesion, api):
    cliente, cola = api
    pid = _programacion(Sesion, hace=-timedelta(days=3))   # le toca dentro de 3 dias
    with Sesion() as s:
        proxima_antes = s.get(Programacion, pid).proxima_ejecucion

    r = cliente.post(f"/api/programaciones/{pid}/ejecutar")
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert cola.encolados == [("jobs:pending", job_id)], "a mano: la cola normal"
    with Sesion() as s:
        prog = s.get(Programacion, pid)
        job = s.get(Job, uuid.UUID(job_id))
        assert job.programacion_id == pid and job.seeds == ["https://acme.com/"]
        assert prog.ultimo_job_id == job.id
        assert prog.ultimo_motivo == "lanzada a mano"
        assert prog.proxima_ejecucion == proxima_antes


def test_lanzar_ahora_funciona_en_pausa(Sesion, api):
    cliente, _cola = api
    pid = _programacion(Sesion, activa=False)
    assert cliente.post(f"/api/programaciones/{pid}/ejecutar").status_code == 201


def test_lanzar_ahora_no_lanza_encima_de_un_rastreo_vivo(Sesion, api):
    cliente, cola = api
    with Sesion() as s:
        vivo = Job(id=uuid.uuid4(), name="corriendo", seeds=["https://acme.com/"],
                   status="running", config={})
        s.add(vivo)
        s.commit()
        vivo_id = vivo.id
    pid = _programacion(Sesion, ultimo_job=vivo_id)
    r = cliente.post(f"/api/programaciones/{pid}/ejecutar")
    assert r.status_code == 409
    assert "sin terminar" in r.json()["detail"]
    assert cola.encolados == []
    with Sesion() as s:
        assert s.query(Job).count() == 1


def test_lanzar_ahora_de_una_que_no_existe(api):
    cliente, _cola = api
    assert cliente.post(f"/api/programaciones/{uuid.uuid4()}/ejecutar").status_code == 404


def test_el_planificador_respeta_lo_lanzado_a_mano(Sesion, api):
    """Si el rastreo lanzado a mano sigue vivo a su hora, ese disparo se salta."""
    cliente, _cola = api
    pid = _programacion(Sesion, hace=-timedelta(days=1))
    assert cliente.post(f"/api/programaciones/{pid}/ejecutar").status_code == 201
    with Sesion() as s:
        s.get(Programacion, pid).proxima_ejecucion = (
            datetime.now(timezone.utc) - timedelta(minutes=1))
        s.commit()
    cola = Cola()
    _lanzar(cola)
    assert cola.encolados == []
    with Sesion() as s:
        assert s.query(Job).count() == 1
        assert "sin terminar" in s.get(Programacion, pid).ultimo_motivo


def test_lanzar_ahora_sin_redis_no_deja_la_programacion_bloqueada(Sesion, api, monkeypatch):
    import api.dependencies as dependencias

    cliente, _cola = api
    pid = _programacion(Sesion, hace=-timedelta(days=1))
    monkeypatch.setattr(dependencias, "get_redis", lambda: Cola(rota=True))
    r = cliente.post(f"/api/programaciones/{pid}/ejecutar")
    assert r.status_code == 503
    with Sesion() as s:
        assert s.query(Job).one().status == "failed"
        assert s.get(Programacion, pid).ultimo_motivo.startswith("no se pudo encolar")
    cola = Cola()
    monkeypatch.setattr(dependencias, "get_redis", lambda: cola)
    assert cliente.post(f"/api/programaciones/{pid}/ejecutar").status_code == 201, \
        "el fallido no cuenta como vivo"


def test_un_job_que_no_esta_pending_no_se_arranca(Sesion, monkeypatch):
    """Una copia vieja del id en la cola (decision 209) no vuelve a rastrear un
    censo terminado ni uno que ya corre en la otra replica."""
    import worker

    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia",
                        lambda *a, **k: pytest.fail("no se tenia que lanzar Scrapy"))
    with Sesion() as s:
        for estado in ("completed", "running", "cancelled", "failed"):
            s.add(Job(id=uuid.uuid4(), name=estado, seeds=["https://acme.com/"],
                      status=estado, config={}))
        s.commit()
        jobs = [(j.id, j.status) for j in s.query(Job)]
    for jid, _ in jobs:
        worker._run_job(str(jid))
    with Sesion() as s:
        assert sorted((j.id, j.status) for j in s.query(Job)) == sorted(jobs)


class RedisConLatidos(Cola):
    def __init__(self, en_cola=(), latidos=None):
        super().__init__()
        self._en_cola = [str(x) for x in en_cola]
        self._latidos = latidos or {}

    def lrange(self, clave, _i, _f):
        return self._en_cola if clave == "jobs:pending" else []

    def get(self, clave):
        return self._latidos.get(clave)


def test_un_pending_fuera_de_las_colas_se_reencola(Sesion):
    """El proceso murio entre el commit y el encolado: nadie lo sacaria nunca."""
    import time

    import worker

    viejo = datetime.now(timezone.utc) - timedelta(hours=2)
    ids = {}
    with Sesion() as s:
        for nombre, creado in (("huerfano", viejo), ("en_cola", viejo), ("con_latido", viejo),
                               ("recien_creado", datetime.now(timezone.utc))):
            j = Job(id=uuid.uuid4(), name=nombre, seeds=["https://acme.com/"], status="pending",
                    config={}, created_at=creado)
            s.add(j)
            ids[nombre] = j.id
        s.commit()
    r = RedisConLatidos(en_cola=[ids["en_cola"]],
                        latidos={f"job:{ids['con_latido']}:heartbeat": str(time.time())})
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp()
    with Sesion() as s:
        worker._reencolar_huerfanos(r, s, cutoff)
    assert r.encolados == [("jobs:pending", str(ids["huerfano"]))]


# ---------------------------------------------------------------------------
# Dos replicas a la vez, contra Postgres de verdad
# ---------------------------------------------------------------------------
URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL_PG, reason="sin PAGERANK_TEST_DATABASE_URL")
def test_dos_replicas_a_la_vez_crean_un_solo_job(monkeypatch):
    """Sin candado, las dos leen la pendiente antes de que la otra guarde y
    crean un job cada una. Se fuerza el cruce: la primera se para con la fila
    cogida hasta que la segunda ha hecho su pasada entera."""
    import shared.programaciones as P

    engine = create_engine(URL_PG)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("shared.database.SessionLocal", S, raising=False)
    marca = f"candado-{uuid.uuid4().hex[:8]}"
    with S() as s:
        p = Programacion(id=uuid.uuid4(), nombre=marca, client_id=marca,
                         seeds=["https://acme.com/"], config={}, cron="0 3 * * 1",
                         zona_horaria="Europe/Madrid", activa=True,
                         proxima_ejecucion=datetime.now(timezone.utc) - timedelta(minutes=1))
        s.add(p)
        s.commit()
        pid = p.id

    dentro = threading.Event()
    sigue = threading.Event()
    original = P.resolver_disparo

    def _lento(db, prog):
        if threading.current_thread().name == "primera":
            dentro.set()
            sigue.wait(10)
        return original(db, prog)

    monkeypatch.setattr(P, "resolver_disparo", _lento)
    colas = {"primera": Cola(), "segunda": Cola()}

    def _replica(nombre):
        import worker

        worker._lanzar_programados(colas[nombre])

    t1 = threading.Thread(target=_replica, args=("primera",), name="primera")
    t1.start()
    assert dentro.wait(10)
    t2 = threading.Thread(target=_replica, args=("segunda",), name="segunda")
    t2.start()
    t2.join(10)
    assert not t2.is_alive(), "la segunda no espera: salta la fila cogida"
    sigue.set()
    t1.join(10)
    try:
        with S() as s:
            n = s.query(Job).filter(Job.programacion_id == pid).count()
        assert n == 1, f"un disparo, un job (salieron {n})"
        assert len(colas["primera"].encolados) + len(colas["segunda"].encolados) == 1
    finally:
        with S() as s:
            s.query(Job).filter(Job.programacion_id == pid).delete()
            s.query(Programacion).filter(Programacion.id == pid).delete()
            s.commit()


@pytest.mark.skipif(not URL_PG, reason="sin PAGERANK_TEST_DATABASE_URL")
def test_dos_programaciones_del_mismo_perfil_en_dos_replicas_lanzan_una(monkeypatch):
    """El candado de la programacion no basta: son dos filas. Sin el del
    perfil, las dos replicas veian «nada vivo» y salian dos rastreos del
    mismo sitio (decision 193)."""
    import shared.programaciones as P
    from shared.models import Client, CrawlProfile

    engine = create_engine(URL_PG)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("shared.database.SessionLocal", S, raising=False)
    marca = f"perfil-{uuid.uuid4().hex[:8]}"
    ahora = datetime.now(timezone.utc)
    with S() as s:
        c = Client(id=uuid.uuid4(), nombre=marca, slug=marca, dominios=["acme.com"],
                   config_defecto={}, integraciones=[])
        s.add(c)
        s.flush()
        perfil = CrawlProfile(id=uuid.uuid4(), cliente_id=c.id, nombre="Sitio completo",
                              seeds=["https://acme.com/"], config={}, activo=True)
        s.add(perfil)
        s.flush()
        progs = []
        for n, hace in (("p1", 2), ("p2", 1)):
            p = Programacion(id=uuid.uuid4(), nombre=f"{marca}-{n}", client_id=marca,
                             cliente_id=c.id, profile_id=perfil.id, hereda_perfil=True,
                             seeds=[], config={}, cron="0 3 * * 1", zona_horaria="Europe/Madrid",
                             activa=True, proxima_ejecucion=ahora - timedelta(minutes=hace))
            s.add(p)
            progs.append(p.id)
        s.commit()
        perfil_id, cliente_id = perfil.id, c.id

    dentro, sigue = threading.Event(), threading.Event()
    original = P.resolver_disparo

    def _lento(db, prog):
        if threading.current_thread().name == "primera" and prog.id == progs[0]:
            dentro.set()
            sigue.wait(10)
        return original(db, prog)

    monkeypatch.setattr(P, "resolver_disparo", _lento)

    def _replica():
        import worker

        worker._lanzar_programados(Cola())

    t1 = threading.Thread(target=_replica, name="primera")
    t1.start()
    assert dentro.wait(10)
    t2 = threading.Thread(target=_replica, name="segunda")
    t2.start()
    t2.join(1.5)
    sigue.set()
    t1.join(10)
    t2.join(10)
    try:
        with S() as s:
            n = s.query(Job).filter(Job.profile_id == perfil_id).count()
            motivo = s.get(Programacion, progs[1]).ultimo_motivo
        assert n == 1, f"un rastreo del perfil a la vez (salieron {n})"
        assert motivo and motivo.startswith("su perfil ya tiene un rastreo")
    finally:
        with S() as s:
            s.query(Job).filter(Job.profile_id == perfil_id).delete()
            s.query(Programacion).filter(Programacion.id.in_(progs)).delete()
            s.query(CrawlProfile).filter(CrawlProfile.id == perfil_id).delete()
            s.query(Client).filter(Client.id == cliente_id).delete()
            s.commit()
