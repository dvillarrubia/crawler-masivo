"""De dos censos en base de datos al «qué se arregló» (#32).

Se ejecuta el analizador de verdad sobre dos censos sintéticos y se compara lo
que escribió. Es lo que comprueba que `group_key` se RELLENA, no solo que la
columna existe: una clave declarada y nunca escrita deja la comparación
contando cualquier cosa.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.comparacion import comparar_incidencias, incidencias_de_censo
from shared.models import Base, Heading, HtmlMeta, Hreflang, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _sesion():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _censo(s, dia, *, titulo_de_b, hreflangs):
    """Dos páginas: `/a` siempre igual, `/b` con lo que cambie."""
    j = Job(id=uuid.uuid4(), name=f"censo {dia}", status="completed",
            finish_reason="finished", seeds=["https://x.com/"], config={},
            started_at=datetime(2026, 9, dia, tzinfo=timezone.utc))
    s.add(j)
    s.flush()
    for ruta, titulo in (("/a", "Zapatillas de running para asfalto"),
                         ("/b", titulo_de_b)):
        u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=f"{dia}{ruta}",
                is_internal=True, is_html=True, status_code=200,
                indexable=True, indexability_status="Indexable", word_count=800)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=titulo, title_len=len(titulo),
                       canonical_href=f"https://x.com{ruta}"))
        s.add(Heading(url_id=u.id, tag="h1", position=0, text="Titular"))
        if ruta == "/b":
            for lang in hreflangs:
                s.add(Hreflang(url_id=u.id, lang=lang,
                               href=f"https://x.com/{lang}/b"))
    s.commit()
    return j.id


def test_de_dos_censos_a_que_se_arreglo():
    s = _sesion()
    # Antes: `/b` comparte titulo con `/a` (duplicado) y tiene DOS hreflang
    # invalidos. Despues: titulo propio y solo uno de los dos arreglado.
    viejo = _censo(s, 1, titulo_de_b="Zapatillas de running para asfalto",
                   hreflangs=["en-UK", "es-SP"])
    nuevo = _censo(s, 8, titulo_de_b="Botas de montana impermeables",
                   hreflangs=["en-GB", "es-SP"])
    for jid in (viejo, nuevo):
        a = SEOAnalyzer(s, jid)
        a.analyze_titles()
        a.analyze_hreflang()
        a._flush_issues()
    s.commit()

    r = comparar_incidencias(incidencias_de_censo(s, viejo),
                             incidencias_de_censo(s, nuevo))

    tipos_resueltos = {f["tipo"] for f in r["resueltas"]["por_tipo"]}
    assert "title_duplicate" in tipos_resueltos, (
        "al cambiar el titulo de /b, el duplicado se resolvio en LAS DOS")
    assert "hreflang_invalid_lang" in tipos_resueltos, "en-UK se arreglo"

    siguen = {f["tipo"]: f["total"] for f in r["siguen"]["por_tipo"]}
    assert siguen.get("hreflang_invalid_lang") == 1, (
        "es-SP sigue mal: sin `group_key` los dos hreflang se confundirian y "
        "arreglar uno pareceria arreglar los dos")
    s.close()


def test_group_key_se_RELLENA_no_solo_existe():
    """Una clave declarada y nunca escrita deja la comparacion contando
    cualquier cosa: dos hallazgos distintos en la misma URL serian el mismo.
    """
    s = _sesion()
    jid = _censo(s, 1, titulo_de_b="Zapatillas de running para asfalto",
                 hreflangs=["en-UK", "es-SP"])
    a = SEOAnalyzer(s, jid)
    a.analyze_titles()
    a.analyze_hreflang()
    a._flush_issues()
    s.commit()

    incidencias = incidencias_de_censo(s, jid)
    por_tipo = {}
    for i in incidencias:
        por_tipo.setdefault(i["issue_type"], []).append(i["group_key"])

    assert all(k for k in por_tipo["title_duplicate"]), (
        "un duplicado pertenece a un grupo y la clave es su valor compartido")
    claves_hreflang = por_tipo["hreflang_invalid_lang"]
    assert all(k for k in claves_hreflang)
    assert len(set(claves_hreflang)) == 2, (
        "dos codigos de idioma distintos, dos claves distintas")
    s.close()
