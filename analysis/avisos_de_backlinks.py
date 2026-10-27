"""La autoridad que entra de fuera y se pierde por el camino (#31, decision 212).

Con los backlinks del CSV del cliente ya cruzados con el censo (lo hace
`compute_pagerank`, que deja el cruce en `an._backlinks`):

- ``backlinks_a_error`` (warning): una pagina que da 4xx/5xx o no responde y a
  la que enlazan desde fuera. Es autoridad que se tira: redirigirla con 301 a
  la mas parecida la recupera. Los detalles dicen cuantos backlinks, de
  cuantos dominios y lo que aportan, para ordenar el mapa de redirecciones.
- ``backlinks_a_noindex`` (warning): 200 con noindex y backlinks.
- ``backlinks_a_bloqueada_robots`` (warning): robots.txt no deja a Google leerla.
- ``huerfana_con_backlinks`` (warning): indexable que el analisis ya marco como
  huerfana (`orphan_page`, `sitemap_orphan` o la `huerfana_con_trafico` de
  Search Console) y que recibe enlaces de fuera: el sitio no le pasa nada de lo
  que le llega. Un aviso por hallazgo (decision 36): sustituye al de huerfana
  normal, como hace Search Console.

Un backlink que llega a una 301 no se avisa: la arista de redireccion lo pasa
a su destino, y eso es lo que tiene que pasar. Best-effort entero (decision
40): si algo falla, se deshace solo lo suyo.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)


def avisar_sin_tumbar(an) -> None:
    pendientes = list(getattr(an, "_pending_issues", []) or [])
    try:
        with an.session.begin_nested():
            avisar(an)
    except Exception:
        an._pending_issues = pendientes
        logger.exception("Avisos de backlinks del job %s: fallo; el analisis sigue", an.job_id)


def avisar(an) -> int:
    from shared.models import Issue, Url

    cruce = getattr(an, "_backlinks", None)
    if cruce is None or not cruce.por_url:
        return 0
    an._flush_issues()
    huerfanas = {uid for (uid,) in an.session.execute(
        select(Issue.url_id).where(Issue.job_id == an.job_id, Issue.issue_type.in_(
            ("orphan_page", "sitemap_orphan", "huerfana_con_trafico"))))}
    ids = list(cruce.por_url)
    filas = []
    for i in range(0, len(ids), 1000):
        filas.extend(an.session.execute(
            select(Url.id, Url.status_code, Url.is_html, Url.indexable, Url.noindex,
                   Url.blocked_by_robots, Url.indexability_status, Url.redirect_url)
            .where(Url.job_id == an.job_id, Url.id.in_(ids[i:i + 1000]))).all())
    avisos = 0
    sustituidas: list[int] = []
    for uid, status, is_html, indexable, noindex, bloqueada, estado, redirect in filas:
        d = cruce.por_url[uid]
        detalle = {"backlinks": d["backlinks"], "dominios": d["dominios"],
                   "autoridad": round(d["aportacion"], 4), "origenes": d["origenes"]}
        estado = estado or ""
        if redirect or (status is not None and 300 <= status < 400):
            continue
        if status is None or status >= 400:
            an._add_issue(uid, "backlinks_a_error", "warning", {**detalle, "codigo": status})
        elif bloqueada or estado.startswith("Blocked"):
            an._add_issue(uid, "backlinks_a_bloqueada_robots", "warning", detalle)
        elif is_html and noindex:
            an._add_issue(uid, "backlinks_a_noindex", "warning", detalle)
        elif is_html and indexable and uid in huerfanas:
            an._add_issue(uid, "huerfana_con_backlinks", "warning", detalle)
            sustituidas.append(uid)
        else:
            continue
        avisos += 1
    if sustituidas:
        an._flush_issues()
        for i in range(0, len(sustituidas), 1000):
            (an.session.query(Issue)
             .filter(Issue.job_id == an.job_id, Issue.url_id.in_(sustituidas[i:i + 1000]),
                     Issue.issue_type.in_(("orphan_page", "sitemap_orphan")))
             .delete(synchronize_session=False))
    an._flush_issues()
    logger.info("Backlinks del job %s: %d avisos", an.job_id, avisos)
    return avisos
