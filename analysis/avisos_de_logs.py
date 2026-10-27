"""Lo que Googlebot visita de verdad, cruzado con el censo (#35, decision 213).

Con la importacion de logs mas reciente (y ya leida) del cliente:

- ``urls.googlebot_visitas`` y ``urls.googlebot_ultima`` de cada URL interna:
  las visitas de Googlebot VERIFICADO (su IP es de Google) en el periodo del
  log. 0 = el log no la vio; NULL = el cliente no tiene logs.
- ``jobs.logs_resumen``: periodo, cobertura de las indexables, en que se va el
  presupuesto (indexables, no indexables, redirecciones, errores, lo que el
  rastreo no encontro, con parametros), los Googlebot falsos y las URLs que
  Google visita y el rastreo no encontro.
- ``indexable_sin_visitas_googlebot`` (warning): indexable 200 del cuartil alto
  de PageRank a la que Googlebot no fue en todo el periodo. Solo con 7 dias de
  log o mas: en tres dias no ir a una pagina no dice nada.
- ``googlebot_recibe_error`` (warning): el rastreo la vio en 200 y a Googlebot
  le respondio con error (5xx o 4xx) en la mayoria de sus visitas: el servidor
  trata distinto a Google, o falla a ratos.

Best-effort entero (decision 40). Un aviso por pagina; los dos son
`NO_COMPARABLES` entre censos: dependen del log subido, no del sitio.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select, text

logger = logging.getLogger(__name__)

# El rastreador de verdad; las variantes (imagenes, inspeccion, AdsBot) no
# cuentan para la cobertura ni para el presupuesto del indice.
BOT_PRINCIPAL = "googlebot"
MIN_DIAS_AUSENCIA = 7
MIN_VISITAS_ERROR = 2
MAX_NO_EN_CENSO = 30


def avisar_sin_tumbar(an) -> None:
    pendientes = list(getattr(an, "_pending_issues", []) or [])
    try:
        with an.session.begin_nested():
            avisar(an)
    except Exception:
        an._pending_issues = pendientes
        logger.exception("Logs del job %s: fallo; el analisis sigue", an.job_id)


def ultima_importacion(session, cliente_id):
    from shared.models import LogImport

    if not cliente_id:
        return None
    return (session.query(LogImport)
            .filter(LogImport.cliente_id == cliente_id, LogImport.estado == "hecho")
            .order_by(LogImport.created_at.desc(), LogImport.id.desc()).first())


def avisar(an) -> dict[str, Any] | None:
    from shared.backlinks import clave_backlink, indice_de_censo, rango_de
    from shared.models import Job, LogUrl, Url

    an._flush_issues()
    job = an.session.get(Job, an.job_id)
    # Lo de un analisis anterior fuera primero: sin logs, o con otros, no
    # puede quedar.
    an.session.execute(text(
        "UPDATE urls SET googlebot_visitas = NULL, googlebot_ultima = NULL "
        "WHERE job_id = :jid AND googlebot_visitas IS NOT NULL"), {"jid": an.job_id})
    imp = ultima_importacion(an.session, getattr(job, "cliente_id", None))
    if imp is None:
        job.logs_resumen = None
        return None

    urls = an.session.execute(
        select(Url.id, Url.url, Url.status_code, Url.indexable, Url.redirect_url, Url.is_html,
               Url.pagerank_score, Url.noindex)
        .where(Url.job_id == an.job_id, Url.is_internal.is_(True))).all()
    indice = indice_de_censo([(u.id, u.url, rango_de(u.status_code, u.indexable, u.redirect_url)) for u in urls])
    por_id = {u.id: u for u in urls}

    visitas: dict[int, int] = defaultdict(int)
    ultima: dict[int, Any] = {}
    codigos_por_url: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    no_en_censo: dict[str, dict[str, Any]] = {}
    reparto = defaultdict(int)
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

    # Las columnas, en bloque: 0 a las internas que el log no vio.
    an.session.execute(text("DROP TABLE IF EXISTS logs_tmp"))
    an.session.execute(text("CREATE TEMP TABLE logs_tmp (id BIGINT PRIMARY KEY, n INTEGER, ultima TIMESTAMPTZ)"))
    filas = [{"id": int(u.id), "n": int(visitas.get(u.id, 0)), "ultima": ultima.get(u.id)} for u in urls]
    for i in range(0, len(filas), 10_000):
        an.session.execute(text("INSERT INTO logs_tmp (id, n, ultima) VALUES (:id, :n, :ultima)"), filas[i:i + 10_000])
    an.session.execute(text("UPDATE urls SET googlebot_visitas = t.n, googlebot_ultima = t.ultima "
                            "FROM logs_tmp t WHERE urls.id = t.id"))
    an.session.execute(text("DROP TABLE IF EXISTS logs_tmp"))

    dias = ((imp.hasta - imp.desde).total_seconds() / 86400 if imp.desde and imp.hasta else 0)
    indexables = [u for u in urls if u.status_code == 200 and u.is_html and u.indexable and not u.noindex]
    visitadas = sum(1 for u in indexables if visitas.get(u.id))
    afirmable = dias >= MIN_DIAS_AUSENCIA

    avisos = 0
    if afirmable and indexables:
        scores = sorted(u.pagerank_score for u in indexables if u.pagerank_score is not None)
        p75 = scores[int(len(scores) * 0.75)] if scores else None
        for u in indexables:
            if p75 is not None and u.pagerank_score is not None and u.pagerank_score >= p75 and not visitas.get(u.id):
                an._add_issue(u.id, "indexable_sin_visitas_googlebot", "warning",
                              {"pagerank_score": u.pagerank_score, "dias_de_log": round(dias, 1)})
                avisos += 1
    for uid, cods in codigos_por_url.items():
        u = por_id[uid]
        n = sum(cods.values())
        malos = sum(k for c, k in cods.items() if c[:1] in ("4", "5"))
        if u.status_code == 200 and n >= MIN_VISITAS_ERROR and malos * 2 > n:
            an._add_issue(uid, "googlebot_recibe_error", "warning",
                          {"visitas": n, "con_error": malos, "codigos": dict(cods)})
            avisos += 1
    an._flush_issues()

    lista_fuera = sorted(no_en_censo.values(), key=lambda x: -x["visitas"])[:MAX_NO_EN_CENSO]
    for x in lista_fuera:
        x["codigos"] = dict(x["codigos"])
    job.logs_resumen = {
        "import_id": imp.id, "fichero": imp.fichero,
        "desde": imp.desde.isoformat() if imp.desde else None,
        "hasta": imp.hasta.isoformat() if imp.hasta else None,
        "dias": round(dias, 1), "afirmable_ausencia": afirmable,
        "visitas": total, "falsas": falsas, "sin_comprobar": sin_comprobar,
        "parte_movil": round(movil / total, 4) if total else None,
        "indexables": len(indexables), "indexables_visitadas": visitadas,
        "reparto": dict(reparto), "no_en_censo": lista_fuera,
        "otros_bots": {b: d for b, d in (imp.bots or {}).items() if b != BOT_PRINCIPAL},
    }
    logger.info("Logs del job %s: %d visitas de Googlebot verificado, %d avisos", an.job_id, total, avisos)
    return job.logs_resumen
