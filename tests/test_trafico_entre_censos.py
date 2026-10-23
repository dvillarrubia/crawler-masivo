"""El trafico de Google entre dos censos, agrupado por lo que cambio
(decision 158), EJECUTADO contra SQLite: caida con cambio, sin cambio, caida
general, ventana incompleta, dia recortado, ruptura de serie, estacional,
subidas y la comparacion que espera a los datos."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared import trafico_entre_censos as T
from shared.gsc import clave_de_url
from shared.models import Base, Client, GscEstado, GscPagina, Job

A = datetime(2026, 9, 1, 3, tzinfo=timezone.utc)     # censo anterior
B = datetime(2026, 10, 1, 3, tzinfo=timezone.utc)    # censo nuevo


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def s():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _montar(s, *, primer=date(2026, 7, 1), ultimo=date(2026, 10, 20), truncados=()):
    c = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={},
               integraciones=[{"tipo": "gsc", "activa": True, "credencial_env": "GSC_X",
                               "parametros": {"propiedad": "sc-domain:x.com"}}])
    s.add(c)
    s.flush()
    ja = Job(id=uuid.uuid4(), name="a", status="completed", seeds=["https://x.com/"], config={},
             cliente_id=c.id, started_at=A)
    jb = Job(id=uuid.uuid4(), name="b", status="completed", seeds=["https://x.com/"], config={},
             cliente_id=c.id, started_at=B)
    s.add_all([ja, jb])
    s.add(GscEstado(cliente_id=c.id, propiedad="sc-domain:x.com", estado="ok", primer_dia=primer,
                    ultimo_dia=ultimo, dias_truncados=list(truncados)))
    # Un dia con datos es un dia con filas: uno de relleno por dia guardado.
    d = primer
    while d <= ultimo:
        s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=d, url="https://x.com/__dia",
                        url_clave="__dia", clics=0, impresiones=1))
        d += timedelta(days=1)
    s.commit()
    return c, ja, jb


def _clics(s, c, ruta, en_a, en_b, *, desplazar=0):
    """Reparte los clics en un dia de cada ventana (desplazadas `desplazar` dias)."""
    url = f"https://x.com{ruta}"
    for fecha, n in ((T.ventana(_J(A), 28)[0] - timedelta(days=desplazar), en_a),
                     (T.ventana(_J(B), 28)[0] - timedelta(days=desplazar), en_b)):
        if n:
            s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=fecha, url=url,
                            url_clave=clave_de_url(url), clics=n, impresiones=10 * n))


class _J:
    def __init__(self, t):
        self.started_at = t


def _p(ruta, **kw):
    base = {"url": f"https://x.com{ruta}", "status_code": 200, "indexability_status": "Indexable",
            "title": "T", "h1": "H", "word_count": 500, "inlinks": 10, "pagerank_score": 50,
            "in_sitemap": True, "canonical_href": f"https://x.com{ruta}", "canonical_distintos": 1}
    return {**base, **kw}


def test_caida_con_cambio_y_sin_cambio_y_subida(s):
    c, ja, jb = _montar(s)
    paginas_a, paginas_b = [], []
    for i in range(10):
        _clics(s, c, f"/f{i}", 100, 30)                         # -70 %: canonical cambiado
        paginas_a.append(_p(f"/f{i}"))
        paginas_b.append(_p(f"/f{i}", canonical_href="https://x.com/otra"))
    _clics(s, c, "/quieta", 100, 20)                            # cae sin cambio tecnico
    paginas_a.append(_p("/quieta"))
    paginas_b.append(_p("/quieta"))
    _clics(s, c, "/arreglada", 10, 80)                          # sube donde quito el noindex
    paginas_a.append(_p("/arreglada", indexability_status="Noindex"))
    paginas_b.append(_p("/arreglada"))
    _clics(s, c, "/poco", 30, 15)                               # -15: por debajo de 20
    paginas_a.append(_p("/poco"))
    paginas_b.append(_p("/poco", title="otro"))
    for i in range(30):                                         # el resto del sitio, estable
        _clics(s, c, f"/e{i}", 100, 100)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, paginas_a, paginas_b)
    assert r["comparable"] and r["pendiente"] is False and r["general"] is False
    canon = next(g for g in r["caidas"] if g["cambio"] == "canonical")
    assert (canon["paginas"], canon["diferencia"]) == (10, -700)
    sin = next(g for g in r["caidas"] if g["cambio"] == "sin_cambio")
    assert sin["paginas"] == 1 and r["caidas"][-1]["cambio"] == "sin_cambio"
    assert [g["cambio"] for g in r["subidas"]] == ["noindex"]
    assert r["paginas_con_caida"] == 11 and r["paginas_con_subida"] == 1
    texto = " ".join(r["textos"])
    assert "coincide con que en ellas cambia el canonical" in texto
    assert "sin cambio técnico" in texto and " por " not in texto.replace("por página", "")
    assert "1 página perdió" in texto and "1 página ganó" in texto


def test_caida_general(s):
    c, ja, jb = _montar(s)
    paginas = [_p(f"/p{i}") for i in range(10)]
    for i in range(10):
        _clics(s, c, f"/p{i}", 100, 40)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, paginas, paginas)
    assert r["general"] is True and r["variacion"] == -0.6
    assert any("general en todo el sitio" in t for t in r["textos"])


def test_ventana_incompleta_espera_y_se_completa(s):
    from shared.models import Job as JobM

    c, ja, jb = _montar(s, ultimo=date(2026, 9, 25))
    _clics(s, c, "/a", 100, 10)
    s.commit()
    # gsc_estado dice que hay datos hasta el 20-oct, pero sin filas: no cuenta
    s.query(GscEstado).one().ultimo_dia = date(2026, 10, 20)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, [_p("/a")], [_p("/a")])
    assert r["comparable"] is False and r["pendiente"] is True and "aún no tiene datos" in r["motivo"]
    # El worker la completa cuando llegan los datos (sin paginas: censos vacios)
    jb.comparacion = {"comparado_con": str(ja.id), "trafico": r}
    s.commit()
    assert T.completar_pendientes(s, c.id) == 0                 # siguen sin llegar
    d = date(2026, 9, 26)
    while d <= date(2026, 9, 29):
        s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=d, url="https://x.com/__dia",
                        url_clave="__dia", clics=0, impresiones=1))
        d += timedelta(days=1)
    s.commit()
    assert T.completar_pendientes(s, c.id) == 1
    assert s.get(JobM, jb.id).comparacion["trafico"]["comparable"] is True


def test_un_dia_recortado_no_se_compara(s):
    c, ja, jb = _montar(s, truncados=["2026-09-10"])
    r = T.comparar_trafico(s, ja, jb, [], [])
    assert r["comparable"] is False and r["pendiente"] is False and "recortó" in r["motivo"]


def test_sin_datos_al_principio_no_se_compara(s):
    c, ja, jb = _montar(s, primer=date(2026, 9, 1))
    r = T.comparar_trafico(s, ja, jb, [], [])
    assert r["comparable"] is False and r["pendiente"] is False and "les faltan" in r["motivo"]


def test_la_ventana_son_los_dias_entre_censos(s):
    c, ja, jb = _montar(s)
    jb.started_at = A + timedelta(days=9)
    r = T.comparar_trafico(s, ja, jb, [], [])
    assert r["dias"] == 7 and r["ventana_anterior"] == ["2026-08-24", "2026-08-30"]
    assert r["ventana_nueva"] == ["2026-09-02", "2026-09-08"]                    # sin pisarse
    jb.started_at = A + timedelta(days=3)
    r = T.comparar_trafico(s, ja, jb, [], [])
    assert r["comparable"] is False and "a 3 días:" in r["motivo"]
    assert T.dias_de_ventana(_J(A), _J(B)) == 28                                 # 30 dias: tope


def test_sin_search_console_no_hay_nada(s):
    c, ja, jb = _montar(s)
    s.query(GscEstado).delete()
    s.commit()
    assert T.comparar_trafico(s, ja, jb, [], []) is None


def test_ruptura_de_serie(s, monkeypatch):
    monkeypatch.setattr(T, "RUPTURAS", ({"fecha": "2026-09-15", "metrica": "impresiones", "texto": "X."},))
    c, ja, jb = _montar(s)
    r = T.comparar_trafico(s, ja, jb, [], [])
    assert [x["fecha"] for x in r["rupturas"]] == ["2026-09-15"]
    assert any("ruptura de Search Console (15/09/2026)" in t for t in r["textos"])


def test_estacional_con_el_ano_anterior(s):
    c, ja, jb = _montar(s, primer=date(2025, 6, 1))
    paginas = [_p(f"/p{i}") for i in range(5)]
    for i in range(5):
        _clics(s, c, f"/p{i}", 100, 40)
        _clics(s, c, f"/p{i}", 100, 45, desplazar=364)          # hace un ano cayo casi igual
    s.commit()
    r = T.comparar_trafico(s, ja, jb, paginas, paginas)
    assert r["estacional"]["igual"] is True
    assert any("parece estacional" in t for t in r["textos"])


@pytest.mark.parametrize("pa, pb, esperado", [
    (_p("/a"), None, ["desaparece"]),
    (_p("/a"), _p("/a", status_code=301), ["redireccion_nueva"]),
    (_p("/a"), _p("/a", status_code=404, indexability_status="Client Error"), ["error_nuevo"]),
    (_p("/a"), _p("/a", indexability_status="Noindex"), ["noindex"]),
    (_p("/a"), _p("/a", h1="Otro"), ["h1"]),
    (_p("/a"), _p("/a", word_count=200), ["contenido"]),
    (_p("/a"), _p("/a", in_sitemap=False), ["sale_del_sitemap"]),
    (_p("/a", inlinks=100), _p("/a", inlinks=20), ["pierde_enlaces"]),
    (_p("/a"), _p("/a", inlinks=2), []),                                 # 8 menos: ruido
    (_p("/a"), _p("/a", pagerank_score=30), ["pierde_pagerank"]),
    (_p("/a"), _p("/a", title="  t "), []),
    (_p("/a"), _p("/a", canonical_href="https://x.com/b", canonical_distintos=2), []),
])
def test_cambios_de_pagina(pa, pb, esperado):
    assert T.cambios_de_pagina(pa, pb) == esperado


def test_el_trafico_que_pasa_a_su_canonical_es_trasvase(s):
    c, ja, jb = _montar(s)
    con_barra = _p("/hotel/", canonical_href="https://x.com/hotel")
    _clics(s, c, "/hotel/", 90, 4)
    _clics(s, c, "/hotel", 40, 120)
    for i in range(20):
        _clics(s, c, f"/e{i}", 100, 100)
    s.commit()
    paginas = [con_barra, _p("/hotel")]
    r = T.comparar_trafico(s, ja, jb, paginas, paginas)
    assert [g["cambio"] for g in r["caidas"]] == ["trasvase"]
    assert [g["cambio"] for g in r["subidas"]] == ["recibe_trasvase"]
    assert any("se mueve, no se pierde" in t for t in r["textos"])
    assert not any("sin cambio técnico" in t for t in r["textos"])


def test_un_clic_de_subida_no_es_trasvase(s):
    c, ja, jb = _montar(s)
    _clics(s, c, "/producto/", 300, 0)
    _clics(s, c, "/producto", 2, 3)
    for i in range(20):
        _clics(s, c, f"/e{i}", 100, 100)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, [_p("/producto/"), _p("/producto")],
                           [_p("/producto/", status_code=404, indexability_status="Client Error"), _p("/producto")])
    assert [g["cambio"] for g in r["caidas"]] == ["error_nuevo"]


def test_un_censo_no_concluyente_no_afirma_nada(s):
    c, ja, jb = _montar(s)
    r = T.comparar_trafico(s, ja, jb, [], [], concluyente=False, motivo_no_concluyente="el censo nuevo paró en max_urls")
    assert r["comparable"] is False and "no es concluyente (el censo nuevo paró en max_urls)" in r["motivo"]


def test_una_pagina_que_no_estaba_antes_no_es_sin_cambio(s):
    c, ja, jb = _montar(s)
    _clics(s, c, "/nueva", 100, 10)
    for i in range(20):
        _clics(s, c, f"/e{i}", 100, 100)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, [], [_p("/nueva")])
    assert [g["cambio"] for g in r["caidas"]] == ["sin_dato_anterior"]
    assert any("no hay con qué comparar" in t for t in r["textos"])


def test_solo_las_propiedades_activas_y_los_hosts_del_censo(s):
    c, ja, jb = _montar(s)
    paginas = [_p(f"/p{i}") for i in range(5)]
    for i in range(5):
        _clics(s, c, f"/p{i}", 100, 100)
    vieja = T.ventana(_J(A), 28)[0]
    s.add(GscPagina(cliente_id=c.id, propiedad="https://x.com/", fecha=vieja, url="https://x.com/p0",
                    url_clave=clave_de_url("https://x.com/p0"), clics=5000, impresiones=1))   # propiedad vieja
    s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=vieja, url="https://blog.x.com/a",
                    url_clave=clave_de_url("https://blog.x.com/a"), clics=5000, impresiones=1))  # otro host
    s.commit()
    r = T.comparar_trafico(s, ja, jb, paginas, paginas)
    assert (r["clics_anterior"], r["clics_nuevo"], r["general"]) == (500, 500, False)
