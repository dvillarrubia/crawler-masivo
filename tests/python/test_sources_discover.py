"""
Descubrimiento de propiedades desde el JSON de una service account.

No se llama a Google de verdad: con una credencial inválida cada fuente
debe degradar a {ok: false, error: ...} sin reventar (nada de 500).
"""

from __future__ import annotations


def test_discover_bad_credentials_is_graceful():
    from api.routers.sources import DiscoverRequest, discover_properties

    body = DiscoverRequest(credentials_json={
        "type": "service_account", "client_email": "x@y.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n",
    })
    out = discover_properties(body)

    assert set(out.keys()) == {"service_account", "gsc", "ga4"}
    assert out["service_account"] == "x@y.iam.gserviceaccount.com"
    for src in ("gsc", "ga4"):
        assert out[src]["ok"] is False          # credencial falsa → no ok
        assert out[src]["properties"] == []
        assert out[src]["error"]                 # con motivo


def test_discover_empty_json():
    from api.routers.sources import DiscoverRequest, discover_properties

    out = discover_properties(DiscoverRequest(credentials_json={}))
    assert out["service_account"] is None
    assert out["gsc"]["ok"] is False and out["ga4"]["ok"] is False
