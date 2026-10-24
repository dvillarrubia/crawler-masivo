"""Intencion de la pagina y de sus busquedas reales (#39, #169 puntos 1 y 2;
decision 176).

Dos modulos del enriquecido (`shared/enriquecido.REGISTRO`) se apoyan aqui:

- **`intencion_pagina`**: `intencion_pagina` y `etapa_embudo` sobre la ruta, el
  titulo, el primer H1 que se pinta, el principio del contenido principal y los
  tipos de schema.org que declara (sin los de sitio).
  Solo las paginas con algo que juzgar (titulo, H1 o contenido).
- **`intencion_busquedas`**: `intencion_busqueda` sobre las busquedas de Search
  Console (`gsc_consultas`, decision 157) que traen clics a cada pagina en la
  ventana del censo: las `MAX_BUSQUEDAS_POR_PAGINA` con mas clics. El estado es
  la busqueda, el sitio (sin `www.`) y las `marcas` del job, y NADA de la
  pagina: asi la misma busqueda en otra pagina o en otro censo del mismo cliente
  es el mismo estado y sale de la cache (decision 84) sin volver a pagar. Sin
  cifras en el estado por lo mismo: los clics se leen al comparar, no se envian.

Con las dos sale el **desajuste pagina-busquedas ponderado por clics**
(`desajuste`), que hoy es orientativo: ninguna de las preguntas tiene umbral
(#8), todo es `incierto`, y la incidencia `intencion_desalineada`
(`incidencias_intencion_desalineada`) esta preparada pero no emite nada hasta
que las dos lo tengan.

Lo que no se puede afirmar no se afirma: las busquedas de marca o ajenas
(`navigational`) pueden caer en cualquier pagina de esa marca, y las `unclear`
no dicen nada; ninguna de las dos cuenta para el desajuste. Por lo mismo, de
una pagina navegacional, o de una cuyos clics son sobre todo de marca, no se
afirma desajuste (`sin_afirmar`); ni se dice «no hay busquedas» de una ventana
que no esta descargada (`cobertura_de_la_ventana`).
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Cuantas busquedas por pagina se preguntan, por clics. Medido el 24-oct-2026
# en Lopesan (censo 1e7ea651, 90 dias): de 3.216 indexables, 1.921 tienen
# busquedas con clics; con 10 por pagina son 10.121 pares y 7.183 busquedas
# distintas que cubren el 77,6 % de los clics con busqueda conocida. Con todas,
# 24.516 pares y 14.943 distintas por el 22 % restante, casi todo cola larga.
MAX_BUSQUEDAS_POR_PAGINA = 10
# Una busqueda sin clics no pesa en un desajuste ponderado por clics.
MIN_CLICS = 1
# Lo que no cuenta para el desajuste (ver el docstring del modulo).
NO_CUENTAN = ("navigational", "unclear")
AMBITO_BUSQUEDA = "busqueda"
# Por encima de estas URLs se lee la ventana entera una vez (ver
# `busquedas_por_url`).
CLAVES_POR_CONSULTA = 500


def _trozos(lista: list, n: int = 500):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


# ---------------------------------------------------------------------------
# Pagina
# ---------------------------------------------------------------------------
# Tipos de schema.org que van en todas las paginas de un sitio (o son piezas de
# otro tipo) y no dicen nada de la pagina: fuera del estado, para no gastar
# tokens ni romper la cache entre paginas que solo se distinguen en eso.
TIPOS_SCHEMA_DE_SITIO = frozenset({
    "organization", "website", "webpage", "breadcrumblist", "listitem", "person", "imageobject",
    "imagegallery", "searchaction", "entrypoint", "sitenavigationelement", "wpheader",
    "wpfooter", "wpsidebar", "geocoordinates", "postaladdress", "quantitativevalue",
    "locationfeaturespecification", "contactpoint", "thing",
})
MAX_TIPOS_SCHEMA = 8


def tipos_schema_por_url(session, url_ids: list[int]) -> dict[int, list[str]]:
    """{url_id: [tipo, ...]}: los tipos de schema.org que declara cada pagina
    (`structured_data.schema_type`, que puede traer «Person, Organization»),
    sin los de sitio, ordenados y sin repetir. Es la senal que el texto no da:
    el widget de reserva y el precio no salen en los 600 caracteres de
    contenido, y un `Hotel` con `Offer` o un `Product` si (decision 176)."""
    from shared.models import StructuredData

    tipos: dict[int, set[str]] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, t in session.query(StructuredData.url_id, StructuredData.schema_type).filter(
                StructuredData.url_id.in_(trozo), StructuredData.schema_type.isnot(None)):
            for parte in str(t).split(","):
                parte = parte.strip()
                if parte and parte.casefold() not in TIPOS_SCHEMA_DE_SITIO:
                    tipos.setdefault(uid, set()).add(parte)
    return {u: sorted(v)[:MAX_TIPOS_SCHEMA] for u, v in tipos.items()}


def estados_intencion_pagina(session, url_ids: list[int]):
    """Ruta, titulo, primer H1 visible, 600 caracteres de contenido sin
    plantilla (`estados_titulo_h1_contenido`) y los tipos de schema.org que
    declara. La ruta ayuda (`/blog/`, `/reservar/`) y no rompe la cache entre la
    URL con y sin `www.`; los tipos de schema separan la ficha de un hotel o un
    producto (que se reserva o se compra aunque el texto solo lo describa) de
    una landing de marca."""
    from shared.enriquecido import estados_titulo_h1_contenido
    from shared.models import Url

    base = estados_titulo_h1_contenido(session, url_ids)
    rutas: dict[int, str] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, path in session.query(Url.id, Url.path).filter(Url.id.in_(trozo)):
            rutas[uid] = path or "/"
    tipos = tipos_schema_por_url(session, url_ids)
    for e in base:
        e.datos = {"ruta": rutas.get(e.url_id, "/"), **e.datos,
                   "tipos_schema": tipos.get(e.url_id, [])}
    return base


def paginas_con_algo_que_juzgar(session, job_id, url_ids: list[int]) -> list[int]:
    """Las que tienen titulo, H1 visible o contenido: sin nada de eso el modelo
    solo puede contestar `unclear`, y se pagaria por nada."""
    from sqlalchemy import func

    from shared.models import Heading, HtmlMeta, PageContent

    if not url_ids:
        return []
    con: set[int] = set()
    for trozo in _trozos(list(url_ids)):
        con |= {u for (u,) in session.query(HtmlMeta.url_id).filter(
            HtmlMeta.url_id.in_(trozo), func.length(func.trim(func.coalesce(HtmlMeta.title, ""))) > 0)}
        con |= {u for (u,) in session.query(Heading.url_id).filter(
            Heading.url_id.in_(trozo), Heading.tag == "h1", Heading.oculto.isnot(True),
            func.length(func.trim(func.coalesce(Heading.text, ""))) > 0)}
        con |= {u for (u,) in session.query(PageContent.url_id).filter(
            PageContent.url_id.in_(trozo),
            func.length(func.trim(func.coalesce(PageContent.content_text, ""))) > 0)}
    return [u for u in url_ids if u in con]


# ---------------------------------------------------------------------------
# Busquedas de Search Console
# ---------------------------------------------------------------------------
def sitio_del_job(job) -> str:
    """El host de la primera semilla sin `www.`: con y sin `www.` es el mismo
    sitio para quien busca, y dos estados distintos pagarian dos veces
    (decision 169)."""
    semillas = list(getattr(job, "seeds", None) or [])
    host = (urlsplit(str(semillas[0])).hostname or "").lower() if semillas else ""
    return host[4:] if host.startswith("www.") else host


def marcas_del_job(job) -> list[str]:
    return sorted({str(m).strip().lower() for m in ((job.config or {}).get("marcas") or [])
                   if str(m).strip()})


def _job_de(session, url_ids: list[int]):
    from shared.models import Job, Url

    if not url_ids:
        return None
    fila = session.query(Url.job_id).filter(Url.id == url_ids[0]).first()
    return session.get(Job, fila[0]) if fila else None


def busquedas_por_url(session, job, url_ids: list[int],
                      maximo: int | None = MAX_BUSQUEDAS_POR_PAGINA) -> dict[int, list[tuple[str, int]]]:
    """{url_id: [(busqueda, clics), ...]}: las `maximo` con mas clics de cada
    URL (None = todas) en la ventana del censo, de las propiedades activas
    del cliente. Vacio sin cliente, sin Search Console o sin la tabla (una base
    sin migrar): best-effort, el try cubre la funcion entera (decision 74).

    `clics > 0` fila a fila y no `HAVING sum > 0`: es lo mismo (los clics no
    son negativos) y en Lopesan pasa de 7,2 s a 0,5 s.
    """
    try:
        from sqlalchemy import func

        from shared.gsc import clave_de_url, propiedades_activas, ventana_del_censo
        from shared.models import GscConsulta as C, Url

        if job is None or not job.cliente_id or not url_ids:
            return {}
        propiedades = propiedades_activas(session, job.cliente_id)
        if not propiedades:
            return {}
        desde, hasta = ventana_del_censo(job)
        ids_de_clave: dict[str, list[int]] = {}
        for trozo in _trozos(list(url_ids)):
            for uid, url in session.query(Url.id, Url.url).filter(Url.id.in_(trozo)):
                try:
                    ids_de_clave.setdefault(clave_de_url(url), []).append(uid)
                except Exception:  # noqa: BLE001 -- un href imposible no tiene busquedas
                    continue
        crudo: dict[int, list[tuple[int, int, str]]] = {}
        base = (session.query(C.url_clave, C.consulta, func.sum(C.clics), func.sum(C.impresiones))
                .filter(C.cliente_id == job.cliente_id, C.propiedad.in_(propiedades),
                        C.fecha >= desde, C.fecha <= hasta, C.clics > 0))
        # Pocas URLs (un lote de la ejecucion): con su clave. Muchas (el filtro
        # de todo el censo): UNA lectura de la ventana y se cruza aqui; por
        # trozos de 500 claves se leia la ventana entera una vez por trozo (el
        # indice es por cliente y fecha, no por clave).
        claves = sorted(ids_de_clave)
        consultas = ([base.filter(C.url_clave.in_(claves))] if len(claves) <= CLAVES_POR_CONSULTA
                     else [base])
        for q in consultas:
            for clave, consulta, clics, imp in q.group_by(C.url_clave, C.consulta):
                if clave not in ids_de_clave:
                    continue
                if int(clics or 0) < MIN_CLICS or not (consulta or "").strip():
                    continue
                for uid in ids_de_clave[clave]:
                    crudo.setdefault(uid, []).append((int(clics or 0), int(imp or 0), consulta))
        salida = {}
        for uid, lista in crudo.items():
            # Mas clics, despues mas impresiones y la busqueda: orden total, para
            # que el estado (y su cache) no cambie entre dos lecturas.
            lista.sort(key=lambda t: (-t[0], -t[1], t[2]))
            salida[uid] = [(q, c) for c, _, q in (lista if maximo is None else lista[:maximo])]
        return salida
    except Exception:  # noqa: BLE001
        logger.exception("Busquedas de Search Console para la intencion: fallo; sin busquedas")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}


def estado_de_busqueda(sitio: str, marcas: list[str], busqueda: str) -> dict:
    return {"sitio": sitio, "marcas": list(marcas), "busqueda": busqueda}


def estados_intencion_busquedas(session, url_ids: list[int]):
    """Un estado por (pagina, busqueda), con `ambito="busqueda"`. Una pagina con
    diez busquedas son diez estados; la misma busqueda en dos paginas es un solo
    estado distinto (una llamada) y dos filas de `decisions`."""
    from shared.decisiones import Estado

    job = _job_de(session, url_ids)
    if job is None:
        return []
    sitio, marcas = sitio_del_job(job), marcas_del_job(job)
    por_url = busquedas_por_url(session, job, url_ids)
    return [Estado(datos=estado_de_busqueda(sitio, marcas, q), ambito=AMBITO_BUSQUEDA, url_id=u)
            for u in url_ids for q, _ in por_url.get(u, [])]


def paginas_con_busquedas(session, job_id, url_ids: list[int]) -> list[int]:
    from shared.models import Job

    job = session.get(Job, _uuid(job_id))
    con = busquedas_por_url(session, job, url_ids)
    return [u for u in url_ids if con.get(u)]


def cobertura_de_la_ventana(session, job) -> dict | None:
    """Que parte de la ventana del censo tiene busquedas descargadas, segun la
    serie `consultas` de `gsc_estado.detalle` (decision 157) de las propiedades
    activas: {"ventana": (desde, hasta), "cubierto": (desde, hasta) | None,
    "completa": bool}. None sin cliente o sin Search Console.

    Hace falta para no afirmar una ausencia (reglas 67 y 70): un censo de antes
    de conectar Search Console, o de hace mas de los dias que se guardan
    (`SEARCH_CONSOLE_DETALLE_DIAS`, lo anterior se borra), no tiene busquedas
    porque no hay datos, no porque no las hubiera. Best-effort: el try cubre la
    funcion entera."""
    try:
        from datetime import date, timedelta

        from shared.gsc import RETRASO_DIAS, propiedades_activas, ventana_del_censo
        from shared.models import GscEstado

        if job is None or not job.cliente_id:
            return None
        propiedades = propiedades_activas(session, job.cliente_id)
        if not propiedades:
            return None
        desde, hasta = ventana_del_censo(job)
        primeros, ultimos = [], []
        for e in session.query(GscEstado).filter(GscEstado.cliente_id == job.cliente_id,
                                                  GscEstado.propiedad.in_(propiedades)):
            serie = (e.detalle or {}).get("consultas") or {}
            if serie.get("primer_dia") and serie.get("ultimo_dia"):
                primeros.append(date.fromisoformat(str(serie["primer_dia"])[:10]))
                ultimos.append(date.fromisoformat(str(serie["ultimo_dia"])[:10]))
        cubierto = None
        if primeros:
            a, b = max(desde, min(primeros)), min(hasta, max(ultimos))
            cubierto = (a, b) if a <= b else None
        # Los ultimos dias de la ventana llegan con el retraso de Google
        # (`RETRASO_DIAS`): un censo de ayer no puede tenerlos y no por eso
        # esta incompleto.
        hasta_posible = min(hasta, date.today() - timedelta(days=RETRASO_DIAS))
        completa = bool(cubierto and cubierto[0] <= desde and cubierto[1] >= hasta_posible)
        return {"ventana": (desde, hasta), "cubierto": cubierto, "completa": completa}
    except Exception:  # noqa: BLE001
        logger.exception("Cobertura de Search Console para la intencion: fallo")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def aviso_de_cobertura(cob: dict | None) -> str | None:
    """Lo que hay que decir cuando la ventana no esta descargada entera."""
    if not cob or cob["completa"]:
        return None
    desde, hasta = cob["ventana"]
    if cob["cubierto"] is None:
        return (f"No hay busquedas de Search Console descargadas entre el {desde} y el {hasta} "
                "(la ventana del censo): o el censo es de antes de conectarlo, o es tan viejo que "
                "esos dias ya se han borrado. No se puede saber que busquedas tenia.")
    a, b = cob["cubierto"]
    return (f"Solo hay busquedas de Search Console descargadas del {a} al {b} de una ventana del "
            f"{desde} al {hasta}: las busquedas y sus clics salen de esos dias, no de la ventana "
            "entera.")


def motivo_sin_busquedas(session, job_id) -> str:
    """Por que `intencion_busquedas` no tiene trabajo, dicho para quien lanza.
    Solo afirma que no hay busquedas si la ventana esta descargada."""
    from shared.gsc import propiedades_activas
    from shared.models import Job

    job = session.get(Job, _uuid(job_id))
    if job is None or not job.cliente_id:
        return "El rastreo no es de ningun cliente: sin cliente no hay Search Console."
    try:
        propiedades = propiedades_activas(session, job.cliente_id)
    except Exception:  # noqa: BLE001
        session.rollback()
        propiedades = []
    if not propiedades:
        return ("El cliente no tiene Search Console conectado (o aun no se ha descargado nada): "
                "este modulo no tiene busquedas que clasificar.")
    cob = cobertura_de_la_ventana(session, job)
    if cob is None or cob["cubierto"] is None:
        return (aviso_de_cobertura(cob) if cob else
                "No se ha podido leer que dias de Search Console hay descargados.") + \
            " Este modulo no tiene trabajo."
    a, b = cob["cubierto"]
    texto = (f"Search Console no tiene busquedas con clics de estas paginas entre el {a} y el "
             f"{b}: este modulo no tiene trabajo.")
    if not cob["completa"]:
        desde, hasta = cob["ventana"]
        texto += (f" Del resto de la ventana del censo ({desde} a {hasta}) no hay datos "
                  "descargados: de esos dias no se puede decir nada.")
    return texto


# ---------------------------------------------------------------------------
# Desajuste pagina-busquedas (orientativo) e incidencia (apagada)
# ---------------------------------------------------------------------------
def desajuste(intencion_pagina: str | None, busquedas: list[dict],
              solo_decididas: bool = False) -> dict:
    """Reparto de los clics de una pagina por la intencion de sus busquedas y
    cuanto casa con la intencion de la pagina.

    `busquedas`: [{"intencion", "clics", "estado"?}]. No cuentan para la parte
    alineada las `navigational` ni las `unclear` (ver el modulo) ni las que no
    tienen respuesta o no tienen clics (`clics` None: ya no estan en Search
    Console). `desalineada` = la definicion de #39 («no coincide con la que
    concentra la mayoria»), y solo cuando se puede afirmar:

    - la intencion de la pagina se conoce y se puede comparar: ni `unclear` ni
      `navigational` (sus busquedas, las de marca, no cuentan, asi que una
      pagina de marca saldria siempre con un 0 % alineado);
    - la intencion que mas clics concentra, CONTANDO las de marca, es otra y no
      es la marca: 1.000 clics de marca y 5 informacionales no dicen que la
      home este desalineada;
    - y la de la pagina se queda con menos de la mitad de los que cuentan.

    Con `solo_decididas`, solo las busquedas `decidido` (la incidencia de verdad).
    """
    por_intencion: dict[str, int] = {}
    excluidos = 0
    for b in busquedas:
        clics = int(b.get("clics") or 0)
        intencion = b.get("intencion")
        if intencion is None or (solo_decididas and b.get("estado") != "decidido"):
            excluidos += clics
            continue
        por_intencion[intencion] = por_intencion.get(intencion, 0) + clics
    contables = {k: v for k, v in por_intencion.items() if k not in NO_CUENTAN}
    total = sum(contables.values())
    # La que manda contando la marca (sin `unclear`, que no dice nada).
    clasificadas = {k: v for k, v in por_intencion.items() if k != "unclear" and v > 0}
    dominante = (sorted(clasificadas.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                 if clasificadas else None)
    comparable = intencion_pagina is not None and intencion_pagina not in NO_CUENTAN
    alineados = contables.get(intencion_pagina, 0) if comparable else 0
    parte = (alineados / total) if (total and comparable) else None
    if not comparable:
        sin_afirmar = "pagina_de_marca" if intencion_pagina == "navigational" else "pagina_sin_intencion"
    elif dominante == "navigational":
        sin_afirmar = "manda_la_marca"
    elif not total:
        sin_afirmar = "sin_clics_que_cuenten"
    else:
        sin_afirmar = None
    return {
        "clics_por_intencion": por_intencion,
        "clics_que_cuentan": total,
        "clics_sin_contar": sum(v for k, v in por_intencion.items() if k in NO_CUENTAN) + excluidos,
        "clics_alineados": alineados,
        "clics_desalineados": total - alineados if comparable else 0,
        "parte_alineada": None if parte is None else round(parte, 4),
        "intencion_dominante": dominante,
        # Por que no se afirma nada de esta pagina (None = se puede comparar).
        "sin_afirmar": sin_afirmar,
        "desalineada": bool(sin_afirmar is None and dominante != intencion_pagina and parte < 0.5),
    }


def incidencias_intencion_desalineada(job_id, paginas: list[dict]) -> list[dict]:
    """`intencion_desalineada`, una por pagina (un aviso por hallazgo), con las
    busquedas que la causan. **Apagada**: devuelve [] mientras
    `intencion_pagina` o `intencion_busqueda` no tengan umbral (#8), y aun con
    umbral solo usa respuestas `decidido`. Nadie la llama todavia: el
    enriquecido no emite incidencias (decision 85, pendiente).

    `paginas`: lo que da `intencion_frente_a_busquedas` (con todas las
    busquedas de cada pagina, no solo las que se enseñan)."""
    from shared.preguntas import obtener

    if obtener("intencion_pagina").umbral is None or obtener("intencion_busqueda").umbral is None:
        return []
    salida = []
    for p in paginas:
        if p.get("estado_pagina") != "decidido":
            continue
        d = desajuste(p.get("intencion_pagina"), p.get("busquedas") or [], solo_decididas=True)
        if not d["desalineada"]:
            continue
        principales = [b for b in (p.get("busquedas") or [])
                       if b.get("estado") == "decidido" and b.get("intencion") == d["intencion_dominante"]]
        salida.append({
            "job_id": job_id, "url_id": p["url_id"], "issue_type": "intencion_desalineada",
            "severity": "info", "group_key": None,
            "details": {"intencion_pagina": p.get("intencion_pagina"),
                        "intencion_dominante": d["intencion_dominante"],
                        "clics_desalineados": d["clics_desalineados"],
                        "parte_alineada": d["parte_alineada"],
                        "busquedas": [{"busqueda": b["busqueda"], "clics": b["clics"]}
                                      for b in principales[:5]]}})
    return salida


def intencion_frente_a_busquedas(session, run_id, limite: int = 100) -> dict:
    """Lo que enseña la pantalla del enriquecido: por pagina, su intencion, la
    de sus busquedas con los clics y el desajuste; y el embudo con el trafico
    real (clics de la pagina por etapa y por plantilla, #169 punto 2).

    Las busquedas son las que se PREGUNTARON en esta ejecucion
    (`decisions.estado_enviado`), no las que hoy serian el top 10: la descarga
    diaria y la retencion cambian `gsc_consultas`, y la pantalla de una
    ejecucion vieja no puede cambiar de busquedas sin que nadie lo diga. Los
    clics, que no van en el estado (la cache no puede depender de ellos), se
    leen de `gsc_consultas` en la ventana del censo; una busqueda preguntada
    que ya no esta sale con `clics` None y se cuenta en el resumen. Todo
    orientativo mientras falte un umbral.
    """
    from shared.decisiones import clave_estado
    from shared.models import Decision, EnrichmentRun, Job, Url
    from shared.plantillas import plantilla_de, reglas_de_plantilla
    from shared.preguntas import obtener

    run = session.get(EnrichmentRun, _uuid(run_id))
    job = session.get(Job, run.job_id) if run else None
    if job is None:
        return {"paginas": [], "resumen": None, "embudo": None}

    def filas(pregunta):
        return (session.query(Decision.url_id, Decision.clave, Decision.respuesta,
                              Decision.confianza, Decision.estado, Decision.estado_enviado)
                .filter(Decision.run_id == run.id, Decision.pregunta == pregunta)
                .order_by(Decision.id).all())

    pagina = {u: (r, c, e) for u, _, r, c, e, _ in filas("intencion_pagina")}
    etapa = {u: (r, c, e) for u, _, r, c, e, _ in filas("etapa_embudo")}
    preguntadas: dict[int, dict[str, dict]] = {}
    sitio, marcas = sitio_del_job(job), marcas_del_job(job)
    for u, k, r, c, e, env in filas("intencion_busqueda"):
        env = env if isinstance(env, dict) else {}
        if env.get("sitio") is not None:
            # El sitio y las marcas con que se pregunto, no los de hoy: con
            # ellos se vuelve a formar la clave para cruzar los clics.
            sitio, marcas = env.get("sitio"), list(env.get("marcas") or [])
        preguntadas.setdefault(u, {})[k] = {"busqueda": env.get("busqueda"), "intencion": r,
                                            "confianza": c, "estado": e}
    umbrales = {n: obtener(n).umbral for n in ("intencion_pagina", "etapa_embudo", "intencion_busqueda")}

    # --- desajuste ---------------------------------------------------------
    con_busquedas = sorted(preguntadas)
    clics_de: dict[tuple[int, str], int] = {}
    if con_busquedas:
        for uid, lista in busquedas_por_url(session, job, con_busquedas, maximo=None).items():
            for q, clics in lista:
                k = clave_estado(estado_de_busqueda(sitio, marcas, q))
                clics_de[(uid, k)] = clics_de.get((uid, k), 0) + clics
    urls = {}
    ids = sorted(set(con_busquedas) | set(etapa) | set(pagina))
    for trozo in _trozos(ids):
        for uid, url, path, clics in session.query(Url.id, Url.url, Url.path, Url.gsc_clics).filter(
                Url.id.in_(trozo)):
            urls[uid] = (url, path, clics)
    paginas = []
    total_por_intencion: dict[str, int] = {}
    sin_clics_hoy = 0
    for uid in con_busquedas:
        intencion, conf, est = pagina.get(uid, (None, None, None))
        busquedas = []
        for k, b in preguntadas[uid].items():
            clics = clics_de.get((uid, k))
            sin_clics_hoy += clics is None
            busquedas.append({**b, "clics": clics})
        busquedas.sort(key=lambda b: (b["clics"] is None, -(b["clics"] or 0), b["busqueda"] or ""))
        d = desajuste(intencion, busquedas)
        for k, v in d["clics_por_intencion"].items():
            total_por_intencion[k] = total_por_intencion.get(k, 0) + v
        paginas.append({"url_id": uid, "url": (urls.get(uid) or ("",))[0],
                        "intencion_pagina": intencion, "confianza_pagina": conf, "estado_pagina": est,
                        "busquedas": busquedas, **d})
    paginas.sort(key=lambda p: (-p["clics_desalineados"], -p["clics_que_cuentan"], p["url"]))
    resumen = {
        "paginas_comparadas": sum(1 for p in paginas if p["intencion_pagina"] is not None),
        "paginas_sin_intencion": sum(1 for p in paginas if p["intencion_pagina"] is None),
        "desalineadas": sum(1 for p in paginas if p["desalineada"]),
        "clics_desalineados": sum(p["clics_desalineados"] for p in paginas),
        "clics_que_cuentan": sum(p["clics_que_cuentan"] for p in paginas),
        "clics_por_intencion": total_por_intencion,
        # Busquedas preguntadas que ya no tienen clics en `gsc_consultas`
        # (retencion, o la descarga diaria rehizo esos dias).
        "busquedas_sin_clics_hoy": sin_clics_hoy,
        "aviso_cobertura": aviso_de_cobertura(cobertura_de_la_ventana(session, job)),
        "orientativo": umbrales["intencion_pagina"] is None or umbrales["intencion_busqueda"] is None,
        "incidencia_activa": bool(umbrales["intencion_pagina"] is not None
                                  and umbrales["intencion_busqueda"] is not None),
    } if con_busquedas else None

    # --- embudo con trafico real ---------------------------------------------
    embudo = None
    if etapa:
        reglas = reglas_de_plantilla((job.config or {}).get("templates"))
        por_etapa: dict[str, dict] = {}
        por_plantilla: dict[str, dict[str, int]] = {}
        for uid, (r, _, _) in etapa.items():
            url, path, clics = urls.get(uid, ("", "/", None))
            e = por_etapa.setdefault(r or "sin_respuesta", {"paginas": 0, "clics": 0})
            e["paginas"] += 1
            e["clics"] += int(clics or 0)
            pl = por_plantilla.setdefault(plantilla_de(path or "/", reglas), {})
            pl[r or "sin_respuesta"] = pl.get(r or "sin_respuesta", 0) + int(clics or 0)
        plantillas = sorted(por_plantilla.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
        embudo = {"por_etapa": por_etapa,
                  "por_plantilla": [{"plantilla": k, "clics_por_etapa": v} for k, v in plantillas[:30]],
                  "orientativo": umbrales["etapa_embudo"] is None}
    return {"resumen": resumen, "paginas": paginas[:max(0, int(limite))], "embudo": embudo}


__all__ = [
    "MAX_BUSQUEDAS_POR_PAGINA", "NO_CUENTAN", "estados_intencion_pagina",
    "paginas_con_algo_que_juzgar", "busquedas_por_url", "estados_intencion_busquedas",
    "paginas_con_busquedas", "motivo_sin_busquedas", "cobertura_de_la_ventana",
    "aviso_de_cobertura", "desajuste",
    "incidencias_intencion_desalineada", "intencion_frente_a_busquedas", "sitio_del_job",
]
