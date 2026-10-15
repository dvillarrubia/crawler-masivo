"""Hreflang completo (decision 99), ejecutando `analyze_hreflang` contra SQLite.

Cada caso con lo que decide Google:

- Cada version se lista a si misma (autorreferencia) y el cluster declara un
  `x-default` para quien no casa con ningun idioma.
- Un mismo codigo con dos URLs es una contradiccion: Google no sabe cual usar.
- Una alternativa que no es indexable (canonicalizada, noindex) Google la
  descarta, como un destino roto.
- Si A llama `en-US` a B y B se llama a si misma `en-GB`, el retorno existe
  pero no casa.
- Google no usa el hreflang de una pagina no indexable: la variante `/es`
  canonicalizada a `/es/` no tiene que recibir retorno de nadie.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer, _clave_de_grupo
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


NUEVOS = {
    "hreflang_sin_autorreferencia", "hreflang_sin_x_default",
    "hreflang_idioma_repetido", "hreflang_destino_no_indexable",
    "hreflang_retorno_incoherente",
}


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Hreflang.__table__,
        Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, url, *, status=200, indexable=True, estado="Indexable",
         canonical=None, redirect=None):
    u = Url(job_id=j.id, url=url, url_hash=url[-40:], is_internal=True,
            is_html=True, status_code=status, host=url.split("/")[2],
            resource_type="html", indexable=indexable,
            indexability_status=estado, redirect_url=redirect)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, canonical_href=canonical or url))
    s.flush()
    return u


def _anotar(s, pagina, *pares):
    for lang, href in pares:
        s.add(Hreflang(url_id=pagina.id, lang=lang, href=href))
    s.flush()


def _analizar(s, j):
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    return s.query(Issue).filter(Issue.job_id == j.id).all()


def _de(issues, tipo):
    return [i for i in issues if i.issue_type == tipo]


ES, EN, DE = "https://x.com/es/a", "https://x.com/en/a", "https://x.com/de/a"
CLUSTER = (("es", ES), ("en", EN), ("de", DE), ("x-default", ES))


def _cluster_bien(s, j):
    paginas = [_url(s, j, u) for u in (ES, EN, DE)]
    for p in paginas:
        _anotar(s, p, *CLUSTER)
    return paginas


def test_un_cluster_bien_hecho_no_da_ningun_aviso():
    s, j = _montar()
    _cluster_bien(s, j)
    assert _analizar(s, j) == []


def test_treinta_idiomas_bien_hechos_no_dan_nada_y_uno_repetido_da_una_fila():
    """Sin N²: un error de plantilla en un cluster de 30 paginas es UNA fila."""
    s, j = _montar()
    urls = [f"https://x.com/l{i}/a" for i in range(30)]
    codigos = ["es", "en", "de", "fr", "it", "pt", "nl", "pl", "sv", "da", "fi",
               "nb", "cs", "sk", "hu", "ro", "bg", "el", "tr", "ru", "uk", "ja",
               "ko", "zh", "ar", "he", "hi", "th", "vi", "id"]
    paginas = [_url(s, j, u) for u in urls]
    for p in paginas:
        _anotar(s, p, *zip(codigos, urls), ("x-default", urls[0]))
    assert _analizar(s, j) == []

    # Ahora todas anotan ademas `fr` hacia otra URL: el mismo error en todas.
    s2, j2 = _montar()
    paginas = [_url(s2, j2, u) for u in urls]
    for p in paginas:
        _anotar(s2, p, *zip(codigos, urls), ("x-default", urls[0]),
                ("fr", "https://x.com/fr-viejo/a"))
    rep = _de(_analizar(s2, j2), "hreflang_idioma_repetido")
    assert len(rep) == 1
    assert rep[0].details["paginas_afectadas"] == 30
    assert rep[0].details["cuantas_urls"] == 2
    assert rep[0].details["lang"] == "fr"


def test_sin_autorreferencia():
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("en", EN), ("x-default", EN))
    _anotar(s, en, ("es", ES), ("en", EN), ("x-default", EN))
    issues = _analizar(s, j)
    sin = _de(issues, "hreflang_sin_autorreferencia")
    assert [i.url_id for i in sin] == [es.id]
    assert sin[0].severity == "warning"
    # Una por URL: sin clave (decision 88). El cluster va en el detalle.
    assert sin[0].group_key is None
    assert sin[0].details["cluster"]


def test_solo_x_default_hacia_si_misma_no_es_autorreferencia():
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("en", EN), ("x-default", ES))
    _anotar(s, en, ("es", ES), ("en", EN), ("x-default", ES))
    sin = _de(_analizar(s, j), "hreflang_sin_autorreferencia")
    assert [i.url_id for i in sin] == [es.id]


def test_se_anota_con_una_variante_no_rastreada_y_se_dice_cual():
    """El caso de progym: `?___store=` en la autorreferencia."""
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("es", ES + "?store=es"), ("en", EN), ("x-default", ES))
    _anotar(s, en, ("es", ES), ("en", EN), ("x-default", ES))
    sin = _de(_analizar(s, j), "hreflang_sin_autorreferencia")
    assert [i.url_id for i in sin] == [es.id]
    assert sin[0].details["variante_propia"] == ES + "?store=es"


def test_autorreferencia_por_una_variante_que_salta_a_ella_es_el_destino_roto():
    """Un aviso por hallazgo: lo que hay que arreglar es el destino."""
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _url(s, j, ES + "/", status=301, indexable=False, estado="Redirect (301)",
         redirect=ES)
    _anotar(s, es, ("es", ES + "/"), ("en", EN), ("x-default", EN))
    _anotar(s, en, ("es", ES), ("en", EN), ("x-default", EN))
    issues = _analizar(s, j)
    assert not _de(issues, "hreflang_sin_autorreferencia")
    assert [i.url_id for i in _de(issues, "hreflang_broken_target")] == [es.id]


def test_un_codigo_invalido_en_la_autorreferencia_es_solo_codigo_invalido():
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("es-UK", ES), ("en", EN), ("x-default", EN))
    _anotar(s, en, ("es", ES), ("en", EN), ("x-default", EN))
    issues = _analizar(s, j)
    assert not _de(issues, "hreflang_sin_autorreferencia")
    assert _de(issues, "hreflang_invalid_lang")


def test_sin_x_default_un_aviso_por_host_aunque_falte_en_varios_clusters():
    s, j = _montar()
    for slug in ("a", "b"):
        es, en = f"https://x.com/es/{slug}", f"https://x.com/en/{slug}"
        p1, p2 = _url(s, j, es), _url(s, j, en)
        for p in (p1, p2):
            _anotar(s, p, ("es", es), ("en", en))
    xd = _de(_analizar(s, j), "hreflang_sin_x_default")
    assert len(xd) == 1
    i = xd[0]
    assert i.severity == "info"
    assert i.details["clusters_afectados"] == 2
    assert i.details["clusters_del_host"] == 2
    assert i.details["paginas_afectadas"] == 4
    assert i.group_key == _clave_de_grupo("x.com")


def test_x_default_en_una_sola_pagina_del_cluster_basta():
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("es", ES), ("en", EN), ("x-default", ES))
    _anotar(s, en, ("es", ES), ("en", EN))
    assert not _de(_analizar(s, j), "hreflang_sin_x_default")


def test_idioma_repetido_en_una_pagina():
    s, j = _montar()
    es = _url(s, j, ES)
    otro = "https://x.com/en/b"
    _anotar(s, es, ("es", ES), ("en", EN), ("EN", otro), ("x-default", ES))
    rep = _de(_analizar(s, j), "hreflang_idioma_repetido")
    assert len(rep) == 1
    assert rep[0].details["lang"] == "en"
    assert rep[0].details["urls"] == [EN, otro]
    assert rep[0].group_key and rep[0].group_key != _clave_de_grupo("en")


def test_el_mismo_codigo_dos_veces_hacia_la_misma_url_no_es_repetido():
    s, j = _montar()
    es = _url(s, j, ES)
    _anotar(s, es, ("es", ES), ("en", EN), ("en", EN), ("x-default", ES))
    assert not _de(_analizar(s, j), "hreflang_idioma_repetido")


def test_destino_canonicalizado_un_aviso_por_destino_y_sin_retorno_falso():
    """Lopesan: `/en/` canonicalizada a `/es/`, anotada desde `/es/` y `/de/`."""
    s, j = _montar()
    es, de = _url(s, j, ES), _url(s, j, DE)
    en = _url(s, j, EN, indexable=False, estado="Canonicalised", canonical=ES)
    for p in (es, de):
        _anotar(s, p, *CLUSTER)
    # La canonicalizada no devuelve nada: su hreflang no cuenta.
    issues = _analizar(s, j)
    ni = _de(issues, "hreflang_destino_no_indexable")
    assert [i.url_id for i in ni] == [en.id]
    assert ni[0].severity == "error"
    assert ni[0].details["motivo"] == "Canonicalised"
    assert ni[0].details["canonical"] == ES
    assert ni[0].details["paginas_afectadas"] == 2
    assert ni[0].details["ejemplos"] == sorted([ES, DE])
    assert not _de(issues, "hreflang_missing_return")


def test_destino_noindex():
    s, j = _montar()
    es, de = _url(s, j, ES), _url(s, j, DE)
    _url(s, j, EN, indexable=False, estado="Noindex")
    for p in (es, de):
        _anotar(s, p, *CLUSTER)
    ni = _de(_analizar(s, j), "hreflang_destino_no_indexable")
    assert len(ni) == 1 and ni[0].details["motivo"] == "Noindex"
    assert "canonical" not in ni[0].details


def test_destino_roto_es_solo_destino_roto():
    """Decision 56: un 404 es `hreflang_broken_target` y nada mas."""
    s, j = _montar()
    es, de = _url(s, j, ES), _url(s, j, DE)
    _url(s, j, EN, status=404, indexable=False, estado="Client Error (404)")
    for p in (es, de):
        _anotar(s, p, *CLUSTER)
    tipos = {i.issue_type for i in _analizar(s, j)}
    assert tipos == {"hreflang_broken_target"}


def test_destino_sin_respuesta_no_se_afirma_nada():
    """Un timeout nuestro no dice que veria Google: ni no indexable ni sin retorno."""
    s, j = _montar()
    es, de = _url(s, j, ES), _url(s, j, DE)
    _url(s, j, EN, status=None, indexable=False, estado="Sin respuesta")
    for p in (es, de):
        _anotar(s, p, *CLUSTER)
    assert _analizar(s, j) == []


def test_destino_bloqueado_por_robots_es_no_indexable():
    s, j = _montar()
    es, de = _url(s, j, ES), _url(s, j, DE)
    _url(s, j, EN, status=None, indexable=False, estado="Blocked by robots.txt")
    for p in (es, de):
        _anotar(s, p, *CLUSTER)
    ni = _de(_analizar(s, j), "hreflang_destino_no_indexable")
    assert len(ni) == 1 and ni[0].details["motivo"] == "Blocked by robots.txt"


def test_una_fuente_no_indexable_no_da_avisos_y_su_retorno_no_se_sabe():
    """El caso de Lopesan: `/es` canonicalizada a `/es/` repite el cluster."""
    s, j = _montar()
    _cluster_bien(s, j)
    var = _url(s, j, "https://x.com/es", indexable=False, estado="Canonicalised",
               canonical=ES)
    _anotar(s, var, ("es", "https://x.com/es"), ("en", EN), ("de", DE),
            ("en-UK", EN))
    assert _analizar(s, j) == []
    filas = s.query(Hreflang).filter(Hreflang.url_id == var.id).all()
    assert {h.return_tag_ok for h in filas} == {None}
    # El codigo invalido se sigue guardando como tal, que es verdad.
    assert {h.lang: h.lang_valid for h in filas}["en-UK"] is False


def test_retorno_incoherente():
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("es", ES), ("en-US", EN), ("x-default", ES))
    _anotar(s, en, ("es", ES), ("en-GB", EN), ("x-default", ES))
    inc = _de(_analizar(s, j), "hreflang_retorno_incoherente")
    assert [i.url_id for i in inc] == [es.id]
    assert inc[0].details["lang"] == "en-us"
    assert inc[0].details["lang_del_destino"] == ["en-gb"]
    assert inc[0].details["href"] == EN
    assert inc[0].group_key == _clave_de_grupo("en-us")


def test_una_url_con_varios_codigos_no_es_incoherente_si_alguno_casa():
    """`/es/` para es-ES y es-MX es legitimo: basta con que un codigo coincida."""
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("es-ES", ES), ("es-MX", ES), ("en", EN), ("x-default", EN))
    _anotar(s, en, ("es-MX", ES), ("en", EN), ("x-default", EN))
    assert _analizar(s, j) == []


def test_sin_retorno_no_es_incoherente():
    """Si no hay retorno el aviso es `hreflang_missing_return`, no este."""
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("es", ES), ("en-US", EN), ("x-default", ES))
    _anotar(s, en, ("en-GB", EN), ("x-default", EN))
    tipos = {i.issue_type for i in _analizar(s, j)}
    assert "hreflang_missing_return" in tipos
    assert "hreflang_retorno_incoherente" not in tipos

