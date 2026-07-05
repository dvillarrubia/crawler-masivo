"""Claves de unión lógica del contrato Seontology (§2).

`page_id` = sha1(URL normalizada)[:16] con la política FIJA del contrato
(lowercase, sin trailing slash, sin parámetros de tracking, sin
fragmento). Es deliberadamente INDEPENDIENTE del `url_hash` del crawler
(sha256 con normalización configurable por job): la identidad del grafo
no puede depender de la config de un job. Cualquier componente puede
reconstruir la referencia cruzada sin consultar nada.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# La misma lista mantenida del repo (T8/C4): una única fuente de verdad
# de parámetros de tracking (incluye prefijos utm_).
from shared.url_normalization import is_tracking_param

_MULTI_SLASH_RE = re.compile(r"/{2,}")


def _normalize_for_graph(url: str) -> str:
    """Política del contrato: lowercase, sin trailing slash, sin utm,
    sin fragmento. Fija — no configurable."""
    parts = urlsplit((url or "").strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if parts.port and not (
        (scheme == "http" and parts.port == 80)
        or (scheme == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    path = _MULTI_SLASH_RE.sub("/", parts.path or "/").lower()
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not is_tracking_param(k)
    ))
    return urlunsplit((scheme, host, path, query, ""))  # sin fragmento


def page_id(url: str) -> str:
    """PK lógica de la página en ambos motores (contrato §2)."""
    return hashlib.sha1(_normalize_for_graph(url).encode()).hexdigest()[:16]


def chunk_id(url: str, chunk_index: int) -> str:
    """Derivable, ordenable, estable entre recrawls (contrato §2)."""
    return f"{page_id(url)}:{chunk_index:04d}"


def query_id(query_text: str) -> str:
    """Id determinista del nodo Query (texto normalizado en minúsculas)."""
    norm = re.sub(r"\s+", " ", (query_text or "").strip().lower())
    return hashlib.sha1(norm.encode()).hexdigest()[:16]
