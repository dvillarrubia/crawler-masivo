"""Acciones del servidor MCP (decision 151): lanzar, cancelar y programar en dos
pasos (sin `confirmar` no se escribe NADA) y la consulta libre, contra una API
de mentira con httpx.MockTransport."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")

from mcp_servidor import acciones as A  # noqa: E402
from mcp_servidor import herramientas as H  # noqa: E402

CID = "6ea9ae5b-426f-4e9b-8700-65a12edd8a53"
PID = "11111111-2222-3333-4444-555555555555"
JID = "391b5ed6-c366-4432-97f8-1de6da33bc0b"
PROG = "99999999-8888-7777-6666-555555555555"
HEREDADA = {"max_depth": 10, "max_urls": 50000, "render_js": False, "robots_mode": "respect",
            "crawl_behavior": {"max_runtime_hours": 72, "request_delay": 0.5}}


class ApiFalsa:
    def __init__(self, perfiles=None, estado_job="running", config_guardada=None):
        self.pedidas: list[tuple[str, str, dict | None]] = []
        self.perfiles = perfiles if perfiles is not None else [
            {"id": PID, "nombre": "ES", "rol": "propio", "activo": True, "seeds": ["https://www.lopesan.com/"]}]
        self.estado_job = estado_job
        self.config_guardada = config_guardada

    def responder(self, req: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(req.content) if req.content else None
        self.pedidas.append((req.method, req.url.path, cuerpo))
        p, q = req.url.path, dict(req.url.params)
        if p == "/api/clients":
            return httpx.Response(200, json=[{"id": CID, "slug": "lopesan", "nombre": "Lopesan"}])
        if p == f"/api/clients/{CID}/profiles":
            return httpx.Response(200, json=self.perfiles)
        if p == f"/api/clients/{CID}/config-resuelta":
            perfil = next((x for x in self.perfiles if x["id"] == q.get("profile_id")), None)
            return httpx.Response(200, json={"cliente_id": CID, "config": HEREDADA,
                                             "seeds": perfil["seeds"] if perfil else []})
        if p == "/api/almacenamiento/prevision":
            return httpx.Response(200, json={"veredicto": "justo", "frase": "Cabe, pero justo.",
                                             "motivo": None, "max_urls": q.get("max_urls")})
        if p == "/api/jobs" and req.method == "GET":
            items = [{"id": "otro", "name": "Saunier", "status": "running", "total_urls_crawled": 900}]
            return httpx.Response(200, json={"items": items if q.get("status") == "running" else []})
        if p == "/api/jobs" and req.method == "POST":
            cfg = self.config_guardada if self.config_guardada is not None else cuerpo["config"]
            return httpx.Response(201, json={"id": JID, "name": cuerpo["name"], "status": "pending", "config": cfg})
        if p == f"/api/jobs/{JID}":
            return httpx.Response(200, json={"id": JID, "name": "Lopesan", "status": self.estado_job,
                                             "total_urls_crawled": 120})
        if p == f"/api/jobs/{JID}/cancel":
            return httpx.Response(200, json={"id": JID, "name": "Lopesan", "status": "cancelled"})
        if p == "/api/programaciones" and req.method == "POST":
            return httpx.Response(201, json={"id": PROG, **cuerpo, "client_id": "lopesan"})
        if p == f"/api/programaciones/{PROG}":
            base = {"id": PROG, "nombre": "Lopesan", "cron": "0 6 * * 1", "activa": True}
            return httpx.Response(200, json={**base, **(cuerpo or {})})
        if p == "/openapi.json":
            return httpx.Response(200, json={"paths": {
                "/api/jobs/{job_id}/urls/detalle": {"get": {"summary": "Detalle de una URL",
                                                            "parameters": [{"name": "url", "in": "query"}]}},
                "/api/jobs/{job_id}/export/csv": {"get": {"summary": "CSV"}},
                "/api/jobs": {"get": {"summary": "Lista"}, "post": {"summary": "Crear"}},
                "/health": {"get": {"summary": "Salud"}}}})
        if p == "/api/grande":
            return httpx.Response(200, json={"x": "a" * (H.MAX_CARACTERES + 10)})
        return httpx.Response(404)

    def escrituras(self):
        return [(m, p, c) for m, p, c in self.pedidas if m != "GET"]


def _api(falsa: ApiFalsa) -> H.Api:
    return H.Api(base="http://api", token="t", transporte=httpx.MockTransport(falsa.responder),
                 publica="https://crawler.example")


def test_el_canario_es_el_de_lanzar_job():
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "lanzar_job.py"
    spec = importlib.util.spec_from_file_location("lanzar_job", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    esperado: dict = {}
    for k, v in mod.CANARY.items():
        A._poner(esperado, k, v)
    assert A.CANARIO == esperado


def test_lanzar_sin_confirmar_no_escribe_y_dice_que_hay_en_marcha():
    f = ApiFalsa()
    r = A.lanzar_rastreo(_api(f), "Lopesan", canario=True)
    assert r["simulacion"] and f.escrituras() == []
    assert r["nombre"] == "CANARY - Lopesan · ES" and r["tipo"] == "canario"
    assert r["configuracion"]["max_urls"] == 300 and r["configuracion"]["max_depth"] == 3
    assert r["limite_horas"] == 1
    assert r["semillas"] == ["https://www.lopesan.com/"]
    assert r["en_marcha_ahora"] == [{"id": "otro", "nombre": "Saunier", "estado": "running", "urls": 900}]


def test_lanzar_confirmado_manda_solo_lo_que_cambia():
    f = ApiFalsa()
    r = A.lanzar_rastreo(_api(f), "lopesan", ajustes={"render_js": True, "crawl_behavior.request_delay": 1},
                         confirmar=True)
    [(metodo, ruta, cuerpo)] = f.escrituras()
    assert (metodo, ruta) == ("POST", "/api/jobs")
    assert cuerpo["cliente_id"] == CID and cuerpo["profile_id"] == PID
    assert cuerpo["config"] == {"render_js": True, "crawl_behavior": {"request_delay": 1}}
    assert "seeds" not in cuerpo  # las del perfil, que resuelve la API
    assert r["lanzado"] and r["enlace"] == f"https://crawler.example/#/rastreos/{JID}"
    assert r["claves_descartadas"] == []


def test_avisa_de_la_clave_que_la_api_tiro():
    f = ApiFalsa(config_guardada={"render_js": True})
    r = A.lanzar_rastreo(_api(f), "lopesan", ajustes={"render_js": True, "renderizar": True}, confirmar=True)
    assert r["claves_descartadas"] == ["renderizar"]


def test_con_varios_perfiles_pide_cual():
    otro = {"id": "p2", "nombre": "EN", "rol": "propio", "activo": True, "seeds": ["https://www.lopesan.com/en"]}
    f = ApiFalsa(perfiles=ApiFalsa().perfiles + [otro])
    with pytest.raises(ValueError, match="varios perfiles.*ES.*EN"):
        A.lanzar_rastreo(_api(f), "lopesan")
    assert A.lanzar_rastreo(_api(f), "lopesan", perfil="en")["semillas"] == ["https://www.lopesan.com/en"]
    assert f.escrituras() == []


def test_sin_perfil_ni_semillas_no_lanza():
    f = ApiFalsa(perfiles=[])
    with pytest.raises(ValueError, match="Faltan semillas"):
        A.lanzar_rastreo(_api(f), "lopesan", confirmar=True)
    r = A.lanzar_rastreo(_api(f), "lopesan", semillas=["https://www.lopesan.com/"], confirmar=True)
    assert f.escrituras()[0][2]["seeds"] == ["https://www.lopesan.com/"] and r["lanzado"]


def test_cancelar_en_dos_pasos_y_solo_lo_que_corre():
    f = ApiFalsa()
    assert A.cancelar_rastreo(_api(f), JID)["simulacion"] and f.escrituras() == []
    assert A.cancelar_rastreo(_api(f), JID, confirmar=True)["estado"] == "cancelled"
    assert f.escrituras() == [("PATCH", f"/api/jobs/{JID}/cancel", None)]
    with pytest.raises(ValueError, match="completed"):
        A.cancelar_rastreo(_api(ApiFalsa(estado_job="completed")), JID, confirmar=True)


def test_programar_un_perfil_lo_cuelga_de_el_con_solo_sus_cambios():
    """Decision 193: con perfil y sin semillas propias, la programacion hereda
    en cada disparo; se manda solo lo que cambia, no una copia congelada."""
    f = ApiFalsa()
    r = A.programar_rastreo(_api(f), "lopesan", "0 6 * * 1")
    assert r["simulacion"] and f.escrituras() == []
    r = A.programar_rastreo(_api(f), "lopesan", "0 6 * * 1", ajustes={"max_urls": 1000}, confirmar=True)
    [(_, ruta, cuerpo)] = f.escrituras()
    assert ruta == "/api/programaciones" and cuerpo["cron"] == "0 6 * * 1"
    assert cuerpo["profile_id"] == PID and cuerpo["config_propia"] == {"max_urls": 1000}
    assert "seeds" not in cuerpo and "config" not in cuerpo
    assert r["programado"] and r["cron"] == "0 6 * * 1"


def test_programar_con_semillas_propias_guarda_la_configuracion_entera():
    f = ApiFalsa()
    A.programar_rastreo(_api(f), "lopesan", "0 6 * * 1", ajustes={"max_urls": 1000},
                        semillas=["https://www.lopesan.com/es/"], confirmar=True)
    [(_, ruta, cuerpo)] = f.escrituras()
    assert cuerpo["config"]["max_urls"] == 1000 and cuerpo["config"]["crawl_behavior"]["request_delay"] == 0.5
    assert cuerpo["seeds"] == ["https://www.lopesan.com/es/"] and cuerpo["profile_id"] == PID


def test_pausar_una_programacion():
    f = ApiFalsa()
    with pytest.raises(ValueError, match="nada que cambiar"):
        A.editar_programacion(_api(f), PROG)
    assert A.editar_programacion(_api(f), PROG, activa=False)["simulacion"] and f.escrituras() == []
    r = A.editar_programacion(_api(f), PROG, activa=False, confirmar=True)
    assert f.escrituras() == [("PATCH", f"/api/programaciones/{PROG}", {"activa": False})]
    assert r["activa"] is False


def test_consultar_lee_cualquier_get_pero_no_descargas():
    f = ApiFalsa()
    api = _api(f)
    assert H.consultar(api, "/api/clients")[0]["slug"] == "lopesan"
    with pytest.raises(ValueError, match="descarga"):
        H.consultar(api, f"/jobs/{JID}/export/csv")
    with pytest.raises(ValueError, match="parametros"):
        H.consultar(api, "/jobs?status=running")
    with pytest.raises(ValueError):
        H.consultar(api, "/jobs/../../health")
    assert H.consultar(api, "/grande")["truncado"]
    assert f.escrituras() == []


def test_endpoints_lista_los_get_de_la_api():
    rutas = [e["ruta"] for e in H.endpoints(_api(ApiFalsa()))]
    assert rutas == ["/jobs/{job_id}/urls/detalle", "/jobs"]
    assert H.endpoints(_api(ApiFalsa()), "detalle")[0]["parametros"] == ["url"]


def test_la_simulacion_dice_si_cabe_en_el_disco():
    f = ApiFalsa()
    r = A.lanzar_rastreo(_api(f), "Lopesan", canario=True)
    assert r["disco"] == {"veredicto": "justo", "frase": "Cabe, pero justo.", "motivo": None}
    pedida = [x for x in f.pedidas if x[1] == "/api/almacenamiento/prevision"]
    assert pedida and f.escrituras() == []
