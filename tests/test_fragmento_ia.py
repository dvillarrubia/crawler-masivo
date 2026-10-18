"""Lo que una pagina indexable le niega al fragmento y a la IA de Google
(Ola 7.2, decision 137): `nosnippet` y `max-snippet` restrictivo.

La regla, contra cadenas; y el analisis EJECUTADO contra SQLite (decision 74).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.indexabilidad import limite_de_fragmento


@pytest.mark.parametrize("meta, x_robots, esperado", [
    ("index, follow", None, (False, None)),
    ("index, nosnippet", None, (True, None)),
    ("NOSNIPPET", None, (True, None)),
    ("max-snippet:0", None, (True, 0)),
    ("max-snippet: 50", None, (False, 50)),          # con espacio tras los dos puntos
    ("max-snippet:-1", None, (False, None)),          # -1 es sin limite
    ({"robots": "max-snippet:120", "googlebot": "max-snippet:40"}, None, (False, 40)),  # la mas restrictiva
    ({"robots": None, "bingbot": "nosnippet"}, None, (False, None)),  # otro bot
    (None, "googlebot: nosnippet", (True, None)),
    (None, "bingbot: nosnippet", (False, None)),
    (None, "max-snippet:-1, noindex", (False, None)),
    (None, "unavailable_after: 25 Jun 2030 15:00:00 PST\nmax-snippet:30", (False, 30)),
])
def test_limite_de_fragmento(meta, x_robots, esperado):
    assert limite_de_fragmento(meta_robots=meta, x_robots=x_robots) == esperado


from analysis.analyzer import SEOAnalyzer  # noqa: E402
from shared.models import Base, HtmlMeta, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def test_el_analisis_avisa_solo_en_indexables():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, HtmlMeta.__table__,
                                             Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()

    def pagina(ruta, robots=None, googlebot=None, x=None, status=200):
        u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com",
                is_internal=True, is_html=True, status_code=status, status_group=f"{status // 100}xx",
                crawl_depth=1, indexability_status="Indexable")
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, meta_robots=robots, meta_robots_googlebot=googlebot,
                       x_robots_tag=x))
        return u

    sin = pagina("/sin", robots="nosnippet")
    cero = pagina("/cero", x="googlebot: max-snippet:0")
    corto = pagina("/corto", googlebot="max-snippet:50")
    pagina("/largo", robots="max-snippet:300")
    pagina("/libre", robots="max-snippet:-1, max-image-preview:large")
    pagina("/noindex", robots="noindex, nosnippet")          # ya avisa noindex_page
    pagina("/404", robots="nosnippet", status=404)            # no hay resultado que recortar
    pagina("/bing", x="bingbot: nosnippet")
    s.commit()

    a = SEOAnalyzer(s, j.id)
    a.analyze_indexability()
    s.commit()
    filas = s.execute(select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details)
                      .where(Issue.issue_type.in_(["nosnippet_en_indexable", "max_snippet_restrictivo"]))
                      ).all()
    vistas = {(u, t, sev, tuple(sorted(d.items()))) for u, t, sev, d in filas}
    assert vistas == {
        (sin.id, "nosnippet_en_indexable", "warning", (("directiva", "nosnippet"),)),
        (cero.id, "nosnippet_en_indexable", "warning", (("directiva", "max-snippet:0"),)),
        (corto.id, "max_snippet_restrictivo", "info", (("max_snippet", 50),)),
    }
