"""Una pagina de muro no lleva incidencias de pagina (decision 103).

Contra SQLite, ejecutando los checks de verdad (decision 74): un desafio de
Cloudflare guardado con 403 salia como `4xx_error`, uno servido con 200 como
`h1_missing` e indexable, y un hreflang o un canonical que apuntaba a una pagina
de muro salia «roto». Ahora el muro lleva UNA incidencia por host y clase.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Heading, Hreflang, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Issue.__table__,
        Heading.__table__, Hreflang.__table__,
    ])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, status=200, clase="contenido", estado="Indexable", host="x.com"):
    u = Url(
        job_id=j.id, url=f"https://{host}{ruta}", url_hash=f"{host}{ruta}", host=host,
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", crawl_depth=1, response_class=clase,
        indexable=estado == "Indexable", indexability_status=estado,
    )
    s.add(u)
    s.flush()
    return u


def _issues(s, j, tipo=None):
    q = select(Issue.url_id, Issue.issue_type, Issue.details).where(Issue.job_id == j.id)
    if tipo:
        q = q.where(Issue.issue_type == tipo)
    return s.execute(q).all()


def test_un_desafio_con_403_no_es_un_4xx_y_va_agrupado_por_host():
    s, j = _montar()
    roto = _url(s, j, "/no-existe", status=404, estado="Client Error (404)")
    for i in range(3):
        _url(s, j, f"/muro/{i}", status=403, clase="challenge_waf", estado="Bloqueada (WAF)")
    _url(s, j, "/limitada", status=429, clase="rate_limited", estado="Limitada (rate limit)")
    s.commit()
    a = SEOAnalyzer(s, j.id)
    a.analyze_status_codes()
    a._flush_issues()
    s.commit()

    assert [u for u, *_ in _issues(s, j, "4xx_error")] == [roto.id]
    muros = {d["clase"]: d for _, _, d in _issues(s, j, "respuesta_no_valida")}
    assert set(muros) == {"challenge_waf", "rate_limited"}
    assert muros["challenge_waf"]["paginas_afectadas"] == 3
    assert muros["challenge_waf"]["host"] == "x.com"
    assert muros["challenge_waf"]["codigos"] == {"403": 3}
    assert len(muros["challenge_waf"]["ejemplos"]) == 3


def test_un_muro_servido_con_200_no_es_indexable_ni_le_falta_el_h1():
    s, j = _montar()
    muro = _url(s, j, "/muro", status=200, clase="challenge_waf", estado="Bloqueada (WAF)")
    sin_h1 = _url(s, j, "/sin-h1")
    s.add(HtmlMeta(url_id=sin_h1.id, title="Pagina"))
    s.commit()
    a = SEOAnalyzer(s, j.id)
    a.analyze_indexability()
    a.analyze_headings()
    a._flush_issues()
    s.commit()

    indexable, estado = s.execute(
        select(Url.indexable, Url.indexability_status).where(Url.id == muro.id)).one()
    assert (indexable, estado) == (False, "Bloqueada (WAF)")
    assert [u for u, *_ in _issues(s, j, "h1_missing")] == [sin_h1.id]


def test_un_canonical_o_un_hreflang_a_un_muro_no_estan_rotos():
    """El destino no se ha visto: no se sabe si esta roto (los 18
    `hreflang_broken_target` hacia paginas con 429 de penguin)."""
    s, j = _montar()
    origen = _url(s, j, "/es/a")
    destino = _url(s, j, "/en/a", status=429, clase="rate_limited",
                   estado="Limitada (rate limit)")
    roto = _url(s, j, "/fr/a", status=404, estado="Client Error (404)")
    s.add(HtmlMeta(url_id=origen.id, title="A", canonical_href=destino.url))
    s.add(Hreflang(url_id=origen.id, lang="en", href=destino.url))
    s.add(Hreflang(url_id=origen.id, lang="fr", href=roto.url))
    s.commit()
    a = SEOAnalyzer(s, j.id)
    a.analyze_canonicals()
    a.analyze_hreflang()
    a._flush_issues()
    s.commit()

    # Ningun aviso de canonical sobre el origen: `canonical_broken` ya no
    # existe (decision 101) y lo que importa es que el muro no se lea como un
    # destino que responde 4xx (canonical_a_error, canonical_a_no_indexable...).
    assert [t for u, t, _ in _issues(s, j) if t.startswith("canonical_") and u == origen.id] == []
    rotos = [d for _, _, d in _issues(s, j, "hreflang_broken_target")]
    assert len(rotos) == 1 and "fr" in str(rotos[0])
    sin_retorno = [d for _, _, d in _issues(s, j, "hreflang_missing_return")]
    assert not any("en/a" in str(d) for d in sin_retorno)


def test_un_muro_no_es_destino_de_error_ni_entrada_rota_del_sitemap():
    """Los checks de la Ola 1 tampoco afirman lo que esconde un muro: una
    redireccion hacia un desafio de 403 no es `redireccion_a_error`, una URL
    del sitemap que respondio un muro no es «4xx» (`sitemap_no_indexable`), y el
    HTML de un desafio no es el de la pagina (`html_supera_2mb`, decision 102).
    Los errores de verdad siguen saliendo, en los tres."""
    s, j = _montar()
    salto_muro = _url(s, j, "/viejo", status=301, estado="Redirect (301)")
    salto_muro.redirect_url = "https://x.com/muro"
    salto_muro.is_html = False
    salto_roto = _url(s, j, "/viejo2", status=301, estado="Redirect (301)")
    salto_roto.redirect_url = "https://x.com/no-existe"
    salto_roto.is_html = False
    muro = _url(s, j, "/muro", status=403, clase="challenge_waf", estado="Bloqueada (WAF)")
    roto = _url(s, j, "/no-existe", status=404, estado="Client Error (404)")
    muro_grande = _url(s, j, "/muro-grande", status=200, clase="challenge_waf",
                       estado="Bloqueada (WAF)")
    grande = _url(s, j, "/grande")
    for u in (muro, roto):
        u.in_sitemap = True
    for u in (muro_grande, grande):
        u.content_length = 3 * 1024 * 1024
        u.transfer_size = 3 * 1024 * 1024
    s.commit()
    a = SEOAnalyzer(s, j.id)
    a.analyze_redirect_chains()
    a._sitemap_no_indexable()
    a.analyze_html_ignorado()
    a._flush_issues()
    s.commit()

    assert [u for u, *_ in _issues(s, j, "redireccion_a_error")] == [salto_roto.id]
    assert [u for u, *_ in _issues(s, j, "sitemap_no_indexable")] == [roto.id]
    assert [u for u, *_ in _issues(s, j, "html_supera_2mb")] == [grande.id]
