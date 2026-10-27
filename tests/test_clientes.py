"""Clientes como entidad (#36, F1), EJECUTADO contra la API y SQLite.

Lo que de verdad hay que sujetar aqui:

- que **ningun endpoint devuelva el valor de una credencial** —la API no tiene
  autenticacion—, comprobado con la variable puesta y buscando su valor en
  cada respuesta;
- que la herencia cliente -> perfil -> job se resuelva al crear el job y
  quede copiada en `jobs.config`, con `None` borrando lo heredado;
- que los `client_id` de texto de siempre acaben colgados de un cliente sin
  fusionar erratas en silencio;
- que borrar no rompa nada: ni el perfil al borrar un job, ni los rastreos al
  borrar un cliente;
- que el backup lleve el cliente y que al importarlo se case por slug.

Se llama a los endpoints de verdad (decision 74: leer el fuente comprueba que
escribiste algo, no que funcione).
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.clientes import (  # noqa: E402
    fusionar_config, migrar_client_id_texto, slug_de_cliente, slugificar)
from shared.credenciales import (  # noqa: E402
    CredencialNoDisponible, credencial, error_en_variable, esta_configurada,
)
from shared.database import get_session  # noqa: E402
from shared.models import Base, Client, CrawlProfile, Job, Programacion, Url  # noqa: E402

SECRETO = "s3cr3t0-que-no-puede-salir-NUNCA-1234"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


class RedisFalso:
    def __init__(self):
        self.cola = []

    def rpush(self, _clave, valor):
        self.cola.append(valor)

    def set(self, *a, **k):
        pass

    def delete(self, *a, **k):
        pass


def _app(Sesion, monkeypatch):
    from api.routers import clientes, jobs, programaciones

    app = FastAPI()
    app.include_router(jobs.router)
    app.include_router(clientes.router)
    app.include_router(programaciones.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    redis = RedisFalso()
    monkeypatch.setattr(jobs, "get_redis", lambda: redis)
    return app


def _motor(tmp_path, nombre="t.db"):
    engine = create_engine(f"sqlite:///{tmp_path}/{nombre}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    return TestClient(_app(Sesion, monkeypatch)), Sesion


def _cliente(api, **extra):
    cuerpo = {"nombre": "Ácme Hoteles", "dominios": ["https://WWW.acme.com/es/"]}
    cuerpo.update(extra)
    r = api.post("/api/clients", json=cuerpo)
    assert r.status_code == 201, r.text
    return r.json()


# --- CRUD -------------------------------------------------------------------

def test_alta_de_cliente_con_slug_y_dominios_normalizados(entorno):
    api, _ = entorno
    c = _cliente(api, notas="cuenta grande")
    assert c["slug"] == "acme-hoteles"
    assert c["dominios"] == ["www.acme.com"]
    assert c["n_jobs"] == 0 and c["n_perfiles"] == 0
    assert api.get(f"/api/clients/{c['id']}").json()["notas"] == "cuenta grande"
    assert [x["slug"] for x in api.get("/api/clients").json()] == ["acme-hoteles"]


def test_dos_clientes_con_el_mismo_slug_son_una_errata(entorno):
    api, _ = entorno
    _cliente(api)
    r = api.post("/api/clients", json={"nombre": "acme hoteles"})
    assert r.status_code == 409


def test_editar_cliente(entorno):
    api, _ = entorno
    c = _cliente(api)
    r = api.patch(f"/api/clients/{c['id']}",
                  json={"nombre": "Acme", "dominios": ["acme.com", "blog.acme.com"]})
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "Acme"
    assert r.json()["slug"] == "acme-hoteles", "el slug no cambia si no se pide"
    assert r.json()["dominios"] == ["acme.com", "blog.acme.com"]


def test_un_campo_que_no_existe_es_un_error_no_un_silencio(entorno):
    """Decision 33: Pydantic tira lo no declarado. En el alta de un cliente,
    una clave mal escrita tiene que decirse."""
    api, _ = entorno
    r = api.post("/api/clients", json={"nombre": "X", "dominio": "x.com"})
    assert r.status_code == 422


def test_perfiles_crud_y_nombre_unico_por_cliente(entorno):
    api, _ = entorno
    c = _cliente(api)
    base = f"/api/clients/{c['id']}/profiles"
    r = api.post(base, json={"nombre": "Sitio principal", "seeds": ["https://www.acme.com/"]})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["rol"] == "propio" and p["cliente_id"] == c["id"]
    assert api.post(base, json={"nombre": "Sitio principal",
                                "seeds": ["https://x.com/"]}).status_code == 409
    r = api.post(base, json={"nombre": "Competidor", "rol": "competidor",
                             "seeds": ["https://rival.com/"]})
    assert r.status_code == 201
    assert len(api.get(base).json()) == 2
    r = api.patch(f"{base}/{p['id']}", json={"seeds": ["https://www.acme.com/es/"]})
    assert r.json()["seeds"] == ["https://www.acme.com/es/"]
    assert api.delete(f"{base}/{p['id']}").status_code == 204
    assert len(api.get(base).json()) == 1
    assert api.get(f"/api/clients/{c['id']}").json()["n_perfiles"] == 1


def test_un_perfil_sin_semillas_no_se_crea(entorno):
    api, _ = entorno
    c = _cliente(api)
    r = api.post(f"/api/clients/{c['id']}/profiles", json={"nombre": "x", "seeds": []})
    assert r.status_code == 422


# --- Configuracion parcial --------------------------------------------------

def test_la_config_del_cliente_guarda_solo_lo_que_fija(entorno):
    """Guardar el JobConfig entero «fijaria» todos los defaults y pisaria al
    perfil."""
    api, _ = entorno
    c = _cliente(api, config_defecto={"render_js": True,
                                       "crawl_behavior": {"download_timeout": 50}})
    assert c["config_defecto"] == {"render_js": True,
                                   "crawl_behavior": {"download_timeout": 50}}


def test_una_clave_de_config_desconocida_se_nombra(entorno):
    api, _ = entorno
    r = api.post("/api/clients", json={
        "nombre": "X", "config_defecto": {"use_sitemaps": False,
                                          "crawl_behavior": {"retry": 3}}})
    assert r.status_code == 422
    assert "use_sitemaps" in r.text and "crawl_behavior.retry" in r.text


def test_un_valor_invalido_en_la_config_del_cliente_es_422(entorno):
    api, _ = entorno
    # -1 y no 0: el 0 es el modo lista (decision 128)
    r = api.post("/api/clients", json={"nombre": "X", "config_defecto": {"max_depth": -1}})
    assert r.status_code == 422


def test_la_contrasena_http_no_se_guarda_en_un_cliente(entorno):
    api, _ = entorno
    r = api.post("/api/clients", json={
        "nombre": "X", "config_defecto": {"http": {"basic_auth_password": "hunter2"}}})
    assert r.status_code == 422
    # El 422 de FastAPI repite lo que se envio; lo que importa es que no se
    # haya guardado.
    assert api.get("/api/clients").json() == []


# --- Herencia ---------------------------------------------------------------

def test_herencia_cliente_perfil_job_se_copia_en_el_job(entorno):
    api, Sesion = entorno
    c = _cliente(api, config_defecto={
        "render_js": True, "max_urls": 1000,
        "crawl_behavior": {"download_timeout": 50, "retry_count": 1},
        "exclude_patterns": ["/carrito/"],
    })
    p = api.post(f"/api/clients/{c['id']}/profiles", json={
        "nombre": "Blog", "seeds": ["https://blog.acme.com/"],
        "config": {"crawl_behavior": {"retry_count": 5}, "max_urls": None,
                   "exclude_patterns": ["/tag/"]},
    }).json()

    r = api.post("/api/jobs", json={
        "name": "blog", "profile_id": p["id"], "config": {"max_depth": 7}})
    assert r.status_code == 201, r.text
    job = r.json()
    cfg = job["config"]
    assert job["cliente_id"] == c["id"] and job["profile_id"] == p["id"]
    assert job["cliente_nombre"] == "Ácme Hoteles"
    assert job["client_id"] == "acme-hoteles", "el texto queda como etiqueta"
    assert job["seeds"] == ["https://blog.acme.com/"], "las semillas del perfil"
    assert cfg["render_js"] is True                        # del cliente
    assert cfg["crawl_behavior"]["download_timeout"] == 50  # del cliente
    assert cfg["crawl_behavior"]["retry_count"] == 5        # el perfil gana
    assert cfg["max_urls"] is None, "None en el perfil quita el tope del cliente"
    assert cfg["exclude_patterns"] == ["/tag/"], "las listas se sustituyen"
    assert cfg["max_depth"] == 7                            # el job gana
    assert cfg["use_sitemap"] is True                       # default intacto
    # El job es autocontenido: el crawler no sabe que existe el cliente.
    assert set(cfg) >= {"max_depth", "crawl_behavior", "analysis_thresholds"}

    s = Sesion()
    assert s.get(CrawlProfile, uuid.UUID(p["id"])).last_job_id == uuid.UUID(job["id"])
    s.close()


def test_editar_el_cliente_no_cambia_un_job_ya_creado(entorno):
    api, _ = entorno
    c = _cliente(api, config_defecto={"render_js": True})
    job = api.post("/api/jobs", json={"name": "j", "cliente_id": c["id"],
                                      "seeds": ["https://www.acme.com/"]}).json()
    api.patch(f"/api/clients/{c['id']}", json={"config_defecto": {"render_js": False}})
    assert api.get(f"/api/jobs/{job['id']}").json()["config"]["render_js"] is True


def test_sin_cliente_el_job_es_el_de_siempre(entorno):
    api, _ = entorno
    r = api.post("/api/jobs", json={"name": "j", "seeds": ["https://x.com/"],
                                    "config": {"max_depth": 4}})
    assert r.status_code == 201
    j = r.json()
    assert j["cliente_id"] is None and j["client_id"] is None
    assert j["config"]["max_depth"] == 4 and j["config"]["render_js"] is False


def test_sin_semillas_ni_perfil_es_422(entorno):
    api, _ = entorno
    assert api.post("/api/jobs", json={"name": "j", "seeds": []}).status_code == 422


def test_un_perfil_de_otro_cliente_es_422(entorno):
    api, _ = entorno
    a = _cliente(api)
    b = _cliente(api, nombre="Otro")
    p = api.post(f"/api/clients/{b['id']}/profiles",
                 json={"nombre": "x", "seeds": ["https://otro.com/"]}).json()
    r = api.post("/api/jobs", json={"name": "j", "cliente_id": a["id"], "profile_id": p["id"]})
    assert r.status_code == 422


def test_config_resuelta_para_precargar_el_modal(entorno):
    api, _ = entorno
    c = _cliente(api, config_defecto={"max_depth": 9})
    p = api.post(f"/api/clients/{c['id']}/profiles", json={
        "nombre": "x", "seeds": ["https://www.acme.com/"], "config": {"render_js": True}}).json()
    r = api.get(f"/api/clients/{c['id']}/config-resuelta", params={"profile_id": p["id"]})
    assert r.status_code == 200
    assert r.json()["seeds"] == ["https://www.acme.com/"]
    assert r.json()["config"]["max_depth"] == 9 and r.json()["config"]["render_js"] is True


def test_fusion_profunda_none_borra_y_no_muta():
    cliente = {"a": 1, "cb": {"x": 1, "y": 2}, "l": [1, 2]}
    perfil = {"cb": {"y": None, "z": 3}, "l": [9]}
    job = {"a": None}
    assert fusionar_config(cliente, perfil, job) == {"cb": {"x": 1, "z": 3}, "l": [9]}
    assert cliente == {"a": 1, "cb": {"x": 1, "y": 2}, "l": [1, 2]}, "no muta las capas"


# --- Compatibilidad con el client_id de texto -------------------------------

def test_un_client_id_de_texto_crea_o_reutiliza_el_cliente(entorno):
    """El camino de `lanzar_job.py`: sin esto cada job quedaria sin cliente
    hasta la siguiente migracion."""
    api, _ = entorno
    a = api.post("/api/jobs", json={"name": "a", "client_id": "Progym",
                                    "seeds": ["https://progym.es/"]}).json()
    b = api.post("/api/jobs", json={"name": "b", "client_id": "progym",
                                    "seeds": ["https://progym.es/"]}).json()
    assert a["cliente_id"] and a["cliente_id"] == b["cliente_id"]
    assert [c["slug"] for c in api.get("/api/clients").json()] == ["progym"]


def test_filtrar_jobs_por_uuid_slug_o_texto_viejo(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    api.post("/api/jobs", json={"name": "nuevo", "cliente_id": c["id"],
                                "seeds": ["https://www.acme.com/"]})
    # Un job anterior a la migracion: solo el texto, sin FK.
    s = Sesion()
    s.add(Job(name="viejo", client_id="acme-hoteles", status="completed",
              seeds=["https://www.acme.com/"], config={}))
    s.add(Job(name="ajeno", client_id="otro", status="completed",
              seeds=["https://otro.com/"], config={}))
    s.commit()
    s.close()

    def nombres(valor):
        return sorted(j["name"] for j in api.get(
            "/api/jobs", params={"client_id": valor}).json()["items"])

    assert nombres(c["id"]) == ["nuevo"]
    assert nombres("acme-hoteles") == ["nuevo", "viejo"]
    assert nombres("otro") == ["ajeno"]


# --- Migracion --------------------------------------------------------------

def test_migracion_de_client_id_de_texto_idempotente(tmp_path):
    Sesion = _motor(tmp_path)
    s = Sesion()
    for texto in ("Acme", "acme ", "Ácme", "otro-cliente", None, "  "):
        s.add(Job(name="j", client_id=texto, status="completed",
                  seeds=["https://x.com/"], config={}))
    s.add(Programacion(nombre="p", client_id="Acme", seeds=["https://x.com/"],
                       config={}, cron="0 3 * * 1"))
    s.commit()

    r = migrar_client_id_texto(s)
    s.commit()
    assert r["creados"] == 2
    assert r["jobs"] == 4 and r["programaciones"] == 1
    assert r["colisiones"] == {"acme": ["Acme", "acme ", "Ácme"]}, \
        "las variantes se imprimen para revisarlas, no se fusionan a ciegas"
    acme = s.query(Client).filter(Client.slug == "acme").one()
    assert acme.nombre == "Ácme"
    assert s.query(Job).filter(Job.cliente_id == acme.id).count() == 3
    assert s.query(Job).filter(Job.cliente_id.is_(None)).count() == 2, \
        "sin texto no hay cliente"
    # Los textos originales se conservan: es la pista si algo sale mal.
    assert {j.client_id for j in s.query(Job).filter(Job.cliente_id == acme.id)} == \
        {"Acme", "acme ", "Ácme"}

    otra = migrar_client_id_texto(s)
    s.commit()
    assert otra == {"creados": 0, "jobs": 0, "programaciones": 0, "colisiones": {}}
    assert s.query(Client).count() == 2
    s.close()


def test_la_migracion_reutiliza_un_cliente_ya_dado_de_alta(tmp_path):
    Sesion = _motor(tmp_path)
    s = Sesion()
    s.add(Client(nombre="Progym", slug="progym", dominios=[], config_defecto={},
                 integraciones=[]))
    s.add(Job(name="j", client_id="PROGYM", status="completed",
              seeds=["https://x.com/"], config={}))
    s.commit()
    assert migrar_client_id_texto(s)["creados"] == 0
    s.commit()
    assert s.query(Job).one().cliente_id == s.query(Client).one().id
    s.close()


def test_los_canarios_son_del_cliente_y_no_otro_cliente(tmp_path):
    """Lo que habia en produccion el 2026-10-13: `lopesan`, `lopesan-canary`,
    `lopesan-canario` y `lopesan-prueba` son UN cliente, no cuatro."""
    Sesion = _motor(tmp_path)
    s = Sesion()
    for texto in ("lopesan", "lopesan-canary", "lopesan-canario", "lopesan-prueba",
                  "saunierduval-canary"):
        s.add(Job(name="j", client_id=texto, status="completed",
                  seeds=["https://x.com/"], config={}))
    s.commit()
    r = migrar_client_id_texto(s)
    s.commit()
    assert sorted(c.slug for c in s.query(Client)) == ["lopesan", "saunierduval"]
    assert r["colisiones"] == {}, "plegar un canario no es una errata"
    # Sin ningun job «normal», el nombre sale del slug, no de «-canary».
    assert s.query(Client).filter(Client.slug == "saunierduval").one().nombre == "saunierduval"
    assert s.query(Job).filter(Job.cliente_id.is_(None)).count() == 0
    s.close()


def test_slug_de_cliente():
    assert slug_de_cliente("Lopesan-Canary") == "lopesan"
    assert slug_de_cliente("progym-canario") == "progym"
    # Solo se pliega si queda algo delante.
    assert slug_de_cliente("test") == "test"
    assert slug_de_cliente("testing-labs") == "testing-labs"


def test_slugificar():
    assert slugificar("  Ácme Hoteles  ") == "acme-hoteles"
    assert slugificar("Saunier Duval / ES") == "saunier-duval-es"
    assert slugificar("niño") == "nino"
    assert slugificar("¿?") == ""


# --- Integraciones y secretos -----------------------------------------------

def _con_integracion(api, **integ):
    base = {"tipo": "dataforseo", "credencial_env": "DATAFORSEO_LIN3S",
            "parametros": {"location_code": 2724, "language_code": "es"}}
    base.update(integ)
    return api.post("/api/clients", json={"nombre": "Lin3s", "integraciones": [base]})


@pytest.mark.parametrize("nombre", [
    "dataforseo_lin3s",          # minusculas
    "DATAFORSEO LIN3S",          # espacio
    "1DATAFORSEO",               # empieza por digito
    "DATAFORSEO-LIN3S",          # guion
    "POSTGRES_PASSWORD",         # fuera de su espacio de nombres
    "DATAFORSEO_",               # solo el prefijo
    "AHREFS_LIN3S",              # prefijo de otra integracion
])
def test_el_nombre_de_la_variable_se_valida(entorno, nombre):
    api, _ = entorno
    r = _con_integracion(api, credencial_env=nombre)
    assert r.status_code == 422, (nombre, r.text)


def test_un_parametro_con_pinta_de_secreto_se_rechaza(entorno):
    api, _ = entorno
    r = _con_integracion(api, parametros={"api_key": SECRETO})
    assert r.status_code == 422
    r = _con_integracion(api, parametros={"password": "x"})
    assert r.status_code == 422


def test_ningun_endpoint_devuelve_el_valor_de_la_credencial(entorno, monkeypatch):
    """La variable PUESTA, y se busca su valor en cada respuesta. Si algun
    dia alguien devuelve el entorno «para depurar», esto lo canta."""
    api, _ = entorno
    monkeypatch.setenv("DATAFORSEO_LIN3S", SECRETO)
    r = _con_integracion(api)
    assert r.status_code == 201, r.text
    c = r.json()
    respuestas = [r]
    integ = c["integraciones"][0]
    assert integ["configurada"] is True
    assert integ["credencial_env"] == "DATAFORSEO_LIN3S"
    assert "valor" not in integ and "secreto" not in integ

    p = api.post(f"/api/clients/{c['id']}/profiles",
                 json={"nombre": "web", "seeds": ["https://lin3s.com/"]})
    respuestas.append(p)
    j = api.post("/api/jobs", json={"name": "j", "profile_id": p.json()["id"]})
    respuestas.append(j)
    respuestas += [
        api.get("/api/clients"),
        api.get(f"/api/clients/{c['id']}"),
        api.patch(f"/api/clients/{c['id']}", json={"notas": "x"}),
        api.get(f"/api/clients/{c['id']}/profiles"),
        api.get(f"/api/clients/{c['id']}/config-resuelta"),
        api.get("/api/jobs"),
        api.get(f"/api/jobs/{j.json()['id']}"),
        api.get("/openapi.json"),
    ]
    for resp in respuestas:
        assert resp.status_code < 400, resp.text
        assert SECRETO not in resp.text, resp.request.url


def test_configurada_es_false_si_la_variable_no_esta(entorno, monkeypatch):
    api, _ = entorno
    monkeypatch.delenv("DATAFORSEO_LIN3S", raising=False)
    c = _con_integracion(api).json()
    assert c["integraciones"][0]["configurada"] is False


def test_credencial_lee_del_entorno_y_nunca_fuera_de_su_prefijo():
    integ = {"tipo": "dataforseo", "credencial_env": "DATAFORSEO_LIN3S"}
    assert credencial(integ, {"DATAFORSEO_LIN3S": "login:pass"}) == "login:pass"
    assert esta_configurada(integ, {"DATAFORSEO_LIN3S": "x"})
    assert not esta_configurada(integ, {"DATAFORSEO_LIN3S": ""})
    with pytest.raises(CredencialNoDisponible) as exc:
        credencial(integ, {})
    assert "DATAFORSEO_LIN3S" in str(exc.value)
    # Editada a mano en la BD para leer otra cosa: no se lee.
    malo = {"tipo": "dataforseo", "credencial_env": "POSTGRES_PASSWORD"}
    entorno = {"POSTGRES_PASSWORD": SECRETO}
    assert not esta_configurada(malo, entorno)
    with pytest.raises(CredencialNoDisponible) as exc:
        credencial(malo, entorno)
    assert SECRETO not in str(exc.value)
    assert error_en_variable("gsc", "GSC_LIN3S") is None


# --- Borrado ----------------------------------------------------------------

def test_borrar_un_job_no_rompe_el_perfil(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    p = api.post(f"/api/clients/{c['id']}/profiles",
                 json={"nombre": "w", "seeds": ["https://www.acme.com/"]}).json()
    j = api.post("/api/jobs", json={"name": "j", "profile_id": p["id"]}).json()
    assert api.get(f"/api/clients/{c['id']}/profiles/{p['id']}").json()["last_job_id"] == j["id"]
    assert api.delete(f"/api/jobs/{j['id']}").status_code == 204
    perfil = api.get(f"/api/clients/{c['id']}/profiles/{p['id']}")
    assert perfil.status_code == 200 and perfil.json()["last_job_id"] is None


def test_borrar_un_cliente_exige_confirmar_y_no_borra_sus_rastreos(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    p = api.post(f"/api/clients/{c['id']}/profiles",
                 json={"nombre": "w", "seeds": ["https://www.acme.com/"]}).json()
    j = api.post("/api/jobs", json={"name": "j", "profile_id": p["id"]}).json()

    assert api.delete(f"/api/clients/{c['id']}", params={"confirmar": "otro"}).status_code == 409
    assert api.delete(f"/api/clients/{c['id']}").status_code == 422
    assert api.delete(f"/api/clients/{c['id']}",
                      params={"confirmar": "acme-hoteles"}).status_code == 204

    assert api.get(f"/api/clients/{c['id']}").status_code == 404
    job = api.get(f"/api/jobs/{j['id']}").json()
    assert job["cliente_id"] is None and job["profile_id"] is None
    assert job["client_id"] == "acme-hoteles", "el texto se queda como pista"
    s = Sesion()
    assert s.query(CrawlProfile).count() == 0
    s.close()


def test_borrar_un_cliente_con_sus_rastreos_si_se_pide(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    j = api.post("/api/jobs", json={"name": "j", "cliente_id": c["id"],
                                    "seeds": ["https://www.acme.com/"]}).json()
    s = Sesion()
    s.add(Url(job_id=uuid.UUID(j["id"]), url="https://www.acme.com/", url_hash="h"))
    s.commit()
    s.close()
    r = api.delete(f"/api/clients/{c['id']}",
                   params={"confirmar": "acme-hoteles", "con_jobs": True})
    assert r.status_code == 204
    assert api.get(f"/api/jobs/{j['id']}").status_code == 404


# --- Programaciones ---------------------------------------------------------

def test_una_programacion_queda_colgada_de_su_cliente(entorno):
    api, _ = entorno
    c = _cliente(api)
    r = api.post("/api/programaciones", json={
        "nombre": "semanal", "cliente_id": c["id"], "seeds": ["https://www.acme.com/"],
        "cron": "0 3 * * 1"})
    assert r.status_code == 201, r.text
    assert r.json()["cliente_id"] == c["id"]
    assert r.json()["client_id"] == "acme-hoteles"


# --- Backup -----------------------------------------------------------------

def _backup(Sesion, monkeypatch, job_id) -> bytes:
    from api import backup

    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    return b"".join(backup.stream_backup_zip(uuid.UUID(job_id)))


def test_backup_ida_y_vuelta_casa_el_cliente_por_slug(entorno, tmp_path, monkeypatch):
    from api.backup import import_backup_zip

    api, Sesion = entorno
    monkeypatch.setenv("DATAFORSEO_LIN3S", SECRETO)
    c = _con_integracion(api).json()
    p = api.post(f"/api/clients/{c['id']}/profiles",
                 json={"nombre": "web", "seeds": ["https://lin3s.com/"]}).json()
    j = api.post("/api/jobs", json={"name": "j", "profile_id": p["id"]}).json()

    datos = _backup(Sesion, monkeypatch, j["id"])
    assert SECRETO.encode() not in datos
    with zipfile.ZipFile(io.BytesIO(datos)) as zf:
        import json
        assert json.loads(zf.read("manifest.json"))["format_version"] == "2"
        assert json.loads(zf.read("client.json"))["slug"] == "lin3s"
        assert json.loads(zf.read("profile.json"))["nombre"] == "web"

    # Otra instalacion, vacia: el cliente se crea; el perfil NO (se avisa).
    Otra = _motor(tmp_path, "otra.db")
    s = Otra()
    r = import_backup_zip(io.BytesIO(datos), False, s)
    job = s.get(Job, r["new_job_id"])
    cliente = s.get(Client, job.cliente_id)
    assert cliente.slug == "lin3s"
    assert cliente.integraciones[0]["credencial_env"] == "DATAFORSEO_LIN3S"
    assert job.profile_id is None
    assert any("perfil" in w for w in r["warnings"])
    # Un segundo import se casa con el mismo cliente, no crea otro.
    import_backup_zip(io.BytesIO(datos), False, s)
    assert s.query(Client).count() == 1
    s.close()

    # La misma instalacion: se casa con el cliente Y el perfil que ya hay.
    s = Sesion()
    r = import_backup_zip(io.BytesIO(datos), False, s)
    job = s.get(Job, r["new_job_id"])
    assert job.cliente_id == uuid.UUID(c["id"]) and job.profile_id == uuid.UUID(p["id"])
    assert s.query(Client).count() == 1
    s.close()


def test_un_backup_de_formato_1_resuelve_el_cliente_por_su_texto(tmp_path):
    """Los backups ya entregados solo traen `client_id` de texto: el job
    importado no puede quedar sin cliente si lo tenia."""
    import json

    from api.backup import import_backup_zip

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(
            {"format_version": "1", "job_id": str(uuid.uuid4())}))
        zf.writestr("job.json", json.dumps(
            {"name": "viejo", "client_id": "Progym", "seeds": ["https://progym.es/"],
             "config": {}, "status": "completed"}))
    Sesion = _motor(tmp_path)
    s = Sesion()
    r = import_backup_zip(io.BytesIO(buf.getvalue()), False, s)
    job = s.get(Job, r["new_job_id"])
    assert s.get(Client, job.cliente_id).slug == "progym"
    s.close()


def test_el_tipo_se_elige_al_lanzar(entorno):
    """Decision 203: el modal manda el tipo y queda marcado; sin mandarlo,
    como antes (se deduce del nombre: los scripts no lo mandan)."""
    api, _ = entorno
    r = api.post("/api/jobs", json={"name": "acme a mano", "seeds": ["https://x.com/"], "tipo": "prueba"})
    assert r.status_code == 201
    assert r.json()["tipo"] == "prueba" and r.json()["tipo_efectivo"] == "prueba"
    r = api.post("/api/jobs", json={"name": "acme a mano", "seeds": ["https://x.com/"]})
    assert r.json()["tipo"] is None and r.json()["tipo_efectivo"] == "censo"
    assert api.post("/api/jobs", json={"name": "j", "seeds": ["https://x.com/"],
                                       "tipo": "lista"}).status_code == 422
