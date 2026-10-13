"""`FakeLLM`: hace de OpenRouter `/systemone` detras de un `httpx.MockTransport`.

En un modulo propio y no dentro de `conftest.py` para poder importarlo
tambien desde `spider_harness.py`, que corre en otro proceso. El fixture
`fake_llm` de `conftest.py` lo expone a los tests.
"""

from __future__ import annotations

import json


class FakeLLM:
    """- `guion`: codigos que se devuelven en orden (`[429, 200]`); al
      acabarse, 200. Con `retry_after_ms` el 429 lleva esa cabecera.
    - respuestas fijas por tipo (`p_noul`; la primera opcion de un choice con
      `p_choice`; el nivel mas alto de un score) o por nombre (`respuestas`).
    - `coste`: `usage.cost` de cada llamada; None = la respuesta no lo trae
      (es opcional en `DecisionsResponseUsage` del SDK de OpenRouter).
    - `prohibido=True`: cualquier llamada es un fallo. Es la trampa de «nada
      se dispara solo».
    """

    def __init__(self, *, guion=None, p_noul=0.9, p_choice=0.8, coste=0.0001,
                 respuestas=None, retry_after_ms=None, prohibido=False,
                 modelo_respuesta="typesafe/jev-1.13-20260917", tokens_entrada=275):
        self.guion = list(guion or [])
        self.p_noul = p_noul
        self.p_choice = p_choice
        self.coste = coste
        self.respuestas = respuestas or {}
        self.retry_after_ms = retry_after_ms
        self.prohibido = prohibido
        self.modelo_respuesta = modelo_respuesta
        self.tokens_entrada = tokens_entrada
        self.peticiones: list[dict] = []
        self.cabeceras: list[dict] = []
        self.intentos_prohibidos = 0

    @property
    def llamadas(self) -> int:
        return len(self.peticiones)

    def _responder(self, nombre, q):
        if nombre in self.respuestas:
            return self.respuestas[nombre]
        if q["type"] == "noul":
            return {"type": "noul", "noul": self.p_noul}
        if q["type"] == "choice":
            opciones = list(q["criteria"])
            resto = (1 - self.p_choice) / max(1, len(opciones) - 1)
            probs = {o: (self.p_choice if i == 0 else resto) for i, o in enumerate(opciones)}
            return {"type": "choice", "choice": opciones[0], "confidence": self.p_choice,
                    "probabilities": probs}
        niveles = len(q["criteria"])
        probs = {str(i): (0.9 if i == niveles - 1 else 0.1 / (niveles - 1))
                 for i in range(niveles)}
        return {"type": "score", "score": float(niveles - 1) - 0.1, "confidence": 0.9,
                "probabilities": probs,
                "legend": {str(i): c for i, c in enumerate(q["criteria"])}}

    def __call__(self, request):
        import httpx

        if self.prohibido:
            self.intentos_prohibidos += 1
            raise AssertionError(f"llamada a un modelo no permitida: {request.url}")
        cuerpo = json.loads(request.content or b"{}")
        self.peticiones.append(cuerpo)
        self.cabeceras.append(dict(request.headers))
        codigo = self.guion.pop(0) if self.guion else 200
        if codigo != 200:
            cab = {}
            if codigo == 429 and self.retry_after_ms is not None:
                cab["retry-after-ms"] = str(self.retry_after_ms)
            return httpx.Response(codigo, headers=cab,
                                  json={"error": {"code": codigo, "message": f"fake {codigo}"}})
        uso = {"input_tokens": self.tokens_entrada, "output_tokens": 20}
        if self.coste is not None:
            uso["cost"] = self.coste
        return httpx.Response(200, json={
            "id": f"gen-dec-fake-{len(self.peticiones)}", "model": self.modelo_respuesta,
            "provider": "TypeSafe",
            "answers": {n: self._responder(n, q) for n, q in cuerpo["questions"].items()},
            "usage": uso})

    @property
    def transport(self):
        import httpx

        return httpx.MockTransport(self)


HOSTS_DE_PAGO = ("openrouter.ai", "typesafe.ai")


def vigilar_httpx(setattr_):
    """Que ningun transporte REAL de httpx salga hacia OpenRouter o TypeSafe.

    `setattr_` es `monkeypatch.setattr` en los tests o `setattr` a secas en un
    subproceso. Los `MockTransport` no pasan por aqui. Devuelve una lista que
    se llena con cada intento bloqueado.
    """
    import httpx

    intentos: list[str] = []

    def _envolver(original):
        def envoltura(self, request, *a, **k):
            if any(str(request.url.host).endswith(h) for h in HOSTS_DE_PAGO):
                intentos.append(str(request.url))
                raise AssertionError(f"intento de llamar a {request.url}")
            return original(self, request, *a, **k)
        return envoltura

    setattr_(httpx.HTTPTransport, "handle_request",
             _envolver(httpx.HTTPTransport.handle_request))
    setattr_(httpx.AsyncHTTPTransport, "handle_async_request",
             _envolver(httpx.AsyncHTTPTransport.handle_async_request))
    return intentos
