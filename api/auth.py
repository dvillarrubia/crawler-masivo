"""Autenticación por API key POR PROYECTO (B1 · auth).

Diseño (regla de oro: no romper nada):
- DESACTIVADA por defecto. Con `API_AUTH_ENABLED != "1"` el middleware deja
  pasar todo → la consola local y los flujos actuales siguen igual.
- Cuando se activa, cada petición a `/api/*` (salvo `/health`, la gestión de
  keys y lo que no es API) exige una API key válida. La key va atada a un
  `client_id` y SOLO da acceso a los datos de ese proyecto (scoping por
  ruta: `/api/clients/{cid}/…` y `/api/jobs/{job_id}…`).
- La gestión de keys (`…/api-keys`) se protege con un ADMIN_TOKEN aparte
  (para poder emitir la primera key sin tener ya una).

Solo se guarda el sha256 de la clave; la clave entera se enseña una vez.

Las partes de decisión son funciones puras (testeables sin la app); el
middleware las usa contra la BD real.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

KEY_PREFIX = "sk_"


# ---------------------------------------------------------------------------
# Config (se lee en runtime para poder testear con monkeypatch del entorno)
# ---------------------------------------------------------------------------
def auth_enabled() -> bool:
    return os.getenv("API_AUTH_ENABLED", "0") == "1"


def admin_token() -> str | None:
    tok = os.getenv("ADMIN_TOKEN", "").strip()
    return tok or None


# ---------------------------------------------------------------------------
# Claves
# ---------------------------------------------------------------------------
def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Devuelve (clave_entera, hash, prefijo). La clave entera solo se
    muestra una vez; se persiste el hash y el prefijo (para reconocerla)."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:12]


def extract_key(headers) -> str | None:
    """Saca la key de `X-API-Key` o `Authorization: Bearer …`."""
    k = headers.get("x-api-key")
    if k:
        return k.strip()
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


# ---------------------------------------------------------------------------
# Decisión de acceso (pura). `get_job_client(job_id) -> client_id | None`.
# Devuelve (status, mensaje): status None = permitido.
# ---------------------------------------------------------------------------
def is_key_management(path: str) -> bool:
    return "/api-keys" in path


def decide_access(path: str, key_client: str, get_job_client) -> tuple[int | None, str]:
    """Aplica el scoping por ruta para una key ya validada de `key_client`."""
    segs = path.split("/")   # ['', 'api', 'clients', '{cid}', ...]
    if path.startswith("/api/clients/") and len(segs) > 3:
        cid = unquote(segs[3])
        if cid != key_client:
            return 403, "La API key no pertenece a este proyecto."
        return None, ""
    if path.startswith("/api/jobs/") and len(segs) > 3:
        jid = segs[3]
        # rutas especiales sin job_id (p. ej. /api/jobs/import): no se scopean
        owner = get_job_client(jid)
        if owner is not None and owner != key_client:
            return 403, "Este rastreo pertenece a otro proyecto."
        return None, ""
    # colección /api/jobs (listar/crear) y otras rutas transversales: se exige
    # key válida (ya comprobado) pero el scoping fino lo aplica el router
    # (list_jobs/create_job fuerzan el client_id de la key).
    return None, ""


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not auth_enabled():
            return await call_next(request)

        path = request.url.path
        method = request.method
        # abierto: health, preflight CORS y todo lo que no es API (frontend)
        if method == "OPTIONS" or path == "/health" or not path.startswith("/api/"):
            return await call_next(request)

        # Gestión de keys: admin token
        if is_key_management(path):
            tok = admin_token()
            if tok is None:
                return _err(503, "Gestión de keys deshabilitada: define ADMIN_TOKEN.")
            got = extract_key(request.headers) or request.headers.get("x-admin-token")
            if got != tok:
                return _err(401, "Admin token inválido o ausente.")
            request.state.is_admin = True
            return await call_next(request)

        # Resto de /api: key de proyecto válida
        raw = extract_key(request.headers)
        if not raw:
            return _err(401, "Falta la API key (cabecera X-API-Key o Authorization: Bearer).")

        client_id = _validate_and_touch(hash_key(raw))
        if client_id is None:
            return _err(401, "API key inválida o revocada.")

        status, msg = decide_access(path, client_id, _job_client_lookup)
        if status is not None:
            return _err(status, msg)

        request.state.auth_client_id = client_id
        return await call_next(request)


def _err(code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=code)


def _validate_and_touch(key_hash: str) -> str | None:
    """Busca la key por hash (no revocada), marca last_used_at y devuelve su
    client_id. None si no vale."""
    from datetime import datetime, timezone

    from shared.database import SessionLocal
    from shared.semantic_models import ApiKey

    db = SessionLocal()
    try:
        row = (db.query(ApiKey)
               .filter(ApiKey.key_hash == key_hash, ApiKey.revoked.is_(False))
               .first())
        if row is None:
            return None
        row.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return row.client_id
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()


def _job_client_lookup(job_id: str) -> str | None:
    """client_id del rastreo, o None si el id no es válido / no existe."""
    import uuid

    from shared.database import SessionLocal
    from shared.models import Job

    try:
        jid = uuid.UUID(job_id)
    except (ValueError, AttributeError):
        return None
    db = SessionLocal()
    try:
        row = db.query(Job.client_id).filter(Job.id == jid).first()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        db.close()
