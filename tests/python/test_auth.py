"""
Auth por API key por proyecto (B1). Partes puras: generación/hash de claves,
extracción de cabeceras y decisión de scoping por ruta.
"""

from __future__ import annotations

from api import auth


def test_generate_and_hash():
    raw, h, prefix = auth.generate_api_key()
    assert raw.startswith("sk_")
    assert prefix == raw[:12]
    assert h == auth.hash_key(raw) and len(h) == 64
    # dos claves distintas
    raw2, h2, _ = auth.generate_api_key()
    assert raw != raw2 and h != h2


def test_extract_key_variants():
    assert auth.extract_key({"x-api-key": "sk_abc"}) == "sk_abc"
    assert auth.extract_key({"authorization": "Bearer sk_xyz"}) == "sk_xyz"
    assert auth.extract_key({"authorization": "bearer sk_low"}) == "sk_low"
    assert auth.extract_key({}) is None


def test_is_key_management():
    assert auth.is_key_management("/api/clients/x/api-keys")
    assert auth.is_key_management("/api/clients/x/api-keys/123")
    assert not auth.is_key_management("/api/clients/x/settings")


def test_decide_access_client_scope():
    # ruta de otro proyecto → 403
    st, _ = auth.decide_access("/api/clients/otro/settings", "mio", lambda j: None)
    assert st == 403
    # ruta del propio proyecto → ok
    st, _ = auth.decide_access("/api/clients/mio/settings", "mio", lambda j: None)
    assert st is None


def test_decide_access_job_scope():
    lookup = {"job-de-mio": "mio", "job-ajeno": "otro"}.get
    # rastreo del proyecto → ok
    st, _ = auth.decide_access("/api/jobs/job-de-mio/stats", "mio", lookup)
    assert st is None
    # rastreo ajeno → 403
    st, _ = auth.decide_access("/api/jobs/job-ajeno/stats", "mio", lookup)
    assert st == 403
    # rastreo inexistente → se deja pasar (la ruta hará 404)
    st, _ = auth.decide_access("/api/jobs/no-existe/stats", "mio", lambda j: None)
    assert st is None


def test_decide_access_collection_is_coarse():
    # /api/jobs (listar/crear) no se scopea por ruta (lo hace el router)
    st, _ = auth.decide_access("/api/jobs", "mio", lambda j: None)
    assert st is None


def test_auth_enabled_flag(monkeypatch):
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    assert auth.auth_enabled() is False
    monkeypatch.setenv("API_AUTH_ENABLED", "1")
    assert auth.auth_enabled() is True
    monkeypatch.setenv("API_AUTH_ENABLED", "0")
    assert auth.auth_enabled() is False


# --- gestión de keys (CRUD) + validación ----------------------------------
import pytest  # noqa: E402


@pytest.fixture()
def apikey_table(db_engine):
    from shared.semantic_models import ApiKey
    ApiKey.__table__.create(db_engine, checkfirst=True)
    return True


def test_create_list_revoke(db_session, apikey_table):
    from api.routers.api_keys import (
        ApiKeyCreate, create_api_key, list_api_keys, revoke_api_key,
    )
    import uuid as _uuid

    created = create_api_key("cli", ApiKeyCreate(name="agente 1"), db=db_session)
    assert created["api_key"].startswith("sk_")          # clave entera, una vez
    assert created["prefix"] == created["api_key"][:12]

    rows = list_api_keys("cli", db=db_session)
    assert len(rows) == 1 and rows[0].name == "agente 1" and rows[0].revoked is False
    # el listado NO expone la clave
    assert not hasattr(rows[0], "api_key")

    revoke_api_key("cli", _uuid.UUID(created["id"]), db=db_session)
    rows = list_api_keys("cli", db=db_session)
    assert rows[0].revoked is True


def test_validate_and_touch(db_session, apikey_table, monkeypatch):
    import shared.database as dbmod
    from api.routers.api_keys import ApiKeyCreate, create_api_key

    class _NoClose:
        def __init__(self, real): self._real = real
        def __getattr__(self, n): return getattr(self._real, n)
        def close(self): pass

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _NoClose(db_session))

    created = create_api_key("cli", ApiKeyCreate(name="k"), db=db_session)
    raw = created["api_key"]

    # clave válida → devuelve su client_id y marca last_used_at
    assert auth._validate_and_touch(auth.hash_key(raw)) == "cli"
    from shared.semantic_models import ApiKey
    row = db_session.query(ApiKey).one()
    assert row.last_used_at is not None

    # clave inexistente → None
    assert auth._validate_and_touch(auth.hash_key("sk_noexiste")) is None

    # revocada → None
    row.revoked = True
    db_session.commit()
    assert auth._validate_and_touch(auth.hash_key(raw)) is None
