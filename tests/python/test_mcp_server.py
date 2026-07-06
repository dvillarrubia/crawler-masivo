"""
Verbos de negocio del servidor MCP (B1).

Se prueba la LÓGICA de los verbos (mcp_server/verbs.py) mockeando la capa
HTTP — no hace falta ni el SDK `mcp` ni la API corriendo.
"""

from __future__ import annotations

import pytest

verbs = pytest.importorskip("mcp_server.verbs")


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    # Por seguridad: que ningún test llame a la red de verdad.
    def _boom(*a, **k):
        raise AssertionError("HTTP real no mockeado en el test")
    monkeypatch.setattr(verbs, "_get", _boom)
    monkeypatch.setattr(verbs, "_post", _boom)
    monkeypatch.setattr(verbs, "_patch", _boom)


def _fake_get(monkeypatch, routes):
    """routes: dict path→respuesta (o callable(path, params)->resp)."""
    def _g(path, params=None):
        v = routes.get(path)
        return v(path, params) if callable(v) else v
    monkeypatch.setattr(verbs, "_get", _g)


def test_listar_proyectos_agrupa(monkeypatch):
    _fake_get(monkeypatch, {"/api/jobs": {"items": [
        {"client_id": "a"}, {"client_id": "a"}, {"client_id": "b"},
        {"client_id": None}]}})
    out = verbs.listar_proyectos()
    d = {p["proyecto"]: p["rastreos"] for p in out["proyectos"]}
    assert d["a"] == 2 and d["b"] == 1 and d["(sin proyecto)"] == 1


def test_listar_rastreos_mapea(monkeypatch):
    _fake_get(monkeypatch, {"/api/jobs": {"items": [
        {"id": "j1", "name": "Uno", "client_id": "a", "status": "completed",
         "created_at": "2026-01-01", "total_urls_crawled": 10, "total_urls_failed": 1}]}})
    out = verbs.listar_rastreos(proyecto="a")
    r = out["rastreos"][0]
    assert r["job_id"] == "j1" and r["estado"] == "completed" and r["urls_rastreadas"] == 10


def test_lanzar_rastreo_valida_esquema():
    out = verbs.lanzar_rastreo("ejemplo.com")   # sin http://
    assert "error" in out and "http" in out["error"].lower()


def test_lanzar_rastreo_construye_body(monkeypatch):
    captured = {}
    def _p(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"id": "new-job", "name": body["name"], "status": "pending"}
    monkeypatch.setattr(verbs, "_post", _p)
    out = verbs.lanzar_rastreo("https://ejemplo.com/", proyecto="cli",
                               max_urls=100, render_js=True)
    assert out["job_id"] == "new-job"
    assert captured["path"] == "/api/jobs"
    assert captured["body"]["seeds"] == ["https://ejemplo.com/"]
    assert captured["body"]["client_id"] == "cli"
    assert captured["body"]["config"]["max_urls"] == 100
    assert captured["body"]["config"]["render_js"] is True
    assert "ejemplo.com" in captured["body"]["name"]   # nombre por dominio


def test_estado_rastreo_combina_progreso(monkeypatch):
    _fake_get(monkeypatch, {
        "/api/jobs/j1": {"name": "N", "status": "running",
                         "total_urls_crawled": 5, "total_urls_failed": 0},
        "/api/jobs/j1/progress": {"crawled_count": 5, "in_queue": 12}})
    out = verbs.estado_rastreo("j1")
    assert out["estado"] == "running" and out["progreso_vivo"]["in_queue"] == 12


def test_resumen_rastreo_agrega_severidad(monkeypatch):
    _fake_get(monkeypatch, {"/api/jobs/j1/stats": {
        "total_urls": 100, "internal_count": 90, "external_count": 10,
        "urls_by_status_group": {"2xx": 95, "4xx": 5},
        "top_hosts": [{"host": "x", "count": 90}],
        "issues_by_type": [
            {"issue_type": "a", "severity": "error", "count": 3},
            {"issue_type": "b", "severity": "warning", "count": 7},
            {"issue_type": "c", "severity": "error", "count": 2}],
        "latency": {"p50": 100}}})
    out = verbs.resumen_rastreo("j1")
    assert out["incidencias_por_severidad"]["error"] == 5
    assert out["incidencias_por_severidad"]["warning"] == 7
    assert out["tipos_de_incidencia_distintos"] == 3


def test_top_incidencias_ordena_y_etiqueta(monkeypatch):
    _fake_get(monkeypatch, {"/api/jobs/j1/stats": {"issues_by_type": [
        {"issue_type": "title_missing", "severity": "warning", "count": 2},
        {"issue_type": "4xx_error", "severity": "error", "count": 40},
        {"issue_type": "raro_sin_label", "severity": "info", "count": 5}]}})
    out = verbs.top_incidencias("j1", limite=2)
    assert len(out["incidencias"]) == 2
    assert out["incidencias"][0]["tipo"] == "4xx_error"        # más volumen primero
    assert out["incidencias"][0]["nombre"] == "Errores 4xx"    # etiqueta ES
    # el que no tiene etiqueta cae fuera del top-2 pero probamos el fallback:
    out3 = verbs.top_incidencias("j1", limite=3)
    raro = next(i for i in out3["incidencias"] if i["tipo"] == "raro_sin_label")
    assert raro["nombre"] == "raro_sin_label"                  # fallback al técnico


def test_buscar_urls_mapea(monkeypatch):
    _fake_get(monkeypatch, {"/api/jobs/j1/urls": {"total": 1, "items": [
        {"id": 7, "url": "https://x/p", "status_code": 200, "indexable": True,
         "pagerank": 0.5, "word_count": 300}]}})
    out = verbs.buscar_urls("j1", contiene="p")
    assert out["total"] == 1 and out["urls"][0]["url_id"] == 7


def test_preguntar_a_los_datos_dossier(monkeypatch):
    _fake_get(monkeypatch, {
        "/api/jobs/j1/stats": {"total_urls": 10, "issues_by_type": [
            {"issue_type": "a", "severity": "error", "count": 1}]},
        "/api/jobs/j1/urls": {"items": [
            {"url": "https://x/home", "pagerank": 0.9, "status_code": 200, "gsc_clicks": 50}]},
        "/api/jobs/j1/semantic/results": {"gsc_summary": {"total_clicks": 123}}})
    out = verbs.preguntar_a_los_datos("j1", "¿cuál es la página más fuerte?")
    assert out["pregunta"].startswith("¿cuál")
    assert out["resumen"]["urls_totales"] == 10
    assert out["paginas_mas_importantes"][0]["url"].endswith("/home")
    assert out["search_console"]["total_clicks"] == 123


def test_error_passthrough(monkeypatch):
    _fake_get(monkeypatch, {"/api/jobs/jX/stats": {"error": "404: no existe"}})
    out = verbs.resumen_rastreo("jX")
    assert out == {"error": "404: no existe"}
