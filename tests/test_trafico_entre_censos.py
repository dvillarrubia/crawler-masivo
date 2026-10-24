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


# --- Decision 165 -------------------------------------------------------------

@pytest.mark.parametrize("pa, pb, esperado", [
    # Mismas palabras, otro texto: con hash en las dos, se ve.
    (_p("/a", content_hash="h1"), _p("/a", content_hash="h2"), ["texto_contenido"]),
    # Otro texto y muchas menos palabras: «cambia mucho», no las dos cosas.
    (_p("/a", content_hash="h1"), _p("/a", word_count=200, content_hash="h2"), ["contenido"]),
    # Mismo hash: el mismo contenido aunque el recuento varie.
    (_p("/a", content_hash="h1"), _p("/a", word_count=200, content_hash="h1"), []),
    # Sin hash en una de las dos: la regla de siempre, por palabras.
    (_p("/a"), _p("/a", content_hash="h2"), []),
    (_p("/a", content_hash="h1"), _p("/a", word_count=200), ["contenido"]),
])
def test_contenido_por_hash_si_lo_tienen_las_dos(pa, pb, esperado):
    assert T.cambios_de_pagina(pa, pb, usar_hash=True) == esperado


def test_sin_usar_hash_un_hash_distinto_no_es_cambio_de_texto():
    """Sin `usar_hash` (otra version del extractor, otra config de extraccion),
    un hash distinto con las mismas palabras no es «cambia el texto»: un caracter
    o un extractor nuevo cambian el hash entero (revision del PR #230)."""
    assert T.cambios_de_pagina(_p("/a", content_hash="h1"), _p("/a", content_hash="h2")) == []
    assert T.cambios_de_pagina(_p("/a", content_hash="h1"),
                               _p("/a", word_count=200, content_hash="h2")) == ["contenido"]


class _JV:
    def __init__(self, version, config=None):
        self.crawler_version = version
        self.config = config or {}


@pytest.mark.parametrize("a, b, esperado", [
    (_JV("v1"), _JV("v1"), True),
    (_JV("v1"), _JV("v2"), False),
    # Desconocido no es igual (decision 66).
    (_JV(None), _JV(None), False),
    (_JV("v1"), _JV(None), False),
    # La misma version con otra config de extraccion tampoco.
    (_JV("v1", {"extraction": {"custom_boilerplate_selectors": [".promo"]}}), _JV("v1"), False),
    (_JV("v1", {"extraction": {"strip_promo_blocks": False}}), _JV("v1"), False),
    (_JV("v1", {"render_js": True}), _JV("v1"), False),
    # Los valores por defecto explicitos son lo mismo que no ponerlos.
    (_JV("v1", {"extraction": {"strip_promo_blocks": True, "custom_boilerplate_selectors": []}}), _JV("v1"), True),
])
def test_hash_comparable(a, b, esperado):
    assert T.hash_comparable(a, b) is esperado


def test_con_otra_version_del_crawler_las_caidas_son_sin_cambio(s):
    """Dos censos con versiones distintas: todos los hashes cambian, y la pagina
    que cae sigue siendo «sin cambio tecnico», no «cambia el texto»."""
    c, ja, jb = _montar(s)
    ja.crawler_version, jb.crawler_version = "v1", "v2"
    _clics(s, c, "/a", 100, 20)
    for i in range(30):
        _clics(s, c, f"/e{i}", 100, 100)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, [_p("/a", content_hash="h1")], [_p("/a", content_hash="h2")])
    assert r["contenido_por_hash"] is False
    assert [g["cambio"] for g in r["caidas"]] == ["sin_cambio"]
    # Con la misma version, el hash si cuenta.
    jb.crawler_version = "v1"
    s.commit()
    r = T.comparar_trafico(s, ja, jb, [_p("/a", content_hash="h1")], [_p("/a", content_hash="h2")])
    assert r["contenido_por_hash"] is True
    assert [g["cambio"] for g in r["caidas"]] == ["texto_contenido"]


def test_la_frase_de_detalle_solo_si_detras_hay_alguna_linea():
    base = {"comparable": True, "ventana_anterior": ["2026-09-01", "2026-09-28"],
            "ventana_nueva": ["2026-09-29", "2026-10-26"], "clics_anterior": 1000, "clics_nuevo": 600,
            "variacion": -0.4, "general": True, "caidas": [],
            "rupturas": [{"fecha": "2026-10-01", "texto": "algo"}]}
    t = T.textos({**base, "subidas": [{"cambio": "sin_cambio", "texto": "x", "paginas": 1, "diferencia": 30}]})
    assert not any("como detalle" in x for x in t)
    t = T.textos({**base, "subidas": [{"cambio": "title", "texto": "cambia el title", "paginas": 1,
                                       "diferencia": 30}]})
    i = next(i for i, x in enumerate(t) if "como detalle" in x)
    assert "ganó 30 clics" in t[i + 1]


def test_caida_general_primero_general_y_los_grupos_como_detalle(s):
    c, ja, jb = _montar(s)
    paginas_a = [_p(f"/p{i}") for i in range(10)]
    paginas_b = [_p(f"/p{i}", title="otro" if i < 4 else "T") for i in range(10)]
    for i in range(10):
        _clics(s, c, f"/p{i}", 100, 40)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, paginas_a, paginas_b)
    assert r["general"] is True
    t = r["textos"]
    i_general = next(i for i, x in enumerate(t) if "general en todo el sitio" in x)
    i_detalle = next(i for i, x in enumerate(t) if x.startswith("Por páginas, como detalle y no como causa"))
    i_title = next(i for i, x in enumerate(t) if "cambia el title" in x)
    i_sin = next(i for i, x in enumerate(t) if "sin cambio técnico" in x)
    assert i_general == 1 and i_general < i_detalle < i_title and i_detalle < i_sin


def test_sin_caida_general_no_hay_frase_de_detalle(s):
    c, ja, jb = _montar(s)
    _clics(s, c, "/a", 100, 20)
    for i in range(30):
        _clics(s, c, f"/e{i}", 100, 100)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, [_p("/a")], [_p("/a", title="otro")])
    assert r["general"] is False
    assert not any("como detalle" in x for x in r["textos"])


def _pendiente_que_se_completa(s, *, programado=True, slack=True):
    """Un censo cuya comparacion de trafico espero a los datos, y los datos que
    llegan despues."""
    c, ja, jb = _montar(s, ultimo=date(2026, 9, 25))
    if slack:
        c.integraciones = c.integraciones + [{"tipo": "slack", "activa": True, "credencial_env": "SLACK_X",
                                              "parametros": {"canal": "C1"}}]
    if programado:
        jb.programacion_id = uuid.uuid4()
    _clics(s, c, "/a", 100, 10)
    for i in range(30):
        _clics(s, c, f"/e{i}", 100, 100)
    s.commit()
    r = T.comparar_trafico(s, ja, jb, [_p("/a")], [_p("/a")])
    assert r["pendiente"] is True
    jb.comparacion = {"comparado_con": str(ja.id), "trafico": r}
    s.commit()
    d = date(2026, 9, 26)
    while d <= date(2026, 9, 29):
        s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=d, url="https://x.com/__dia",
                        url_clave="__dia", clics=0, impresiones=1))
        d += timedelta(days=1)
    s.commit()
    return c, ja, jb


@pytest.fixture
def enviados(monkeypatch):
    """Lo que sale a Slack, sin red: `publicar` es la llamada a la API."""
    import shared.credenciales
    import shared.slack

    import shared.comparacion

    salida = []
    # Los dos censos, sin montar `urls`: la pagina que cae y el resto estable.
    paginas = [_p("/a")] + [_p(f"/e{i}") for i in range(30)]
    monkeypatch.setattr(shared.comparacion, "paginas_para_comparar", lambda db, a, b: (paginas, paginas))
    monkeypatch.setattr(shared.slack, "publicar",
                        lambda token, canal, texto, enlace=None, titulo="Abrir": salida.append((canal, texto)) or {})
    monkeypatch.setattr(shared.credenciales, "credencial", lambda integ: "xoxb-falso")
    monkeypatch.setattr(shared.slack, "_anotar", lambda *a, **k: None)
    return salida


def test_al_completarse_se_avisa_una_vez_con_el_texto_de_trafico(s, enviados):
    from shared.models import Job as JobM

    c, ja, jb = _pendiente_que_se_completa(s)
    assert T.completar_pendientes(s, c.id) == 1
    assert len(enviados) == 1
    canal, texto = enviados[0]
    t = s.get(JobM, jb.id).comparacion["trafico"]
    assert canal == "C1" and t["comparable"] is True and t["aviso"]["estado"] == "enviado"
    # Las frases son las de `textos`, el mismo redactor que la ficha.
    for linea in T.textos(t):
        assert linea in texto
    assert "aún no tiene datos hasta" not in texto and "1 página perdió 90 clics" in texto
    # Una sola vez: otra descarga no repite el aviso.
    assert T.completar_pendientes(s, c.id) == 0
    assert len(enviados) == 1


def test_sin_slack_o_lanzado_a_mano_no_sale_nada(s, enviados):
    from shared.models import Job as JobM

    c, ja, jb = _pendiente_que_se_completa(s, slack=False)
    assert T.completar_pendientes(s, c.id) == 1
    assert enviados == []
    assert s.get(JobM, jb.id).comparacion["trafico"]["aviso"]["estado"] == "no_enviado"


def test_lanzado_a_mano_no_avisa(s, enviados):
    c, ja, jb = _pendiente_que_se_completa(s, programado=False)
    assert T.completar_pendientes(s, c.id) == 1
    assert enviados == []


def test_un_competidor_no_avisa_ni_se_compara(s, enviados):
    """Un rastreo programado de la competencia: el worker no manda su informe
    ni sus alertas, y su trafico no esta en el Search Console del cliente. Ni
    se compara ni se completa ni se avisa (revision del PR #230)."""
    from shared.models import CrawlProfile

    c, ja, jb = _pendiente_que_se_completa(s)
    rival = CrawlProfile(id=uuid.uuid4(), cliente_id=c.id, nombre="rival", seeds=["https://x.com/"],
                         rol="competidor")
    s.add(rival)
    s.flush()
    ja.profile_id = jb.profile_id = rival.id
    s.commit()
    assert T.completar_pendientes(s, c.id) == 0
    assert enviados == []
    assert T.comparar_trafico(s, ja, jb, [_p("/a")], [_p("/a")]) is None


def test_avisar_de_trafico_no_avisa_de_la_competencia(enviados):
    import shared.avisos

    class _P:
        rol = "competidor"

    class _Jb:
        id, name, programacion_id = uuid.uuid4(), "b", uuid.uuid4()

    cliente = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={},
                     integraciones=[{"tipo": "slack", "activa": True, "credencial_env": "SLACK_X",
                                     "parametros": {"canal": "C1"}}])
    t = {"comparable": False, "pendiente": False, "motivo": "x"}
    assert shared.avisos.avisar_de_trafico(cliente, _Jb(), t, _P()) is False
    assert enviados == []


def test_un_fallo_al_avisar_no_deshace_la_comparacion(s, enviados, monkeypatch):
    import shared.avisos
    from shared.models import Job as JobM

    c, ja, jb = _pendiente_que_se_completa(s)

    def roto(*a, **k):
        raise RuntimeError("slack caido")

    monkeypatch.setattr(shared.avisos, "avisar_de_trafico", roto)
    assert T.completar_pendientes(s, c.id) == 1
    t = s.get(JobM, jb.id).comparacion["trafico"]
    assert t["comparable"] is True and t["aviso"]["estado"] == "por_enviar"
