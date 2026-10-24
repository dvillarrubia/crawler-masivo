"""Lo de back que pide la interfaz del adaptador WordPress (decision 168),
EJECUTANDO los endpoints contra una base de verdad (SQLite; Postgres con
PAGERANK_TEST_DATABASE_URL, como test_segmentos_db):

- la ficha de una URL lleva la entidad de WordPress que es (tipo e ID), por
  `url_hash` o por la misma URL con el otro esquema, y nunca una sonda;
- «Solo en WordPress» (`/wordpress/entidades?solo_no_rastreadas=true`) deja
  fuera las sondas, que no son entidades del sitio, y busca en la URL;
- `/wordpress/assets` saca plugins y tema de la ruta de los CSS y JS, con la
  version del `?ver=` solo cuando tiene forma de version y no es una copia de
  cache. Las URLs son de un censo de verdad (Lopesan, blog en WordPress con
  Elementor y WP Rocket, leidas en produccion el 2026-10-24).
"""

from __future__ import annotations

import os
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
from shared.models import AdapterEntity, Base, Job, Resource, Url  # noqa: E402

URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")
MOTORES = ["sqlite"] + (["postgres"] if URL_PG else [])

B = "https://www.lopesan.com/blog/wp-content"
# (recurso, tipo): lo que cargan las paginas del blog, tal cual.
RECURSOS = [
    (f"{B}/plugins/elementor/assets/css/frontend.min.css?ver=4.1.4", "css"),
    (f"{B}/plugins/elementor/assets/css/widget-heading.min.css?ver=4.1.4", "css"),
    (f"{B}/plugins/pro-elements/assets/css/widget-search.min.css?ver=4.1.0", "css"),
    (f"{B}/themes/astra/assets/css/minified/main.min.css?ver=4.13.6", "css"),
    # Copia de WP Rocket: el plugin si, el numero de cache no es su version.
    (f"{B}/cache/min/1/blog/wp-content/plugins/jet-engine/assets/css/frontend.css?ver=1791341166", "css"),
    # `ver=1` (plantilla de WPML) y un sello de tiempo: sin forma de version.
    (f"{B}/plugins/sitepress-multilingual-cms/templates/language-switchers/menu-item/style.min.css?ver=1", "css"),
    (f"{B}/themes/astra-child/style.css?ver=1781175533", "css"),
    # Version igual a la del nucleo: WordPress la pone si el plugin no pasa la suya.
    (f"{B}/plugins/wp-sin-version/x.js?ver=6.8.3", "js"),
    # Ni CSS ni JS: una imagen de un plugin no cuenta.
    (f"{B}/plugins/solo-imagen/logo.png", "image"),
    ("https://www.lopesan.com/blog/wp-includes/js/jquery/jquery.min.js?ver=3.7.1", "js"),
]


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture(params=MOTORES)
def api(request, tmp_path):
    from api.routers import results

    engine = (create_engine(URL_PG) if request.param == "postgres"
              else create_engine(f"sqlite:///{tmp_path}/w.db"))
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    with Sesion() as s:
        job = Job(id=uuid.uuid4(), name="wp", status="completed", seeds=["https://x.com/"],
                  config={}, adapters={"wordpress": {"detectado": True, "rest": "abierta",
                                                     "version": "6.8.3"}})
        s.add(job)
        s.flush()
        urls = {}
        for ruta in ("/a/", "/b/", "/c/"):
            u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=f"h{ruta}-{job.id}",
                    host="x.com", path=ruta, is_internal=True, is_html=True, status_code=200,
                    resource_type="html")
            s.add(u)
            urls[ruta] = u
        s.flush()
        # /a/ y /b/ cargan todo; /c/ solo el tema (un plugin en 2 paginas, el tema en 3).
        for ruta in ("/a/", "/b/"):
            for rec, tipo in RECURSOS:
                s.add(Resource(url_id=urls[ruta].id, resource_url=rec, resource_type=tipo))
        s.add(Resource(url_id=urls["/c/"].id, resource_url=RECURSOS[3][0], resource_type="css"))
        h = lambda r: f"h{r}-{job.id}"  # noqa: E731
        s.add_all([
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="post", id_externo="11",
                          url="https://x.com/a/", url_hash=h("/a/"), estado="publish",
                          modificado="2026-09-30T10:00:00"),
            # `home` en http: la del censo es la https, por el hash alternativo.
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="category", id_externo="3",
                          url="http://x.com/b/", url_hash="otro", url_hash_alt=h("/b/"), cuenta=1),
            # Una sonda con la URL de /c/: no es una entidad del sitio.
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="sonda_adjunto",
                          url="https://x.com/c/", url_hash=h("/c/"), status_code=200),
            # Solo en WordPress: una entrada y una sonda de paginacion.
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="post", id_externo="12",
                          url="https://x.com/Nueva-entrada/", url_hash="sin-censo-1"),
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="page", id_externo="7",
                          url="https://x.com/contacto/", url_hash="sin-censo-2"),
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="sonda_paginacion",
                          url="https://x.com/page/9/", url_hash="sin-censo-3", status_code=404),
        ])
        s.commit()
        ids = {"job": job.id, **{r: u.id for r, u in urls.items()}}

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


def test_ficha_de_url_con_su_entidad(api):
    cliente, ids = api
    r = cliente.get(f"/api/jobs/{ids['job']}/urls/{ids['/a/']}")
    assert r.status_code == 200, r.text
    assert r.json()["wordpress"] == [{"tipo": "post", "id": "11", "estado": "publish",
                                      "modificado": "2026-09-30T10:00:00", "cuenta": None,
                                      "url": "https://x.com/a/"}]


def test_ficha_por_el_otro_esquema(api):
    cliente, ids = api
    w = cliente.get(f"/api/jobs/{ids['job']}/urls/{ids['/b/']}").json()["wordpress"]
    assert [(e["tipo"], e["id"], e["cuenta"]) for e in w] == [("category", "3", 1)]


def test_una_sonda_no_es_la_entidad_de_la_url(api):
    cliente, ids = api
    assert cliente.get(f"/api/jobs/{ids['job']}/urls/{ids['/c/']}").json()["wordpress"] == []


def test_solo_en_wordpress_sin_sondas(api):
    cliente, ids = api
    r = cliente.get(f"/api/jobs/{ids['job']}/wordpress/entidades",
                    params={"solo_no_rastreadas": True})
    assert r.status_code == 200, r.text
    assert [(e["tipo"], e["id_externo"]) for e in r.json()["items"]] == [("page", "7"), ("post", "12")]
    # Sin el filtro las sondas siguen saliendo: es la lista de todo lo leido.
    todas = cliente.get(f"/api/jobs/{ids['job']}/wordpress/entidades").json()
    assert {e["tipo"] for e in todas["items"]} >= {"sonda_adjunto", "sonda_paginacion"}


def test_solo_en_wordpress_busca_en_la_url(api):
    cliente, ids = api
    r = cliente.get(f"/api/jobs/{ids['job']}/wordpress/entidades",
                    params={"solo_no_rastreadas": True, "buscar": "nueva"})
    assert [e["url"] for e in r.json()["items"]] == ["https://x.com/Nueva-entrada/"]
    # Un comodin de LIKE se busca tal cual.
    r = cliente.get(f"/api/jobs/{ids['job']}/wordpress/entidades",
                    params={"solo_no_rastreadas": True, "buscar": "%"})
    assert r.json()["total"] == 0


def test_inventario_por_los_ficheros(api):
    cliente, ids = api
    r = cliente.get(f"/api/jobs/{ids['job']}/wordpress/assets")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["paginas_miradas"] == 3 and d["version_wordpress"] == "6.8.3"
    temas = {t["slug"]: t for t in d["temas"]}
    plugins = {p["slug"]: p for p in d["plugins"]}
    assert [t["slug"] for t in d["temas"]] == ["astra", "astra-child"]
    assert temas["astra"]["paginas"] == 3
    assert temas["astra"]["versiones"] == [{"version": "4.13.6", "paginas": 3,
                                            "igual_que_wordpress": False}]
    assert set(plugins) == {"elementor", "pro-elements", "jet-engine",
                            "sitepress-multilingual-cms", "wp-sin-version"}
    assert plugins["elementor"]["paginas"] == 2
    assert [v["version"] for v in plugins["elementor"]["versiones"]] == ["4.1.4"]
    # Copia de cache, `ver=1` y sello de tiempo: el plugin sale, sin version.
    assert plugins["jet-engine"]["versiones"] == []
    assert plugins["sitepress-multilingual-cms"]["versiones"] == []
    assert temas["astra-child"]["versiones"] == []
    assert plugins["wp-sin-version"]["versiones"][0]["igual_que_wordpress"] is True


def test_inventario_con_muestra(api):
    cliente, ids = api
    d = cliente.get(f"/api/jobs/{ids['job']}/wordpress/assets", params={"muestra": 1}).json()
    # La primera pagina por orden de rastreo (/a/) carga todo, cada uno en 1.
    assert d["paginas_miradas"] == 1
    assert {p["paginas"] for p in d["plugins"]} == {1}


def test_inventario_de_un_job_sin_nada(api):
    cliente, _ = api
    r = cliente.get(f"/api/jobs/{uuid.uuid4()}/wordpress/assets")
    assert r.status_code == 404
