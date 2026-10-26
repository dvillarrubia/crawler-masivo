"""El perfil como unica unidad (decision 193, revision de UX del 25-oct, 2.2)
y el alta de cliente guiada (decision 194, 2.14). EJECUTADO: se llama a la API
con TestClient, al disparo del worker y al script de migracion de verdad, sobre
SQLite, y se lee lo que quedo escrito (decision 74).

Lo que se sujeta:

- una programacion colgada de un perfil lanza con la configuracion y las
  semillas del MOMENTO (cliente -> perfil -> lo suyo), no con una copia;
- las viejas, de copia fija, siguen lanzando igual (compatibilidad);
- sin perfil, con el perfil desactivado o sin semillas, no se lanza y se dice;
- se avisa de las repetidas (mismo perfil o mismas semillas, activas);
- la migracion no cambia lo que se rastrea, reutiliza el perfil cuando las
  semillas coinciden y une la serie (rastreos e incidentes) a la del perfil;
- el alta guiada crea el perfil «Sitio principal» con la home.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.clientes import fusionar_config  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import (  # noqa: E402
    Base, Client, CrawlProfile, Incidente, Job, Programacion,
)
from shared.programaciones import con_defaults  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


class ColaFalsa:
    def __init__(self):
        self.encolados = []

    def rpush(self, _clave, valor):
        self.encolados.append(valor)


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    from api.routers import clientes, programaciones

    engine = create_engine(f"sqlite:///{tmp_path}/p.db")

    # pysqlite no abre la transaccion hasta el primer INSERT y suelta los
    # SAVEPOINT como si fueran commits: la receta de SQLAlchemy para que los
    # savepoints (la migracion usa uno por programacion) se porten como en
    # PostgreSQL.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sin_autocommit(conexion, _):
        conexion.isolation_level = None

    @event.listens_for(engine, "begin")
    def _begin(conexion):
        conexion.exec_driver_sql("BEGIN")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("shared.database.SessionLocal", Sesion, raising=False)
    app = FastAPI()
    app.include_router(clientes.router)
    app.include_router(programaciones.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), Sesion


def _cliente_y_perfil(api, config_cliente=None, config_perfil=None, seeds=None):
    c = api.post("/api/clients", json={"nombre": "Acme", "dominios": ["www.acme.com"],
                                       "config_defecto": config_cliente or {}})
    assert c.status_code == 201, c.text
    p = api.post(f"/api/clients/{c.json()['id']}/profiles", json={
        "nombre": "Sitio completo", "seeds": seeds or ["https://www.acme.com/"],
        "config": config_perfil or {}})
    assert p.status_code == 201, p.text
    return c.json(), p.json()


def _que_toque_ya(Sesion, prog_id):
    with Sesion() as s:
        s.get(Programacion, uuid.UUID(prog_id)).proxima_ejecucion = (
            datetime.now(timezone.utc) - timedelta(minutes=1))
        s.commit()


def _disparar(cola=None):
    import worker

    cola = cola or ColaFalsa()
    worker._lanzar_programados(cola)
    return cola


# --- La config por defecto del worker no se desfasa ---------------------------

def test_la_copia_de_los_defaults_es_la_de_JobConfig():
    """El worker no lleva la API: usa `shared/config_por_defecto.json`. Si se
    anade un campo a JobConfig sin regenerarla, este test falla. Lleva tambien
    los defaults que valen `None` y los de cada regla con nombre."""
    from api.schemas import BusquedaAMedida, ExtractorAMedida, JobConfig, ReglaSegmento

    def defaults(modelo):
        return {n: (f.default_factory() if f.default_factory else f.default)
                for n, f in modelo.model_fields.items() if not f.is_required()}

    copia = json.loads((Path(__file__).parents[1] / "shared" / "config_por_defecto.json").read_text())
    assert copia == json.loads(json.dumps({
        "config": JobConfig().model_dump(mode="json"),
        "reglas": {"custom_extraction": defaults(ExtractorAMedida),
                   "custom_search": defaults(BusquedaAMedida),
                   "segmentos": defaults(ReglaSegmento)},
    })), "Regenera shared/config_por_defecto.json (ver este test)"


# Configuraciones con la forma de las de produccion (clientes que fijan tope,
# exclusiones y sitemap; un perfil de modo lista) y las que deducen claves.
_UA_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
_CLIENTES = [
    {},
    {"marcas": ["marca", "otra marca"]},
    {"max_depth": 6, "max_urls": 5000, "robots_mode": "respect", "use_sitemap": True,
     "exclude_patterns": [r"\.(jpe?g|png|pdf)(\?|$)", "%25"],
     "crawl_behavior": {"request_delay": 1.0}},
    {"max_depth": 0},
]
_PERFILES = [
    None, {}, {"max_depth": 0}, {"max_depth": 0, "use_sitemap": True},
    {"user_agent": _UA_IPHONE}, {"impersonate": "safari17_0"},
    {"user_agent": _UA_IPHONE, "impersonate": "chrome124"},
    {"custom_extraction": {"precio": {"tipo": "css", "expresion": ".precio"}},
     "custom_search": {"agotado": {"patron": "Agotado"}}},
    {"segmentos": [{"nombre": "blog", "patron": "^/blog/"}]},
    {"crawl_behavior": {"render_wait_ms": 1500, "retry_after_max_s": 60}},
    {"max_urls": None, "use_sitemap": None, "exclude_patterns": None, "max_depth": None},
    {"adapters": {"wordpress": {"modo": "off"}}}, {"topes_por_carpeta": {"/blog/": 50}},
    {"templates": [{"nombre": "ficha", "patron": "^/p/"}]},
]
_PROPIAS = [None, {}, {"max_depth": 0}, {"render_js": True}, {"use_sitemap": True}]


def test_el_worker_resuelve_la_config_igual_que_la_api():
    """Revision del 26-oct de la decision 193: el worker fundia las capas
    sobre los defaults y se saltaba los validadores de JobConfig, asi que un
    perfil de modo lista (`max_depth: 0`) programado sembraba el sitemap
    entero, y un UA de iPhone salia con huella de Chrome. Se compara la
    funcion del worker con la de la API, caso a caso."""
    from types import SimpleNamespace as N

    from api.routers.clientes import resolver_config
    from shared.programaciones import config_del_momento

    casos = 0
    for c in _CLIENTES:
        for p in _PERFILES:
            for propia in _PROPIAS:
                cli = N(config_defecto=c)
                per = N(config=p) if p is not None else None
                api = resolver_config(cli, per, propia)
                worker = config_del_momento(cli, per, propia)
                assert worker == api, (c, p, propia, {
                    k: (api.get(k), worker.get(k))
                    for k in set(api) | set(worker) if api.get(k) != worker.get(k)})
                casos += 1
    assert casos == len(_CLIENTES) * len(_PERFILES) * len(_PROPIAS)


def test_los_defaults_de_cada_regla_son_los_de_pydantic():
    from api.schemas import BusquedaAMedida, ExtractorAMedida, ReglaSegmento
    from shared.programaciones import con_defaults

    ext = {"tipo": "css", "expresion": ".precio", "cuantos": "todos"}
    bus = {"patron": "Agotado"}
    seg = {"nombre": "blog", "patron": "^/blog/"}
    cfg = con_defaults({"custom_extraction": {"precio": ext}, "custom_search": {"agotado": bus},
                        "segmentos": [seg]})
    assert cfg["custom_extraction"]["precio"] == ExtractorAMedida(**ext).model_dump()
    assert cfg["custom_search"]["agotado"] == BusquedaAMedida(**bus).model_dump()
    assert cfg["segmentos"] == [ReglaSegmento(**seg).model_dump()]


def test_un_perfil_de_lista_programado_no_siembra_el_sitemap():
    from types import SimpleNamespace as N

    from shared.programaciones import config_del_momento

    cfg = config_del_momento(N(config_defecto={}), N(config={"max_depth": 0}), None)
    assert cfg["use_sitemap"] is False
    cfg = config_del_momento(N(config_defecto={}), N(config={"user_agent": _UA_IPHONE}), None)
    assert cfg["impersonate"].startswith("safari") and "ios" in cfg["impersonate"]


def test_diferencia_de_config_ida_y_vuelta():
    from shared.clientes import diferencia_de_config, fusionar_config

    base = {"max_depth": 10, "render_js": False, "crawl_behavior": {"a": 1, "b": 2},
            "exclude_patterns": ["/x/"], "custom_extraction": {"precio": {"selector": "a", "solo_urls": "/p/"}},
            "user_agent": "UA"}
    objetivo = {"max_depth": 10, "render_js": True, "crawl_behavior": {"a": 1, "b": 3},
                "exclude_patterns": ["/x/", "/y/"], "custom_extraction": {"precio": {"selector": "b"}}}
    d = diferencia_de_config(base, objetivo)
    assert fusionar_config(base, d) == objetivo
    assert "max_depth" not in d and d["crawl_behavior"] == {"b": 3}
    assert d["custom_extraction"] == {"precio": {"selector": "b"}}, "la regla va entera"
    assert d["user_agent"] is None, "lo que no tiene el objetivo se quita"


# --- El disparo hereda lo del momento -----------------------------------------

def test_colgada_de_un_perfil_hereda_la_config_del_momento(entorno):
    api, Sesion = entorno
    c, p = _cliente_y_perfil(api, config_cliente={"render_js": True, "max_depth": 7},
                             config_perfil={"max_depth": 9})
    r = api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * 1",
                                              "config_propia": {"max_urls": 500}})
    assert r.status_code == 201, r.text
    prog = r.json()
    assert prog["hereda_perfil"] is True and prog["nombre"] == "Sitio completo"
    assert prog["seeds"] == ["https://www.acme.com/"], "enseña las semillas que lanzaria hoy"
    assert prog["config"] == {"max_urls": 500}, "solo lo propio, no una copia"

    # Despues de programar, cambian el cliente y el perfil.
    assert api.patch(f"/api/clients/{c['id']}", json={"config_defecto": {"render_js": False, "max_depth": 7}}).status_code == 200
    assert api.patch(f"/api/clients/{c['id']}/profiles/{p['id']}", json={
        "seeds": ["https://www.acme.com/", "https://www.acme.com/blog/"], "config": {"max_depth": 12}}).status_code == 200

    _que_toque_ya(Sesion, prog["id"])
    cola = _disparar()
    with Sesion() as s:
        job = s.query(Job).one()
        assert cola.encolados == [str(job.id)]
        assert job.seeds == ["https://www.acme.com/", "https://www.acme.com/blog/"]
        assert job.config["render_js"] is False, "el cambio del cliente llega"
        assert job.config["max_depth"] == 12, "el del perfil tambien"
        assert job.config["max_urls"] == 500, "y lo propio de la programacion encima"
        assert job.config["robots_mode"] == "respect", "con los defaults debajo, como un job de la API"
        assert job.profile_id == uuid.UUID(p["id"]) and job.cliente_id == uuid.UUID(c["id"])
        assert job.programacion_id == uuid.UUID(prog["id"])
        assert s.get(CrawlProfile, uuid.UUID(p["id"])).last_job_id == job.id


def test_una_de_copia_fija_sigue_lanzando_su_copia(entorno):
    api, Sesion = entorno
    c, p = _cliente_y_perfil(api, config_cliente={"render_js": True})
    r = api.post("/api/programaciones", json={
        "nombre": "vieja", "cliente_id": c["id"], "seeds": ["https://www.acme.com/es/"],
        "config": {"max_depth": 4}, "cron": "0 3 * * 1"})
    assert r.status_code == 201, r.text
    assert r.json()["hereda_perfil"] is False
    api.patch(f"/api/clients/{c['id']}", json={"config_defecto": {"render_js": False}})
    _que_toque_ya(Sesion, r.json()["id"])
    _disparar()
    with Sesion() as s:
        job = s.query(Job).one()
        assert job.seeds == ["https://www.acme.com/es/"] and job.config["max_depth"] == 4
        assert job.config["render_js"] is False, "la copia es la de JobConfig de cuando se creo"


@pytest.mark.parametrize("que,motivo", [
    ("desactivar", "el perfil esta desactivado"),
    ("borrar_semillas", "el perfil no tiene semillas"),
])
def test_si_el_perfil_no_sirve_no_se_lanza_y_se_dice(entorno, que, motivo):
    api, Sesion = entorno
    c, p = _cliente_y_perfil(api)
    prog = api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * 1"}).json()
    with Sesion() as s:
        perfil = s.get(CrawlProfile, uuid.UUID(p["id"]))
        if que == "desactivar":
            perfil.activo = False
        else:
            perfil.seeds = []
        s.commit()
    _que_toque_ya(Sesion, prog["id"])
    cola = _disparar()
    with Sesion() as s:
        assert s.query(Job).count() == 0 and cola.encolados == []
        prog_db = s.get(Programacion, uuid.UUID(prog["id"]))
        assert prog_db.ultimo_motivo == motivo
        prox = prog_db.proxima_ejecucion
        prox = prox if prox.tzinfo else prox.replace(tzinfo=timezone.utc)
        assert prox > datetime.now(timezone.utc), "y la proxima se recalcula igual"


def test_no_se_lanza_encima_de_un_rastreo_vivo_del_mismo_perfil(entorno):
    api, Sesion = entorno
    c, p = _cliente_y_perfil(api)
    prog = api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * 1"}).json()
    with Sesion() as s:
        s.add(Job(name="a mano", seeds=["https://www.acme.com/"], config={}, status="running",
                  profile_id=uuid.UUID(p["id"]), cliente_id=uuid.UUID(c["id"])))
        s.commit()
    _que_toque_ya(Sesion, prog["id"])
    assert _disparar().encolados == []
    with Sesion() as s:
        assert s.query(Job).count() == 1
        motivo = s.get(Programacion, uuid.UUID(prog["id"])).ultimo_motivo
        assert motivo.startswith("su perfil ya tiene un rastreo"), (
            "no dice «el anterior de esta programacion» cuando el vivo es otro")


def test_una_de_copia_fija_con_perfil_no_mira_los_rastreos_del_perfil(entorno):
    """Compatibilidad: antes de la decision 193 una de copia fija solo miraba
    su propio rastreo anterior, aunque tuviera `profile_id`."""
    api, Sesion = entorno
    c, p = _cliente_y_perfil(api)
    prog = api.post("/api/programaciones", json={
        "profile_id": p["id"], "hereda_perfil": False, "nombre": "copia",
        "seeds": ["https://www.acme.com/"], "config": {"max_depth": 1},
        "cron": "0 3 * * 1"}).json()
    with Sesion() as s:
        s.add(Job(name="a mano", seeds=["https://www.acme.com/"], config={}, status="running",
                  profile_id=uuid.UUID(p["id"]), cliente_id=uuid.UUID(c["id"])))
        s.commit()
    _que_toque_ya(Sesion, prog["id"])
    assert len(_disparar().encolados) == 1


def test_el_perfil_creado_desde_un_rastreo_conserva_su_config(entorno):
    """«Programar» desde un rastreo con «Crear un perfil con esas semillas»
    guardaba solo las semillas: un censo con exclusiones propias (`%25`, la que
    evita los 400 de un F5 por triple codificacion) salia sin ellas en cuanto
    lo lanzaba la programacion. Ahora el perfil guarda lo que el rastreo tiene
    de distinto respecto al cliente, y lo que se lanzaria es lo mismo."""
    from api.routers.clientes import resolver_config
    from api.schemas import JobConfig

    api, Sesion = entorno
    c = api.post("/api/clients", json={"nombre": "Acme", "dominios": ["www.acme.com"],
                                       "config_defecto": {"marcas": ["acme"]}}).json()
    # La config de un job es la resuelta entera (con los defaults), como la
    # guarda la API al lanzarlo; y una clave que JobConfig ya no tiene.
    cfg_job = JobConfig.model_validate({
        "marcas": ["acme"], "max_depth": 200, "render_js": True, "robots_mode": "ignore",
        "exclude_patterns": ["_redirect=", "p_p_id=", "%25", "e-page-"],
        "http": {"basic_auth_user": "u", "basic_auth_password": "secreta"},
    }).model_dump(mode="json")
    cfg_job["clave_de_otra_epoca"] = 1
    with Sesion() as s:
        job = Job(name="censo", seeds=["https://www.acme.com/", "https://www.acme.com/b"],
                  config=cfg_job, status="completed", cliente_id=uuid.UUID(c["id"]))
        s.add(job)
        s.commit()
        job_id = job.id
    r = api.post(f"/api/clients/{c['id']}/profiles", json={
        "nombre": "Sitio completo", "seeds": ["https://www.acme.com/"], "desde_job_id": str(job_id)})
    assert r.status_code == 201, r.text
    perfil = r.json()
    assert perfil["config"]["exclude_patterns"] == ["_redirect=", "p_p_id=", "%25", "e-page-"]
    assert "marcas" not in perfil["config"], "lo que ya da el cliente no se copia"
    assert "basic_auth_password" not in (perfil["config"].get("http") or {}) or \
        not perfil["config"]["http"]["basic_auth_password"]
    assert any("contraseña" in a for a in perfil["avisos"])
    assert any("clave_de_otra_epoca" in a for a in perfil["avisos"])

    from types import SimpleNamespace as N
    resuelta = resolver_config(N(config_defecto=c["config_defecto"]), N(config=perfil["config"]), {})
    esperada = {k: v for k, v in cfg_job.items() if k != "clave_de_otra_epoca"}
    esperada["http"] = {**esperada["http"], "basic_auth_password": ""}
    assert json.loads(json.dumps(resuelta)) == esperada

    otro = api.post("/api/clients", json={"nombre": "Otro", "dominios": ["otro.example"]}).json()
    r = api.post(f"/api/clients/{otro['id']}/profiles", json={
        "nombre": "x", "seeds": ["https://otro.example/"], "desde_job_id": str(job_id)})
    assert r.status_code == 422


def test_borrar_el_perfil_se_lleva_sus_programaciones_colgadas(entorno):
    api, Sesion = entorno
    c, p = _cliente_y_perfil(api)
    colgada = api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * 1"}).json()
    fija = api.post("/api/programaciones", json={
        "nombre": "fija", "profile_id": p["id"], "seeds": ["https://www.acme.com/"],
        "cron": "0 3 * * 1"}).json()
    assert fija["hereda_perfil"] is False
    assert api.delete(f"/api/clients/{c['id']}/profiles/{p['id']}").status_code == 204
    assert api.get(f"/api/programaciones/{colgada['id']}").status_code == 404
    queda = api.get(f"/api/programaciones/{fija['id']}").json()
    assert queda["profile_id"] is None, "la de copia fija sigue, sin perfil"


def test_validaciones_al_crear(entorno):
    api, _ = entorno
    c, p = _cliente_y_perfil(api)
    # Hereda sin perfil
    r = api.post("/api/programaciones", json={"hereda_perfil": True, "cron": "0 3 * * 1"})
    assert r.status_code == 422
    # Hereda y manda semillas
    r = api.post("/api/programaciones", json={"profile_id": p["id"], "hereda_perfil": True,
                                              "seeds": ["https://www.acme.com/"], "cron": "0 3 * * 1"})
    assert r.status_code == 422 and "semillas del perfil" in r.text
    # Una clave que no existe en lo propio se nombra (decision 33)
    r = api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * 1",
                                              "config_propia": {"no_existe": 1}})
    assert r.status_code == 422 and "no_existe" in r.text
    # Lo propio solo se edita en las colgadas
    fija = api.post("/api/programaciones", json={"nombre": "f", "seeds": ["https://www.acme.com/"],
                                                 "cron": "0 3 * * 1"}).json()
    r = api.patch(f"/api/programaciones/{fija['id']}", json={"config_propia": {"max_urls": 3}})
    assert r.status_code == 422


def test_editar_cuando_y_lo_propio(entorno):
    api, _ = entorno
    c, p = _cliente_y_perfil(api)
    prog = api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * 1"}).json()
    r = api.patch(f"/api/programaciones/{prog['id']}", json={
        "cron": "0 11 * * 1", "zona_horaria": "Europe/Madrid", "config_propia": {"max_urls": 250}})
    assert r.status_code == 200, r.text
    assert r.json()["cron"] == "0 11 * * 1" and r.json()["config"] == {"max_urls": 250}
    prox = datetime.fromisoformat(r.json()["proxima_ejecucion"].replace("Z", "+00:00"))
    from zoneinfo import ZoneInfo
    local = prox.astimezone(ZoneInfo("Europe/Madrid"))
    assert (local.weekday(), local.hour, local.minute) == (0, 11, 0), "lunes a las 11:00 de Madrid"


# --- Repetidas ------------------------------------------------------------------

def test_avisa_de_programaciones_repetidas_activas(entorno):
    api, _ = entorno
    c, p = _cliente_y_perfil(api, seeds=["https://www.acme.com/"])
    a = api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 11 * * 1"}).json()
    b = api.post("/api/programaciones", json={"nombre": "copia", "cliente_id": c["id"],
                                              "seeds": ["https://www.acme.com/"], "cron": "0 3 * * 3"}).json()
    otra = api.post("/api/programaciones", json={"nombre": "blog", "cliente_id": c["id"],
                                                 "seeds": ["https://blog.acme.com/"], "cron": "0 3 * * 3"}).json()
    lista = {x["id"]: x for x in api.get("/api/programaciones", params={"cliente_id": c["id"]}).json()}
    assert [r["id"] for r in lista[a["id"]]["repetida_con"]] == [b["id"]]
    assert lista[a["id"]]["repetida_con"][0]["motivo"] == "mismas semillas"
    assert lista[otra["id"]]["repetida_con"] == []
    assert lista[a["id"]]["perfil_nombre"] == "Sitio completo" and lista[a["id"]]["cliente_nombre"] == "Acme"

    # Pausada, ya no es repetida de nadie.
    api.patch(f"/api/programaciones/{b['id']}", json={"activa": False})
    lista = {x["id"]: x for x in api.get("/api/programaciones").json()}
    assert lista[a["id"]]["repetida_con"] == [] and lista[b["id"]]["repetida_con"] == []

    # Dos del mismo perfil
    api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * *",
                                          "config_propia": {"max_urls": 250}})
    lista = {x["id"]: x for x in api.get("/api/programaciones", params={"resumen": True}).json()}
    assert lista[a["id"]]["repetida_con"][0]["motivo"] == "mismo perfil"


def test_resumen_recorta_las_semillas_pero_dice_cuantas(entorno):
    api, _ = entorno
    semillas = [f"https://www.acme.com/p{i}" for i in range(10)]
    c, p = _cliente_y_perfil(api, seeds=semillas)
    api.post("/api/programaciones", json={"profile_id": p["id"], "cron": "0 3 * * 1"})
    [x] = api.get("/api/programaciones", params={"resumen": True}).json()
    assert len(x["seeds"]) == 3 and x["n_semillas"] == 10


# --- Migracion -------------------------------------------------------------------

def _lopesan(Sesion):
    """Como Lopesan el 26-oct: un perfil «lista» (52 semillas) nunca lanzado y
    tres programaciones de copia fija con las mismas semillas, una activa."""
    semillas = [f"https://www.lopesan.com/es/{i}/" for i in range(30)]
    ahora = datetime.now(timezone.utc)
    with Sesion() as s:
        c = Client(nombre="lopesan", slug="lopesan", dominios=[],
                   config_defecto={"render_js": True})
        s.add(c)
        s.flush()
        s.add(CrawlProfile(cliente_id=c.id, nombre="lista", seeds=semillas[:5], config={"max_depth": 0}))
        progs = []
        for nombre, cron, activa, extra, edad in (
                ("lopesan.com (semanal)", "0 11 * * 1", True, {}, 1),
                ("lopesan.com (semanal)", "0 3 * * 3", False, {"sitemap_urls": ["https://www.lopesan.com/s.xml"]}, 2),
                ("lopesan.com (semanal)", "0 3 * * 1", False, {}, 3)):
            cfg = {"render_js": True, "max_depth": 200, "robots_mode": "ignore",
                   "user_agent": "Mozilla/5.0 Chrome", **extra}
            p = Programacion(id=uuid.uuid4(), nombre=nombre, client_id="lopesan", cliente_id=c.id,
                             seeds=list(semillas), config=cfg, cron=cron, zona_horaria="Europe/Madrid",
                             activa=activa, created_at=ahora - timedelta(days=edad))
            s.add(p)
            progs.append(p)
        s.flush()
        # Dos censos de la activa y uno de la del miercoles, e incidentes.
        for i, prog in enumerate((progs[0], progs[0], progs[1])):
            s.add(Job(name=f"j{i}", seeds=list(semillas), config=prog.config, status="completed",
                      cliente_id=c.id, programacion_id=prog.id, created_at=ahora - timedelta(days=10 - i)))
        for prog, clave in ((progs[0], "rotas"), (progs[1], "rotas"), (progs[1], "salen")):
            s.add(Incidente(cliente_id=c.id, serie=f"programacion:{prog.id}", nombre_serie=prog.nombre,
                            clave=clave, regla=clave, severidad="warning", titulo=clave, estado="abierto",
                            detalle={}))
        s.commit()
        return c.id, [p.id for p in progs]


def _script():
    import importlib.util

    ruta = Path(__file__).parents[1] / "scripts" / "migrar_programaciones_a_perfiles.py"
    spec = importlib.util.spec_from_file_location("migrar_programaciones_a_perfiles", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _efectiva_antes(Sesion, ids):
    from shared.programaciones import resolver_disparo

    with Sesion() as s:
        return {pid: resolver_disparo(s, s.get(Programacion, pid))[0] for pid in ids}


def test_la_migracion_en_simulacion_no_escribe_nada(entorno):
    M = _script()

    _, Sesion = entorno
    cid, ids = _lopesan(Sesion)
    with Sesion() as s:
        lineas = M.migrar(s, confirmar=False)
    assert [l["accion"] for l in lineas] == ["colgada"] * 3
    with Sesion() as s:
        assert s.query(CrawlProfile).count() == 1, "sigue solo «lista»"
        assert all(not p.hereda_perfil for p in s.query(Programacion))
        assert s.query(Job).filter(Job.profile_id.isnot(None)).count() == 0


def test_la_migracion_deja_un_perfil_sitio_completo_y_no_cambia_lo_que_se_rastrea(entorno):
    M = _script()

    _, Sesion = entorno
    cid, ids = _lopesan(Sesion)
    antes = _efectiva_antes(Sesion, ids)
    with Sesion() as s:
        lineas = M.migrar(s, confirmar=True)
    assert [l["perfil_nuevo"] for l in lineas] == [True, False, False], "una crea, las otras reutilizan"

    with Sesion() as s:
        perfiles = {p.nombre: p for p in s.query(CrawlProfile)}
        assert set(perfiles) == {"lista", "Sitio completo"}
        sitio = perfiles["Sitio completo"]
        assert "render_js" not in sitio.config, "lo que ya da el cliente no se copia al perfil"
        assert sitio.config["max_depth"] == 200 and sitio.config["robots_mode"] == "ignore"
        progs = {p.id: p for p in s.query(Programacion)}
        for pid in ids:
            assert progs[pid].hereda_perfil and progs[pid].profile_id == sitio.id and progs[pid].seeds == []
        assert progs[ids[0]].config == {}, "la activa no tiene nada propio"
        assert progs[ids[1]].config == {"sitemap_urls": ["https://www.lopesan.com/s.xml"]}

        # La serie, unida: los tres censos son del perfil.
        assert s.query(Job).filter(Job.profile_id == sitio.id).count() == 3
        assert sitio.last_job_id is not None
        vivos = s.query(Incidente).filter(Incidente.estado != "cerrado").all()
        assert {(i.serie, i.clave) for i in vivos} == {(f"perfil:{sitio.id}", "rotas"),
                                                      (f"perfil:{sitio.id}", "salen")}
        cerrado = s.query(Incidente).filter(Incidente.estado == "cerrado").one()
        assert cerrado.motivo_cierre == "serie_unida" and cerrado.serie == f"perfil:{sitio.id}"

    despues = _efectiva_antes(Sesion, ids)
    for pid in ids:
        assert despues[pid]["seeds"] == antes[pid]["seeds"]
        assert despues[pid]["config"] == con_defaults(antes[pid]["config"]), (
            "el siguiente disparo rastrea lo mismo que antes de migrar")

    # Idempotente: una segunda pasada no encuentra nada.
    with Sesion() as s:
        assert M.migrar(s, confirmar=True) == []


def test_la_migracion_conserva_lo_que_el_worker_deduciria_distinto(entorno):
    """Una copia de modo lista con sitemap pedido y un UA de iPhone con huella
    de Chrome: colgada del perfil, el worker deduciria `use_sitemap: false` y
    otra huella si la capa solo guardara lo que difiere. La migracion las fija
    explicitas y el disparo sigue siendo el mismo."""
    M = _script()

    _, Sesion = entorno
    with Sesion() as s:
        c = Client(nombre="Lista", slug="lista-sitio", dominios=["lista.example"], config_defecto={})
        s.add(c)
        s.flush()
        cfg = {"max_depth": 0, "use_sitemap": True, "user_agent": _UA_IPHONE,
               "impersonate": "chrome124"}
        p = Programacion(id=uuid.uuid4(), nombre="lista", client_id="lista-sitio", cliente_id=c.id,
                         seeds=["https://lista.example/a", "https://lista.example/b"], config=cfg,
                         cron="0 3 * * 1", zona_horaria="Europe/Madrid", activa=True)
        s.add(p)
        s.commit()
        pid = p.id
    antes = _efectiva_antes(Sesion, [pid])
    with Sesion() as s:
        assert [l["accion"] for l in M.migrar(s, confirmar=True)] == ["colgada"]
    despues = _efectiva_antes(Sesion, [pid])
    assert despues[pid]["config"] == con_defaults(antes[pid]["config"])
    assert despues[pid]["config"]["use_sitemap"] is True


def test_si_la_comprobacion_falla_no_queda_un_perfil_huerfano(entorno, monkeypatch):
    M = _script()
    import shared.programaciones as P

    _, Sesion = entorno
    cid, ids = _lopesan(Sesion)
    real = P.config_del_momento
    # La comprobacion final (con lo propio ya calculado) nunca cuadra.
    monkeypatch.setattr(P, "config_del_momento",
                        lambda cli, per, propia: {**real(cli, per, propia), "_roto": propia is not None})
    with Sesion() as s:
        lineas = M.migrar(s, confirmar=True)
    assert {l["accion"] for l in lineas} == {"omitida"}
    with Sesion() as s:
        assert [p.nombre for p in s.query(CrawlProfile)] == ["lista"], "ningun «Sitio completo» huerfano"
        assert all(not p.hereda_perfil for p in s.query(Programacion))


def test_la_evolucion_enseña_una_sola_serie_tras_migrar(entorno):
    M = _script()
    from shared.evolucion import leer

    _, Sesion = entorno
    cid, ids = _lopesan(Sesion)
    with Sesion() as s:
        claves_antes = {x["clave"] for x in leer(s, cid)["series"]}
    with Sesion() as s:
        M.migrar(s, confirmar=True)
    with Sesion() as s:
        series = leer(s, cid)["series"]
    assert len(claves_antes) == 2, "antes, dos series iguales (V06)"
    assert len(series) == 1 and series[0]["clave"].startswith("perfil:")
    assert series[0]["nombre"] == "Sitio completo"


# --- Alta guiada (2.14) --------------------------------------------------------

def test_el_alta_guiada_crea_el_perfil_sitio_principal(entorno):
    api, _ = entorno
    r = api.post("/api/clients", json={"nombre": "Nuevo", "dominios": ["https://www.nuevo.es/blog"],
                                       "perfil_principal": True})
    assert r.status_code == 201, r.text
    assert r.json()["n_perfiles"] == 1
    [p] = api.get(f"/api/clients/{r.json()['id']}/profiles").json()
    assert p["nombre"] == "Sitio principal" and p["seeds"] == ["https://www.nuevo.es/"]


def test_sin_marcar_o_sin_dominio_no_crea_perfil(entorno):
    api, _ = entorno
    a = api.post("/api/clients", json={"nombre": "A", "dominios": ["a.es"]}).json()
    b = api.post("/api/clients", json={"nombre": "B", "perfil_principal": True}).json()
    assert a["n_perfiles"] == 0 and b["n_perfiles"] == 0
