"""Tipo de pagina por plantilla (#12, decision 178): la muestra, el estado y
la propuesta para la plantilla entera.

Preguntar URL a URL cuesta lo mismo por cada una de las 564 entradas de un
blog que dicen lo mismo. Aqui se pregunta por unas pocas de cada plantilla
(`muestras_por_plantilla`) y la respuesta se PROPONE para todas las URLs de
esa plantilla con su acuerdo y su confianza (`propuesta_por_plantilla`). Una
plantilla cuyas muestras no coinciden es «mixta»: no se propaga nada y hara
falta decidirla URL a URL.

La plantilla es la de `shared/plantillas.plantilla_de` (las reglas del
cliente en `config.templates` y, si no casa ninguna, la forma de la ruta),
con el host delante sin `www.`: dos sitios con `/blog/:slug` son dos
plantillas, y la misma pagina con y sin `www` es una (la decision 169 vio que
contarla dos veces infla la muestra).

Nada de esto llama a un modelo: el estado y la muestra los usa el modulo de
`shared/enriquecido.py`, y la propuesta solo lee `decisions`. Sin umbral
calibrado (#8) ninguna plantilla sale `propagable`.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from shared.plantillas import plantilla_de, reglas_de_plantilla

# Cuantas muestras por plantilla: 3 de base y una mas por cada orden de
# magnitud, hasta 8. Una plantilla de 8.718 URLs (`/:idioma/:slug` de Salle)
# necesita mas pruebas de que es homogenea que una de 12. Medido en
# produccion el 24-oct-2026: 262 de 3.216 indexables en Lopesan (84
# plantillas), 153 de 1.080 en Saunier Duval (68), 558 de 13.069 en Salle
# (247) y 405 de 78.457 en Penguin (100).
MUESTRAS_BASE = 3
MUESTRAS_MAX = 8
# Los tipos de schema que estan en todas las paginas de un sitio no dicen nada
# del tipo de pagina y solo gastan tokens.
SCHEMA_DE_TODAS = frozenset({
    "WebSite", "WebPage", "Organization", "BreadcrumbList", "ImageObject", "Person", "ListItem",
    "SiteNavigationElement", "SearchAction", "EntryPoint", "PostalAddress", "ContactPoint",
    "Brand", "Place", "GeoCoordinates", "Thing", "WPHeader", "WPFooter", "WPSideBar",
})
MAX_TIPOS_SCHEMA = 8
MAX_RUTA = 200


def muestras_por_plantilla(n: int) -> int:
    if n <= 0:
        return 0
    return min(n, MUESTRAS_MAX, MUESTRAS_BASE + int(math.log10(n)))


def clave_de_plantilla(url: str | None, path: str | None, reglas=None) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}|{plantilla_de(path or '/', reglas)}"


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def _trozos(lista: list, n: int = 500):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def _config(session, job_id) -> dict:
    from shared.models import Job

    job = session.get(Job, _uuid(job_id))
    return (job.config or {}) if job is not None else {}


def plantillas_de(session, job_id, url_ids: list[int]) -> dict[int, tuple[str, str]]:
    """url_id -> (plantilla, url_hash), con las reglas del job."""
    from shared.models import Url

    reglas = reglas_de_plantilla(_config(session, job_id).get("templates"))
    salida: dict[int, tuple[str, str]] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, url, path, h in session.query(Url.id, Url.url, Url.path, Url.url_hash).filter(
                Url.id.in_(trozo)):
            salida[uid] = (clave_de_plantilla(url, path, reglas), h or "")
    return salida


def muestra_por_plantilla(session, job_id, url_ids: list[int]) -> list[int]:
    """De las URLs del alcance, las que se preguntan: `muestras_por_plantilla`
    de cada plantilla, por `url_hash` (un SHA-256: reparto estable, y repetir
    la ejecucion pregunta lo mismo y sale de cache). En el orden de entrada."""
    if not url_ids:
        return []
    from shared.models import Url

    grupos: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for uid, (plantilla, h) in plantillas_de(session, job_id, url_ids).items():
        grupos[plantilla].append((h, uid))
    rutas: dict[int, str] = {}
    for trozo in _trozos(list(url_ids)):
        rutas.update({uid: (p or "/").lower() for uid, p in session.query(Url.id, Url.path).filter(
            Url.id.in_(trozo))})
    elegidas: set[int] = set()
    for miembros in grupos.values():
        miembros.sort()
        # Dentro de una plantilla el host ya es el mismo salvo el `www.`: la
        # misma ruta es la misma pagina, y dos muestras iguales «coincidirian»
        # sin decir nada de si la plantilla es homogenea (decision 169).
        distintas: dict[str, int] = {}
        for _, uid in miembros:
            distintas.setdefault(rutas.get(uid, ""), uid)
        unicas = list(distintas.values())  # en orden de url_hash: el dict conserva el de insercion
        elegidas.update(unicas[:muestras_por_plantilla(len(unicas))])
    return [u for u in url_ids if u in elegidas]


def taxonomia_de(session, job) -> list:
    """La taxonomia con la que se pregunta: la del cliente HOY
    (`clients.config_defecto.taxonomia`, que se edita en su ficha) y, si el
    cliente no tiene, la que viajo en la config del job.

    La del cliente manda porque la taxonomia es del negocio, no del rastreo:
    `jobs.config` se congela al lanzar y no se puede cambiar despues, y leer
    solo de ahi obligaba a volver a rastrear el sitio entero (78.457 URLs en
    Penguin) para preguntar por un censo ya hecho."""
    from shared.models import Client

    if job is None:
        return []
    cliente = session.get(Client, job.cliente_id) if getattr(job, "cliente_id", None) else None
    propia = ((cliente.config_defecto or {}).get("taxonomia") if cliente is not None else None) or []
    return list(propia) or list((job.config or {}).get("taxonomia") or [])


def _job(session, job_id):
    from shared.models import Job

    return session.get(Job, _uuid(job_id))


def muestra_si_hay_taxonomia(session, job_id, url_ids: list[int]) -> list[int]:
    """Lo mismo, pero solo si hay una taxonomia de dos categorias o mas
    (`taxonomia_de`): sin ella no hay nada que preguntar ni que pagar."""
    from shared.preguntas.tipo_pagina import opciones_de_taxonomia

    if opciones_de_taxonomia(taxonomia_de(session, _job(session, job_id))) is None:
        return []
    return muestra_por_plantilla(session, job_id, url_ids)


def sin_taxonomia(session, job) -> str | None:
    """Por que `categoria_cliente` no pregunta nada, para la estimacion y la
    ejecucion: sin esto salian 0 URLs y 0 llamadas sin decir por que."""
    from shared.preguntas.tipo_pagina import opciones_de_taxonomia

    if opciones_de_taxonomia(taxonomia_de(session, job)) is not None:
        return None
    return ("categoria_cliente no pregunta nada: el cliente no tiene taxonomia de dos categorias o mas. "
            "Ponla en la configuracion por defecto del cliente (`taxonomia`) y vale tambien para los "
            "censos ya rastreados.")


def preguntas_de_taxonomia(session, job) -> list:
    """`categoria_cliente` con las categorias del cliente como opciones."""
    from shared.preguntas.tipo_pagina import CATEGORIA_CLIENTE, opciones_de_taxonomia

    opciones = opciones_de_taxonomia(taxonomia_de(session, job))
    return [CATEGORIA_CLIENTE.con_opciones(opciones)] if opciones else [CATEGORIA_CLIENTE]


def estados_tipo_pagina(session, url_ids: list[int]) -> list:
    """Titulo, h1 y el principio del contenido (como `pagina_legal`) y,
    ademas, lo que distingue un tipo de otro sin leer la pagina entera: la
    ruta y, si los hay, las palabras propias, el `og:type`, si esta paginada y
    los tipos de schema que no estan en todas las paginas. Minimo (decision 84)."""
    from shared.enriquecido import estados_titulo_h1_contenido
    from shared.models import HtmlMeta, StructuredData, Url

    base = estados_titulo_h1_contenido(session, url_ids)
    url: dict[int, tuple] = {}
    meta: dict[int, tuple] = {}
    schema: dict[int, set] = defaultdict(set)
    for trozo in _trozos(list(url_ids)):
        for uid, path, palabras in session.query(Url.id, Url.path, Url.content_word_count).filter(
                Url.id.in_(trozo)):
            url[uid] = ((path or "/")[:MAX_RUTA], palabras)
        for uid, og, nxt, prv in session.query(HtmlMeta.url_id, HtmlMeta.og_type, HtmlMeta.rel_next,
                                               HtmlMeta.rel_prev).filter(HtmlMeta.url_id.in_(trozo)):
            meta[uid] = (og or "", bool(nxt or prv))
        for uid, tipo in session.query(StructuredData.url_id, StructuredData.schema_type).filter(
                StructuredData.url_id.in_(trozo), StructuredData.schema_type.isnot(None)):
            t = (tipo or "").strip()
            if t and t not in SCHEMA_DE_TODAS:
                schema[uid].add(t)
    for e in base:
        ruta, palabras = url.get(e.url_id, ("/", None))
        og, paginada = meta.get(e.url_id, ("", False))
        extra = {"palabras": palabras, "og_type": og, "paginada": paginada,
                 "schema": sorted(schema.get(e.url_id, ()))[:MAX_TIPOS_SCHEMA]}
        # Lo que el sitio no tiene no se manda: `content_word_count` es NULL en
        # todo Salle y todo Penguin, Salle y Saunier no tienen schema y Salle
        # no tiene `og:type`. Una clave vacia gasta tokens y la instruccion
        # dice «when present». `paginada` solo cuando lo esta.
        e.datos = {"ruta": ruta, **{k: v for k, v in extra.items() if v not in (None, "", [], False)},
                   **e.datos}
    return base


# ---------------------------------------------------------------------------
# Propuesta por plantilla
# ---------------------------------------------------------------------------
PROPAGABLE, ORIENTATIVA, MIXTA = "propagable", "orientativa", "mixta"


def propuesta_por_plantilla(session, run_id, pregunta: str = "tipo_pagina", *,
                            limite: int = 500) -> dict:
    """Por plantilla: la respuesta de sus muestras, cuantas coinciden, con que
    confianza, y a cuantas URLs del alcance se propondria.

    - `mixta`: las muestras no coinciden. La plantilla no es homogenea (una
      `/:idioma/:slug` que junta ofertas, legales y corporativas): no se
      propaga y habra que decidirla URL a URL.
    - `propagable`: todas coinciden y todas estan `decidido`, lo que exige un
      umbral calibrado (#8). Hoy ninguna pregunta lo tiene.
    - `orientativa`: coinciden, pero sin umbral o con alguna `incierto`.

    No escribe nada: es lo que el dia que haya umbral rellenara el tipo de
    pagina de cada URL (#12), y lo que lee la pantalla del enriquecido.
    """
    from shared.enriquecido import urls_del_alcance
    from shared.models import Decision, EnrichmentRun
    from shared.preguntas import CATALOGO

    run = session.get(EnrichmentRun, _uuid(run_id))
    if run is None:
        return {"pregunta": pregunta, "umbral": None, "plantillas": [], "total_plantillas": 0}
    actual = CATALOGO.get(pregunta)
    alcance = urls_del_alcance(session, run.job_id, run.alcance)
    de_url = plantillas_de(session, run.job_id, alcance)
    tamano = Counter(p for p, _ in de_url.values())
    por_plantilla: dict[str, list[tuple]] = defaultdict(list)
    filas = (session.query(Decision.url_id, Decision.respuesta, Decision.confianza, Decision.estado)
             .filter(Decision.run_id == run.id, Decision.pregunta == pregunta))
    for uid, resp, conf, estado in filas:
        plantilla = de_url.get(uid, (None,))[0]
        if plantilla is None:
            # Fuera del alcance de hoy (otra pagina tras un recrawl): no se inventa su plantilla.
            continue
        por_plantilla[plantilla].append((resp, conf, estado))
    salida = []
    for plantilla, muestras in por_plantilla.items():
        con_respuesta = [m for m in muestras if m[0] is not None]
        votos = Counter(m[0] for m in con_respuesta)
        fila = {"plantilla": plantilla, "urls": tamano.get(plantilla, 0), "muestras": len(muestras),
                "respondidas": len(con_respuesta), "respuestas": dict(votos.most_common())}
        if not con_respuesta:
            fila.update(respuesta=None, acuerdo=None, confianza_media=None, confianza_min=None,
                        estado="sin_respuesta")
        else:
            respuesta, n = votos.most_common(1)[0]
            confs = [m[1] for m in con_respuesta if m[0] == respuesta and m[1] is not None]
            unanime = n == len(con_respuesta) == len(muestras)
            todas_decididas = all(m[2] == "decidido" for m in muestras)
            fila.update(
                respuesta=respuesta, acuerdo=round(n / len(con_respuesta), 4),
                confianza_media=round(sum(confs) / len(confs), 4) if confs else None,
                confianza_min=round(min(confs), 4) if confs else None,
                estado=(MIXTA if len(votos) > 1 else
                        PROPAGABLE if unanime and todas_decididas and actual is not None
                        and actual.umbral is not None else ORIENTATIVA))
        salida.append(fila)
    salida.sort(key=lambda f: (-f["urls"], f["plantilla"]))
    return {"pregunta": pregunta, "umbral": actual.umbral if actual else None,
            "total_plantillas": len(salida),
            "urls_cubiertas": sum(f["urls"] for f in salida if f["estado"] in (PROPAGABLE, ORIENTATIVA)),
            "por_estado": dict(Counter(f["estado"] for f in salida)),
            "plantillas": salida[:limite]}
