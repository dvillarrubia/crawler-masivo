"""Canonical y hreflang: lo que Google ignora y lo que cuenta dos veces.

Cuatro decisiones, cada una con lo que decide Google:

- Con VARIOS canonicals Google los ignora todos y elige por su cuenta, y uno en
  el `<body>` lo ignora siempre: la pagina esta sin canonicalizar, aunque el
  informe mostraba el primero como si valiera.
- `www.x.com` y `x.com` no son otro dominio, son el mismo sitio resolviendo su
  variante: marcarlo "canonical a otro dominio" convertia la solucion en un
  problema. (En los tres censos medidos no habia ni un caso, asi que esto es
  correccion de criterio y no una cifra.)
- Un canonical a http desde una pagina https manda a Google a la version sin
  cifrar.
- Si el destino de un hreflang no responde 200, no se puede saber si devuelve el
  enlace: emitir "sin retorno" Y "destino roto" es contar el mismo hallazgo dos
  veces. Medido: 282 + 38 + 44 paginas con los dos avisos a la vez.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Hreflang.__table__,
        Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, url, *, canonical=None, cuantos=None, en_body=None, status=200):
    u = Url(job_id=j.id, url=url, url_hash=url[-40:], is_internal=True, is_html=True,
            status_code=status, host=url.split("/")[2])
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, canonical_href=canonical,
                   canonical_count=cuantos, canonical_in_body=en_body))
    s.flush()
    return u


def _tipos(s, j, url_id=None):
    q = s.query(Issue).filter(Issue.job_id == j.id)
    if url_id is not None:
        q = q.filter(Issue.url_id == url_id)
    return sorted(i.issue_type for i in q.all())


def test_varios_canonicals_se_avisan():
    s, j = _montar()
    u = _url(s, j, "https://x.com/a", canonical="https://x.com/a", cuantos=2)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    assert "canonical_multiple" in _tipos(s, j, u.id)


def test_un_canonical_en_el_body_se_avisa():
    s, j = _montar()
    u = _url(s, j, "https://x.com/a", canonical="https://x.com/a", cuantos=1,
             en_body=True)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    assert "canonical_in_body" in _tipos(s, j, u.id)


def test_de_www_a_sin_www_no_es_otro_dominio():
    s, j = _montar()
    u = _url(s, j, "https://www.x.com/a", canonical="https://x.com/a", cuantos=1)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    assert "canonical_cross_domain" not in _tipos(s, j, u.id)


def test_a_otro_dominio_de_verdad_si_se_avisa():
    s, j = _montar()
    u = _url(s, j, "https://x.com/a", canonical="https://otro.com/a", cuantos=1)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    assert "canonical_cross_domain" in _tipos(s, j, u.id)


def _severidad(s, j, url_id, tipo):
    from shared.models import Issue
    from sqlalchemy import select as sel

    return s.execute(
        sel(Issue.severity, Issue.details)
        .where(Issue.job_id == j.id, Issue.url_id == url_id, Issue.issue_type == tipo)
    ).first()


def test_irse_a_otro_dominio_registrable_es_un_error():
    """La pagina se saca del indice en favor de un sitio que no es el suyo.

    Es lo que le paso a un cliente: 2.367 paginas —el 99,5% de su seccion de
    hoteles— con el canonical al servidor de origen de Liferay
    (`*.lfr.cloud`). Estaba en el informe entregado, archivado como `info`
    entre 315.119 incidencias, y no lo vio nadie.
    """
    s, j = _montar()
    u = _url(s, j, "https://cliente.com/a",
             canonical="https://webserver-cliente-prd.lfr.cloud/a", cuantos=1)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    sev, det = _severidad(s, j, u.id, "canonical_cross_domain")
    assert sev == "error"
    assert det["otro_dominio_registrable"] is True


def test_a_un_subdominio_de_la_misma_casa_es_informativo():
    """`comein.uoc.edu` desde `www.uoc.edu` es consolidacion de contenido: una
    decision que conviene ver, no una alarma. Medido en ese censo: 3.551
    canonicals a otro host y 3.550 se quedan dentro de uoc.edu, asi que con el
    criterio de host eran 3.411 lineas de ruido tapando el unico que si salia.
    """
    s, j = _montar()
    u = _url(s, j, "https://www.uoc.edu/a", canonical="https://comein.uoc.edu/a",
             cuantos=1)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    sev, det = _severidad(s, j, u.id, "canonical_cross_domain")
    assert sev == "info"
    assert det["otro_dominio_registrable"] is False


def test_un_canonical_a_http_desde_https_se_avisa():
    s, j = _montar()
    u = _url(s, j, "https://x.com/a", canonical="http://x.com/a", cuantos=1)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    assert "canonical_a_http" in _tipos(s, j, u.id)


def test_un_destino_de_hreflang_roto_no_cuenta_dos_veces():
    s, j = _montar()
    a = _url(s, j, "https://x.com/es/", canonical="https://x.com/es/", cuantos=1)
    _url(s, j, "https://x.com/en/", canonical="https://x.com/en/", cuantos=1,
         status=404)
    s.add(Hreflang(url_id=a.id, lang="en", href="https://x.com/en/"))
    s.add(Hreflang(url_id=a.id, lang="es", href="https://x.com/es/"))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    tipos = _tipos(s, j, a.id)
    assert "hreflang_broken_target" in tipos
    assert "hreflang_missing_return" not in tipos


def test_x_default_en_mayusculas_es_valido():
    """`X-Default` lo escriben media docena de plugins de WordPress y salia
    como idioma invalido."""
    s, j = _montar()
    a = _url(s, j, "https://x.com/", canonical="https://x.com/", cuantos=1)
    s.add(Hreflang(url_id=a.id, lang="X-Default", href="https://x.com/"))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert "hreflang_invalid_lang" not in _tipos(s, j, a.id)


def test_en_uk_no_es_una_region():
    """El codigo de Reino Unido es GB: con `en-UK` Google ignora la anotacion
    entera y ese idioma se queda sin hreflang."""
    s, j = _montar()
    a = _url(s, j, "https://x.com/", canonical="https://x.com/", cuantos=1)
    s.add(Hreflang(url_id=a.id, lang="en-UK", href="https://x.com/uk/"))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert "hreflang_invalid_lang" in _tipos(s, j, a.id)
    issue = [i for i in s.query(Issue).all() if i.issue_type == "hreflang_invalid_lang"][0]
    assert "GB" in issue.details["motivo"]


def test_uk_como_idioma_es_ucraniano_y_es_valido():
    s, j = _montar()
    a = _url(s, j, "https://x.com/", canonical="https://x.com/", cuantos=1)
    s.add(Hreflang(url_id=a.id, lang="uk", href="https://x.com/uk/"))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert "hreflang_invalid_lang" not in _tipos(s, j, a.id)


def test_los_veredictos_de_hreflang_se_guardan_en_TODAS_las_filas():
    """Las columnas, no solo las incidencias.

    `lang_valid` y `return_tag_ok` los consumen los porcentajes de `/insights`
    (decision 30) y no los mira ningun otro test: se comprobaban los avisos y
    las columnas iban por libre. Importa porque se escriben POR LOTES —antes
    era un UPDATE por anotacion, y DOS cuando el destino estaba roto: 438.957
    sentencias en el censo de penguin para grabar seis valores distintos— y un
    agrupamiento mal hecho deja filas sin tocar sin que nada lo diga.

    Los cinco casos que hay, cada uno con su veredicto:
    """
    s, j = _montar()
    es = _url(s, j, "https://x.com/es/", canonical="https://x.com/es/", cuantos=1)
    en = _url(s, j, "https://x.com/en/", canonical="https://x.com/en/", cuantos=1)
    _url(s, j, "https://x.com/de/", canonical="https://x.com/de/", cuantos=1,
         status=404)
    # Reciproco de verdad: las dos se apuntan.
    s.add(Hreflang(url_id=es.id, lang="en", href="https://x.com/en/"))
    s.add(Hreflang(url_id=en.id, lang="es", href="https://x.com/es/"))
    # Solo de ida: `en` no apunta a `fr`, y `fr` ni se rastreo.
    s.add(Hreflang(url_id=en.id, lang="pt", href="https://x.com/pt/"))
    # x-default: no necesita retorno.
    s.add(Hreflang(url_id=es.id, lang="x-default", href="https://x.com/"))
    # Destino roto: no se puede saber si devuelve el enlace.
    s.add(Hreflang(url_id=es.id, lang="de", href="https://x.com/de/"))
    # Idioma invalido.
    s.add(Hreflang(url_id=es.id, lang="en-UK", href="https://x.com/uk/"))
    s.flush()

    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()

    filas = {(h.url_id, h.lang): h for h in s.query(Hreflang).all()}
    assert len(filas) == 6, "ninguna fila se queda fuera del lote"

    assert filas[(es.id, "en")].return_tag_ok is True, "reciproco"
    assert filas[(en.id, "es")].return_tag_ok is True
    assert filas[(en.id, "pt")].return_tag_ok is None, "destino sin rastrear"
    assert filas[(es.id, "x-default")].return_tag_ok is None, "no necesita retorno"
    assert filas[(es.id, "de")].return_tag_ok is None, "destino roto: no se sabe"

    assert filas[(es.id, "en-UK")].lang_valid is False
    assert all(filas[k].lang_valid is True
               for k in filas if k != (es.id, "en-UK"))
