"""Rastreo incremental de los programados de un perfil (#36 F4, decision 220).

Un perfil con `config.incremental.activo` no rastrea el sitio entero cada
ciclo: pide lo que toca y copia el resto del censo anterior. Tres piezas, en
el orden en que corren:

1. **Seleccion** (`preparar_job`, al crear el job de un disparo): decide si el
   ciclo es completo o incremental y, si es incremental, rellena
   `job_frontera` desde `url_state` con lo que toca, lo fijo y una muestra de
   control al azar del resto. El spider lee eso y su job; no sabe de perfiles.
2. **Arrastre** (`analysis/arrastre.py`, despues del rastreo y antes del
   analisis): completa los 304 y copia lo no pedido. El job queda como una
   foto completa, asi que el analizador, el PageRank y la comparacion no
   cambian.
3. **Estado** (`actualizar_estado`, despues del analisis de cualquier job del
   perfil, completo o incremental): firmas, intervalo adaptativo y en que
   ciclo vuelve a tocar cada URL; y si el siguiente tiene que ser completo
   (la muestra de control cambio mucho, o el robots.txt).

Un ciclo es un job del perfil, no un dia: «Lanzar ahora» y un cron de lunes y
jueves cuentan igual, y no hace falta leer el cron para saber que toca.

Las decisiones de las que depende cada regla (que el sitio varia solo, que no
se afirma lo que no se ha visto) estan en la decision 220.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Lo mismo que `api.schemas.IncrementalConfig`: el worker no lleva Pydantic.
# `config_por_defecto.json` lo trae igual; esto es por si llega un job
# anterior sin la clave.
DEFECTO: dict[str, Any] = {
    "activo": False,
    "completo_cada": 4,
    "muestra_control": 0.1,
    "umbral_control": 0.2,
    "intervalo_max": 4.0,
}

# Suavizado del intervalo (StormCrawler, `AdaptiveScheduler`): sin cambio
# crece x1,4; con cambio se acerca al minimo un 20 % por ciclo, sin rebotar.
CRECE = 1.4
ACERCA = 0.2
INTERVALO_MIN = 1.0

# Lo que sale en `jobs.incremental.motivo` de un completo.
SIN_BASE = "sin_censo_anterior"
BASE_TRUNCADA = "censo_anterior_truncado"
SIN_ESTADO = "sin_estado"
TOCA_COMPLETO = "toca_completo"
CONFIG_CAMBIADA = "config_cambiada"
FORZADO = "forzado_por_el_anterior"
SIN_PERFIL = "sin_perfil"
ERROR = "error_al_preparar"

# Lo que no entra en la huella de la configuracion: el propio bloque
# incremental (cambiar la muestra de control no invalida lo copiado).
_FUERA_DE_LA_HUELLA = ("incremental",)


def config_de(config: dict | None) -> dict[str, Any]:
    """`config.incremental` con los defaults de lo que falte."""
    propio = (config or {}).get("incremental") or {}
    return {**DEFECTO, **{k: v for k, v in propio.items() if v is not None}}


def huella(seeds: Iterable[str] | None, config: dict | None) -> str:
    """La configuracion que hace que dos censos sean comparables.

    Si cambia (otro `max_depth`, otras exclusiones, otras semillas), lo
    copiado del anterior es de otro rastreo: el ciclo es completo.
    """
    datos = {k: v for k, v in (config or {}).items() if k not in _FUERA_DE_LA_HUELLA}
    texto = json.dumps({"seeds": sorted(seeds or []), "config": datos},
                       sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def siguiente_intervalo(intervalo: float | None, cambio: bool, maximo: float) -> float:
    """Cada cuantos ciclos se pide una URL despues de verla. Funcion pura.

    Con cambio se acerca al minimo (un ciclo) sin saltar a el: una pagina que
    cambia una vez no se pide en todos los ciclos para siempre. Sin cambio
    crece x1,4 hasta `maximo`.
    """
    i = max(INTERVALO_MIN, float(intervalo or INTERVALO_MIN))
    if cambio:
        return max(INTERVALO_MIN, (1 - ACERCA) * i + ACERCA * INTERVALO_MIN)
    return min(i * CRECE, max(INTERVALO_MIN, float(maximo)))


def ciclos_hasta(intervalo: float) -> int:
    """Cuantos ciclos faltan para volver a pedirla: al menos uno."""
    return max(1, int(math.floor(intervalo + 1e-9)))


def decidir(cfg: dict[str, Any], base: dict[str, Any] | None,
            huella_actual: str) -> tuple[str, str | None]:
    """`("incremental", None)` o `("completo", motivo)`. Funcion pura.

    `base` es el ultimo censo terminado del perfil que paso por aqui:
    `{"finish_reason", "huella", "ciclos_desde_completo", "forzar_completo",
    "estado_ok"}`. Sin base, o con una que no se puede usar, completo: lo que
    se copia tiene que ser una foto completa y comparable.
    """
    if base is None:
        return "completo", SIN_BASE
    if base.get("finish_reason") != "finished":
        return "completo", BASE_TRUNCADA
    if not base.get("estado_ok"):
        return "completo", SIN_ESTADO
    if base.get("forzar_completo"):
        return "completo", FORZADO
    if base.get("huella") != huella_actual:
        return "completo", CONFIG_CAMBIADA
    if int(base.get("ciclos_desde_completo") or 0) + 1 >= int(cfg["completo_cada"]):
        return "completo", TOCA_COMPLETO
    return "incremental", None


# ---------------------------------------------------------------------------
# Seleccion
# ---------------------------------------------------------------------------
def _base_del_perfil(db, job):
    """El ultimo censo terminado del perfil con estado incremental."""
    from shared.models import Job

    return (
        db.query(Job)
        .filter(Job.profile_id == job.profile_id, Job.id != job.id,
                Job.status == "completed", Job.incremental.isnot(None))
        # Por creacion: es el orden de los ciclos. `completed_at` no lo es
        # (un job reanudado termina despues de otro mas nuevo).
        .order_by(Job.created_at.desc())
        .first()
    )


def preparar_job(db, job) -> dict[str, Any] | None:
    """Decide el modo del job de un disparo y, si es incremental, su frontera.

    Sin commit (lo hace quien llama, como el resto del disparo). Devuelve lo
    que se escribio en `jobs.incremental`, o None si el perfil no tiene el
    incremental activo. Si algo falla, el job sale COMPLETO con el motivo:
    rastrear de mas nunca deja una foto incompleta.
    """
    cfg = config_de(job.config)
    if not cfg.get("activo"):
        return None
    if job.profile_id is None:
        job.incremental = {"modo": "completo", "motivo": SIN_PERFIL, "ciclo": 0,
                           "ciclos_desde_completo": 0,
                           "huella": huella(job.seeds, job.config)}
        return job.incremental
    try:
        with db.begin_nested():
            datos = _preparar(db, job, cfg)
    except Exception:
        logger.exception("Job %s: no se pudo preparar el incremental; va completo", job.id)
        datos = {"modo": "completo", "motivo": ERROR, "ciclo": 0,
                 "ciclos_desde_completo": 0, "huella": huella(job.seeds, job.config)}
    job.incremental = datos
    return datos


def _preparar(db, job, cfg: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import case, func, insert, literal, or_, select

    from shared.models import JobFrontera, UrlState

    huella_actual = huella(job.seeds, job.config)
    base = _base_del_perfil(db, job)
    info_base = None
    if base is not None:
        inc = base.incremental or {}
        info_base = {
            "finish_reason": base.finish_reason,
            "huella": inc.get("huella"),
            "ciclos_desde_completo": inc.get("ciclos_desde_completo"),
            "forzar_completo": inc.get("forzar_completo"),
            "estado_ok": bool((inc.get("estado") or {}).get("actualizado")),
        }
    ciclo = int(((base.incremental or {}).get("ciclo") or 0) + 1) if base is not None else 1
    modo, motivo = decidir(cfg, info_base, huella_actual)
    datos: dict[str, Any] = {
        "modo": modo, "motivo": motivo, "ciclo": ciclo,
        "huella": huella_actual,
        "base_job_id": str(base.id) if base is not None else None,
    }
    if modo == "completo":
        datos["ciclos_desde_completo"] = 0
        return datos

    datos["ciclos_desde_completo"] = int(info_base.get("ciclos_desde_completo") or 0) + 1
    datos["prev_job_id"] = str(base.id)

    columnas = ["job_id", "url_hash", "url", "profundidad", "etag", "last_modified", "motivo"]
    del_perfil = UrlState.profile_id == job.profile_id
    toca = or_(UrlState.fijo.is_(True), UrlState.proximo_ciclo <= ciclo)

    def _desde(filtro, motivo_sql, limite=None):
        sel = select(literal(job.id), UrlState.url_hash, UrlState.url,
                     UrlState.profundidad, UrlState.etag, UrlState.last_modified,
                     motivo_sql).where(del_perfil, filtro)
        if limite is not None:
            sel = sel.order_by(func.random()).limit(limite)
        return insert(JobFrontera).from_select(columnas, sel)

    db.execute(_desde(toca, case((UrlState.fijo.is_(True), "fijo"), else_="toca")))
    resto = db.query(func.count()).select_from(UrlState).filter(del_perfil, ~toca).scalar() or 0
    total = db.query(func.count()).select_from(UrlState).filter(del_perfil).scalar() or 0
    n_control = int(math.ceil(resto * float(cfg["muestra_control"]))) if resto else 0
    if n_control:
        db.execute(_desde(~toca, literal("control"), limite=n_control))
    por_motivo = dict(
        db.query(JobFrontera.motivo, func.count())
        .filter(JobFrontera.job_id == job.id).group_by(JobFrontera.motivo).all()
    )
    datos["seleccion"] = {
        "conocidas": int(total),
        "toca": int(por_motivo.get("toca", 0)),
        "fijo": int(por_motivo.get("fijo", 0)),
        "control": int(por_motivo.get("control", 0)),
    }
    return datos


def anotar(db, job_id, **campos) -> None:
    """Mezcla `campos` en `jobs.incremental` (sin commit).

    Lee y reescribe la columna entera: JSON no es JSONB y no hay `||`. Solo lo
    llaman el worker y el arrastre del MISMO job, uno detras de otro.
    """
    from shared.models import Job

    job = db.get(Job, job_id)
    if job is None:
        return
    datos = dict(job.incremental or {})
    datos.update(campos)
    job.incremental = datos


def es_incremental(job) -> bool:
    return (getattr(job, "incremental", None) or {}).get("modo") == "incremental"


# ---------------------------------------------------------------------------
# Estado, despues del analisis
# ---------------------------------------------------------------------------
# Lo que no es la pagina (decision 103): no dice nada de si cambio.
_NO_VALIDAS = ("challenge_waf", "captcha", "rate_limited", "respuesta_binaria",
               "login_requerido")


def _es_error(status: int | None, clase: str | None) -> bool:
    return status is None or status >= 400 or (clase in _NO_VALIDAS)


def estado_nuevo(previo: dict | None, fila: dict, *, ciclo: int, maximo: float,
                 fijo: bool) -> tuple[dict[str, Any], bool | None]:
    """El `url_state` de una URL tras verla en el ciclo `ciclo`. Funcion pura.

    Devuelve `(estado, cambio)`: `cambio` es None si no se pidio (arrastrada)
    o no se puede saber (primera vez, o un error).

    - Arrastrada: no se vio; conserva su intervalo y su turno.
    - 304: el servidor dice que no cambio; crece el intervalo.
    - Pedida: cambio si su firma SEO o su contenido son otros.
    - Un error, un 4xx/5xx o un muro: se vuelve a pedir en el siguiente ciclo
      y sin cabeceras condicionales (lo que dijo antes ya no vale).
    """
    modo = fila.get("fetch_mode")
    base = dict(previo or {})
    estado: dict[str, Any] = {
        "url": fila["url"],
        "profundidad": fila.get("crawl_depth"),
        "fijo": fijo,
        "intervalo": float(base.get("intervalo") or INTERVALO_MIN),
        "proximo_ciclo": int(base.get("proximo_ciclo") or ciclo + 1),
        "sin_cambios": int(base.get("sin_cambios") or 0),
        "ultimo_cambio_ciclo": base.get("ultimo_cambio_ciclo"),
        "etag": base.get("etag"),
        "last_modified": base.get("last_modified"),
        "firma_contenido": base.get("firma_contenido"),
        "firma_seo": base.get("firma_seo"),
        "ultima_peticion": base.get("ultima_peticion"),
    }
    if modo == "carried":
        if previo is None:
            # No deberia pasar (lo arrastrado sale del censo anterior, que ya
            # tenia estado), pero si pasa, que toque en el siguiente.
            estado["proximo_ciclo"] = ciclo + 1
        return estado, None

    estado["ultima_peticion"] = fila.get("last_crawled_at")
    if modo == "not_modified":
        cambio = False
        estado["etag"] = fila.get("etag") or estado["etag"]
        estado["last_modified"] = fila.get("last_modified") or estado["last_modified"]
    elif _es_error(fila.get("status_code"), fila.get("response_class")):
        estado.update(intervalo=INTERVALO_MIN, proximo_ciclo=ciclo + 1,
                      etag=None, last_modified=None, sin_cambios=0)
        return estado, None
    else:
        nuevas = (fila.get("firma_contenido"), fila.get("firma_seo"))
        antes = (base.get("firma_contenido"), base.get("firma_seo"))
        cambio = None if previo is None or antes == (None, None) else nuevas != antes
        estado["firma_contenido"], estado["firma_seo"] = nuevas
        estado["etag"] = fila.get("etag")
        estado["last_modified"] = fila.get("last_modified")

    if cambio is None:
        estado["intervalo"] = INTERVALO_MIN
        estado["sin_cambios"] = 0
    else:
        estado["intervalo"] = siguiente_intervalo(estado["intervalo"], cambio, maximo)
        estado["sin_cambios"] = 0 if cambio else estado["sin_cambios"] + 1
        if cambio:
            estado["ultimo_cambio_ciclo"] = ciclo
    estado["proximo_ciclo"] = ciclo + ciclos_hasta(estado["intervalo"])
    return estado, cambio


def _firmas_seo(db, job_id) -> dict[str, str]:
    """Firma de lo SEO de cada URL pedida del job, por `url_hash`.

    Estado, destino, title, description, canonical, robots, los H1 y los
    enlaces salientes (destino, rel y si se siguen): lo que, si cambia, cambia
    el censo. En SQL y por `job_id` (decision 37): son millones de enlaces.
    """
    from sqlalchemy import text

    filas = db.execute(text("""
        WITH enl AS (
            SELECT l.from_url_id,
                   md5(string_agg(l.to_url_hash || ':' || coalesce(l.rel, '') || ':'
                                  || coalesce(l.follow::text, '') || ':'
                                  || coalesce(l.repeticiones, 1)::text,
                                  ',' ORDER BY l.to_url_hash, l.rel, l.follow)) AS f
              FROM links l
             WHERE l.job_id = :j
             GROUP BY l.from_url_id
        ), h1 AS (
            SELECT h.url_id, string_agg(coalesce(h.text, ''), '|' ORDER BY h.position) AS t
              FROM headings h
              JOIN urls u ON u.id = h.url_id AND u.job_id = :j
             WHERE h.tag = 'h1'
             GROUP BY h.url_id
        )
        SELECT u.url_hash,
               md5(concat_ws(chr(31), coalesce(u.status_code::text, ''),
                             coalesce(u.redirect_url, ''), coalesce(m.title, ''),
                             coalesce(m.meta_description, ''),
                             coalesce(m.canonical_href, ''), coalesce(m.meta_robots, ''),
                             coalesce(m.meta_robots_googlebot, ''),
                             coalesce(m.x_robots_tag, ''), coalesce(h1.t, ''),
                             coalesce(enl.f, '')))
          FROM urls u
          LEFT JOIN html_meta m ON m.url_id = u.id
          LEFT JOIN h1 ON h1.url_id = u.id
          LEFT JOIN enl ON enl.from_url_id = u.id
         WHERE u.job_id = :j
           AND u.fetch_mode IS DISTINCT FROM 'carried'
           AND u.fetch_mode IS DISTINCT FROM 'not_modified'
    """), {"j": str(job_id)}).all()
    return {h: f for h, f in filas}


def _robots(db, job_id) -> dict[str, str | None]:
    from shared.models import RobotsTxt

    return {u: s for u, s in db.query(RobotsTxt.url, RobotsTxt.sha256)
            .filter(RobotsTxt.job_id == job_id).all()}


def _hashes_de_semillas(seeds) -> set[str]:
    try:
        from seo_crawler.extractors import compute_url_hash, normalize_url
    except ImportError:  # fuera de la imagen del crawler
        from crawler.seo_crawler.extractors import compute_url_hash, normalize_url
    salida = set()
    for s in seeds or []:
        try:
            salida.add(compute_url_hash(normalize_url(s)))
        except Exception:
            continue
    return salida


def actualizar_estado(db, job_id) -> dict[str, Any] | None:
    """`url_state` del perfil tras el analisis del job. Con commit.

    Solo para jobs con `jobs.incremental` (de un perfil con el incremental
    activo), completos o incrementales. Lo que no esta en el job sale de
    `url_state`: si vuelve a aparecer, es nueva. Devuelve el resumen que se
    deja en `jobs.incremental.estado`.
    """
    from shared.models import Job, JobFrontera, Url, UrlState

    job = db.get(Job, job_id)
    if job is None or not job.incremental or job.profile_id is None:
        return None
    inc = dict(job.incremental)
    cfg = config_de(job.config)
    ciclo = int(inc.get("ciclo") or 0)
    maximo = float(cfg["intervalo_max"])

    firmas = _firmas_seo(db, job.id)
    semillas = _hashes_de_semillas(job.seeds)
    # Hubs: los de mas enlaces salientes (percentil 95). Se piden siempre:
    # son por donde se descubre lo nuevo de un sitio.
    outlinks = sorted(
        (n or 0) for (n,) in db.query(Url.outlinks_count)
        .filter(Url.job_id == job.id, Url.is_html.is_(True), Url.is_internal.is_(True))
    )
    p95 = outlinks[int(0.95 * (len(outlinks) - 1))] if outlinks else None
    # Y por encima de la mediana: si casi todas las paginas tienen los mismos
    # enlaces (el menu), el percentil 95 es ese numero y todo saldria fijo.
    mediana = outlinks[len(outlinks) // 2] if outlinks else None
    control = {h for (h,) in db.query(JobFrontera.url_hash).filter(
        JobFrontera.job_id == job.id, JobFrontera.motivo == "control")}

    previos = {
        e.url_hash: {c: getattr(e, c) for c in (
            "intervalo", "proximo_ciclo", "sin_cambios", "ultimo_cambio_ciclo", "etag",
            "last_modified", "firma_contenido", "firma_seo", "ultima_peticion")}
        for e in db.query(UrlState).filter(UrlState.profile_id == job.profile_id)
    }

    vistos: set[str] = set()
    resumen = {"urls": 0, "cambiadas": 0, "sin_cambios": 0, "no_se_sabe": 0,
               "arrastradas": 0, "fijas": 0}
    control_medidas = control_cambiadas = 0
    filas = (
        db.query(Url.url_hash, Url.url, Url.crawl_depth, Url.fetch_mode, Url.status_code,
                 Url.response_class, Url.content_hash, Url.etag, Url.last_modified,
                 Url.last_crawled_at, Url.outlinks_count, Url.path, Url.is_html)
        .filter(Url.job_id == job.id)
        .yield_per(5000)
    )
    nuevos: list[dict[str, Any]] = []
    for f in filas:
        vistos.add(f.url_hash)
        fila = {
            "url": f.url, "crawl_depth": f.crawl_depth, "fetch_mode": f.fetch_mode,
            "status_code": f.status_code, "response_class": f.response_class,
            "firma_contenido": f.content_hash, "firma_seo": firmas.get(f.url_hash),
            "etag": f.etag, "last_modified": f.last_modified,
            "last_crawled_at": f.last_crawled_at,
        }
        fijo = (f.url_hash in semillas or (f.path or "/") == "/"
                or (p95 is not None and f.is_html
                    and (f.outlinks_count or 0) >= p95
                    and (f.outlinks_count or 0) > mediana))
        estado, cambio = estado_nuevo(previos.get(f.url_hash), fila, ciclo=ciclo,
                                      maximo=maximo, fijo=fijo)
        estado.update(profile_id=job.profile_id, url_hash=f.url_hash, last_job_id=job.id)
        nuevos.append(estado)
        resumen["urls"] += 1
        resumen["fijas"] += int(fijo)
        if f.fetch_mode == "carried":
            resumen["arrastradas"] += 1
        elif cambio is None:
            resumen["no_se_sabe"] += 1
        elif cambio:
            resumen["cambiadas"] += 1
        else:
            resumen["sin_cambios"] += 1
        if f.url_hash in control and cambio is not None:
            control_medidas += 1
            control_cambiadas += int(cambio)

    _guardar_estado(db, job.profile_id, nuevos)
    desaparecidas = [h for h in previos if h not in vistos]
    for i in range(0, len(desaparecidas), 5000):
        db.query(UrlState).filter(
            UrlState.profile_id == job.profile_id,
            UrlState.url_hash.in_(desaparecidas[i:i + 5000]),
        ).delete(synchronize_session=False)
    resumen["fuera"] = len(desaparecidas)

    forzar = None
    if control_medidas and control_cambiadas / control_medidas > float(cfg["umbral_control"]):
        # La muestra de lo que no tocaba ha cambiado mucho: casi seguro una
        # plantilla. Lo copiado del anterior ya no se parece al sitio.
        forzar = "muestra_control"
    base_id = inc.get("base_job_id")
    if forzar is None and base_id:
        antes, ahora = _robots(db, base_id), _robots(db, job.id)
        if any(ahora.get(u) != s for u, s in antes.items() if u in ahora):
            forzar = "robots_cambiado"
    resumen["control"] = {"medidas": control_medidas, "cambiadas": control_cambiadas}
    resumen["actualizado"] = True
    inc["estado"] = resumen
    inc["forzar_completo"] = forzar
    job.incremental = inc
    db.commit()
    logger.info("Job %s: estado incremental del perfil al dia (%s)", job.id, resumen)
    return resumen


def _guardar_estado(db, profile_id, filas: list[dict[str, Any]]) -> None:
    """Upsert por (perfil, url_hash). En Postgres, `ON CONFLICT`; fuera, a mano."""
    from shared.models import UrlState

    if not filas:
        return
    dialecto = db.get_bind().dialect.name
    if dialecto.startswith("postgres"):
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        columnas = [c.name for c in UrlState.__table__.columns]
        for i in range(0, len(filas), 2000):
            lote = [{c: f.get(c) for c in columnas} for f in filas[i:i + 2000]]
            sentencia = pg_insert(UrlState).values(lote)
            sentencia = sentencia.on_conflict_do_update(
                index_elements=["profile_id", "url_hash"],
                set_={c: sentencia.excluded[c] for c in columnas
                      if c not in ("profile_id", "url_hash")},
            )
            db.execute(sentencia)
        return
    for f in filas:
        db.merge(UrlState(**f))

