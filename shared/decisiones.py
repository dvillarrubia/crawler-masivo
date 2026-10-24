"""Capa de decisiones tipadas sobre Jev (#7, decision 84 de CLAUDE.md).

`preguntar(estados, preguntas)` devuelve, por cada estado, una `Decision` por
pregunta. Lo que hace por dentro, en este orden:

1. **Cache.** Cada `(estado normalizado, pregunta, version, modelo)` se busca
   en memoria, en Redis (lo que comparten las replicas) y en la tabla
   `decisions` (la cache duradera: un recrawl no vuelve a pagar).
2. **Agrupa.** Las preguntas que faltan de un mismo estado van en UNA llamada
   (Jev las evalua en una pasada y se paga el estado una vez), y dos estados
   identicos —bloques de plantilla— son una sola llamada.
3. **Presupuesto.** Antes de cada llamada se RESERVA su coste estimado contra
   el tope de la ejecucion, el mensual del cliente y el global; despues se
   ajusta con `usage.cost`. Sin reserva previa, con concurrencia se pasa del
   tope.
4. **Degrada.** Al agotarse un tope, sin credito (402), con la clave mala o
   con la API caida, lo pendiente queda `sin_decidir` con su motivo y **nada
   lanza una excepcion**: una caida de Jev no puede tumbar un censo.

Nada de esto se dispara solo. Ni el spider, ni el analizador, ni el worker lo
importan: el disparo es el boton de #40 (y, durante el rastreo, los modulos de
#10, #14 y #20 cuando existan y se activen por job).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol

from shared.llm import ClienteOpenRouter, ConfigLLM, ErrorLLM, numero_de_entorno
from shared.preguntas import Pregunta

logger = logging.getLogger(__name__)

# Verificado el 2026-10-13 en `GET /api/v1/models/typesafe/jev-1.13/endpoints`:
# 0,000000042 $/token de entrada y 0 de salida. Solo se usa para la RESERVA y
# cuando la respuesta no trae `usage.cost`; lo que cuenta es el coste real.
PRECIO_USD_MTOK_DEFECTO = 0.042
# Lo que cada llamada a `/systemone` factura ademas del cuerpo (decision 162).
TOKENS_FIJOS_POR_LLAMADA = 250

# decidido | incierto | sin_decidir
DECIDIDO, INCIERTO, SIN_DECIDIR = "decidido", "incierto", "sin_decidir"


# ---------------------------------------------------------------------------
# Estados y claves
# ---------------------------------------------------------------------------
@dataclass
class Estado:
    """Lo que se le enseña al modelo. MINIMO: titulo, h1, el principio del
    contenido, el codigo... nunca el HTML entero (cuesta y expone mas). Lo
    construye cada familia de preguntas (#9 en adelante)."""

    datos: Any
    ambito: str = "url"
    url_id: int | None = None


_RE_ESPACIOS = re.compile(r"\s+")
# Tokens volatiles: cambian en cada respuesta sin que cambie la pagina, y si
# entran en la clave la cache no acierta nunca.
# - parametro de version de un recurso: `?v=1712345`, `&ver=6.4.2`
_RE_VERSION_RECURSO = re.compile(r"([?&](?:v|ver|version|cb|_)=)[\w.\-]+", re.I)
# - marca de tiempo ISO CON hora (una fecha sola puede ser la de una oferta)
_RE_TIMESTAMP = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?\b")
# - nonces, tokens CSRF, ids de sesion: 24+ caracteres sin espacios que mezclan
#   letras y cifras. Un precio, un SKU corto o una fecha no llegan a 24.
_RE_TOKEN = re.compile(r"\b(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])"
                       r"[A-Za-z0-9_\-+/=]{24,}\b")


def _texto_para_enviar(texto: str) -> str:
    return _RE_ESPACIOS.sub(" ", unicodedata.normalize("NFKC", texto)).strip()


def _texto_para_clave(texto: str) -> str:
    t = _texto_para_enviar(texto)
    t = _RE_VERSION_RECURSO.sub(r"\1", t)
    t = _RE_TIMESTAMP.sub("", t)
    t = _RE_TOKEN.sub("", t)
    return _RE_ESPACIOS.sub(" ", t).strip().casefold()


def _mapear(datos: Any, f: Callable[[str], str]) -> Any:
    if isinstance(datos, str):
        return f(datos)
    if isinstance(datos, dict):
        return {str(k): _mapear(v, f) for k, v in datos.items()}
    if isinstance(datos, (list, tuple)):
        return [_mapear(v, f) for v in datos]
    return datos


def normalizar_estado(datos: Any) -> Any:
    """Lo que se ENVIA: NFKC y espacios colapsados (ahorra tokens sin cambiar
    el sentido). Se mantiene la caja y los tokens: el modelo ve la pagina."""
    return _mapear(datos, _texto_para_enviar)


def clave_estado(datos: Any) -> str:
    """Hash del estado normalizado PARA LA CLAVE: ademas de lo anterior, sin
    caja y sin tokens volatiles (nonces, CSRF, marcas de tiempo, `?v=`).

    Los numeros de negocio NO se tocan —un precio o la fecha de una oferta
    cambian la respuesta—, y por eso el patron de token exige 24 caracteres.
    Misma idea que `content_hash` (decision 47): se compara el contenido, no
    los bytes. Estable entre jobs: no depende de `url_id`.
    """
    norm = _mapear(datos, _texto_para_clave)
    crudo = json.dumps(norm, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def clave_cache(clave_est: str, pregunta: Pregunta) -> str:
    """La version y el MODELO PEDIDO entran en la clave: cambiar el texto de
    una pregunta (que es subir su version) o el modelo invalida lo guardado."""
    crudo = f"{clave_est}|{pregunta.nombre}|{pregunta.version}|{pregunta.modelo}"
    if pregunta.variante:
        # Opciones de un cliente (decision 178): otras opciones, otra clave.
        crudo += f"|{pregunta.variante}"
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    pregunta: str
    version_pregunta: int
    estado: str  # decidido | incierto | sin_decidir
    origen: str  # modelo | cache | arrastre | regla | humano
    modelo: str  # el pedido (forma la clave)
    respuesta: str | None = None
    probabilidades: dict[str, float] | None = None
    confianza: float | None = None
    modelo_respuesta: str | None = None
    proveedor: str | None = None
    motivo: str | None = None
    tokens_entrada: int | None = None
    coste_usd: float | None = None
    coste_estimado: bool | None = None
    clave: str | None = None


def interpretar(pregunta: Pregunta, respuesta: Any) -> tuple[str | None, dict | None, float | None]:
    """(respuesta, probabilidades, confianza) de una respuesta de `/systemone`.

    La confianza se normaliza porque los tres tipos no la traen igual:
    - `noul` solo devuelve la probabilidad del si (sin `confidence`, en los dos
      SDK): la confianza es `max(p, 1-p)`;
    - `choice` y `score` traen `confidence` y `probabilities` (opcionales en el
      SDK de OpenRouter): se usa `confidence` y, si falta, la probabilidad mas
      alta; si no viene ninguna, None y la decision queda `incierto`.
    """
    if not isinstance(respuesta, dict) or respuesta.get("type") != pregunta.tipo:
        return None, None, None
    if pregunta.tipo == "noul":
        p = respuesta.get("noul")
        if not isinstance(p, (int, float)) or not 0 <= p <= 1:
            return None, None, None
        p = float(p)
        return ("true" if p >= 0.5 else "false"), {"true": p, "false": 1 - p}, max(p, 1 - p)
    probs = respuesta.get("probabilities")
    probs = ({str(k): float(v) for k, v in probs.items() if isinstance(v, (int, float))}
             if isinstance(probs, dict) else None) or None
    conf = respuesta.get("confidence")
    conf = float(conf) if isinstance(conf, (int, float)) else (max(probs.values()) if probs else None)
    if pregunta.tipo == "choice":
        eleccion = respuesta.get("choice")
        return (str(eleccion) if eleccion is not None else None), probs, conf
    score = respuesta.get("score")
    if not isinstance(score, (int, float)):
        return None, probs, conf
    return repr(float(score)), probs, conf


def estado_por_confianza(pregunta: Pregunta, confianza: float | None) -> str:
    """Sin umbral calibrado (#8) nada es `decidido`: una pregunta sin evaluar
    no puede firmar una respuesta, por segura que se declare. Se recalcula al
    servir de cache, para que subir el umbral afecte tambien a lo guardado."""
    if confianza is None or pregunta.umbral is None or confianza < pregunta.umbral:
        return INCIERTO
    return DECIDIDO


# ---------------------------------------------------------------------------
# Cache y contadores (Redis o memoria)
# ---------------------------------------------------------------------------
class Cache(Protocol):
    def obtener(self, claves: list[str]) -> dict[str, dict]: ...
    def guardar(self, clave: str, valor: dict) -> None: ...


class CacheMemoria:
    def __init__(self):
        self.datos: dict[str, dict] = {}

    def obtener(self, claves):
        return {c: self.datos[c] for c in claves if c in self.datos}

    def guardar(self, clave, valor):
        self.datos[clave] = valor


class CacheRedis:
    """`llm:dec:{clave}` con TTL (`LLM_CACHE_TTL_D`, 30 dias). Un fallo de
    Redis es un fallo de cache, no de la decision: se sigue sin ella."""

    def __init__(self, redis, ttl_dias: float | None = None):
        self.r = redis
        self.ttl = int((ttl_dias if ttl_dias is not None
                        else numero_de_entorno("LLM_CACHE_TTL_D", 30.0)) * 86400)

    def obtener(self, claves):
        if not claves:
            return {}
        try:
            valores = self.r.mget([f"llm:dec:{c}" for c in claves])
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache de decisiones en Redis no disponible: %s", exc)
            return {}
        salida = {}
        for c, v in zip(claves, valores):
            if v:
                try:
                    salida[c] = json.loads(v)
                except ValueError:
                    pass
        return salida

    def guardar(self, clave, valor):
        try:
            self.r.set(f"llm:dec:{clave}", json.dumps(valor), ex=self.ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("no se pudo guardar en la cache de Redis: %s", exc)


class Contadores(Protocol):
    def sumar(self, clave: str, valor: float) -> float: ...
    def sembrar(self, clave: str, valor: float) -> None: ...


class ContadoresMemoria:
    def __init__(self):
        self.valores: dict[str, float] = {}

    def sumar(self, clave, valor):
        self.valores[clave] = self.valores.get(clave, 0.0) + valor
        return self.valores[clave]

    def sembrar(self, clave, valor):
        self.valores.setdefault(clave, float(valor))


class ContadoresRedis:
    """`INCRBYFLOAT`: atomico entre replicas y procesos, que es lo que hace
    falta para que dos ejecuciones del mismo cliente no se pasen del tope
    mensual a la vez. Caducan a los 40 dias (el mes y algo de margen)."""

    def __init__(self, redis):
        self.r = redis

    def sumar(self, clave, valor):
        nuevo = float(self.r.incrbyfloat(clave, valor))
        self.r.expire(clave, 40 * 86400)
        return nuevo

    def sembrar(self, clave, valor):
        # NX: si otro proceso ya lo sembro (y ha gastado desde entonces), no se
        # pisa su cuenta con la de la BD, que va por detras.
        self.r.set(clave, float(valor), nx=True, ex=40 * 86400)


# ---------------------------------------------------------------------------
# Presupuesto
# ---------------------------------------------------------------------------
def _mes(ahora: datetime | None = None) -> str:
    return (ahora or datetime.now(timezone.utc)).strftime("%Y-%m")


@dataclass
class Presupuesto:
    """Tres topes en dolares y uno de llamadas. None = sin ese tope.

    - `ejecucion_usd` y `max_llamadas`: los de una ejecucion (#40 los pone en
      el dialogo de lanzamiento). Viven en memoria: una ejecucion es un proceso.
    - `cliente_mensual_usd`: `clients.decisiones.presupuesto_mensual_usd`
      (#36). Con rastreos semanales un tope por ejecucion se multiplica sin
      control; este no.
    - `global_mensual_usd`: `ENRICHMENT_MAX_USD_MES`, la red de seguridad de la
      instalacion entera.
    Los dos mensuales van en `Contadores` (Redis en produccion) para que los
    compartan todas las ejecuciones del mes.
    """

    ejecucion_usd: float | None = None
    max_llamadas: int | None = None
    cliente_mensual_usd: float | None = None
    global_mensual_usd: float | None = None
    cliente_id: str | None = None
    contadores: Contadores = field(default_factory=ContadoresMemoria)
    gastado_ejecucion: float = 0.0
    llamadas: int = 0
    mes: str = field(default_factory=_mes)

    @property
    def clave_cliente(self) -> str | None:
        return f"llm:coste:cliente:{self.cliente_id}:{self.mes}" if self.cliente_id else None

    @property
    def clave_global(self) -> str:
        return f"llm:coste:global:{self.mes}"

    def _mensuales(self) -> list[tuple[str, float, str]]:
        topes = []
        if self.cliente_mensual_usd is not None and self.clave_cliente:
            topes.append((self.clave_cliente, self.cliente_mensual_usd, "presupuesto_cliente"))
        if self.global_mensual_usd is not None:
            topes.append((self.clave_global, self.global_mensual_usd, "presupuesto_global"))
        return topes

    def reservar(self, estimado: float) -> str | None:
        """None si cabe (y queda reservado); si no, el motivo."""
        if self.max_llamadas is not None and self.llamadas >= self.max_llamadas:
            return "max_llamadas"
        if self.ejecucion_usd is not None and self.gastado_ejecucion + estimado > self.ejecucion_usd:
            return "presupuesto_ejecucion"
        aplicados: list[str] = []
        try:
            for clave, tope, motivo in self._mensuales():
                nuevo = self.contadores.sumar(clave, estimado)
                aplicados.append(clave)
                if nuevo > tope:
                    for c in aplicados:
                        self.contadores.sumar(c, -estimado)
                    return motivo
        except Exception as exc:  # noqa: BLE001
            # Sin contadores no se puede garantizar el tope: no se llama.
            for c in aplicados:
                try:
                    self.contadores.sumar(c, -estimado)
                except Exception:  # noqa: BLE001
                    pass
            logger.warning("contadores de presupuesto no disponibles: %s", exc)
            return "presupuesto_sin_contador"
        self.gastado_ejecucion += estimado
        self.llamadas += 1
        return None

    def ajustar(self, reservado: float, real: float) -> None:
        delta = real - reservado
        self.gastado_ejecucion += delta
        for clave, _, _ in self._mensuales():
            try:
                self.contadores.sumar(clave, delta)
            except Exception:  # noqa: BLE001
                pass

    def liberar(self, reservado: float) -> None:
        """La llamada no se llego a cobrar (fallo antes de responder)."""
        self.ajustar(reservado, 0.0)
        self.llamadas = max(0, self.llamadas - 1)

    def sembrar_desde_bd(self, session) -> None:
        """El gasto del mes ya registrado en `decisions`, por si Redis se
        vacio: sin esto un reinicio de Redis regalaria el presupuesto entero."""
        from sqlalchemy import func

        from shared.models import Decision as FilaDecision, Job

        inicio = datetime.strptime(self.mes + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        try:
            total = session.query(func.coalesce(func.sum(FilaDecision.coste_usd), 0.0)).filter(
                FilaDecision.created_at >= inicio).scalar() or 0.0
            self.contadores.sembrar(self.clave_global, float(total))
            if self.clave_cliente:
                del_cliente = (
                    session.query(func.coalesce(func.sum(FilaDecision.coste_usd), 0.0))
                    .join(Job, Job.id == FilaDecision.job_id)
                    .filter(Job.cliente_id == _uuid(self.cliente_id),
                            FilaDecision.created_at >= inicio)
                    .scalar() or 0.0)
                self.contadores.sembrar(self.clave_cliente, float(del_cliente))
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning("no se pudo sembrar el gasto del mes desde la BD: %s", exc)


def _uuid(valor):
    return valor if isinstance(valor, uuid.UUID) else uuid.UUID(str(valor))


def presupuesto_mensual_de_cliente(session, cliente_id) -> float | None:
    from shared.models import Client

    cliente = session.get(Client, _uuid(cliente_id)) if cliente_id else None
    valor = ((cliente.decisiones or {}) if cliente else {}).get("presupuesto_mensual_usd")
    return float(valor) if isinstance(valor, (int, float)) else None


def presupuesto_global_mensual() -> float | None:
    v = (os.getenv("ENRICHMENT_MAX_USD_MES") or "").strip()
    return float(v) if v else None


# ---------------------------------------------------------------------------
# Observabilidad
# ---------------------------------------------------------------------------
# Limites superiores de los cubos de latencia, en ms. Un histograma y no una
# lista de muestras: se puede fusionar EXACTO entre ejecuciones y procesos.
_CUBOS_MS = (50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 30000)
_GRAVEDAD = {"ok": 0, "degradado": 1, "presupuesto_agotado": 2, "desactivado": 3,
             "clave_invalida": 4, "sin_credito": 5}


@dataclass
class Observabilidad:
    llamadas: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    coste_usd: float = 0.0
    llamadas_coste_estimado: int = 0
    aciertos_cache: int = 0
    aciertos_cache_bd: int = 0
    n_429: int = 0
    n_errores: int = 0
    reintentos: int = 0
    latencia_hist: dict[str, int] = field(default_factory=dict)
    por_pregunta: dict[str, dict] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    estado: str = "ok"

    def peor_estado(self, estado: str) -> None:
        if _GRAVEDAD.get(estado, 1) > _GRAVEDAD.get(self.estado, 0):
            self.estado = estado

    def avisar(self, texto: str) -> None:
        if texto not in self.avisos and len(self.avisos) < 50:
            self.avisos.append(texto)

    def latencia(self, ms: float) -> None:
        cubo = next((str(c) for c in _CUBOS_MS if ms <= c), "inf")
        self.latencia_hist[cubo] = self.latencia_hist.get(cubo, 0) + 1

    def contar(self, d: Decision, pregunta: Pregunta) -> None:
        pp = self.por_pregunta.setdefault(
            d.pregunta, {DECIDIDO: 0, INCIERTO: 0, SIN_DECIDIR: 0, "distribucion": {}})
        pp[d.estado] = pp.get(d.estado, 0) + 1
        if d.respuesta is not None:
            etiqueta = d.respuesta
            if pregunta.tipo == "score":
                # El score es un valor esperado (1,7): se agrupa por nivel.
                etiqueta = str(int(round(float(d.respuesta))))
            pp["distribucion"][etiqueta] = pp["distribucion"].get(etiqueta, 0) + 1

    def a_dict(self) -> dict:
        d = asdict(self)
        d["coste_usd"] = round(self.coste_usd, 8)
        d["latencia_p50_ms"] = _percentil(self.latencia_hist, 0.50)
        d["latencia_p95_ms"] = _percentil(self.latencia_hist, 0.95)
        return d


def _percentil(hist: dict[str, int], q: float) -> float | None:
    total = sum(hist.values())
    if not total:
        return None
    acumulado = 0
    for c in [*map(str, _CUBOS_MS), "inf"]:
        acumulado += hist.get(c, 0)
        if acumulado >= q * total:
            return float(c) if c != "inf" else None
    return None


def fusionar_resumen(previo: dict | None, nuevo: dict) -> dict:
    """Suma dos resumenes: los contadores se suman, los histogramas y las
    distribuciones por clave, y el estado se queda con el peor."""
    if not previo:
        return dict(nuevo)
    s = dict(previo)
    for k in ("llamadas", "tokens_entrada", "tokens_salida", "llamadas_coste_estimado",
              "aciertos_cache", "aciertos_cache_bd", "n_429", "n_errores", "reintentos"):
        s[k] = int(previo.get(k) or 0) + int(nuevo.get(k) or 0)
    s["coste_usd"] = round(float(previo.get("coste_usd") or 0) + float(nuevo.get("coste_usd") or 0), 8)
    hist = dict(previo.get("latencia_hist") or {})
    for c, n in (nuevo.get("latencia_hist") or {}).items():
        hist[c] = hist.get(c, 0) + n
    s["latencia_hist"] = hist
    s["latencia_p50_ms"] = _percentil(hist, 0.50)
    s["latencia_p95_ms"] = _percentil(hist, 0.95)
    pp = {k: {**v, "distribucion": dict(v.get("distribucion") or {})}
          for k, v in (previo.get("por_pregunta") or {}).items()}
    for nombre, v in (nuevo.get("por_pregunta") or {}).items():
        dest = pp.setdefault(nombre, {DECIDIDO: 0, INCIERTO: 0, SIN_DECIDIR: 0, "distribucion": {}})
        for e in (DECIDIDO, INCIERTO, SIN_DECIDIR):
            dest[e] = int(dest.get(e) or 0) + int(v.get(e) or 0)
        for r, n in (v.get("distribucion") or {}).items():
            dest["distribucion"][r] = dest["distribucion"].get(r, 0) + n
    s["por_pregunta"] = pp
    s["avisos"] = list(dict.fromkeys([*(previo.get("avisos") or []), *(nuevo.get("avisos") or [])]))[:50]
    a, b = previo.get("estado") or "ok", nuevo.get("estado") or "ok"
    s["estado"] = a if _GRAVEDAD.get(a, 1) >= _GRAVEDAD.get(b, 1) else b
    return s


def guardar_resumen(session, job_id, obs: Observabilidad) -> None:
    """Acumula el resumen en `jobs.decisiones_resumen`. Con bloqueo de fila:
    el spider y el analizador (o dos replicas) pueden volcar a la vez. El
    commit es de quien llama."""
    from shared.models import Job

    job = session.query(Job).filter(Job.id == job_id).with_for_update().one_or_none()
    if job is None:
        return
    job.decisiones_resumen = fusionar_resumen(job.decisiones_resumen, obs.a_dict())


# ---------------------------------------------------------------------------
# La capa
# ---------------------------------------------------------------------------
_DESACTIVADO = "desactivado"


def _valor_cache(d: Decision) -> dict:
    return {"respuesta": d.respuesta, "probabilidades": d.probabilidades,
            "confianza": d.confianza, "modelo_respuesta": d.modelo_respuesta,
            "proveedor": d.proveedor}


def _partir(pendientes: list[Pregunta]) -> list[list[Pregunta]]:
    """Las llamadas que hacen falta para las preguntas pendientes de un estado.

    Las de un estado van juntas (decision 84: el estado se paga una vez), con
    dos excepciones. Una llamada va a UN modelo: si el catalogo mezclara
    modelos se parte por modelo. Y una pregunta con `solo_si` va SOLA (decision
    175): su instruccion da por hecha la respuesta de otra («son sustitutas»),
    y leida junto a ella la empujaria; ademas la evaluacion (#8) pregunta una
    a una, y lo calibrado tiene que ser el prompt que corre en produccion. Asi
    la clave de cache, que no depende de las acompañantes, sigue siendo
    valida: lo que se juntaria no cambia lo que se responde.
    """
    grupos: list[list[Pregunta]] = []
    por_modelo: dict[str, list[Pregunta]] = {}
    for p in pendientes:
        if p.solo_si is not None:
            grupos.append([p])
        else:
            por_modelo.setdefault(p.modelo, []).append(p)
    return [*por_modelo.values(), *grupos]


class CapaDecisiones:
    """Una por ejecucion: el presupuesto, la observabilidad y el corte son
    suyos. `fabrica_cliente=None` = capa desactivada (sin clave o con
    `DECISIONS_ENABLED=0`): todo sale `sin_decidir` sin tocar la red."""

    def __init__(self, fabrica_cliente: Callable[[], ClienteOpenRouter] | None, *,
                 cache: Cache | None = None,
                 sesion_factory: Callable[[], Any] | None = None,
                 presupuesto: Presupuesto | None = None,
                 obs: Observabilidad | None = None,
                 job_id=None, run_id=None, usuario: str | None = None,
                 precio_usd_mtok: float | None = None,
                 concurrencia: int | None = None,
                 motivo_desactivada: str = _DESACTIVADO):
        self._fabrica = fabrica_cliente
        self._cliente: ClienteOpenRouter | None = None
        self.cache = cache or CacheMemoria()
        self._memoria = CacheMemoria()
        self.sesion_factory = sesion_factory
        self.presupuesto = presupuesto or Presupuesto()
        self.obs = obs or Observabilidad()
        self.job_id = job_id
        self.run_id = run_id
        self.usuario = usuario
        self.precio = (precio_usd_mtok if precio_usd_mtok is not None
                       else numero_de_entorno("JEV_PRECIO_USD_MTOK", PRECIO_USD_MTOK_DEFECTO))
        # Motivo por el que ya no se llama (sin_credito, clave_invalida...).
        self.cortada: str | None = None if fabrica_cliente else motivo_desactivada
        # Cuanto se ha quedado corta la estimacion por precio frente al coste
        # real (nunca por debajo de 1). Sin esto la reserva solo sabe del
        # precio publicado y, si OpenRouter cobra mas (un recargo, otro
        # proveedor), el tope se pasaria por una llamada entera cada vez.
        self._factor_coste = 1.0
        # Llamadas en vuelo. La reserva se hace DENTRO de este cupo y no al
        # lanzar las corrutinas: si no, con `gather` todas reservan a la vez
        # con la estimacion inicial antes de que vuelva ninguna, y el tope se
        # pasa por tantas llamadas como estados. Asi el exceso maximo es el
        # error de estimacion de `concurrencia` llamadas.
        self.concurrencia = max(1, concurrencia or numero_de_entorno("LLM_MAX_CONCURRENCIA", 16, int))
        self._cupo: tuple[Any, asyncio.Semaphore] | None = None
        if fabrica_cliente is None:
            self.obs.peor_estado(_DESACTIVADO)

    # -- estimacion sin llamar (#40) --------------------------------------
    def _tokens_estimados(self, cuerpo: Any) -> int:
        # Medido en la primera llamada real (24-oct-2026, 150 paginas de Lopesan,
        # decision 162): tokens = 0,247 x caracteres + 246. Hay un coste FIJO
        # por llamada que el proveedor anade; con «caracteres / 3» la
        # estimacion se quedaba un 34 % corta y el coste real fue un 52 % mayor
        # que lo que enseño el dialogo de #40. Con 4 y 250, un pelo por encima
        # (error medio del 2,8 %), que es el lado bueno para una RESERVA.
        return max(1, math.ceil(len(json.dumps(cuerpo, ensure_ascii=False)) / 4) + TOKENS_FIJOS_POR_LLAMADA)

    def _coste(self, tokens_entrada: int) -> float:
        return tokens_entrada * self.precio / 1_000_000

    def _reserva(self, cuerpo: dict) -> float:
        return self._coste(self._tokens_estimados(cuerpo)) * self._factor_coste

    def _plan(self, estados: list[Estado], preguntas: list[Pregunta]):
        """Por estado distinto: su clave, lo que hay en cache y lo que falta."""
        claves_est = [clave_estado(e.datos) for e in estados]
        todas = {(ce, p.nombre): clave_cache(ce, p) for ce in set(claves_est) for p in preguntas}
        encontrado: dict[str, tuple[dict, str]] = {}
        for c, v in self._memoria.obtener(list(todas.values())).items():
            encontrado[c] = (v, "memoria")
        faltan = [c for c in todas.values() if c not in encontrado]
        for c, v in self.cache.obtener(faltan).items():
            encontrado[c] = (v, "redis")
        faltan = [c for c in todas.values() if c not in encontrado]
        for c, v in self._buscar_en_bd(claves_est, preguntas, set(faltan)).items():
            encontrado[c] = (v, "bd")
        return claves_est, todas, encontrado

    def estimar(self, estados: list[Estado], preguntas: list[Pregunta]) -> dict:
        """Cuanto costaria preguntar esto ahora, SIN llamar a la API: lo que
        enseña el dialogo de #40 antes de confirmar."""
        claves_est, todas, encontrado = self._plan(estados, preguntas)
        primero = {}
        for e, ce in zip(estados, claves_est):
            primero.setdefault(ce, e)
        llamadas = tokens = 0
        for ce, e in primero.items():
            pendientes = [p for p in preguntas if todas[(ce, p.nombre)] not in encontrado]
            for grupo in _partir(pendientes):
                llamadas += 1
                tokens += self._tokens_estimados(self._cuerpo(e, grupo))
        return {"estados": len(estados), "estados_distintos": len(primero),
                "preguntas": len(preguntas), "aciertos_cache": len(encontrado),
                "llamadas": llamadas, "tokens_estimados": tokens,
                "coste_estimado_usd": round(self._coste(tokens), 8)}

    # -- cache en BD -------------------------------------------------------
    def _buscar_en_bd(self, claves_est, preguntas, buscadas: set[str]) -> dict[str, dict]:
        if not self.sesion_factory or not buscadas:
            return {}
        from shared.models import Decision as FilaDecision

        por_nombre = {p.nombre: p for p in preguntas}
        distintas = sorted(set(claves_est))
        salida: dict[str, dict] = {}
        session = self.sesion_factory()
        try:
            for i in range(0, len(distintas), 500):
                filas = (
                    session.query(FilaDecision)
                    .filter(FilaDecision.clave.in_(distintas[i:i + 500]),
                            FilaDecision.pregunta.in_(list(por_nombre)),
                            FilaDecision.estado != SIN_DECIDIR)
                    .order_by(FilaDecision.id.desc()).all())
                for f in filas:
                    p = por_nombre[f.pregunta]
                    if f.version_pregunta != p.version or f.modelo != p.modelo:
                        continue
                    # La fila no guarda la variante: una respuesta elegida entre
                    # otras opciones (la taxonomia de antes) no vale (decision 178).
                    if p.variante and set(f.probabilidades or {}) != set(p.opciones):
                        continue
                    c = clave_cache(f.clave, p)
                    if c in buscadas and c not in salida:
                        salida[c] = {"respuesta": f.respuesta, "probabilidades": f.probabilidades,
                                     "confianza": f.confianza, "modelo_respuesta": f.modelo_respuesta,
                                     "proveedor": f.proveedor}
        except Exception as exc:  # noqa: BLE001
            # Durante el primer minuto de un despliegue la tabla puede no
            # existir aun (init_db corre despues de `up -d`): es una cache que
            # falla, no una decision que falla.
            logger.warning("cache de decisiones en BD no disponible: %s", exc)
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        finally:
            session.close()
        return salida

    # -- llamada -------------------------------------------------------------
    def _cuerpo(self, estado: Estado, preguntas: list[Pregunta]) -> dict:
        return {"model": preguntas[0].modelo, "state": normalizar_estado(estado.datos),
                "questions": {p.nombre: p.a_api() for p in preguntas}}

    def _sin_decidir(self, p: Pregunta, motivo: str, clave: str) -> Decision:
        return Decision(pregunta=p.nombre, version_pregunta=p.version, estado=SIN_DECIDIR,
                        origen="modelo", modelo=p.modelo, motivo=motivo, clave=clave)

    def _cortar(self, motivo: str) -> None:
        if self.cortada is None:
            self.cortada = motivo
            estado = {"sin_credito": "sin_credito", "clave_invalida": "clave_invalida",
                      "modelo_inexistente": "clave_invalida"}.get(motivo)
            if motivo.startswith("presupuesto") or motivo == "max_llamadas":
                estado = "presupuesto_agotado"
            self.obs.peor_estado(estado or "degradado")
            self.obs.avisar(f"capa de decisiones cortada: {motivo}")
            logger.warning("decisiones: se deja de llamar a la API (%s)", motivo)

    async def _llamar(self, cliente: ClienteOpenRouter, estado: Estado, clave_est: str,
                      pendientes: list[Pregunta]) -> list[Decision]:
        salida: list[Decision] = []
        for grupo in _partir(pendientes):
            salida.extend(await self._llamar_un_modelo(cliente, estado, clave_est, grupo))
        return salida

    def _semaforo(self) -> asyncio.Semaphore:
        bucle = asyncio.get_running_loop()
        if self._cupo is None or self._cupo[0] is not bucle:
            self._cupo = (bucle, asyncio.Semaphore(self.concurrencia))
        return self._cupo[1]

    async def _llamar_un_modelo(self, cliente, estado, clave_est, grupo) -> list[Decision]:
        async with self._semaforo():
            return await self._llamar_en_cupo(cliente, estado, clave_est, grupo)

    async def _llamar_en_cupo(self, cliente, estado, clave_est, grupo) -> list[Decision]:
        if self.cortada:
            return [self._sin_decidir(p, self.cortada, clave_est) for p in grupo]
        cuerpo = self._cuerpo(estado, grupo)
        reserva = self._reserva(cuerpo)
        motivo = self.presupuesto.reservar(reserva)
        if motivo:
            self._cortar(motivo)
            return [self._sin_decidir(p, motivo, clave_est) for p in grupo]
        try:
            resp = await cliente.systemone(
                cuerpo["state"], cuerpo["questions"], cuerpo["model"],
                session_id=str(self.job_id) if self.job_id else None, user=self.usuario)
        except ErrorLLM as exc:
            self.presupuesto.liberar(reserva)
            # intentos=0: no llego a salir (capa ya cortada por otra llamada).
            self.obs.n_errores += int(exc.intentos > 0)
            self.obs.n_429 += exc.n_429
            self.obs.reintentos += max(0, exc.intentos - 1)
            self.obs.peor_estado("degradado")
            self.obs.avisar(f"{exc.motivo}: {str(exc)[:160]}")
            if exc.corta_capa:
                self._cortar(exc.motivo)
            return [self._sin_decidir(p, exc.motivo, clave_est) for p in grupo]
        except Exception as exc:  # noqa: BLE001 - nada de la capa tumba un censo
            self.presupuesto.liberar(reserva)
            self.obs.n_errores += 1
            self.obs.peor_estado("degradado")
            self.obs.avisar(f"error inesperado: {type(exc).__name__}: {str(exc)[:160]}")
            logger.exception("decisiones: error inesperado llamando a la API")
            return [self._sin_decidir(p, "error", clave_est) for p in grupo]

        uso = resp.uso
        estimado = uso.coste_usd is None
        coste = self._coste(uso.tokens_entrada) if estimado else uso.coste_usd
        self.presupuesto.ajustar(reserva, coste)
        base = self._coste(self._tokens_estimados(cuerpo))
        if base > 0 and coste > 0:
            self._factor_coste = max(self._factor_coste, coste / base)
        o = self.obs
        o.llamadas += 1
        o.tokens_entrada += uso.tokens_entrada
        o.tokens_salida += uso.tokens_salida
        o.coste_usd += coste
        o.llamadas_coste_estimado += int(estimado)
        o.n_429 += resp.n_429
        o.reintentos += max(0, resp.intentos - 1)
        o.latencia(resp.latencia_ms)

        respuestas = resp.datos.get("answers") or {}
        n = len(grupo)
        salida = []
        for p in grupo:
            r, probs, conf = interpretar(p, respuestas.get(p.nombre))
            if r is None:
                d = self._sin_decidir(p, "respuesta_incompleta", clave_est)
                self.obs.peor_estado("degradado")
                self.obs.avisar(f"respuesta sin '{p.nombre}' o de otro tipo")
            else:
                d = Decision(pregunta=p.nombre, version_pregunta=p.version,
                             estado=estado_por_confianza(p, conf), origen="modelo",
                             modelo=p.modelo, respuesta=r, probabilidades=probs,
                             confianza=conf, modelo_respuesta=uso.modelo,
                             proveedor=uso.proveedor, clave=clave_est)
                valor = _valor_cache(d)
                cc = clave_cache(clave_est, p)
                self._memoria.guardar(cc, valor)
                self.cache.guardar(cc, valor)
            d.tokens_entrada = round(uso.tokens_entrada / n)
            d.coste_usd = coste / n
            d.coste_estimado = estimado
            salida.append(d)
        return salida

    async def preguntar(self, estados: list[Estado],
                        preguntas: list[Pregunta]) -> list[list[Decision]]:
        """Por cada estado, una `Decision` por pregunta y en el mismo orden.
        No lanza: lo que no se pueda decidir sale `sin_decidir` con su motivo."""
        if not estados or not preguntas:
            return [[] for _ in estados]
        if self.cortada == _DESACTIVADO:
            resultado = [[self._sin_decidir(p, _DESACTIVADO, clave_estado(e.datos))
                          for p in preguntas] for e in estados]
            self._contar(resultado, preguntas)
            return resultado

        claves_est, todas, encontrado = self._plan(estados, preguntas)
        por_clave: dict[str, dict[str, Decision]] = {}
        pendientes: dict[str, tuple[Estado, list[Pregunta]]] = {}
        for e, ce in zip(estados, claves_est):
            if ce in por_clave or ce in pendientes:
                continue  # estado repetido: se decide una vez
            por_clave[ce] = {}
            faltan = []
            for p in preguntas:
                hit = encontrado.get(todas[(ce, p.nombre)])
                if hit is None:
                    faltan.append(p)
                    continue
                valor, donde = hit
                self.obs.aciertos_cache += 1
                self.obs.aciertos_cache_bd += int(donde == "bd")
                if donde != "memoria":
                    self._memoria.guardar(todas[(ce, p.nombre)], valor)
                por_clave[ce][p.nombre] = Decision(
                    pregunta=p.nombre, version_pregunta=p.version,
                    estado=estado_por_confianza(p, valor.get("confianza")), origen="cache",
                    modelo=p.modelo, respuesta=valor.get("respuesta"),
                    probabilidades=valor.get("probabilidades"), confianza=valor.get("confianza"),
                    modelo_respuesta=valor.get("modelo_respuesta"),
                    proveedor=valor.get("proveedor"), tokens_entrada=0, coste_usd=0.0,
                    coste_estimado=False, clave=ce)
            if faltan:
                pendientes[ce] = (e, faltan)

        if pendientes:
            cliente = self._obtener_cliente()
            resultados = await asyncio.gather(
                *(self._llamar(cliente, e, ce, faltan) for ce, (e, faltan) in pendientes.items()))
            for ce, decisiones in zip(pendientes, resultados):
                for d in decisiones:
                    por_clave[ce][d.pregunta] = d

        resultado = []
        pagadas: set[tuple[str, str]] = set()
        for ce in claves_est:
            fila = []
            for p in preguntas:
                d = por_clave[ce][p.nombre]
                # Copia: dos URLs con el mismo estado comparten respuesta pero
                # no la fila de `decisions`. El coste, SOLO en la primera: se
                # pago una llamada, y con el coste en cada copia la suma de
                # `decisions` (de la que sale el gasto del mes al arrancar)
                # contaba el mismo pago tantas veces como URLs (decision 162).
                copia = Decision(**asdict(d))
                if (ce, p.nombre) in pagadas:
                    copia.coste_usd = 0.0 if d.coste_usd is not None else None
                    copia.tokens_entrada = 0 if d.tokens_entrada is not None else None
                pagadas.add((ce, p.nombre))
                fila.append(copia)
            resultado.append(fila)
        self._contar(resultado, preguntas)
        return resultado

    def _contar(self, resultado, preguntas) -> None:
        por_nombre = {p.nombre: p for p in preguntas}
        for fila in resultado:
            for d in fila:
                self.obs.contar(d, por_nombre[d.pregunta])

    def _obtener_cliente(self) -> ClienteOpenRouter:
        if self._cliente is None:
            self._cliente = self._fabrica()
        return self._cliente

    async def cerrar(self) -> None:
        if self._cliente is not None:
            await self._cliente.cerrar()
            self._cliente = None

    def preguntar_sync(self, estados, preguntas) -> list[list[Decision]]:
        """Para el analizador, que corre en un hilo del worker sin bucle de
        eventos. Cliente nuevo por llamada (sus primitivas asyncio son de UN
        bucle); el corte, el presupuesto y la cache sobreviven entre llamadas.
        Conviene llamarlo con TODOS los estados de una vez."""
        async def _una():
            try:
                return await self.preguntar(estados, preguntas)
            finally:
                await self.cerrar()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_una())
        raise RuntimeError("preguntar_sync dentro de un bucle de eventos: usa `await preguntar`")

    # -- persistencia ------------------------------------------------------
    def filas(self, estados: list[Estado], resultados: list[list[Decision]],
              guardar_estado: bool = True) -> list:
        """Filas de `decisions`. Se escriben DESPUES de llamar, con una sesion
        corta: nada de tener una transaccion abierta mientras se espera a la API."""
        from shared.models import Decision as FilaDecision

        filas = []
        for e, fila in zip(estados, resultados):
            enviado = normalizar_estado(e.datos) if guardar_estado else None
            for d in fila:
                filas.append(FilaDecision(
                    job_id=self.job_id, url_id=e.url_id, run_id=self.run_id, ambito=e.ambito,
                    clave=d.clave or clave_estado(e.datos), pregunta=d.pregunta,
                    version_pregunta=d.version_pregunta, estado=d.estado, origen=d.origen,
                    respuesta=d.respuesta, probabilidades=d.probabilidades,
                    confianza=d.confianza, modelo=d.modelo, modelo_respuesta=d.modelo_respuesta,
                    proveedor=d.proveedor, tokens_entrada=d.tokens_entrada,
                    coste_usd=d.coste_usd, coste_estimado=d.coste_estimado,
                    motivo=d.motivo, estado_enviado=enviado))
        return filas

    def guardar(self, session, estados, resultados) -> int:
        """Escribe las decisiones y acumula el resumen del job. Commit de quien llama."""
        filas = self.filas(estados, resultados)
        session.add_all(filas)
        if self.job_id is not None:
            guardar_resumen(session, self.job_id, self.obs)
            # Lo volcado no se vuelve a sumar en el siguiente `guardar` de la
            # misma capa; el estado (cortada, degradada) si se conserva.
            self.obs = Observabilidad(estado=self.obs.estado)
        return len(filas)


def decisiones_activadas() -> bool:
    return os.getenv("DECISIONS_ENABLED", "0") == "1"


def capa_desde_entorno(*, job_id=None, run_id=None, cliente_id=None, usuario: str | None = None,
                       sesion_factory=None, redis=None, presupuesto_ejecucion_usd=None,
                       max_llamadas=None, transport=None) -> CapaDecisiones:
    """La capa tal como la montaria #40. Desactivada (todo `sin_decidir`, sin
    red) si `DECISIONS_ENABLED` no es 1 o falta `OPENROUTER_API_KEY`.

    `usuario` es el SLUG del cliente, no su nombre: viaja a OpenRouter como
    `user` para agrupar el gasto, y un nombre comercial no tiene por que.
    """
    config = ConfigLLM.desde_entorno()
    if not decisiones_activadas() or config is None:
        motivo = _DESACTIVADO
        if decisiones_activadas() and config is None:
            logger.warning("DECISIONS_ENABLED=1 pero falta OPENROUTER_API_KEY: capa desactivada")
        return CapaDecisiones(None, job_id=job_id, run_id=run_id, motivo_desactivada=motivo)

    contadores: Contadores = ContadoresRedis(redis) if redis is not None else ContadoresMemoria()
    presupuesto = Presupuesto(
        ejecucion_usd=presupuesto_ejecucion_usd, max_llamadas=max_llamadas,
        global_mensual_usd=presupuesto_global_mensual(),
        cliente_id=str(cliente_id) if cliente_id else None, contadores=contadores)
    if sesion_factory is not None:
        s = sesion_factory()
        try:
            presupuesto.cliente_mensual_usd = presupuesto_mensual_de_cliente(s, cliente_id)
            presupuesto.sembrar_desde_bd(s)
        finally:
            s.close()
    return CapaDecisiones(
        lambda: ClienteOpenRouter(config, transport=transport),
        concurrencia=config.concurrencia,
        cache=CacheRedis(redis) if redis is not None else CacheMemoria(),
        sesion_factory=sesion_factory, presupuesto=presupuesto,
        job_id=job_id, run_id=run_id, usuario=usuario)


async def preguntar(estados: Iterable[Estado], preguntas: Iterable[Pregunta], *,
                    capa: CapaDecisiones | None = None) -> list[list[Decision]]:
    """Atajo: con `capa=None` se monta desde el entorno (y, sin clave o sin
    `DECISIONS_ENABLED=1`, todo sale `sin_decidir`)."""
    capa = capa or capa_desde_entorno()
    try:
        return await capa.preguntar(list(estados), list(preguntas))
    finally:
        await capa.cerrar()


__all__ = [
    "Estado", "Decision", "CapaDecisiones", "Presupuesto", "Observabilidad",
    "CacheMemoria", "CacheRedis", "ContadoresMemoria", "ContadoresRedis",
    "preguntar", "capa_desde_entorno", "clave_estado", "clave_cache",
    "normalizar_estado", "interpretar", "fusionar_resumen", "guardar_resumen",
    "DECIDIDO", "INCIERTO", "SIN_DECIDIR",
]
