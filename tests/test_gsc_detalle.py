"""Search Console por busqueda y sitemaps segun Google
(decision 157), con un Google de mentira: ningun test llama a la red."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared import gsc as G
from shared import gsc_detalle as D
from shared import secretos

LLAVE = {"type": "service_account", "project_id": "lin3s-seo", "client_email": "crawler@lin3s-seo.iam.gserviceaccount.com",
         "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n",
         "token_uri": "https://oauth2.googleapis.com/token"}
HOY = date(2026, 10, 19)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture(autouse=True)
def _secretos(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRETOS_DIR", str(tmp_path / "secretos"))
    monkeypatch.setenv("SEARCH_CONSOLE_DIAS_INICIALES", "3")


@pytest.fixture
def s():
    from shared.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def cliente(s):
    from shared.models import Client, GscEstado

    secretos.guardar("GSC_LIN3S", json.dumps(LLAVE))
    c = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={},
               integraciones=[{"tipo": "gsc", "credencial_env": "GSC_LIN3S", "activa": True,
                               "parametros": {"propiedad": "sc-domain:acme.com"}}])
    s.add(c)
    # Las paginas ya estan bajadas (decision 141): el detalle se rellena aparte.
    s.add(GscEstado(cliente_id=c.id, propiedad="sc-domain:acme.com", estado="ok",
                    primer_dia=date(2026, 7, 1), ultimo_dia=date(2026, 10, 17)))
    s.commit()
    return c


class GoogleFalso:
    def __init__(self, filas=None, sitemaps=None, error=None):
        self.pedidas: list[tuple[tuple, str]] = []
        self.filas = filas or (lambda dims, dia: [
            {"keys": ["https://acme.com/a", "zapatos"], "clicks": 2, "impressions": 20, "position": 4.0},
            {"keys": ["https://acme.com/b", "zapatos rojos"], "clicks": 1, "impressions": 10, "position": 8.0}])
        self._sitemaps = sitemaps if sitemaps is not None else []
        self.error = error

    def __call__(self, info):
        return self

    def consultar(self, propiedad, cuerpo):
        if self.error:
            raise self.error
        self.pedidas.append((tuple(cuerpo["dimensions"]), cuerpo["startDate"]))
        filas = self.filas(cuerpo["dimensions"], cuerpo["startDate"])
        return {"rows": filas[cuerpo["startRow"]: cuerpo["startRow"] + cuerpo["rowLimit"]]}

    def sitemaps(self, propiedad):
        return self._sitemaps


def _integ(c):
    return c.integraciones[0]


def test_rellena_hacia_atras_aunque_las_paginas_ya_esten(s, cliente):
    from shared.models import GscConsulta

    g = GoogleFalso()
    e = D.sincronizar(s, cliente, _integ(cliente), hoy=HOY, consultor=g)
    d = e.detalle
    assert d["consultas"] == {"primer_dia": "2026-10-15", "ultimo_dia": "2026-10-17", "truncados": []}
    assert d["motivo"] is None and all(dims == ("page", "query") for dims, _ in g.pedidas)
    assert len(g.pedidas) == 3
    assert s.query(GscConsulta).count() == 6
    fila = s.query(GscConsulta).filter(GscConsulta.consulta == "zapatos",
                                       GscConsulta.fecha == date(2026, 10, 16)).one()
    assert (fila.clics, fila.impresiones, fila.posicion) == (2, 20, 4.0)
    assert fila.url_clave == G.clave_de_url("https://acme.com/a")
    # El dia siguiente: solape de tres dias y uno nuevo, reescritos sin duplicar
    g2 = GoogleFalso()
    D.sincronizar(s, cliente, _integ(cliente), hoy=HOY + timedelta(days=1), consultor=g2)
    assert sorted({dia for _, dia in g2.pedidas}) == [
        "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18"]
    assert s.query(GscConsulta).count() == 10


def test_dos_urls_que_son_la_misma_se_suman_por_consulta(s, cliente):
    from shared.models import GscConsulta

    filas = lambda dims, dia: [
        {"keys": ["https://acme.com/a?x=1&y=2", "q"], "clicks": 1, "impressions": 10, "position": 2},
        {"keys": ["https://acme.com/a?y=2&x=1", "q"], "clicks": 3, "impressions": 30, "position": 6}]
    D.sincronizar(s, cliente, _integ(cliente), hoy=HOY, consultor=GoogleFalso(filas))
    fila = s.query(GscConsulta).filter(GscConsulta.fecha == date(2026, 10, 17)).one()
    assert (fila.clics, fila.impresiones, fila.posicion) == (4, 40, 5.0)


def test_el_tope_de_filas_marca_el_dia_en_su_serie(s, cliente, monkeypatch):
    monkeypatch.setenv("SEARCH_CONSOLE_DIAS_INICIALES", "1")
    monkeypatch.setattr(G, "FILAS_POR_PAGINA", 2)
    monkeypatch.setattr(G, "TOPE_FILAS_DIA", 4)
    muchas = lambda dims, dia: [{"keys": [f"https://acme.com/x{i}", "q"], "clicks": 1, "impressions": 1,
                                 "position": 1} for i in range(10)]
    e = D.sincronizar(s, cliente, _integ(cliente), hoy=HOY, consultor=GoogleFalso(muchas))
    assert e.detalle["consultas"]["truncados"] == ["2026-10-17"]


def test_sitemaps_segun_google_y_los_que_desaparecen(s, cliente):
    from shared.models import GscSitemap

    sm = [{"path": "https://acme.com/sitemap.xml", "lastSubmitted": "2026-09-01T10:00:00Z",
           "lastDownloaded": "2026-10-18T03:00:00.123Z", "isPending": False, "isSitemapsIndex": True,
           "warnings": "2", "errors": "1", "contents": [{"type": "web", "submitted": "120", "indexed": "0"}]},
          {"path": "https://acme.com/viejo.xml"}]
    e = D.sincronizar(s, cliente, _integ(cliente), hoy=HOY, consultor=GoogleFalso(sitemaps=sm))
    assert e.detalle["sitemaps"] == 2
    f = s.query(GscSitemap).filter(GscSitemap.path == "https://acme.com/sitemap.xml").one()
    assert (f.errores, f.avisos, f.es_indice, f.enviadas) == (1, 2, True, [{"type": "web", "submitted": 120}])
    assert f.descargado.replace(tzinfo=timezone.utc).date() == date(2026, 10, 18)
    D.sincronizar(s, cliente, _integ(cliente), hoy=HOY + timedelta(days=1), consultor=GoogleFalso(sitemaps=sm[:1]))
    assert [x.path for x in s.query(GscSitemap).all()] == ["https://acme.com/sitemap.xml"]


def test_un_error_queda_en_el_motivo_y_se_reintenta_a_las_seis_horas(s, cliente):
    ahora = datetime(2026, 10, 19, 5, tzinfo=timezone.utc)
    e = D.sincronizar(s, cliente, _integ(cliente), hoy=HOY, ahora=ahora,
                      consultor=GoogleFalso(error=G.GscError(403, "x")))
    assert "no tiene acceso" in e.detalle["motivo"]
    assert not D.toca(e, ahora + timedelta(hours=1))
    assert D.toca(e, ahora + timedelta(hours=7))


def test_toca_una_vez_al_dia_y_solo_con_las_paginas_bien(s, cliente):
    from shared.models import GscEstado

    e = s.query(GscEstado).one()
    ahora = datetime(2026, 10, 19, 5, tzinfo=timezone.utc)
    assert D.toca(e, ahora)
    D.sincronizar(s, cliente, _integ(cliente), hoy=HOY, ahora=ahora, consultor=GoogleFalso())
    assert not D.toca(e, ahora + timedelta(hours=10))
    assert D.toca(e, ahora + timedelta(days=1))
    e.estado = "error"
    assert not D.toca(e, ahora + timedelta(days=1))
    assert not D.toca(None, ahora)


def test_lo_que_pasa_de_los_dias_guardados_se_borra(s, cliente, monkeypatch):
    from shared.models import GscConsulta

    monkeypatch.setenv("SEARCH_CONSOLE_DETALLE_DIAS", "90")
    D.sincronizar(s, cliente, _integ(cliente), hoy=HOY, consultor=GoogleFalso())
    s.add(GscConsulta(cliente_id=cliente.id, propiedad="sc-domain:acme.com", fecha=HOY - timedelta(days=91),
                      url_clave="x", consulta="vieja", clics=1, impresiones=1))
    s.commit()
    e = D.sincronizar(s, cliente, _integ(cliente), hoy=HOY + timedelta(days=1), consultor=GoogleFalso())
    assert not s.query(GscConsulta).filter(GscConsulta.consulta == "vieja").count()
    assert e.detalle["consultas"]["primer_dia"] == "2026-10-14"
