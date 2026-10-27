"""La ficha de una URL con lo que Google dice de ella (decision 189),
EJECUTANDO el endpoint contra una base de verdad (SQLite; Postgres con
PAGERANK_TEST_DATABASE_URL):

- la ultima inspeccion del cliente para esa URL, con su fecha, y si Google
  acepta la canonical que vio declarada, si esa es la que declaraba la pagina
  en el censo y si la que elige Google ya es esa (con la regla de
  `shared.indexabilidad.misma_url`, la del analisis);
- una inspeccion fuera de la ventana de la decision 144 (30 dias antes, 14
  despues del censo) va con su fecha pero sin compararla con el censo: son dos
  fechas distintas (revision de la 2.7);
- sin inspeccion, o un censo sin cliente, `google_inspeccion` es None y la
  ficha sale igual;
- una inspeccion con error no cuenta, y la de otro cliente tampoco.

Las URLs son de Lopesan (la ficha de habitaciones en ingles con un canonical
mal concatenado, leida en produccion el 2026-10-26).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.gsc import clave_de_url  # noqa: E402
from shared.models import Base, Client, GscInspeccion, HtmlMeta, Job, Url  # noqa: E402

URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")
MOTORES = ["sqlite"] + (["postgres"] if URL_PG else [])

H = "https://www.lopesan.com"
ROOMS = f"{H}/en/hotels/spain/gran-canaria/san-agustin/abora-interclub-atlantic/rooms"
MAL = f"{H}/es/hoteles/espana/gran-canaria/san-agustin/abora-interclub-atlantic/habitaciones" + ROOMS[len(H):]


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _cuando(d, h=0):
    return datetime(2026, 10, d, h, tzinfo=timezone.utc)


@pytest.fixture(params=MOTORES)
def api(request, tmp_path):
    from api.routers import results

    engine = (create_engine(URL_PG) if request.param == "postgres"
              else create_engine(f"sqlite:///{tmp_path}/g.db"))
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    sufijo = uuid.uuid4().hex[:8]
    with Sesion() as s:
        c = Client(id=uuid.uuid4(), nombre="Lopesan", slug="lopesan-" + sufijo, dominios=[], config_defecto={})
        otro = Client(id=uuid.uuid4(), nombre="Otro", slug="otro-" + sufijo, dominios=[], config_defecto={})
        s.add_all([c, otro])
        s.flush()
        job = Job(id=uuid.uuid4(), name="semanal", status="completed", seeds=[H + "/"], config={},
                  cliente_id=c.id, started_at=_cuando(16))
        suelto = Job(id=uuid.uuid4(), name="sin cliente", status="completed", seeds=[H + "/"], config={})
        s.add_all([job, suelto])
        s.flush()
        ids = {"job": job.id, "suelto": suelto.id}
        for clave, j, url, canon in (
            ("rooms", job, ROOMS, MAL),            # Google ignora un canonical roto
            ("about", job, f"{H}/es/about", None),  # autocanonica sin etiqueta: acepta
            ("viejo", job, f"{H}/de/x", f"{H}/de/x"),  # Google vio otro canonical antes
            ("ya_sigue", job, f"{H}/es/oferta", f"{H}/es/oferta"),  # vio otro, pero ya elige el del censo
            ("tarde", job, f"{H}/es/tarde", f"{H}/es/tarde"),  # inspeccionada un mes despues del censo
            ("nada", job, f"{H}/es/nada", None),    # sin inspeccion
            ("error", job, f"{H}/es/error", None),  # solo una inspeccion con error
            ("suelta", suelto, ROOMS, MAL),         # censo sin cliente
        ):
            u = Url(job_id=j.id, url=url, url_hash=f"{clave}-{j.id}", is_internal=True, is_html=True,
                    status_code=200, indexable=True, gsc_clics=101, gsc_impresiones=5451, gsc_posicion=14.2)
            s.add(u)
            s.flush()
            if canon:
                s.add(HtmlMeta(url_id=u.id, canonical_href=canon))
            ids[clave] = u.id

        def insp(cliente, url, **kw):
            s.add(GscInspeccion(cliente_id=cliente.id, propiedad="sc-domain:lopesan.com", url=url,
                                url_clave=clave_de_url(url), **kw))

        insp(c, ROOMS, fecha=_cuando(19, 2), verdict="PASS", cobertura="Enviada e indexada",
             canonical_usuario=MAL, canonical_google=ROOMS, ultimo_rastreo=_cuando(11, 19))
        # La de otro cliente con la misma URL no se mezcla.
        insp(otro, f"{H}/es/nada", fecha=_cuando(20), verdict="FAIL", cobertura="x")
        insp(c, f"{H}/es/about", fecha=_cuando(18), verdict="PASS", cobertura="Enviada e indexada",
             canonical_usuario=f"{H}/es/about", canonical_google=f"{H}/es/about")
        insp(c, f"{H}/de/x", fecha=_cuando(17), verdict="PASS", cobertura="Enviada e indexada",
             canonical_usuario=f"{H}/es/x", canonical_google=f"{H}/es/x")
        insp(c, f"{H}/es/error", fecha=_cuando(17), error="403")
        insp(c, f"{H}/es/oferta", fecha=_cuando(19), verdict="PASS", cobertura="Enviada e indexada",
             canonical_usuario="https://webserver-lopesan-prd.lfr.cloud/es/oferta",
             canonical_google=f"{H}/es/oferta")
        insp(c, f"{H}/es/tarde", fecha=datetime(2026, 11, 20, tzinfo=timezone.utc), verdict="PASS",
             cobertura="Enviada e indexada", canonical_usuario=f"{H}/es/otra", canonical_google=f"{H}/es/otra")
        s.commit()

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    yield TestClient(app), ids
    engine.dispose()


def _ficha(cliente, ids, clave, job="job"):
    r = cliente.get(f"/api/jobs/{ids[job]}/urls/{ids[clave]}")
    assert r.status_code == 200, r.text
    return r.json()


def test_google_elige_otro_canonical_con_su_fecha(api):
    cliente, ids = api
    f = _ficha(cliente, ids, "rooms")
    g = f["google_inspeccion"]
    assert g["canonical_google"] == ROOMS
    assert g["canonical_visto"] == MAL
    assert g["google_acepta"] is False
    assert g["visto_igual_que_censo"] is True and g["google_igual_que_censo"] is False
    assert g["en_ventana_del_censo"] is True
    assert g["cobertura"] == "Enviada e indexada" and g["verdict"] == "PASS"
    assert g["fecha"].startswith("2026-10-19") and g["ultimo_rastreo"].startswith("2026-10-11")
    # Los datos de Search Console por URL ya venian en la ficha
    assert (f["gsc_clics"], f["gsc_impresiones"], f["gsc_posicion"]) == (101, 5451, 14.2)


def test_autocanonica_sin_etiqueta_google_acepta(api):
    cliente, ids = api
    g = _ficha(cliente, ids, "about")["google_inspeccion"]
    assert g["google_acepta"] is True and g["visto_igual_que_censo"] is True
    assert g["google_igual_que_censo"] is True


def test_google_vio_un_canonical_que_ya_no_esta(api):
    cliente, ids = api
    g = _ficha(cliente, ids, "viejo")["google_inspeccion"]
    assert g["visto_igual_que_censo"] is False
    # y lo que eligio no es la del censo: puede cambiar en la proxima visita
    assert g["google_igual_que_censo"] is False


def test_google_ya_elige_la_del_censo_aunque_viera_otra(api):
    # oferta-om-spa-costa-meloneras-cumpleanos: Google vio declarada la de
    # webserver-lopesan-prd.lfr.cloud y aun asi elige la de www, que es la del
    # censo. Decir «cambiara con la proxima visita» seria falso.
    cliente, ids = api
    g = _ficha(cliente, ids, "ya_sigue")["google_inspeccion"]
    assert g["visto_igual_que_censo"] is False and g["google_igual_que_censo"] is True


def test_inspeccion_fuera_de_la_ventana_no_se_compara_con_el_censo(api):
    cliente, ids = api
    g = _ficha(cliente, ids, "tarde")["google_inspeccion"]
    assert g["fecha"].startswith("2026-11-20")
    assert g["en_ventana_del_censo"] is False
    assert g["visto_igual_que_censo"] is None and g["google_igual_que_censo"] is None
    # lo que no depende del censo, si
    assert g["google_acepta"] is True


def test_sin_inspeccion_con_error_o_sin_cliente_es_none(api):
    cliente, ids = api
    assert _ficha(cliente, ids, "nada")["google_inspeccion"] is None
    assert _ficha(cliente, ids, "error")["google_inspeccion"] is None
    assert _ficha(cliente, ids, "suelta", job="suelto")["google_inspeccion"] is None
