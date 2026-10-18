"""Transporte unico hacia OpenRouter (#7, decision 84 de CLAUDE.md).

Toda llamada a un modelo sale de aqui. Hoy solo Jev por `POST /systemone`;
los embeddings del modulo semantico y cualquier generacion vendran detras
usando el mismo `_post`, para que la resiliencia, el ritmo y la contabilidad
del coste existan UNA vez y no una por cada sitio que habla con un modelo.

Por que un cliente propio sobre httpx y no los SDK oficiales:

- el de OpenRouter (`openrouter` 1.3.22) fija `pydantic<2.13` y por defecto
  **no reintenta los 429** (`retry_config = (retries, ["5XX"])`);
- el de TypeSafe (`typesafe-sdk` 0.7.2) arrastra otra libreria HTTP
  (`httpx2`) y su `Usage` tiene `extra="ignore"`: **descarta `usage.cost`**,
  que es justo lo que necesita el presupuesto.

El formato del cuerpo y de la respuesta lo fijan tests de contrato contra los
modelos Pydantic de los dos SDK (`tests/test_llm_contrato.py`), que se
instalan solo para eso.

Formato verificado (2026-10-13) en el codigo de los dos SDK y en la guia
publica de OpenRouter `docs/guides/community/typesafe-sdk`:

    POST https://openrouter.ai/api/v1/systemone
    {"model": "typesafe/jev-1.13", "state": <str|obj|lista>,
     "questions": {"<nombre>": {"type": "noul|choice|score",
                                "instructions": ..., "criteria": ...}},
     "provider": {...}, "session_id": "...", "user": "..."}
    -> {"id": "gen-dec-...", "model": "typesafe/jev-1.13-20260917",
        "provider": "TypeSafe",
        "answers": {"<nombre>": {"type": "noul", "noul": 0.98}},
        "usage": {"input_tokens": 275, "output_tokens": 20, "cost": 0.00003}}

Nada de este modulo se ejecuta solo: sin `OPENROUTER_API_KEY` no se
construye ningun cliente, y ningun rastreo ni analisis lo importa (lo vigila
`tests/test_decisiones_no_se_disparan.py`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from shared.respuestas import retry_after_segundos

logger = logging.getLogger(__name__)

URL_BASE_DEFECTO = "https://openrouter.ai/api/v1"

# Reintentables: el servidor o la red han fallado, no la peticion. 524 y 529
# son de OpenRouter (timeout del borde y proveedor sobrecargado) y estan en
# la lista de errores tipados de su SDK; 408 lo reintenta el de TypeSafe.
ESTADOS_REINTENTABLES = frozenset({408, 429, *range(500, 600)})
# Sin credito: no se arregla reintentando y vale para TODO lo que queda.
ESTADO_SIN_CREDITO = 402
# La clave o el modelo no valen: tampoco se arregla solo y afecta a todas las
# peticiones siguientes, asi que cortan la capa entera en vez de quemar
# reintentos estado por estado.
ESTADOS_QUE_CORTAN = frozenset({401, 403, 404})


class ErrorLLM(Exception):
    """Una llamada que no ha dado respuesta, con lo que hace falta para decidir.

    `corta_capa` = no tiene sentido seguir llamando en esta ejecucion (sin
    credito, clave invalida, modelo inexistente, circuito abierto). Sin el, el
    error es de ESTA peticion (un 400/413/422 por un estado concreto, o los
    reintentos agotados) y las demas siguen.
    """

    def __init__(self, mensaje: str, *, status: int | None = None,
                 motivo: str = "error", corta_capa: bool = False,
                 intentos: int = 1, n_429: int = 0):
        super().__init__(mensaje)
        self.status = status
        self.motivo = motivo
        self.corta_capa = corta_capa
        self.intentos = intentos
        self.n_429 = n_429


@dataclass(frozen=True)
class Uso:
    """Lo que costo una llamada, tal como lo informa OpenRouter.

    `coste_usd` es None cuando la respuesta no trae `usage.cost`: el campo es
    OPCIONAL en `DecisionsResponseUsage` del SDK. Quien lleve el presupuesto lo
    estima entonces con tokens x precio y lo marca como estimado.
    """

    tokens_entrada: int
    tokens_salida: int
    coste_usd: float | None
    modelo: str | None
    proveedor: str | None
    id: str | None


@dataclass
class Respuesta:
    datos: dict
    # Lo rellena el metodo de cada ruta, que es quien sabe leer su `usage`.
    uso: Uso | None
    latencia_ms: float
    intentos: int = 1
    n_429: int = 0


class _Ritmo:
    """Peticiones por minuto con ventana deslizante y AIMD ante un 429.

    El tope publicado es de TypeSafe directo (80 peticiones/s y 100.000
    tokens/s en `docs.typesafe.ai/models`, consultado el 2026-10-13); el de Jev
    A TRAVES de OpenRouter no esta publicado. Por eso el defecto es
    conservador (`LLM_RPM=600`) y se ajusta midiendo, no copiando la cifra.

    Ante un 429 el ritmo efectivo baja a la mitad durante 60 s: insistir al
    mismo ritmo solo produce mas 429 y quema los reintentos de todos.
    """

    def __init__(self, rpm: int, reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.rpm = max(1, int(rpm))
        self._reloj = reloj
        self._dormir = dormir
        self._envios: deque[float] = deque()
        self._reducido_hasta = 0.0
        self._cerrojo = asyncio.Lock()

    @property
    def rpm_efectivo(self) -> int:
        if self._reloj() < self._reducido_hasta:
            return max(1, self.rpm // 2)
        return self.rpm

    def frenar(self) -> None:
        self._reducido_hasta = self._reloj() + 60.0

    async def esperar_turno(self) -> None:
        while True:
            async with self._cerrojo:
                ahora = self._reloj()
                while self._envios and ahora - self._envios[0] >= 60.0:
                    self._envios.popleft()
                if len(self._envios) < self.rpm_efectivo:
                    self._envios.append(ahora)
                    return
                espera = 60.0 - (ahora - self._envios[0])
            await self._dormir(max(espera, 0.01))


def _retry_after_s(cabeceras) -> float | None:
    """`retry-after-ms` manda sobre `retry-after` (mismo orden que el SDK de
    TypeSafe, `_core/errors.parse_retry_after`). Admite segundos o fecha HTTP;
    el `retry-after` lo lee la misma funcion que el rastreo (decision 111)."""
    bruto = cabeceras.get("retry-after-ms")
    if bruto is not None:
        try:
            v = float(bruto)
            if v >= 0:
                return v / 1000.0
        except ValueError:
            pass
    return retry_after_segundos(cabeceras.get("retry-after"))


def _backoff(intento: int, inicial: float, maximo: float) -> float:
    """Exponencial con jitter RESTADO, como `_backoff` del SDK de TypeSafe: el
    jitter separa a los clientes que fallaron a la vez sin pasar nunca del
    maximo."""
    base = min(maximo, inicial * (2 ** max(0, intento - 1)))
    return base * (1 - random.random() * 0.25)


def _mensaje_error(resp) -> str:
    try:
        cuerpo = resp.json()
    except Exception:  # noqa: BLE001 - el cuerpo de un error puede ser HTML
        return (resp.text or "")[:200]
    if isinstance(cuerpo, dict):
        err = cuerpo.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"][:200]
        if isinstance(err, str):
            return err[:200]
        if isinstance(cuerpo.get("detail"), (str, list)):
            return str(cuerpo["detail"])[:200]
    return str(cuerpo)[:200]


def numero_de_entorno(nombre: str, defecto: float, tipo=float):
    """Una variable vacia (`LLM_RPM=` en el .env) es «sin poner», no un error."""
    bruto = (os.getenv(nombre) or "").strip()
    return tipo(bruto) if bruto else tipo(defecto)


@dataclass
class ConfigLLM:
    api_key: str
    base_url: str = URL_BASE_DEFECTO
    rpm: int = 600
    concurrencia: int = 16
    timeout_s: float = 15.0
    max_reintentos: int = 3
    backoff_inicial_s: float = 0.5
    backoff_max_s: float = 10.0
    # Tope de tiempo de una llamada contando reintentos: un estado no puede
    # retener un hueco del semaforo minutos enteros.
    presupuesto_reintentos_s: float = 30.0
    # Fallos seguidos (ya reintentados) tras los que se deja de llamar.
    fallos_para_abrir_circuito: int = 5
    app_url: str | None = None
    app_titulo: str | None = "SEO Crawler"
    # `data_collection: deny` por defecto: que el proveedor no use lo enviado
    # para entrenar. `zdr` (retencion cero) es opcional porque, si el endpoint
    # no la ofrece, la peticion FALLA en vez de degradar (ver decision 84).
    zdr: bool = False
    extra_cabeceras: dict = field(default_factory=dict)

    @classmethod
    def desde_entorno(cls) -> "ConfigLLM | None":
        """None sin `OPENROUTER_API_KEY`: sin clave no hay capa, y no es un error."""
        clave = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        if not clave:
            return None
        return cls(
            api_key=clave,
            base_url=(os.getenv("OPENROUTER_BASE_URL") or URL_BASE_DEFECTO).rstrip("/"),
            rpm=numero_de_entorno("LLM_RPM", 600, int),
            concurrencia=numero_de_entorno("LLM_MAX_CONCURRENCIA", 16, int),
            timeout_s=numero_de_entorno("LLM_TIMEOUT_S", 15.0),
            app_url=os.getenv("OPENROUTER_APP_URL") or None,
            app_titulo=os.getenv("OPENROUTER_APP_TITLE") or "SEO Crawler",
            zdr=os.getenv("LLM_ZDR", "0") == "1",
        )


class ClienteOpenRouter:
    """Cliente asincrono. Uno por ejecucion: el ritmo y el circuito son suyos.

    Asincrono porque lo van a usar el spider (#10, sobre el bucle asyncio del
    reactor: `settings.py` instala `AsyncioSelectorReactor`) y el analizador
    por lotes. El `requests.post` sincrono de `scrapy-jev` bloquea el reactor
    mientras dura cada llamada: es el ejemplo de lo que no hay que hacer.
    """

    def __init__(self, config: ConfigLLM, *, transport=None,
                 reloj: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], Awaitable[None]] = asyncio.sleep):
        import httpx  # aqui y no arriba: la imagen que no llama a nadie no lo necesita

        self.config = config
        self._dormir = dormir
        self._reloj = reloj
        self._ritmo = _Ritmo(config.rpm, reloj=reloj, dormir=dormir)
        self._semaforo = asyncio.Semaphore(max(1, config.concurrencia))
        self._fallos_seguidos = 0
        self.circuito_abierto = False
        # Motivo de un error que vale para todo lo que queda (402, clave o
        # modelo invalidos). Se mira tambien DESPUES de esperar al semaforo:
        # las llamadas que ya estaban en cola no deben salir tras un 402.
        self.corte: str | None = None
        cabeceras = {"Authorization": f"Bearer {config.api_key}",
                     "Content-Type": "application/json",
                     "Accept": "application/json"}
        # `X-OpenRouter-Title` y no `X-Title`: es el que manda el SDK oficial
        # (`operations/createsystemone.py`).
        if config.app_url:
            cabeceras["HTTP-Referer"] = config.app_url
        if config.app_titulo:
            cabeceras["X-OpenRouter-Title"] = config.app_titulo
        cabeceras.update(config.extra_cabeceras)
        self._http = httpx.AsyncClient(
            base_url=config.base_url, headers=cabeceras,
            timeout=httpx.Timeout(config.timeout_s), transport=transport)

    async def cerrar(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.cerrar()

    @property
    def rpm_efectivo(self) -> int:
        return self._ritmo.rpm_efectivo

    def preferencias_proveedor(self) -> dict:
        prov: dict[str, Any] = {"data_collection": "deny", "allow_fallbacks": True}
        if self.config.zdr:
            prov["zdr"] = True
        return prov

    async def systemone(self, estado, preguntas: dict, modelo: str, *,
                        session_id: str | None = None,
                        user: str | None = None) -> Respuesta:
        """Una llamada a Jev con TODAS las preguntas de un estado.

        `session_id` (el job) y `user` (el slug del cliente) son
        identificadores para agrupar el gasto en el panel de OpenRouter; segun
        el SDK, `session_id` «never sent to the provider». Nunca el nombre del
        cliente.
        """
        cuerpo: dict[str, Any] = {
            "model": modelo,
            "state": estado,
            "questions": preguntas,
            "provider": self.preferencias_proveedor(),
        }
        if session_id:
            cuerpo["session_id"] = str(session_id)[:256]
        if user:
            cuerpo["user"] = str(user)
        resp = await self._post("/systemone", cuerpo)
        datos = resp.datos
        uso = datos.get("usage") or {}
        coste = uso.get("cost")
        resp.uso = Uso(
            tokens_entrada=int(uso.get("input_tokens") or 0),
            tokens_salida=int(uso.get("output_tokens") or 0),
            coste_usd=float(coste) if isinstance(coste, (int, float)) else None,
            modelo=datos.get("model"),
            proveedor=datos.get("provider"),
            id=datos.get("id"),
        )
        return resp

    async def _post(self, ruta: str, cuerpo: dict) -> Respuesta:
        self._comprobar_corte()
        inicio = self._reloj()
        async with self._semaforo:
            self._comprobar_corte()
            try:
                return await self._intentar(ruta, cuerpo, inicio)
            except ErrorLLM as exc:
                if exc.corta_capa and exc.intentos:
                    self.corte = self.corte or exc.motivo
                raise

    def _comprobar_corte(self) -> None:
        if self.circuito_abierto:
            raise ErrorLLM("circuito abierto: demasiados fallos seguidos",
                           motivo="circuito_abierto", corta_capa=True, intentos=0)
        if self.corte:
            raise ErrorLLM(f"capa cortada: {self.corte}", motivo=self.corte,
                           corta_capa=True, intentos=0)

    async def _intentar(self, ruta: str, cuerpo: dict, inicio: float) -> Respuesta:
        import httpx

        intentos = 0
        n_429 = 0
        ultimo: ErrorLLM | None = None
        while True:
            intentos += 1
            await self._ritmo.esperar_turno()
            t0 = time.perf_counter()
            espera: float | None = None
            try:
                r = await self._http.post(ruta, json=cuerpo)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                ultimo = ErrorLLM(f"{type(exc).__name__}: {exc}",
                                  motivo="red", intentos=intentos, n_429=n_429)
            else:
                latencia = (time.perf_counter() - t0) * 1000.0
                if 200 <= r.status_code < 300:
                    self._fallos_seguidos = 0
                    try:
                        datos = r.json()
                    except ValueError as exc:
                        raise ErrorLLM(f"respuesta no JSON: {exc}",
                                       status=r.status_code, motivo="respuesta_invalida",
                                       intentos=intentos, n_429=n_429) from exc
                    return Respuesta(datos=datos, uso=None, latencia_ms=latencia,
                                     intentos=intentos, n_429=n_429)
                msg = f"HTTP {r.status_code}: {_mensaje_error(r)}"
                if r.status_code == ESTADO_SIN_CREDITO:
                    raise ErrorLLM(msg, status=402, motivo="sin_credito",
                                   corta_capa=True, intentos=intentos, n_429=n_429)
                if r.status_code in ESTADOS_QUE_CORTAN:
                    motivo = "modelo_inexistente" if r.status_code == 404 else "clave_invalida"
                    raise ErrorLLM(msg, status=r.status_code, motivo=motivo,
                                   corta_capa=True, intentos=intentos, n_429=n_429)
                if r.status_code not in ESTADOS_REINTENTABLES:
                    # 400/413/422: la peticion de ESTE estado esta mal.
                    # Reintentarla da lo mismo y no dice nada de las demas.
                    raise ErrorLLM(msg, status=r.status_code, motivo="peticion_rechazada",
                                   intentos=intentos, n_429=n_429)
                if r.status_code == 429:
                    n_429 += 1
                    self._ritmo.frenar()
                espera = _retry_after_s(r.headers)
                ultimo = ErrorLLM(msg, status=r.status_code,
                                  motivo="limite" if r.status_code == 429 else "servidor",
                                  intentos=intentos, n_429=n_429)
            if intentos > self.config.max_reintentos:
                break
            if espera is None:
                espera = _backoff(intentos, self.config.backoff_inicial_s,
                                  self.config.backoff_max_s)
            if self._reloj() - inicio + espera >= self.config.presupuesto_reintentos_s:
                break
            await self._dormir(espera)

        assert ultimo is not None
        ultimo.intentos, ultimo.n_429 = intentos, n_429
        self._fallos_seguidos += 1
        if self._fallos_seguidos >= self.config.fallos_para_abrir_circuito:
            # Un proveedor caido no se arregla dentro de un censo: seguir
            # llamando gastaria 30 s por estado para dejarlos todos igual de
            # sin decidir.
            self.circuito_abierto = True
            ultimo.corta_capa = True
            logger.warning("LLM: circuito abierto tras %d fallos seguidos (%s)",
                           self._fallos_seguidos, ultimo)
        raise ultimo
