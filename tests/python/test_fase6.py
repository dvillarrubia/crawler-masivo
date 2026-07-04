"""
Tests de la Fase 6 — T22 clasificador de aristas + arch_edges, T23 click
depth real, flujos entre secciones y checks ARQ.
"""

from __future__ import annotations

import pytest


def _url(db_session, job, path, *, status=200, indexable=True, pagerank=None):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host="toy.local", path=path, scheme="https",
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", indexable=indexable,
        pagerank=pagerank,
    )
    db_session.add(u)
    db_session.flush()
    return u


def _link(db_session, job, src, dst_path, *, ancestor=None, container=None,
          rel=None, anchor=None, position="content"):
    from shared.models import Link
    from shared.url_normalization import compute_url_hash

    dst = f"https://toy.local{dst_path}"
    l = Link(
        job_id=job.id, from_url_id=src.id, to_url=dst,
        to_url_hash=compute_url_hash(dst), is_internal=True, follow=True,
        link_position=position, dom_ancestor=ancestor,
        dom_container=container, rel=rel, anchor_text=anchor,
    )
    db_session.add(l)
    db_session.flush()
    return l


# ---------------------------------------------------------------------------
# T22 — clasificador DOM (pasada 1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ancestor,container,rel,expected", [
    ("nav", "ul.menu-principal", None, "menu"),
    ("header", None, None, "menu"),
    ("footer", "div.footer-links", None, "footer"),
    ("aside", None, None, "sidebar"),
    (None, "div.sidebar-widget", None, "sidebar"),
    ("main", "nav.breadcrumbs", None, "breadcrumb"),
    ("main", "div.pagination", None, "paginacion"),
    ("main", "a.page", "next", "paginacion"),
    ("main", "div.related-posts.grid", None, "listado"),
    ("main", "p.intro", None, "contextual"),
    ("article", None, None, "contextual"),
    (None, None, None, "desconocido"),
])
def test_classify_edge_dom(ancestor, container, rel, expected):
    from analysis.architecture import classify_edge_dom

    assert classify_edge_dom(ancestor, container, rel) == expected


def test_classify_edge_dom_client_selector_wins():
    from analysis.architecture import classify_edge_dom

    rules = [("listado", "modulo-recomendados")]
    assert classify_edge_dom(
        "main", "div.modulo-recomendados", None, rules
    ) == "listado"


# ---------------------------------------------------------------------------
# T22 — fixture completa: seis clases + reclasificación sitewide
# ---------------------------------------------------------------------------

def _arch_job(db_session, make_job):
    job = make_job(config={"edge_classification": True,
                           "analysis_thresholds": {"pagerank_version": 2}})
    job.client_id = "cliente-arq"
    db_session.flush()
    return job


def test_classifier_six_classes_and_sitewide(db_session, make_job):
    from analysis.architecture import classify_edges
    from shared.models import ArchEdge, Link

    job = _arch_job(db_session, make_job)
    pages = [_url(db_session, job, f"/p{i}") for i in range(5)]
    # los destinos sitewide no son indexables en la fixture para que el
    # denominador de la pasada 2 sean exactamente las 5 páginas fuente
    _url(db_session, job, "/contacto", indexable=False)

    # menú sitewide: /contacto enlazada desde TODAS las páginas
    for p in pages:
        _link(db_session, job, p, "/contacto", ancestor="nav", anchor="Contacto")
    # banner de plantilla DENTRO de main en todas → pasada 2 lo reclasifica
    for p in pages:
        _link(db_session, job, p, "/promo", ancestor="main", container="div.hero")
    _url(db_session, job, "/promo", indexable=False)

    src = pages[0]
    _link(db_session, job, src, "/legal", ancestor="footer")
    _link(db_session, job, src, "/categoria", container="nav.breadcrumbs")
    _link(db_session, job, src, "/pagina-2", container="div.pagination")
    _link(db_session, job, src, "/relacionado", ancestor="main", container="div.related.grid")
    _link(db_session, job, src, "/editorial", ancestor="article", container="p.body")

    summary = classify_edges(db_session, job.id, "cliente-arq")
    db_session.flush()

    classes = dict(db_session.query(Link.to_url, Link.edge_class).all())
    assert classes["https://toy.local/contacto"] == "menu"
    assert classes["https://toy.local/legal"] == "footer"
    assert classes["https://toy.local/categoria"] == "breadcrumb"
    assert classes["https://toy.local/pagina-2"] == "paginacion"
    assert classes["https://toy.local/relacionado"] == "listado"
    assert classes["https://toy.local/editorial"] == "contextual"
    # el banner sitewide dentro de main fue reclasificado por la pasada 2
    assert classes["https://toy.local/promo"] == "menu"
    assert summary["sitewide_targets"] >= 2

    # arch_edges: los sitewide colapsan el origen a '*'
    menu_rows = db_session.query(ArchEdge).filter(
        ArchEdge.job_id == job.id, ArchEdge.sitewide.is_(True),
    ).all()
    assert all(r.source_hash == "*" for r in menu_rows)
    contacto = [r for r in menu_rows if r.target_hash.endswith("") and r.n_pages == 5]
    assert len(contacto) >= 1


def test_classifier_off_leaves_edge_class_null(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import ArchEdge, Link

    job = make_job()  # sin flag
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/b")
    _link(db_session, job, a, "/b", ancestor="nav")

    SEOAnalyzer(db_session, job.id).analyze_architecture()
    db_session.flush()

    assert db_session.query(Link).filter(Link.edge_class.isnot(None)).count() == 0
    assert db_session.query(ArchEdge).count() == 0


# ---------------------------------------------------------------------------
# T23 — click depth real + link_orphan
# ---------------------------------------------------------------------------

def test_click_depth_differs_from_crawl_depth(db_session, make_job):
    """Criterio: ficha a 3 clics pero descubierta a depth 1 por sitemap."""
    from analysis.architecture import compute_click_depth
    from shared.url_normalization import compute_url_hash

    job = make_job(name="depth", seeds=["https://toy.local/"])
    home = _url(db_session, job, "/")
    cat = _url(db_session, job, "/categoria")
    sub = _url(db_session, job, "/categoria/sub")
    ficha = _url(db_session, job, "/categoria/sub/ficha")
    ficha.crawl_depth = 1  # descubierta por sitemap a depth 1
    db_session.flush()

    _link(db_session, job, home, "/categoria")
    _link(db_session, job, cat, "/categoria/sub")
    _link(db_session, job, sub, "/categoria/sub/ficha")

    depth = compute_click_depth(
        db_session, job.id, {compute_url_hash("https://toy.local/")},
    )
    db_session.expire_all()

    assert ficha.click_depth == 3
    assert ficha.click_depth != ficha.crawl_depth
    assert home.click_depth == 0


def test_link_orphan_only_for_pages_without_click_path(db_session, make_job):
    """Criterio: página en sitemap sin camino de clics → link_orphan y ni
    rastro de los otros dos tipos de huérfana."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job(name="orphans", seeds=["https://toy.local/"],
                   config={"edge_classification": True})
    home = _url(db_session, job, "/")
    linked = _url(db_session, job, "/enlazada")
    linked.inlinks_count = 1
    isla = _url(db_session, job, "/isla-sitemap")  # rastreada, sin caminos
    isla.inlinks_count = 1  # tiene inlink externo al grafo? no: evita orphan_page
    db_session.flush()
    _link(db_session, job, home, "/enlazada", ancestor="main")

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.analyze_architecture()
    db_session.flush()

    issues = db_session.query(Issue).filter(Issue.job_id == job.id).all()
    by_type = {}
    for i in issues:
        by_type.setdefault(i.issue_type, set()).add(i.url_id)

    assert isla.id in by_type.get("link_orphan", set())
    assert linked.id not in by_type.get("link_orphan", set())
    assert home.id not in by_type.get("link_orphan", set())
    # ni rastro de los otros dos tipos para la isla
    assert isla.id not in by_type.get("orphan_page", set())
    assert isla.id not in by_type.get("orphan_not_in_crawl", set())


def test_excessive_click_depth_business_vs_default(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, Segment

    job = make_job(name="depths", seeds=["https://toy.local/"],
                   config={"edge_classification": True})
    job.client_id = "cliente-arq"
    seg = Segment(client_id="cliente-arq", name="Producto", rule_type="regex",
                  rule=r"^/producto/", priority=10, is_business=True)
    db_session.add(seg)
    db_session.flush()

    # cadena home → a → b → c → producto-profundo (depth 4)
    chain = [_url(db_session, job, "/")]
    for i, path in enumerate(["/a", "/b", "/c"]):
        nxt = _url(db_session, job, path)
        _link(db_session, job, chain[-1], path, ancestor="main")
        chain.append(nxt)
    deep_biz = _url(db_session, job, "/producto/profundo")
    _link(db_session, job, chain[-1], "/producto/profundo", ancestor="main")
    deep_normal = _url(db_session, job, "/soporte-profundo")
    _link(db_session, job, chain[-1], "/soporte-profundo", ancestor="main")

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.assign_segments()
    analyzer.analyze_architecture()
    db_session.flush()

    flagged = {
        i.url_id for i in db_session.query(Issue).filter(
            Issue.job_id == job.id,
            Issue.issue_type == "excessive_click_depth",
        )
    }
    # ambos a depth 4: el de negocio (límite 4) se marca, el normal (límite 5) no
    assert deep_biz.id in flagged
    assert deep_normal.id not in flagged


def test_authority_sink_and_contextual_counters(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job(name="sink", seeds=["https://toy.local/"],
                   config={"edge_classification": True})
    home = _url(db_session, job, "/", pagerank=10.0)
    sink = _url(db_session, job, "/acumuladora", pagerank=9.5)
    normal = _url(db_session, job, "/reparte", pagerank=8.0)
    low = _url(db_session, job, "/baja", pagerank=1.0)
    # p50 de [1, 8, 9.5, 10] = 9.5 → el sink debe superarlo: añade una
    # quinta página para que el p50 baje a 8.0
    _url(db_session, job, "/quinta", pagerank=2.0)

    _link(db_session, job, home, "/acumuladora", ancestor="main")
    _link(db_session, job, home, "/reparte", ancestor="main")
    _link(db_session, job, home, "/baja", ancestor="main")
    _link(db_session, job, normal, "/baja", ancestor="article")  # reparte
    # sink no tiene salientes contextuales (solo menú)
    _link(db_session, job, sink, "/", ancestor="nav")

    SEOAnalyzer(db_session, job.id).analyze_architecture()
    db_session.flush()
    db_session.expire_all()

    assert sink.out_contextual == 0
    assert normal.out_contextual == 1
    assert low.in_contextual == 2

    sinks = {
        i.url_id for i in db_session.query(Issue).filter(
            Issue.job_id == job.id, Issue.issue_type == "authority_sink",
        )
    }
    assert sink.id in sinks
    assert normal.id not in sinks
    assert low.id not in sinks  # pagerank bajo


def test_section_flows_conservation(db_session, make_job):
    """Criterio: la matriz de flujos suma ≈ la masa repartida."""
    from analysis.architecture import (
        EDGE_CLASS_WEIGHT, classify_edges, compute_section_flows,
    )
    from shared.models import SectionFlow

    job = _arch_job(db_session, make_job)
    a = _url(db_session, job, "/a", pagerank=6.0)
    b = _url(db_session, job, "/b", pagerank=4.0)
    _url(db_session, job, "/c", pagerank=2.0)
    _link(db_session, job, a, "/b", ancestor="main")
    _link(db_session, job, a, "/c", ancestor="footer")
    _link(db_session, job, b, "/c", ancestor="article")

    classify_edges(db_session, job.id, None)
    total = compute_section_flows(db_session, job.id)

    flows = db_session.query(SectionFlow).filter(
        SectionFlow.job_id == job.id
    ).all()
    assert sum(f.flow for f in flows) == pytest.approx(total, rel=1e-6)
    # masa esperada: 0.85 × (PR de páginas con salientes) = 0.85 × (6+4)
    assert total == pytest.approx(0.85 * 10.0, rel=1e-6)


def test_deep_pagination_chain(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job(name="pag", seeds=["https://toy.local/"],
                   config={"edge_classification": True})
    pages = [_url(db_session, job, "/lista")]
    _url(db_session, job, "/")
    for i in range(2, 8):
        nxt = _url(db_session, job, f"/lista?page={i}")
        _link(db_session, job, pages[-1], f"/lista?page={i}",
              container="div.pagination")
        pages.append(nxt)

    SEOAnalyzer(db_session, job.id).analyze_architecture()
    db_session.flush()

    deep = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "deep_pagination"
    ).all()
    assert len(deep) == 1
    assert deep[0].url_id == pages[0].id
    assert deep[0].details["chain_length"] == 6
