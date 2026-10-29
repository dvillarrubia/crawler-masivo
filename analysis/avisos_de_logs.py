"""Lo que Googlebot visita de verdad, cruzado con el censo (#35, decision 213).

Con el log mas reciente, ya leido, del cliente, del MISMO host que el censo
(con o sin `www.`; un log del blog no habla de la web) y de su EPOCA (se
solapa con el o le queda a `MAX_DIAS_ENTRE` dias); sin uno asi, nada:

- ``urls.googlebot_visitas`` y ``urls.googlebot_ultima`` de cada URL interna:
  las visitas de Googlebot VERIFICADO (su IP es de Google) en el periodo del
  log. 0 = el log no la vio; NULL = sin log de ese host.
- ``jobs.logs_resumen``: periodo, cobertura de las indexables, en que se va el
  presupuesto, los Googlebot falsos y lo que Google visita y el rastreo no
  encontro.
- ``indexable_sin_visitas_googlebot`` (warning, uno por plantilla): las
  indexables 200 del cuarto con mas PageRank a las que Googlebot no fue.
- ``googlebot_recibe_error`` (warning, uno por plantilla): el rastreo las vio
  en 200 y a Googlebot le respondieron con error en la mayoria de sus visitas.

**Lo que no se puede afirmar no se afirma** (decisiones 67, 70 y 89). Los dos
avisos solo salen si el log es de la epoca del censo (se solapa con el, o
acaba como mucho `MAX_DIAS_ENTRE` dias antes o empieza despues). La ausencia
pide ademas 7 dias de log, y que el log VEA a Googlebot: con muy pocas visitas
verificadas, o cubriendo menos del 10 % de las indexables, el problema es el
log (detras de una CDN, de otro host, un formato que no captura la IP buena),
no el sitio, y avisar en todo el cuartil alto seria falso.

Agrupados por plantilla (decisiones 36 y 53): si Google no visita las fichas,
es UN hallazgo con sus paginas, no cien. Casan entre censos por la plantilla
(`shared.comparacion.ANCLADOS_POR_GRUPO`). Best-effort entero (decision 40).
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, text

logger = logging.getLogger(__name__)

# El rastreador de verdad; las variantes (imagenes, inspeccion, AdsBot) no
# cuentan para la cobertura ni para el presupuesto del indice.
BOT_PRINCIPAL = "googlebot"
MIN_DIAS_AUSENCIA = 7
MIN_VISITAS_VERIFICADAS = 100
MIN_COBERTURA = 0.10
MIN_VISITAS_ERROR = 2
MAX_DIAS_ENTRE = 30
MAX_NO_EN_CENSO = 30
MAX_EJEMPLOS = 10
# Filas del reparto por plantilla en `logs_resumen` (las de mas visitas).
MAX_PLANTILLAS = 30
# `shared.comparacion.ANCLADOS_POR_GRUPO` repite la lista (la API no importa
# `analysis/`); un test comprueba que coinciden.
TIPOS_ANCLADOS_POR_GRUPO = ("indexable_sin_visitas_googlebot", "googlebot_recibe_error")


def avisar_sin_tumbar(an) -> None:
    pendientes = list(getattr(an, "_pending_issues", []) or [])
    try:
        with an.session.begin_nested():
            avisar(an)
    except Exception:
        an._pending_issues = pendientes
        logger.exception("Logs del job %s: fallo; el analisis sigue", an.job_id)


def _sin_www(h: str | None) -> str:
    return (h or "").lower().removeprefix("www.")


def _de_su_epoca(imp, censo: datetime) -> bool:
    if not imp.desde or not imp.hasta:
        return False
    desde = imp.desde if imp.desde.tzinfo else imp.desde.replace(tzinfo=timezone.utc)
    hasta = imp.hasta if imp.hasta.tzinfo else imp.hasta.replace(tzinfo=timezone.utc)
    return desde - timedelta(days=MAX_DIAS_ENTRE) <= censo <= hasta + timedelta(days=MAX_DIAS_ENTRE)


def importacion_para(session, job):
    """El log ya leido del cliente del job, de su host y de la epoca del censo
    (el mas reciente que lo cumpla). Un log de octubre no dice nada de un censo
    de marzo: ni avisos ni columnas."""
    from shared.models import LogImport

    if not getattr(job, "cliente_id", None):
        return None
    hosts = {_sin_www(urlsplit(s).hostname) for s in (job.seeds or []) if s}
    censo = _dia_del_censo(job)
    for imp in (session.query(LogImport)
                .filter(LogImport.cliente_id == job.cliente_id, LogImport.estado == "hecho")
                .order_by(LogImport.created_at.desc(), LogImport.id.desc())):
        if (not hosts or _sin_www(imp.host) in hosts) and _de_su_epoca(imp, censo):
            return imp
    return None


def _grupo(host: str, plantilla: str, tipo: str) -> str:
    return hashlib.sha256(f"{host}|{plantilla}|{tipo}".encode()).hexdigest()[:64]


def _dia_del_censo(job) -> datetime:
    d = job.started_at or job.created_at or datetime.now(timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def avisar(an) -> dict[str, Any] | None:
    from shared.backlinks import clave_backlink, indice_de_censo, rango_de
    from shared.models import Job, LogUrl, Url
    from shared.plantillas import firma_de_ruta, plantilla_de, reglas_de_plantilla

    an._flush_issues()
    job = an.session.get(Job, an.job_id)
    # Lo de un analisis anterior fuera primero: sin log, o con otro, no puede quedar.
    an.session.execute(text(
        "UPDATE urls SET googlebot_visitas = NULL, googlebot_ultima = NULL "
        "WHERE job_id = :jid AND googlebot_visitas IS NOT NULL"), {"jid": an.job_id})
    imp = importacion_para(an.session, job)
    if imp is None:
        job.logs_resumen = None
        return None

    urls = an.session.execute(
        select(Url.id, Url.url, Url.path, Url.status_code, Url.indexable, Url.redirect_url, Url.is_html,
               Url.pagerank_score, Url.noindex)
        .where(Url.job_id == an.job_id, Url.is_internal.is_(True))).all()
    indice = indice_de_censo([(u.id, u.url, rango_de(u.status_code, u.indexable, u.redirect_url)) for u in urls])
    por_id = {u.id: u for u in urls}

    visitas: dict[int, int] = defaultdict(int)
    ultima: dict[int, Any] = {}
    codigos_por_url: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    no_en_censo: dict[str, dict[str, Any]] = {}
    reparto: dict[str, int] = defaultdict(int)
    falsas = sin_comprobar = movil = total = 0
    for f in (an.session.query(LogUrl.ruta, LogUrl.clave, LogUrl.verificado, LogUrl.visitas, LogUrl.codigos,
                               LogUrl.movil, LogUrl.ultimo)
              .filter(LogUrl.import_id == imp.id, LogUrl.bot == BOT_PRINCIPAL)):
        if f.verificado is False:
            falsas += f.visitas
            continue
        if f.verificado is None:
            sin_comprobar += f.visitas
            continue
        total += f.visitas
        movil += f.movil or 0
        uid = indice.por_clave_suelta(f.clave or clave_backlink(f"https://{imp.host}{f.ruta}"))
        if "?" in f.ruta:
            reparto["con_parametros"] += f.visitas
        if uid is None:
            reparto["no_en_censo"] += f.visitas
            n = no_en_censo.setdefault(f.ruta, {"ruta": f.ruta, "visitas": 0, "codigos": defaultdict(int)})
            n["visitas"] += f.visitas
            for c, k in (f.codigos or {}).items():
                n["codigos"][c] += k
            continue
        visitas[uid] += f.visitas
        if f.ultimo is not None and (uid not in ultima or f.ultimo > ultima[uid]):
            ultima[uid] = f.ultimo
        for c, k in (f.codigos or {}).items():
            codigos_por_url[uid][c] += k
        u = por_id[uid]
        if u.redirect_url or (u.status_code is not None and 300 <= u.status_code < 400):
            reparto["redirecciones"] += f.visitas
        elif u.status_code is None or u.status_code >= 400:
            reparto["errores"] += f.visitas
        elif u.is_html and u.indexable:
            reparto["indexables"] += f.visitas
        else:
            reparto["no_indexables"] += f.visitas

    # Las columnas: 0 a todas las internas en UNA sentencia, y las visitadas
    # por una tabla temporal con solo ellas.
    an.session.execute(text("UPDATE urls SET googlebot_visitas = 0 WHERE job_id = :jid AND is_internal"),
                       {"jid": an.job_id})
    if visitas:
        an.session.execute(text("DROP TABLE IF EXISTS logs_tmp"))
        an.session.execute(text("CREATE TEMP TABLE logs_tmp (id BIGINT PRIMARY KEY, n INTEGER, ultima TIMESTAMPTZ)"))
        filas = [{"id": int(k), "n": int(n), "ultima": ultima.get(k)} for k, n in visitas.items()]
        for i in range(0, len(filas), 10_000):
            an.session.execute(text("INSERT INTO logs_tmp (id, n, ultima) VALUES (:id, :n, :ultima)"),
                               filas[i:i + 10_000])
        an.session.execute(text("UPDATE urls SET googlebot_visitas = t.n, googlebot_ultima = t.ultima "
                                "FROM logs_tmp t WHERE urls.id = t.id"))
        an.session.execute(text("DROP TABLE IF EXISTS logs_tmp"))

    dias = ((imp.hasta - imp.desde).total_seconds() / 86400 if imp.desde and imp.hasta else 0)
    indexables = [u for u in urls if u.status_code == 200 and u.is_html and u.indexable and not u.noindex]
    visitadas = sum(1 for u in indexables if visitas.get(u.id))
    cobertura = visitadas / len(indexables) if indexables else 0.0
    censo = _dia_del_censo(job)
    desde = imp.desde.replace(tzinfo=imp.desde.tzinfo or timezone.utc) if imp.desde else None
    hasta = imp.hasta.replace(tzinfo=imp.hasta.tzinfo or timezone.utc) if imp.hasta else None
    de_su_epoca = _de_su_epoca(imp, censo)
    cortado = int((imp.resumen or {}).get("fuera_del_tope_googlebot") or 0) > 0
    afirmable = (de_su_epoca and not cortado and dias >= MIN_DIAS_AUSENCIA
                 and total >= MIN_VISITAS_VERIFICADAS and cobertura >= MIN_COBERTURA)
    motivo_no = None
    if not de_su_epoca:
        motivo_no = "el log no es de la época del rastreo"
    elif cortado:
        motivo_no = "el log tenía más rutas de Googlebot de las que se guardan"
    elif dias < MIN_DIAS_AUSENCIA:
        motivo_no = f"el log cubre menos de {MIN_DIAS_AUSENCIA} días"
    elif total < MIN_VISITAS_VERIFICADAS or cobertura < MIN_COBERTURA:
        motivo_no = ("el log apenas ve a Googlebot verificado: puede estar detrás de una CDN, ser de otro host "
                     "o capturar otra IP")

    def _plantilla(u):
        return (urlsplit(u.url).hostname or "", firma_de_ruta(u.path or urlsplit(u.url).path))

    avisos = 0
    if afirmable:
        # El cuarto de arriba por POSICION, no por `>= p75`: con empates en
        # una escala entera, el p75 podia ser el minimo y entraban todas.
        orden = sorted((u for u in indexables if u.pagerank_score is not None),
                       key=lambda u: (-u.pagerank_score, u.id))
        importantes = orden[:max(1, len(orden) // 4)] if orden else []
        grupos: dict[tuple[str, str], list] = defaultdict(list)
        for u in importantes:
            if not visitas.get(u.id):
                grupos[_plantilla(u)].append(u)
        for (host, plantilla), us in grupos.items():
            us.sort(key=lambda u: (-u.pagerank_score, u.id))
            an._add_issue(us[0].id, "indexable_sin_visitas_googlebot", "warning",
                          {"plantilla": plantilla, "host": host, "paginas_afectadas": len(us),
                           "ejemplos": [u.url for u in us[:MAX_EJEMPLOS]], "dias_de_log": round(dias, 1)},
                          group_key=_grupo(host, plantilla, "indexable_sin_visitas_googlebot"))
            avisos += 1
    if de_su_epoca:
        errores: dict[tuple[str, str], list] = defaultdict(list)
        for uid, cods in codigos_por_url.items():
            u = por_id[uid]
            n = sum(cods.values())
            malos = sum(k for c, k in cods.items() if c[:1] in ("4", "5"))
            if u.status_code == 200 and n >= MIN_VISITAS_ERROR and malos * 2 > n:
                errores[_plantilla(u)].append((u, n, malos, dict(cods)))
        for (host, plantilla), us in errores.items():
            us.sort(key=lambda x: (-x[2], x[0].id))
            codigos: dict[str, int] = defaultdict(int)
            for _, _, _, cods in us:
                for c, k in cods.items():
                    codigos[c] += k
            an._add_issue(us[0][0].id, "googlebot_recibe_error", "warning",
                          {"plantilla": plantilla, "host": host, "paginas_afectadas": len(us),
                           "ejemplos": [x[0].url for x in us[:MAX_EJEMPLOS]], "codigos": dict(codigos)},
                          group_key=_grupo(host, plantilla, "googlebot_recibe_error"))
            avisos += 1
    an._flush_issues()

    por_plantilla = reparto_por_plantilla(
        urls, visitas, indexables, reglas_de_plantilla((job.config or {}).get("templates")),
        plantilla_de)

    lista_fuera = sorted(no_en_censo.values(), key=lambda x: -x["visitas"])[:MAX_NO_EN_CENSO]
    for x in lista_fuera:
        x["codigos"] = dict(x["codigos"])
    job.logs_resumen = {
        "import_id": imp.id, "fichero": imp.fichero, "host": imp.host,
        "desde": desde.isoformat() if desde else None, "hasta": hasta.isoformat() if hasta else None,
        "dias": round(dias, 1), "de_su_epoca": de_su_epoca, "afirmable_ausencia": afirmable,
        "no_se_afirma": motivo_no,
        "visitas": total, "falsas": falsas, "sin_comprobar": sin_comprobar,
        "parte_movil": round(movil / total, 4) if total else None,
        "indexables": len(indexables), "indexables_visitadas": visitadas,
        "reparto": dict(reparto), "por_plantilla": por_plantilla, "no_en_censo": lista_fuera,
        "apartado": imp.resumen or {},
        "otros_bots": {b: d for b, d in (imp.bots or {}).items() if b != BOT_PRINCIPAL},
    }
    logger.info("Logs del job %s: %d visitas de Googlebot verificado, %d avisos", an.job_id, total, avisos)
    return job.logs_resumen


def reparto_por_plantilla(urls, visitas: dict, indexables, reglas, plantilla_de) -> list[dict[str, Any]]:
    """En que tipo de pagina se va el presupuesto de Googlebot (#35, #12).

    Por plantilla (`config.templates` del cliente y, si no casa ninguna, la
    forma de la ruta: la misma `plantilla_de` que los avisos), las visitas de
    Googlebot verificado a URLs del censo, cuantas URLs tiene y cuantas
    visito, y lo mismo de las indexables. Solo lo que el censo tiene: lo que
    Google visita y el rastreo no encontro va aparte (`no_en_censo`). Las
    `MAX_PLANTILLAS` de mas visitas, y las que no tienen ninguna si tienen
    indexables (que Google no pase por una plantilla entera es justo lo que
    hay que ver).
    """
    ids_indexables = {u.id for u in indexables}
    filas: dict[str, dict[str, Any]] = {}
    for u in urls:
        nombre = plantilla_de(u.path or urlsplit(u.url).path, reglas)
        f = filas.setdefault(nombre, {"plantilla": nombre, "visitas": 0, "urls": 0, "urls_visitadas": 0,
                                      "indexables": 0, "indexables_visitadas": 0})
        n = visitas.get(u.id, 0)
        f["urls"] += 1
        f["visitas"] += n
        f["urls_visitadas"] += 1 if n else 0
        if u.id in ids_indexables:
            f["indexables"] += 1
            f["indexables_visitadas"] += 1 if n else 0
    total = sum(f["visitas"] for f in filas.values())
    for f in filas.values():
        f["parte"] = round(f["visitas"] / total, 4) if total else None
    orden = sorted(filas.values(), key=lambda f: (-f["visitas"], -f["indexables"], f["plantilla"]))
    arriba = orden[:MAX_PLANTILLAS]
    vistas = {f["plantilla"] for f in arriba}
    sin_visitas = [f for f in orden[MAX_PLANTILLAS:] if not f["visitas"] and f["indexables"]
                   and f["plantilla"] not in vistas][:MAX_PLANTILLAS]
    return arriba + sin_visitas
