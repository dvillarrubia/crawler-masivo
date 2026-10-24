"""La columna «Cambio frente al anterior» del Explorador mira tambien lo
extraido a medida (#34, decision 166), con los guardias de la comparacion de
censos (decision 127). EJECUTADO con TestClient y SQLite.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, CustomExtraction, HtmlMeta, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


REGLAS = {
    "custom_extraction": {
        "precio": {"etiqueta": "Precio", "tipo": "css", "expresion": ".precio", "valor": "numero"},
        "stock": {"tipo": "css", "expresion": ".stock"},
        "marca": {"tipo": "css", "expresion": ".marca"},
    },
    "custom_search": {"gtm": {"etiqueta": "GTM", "patron": "GTM-"}},
}


def _job(s, config, ref=None):
    j = Job(id=uuid.uuid4(), name="t", status="completed", seeds=["https://x.com/"], config=config,
            comparacion={"comparado_con": str(ref)} if ref else None)
    s.add(j)
    s.flush()
    return j.id


def _url(s, jid, ruta):
    u = Url(job_id=jid, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com", path=ruta,
            is_internal=True, is_html=True, status_code=200, status_group="2xx",
            resource_type="html")
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, title="mismo"))
    return u.id


def _ce(s, jid, uid, nombre, estado="ok", valor=None, num=None, clase="extractor", render=False):
    s.add(CustomExtraction(job_id=jid, url_id=uid, nombre=nombre, clase=clase, estado=estado,
                           valor=valor, valor_clave=valor, valor_num=num, truncado=False,
                           render=render))


@pytest.fixture()
def api(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/c.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    # La regla `marca` cambia de expresion entre los dos censos: no se compara.
    config_antes = {**REGLAS, "custom_extraction": {
        **REGLAS["custom_extraction"], "marca": {"tipo": "css", "expresion": ".brand"}}}
    antes = _job(s, config_antes)
    ahora = _job(s, REGLAS, ref=antes)
    ids = {}
    for ruta in ("/p/1", "/p/2", "/p/3", "/p/4"):
        a, b = _url(s, antes, ruta), _url(s, ahora, ruta)
        ids[ruta] = b
        if ruta == "/p/1":       # cambia el precio, deja de contener GTM, la marca no cuenta
            _ce(s, antes, a, "precio", valor="19,95 €", num=19.95)
            _ce(s, ahora, b, "precio", valor="21,00 €", num=21.0)
            _ce(s, antes, a, "gtm", valor="GTM-1", clase="busqueda")
            _ce(s, ahora, b, "gtm", estado="sin_coincidencia", clase="busqueda")
            _ce(s, antes, a, "marca", valor="Nike")
            _ce(s, ahora, b, "marca", valor="Adidas")
        elif ruta == "/p/2":     # sin fila en el anterior; stock con tiempo agotado
            _ce(s, ahora, b, "precio", valor="5 €", num=5.0)
            _ce(s, antes, a, "stock", valor="InStock")
            _ce(s, ahora, b, "stock", estado="tiempo_agotado")
        elif ruta == "/p/3":     # render distinto: no leen la misma pagina
            _ce(s, antes, a, "stock", valor="InStock", render=False)
            _ce(s, ahora, b, "stock", estado="sin_coincidencia", render=True)
        else:                    # desaparece el stock; mismo precio con otro formato
            _ce(s, antes, a, "stock", valor="InStock")
            _ce(s, ahora, b, "stock", estado="sin_coincidencia")
            _ce(s, antes, a, "precio", valor="10,00 €", num=10.0)
            _ce(s, ahora, b, "precio", valor="10 €", num=10.0)
    s.commit()
    s.close()
    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), ahora, ids


def test_la_columna_cambio_mira_lo_extraido_con_los_guardias_de_127(api):
    cliente, jid, ids = api
    r = cliente.get(f"/api/jobs/{jid}/urls?cambios=true&page_size=50")
    assert r.status_code == 200, r.text
    cambio = {i["id"]: i["cambio"] for i in r.json()["items"]}
    assert cambio[ids["/p/1"]] == "deja de contener GTM; Precio 19,95 €→21,00 €"
    assert cambio[ids["/p/2"]] is None          # sin fila antes / tiempo agotado: no se afirma
    assert cambio[ids["/p/3"]] is None          # render distinto
    assert cambio[ids["/p/4"]] == "stock desaparece"   # 10 y 10,00 son el mismo numero

