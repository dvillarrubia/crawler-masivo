"""Herramientas del servidor MCP (decision 148), contra una API de mentira
con httpx.MockTransport: mandan el token, aceptan cliente por slug o nombre,
no escriben nada y devuelven lo justo."""

from __future__ import annotations

import json

import pytest

httpx = pytest.importorskip("httpx")

from mcp_servidor import herramientas as H  # noqa: E402

CID = "6ea9ae5b-426f-4e9b-8700-65a12edd8a53"
JID = "391b5ed6-c366-4432-97f8-1de6da33bc0b"


def _api(pedidas):
    def responder(req: httpx.Request) -> httpx.Response:
        pedidas.append((req.method, req.url.path, dict(req.url.params), req.headers.get("authorization")))
        assert req.method == "GET", "las herramientas no escriben"
        p = req.url.path
        if p == "/api/clients":
            return httpx.Response(200, json=[{"id": CID, "slug": "lopesan", "nombre": "Lopesan"}])
        if p == "/api/clients/panel":
            return httpx.Response(200, json={"clientes": [{"nombre": "Lopesan", "slug": "lopesan", "n_rastreos": 8,
                                                           "ultimo": {"id": JID, "name": "semanal", "errores": 126},
                                                           "incidentes_vivos": 2}]})
        if p == f"/api/jobs/{JID}/informe":
            return httpx.Response(200, json={"nota": {"global": 79}, "prioridades": [
                {"issue_type": "x", "por_segmento": None, "pct_paginas": None}]})
        if p == f"/api/jobs/{JID}/issues/resumen":
            return httpx.Response(200, json=[
                {"issue_type": "structured_data_error", "severity": "error", "paginas": 11, "incidencias": 14,
                 "prioridad": 143645.9, "ejemplos": [{"url": "https://x/a", "url_id": 1}]},
                {"issue_type": "url_too_long", "severity": "warning", "paginas": 424, "incidencias": 424,
                 "prioridad": 1760.6, "ejemplos": []}])
        if p == f"/api/clients/{CID}/incidentes":
            return httpx.Response(200, json=[{"titulo": "t", "estado": "abierto", "severidad": "critical"}])
        if p == "/api/jobs":
            return httpx.Response(200, json={"items": [{"id": JID, "name": "semanal", "status": "completed"}]})
        return httpx.Response(404, json={"detail": "no"})
    return H.Api(base="https://api.prueba", token="t" * 30, transporte=httpx.MockTransport(responder))


def test_manda_el_token_y_solo_lee():
    pedidas = []
    api = _api(pedidas)
    assert H.clientes(api)[0]["alertas_abiertas"] == 2
    assert all(m == "GET" and a == "Bearer " + "t" * 30 for m, _, _, a in pedidas)


def test_el_informe_sin_rastreo_es_el_del_ultimo_censo_y_sin_ruido():
    api = _api([])
    inf = H.informe(api, cliente="Lopesan")
    assert inf["nota"]["global"] == 79 and inf["prioridades"] == [{"issue_type": "x"}]


def test_problemas_por_prioridad_y_filtrados():
    pedidas = []
    filas = H.problemas(_api(pedidas), JID, gravedad="error")
    assert [f["tipo"] for f in filas] == ["structured_data_error"] and filas[0]["ejemplos"] == ["https://x/a"]
    assert pedidas[-1][2]["orden"] == "prioridad"


def test_cliente_por_slug_nombre_o_id_y_uno_que_no_existe():
    pedidas = []
    api = _api(pedidas)
    for c in ("lopesan", "LOPESAN", CID):
        assert H.alertas(api, c)[0]["estado"] == "abierto"
    with pytest.raises(ValueError, match="Hay: lopesan"):
        H.alertas(api, "penguin")
    H.rastreos(api, "lopesan", limite=500)
    assert pedidas[-1][2] == {"page_size": "50", "client_id": CID}


def test_el_token_sale_del_fichero_si_no_hay_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("CRAWLER_API_TOKEN", raising=False)
    f = tmp_path / "token"
    f.write_text("abc123\n")
    monkeypatch.setenv("CRAWLER_API_TOKEN_FILE", str(f))
    assert H._token() == "abc123"
