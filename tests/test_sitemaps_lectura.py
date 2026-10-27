"""«No incluida en el sitemap» con una lectura de sitemaps parcial (decision
205): la regla de `shared/sitemaps_lectura.py`, la misma que la del frontend
(ejecutando las dos sobre los mismos casos), y los totales del informe y del
aviso de Slack sin ese tipo cuando cualquiera de los dos censos se leyo a
medias, EJECUTADOS contra SQLite."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.models import Base, Client, Job
from shared.sitemaps_lectura import lectura_dudosa, sin_tipos_de_sitemap


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


CASOS = [
    None,
    {},
    {"ficheros": [{"formato": "urlset", "status": 200, "devueltas": 3}]},
    {"parcial": True, "ficheros": []},
    {"ficheros": [{"formato": "urlset", "status": 200}, {"formato": "desconocido", "status": 200, "bytes": 0}]},
    {"ficheros": [{"formato": "sitemapindex", "status": 200, "hijos": 3}, {"status": 404}]},
    {"ficheros": [{"formato": None, "status": 200}]},
    {"ficheros": [{"status": None}]},
]


def test_la_regla_de_python():
    assert [lectura_dudosa(c) for c in CASOS] == [False, False, False, True, True, True, True, False]


def test_la_misma_regla_que_el_frontend():
    pytest.importorskip("playwright")
    from test_pulido_frontend import _evaluar

    js = _evaluar("() => %s.map(s => { const r = resumenSitemaps({ sitemaps: s }); return !!(r && r.dudoso) })"
                  % json.dumps(CASOS))
    assert js == [lectura_dudosa(c) for c in CASOS]


INC = {
    "comparable": True,
    "nuevas": {"total": 240, "por_tipo": [{"tipo": "not_in_sitemap", "total": 200}, {"tipo": "status_4xx", "total": 40}]},
    "resueltas": {"total": 775, "por_tipo": [{"tipo": "not_in_sitemap", "total": 700}, {"tipo": "status_4xx", "total": 75}]},
    "siguen": {"total": 3000, "por_tipo": [{"tipo": "not_in_sitemap", "total": 2900}, {"tipo": "status_4xx", "total": 100}]},
}


def test_los_totales_sin_el_tipo_de_sitemap():
    assert sin_tipos_de_sitemap(INC, True) == {"nuevas": 40, "resueltas": 75, "siguen": 100, "sin_sitemap": True}
    assert sin_tipos_de_sitemap(INC, False) == {"nuevas": 240, "resueltas": 775, "siguen": 3000, "sin_sitemap": False}


DUDOSO = {"ficheros": [{"formato": "desconocido", "status": 200, "bytes": 0}]}
ENTERO = {"ficheros": [{"formato": "urlset", "status": 200, "devueltas": 3}]}


@pytest.fixture()
def sesion(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    c = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={}, integraciones=[])
    s.add(c)
    s.flush()
    s.cliente = c
    yield s
    s.close()


def _par(s, aqui, antes, con_referencia=True):
    ref = Job(id=uuid.uuid4(), name="antes", status="completed", finish_reason="finished", seeds=[], config={},
              cliente_id=s.cliente.id, sitemaps=antes)
    if con_referencia:
        s.add(ref)
    j = Job(id=uuid.uuid4(), name="ahora", status="completed", finish_reason="finished", seeds=[], config={},
            cliente_id=s.cliente.id, sitemaps=aqui,
            comparacion={"comparado_con": str(ref.id), "comparable": True, "incidencias": INC})
    s.add(j)
    s.commit()
    return j


@pytest.mark.parametrize("aqui,antes,con_ref,esperado", [
    (DUDOSO, ENTERO, True, (40, 75, 100, True)),
    (ENTERO, DUDOSO, True, (40, 75, 100, True)),
    (ENTERO, ENTERO, True, (240, 775, 3000, False)),
    # El censo de referencia ya no esta: no se puede comprobar, no se afirma.
    (ENTERO, None, False, (40, 75, 100, True)),
])
def test_el_informe_y_slack_no_cuentan_lo_dudoso(sesion, aqui, antes, con_ref, esperado):
    from shared.avisos import texto_de_informe as texto
    from shared.resumen_semanal import _resumen_de_rastreo

    r = _resumen_de_rastreo(sesion, _par(sesion, aqui, antes, con_ref))
    i = r["incidencias"]
    assert (i["nuevas"], i["resueltas"], i["siguen"], i["sin_sitemap"]) == esperado
    linea = texto("ahora", r)
    assert f"{esperado[0]} incidencias nuevas" in linea
    assert ("sin contar «No incluida en el sitemap»" in linea) is esperado[3]
