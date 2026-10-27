"""Cabeceras de seguridad en todas las respuestas (decision 208).

Antes la API no mandaba ninguna. Se añaden como middleware ASGI puro (no
`BaseHTTPMiddleware`) para no envolver la respuesta entera: por aqui pasan el
CSV en streaming y el backup de un censo de 1 GB, igual que en la autenticacion.

- `X-Content-Type-Options: nosniff` y `X-Frame-Options: DENY` (mas
  `frame-ancestors 'none'` en la CSP): sin rastreo de tipo MIME y sin poder
  enmarcar la interfaz autenticada (clickjacking de «lanzar»/«borrar»).
- `Referrer-Policy: no-referrer`.
- **CSP de contencion.** El frontend es Alpine.js, que evalua sus expresiones
  con `new Function` y usa atributos en linea, asi que necesita `unsafe-eval` y
  `unsafe-inline` en los scripts: una CSP no puede impedir aqui que se ejecute
  un script inyectado (eso lo hace el saneo de la salida, decision 206 y el
  `x-text` de Alpine). Lo que si hace esta CSP es limitar el daño: `connect-src
  'self'` impide que un script robe datos y los mande a otro servidor;
  `frame-ancestors`, `object-src` y `base-uri` cierran enmarcado, plugins y el
  secuestro de la URL base. Los origenes de script/estilo son los CDNs que ya
  carga `index.html`.
"""

from __future__ import annotations

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
    "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://unpkg.com https://cdn.plot.ly; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://unpkg.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: blob:; "
    "worker-src 'self' blob:; "
    "connect-src 'self' https://unpkg.com https://cdn.jsdelivr.net; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

_CABECERAS = [
    (b"content-security-policy", _CSP.encode("latin-1")),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


class CabecerasSeguridadMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_con_cabeceras(evento):
            if evento["type"] == "http.response.start":
                existentes = {k.lower() for k, _ in evento.get("headers") or []}
                evento = dict(evento)
                evento["headers"] = list(evento.get("headers") or []) + [
                    (k, v) for k, v in _CABECERAS if k not in existentes
                ]
            await send(evento)

        return await self.app(scope, receive, send_con_cabeceras)
