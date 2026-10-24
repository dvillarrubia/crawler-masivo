"""Analisis enriquecido bajo demanda (#40, decision 85).

Lo unico que dispara la capa de decisiones (`shared/decisiones.py`) despues de
un rastreo. Tres piezas:

- **Modulos.** Un modulo es un conjunto de preguntas del catalogo
  (`shared/preguntas/`) mas la forma de construir el estado de cada URL. Hoy
  solo existe uno, el de la pregunta de ejemplo de #7: los de verdad (tipo de
  pagina, soft 404, intencion...) son #9 y se evaluan en #8. Cada modulo
  declara de que otros depende y `resolver_modulos` los anade solos.
- **Estimacion.** `estimar()` dice cuanto costaria SIN llamar a nadie: lo que
  ya esta en cache (memoria, Redis o la tabla `decisions`) no cuenta.
- **Ejecucion.** `ejecutar()` la corre el worker, sacando el id de la cola
  `enrichment:pending`; nunca la API dentro de un request. Por lotes: entre
  lote y lote mira si se ha pedido cancelar y anota progreso, gasto y latido.
  Reanudar es idempotente: se saltan las URLs que ya tienen fila en
  `decisions` con este `run_id`, y lo que se pague otra vez sale de cache.

Ningun rastreo ni analisis importa este modulo; el worker lo importa solo
dentro del manejador de la cola de enriquecido (lo vigila
`tests/test_decisiones_no_se_disparan.py`).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from shared.decisiones import (
    SIN_DECIDIR, CacheMemoria, CacheRedis, CapaDecisiones, Estado, capa_desde_entorno,
    guardar_resumen, presupuesto_global_mensual, presupuesto_mensual_de_cliente,
)
from shared.llm import numero_de_entorno
from shared.preguntas import Pregunta, obtener

logger = logging.getLogger(__name__)

# Estados de una ejecucion
PENDIENTE, EN_CURSO, CANCELANDO = "pendiente", "en_curso", "cancelando"
COMPLETADA, PARCIAL, CANCELADA, FALLIDA = "completada", "parcial", "cancelada", "fallida"
ACTIVOS = (PENDIENTE, EN_CURSO, CANCELANDO)
TERMINALES = (COMPLETADA, PARCIAL, CANCELADA, FALLIDA)

# Lo que el modo muestra coge si no se dice cuanto: suficiente para ver si
# las respuestas tienen sentido, barato para tirarlo si no.
MUESTRA_DEFECTO = 500
# Cuanto texto del contenido principal entra en el estado. El estado es
# MINIMO (decision 84): titulo, h1 y el principio del contenido, nunca el HTML.
CARACTERES_CONTENIDO = 600


# ---------------------------------------------------------------------------
# Modulos
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Modulo:
    nombre: str
    titulo: str
    descripcion: str
    # Nombres del catalogo, no objetos: el catalogo es quien manda en la
    # version y el modelo, y un modulo no puede tener una copia distinta.
    preguntas: tuple[str, ...]
    # (session, url_ids) -> un Estado por URL, en el mismo orden.
    construir_estados: Callable[[Any, list[int]], list[Estado]]
    depende_de: tuple[str, ...] = ()
    # Los de ejemplo se ven en el dialogo marcados como tal: sus respuestas
    # no estan evaluadas (#8) y no deben acabar en un informe.
    ejemplo: bool = False
    # (session, job_id, url_ids del alcance) -> las que este modulo pregunta.
    # El prefiltro barato: soft 404 solo sobre los candidatos de la decision
    # 105, no sobre todo el censo. None = todas las del alcance.
    filtrar_urls: Callable[[Any, Any, list[int]], list[int]] | None = None

    def objetos_pregunta(self) -> list[Pregunta]:
        return [obtener(n) for n in self.preguntas]


def _trozos(lista: list, n: int = 500):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def estados_titulo_h1_contenido(session, url_ids: list[int]) -> list[Estado]:
    """Titulo, primer h1 que se pinta y el principio del contenido principal.

    El h1 oculto no cuenta (decision 46: los clones movil/escritorio no son
    titulares), y el contenido es `page_content.content_text`, sin plantilla:
    con el body entero el menu y el pie le dirian al modelo lo mismo en
    todas las paginas.
    """
    from sqlalchemy import func

    from shared.models import Heading, HtmlMeta, PageContent

    titulos: dict[int, str] = {}
    h1: dict[int, str] = {}
    contenido: dict[int, str] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, t in session.query(HtmlMeta.url_id, HtmlMeta.title).filter(
                HtmlMeta.url_id.in_(trozo)):
            titulos[uid] = t or ""
        for uid, texto in (session.query(Heading.url_id, Heading.text)
                           .filter(Heading.url_id.in_(trozo), Heading.tag == "h1",
                                   Heading.oculto.isnot(True))
                           .order_by(Heading.url_id, Heading.position)):
            h1.setdefault(uid, texto or "")
        for uid, c in session.query(
                PageContent.url_id,
                func.substr(PageContent.content_text, 1, CARACTERES_CONTENIDO)).filter(
                PageContent.url_id.in_(trozo)):
            contenido[uid] = c or ""
    return [Estado(datos={"titulo": titulos.get(u, ""), "h1": h1.get(u, ""),
                          "contenido": contenido.get(u, "")}, url_id=u)
            for u in url_ids]


def estados_soft404(session, url_ids: list[int]) -> list[Estado]:
    """Lo de `estados_titulo_h1_contenido` y, ademas, la ruta y las palabras:
    `/agotado/` o una pagina de 12 palabras dicen mucho de si es un error."""
    from shared.models import Url

    base = estados_titulo_h1_contenido(session, url_ids)
    datos = {}
    for trozo in _trozos(list(url_ids)):
        for uid, path, palabras in session.query(Url.id, Url.path, Url.content_word_count).filter(
                Url.id.in_(trozo)):
            datos[uid] = (path or "/", palabras)
    for e in base:
        ruta, palabras = datos.get(e.url_id, ("/", None))
        e.datos = {"ruta": ruta, "palabras": palabras, **e.datos}
    return base


# Por debajo de estas palabras de contenido propio una pagina indexable es
# sospechosa aunque la version sin modelo no la marque. Medido el 24-oct-2026:
# 17 de 3.216 indexables en Lopesan y 47 de 1.080 en Saunier Duval.
SOFT404_POCAS_PALABRAS = 50


def candidatos_soft404(session, job_id, url_ids: list[int]) -> list[int]:
    """De las URLs del alcance, las sospechosas de soft 404: las que la version
    determinista dejo como candidatas (decision 105) y las que tienen menos de
    `SOFT404_POCAS_PALABRAS` palabras propias. Las afirmadas (`soft_404`) no se
    preguntan: ya estan decididas sin modelo. En produccion la determinista
    solo dejaba UN candidato: sin las de poco contenido, el modulo no tenia
    nada que hacer."""
    from shared.models import Issue, Url

    if not url_ids:
        return []
    sospechosas: set[int] = set()
    afirmadas: set[int] = set()
    for trozo in _trozos(list(url_ids)):
        for uid, tipo in session.query(Issue.url_id, Issue.issue_type).filter(
                Issue.job_id == _uuid(job_id), Issue.issue_type.in_(("soft_404_candidato", "soft_404")),
                Issue.url_id.in_(trozo)):
            (sospechosas if tipo == "soft_404_candidato" else afirmadas).add(uid)
        sospechosas |= {uid for (uid,) in session.query(Url.id).filter(
            Url.id.in_(trozo), Url.content_word_count.isnot(None),
            Url.content_word_count < SOFT404_POCAS_PALABRAS)}
    return [u for u in url_ids if u in sospechosas and u not in afirmadas]


def _registro_inicial() -> dict[str, Modulo]:
    # El unico modulo que hay: la pregunta de ejemplo de #7. Los de verdad
    # (#11 tipo de pagina, #12 soft 404...) llegan con su pregunta evaluada.
    modulos = [
        Modulo(
            nombre="pagina_legal",
            titulo="Pagina legal (ejemplo)",
            descripcion=("Pregunta de ejemplo de #7: si la pagina es un aviso legal, "
                         "privacidad, cookies o condiciones. Sin evaluar (#8): sirve "
                         "para probar el circuito, no para decidir nada."),
            preguntas=("es_pagina_legal",),
            construir_estados=estados_titulo_h1_contenido,
            ejemplo=True,
        ),
        Modulo(
            nombre="soft_404",
            titulo="Soft 404 (candidatos)",
            descripcion=("Si la pagina comunica un error, un producto que ya no esta, una busqueda "
                         "vacia o un «proximamente» aunque responda 200. Solo sobre las sospechosas: "
                         "los candidatos de la version sin modelo (decision 105) y las de menos de "
                         "50 palabras. Sin umbral (decision 169): las respuestas salen inciertas y no "
                         "van a ningun informe."),
            preguntas=("es_soft_404",),
            construir_estados=estados_soft404,
            filtrar_urls=candidatos_soft404,
            # Sin umbral (#8, decision 169): se ve marcado en el dialogo y nada
            # suyo va a un informe. Pasa a False el dia que su pregunta tenga umbral.
            ejemplo=True,
        ),
        Modulo(
            nombre="pagina_de_utilidad",
            titulo="Paginas de utilidad (candidatos)",
            descripcion=("Si la pagina es un paso de un proceso sin valor de busqueda: login, "
                         "registro, gracias o confirmacion, baja, check-in, carrito. Sobre los mismos "
                         "sospechosos que el soft 404 (decision 169). Sin evaluar (#8): las respuestas "
                         "salen inciertas y no van a ningun informe."),
            preguntas=("es_pagina_de_utilidad",),
            construir_estados=estados_soft404,
            filtrar_urls=candidatos_soft404,
            ejemplo=True,
        ),
    ]
    modulos += _modulos_coherencia()  # decision 177 (#16, #17)
    return {m.nombre: m for m in modulos}


def _modulos_coherencia() -> list[Modulo]:
    """Title frente a contenido, title generico y marcado frente a lo visible
    (#16, #17; decision 177). Filtros y estados en `shared/coherencia.py`. Sin
    umbral: marcados como sin evaluar, todo sale incierto."""
    from shared import coherencia as c

    return [
        Modulo(
            nombre="title_contenido",
            titulo="Title frente al contenido",
            descripcion=("Si el title dice de que va el contenido (score de 4 niveles). Sin las que "
                         "ya resuelve una regla (sin title, placeholder, duplicado exacto) ni las de "
                         "menos de 50 palabras; hasta 100 por plantilla. Sin evaluar (#8): las "
                         "respuestas salen inciertas y no van a ningun informe."),
            preguntas=("title_describe_contenido",),
            construir_estados=c.estados_title_contenido,
            filtrar_urls=c.candidatos_title_contenido,
            ejemplo=True,
        ),
        Modulo(
            nombre="title_generico",
            titulo="Title generico",
            descripcion=("Si el title, quitada la marca, no identifica la pagina («Detalle noticia», "
                         "«SAT»). Solo titles de 4 palabras o menos sin la marca, sin la portada y sin "
                         "los placeholders que ya dice la regla; hasta 50 titles distintos por "
                         "plantilla. Sin evaluar (#8): las respuestas salen inciertas."),
            preguntas=("title_es_generico",),
            construir_estados=c.estados_title_generico,
            filtrar_urls=c.candidatos_title_generico,
            ejemplo=True,
        ),
        Modulo(
            nombre="schema_visible",
            titulo="Datos estructurados frente a lo visible",
            descripcion=("Si el texto visible contradice un precio, valoracion, fecha de un evento "
                         "u oferta o la disponibilidad del marcado. Solo paginas con algo visible con lo que "
                         "comparar; hasta 10 por plantilla. Que un valor no se vea no se afirma. Sin "
                         "evaluar (#8): las respuestas salen inciertas."),
            preguntas=("schema_contradice_contenido",),
            construir_estados=c.estados_schema,
            filtrar_urls=c.candidatos_schema,
            ejemplo=True,
        ),
    ]


REGISTRO: dict[str, Modulo] = _registro_inicial()


class ModuloDesconocido(ValueError):
    pass


def resolver_modulos(nombres: list[str], registro: dict[str, Modulo] | None = None
                     ) -> tuple[list[str], list[str]]:
    """(orden de ejecucion con las dependencias, las que se han anadido solas).

    Las dependencias van antes que quien las usa: la intencion de #39 necesita
    el tipo de pagina YA decidido. Un ciclo es un error de quien escribio los
    modulos, y se dice en vez de colgarse.
    """
    reg = REGISTRO if registro is None else registro
    orden: list[str] = []
    visitando: set[str] = set()

    def visitar(n: str, camino: tuple[str, ...]):
        if n not in reg:
            raise ModuloDesconocido(f"modulo desconocido: {n!r}")
        if n in orden:
            return
        if n in visitando:
            raise ValueError(f"dependencia circular: {' -> '.join((*camino, n))}")
        visitando.add(n)
        for d in reg[n].depende_de:
            visitar(d, (*camino, n))
        visitando.discard(n)
        orden.append(n)

    pedidos = list(dict.fromkeys(nombres))
    for n in pedidos:
        visitar(n, ())
    return orden, [n for n in orden if n not in pedidos]


def describir_modulos(registro: dict[str, Modulo] | None = None) -> list[dict]:
    reg = REGISTRO if registro is None else registro
    return [{"nombre": m.nombre, "titulo": m.titulo, "descripcion": m.descripcion,
             "preguntas": list(m.preguntas), "depende_de": list(m.depende_de),
             "ejemplo": m.ejemplo} for m in reg.values()]


# ---------------------------------------------------------------------------
# Alcance
# ---------------------------------------------------------------------------
def normalizar_alcance(alcance: dict | None) -> dict:
    a = dict(alcance or {})
    carpeta = (a.get("carpeta") or "").strip() or None
    if carpeta and not carpeta.startswith("/"):
        carpeta = "/" + carpeta
    max_urls = a.get("max_urls")
    max_urls = int(max_urls) if max_urls not in (None, "", 0) else None
    muestra = bool(a.get("muestra"))
    if muestra and max_urls is None:
        max_urls = MUESTRA_DEFECTO
    return {"carpeta": carpeta, "max_urls": max_urls, "muestra": muestra}


def urls_del_alcance(session, job_id, alcance: dict | None) -> list[int]:
    """Por defecto, las URLs internas HTML, 200 e indexables: lo que Google
    puede posicionar. Una redireccion, un 404 o una canonicalizada no tienen
    nada que decidir y cuestan lo mismo.

    Con `max_urls` sin muestra salen las primeras por orden de rastreo (la
    home y lo cercano); en modo muestra, por `url_hash`, que es un SHA-256:
    un reparto estable y sin sesgo por seccion, y la misma muestra si se
    repite (para que repetir cueste ~0 por la cache).
    """
    from shared.models import Url

    a = normalizar_alcance(alcance)
    q = session.query(Url.id).filter(
        Url.job_id == _uuid(job_id), Url.is_internal.is_(True), Url.is_html.is_(True),
        Url.status_code == 200, Url.indexable.is_(True))
    if a["carpeta"]:
        q = q.filter(Url.path.startswith(a["carpeta"], autoescape=True))
    q = q.order_by(Url.url_hash if a["muestra"] else Url.id)
    if a["max_urls"]:
        q = q.limit(a["max_urls"])
    return [uid for (uid,) in q]


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


# ---------------------------------------------------------------------------
# Gasto del mes
# ---------------------------------------------------------------------------
def _inicio_de_mes(ahora: datetime | None = None) -> datetime:
    ahora = ahora or datetime.now(timezone.utc)
    return ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def gasto_del_mes(session, cliente_id=None) -> float:
    """Lo pagado este mes segun `decisions` (la fuente duradera: los
    contadores de Redis se siembran de aqui). Sin cliente, el de la instancia."""
    from sqlalchemy import func

    from shared.models import Decision, Job

    q = session.query(func.coalesce(func.sum(Decision.coste_usd), 0.0)).filter(
        Decision.created_at >= _inicio_de_mes())
    if cliente_id is not None:
        q = q.join(Job, Job.id == Decision.job_id).filter(Job.cliente_id == _uuid(cliente_id))
    return float(q.scalar() or 0.0)


def topes(session, job) -> dict:
    """Lo que el dialogo enseña al lado de la estimacion, y lo que la API
    mira antes de encolar: cuanto lleva gastado el cliente este mes frente a
    su tope, y lo mismo para la instancia."""
    cliente = None
    if job.cliente_id:
        from shared.models import Client

        c = session.get(Client, job.cliente_id)
        tope = presupuesto_mensual_de_cliente(session, job.cliente_id)
        gastado = gasto_del_mes(session, job.cliente_id)
        cliente = {"id": str(job.cliente_id), "nombre": c.nombre if c else None,
                   "gastado_mes_usd": round(gastado, 6), "tope_mensual_usd": tope,
                   "restante_usd": None if tope is None else round(max(0.0, tope - gastado), 6)}
    tope_g = presupuesto_global_mensual()
    gastado_g = gasto_del_mes(session)
    return {"cliente": cliente,
            "global": {"gastado_mes_usd": round(gastado_g, 6), "tope_mensual_usd": tope_g,
                       "restante_usd": None if tope_g is None
                       else round(max(0.0, tope_g - gastado_g), 6)}}


# ---------------------------------------------------------------------------
# Estimacion (no gasta nada)
# ---------------------------------------------------------------------------
def _fabrica_de_sesiones(session):
    """La capa cierra la sesion que pide para mirar la cache en BD: se le da
    una nueva sobre el mismo motor, no la del request."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=session.get_bind())


def estimar(session, job, modulos: list[str], alcance: dict | None, *, redis=None) -> dict:
    """Llamadas DESPUES de la cache, tokens, coste, tiempo y topes.

    La capa se monta SIN cliente (`fabrica_cliente=None`): `estimar` de
    `CapaDecisiones` solo consulta caches, y sin fabrica no hay forma de que
    salga una peticion aunque alguien la llamase mal.
    """
    orden, anadidos = resolver_modulos(modulos)
    url_ids = urls_del_alcance(session, job.id, alcance)
    capa = CapaDecisiones(None, cache=CacheRedis(redis) if redis is not None else CacheMemoria(),
                          sesion_factory=_fabrica_de_sesiones(session))
    por_modulo: dict[str, dict] = {}
    total = {"llamadas": 0, "tokens_estimados": 0, "coste_estimado_usd": 0.0,
             "aciertos_cache": 0, "decisiones": 0}
    for nombre in orden:
        m = REGISTRO[nombre]
        preguntas = m.objetos_pregunta()
        ids = m.filtrar_urls(session, job.id, url_ids) if m.filtrar_urls else url_ids
        estados = m.construir_estados(session, ids) if ids else []
        e = capa.estimar(estados, preguntas) if estados else {
            "estados": 0, "estados_distintos": 0, "preguntas": len(preguntas),
            "aciertos_cache": 0, "llamadas": 0, "tokens_estimados": 0,
            "coste_estimado_usd": 0.0}
        # Decisiones (estado distinto x pregunta) que ya estan pagadas.
        e["decisiones"] = e["estados_distintos"] * len(preguntas)
        e["urls"] = len(ids)
        por_modulo[nombre] = e
        for k in total:
            total[k] += e[k]
    rpm = max(1, numero_de_entorno("LLM_RPM", 600, int))
    coste = round(total["coste_estimado_usd"], 8)
    # Margen del 50% y redondeo al centimo hacia arriba: la estimacion es por
    # caracteres (~3 por token) y el coste real puede venir con recargo.
    sugerido = max(0.01, math.ceil(coste * 1.5 * 100) / 100)
    return {
        "modulos": orden, "anadidos": anadidos, "alcance": normalizar_alcance(alcance),
        "urls": len(url_ids), "por_modulo": por_modulo,
        "llamadas": total["llamadas"], "tokens_estimados": total["tokens_estimados"],
        "coste_estimado_usd": coste,
        "aciertos_cache": total["aciertos_cache"], "decisiones": total["decisiones"],
        # Segun el limite por minuto: es el techo, no la latencia (que con 16
        # en vuelo casi nunca manda).
        "segundos_estimados": math.ceil(total["llamadas"] / rpm * 60),
        "rpm": rpm,
        "presupuesto_sugerido_usd": sugerido,
        **topes(session, job),
    }


# ---------------------------------------------------------------------------
# Ejecucion (en el worker)
# ---------------------------------------------------------------------------
def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def reclamar(session, run_id) -> bool:
    """Pasa de `pendiente` a `en_curso` con un UPDATE condicional: si dos
    workers sacan el mismo id (un reencolado duplicado tras un reinicio), solo
    uno lo ejecuta. Es lo que permite al vigilante reencolar sin miedo."""
    from shared.models import EnrichmentRun

    ahora = _ahora()
    n = (session.query(EnrichmentRun)
         .filter(EnrichmentRun.id == _uuid(run_id), EnrichmentRun.estado == PENDIENTE)
         .update({EnrichmentRun.estado: EN_CURSO, EnrichmentRun.latido_at: ahora,
                  EnrichmentRun.started_at: ahora}, synchronize_session=False))
    session.commit()
    return n == 1


def _guardable(fila) -> bool:
    """Lo que no se decidio NI se pago no se escribe: es lo que quedo sin
    llamar (presupuesto, sin credito, red) y al reanudar tiene que volver a
    intentarse. Lo pagado se escribe siempre, aunque la respuesta viniera
    incompleta: si no, ese dinero desaparece de la cuenta."""
    return fila.estado != SIN_DECIDIR or (fila.coste_usd or 0) > 0


def _hechas(session, run_id, preguntas: list[str]) -> set[int]:
    from shared.models import Decision

    filas = (session.query(Decision.url_id, Decision.pregunta)
             .filter(Decision.run_id == _uuid(run_id), Decision.pregunta.in_(preguntas)))
    por_url: dict[int, set[str]] = {}
    for uid, p in filas:
        por_url.setdefault(uid, set()).add(p)
    return {u for u, ps in por_url.items() if ps >= set(preguntas)}


def ejecutar(run_id, *, sesion_factory, redis=None, transport=None,
             lote: int | None = None) -> str | None:
    """Corre una ejecucion de principio a fin. Devuelve el estado final, o
    None si no le tocaba (ya la tenia otro worker o no estaba pendiente).

    No lanza: cualquier excepcion deja la ejecucion `fallida` con el error,
    porque una ejecucion colgada en `en_curso` parece viva y bloquea lanzar
    otra sobre el mismo job.
    """
    s = sesion_factory()
    try:
        if not reclamar(s, run_id):
            return None
        try:
            return asyncio.run(_ejecutar(s, run_id, sesion_factory, redis, transport,
                                         lote or numero_de_entorno("ENRICHMENT_LOTE", 200, int)))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Enriquecido %s: fallo inesperado", run_id)
            s.rollback()
            _cerrar(s, run_id, FALLIDA, motivo="error",
                    error=f"{type(exc).__name__}: {str(exc)[:300]}")
            return FALLIDA
    finally:
        s.close()


def _cerrar(s, run_id, estado: str, *, motivo: str | None = None, error: str | None = None):
    from shared.models import EnrichmentRun

    run = s.get(EnrichmentRun, _uuid(run_id))
    if run is None:
        return
    run.estado = estado
    run.finished_at = _ahora()
    if motivo:
        run.motivo = motivo
    if error:
        run.errores = [*(run.errores or []), error][:50]
    s.commit()


async def _ejecutar(s, run_id, sesion_factory, redis, transport, lote: int) -> str:
    from shared.models import Client, EnrichmentRun, Job

    run = s.get(EnrichmentRun, _uuid(run_id))
    job = s.get(Job, run.job_id)
    cliente = s.get(Client, job.cliente_id) if job.cliente_id else None
    previo = float(run.gastado_usd or 0.0)
    restante = (None if run.presupuesto_usd is None
                else max(0.0, float(run.presupuesto_usd) - previo))
    capa = capa_desde_entorno(
        job_id=job.id, run_id=run.id, cliente_id=job.cliente_id,
        usuario=cliente.slug if cliente else None, sesion_factory=sesion_factory,
        redis=redis, presupuesto_ejecucion_usd=restante, transport=transport)
    if capa.cortada == "desactivado":
        _cerrar(s, run_id, FALLIDA, motivo="desactivado",
                error=("La capa de decisiones esta desactivada en el worker: hace falta "
                       "DECISIONS_ENABLED=1 y OPENROUTER_API_KEY en su entorno."))
        return FALLIDA

    url_ids = urls_del_alcance(s, job.id, run.alcance)
    # Las URLs de cada modulo ANTES de empezar: con un prefiltro (soft 404)
    # cada modulo tiene las suyas, y el total del progreso no puede crecer.
    ids_de = {}
    for nombre in run.modulos:
        m = REGISTRO.get(nombre)
        if m is not None:
            ids_de[nombre] = m.filtrar_urls(s, job.id, url_ids) if m.filtrar_urls else url_ids
    total_urls = sum(len(v) for v in ids_de.values())
    progreso = dict(run.progreso or {})
    por_modulo = dict(progreso.get("por_modulo") or {})
    errores: list[str] = list(run.errores or [])
    cancelada = False
    try:
        for nombre in run.modulos:
            m = REGISTRO.get(nombre)
            if m is None:
                errores.append(f"modulo {nombre!r} ya no existe; se omite")
                continue
            preguntas = m.objetos_pregunta()
            ids = ids_de[nombre]
            hechas = _hechas(s, run.id, [p.nombre for p in preguntas])
            pendientes = [u for u in ids if u not in hechas]
            pm = por_modulo.setdefault(nombre, {"total": len(ids), "procesadas": 0,
                                                "de_cache": 0, "sin_decidir": 0})
            pm["total"] = len(ids)
            pm["procesadas"] = len(hechas)
            for trozo in _trozos(pendientes, lote):
                s.expire(run)
                # CANCELADA tambien: la API puede haberla cancelado como
                # pendiente justo mientras este worker la reclamaba.
                if run.estado in (CANCELANDO, CANCELADA):
                    cancelada = True
                    break
                estados = m.construir_estados(s, trozo)
                resultados = await capa.preguntar(estados, preguntas)
                filas = [f for f in capa.filas(estados, resultados) if _guardable(f)]
                s.add_all(filas)
                guardar_resumen(s, job.id, capa.obs)
                for a in capa.obs.avisos:
                    if a not in errores:
                        errores.append(a)
                capa.obs = type(capa.obs)(estado=capa.obs.estado)
                urls_con_fila = {f.url_id for f in filas}
                pm["procesadas"] += len(urls_con_fila)
                pm["de_cache"] += sum(1 for f in filas if f.origen == "cache")
                pm["sin_decidir"] += sum(1 for f in filas if f.estado == SIN_DECIDIR)
                run.progreso = {"modulo": nombre, "por_modulo": por_modulo,
                                "procesadas": sum(v["procesadas"] for v in por_modulo.values()),
                                "total": total_urls}
                run.gastado_usd = round(previo + capa.presupuesto.gastado_ejecucion, 10)
                run.errores = errores[:50] or None
                run.latido_at = _ahora()
                s.commit()
                if capa.cortada:
                    break
            if cancelada or capa.cortada:
                break
    finally:
        await capa.cerrar()

    if cancelada:
        _cerrar(s, run_id, CANCELADA, motivo="cancelada")
        return CANCELADA
    motivo = capa.cortada
    if motivo and (motivo.startswith("presupuesto") or motivo == "max_llamadas"):
        _cerrar(s, run_id, PARCIAL, motivo=motivo)
        return PARCIAL
    if motivo:
        _cerrar(s, run_id, FALLIDA, motivo=motivo[:32],
                error=f"la capa dejo de llamar: {motivo}")
        return FALLIDA
    s.expire(run)
    # Pidieron cancelar justo durante el ultimo lote: lo que habia que hacer
    # esta hecho, asi que es una ejecucion completa.
    _cerrar(s, run_id, COMPLETADA)
    return COMPLETADA


def recuperar(session, minutos: float) -> list[str]:
    """Ejecuciones huerfanas tras un reinicio del worker.

    `en_curso` sin latido en `minutos` vuelve a `pendiente` para reencolarse
    (reanudar es idempotente: se salta lo que ya tiene fila y la cache cubre
    el resto); `cancelando` sin latido se cierra como `cancelada`, que es lo
    que se pidio. Devuelve los ids a reencolar.
    """
    from shared.models import EnrichmentRun

    corte = _ahora() - timedelta(minutes=minutos)
    viejas = (session.query(EnrichmentRun)
              .filter(EnrichmentRun.estado.in_((EN_CURSO, CANCELANDO)),
                      (EnrichmentRun.latido_at.is_(None)) | (EnrichmentRun.latido_at < corte))
              .all())
    reencolar = []
    for r in viejas:
        if r.estado == CANCELANDO:
            r.estado = CANCELADA
            r.finished_at = _ahora()
        else:
            r.estado = PENDIENTE
            reencolar.append(str(r.id))
    session.commit()
    return reencolar


def minutos_de_latido() -> float:
    return numero_de_entorno("ENRICHMENT_STALE_MINUTES", 15.0)


__all__ = [
    "Modulo", "REGISTRO", "resolver_modulos", "describir_modulos", "ModuloDesconocido",
    "normalizar_alcance", "urls_del_alcance", "estimar", "ejecutar", "recuperar", "reclamar",
    "gasto_del_mes", "topes", "PENDIENTE", "EN_CURSO", "CANCELANDO", "COMPLETADA", "PARCIAL",
    "CANCELADA", "FALLIDA", "ACTIVOS", "TERMINALES",
]
