"""Accesibilidad con axe por muestra de plantilla (decision 174): el resumen de
lo que devuelve axe, la agregacion por regla y plantilla, y el analisis
EJECUTADO contra SQLite (una fila por regla y plantilla, nunca `error`, lo
incompleto y lo no evaluado no se afirman, y las capturas de axe no se leen
como diferencias de render ni de movil)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_accesibilidad, avisos_de_render
from analysis.analyzer import SEOAnalyzer
from shared.accesibilidad import ETIQUETAS, agregar, criterios, nivel, por_regla, resumir
from shared.models import Base, CapturaAlternativa, Issue, Job, Url
from shared.respuestas import CHALLENGE_WAF

RAIZ = Path(__file__).resolve().parent.parent
VENDOR = RAIZ / "crawler" / "seo_crawler" / "vendor" / "axe"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------
def test_criterios_y_nivel_salen_de_las_etiquetas_de_axe():
    assert criterios(["cat.color", "wcag2aa", "wcag143", "EN-301-549", "wcag1410"]) == ["1.4.3", "1.4.10"]
    assert nivel(["wcag2aa", "wcag143"]) == "AA"
    assert nivel(["wcag2a", "wcag111"]) == "A"
    assert nivel(["best-practice"]) is None


def test_solo_se_piden_reglas_wcag_a_y_aa():
    assert all(e.startswith("wcag2") and e.endswith(("a", "aa")) for e in ETIQUETAS)
    assert not any("aaa" in e for e in ETIQUETAS) and "best-practice" not in ETIQUETAS


CRUDO = {
    "version": "4.14.0", "ms": 812.4, "passes": 20,
    "violations": [
        {"id": "color-contrast", "impact": "serious", "tags": ["wcag2aa", "wcag143"],
         "help": "Contraste", "helpUrl": "https://dequeuniversity.com/rules/axe/4.14/color-contrast",
         "n": 14, "nodes": [{"target": ["#a"], "html": "<p>" + "x" * 500}, {"target": [["#host", "b"]], "html": "<b>"}]},
        {"id": "image-alt", "impact": "critical", "tags": ["wcag2a", "wcag111"], "help": "Alt",
         "helpUrl": "u", "n": 2, "nodes": [{"target": ["img"], "html": "<img>"}]},
    ],
    "incomplete": [{"id": "bypass", "impact": "serious", "tags": ["wcag2a", "wcag241"], "help": "Saltar",
                    "helpUrl": "u", "n": 1, "nodes": []}],
}


def test_resumir_cuenta_todos_los_nodos_y_guarda_pocos_ejemplos_recortados():
    r = resumir(CRUDO)
    assert r["axe"] == "4.14.0" and r["ms_axe"] == 812 and r["reglas_que_pasan"] == 20
    # De mas a menos grave: el critico primero aunque tenga menos nodos.
    assert [v["regla"] for v in r["violaciones"]] == ["image-alt", "color-contrast"]
    cc = r["violaciones"][1]
    assert cc["nodos"] == 14 and cc["criterios"] == ["1.4.3"] and cc["nivel"] == "AA"
    assert len(cc["ejemplos"]) == 2 and len(cc["ejemplos"][0]["html"]) == 240
    assert cc["ejemplos"][1]["selector"] == "#host b"  # shadow DOM: lista de listas
    assert r["incompletas"] == [{"regla": "bypass", "impacto": "serious", "criterios": ["2.4.1"],
                                 "nivel": "A", "ayuda": "Saltar", "url_ayuda": None, "nodos": 1}]


def test_una_regla_experimental_no_se_afirma():
    r = resumir({"version": "4", "incomplete": [], "violations": [
        {"id": "p-as-heading", "impact": "serious", "tags": ["wcag2a", "wcag131", "experimental"],
         "help": "", "helpUrl": "", "n": 2}]})
    assert r["violaciones"] == [] and [x["regla"] for x in r["incompletas"]] == ["p-as-heading"]


def test_un_resultado_que_no_es_de_axe_es_no_evaluado_no_limpio():
    assert resumir(None) is None
    assert resumir({"error": "tope_axe"}) is None
    assert resumir({"version": "x"}) is None  # sin `violations`
    limpio = resumir({"version": "4", "violations": [], "incomplete": []})
    assert limpio["violaciones"] == [] and limpio["incompletas"] == []


def test_agregar_y_por_regla_juntan_por_regla_y_plantilla_con_el_peor_impacto():
    m1 = resumir(CRUDO)
    m2 = resumir({**CRUDO, "violations": [{**CRUDO["violations"][0], "impact": "critical", "n": 3}]})
    datos = agregar([(1, "https://x.com/f/b", "/f/:slug", m1), (2, "https://x.com/f/a", "/f/:slug", m2),
                     (3, "https://x.com/", "/ (home)", resumir({"version": "4", "violations": []})),
                     (4, "https://x.com/z", "/z", None)])
    assert datos["muestreadas"] == {"/f/:slug": 2, "/ (home)": 1}
    g = datos["violaciones"][("color-contrast", "/f/:slug")]
    assert g["impacto"] == "critical" and g["nodos"] == 17
    assert [p["url"] for p in g["paginas"]] == ["https://x.com/f/a", "https://x.com/f/b"]
    reglas = por_regla(datos["violaciones"], datos["muestreadas"])
    assert [r["regla"] for r in reglas] == ["color-contrast", "image-alt"]
    assert reglas[0]["plantillas"] == [{"plantilla": "/f/:slug", "paginas": 2, "de": 2}]
    assert reglas[0]["ejemplo"] == "https://x.com/f/a"
    assert list(datos["incompletas"]) == [("bypass", "/f/:slug")]


def test_axe_vendorizado_con_su_licencia_y_el_locale():
    fuente = (VENDOR / "axe.min.js").read_text(encoding="utf-8")
    assert fuente.startswith("/*! axe v4.") and "Mozilla Public" in fuente[:400]
    assert "Mozilla Public License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert (VENDOR / "LICENSE-3RD-PARTY.txt").exists()
    es = json.loads((VENDOR / "es.json").read_text(encoding="utf-8"))
    assert es["lang"] == "es" and "image-alt" in es["rules"]
    version = fuente.split("\n", 1)[0].split("axe v", 1)[1].strip()
    assert version in (VENDOR / "README.md").read_text(encoding="utf-8")


def test_el_script_lleva_axe_el_locale_las_etiquetas_y_el_tope():
    import sys

    sys.path.insert(0, str(RAIZ / "crawler"))
    try:
        from seo_crawler.sonda_axe import TOPE_AXE_MS, script_axe
    except ImportError:  # sin scrapy: el resto ya lo cubre
        return
    s = script_axe()
    assert s.startswith("/*! axe v4.") and "window._axeLocale = {" in s
    assert json.dumps(list(ETIQUETAS)) in s and str(TOPE_AXE_MS) in s
    assert "iframes: false" in s and "cdn" not in s.split("window._axeLocale", 1)[1].lower()


# ---------------------------------------------------------------------------
# Analisis contra SQLite
# ---------------------------------------------------------------------------
def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, CapturaAlternativa.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _pagina(s, j, ruta, *, status=200, clase="contenido"):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            is_html=True, status_code=status, host="x.com", resource_type="html",
            response_class=clase, crawl_depth=1)
    s.add(u)
    s.flush()
    return u


def _axe(s, j, u, crudo=None, *, estado="evaluada", plantilla="/f/:slug", detalle=None):
    s.add(CapturaAlternativa(job_id=j.id, url_id=u.id, perfil="axe", modo="render", estado=estado,
                             plantilla=plantilla, medidas=resumir(crudo) if crudo else None,
                             detalle=detalle or {"ms_axe": 800, "ms_total": 2400}))
    s.flush()


def _analizar(s, j):
    az = SEOAnalyzer(s, j.id)
    avisos_de_accesibilidad.avisar(az)
    avisos_de_render.avisar(az)
    az.analyze_paridad_movil()
    s.flush()
    return s.execute(select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details,
                            Issue.group_key).where(Issue.job_id == j.id)).all()


def test_una_fila_por_regla_y_plantilla_anclada_en_la_url_menor_y_nunca_error():
    s, j = _montar()
    b, a, c = (_pagina(s, j, f"/f/{x}") for x in "bac")
    home = _pagina(s, j, "/")
    _axe(s, j, b, CRUDO)
    _axe(s, j, a, CRUDO)
    _axe(s, j, c, {"version": "4", "violations": [], "incomplete": []})  # muestreada y limpia
    _axe(s, j, home, {"version": "4", "violations": [CRUDO["violations"][0]], "incomplete": []},
         plantilla="/ (home)")
    filas = _analizar(s, j)
    assert {f.severity for f in filas} <= {"warning", "info"}
    por = {(f.details["regla"], f.details["plantilla"]): f for f in filas}
    assert set(por) == {("color-contrast", "/f/:slug"), ("image-alt", "/f/:slug"),
                        ("color-contrast", "/ (home)")}
    cc = por[("color-contrast", "/f/:slug")]
    assert cc.issue_type == "accesibilidad_grave" and cc.severity == "warning"
    assert cc.url_id == a.id  # la URL menor del grupo
    assert cc.details["paginas_afectadas"] == 2 and cc.details["paginas_muestreadas"] == 3
    assert cc.details["nodos"] == 28 and cc.details["wcag"] == "1.4.3" and cc.details["nivel"] == "AA"
    assert cc.details["impacto_axe"] == "serious" and cc.details["impacto"] == "grave"
    assert cc.details["ejemplos"][0]["url"] == "https://x.com/f/a"
    assert cc.group_key and cc.group_key != por[("color-contrast", "/ (home)")].group_key
    # Las incompletas (bypass) no avisan; las capturas de axe no son render ni movil.
    assert not any(f.details.get("regla") == "bypass" for f in filas)
    assert {f.issue_type for f in filas} == {"accesibilidad_grave"}


def test_impacto_moderado_o_leve_es_info():
    s, j = _montar()
    u = _pagina(s, j, "/f/a")
    _axe(s, j, u, {"version": "4", "incomplete": [], "violations": [
        {"id": "region", "impact": "moderate", "tags": ["wcag2a"], "help": "", "helpUrl": "", "n": 4},
        {"id": "x-leve", "impact": "minor", "tags": ["wcag2aa"], "help": "", "helpUrl": "", "n": 1}]})
    filas = _analizar(s, j)
    assert sorted((f.issue_type, f.severity) for f in filas) == [
        ("accesibilidad_menor", "info"), ("accesibilidad_menor", "info")]


def test_lo_no_evaluado_y_las_paginas_que_ya_no_son_200_no_avisan():
    s, j = _montar()
    muro = _pagina(s, j, "/f/muro", clase=CHALLENGE_WAF)
    roto = _pagina(s, j, "/f/roto", status=404)
    sin = _pagina(s, j, "/f/sin")
    _axe(s, j, muro, CRUDO)
    _axe(s, j, roto, CRUDO)
    _axe(s, j, sin, None, estado="no_evaluada", detalle={"motivo": "axe_fallido"})
    assert _analizar(s, j) == []


def test_sin_tabla_no_falla_el_analisis():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    avisos_de_accesibilidad.avisar(SEOAnalyzer(s, j.id))
    assert s.query(Issue).count() == 0


def test_reanalizar_da_lo_mismo():
    s, j = _montar()
    _axe(s, j, _pagina(s, j, "/f/a"), CRUDO)
    primero = sorted((f.issue_type, f.details["regla"]) for f in _analizar(s, j))
    s.query(Issue).delete()
    assert sorted((f.issue_type, f.details["regla"]) for f in _analizar(s, j)) == primero


# --- Lo que devuelve el navegador lo controla la pagina auditada ------------
# (puede suplantar `window.axe` antes de que se inyecte el bundle).


def test_url_ayuda_solo_http_o_https():
    def url(h):
        r = resumir({"version": "4", "incomplete": [], "violations": [
            {"id": "r", "impact": "critical", "tags": ["wcag2a"], "help": "h", "helpUrl": h,
             "nodes": []}]})
        return r["violaciones"][0]["url_ayuda"]

    assert url("javascript:alert(document.domain)") is None
    assert url(" JaVaScRiPt:alert(1)") is None
    assert url("data:text/html,x") is None
    assert url("https://dequeuniversity.com/rules/axe/4.14/r?application=axeAPI") \
        == "https://dequeuniversity.com/rules/axe/4.14/r?application=axeAPI"


def test_resumir_pone_tope_a_las_reglas_y_se_queda_con_las_mas_graves():
    from shared.accesibilidad import MAX_REGLAS

    viol = [{"id": f"r{i}", "impact": "minor", "tags": ["wcag2a"], "nodes": []}
            for i in range(50_000)]
    viol.append({"id": "grave", "impact": "critical", "tags": ["wcag2a"], "nodes": []})
    r = resumir({"version": "x", "violations": viol, "incomplete": viol, "passes": "no"})
    assert len(r["violaciones"]) == MAX_REGLAS and len(r["incompletas"]) == MAX_REGLAS
    assert r["reglas_recortadas"] > 0 and r["reglas_que_pasan"] == 0
    # Una lista que no es lista no revienta: cuenta como vacia.
    assert resumir({"version": "x", "violations": "x", "incomplete": 3})["violaciones"] == []


# --- Comparacion entre censos (shared.comparacion) ---------------------------


def _inc(url, plantilla="/blog/:slug", gk="k1", tipo="accesibilidad_grave"):
    return {"issue_type": tipo, "severity": "warning", "group_key": gk, "url": url,
            "plantilla": plantilla}


def _cmp(a, b, pa, pb, urls=("https://x.com/blog/a", "https://x.com/blog/b")):
    from shared.comparacion import comparar_incidencias

    return comparar_incidencias(a, b, analisis_a="v", analisis_b="v", urls_del_nuevo=set(urls),
                                urls_del_anterior=set(urls),
                                plantillas_axe_a=pa, plantillas_axe_b=pb)


def test_comparar_el_mismo_aviso_de_axe_en_otra_pagina_de_la_muestra_sigue():
    r = _cmp([_inc("https://x.com/blog/a")], [_inc("https://x.com/blog/b")],
             {"/blog/:slug"}, {"/blog/:slug"})
    assert r["nuevas"]["total"] == 0 and r["resueltas"]["total"] == 0
    assert r["siguen"]["total"] == 1


def test_comparar_sin_axe_en_el_nuevo_no_afirma_resueltas():
    r = _cmp([_inc("https://x.com/blog/a")], [], {"/blog/:slug"}, set())
    assert r["resueltas"]["total"] == 0 and r["sin_pagina_en_el_nuevo"] == 0
    assert r["no_afirmables_por_muestra"]["total"] == 1
    # Sin saber que evaluo (None), tampoco.
    r = _cmp([_inc("https://x.com/blog/a")], [], {"/blog/:slug"}, None)
    assert r["resueltas"]["total"] == 0 and r["no_afirmables_por_muestra"]["total"] == 1


def test_comparar_sin_axe_en_el_anterior_no_afirma_nuevas():
    r = _cmp([], [_inc("https://x.com/blog/a")], set(), {"/blog/:slug"})
    assert r["nuevas"]["total"] == 0 and r["nuevas"]["en_url_existente"]["total"] == 0
    assert r["no_afirmables_por_muestra"]["total"] == 1


def test_comparar_con_la_plantilla_evaluada_en_los_dos_si_afirma():
    # Resuelta aunque la pagina muestreada antes ya no este en el censo: el
    # aviso es de la plantilla, y el nuevo la evaluo en otra pagina.
    r = _cmp([_inc("https://x.com/blog/vieja")], [], {"/blog/:slug"}, {"/blog/:slug"})
    assert r["resueltas"]["total"] == 1 and r["sin_pagina_en_el_nuevo"] == 0
    r = _cmp([], [_inc("https://x.com/blog/a", gk="k2")], {"/blog/:slug"}, {"/blog/:slug"})
    assert r["nuevas"]["total"] == 1 and r["no_afirmables_por_muestra"]["total"] == 0
    # Otra plantilla que el anterior no muestreo: no se afirma.
    r = _cmp([], [_inc("https://x.com/a", plantilla="/:x")], {"/blog/:slug"}, {"/:x"})
    assert r["nuevas"]["total"] == 0 and r["no_afirmables_por_muestra"]["total"] == 1


def test_comparar_lo_que_no_es_de_axe_no_cambia():
    otro = {"issue_type": "missing_title", "severity": "error", "group_key": None,
            "url": "https://x.com/blog/a"}
    r = _cmp([otro], [], None, None)
    assert r["resueltas"]["total"] == 1 and r["no_afirmables_por_muestra"]["total"] == 0


def test_comparar_dos_censos_reales_con_otra_muestra_y_luego_sin_axe():
    """De la BD a la comparacion: el anterior muestreo /f/a, el nuevo /f/b
    (la concurrencia cambia la muestra), y un tercero no paso axe."""
    from shared.accesibilidad import plantillas_evaluadas
    from shared.comparacion import comparar_incidencias, incidencias_de_censo

    s, j1 = _montar()
    _axe(s, j1, _pagina(s, j1, "/f/a"), CRUDO)
    _pagina(s, j1, "/f/b")
    _analizar(s, j1)

    def censo(con_axe):
        j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
        s.add(j)
        s.flush()
        _pagina(s, j, "/f/a")
        b = _pagina(s, j, "/f/b")
        if con_axe:
            _axe(s, j, b, CRUDO)
        _analizar(s, j)
        return j

    def cmp(viejo, nuevo):
        return comparar_incidencias(
            incidencias_de_censo(s, viejo.id), incidencias_de_censo(s, nuevo.id),
            analisis_a="v", analisis_b="v",
            urls_del_nuevo={"https://x.com/f/a", "https://x.com/f/b"},
            urls_del_anterior={"https://x.com/f/a", "https://x.com/f/b"},
            plantillas_axe_a=plantillas_evaluadas(s, viejo.id),
            plantillas_axe_b=plantillas_evaluadas(s, nuevo.id))

    assert incidencias_de_censo(s, j1.id)[0]["plantilla"] == "/f/:slug"
    assert plantillas_evaluadas(s, j1.id) == {"/f/:slug"}
    j2, j3 = censo(True), censo(False)
    assert plantillas_evaluadas(s, j3.id) == set()
    r = cmp(j1, j2)
    assert (r["nuevas"]["total"], r["resueltas"]["total"], r["siguen"]["total"]) == (0, 0, 2)
    r = cmp(j2, j3)
    assert r["resueltas"]["total"] == 0 and r["no_afirmables_por_muestra"]["total"] == 2
