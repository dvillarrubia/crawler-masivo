"""Soft 404 de plantilla como UN aviso agrupado (decision 190).

Ejecutan el analisis contra SQLite (decision 74), con los mismos ayudantes que
`test_soft404.py`. Fallan sin el cambio: nadie emitia `soft_404_plantilla`, y
en Lopesan el Resumen decia «Soft 404: 0 paginas» en verde junto a 7 sondas de
plantilla que respondian 200 a URLs que no existen.
"""

from __future__ import annotations

import uuid

from shared.models import Job
from shared.plantillas import firma_de_ruta

from test_soft404 import ERROR, NORMAL, _analizar, _pagina, _sonda, sesion  # noqa: F401


def _plantilla(path: str) -> str:
    return firma_de_ruta(path)


def _filas(issues, tipo="soft_404_plantilla"):
    return [i for k, i in issues.items() if k[1] == tipo]


def test_el_caso_lopesan_categorias_que_responden_200(sesion):
    """La raiz redirige, los posts responden 404 y las categorias 200: una
    fila, de las categorias, sin ninguna pagina marcada como soft 404."""
    job_id = uuid.uuid4()
    cat = _plantilla("/blog/category/destinos/")
    post = _plantilla("/blog/un-post/")
    sesion.add(Job(id=job_id, name="t", seeds=[], config={}, soft404={"sondas": [
        _sonda(path="/zz00", status=302, redirige_a="https://x.com/es/zz00"),
        _sonda(path="/blog/zz01/", status=404, plantilla=post),
        _sonda(path="/blog/category/zz02/", status=200, plantilla=cat),
    ]}))
    _pagina(sesion, job_id, 1, "/blog/un-post/", NORMAL)
    _pagina(sesion, job_id, 2, "/blog/otro-post/", NORMAL + " dos")
    _pagina(sesion, job_id, 3, "/blog/category/tematicas/", NORMAL + " tres")
    _pagina(sesion, job_id, 4, "/blog/category/destinos/", NORMAL + " cuatro")
    sesion.commit()

    issues = _analizar(sesion, job_id)
    filas = _filas(issues)
    assert len(filas) == 1, "un aviso por hallazgo: una fila por host, no una por pagina"
    f = filas[0]
    assert f.severity == "warning"
    assert f.details["plantillas_afectadas"] == 1
    assert [p["plantilla"] for p in f.details["plantillas"]] == [cat]
    assert f.details["plantillas"][0]["status"] == 200
    assert f.details["paginas_afectadas"] == 2, "las paginas del censo con esa plantilla"
    assert f.url_id == 4, "anclada en la URL menor de sus paginas (decision 88)"
    assert f.group_key
    assert f.details["raiz"] is False
    assert not _filas(issues, "soft_404"), "ninguna pagina es la pagina de error"
    resumen = sesion.get(Job, job_id).soft404["resumen"]
    assert resumen["plantillas_responden_200"] == 1
    assert resumen["soft_404"] == 0


def test_lo_que_no_se_puede_afirmar_no_emite(sesion):
    """Redireccion, error, muro de WAF y 404: ninguna dice que el sitio
    responda 200 a lo que no existe."""
    job_id = uuid.uuid4()
    p = _plantilla("/fichas/x")
    sesion.add(Job(id=job_id, name="t", seeds=[], config={}, soft404={"sondas": [
        _sonda(path="/fichas/zz1", status=301, plantilla=p, redirige_a="https://x.com/"),
        _sonda(path="/fichas/zz2", status=200, plantilla=p, error="timeout"),
        _sonda(path="/fichas/zz3", status=200, plantilla=p, response_class="challenge_waf"),
        _sonda(path="/fichas/zz4", status=200, plantilla=p, titulo="Just a moment..."),
        _sonda(path="/zz5", status=404),
    ]}))
    _pagina(sesion, job_id, 1, "/fichas/a", NORMAL)
    _pagina(sesion, job_id, 2, "/fichas/b", NORMAL + " b")
    sesion.commit()
    assert not _filas(_analizar(sesion, job_id))
    assert sesion.get(Job, job_id).soft404["resumen"]["plantillas_responden_200"] == 0


def test_la_raiz_que_responde_200_afecta_a_todo_el_host(sesion):
    job_id = uuid.uuid4()
    sesion.add(Job(id=job_id, name="t", seeds=[], config={}, soft404={"sondas": [
        _sonda(path="/zz00", status=200, texto=NORMAL.replace("propio", "x")),
        _sonda(path="/a/zz01", status=200, plantilla=_plantilla("/a/b"),
               texto=NORMAL.replace("propio", "y")),
    ]}))
    _pagina(sesion, job_id, 1, "/a/b", ERROR + " uno" * 40)
    _pagina(sesion, job_id, 2, "/c/d", NORMAL + " dos")
    _pagina(sesion, job_id, 3, "/c/e", NORMAL + " tres")
    sesion.commit()
    f = _filas(_analizar(sesion, job_id))
    assert len(f) == 1
    d = f[0].details
    assert d["raiz"] is True and d["plantillas_afectadas"] == 2
    assert d["plantillas"][0]["plantilla"] is None, "la raiz primero"
    assert d["paginas_afectadas"] == 3


def test_una_fila_por_host(sesion):
    from shared.models import HtmlMeta, PageContent, Url

    job_id = uuid.uuid4()
    otro = {"clave": "y.com|raiz", "host": "y.com", "plantilla": None,
            "url": "https://y.com/zz9", "status": 200, "redirige_a": None,
            "titulo": "Y", "texto": "nada", "origen": "rastreo"}
    sesion.add(Job(id=job_id, name="t", seeds=[], config={}, soft404={"sondas": [
        _sonda(path="/zz00", status=200), otro]}))
    _pagina(sesion, job_id, 1, "/a/b", NORMAL)
    sesion.add(Url(id=2, job_id=job_id, url="https://y.com/p", url_hash="hy", host="y.com",
                   path="/p", is_html=True, is_internal=True, status_code=200,
                   indexable=True, content_word_count=len(NORMAL.split())))
    sesion.add(PageContent(url_id=2, content_text=NORMAL + " y"))
    sesion.add(HtmlMeta(url_id=2, title="P | Y"))
    sesion.commit()
    filas = _filas(_analizar(sesion, job_id))
    assert sorted(f.details["host"] for f in filas) == ["x.com", "y.com"]
    assert len({f.group_key for f in filas}) == 2


def test_entre_censos_casa_aunque_cambie_la_url_de_anclaje():
    """Ancla por grupo (decision 122): la URL menor de las paginas de la
    plantilla cambia de un censo a otro y el hallazgo es el mismo."""
    from shared.comparacion import comparar_incidencias

    a = [{"issue_type": "soft_404_plantilla", "severity": "warning", "group_key": "k1",
          "url": "https://x.com/blog/category/a/"}]
    b = [{"issue_type": "soft_404_plantilla", "severity": "warning", "group_key": "k1",
          "url": "https://x.com/blog/category/b/"}]
    r = comparar_incidencias(a, b, analisis_a="v", analisis_b="v")
    assert r["siguen"]["total"] == 1
    assert r["nuevas"]["total"] == 0 and r["resueltas"]["total"] == 0
