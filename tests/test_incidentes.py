"""Alertas con estado (Ola 5.1, decision 139), EJECUTADAS contra SQLite: abrir,
seguir sin volver a avisar, cerrar solo cuando se puede afirmar, el rastreo
mas reciente manda, el silencio dura aunque se cierre y vuelva, y el worker
solo avisa de lo que abre."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared import incidentes as I


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def s():
    from shared.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def serie(s):
    from shared.models import Client, CrawlProfile

    c = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={},
               integraciones=[])
    s.add(c)
    s.flush()
    p = CrawlProfile(id=uuid.uuid4(), cliente_id=c.id, nombre="semanal", rol="propio",
                     seeds=["https://x.com/"], config={}, activo=True)
    s.add(p)
    s.flush()
    return c, p


DIA = [datetime(2026, 10, d, 3, tzinfo=timezone.utc) for d in range(1, 32)]


def _job(s, serie, dia, **kw):
    from shared.models import Job

    c, p = serie
    j = Job(id=uuid.uuid4(), name=f"semanal {dia}", status="completed", seeds=["https://x.com/"],
            config={}, cliente_id=c.id, profile_id=p.id, created_at=DIA[dia], **kw)
    s.add(j)
    s.flush()
    return j


ROTAS = {"regla": "rotas", "severidad": "critical", "paginas": 30, "de": 100, "pct": 30.0,
         "que_decide_google": "Sale del índice."}


def _comp(*alertas, concluyente=True):
    return {"comparable": True, "concluyente": concluyente, "alertas": list(alertas)}


def _vivos(s):
    from shared.models import Incidente

    return s.query(Incidente).filter(Incidente.estado.in_(I.VIVOS)).all()


def test_abre_sigue_sin_volver_a_avisar_y_cierra(s, serie):
    j1 = _job(s, serie, 1)
    assert [a["regla"] for a in I.actualizar(s, j1, _comp(ROTAS), {})] == ["rotas"]
    (inc,) = _vivos(s)
    assert (inc.estado, inc.veces, inc.nombre_serie, inc.cliente_id) == ("abierto", 1, "semanal", serie[0].id)

    j2 = _job(s, serie, 8)
    assert I.actualizar(s, j2, _comp(dict(ROTAS, paginas=35)), {}) == []   # ya se aviso
    s.refresh(inc)
    assert (inc.veces, inc.ultimo_job_id, inc.detalle["paginas"]) == (2, j2.id, 35)

    j3 = _job(s, serie, 15)
    assert I.actualizar(s, j3, _comp(), {}) == []
    s.refresh(inc)
    assert (inc.estado, inc.cerrado_job_id) == ("cerrado", j3.id)

    # Vuelve: es otro incidente, y se avisa otra vez.
    j4 = _job(s, serie, 22)
    assert [a["regla"] for a in I.actualizar(s, j4, _comp(ROTAS), {})] == ["rotas"]
    assert len(_vivos(s)) == 1


@pytest.mark.parametrize("comparacion", [
    None,                                     # primer rastreo de la serie: sin referencia
    {"comparable": False, "alertas": []},     # otro alcance, otro host
    _comp(concluyente=False),                 # el censo se corto
])
def test_no_ver_una_alerta_no_es_verla_resuelta(s, serie, comparacion):
    I.actualizar(s, _job(s, serie, 1), _comp(ROTAS), {})
    I.actualizar(s, _job(s, serie, 8), comparacion, {})
    assert [i.estado for i in _vivos(s)] == ["abierto"]


def test_solo_el_rastreo_mas_reciente_abre_o_cierra(s, serie):
    viejo = _job(s, serie, 1)
    I.actualizar(s, _job(s, serie, 8), _comp(ROTAS), {})
    # El viejo termina (o se re-analiza) despues: ni cierra ni abre nada.
    assert I.actualizar(s, viejo, _comp(), {}) == []
    assert I.actualizar(s, viejo, _comp(dict(ROTAS, regla="salen_del_indice")), {}) == []
    assert [i.clave for i in _vivos(s)] == ["rotas"]


def test_un_rastreo_sin_serie_no_lleva_incidentes(s, serie):
    from shared.models import Job

    j = Job(id=uuid.uuid4(), name="a mano", status="completed", seeds=["https://x.com/"],
            config={}, cliente_id=serie[0].id, created_at=DIA[1])
    s.add(j)
    s.flush()
    assert I.actualizar(s, j, _comp(ROTAS), {}) is None
    assert _vivos(s) == []


def test_dos_campos_extraidos_son_dos_incidentes(s, serie):
    a = {"regla": "extraido_cambia", "severidad": "warning", "extraccion": "precio"}
    b = {"regla": "extraido_cambia", "severidad": "warning", "extraccion": "sku"}
    assert len(I.actualizar(s, _job(s, serie, 1), _comp(a, b), {})) == 2
    assert sorted(i.clave for i in _vivos(s)) == ["extraido_cambia:precio", "extraido_cambia:sku"]


def test_el_silencio_dura_aunque_se_cierre_y_vuelva(s, serie):
    I.actualizar(s, _job(s, serie, 1), _comp(ROTAS), {})
    (inc,) = _vivos(s)
    inc.silenciado_hasta = date(2026, 10, 31)
    I.actualizar(s, _job(s, serie, 8), _comp(), {}, ahora=DIA[8])             # se cierra
    assert I.actualizar(s, _job(s, serie, 15), _comp(ROTAS), {}, ahora=DIA[15]) == []  # vuelve: callado
    assert len(_vivos(s)) == 1                                                  # pero se lleva


def test_una_regla_del_cliente_solo_se_cierra_si_se_evaluo(s, serie):
    regla = {"regla": "cliente:fichas-rotas", "id": "fichas-rotas", "severidad": "critical",
             "texto": "Fichas rotas: 25", "avisar": True}
    I.actualizar(s, _job(s, serie, 1), None, {"evaluadas": 1, "disparadas": [regla]})
    (inc,) = _vivos(s)
    assert inc.titulo == "Fichas rotas: 25"
    # Sin referencia valida esa regla no se pudo evaluar: sigue abierta.
    I.actualizar(s, _job(s, serie, 8), None, {"evaluadas": 0, "disparadas": [],
                                             "sin_evaluar": [{"id": "fichas-rotas", "motivo": "x"}]})
    # Detras de un muro: nada se evalua.
    I.actualizar(s, _job(s, serie, 15), None, {"evaluadas": 0, "disparadas": [], "motivo": "muro"})
    assert [i.estado for i in _vivos(s)] == ["abierto"]
    I.actualizar(s, _job(s, serie, 22), None, {"evaluadas": 1, "disparadas": []})
    assert _vivos(s) == []


def test_el_worker_solo_avisa_de_lo_que_abre(s, serie, monkeypatch):
    import worker

    import shared.avisos
    import shared.comparacion
    import shared.database

    enviados = []
    monkeypatch.setattr(shared.database, "SessionLocal", lambda: s)
    monkeypatch.setattr(shared.comparacion, "comparar_con_el_anterior", lambda *a, **k: _comp(ROTAS))
    monkeypatch.setattr(shared.avisos, "avisar_de_alertas",
                        lambda jid, nombre, alertas, **k: enviados.append([a["regla"] for a in alertas]))
    monkeypatch.setattr(s, "close", lambda: None)
    for dia in (1, 8):
        j = _job(s, serie, dia)
        s.commit()
        worker._comparar_con_el_censo_anterior(j.id)
    assert enviados == [["rotas"]]          # la segunda semana, nada
    (inc,) = _vivos(s)
    assert inc.veces == 2


def test_el_resumen_dice_lo_que_sigue_abierto_y_desde_cuando(s, serie):
    from shared import resumen_semanal as RS
    from shared.models import Programacion

    c, p = serie
    s.add(Programacion(id=uuid.uuid4(), nombre="semanal", cliente_id=c.id, profile_id=p.id,
                       seeds=["https://x.com/"], config={}, cron="0 3 * * 1", activa=True))
    I.actualizar(s, _job(s, serie, 1), _comp(ROTAS), {}, ahora=DIA[1])
    I.actualizar(s, _job(s, serie, 8), _comp(ROTAS), {}, ahora=DIA[8])
    s.commit()
    r = RS.generar(s, c, datetime(2026, 10, 12, 8, tzinfo=timezone.utc))
    assert len(r["contenido"]["incidentes_vivos"]) == 1
    assert "Siguen abiertos (1)" in r["texto"]
    assert "30 de 100 páginas (30.0%) — Sale del índice. — desde el 02/10, 2 rastreos" in r["texto"]


def test_el_worker_manda_el_informe_solo_de_lo_programado(s, serie, monkeypatch):
    """Decision 149: el informe de un rastreo programado va al Slack del
    cliente, con el cliente de verdad, y DESPUES de sus alertas."""
    import worker

    import shared.avisos
    import shared.comparacion
    import shared.database

    orden = []
    monkeypatch.setattr(shared.database, "SessionLocal", lambda: s)
    monkeypatch.setattr(shared.comparacion, "comparar_con_el_anterior", lambda *a, **k: _comp(ROTAS))
    monkeypatch.setattr(shared.avisos, "avisar_de_alertas",
                        lambda jid, nombre, alertas, cliente=None: orden.append(("alertas", cliente.slug)))
    monkeypatch.setattr(shared.avisos, "avisar_de_informe",
                        lambda cliente, job, r: orden.append(("informe", cliente.slug, r["job_id"])))
    monkeypatch.setattr(s, "close", lambda: None)
    a_mano = _job(s, serie, 1)
    s.commit()
    worker._comparar_con_el_censo_anterior(a_mano.id)
    assert orden == [("alertas", "acme")]
    orden.clear()
    programado = _job(s, serie, 8, programacion_id=uuid.uuid4())
    s.commit()
    worker._comparar_con_el_censo_anterior(programado.id)
    # La alerta ya estaba abierta: no se repite, pero el informe sale.
    assert orden == [("informe", "acme", str(programado.id))]
