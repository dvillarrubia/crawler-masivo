"""Reglas de alerta por cliente y resumen semanal (decision 135)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared import reglas as R
from shared.comparacion import comparar_censos


@compiles(BigInteger, "sqlite")
def _bigint(tipo, compilador, **kw):
    return "INTEGER"


# --- Validar y fusionar -------------------------------------------------------

def test_sin_nada_guardado_son_las_de_serie_y_reproducen_lo_de_antes():
    e = R.efectivas(None)
    assert set(e["comparacion"]) == set(R.REGLAS)
    for regla, (sev, _q) in R.REGLAS.items():
        c = e["comparacion"][regla]
        assert c == {"activa": True, "avisar": sev == "critical", "pct_sitio": 5.0,
                     "min_paginas": 20, "pct_plantilla": 50.0}
    assert [r["id"] for r in e["propias"]] == ["caen-indexables", "suben-errores"]
    assert e["resumen_semanal"] == {"activo": True, "dia": 1, "hora": 8}


def test_lo_guardado_es_parcial_y_lo_demas_sigue_siendo_de_serie():
    g = R.validar({"comparacion": {"rotas": {"min_paginas": 5}}})
    assert g == {"comparacion": {"rotas": {"min_paginas": 5}}}
    e = R.efectivas(g)
    assert e["comparacion"]["rotas"]["min_paginas"] == 5
    assert e["comparacion"]["rotas"]["pct_sitio"] == 5.0
    assert e["comparacion"]["salen_del_indice"]["min_paginas"] == 20
    assert len(e["propias"]) == 2   # no se tocaron: siguen las de serie


@pytest.mark.parametrize("malo,trozo", [
    ({"otra": 1}, "claves desconocidas"),
    ({"comparacion": {"no_existe": {}}}, "desconocida"),
    ({"comparacion": {"rotas": {"pct_sitio": 140}}}, "fuera de rango"),
    ({"propias": [{"id": "a", "nombre": "x", "medida": "nada", "umbral": 1}]}, "medida"),
    ({"propias": [{"id": "a", "nombre": "x", "medida": "urls", "umbral": "20"}]}, "número"),
    ({"propias": [{"id": "a", "nombre": "x", "medida": "urls", "umbral": 1, "operador": "=="}]},
     "operador"),
    ({"propias": [{"id": "a", "nombre": "x", "medida": "urls", "umbral": 1}] * 2}, "mismo id"),
    ({"propias": [{"id": "A B", "nombre": "x", "medida": "urls", "umbral": 1}]}, "id"),
    ({"silenciado_hasta": "mañana"}, "fecha"),
    ({"resumen_semanal": {"dia": 8}}, "fuera de rango"),
])
def test_lo_que_no_vale_se_rechaza_con_el_motivo(malo, trozo):
    with pytest.raises(R.ReglaInvalida, match=trozo):
        R.validar(malo)


def test_una_regla_rota_escrita_a_mano_en_la_bd_se_ignora_sin_romper_las_demas():
    e = R.efectivas({"propias": [{"id": "rota", "medida": "nada"},
                                 {"id": "buena", "nombre": "B", "medida": "urls_4xx", "umbral": 3}]})
    assert [r["id"] for r in e["propias"]] == ["buena"]


def test_incidencia_concreta_es_una_medida():
    assert R.medida_valida("incidencia:4xx_error")
    assert not R.medida_valida("incidencia:DROP TABLE")
    assert R.frase({"medida": "urls_4xx", "segmento": "fichas", "modo": "valor",
                    "operador": ">", "umbral": 20}) == "URLs 4xx en «fichas»: el valor del rastreo > 20"


# --- Los umbrales del cliente en la comparacion -------------------------------

N = 100


def _pag(i, **kw):
    p = {"url": f"https://x.com/p{i}", "status_code": 200, "indexability_status": "Indexable",
         "canonical_href": None, "title": "T", "h1": "H", "word_count": 500}
    p.update(kw)
    return p


JOB_A = {"semilla": "https://x.com/p0", "status": "completed", "finish_reason": "finished",
         "crawler_version": "abc", "fecha": "2026-01-01T00:00:00"}
JOB_B = {**JOB_A, "fecha": "2026-01-02T00:00:00"}


def _comparar(n_rotas, umbrales=None):
    a = [_pag(i) for i in range(N)]
    b = [_pag(i, status_code=404, indexability_status="Client Error (404)") if i < n_rotas
         else _pag(i) for i in range(N)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B, umbrales=umbrales)
    return [x["regla"] for x in r["alertas"]]


def test_las_de_serie_dan_lo_mismo_que_sin_reglas():
    u = R.umbrales_de_comparacion(R.efectivas(None))
    assert _comparar(25, u) == _comparar(25) == ["rotas"]
    assert _comparar(10, u) == _comparar(10) == []   # 10 < piso de 20


def test_bajar_el_piso_hace_saltar_lo_que_antes_no():
    u = R.umbrales_de_comparacion(R.efectivas({"comparacion": {"rotas": {"min_paginas": 5}}}))
    assert _comparar(10, u) == ["rotas"]


def test_apagar_una_regla_la_quita():
    u = R.umbrales_de_comparacion(R.efectivas({"comparacion": {"rotas": {"activa": False}}}))
    assert _comparar(25, u) == []


def test_subir_el_porcentaje_la_calla():
    u = R.umbrales_de_comparacion(R.efectivas(
        {"comparacion": {"rotas": {"pct_sitio": 30, "pct_plantilla": 100}}}))
    assert _comparar(25, u) == []


# --- Reglas propias contra la BD ----------------------------------------------

@pytest.fixture
def s():
    from shared.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _cliente(s, reglas=None, **kw):
    from shared.models import Client

    c = Client(id=uuid.uuid4(), nombre=kw.pop("nombre", "Acme"),
               slug=kw.pop("slug", "acme-" + uuid.uuid4().hex[:6]),
               dominios=[], config_defecto={}, integraciones=[], reglas_alerta=reglas, **kw)
    s.add(c)
    s.flush()
    return c


def _perfil(s, cliente, nombre="semanal", rol="propio"):
    from shared.models import CrawlProfile

    p = CrawlProfile(id=uuid.uuid4(), cliente_id=cliente.id, nombre=nombre, rol=rol,
                     seeds=["https://x.com/"], config={}, activo=True)
    s.add(p)
    s.flush()
    return p


def _programar(s, cliente, perfil=None, nombre="semanal"):
    from shared.models import Programacion

    prog = Programacion(id=uuid.uuid4(), nombre=nombre, cliente_id=cliente.id,
                        profile_id=perfil.id if perfil else None,
                        seeds=["https://x.com/"], config={}, cron="0 3 * * 3", activa=True)
    s.add(prog)
    s.flush()
    return prog


def _censo(s, cliente, *, fichas_4xx=0, blog_4xx=0, indexables=50, perfil=None,
           version="v1", status="completed", finish_reason="finished", dia=2, **kw):
    """Un censo con 50 fichas y 50 entradas de blog; las primeras N de cada
    segmento en 404 y el resto 200 (las `indexables` primeras de los 200)."""
    from shared.models import Issue, Job, Url

    j = Job(id=uuid.uuid4(), name=f"censo-{dia}", status=status, finish_reason=finish_reason,
            seeds=["https://x.com/"], config={}, cliente_id=cliente.id,
            profile_id=perfil.id if perfil else None, analisis_version=version,
            created_at=datetime(2026, 10, dia, tzinfo=timezone.utc),
            completed_at=datetime(2026, 10, dia, 5, tzinfo=timezone.utc), **kw)
    s.add(j)
    s.flush()
    n_idx = 0
    for seg, rotas in (("fichas", fichas_4xx), ("blog", blog_4xx)):
        for i in range(50):
            roto = i < rotas
            idx = (not roto) and n_idx < indexables
            n_idx += int(idx)
            u = Url(job_id=j.id, url=f"https://x.com/{seg}/{i}", url_hash=f"{j.id}-{seg}-{i}",
                    is_internal=True, is_html=True, segmento=seg,
                    status_code=404 if roto else 200,
                    status_group="4xx" if roto else "2xx", indexable=idx)
            s.add(u)
            s.flush()
            if roto:
                s.add(Issue(job_id=j.id, url_id=u.id, issue_type="4xx_error", severity="error"))
    s.flush()
    return j


REGLA_FICHAS = {"id": "fichas-rotas", "nombre": "Fichas rotas", "medida": "urls_4xx",
                "segmento": "fichas", "modo": "valor", "operador": ">", "umbral": 20,
                "severidad": "critical", "avisar": True}


def test_cuatro_xx_en_fichas_dispara_en_el_caso_y_no_en_el_control(s):
    """El criterio de hecho de 5.2: «4xx > 20 en fichas»."""
    c = _cliente(s, {"propias": [REGLA_FICHAS]})
    e = R.efectivas(c.reglas_alerta)
    control = _censo(s, c, fichas_4xx=3, blog_4xx=30)   # el blog roto no es la regla
    caso = _censo(s, c, fichas_4xx=25, dia=3)
    r_control = R.evaluar_propias(s, control, e, None)
    r_caso = R.evaluar_propias(s, caso, e, None)
    assert r_control["evaluadas"] == 1 and r_control["disparadas"] == []
    (a,) = r_caso["disparadas"]
    assert (a["regla"], a["valor"], a["severidad"]) == ("cliente:fichas-rotas", 25, "critical")
    assert a["texto"].startswith("Fichas rotas: 25")


def test_una_incidencia_concreta_cuenta_paginas(s):
    c = _cliente(s)
    regla = {**REGLA_FICHAS, "id": "i", "medida": "incidencia:4xx_error", "segmento": None,
             "umbral": 40}
    e = R.efectivas({"propias": [regla]})
    assert R.evaluar_propias(s, _censo(s, c, fichas_4xx=25, blog_4xx=20), e, None)["disparadas"]
    assert not R.evaluar_propias(s, _censo(s, c, fichas_4xx=25, blog_4xx=10, dia=3),
                                 e, None)["disparadas"]


COMPARABLE = {"comparable": True, "concluyente": True}


def test_los_cambios_se_miden_frente_al_censo_de_la_comparacion(s):
    c = _cliente(s)
    antes = _censo(s, c, indexables=80)
    ahora = _censo(s, c, indexables=60, dia=3)
    e = R.efectivas(None)   # de serie: caen las indexables un 10% o mas
    r = R.evaluar_propias(s, ahora, e, {**COMPARABLE, "comparado_con": str(antes.id)})
    (a,) = [x for x in r["disparadas"] if x["id"] == "caen-indexables"]
    assert (a["antes"], a["valor"], a["medido"]) == (80, 60, -25.0)


def test_sin_referencia_comparable_los_cambios_no_se_evaluan(s):
    c = _cliente(s)
    antes = _censo(s, c, indexables=80)
    ahora = _censo(s, c, indexables=60, dia=3)
    e = R.efectivas(None)
    for comp, trozo in ((None, "no hay censo anterior"),
                        ({"comparable": False, "motivo": "otro host"}, "otro host"),
                        ({**COMPARABLE, "concluyente": False, "comparado_con": str(antes.id)},
                         "no es concluyente")):
        r = R.evaluar_propias(s, ahora, e, comp)
        assert r["disparadas"] == []
        assert {x["id"] for x in r["sin_evaluar"]} == {"caen-indexables", "suben-errores"}
        assert all(trozo in x["motivo"] for x in r["sin_evaluar"])


def test_con_otra_version_del_analisis_solo_se_comparan_las_del_rastreo(s):
    """Decision 91: el cambio de indexables o de incidencias puede ser nuestro;
    el de los codigos de estado no."""
    c = _cliente(s)
    antes = _censo(s, c, fichas_4xx=0, indexables=80, version="v1")
    ahora = _censo(s, c, fichas_4xx=30, indexables=60, version="v2", dia=3)
    e = R.efectivas({"propias": [
        {**REGLA_FICHAS, "id": "suben-4xx", "modo": "cambio", "segmento": None, "umbral": 10},
        R.PROPIAS_DE_SERIE[0]]})
    r = R.evaluar_propias(s, ahora, e, {**COMPARABLE, "comparado_con": str(antes.id)})
    assert [a["id"] for a in r["disparadas"]] == ["suben-4xx"]
    assert [x["id"] for x in r["sin_evaluar"]] == ["caen-indexables"]
    assert "versiones distintas" in r["sin_evaluar"][0]["motivo"]


def test_de_cero_a_algo_no_es_un_porcentaje(s):
    c = _cliente(s)
    antes = _censo(s, c, fichas_4xx=0)
    ahora = _censo(s, c, fichas_4xx=5, dia=3)
    regla = {**REGLA_FICHAS, "modo": "cambio_pct", "umbral": 50}
    r = R.evaluar_propias(s, ahora, R.efectivas({"propias": [regla]}),
                          {**COMPARABLE, "comparado_con": str(antes.id)})
    assert r["disparadas"] == [] and "0" in r["sin_evaluar"][0]["motivo"]


def test_un_censo_bloqueado_o_de_la_competencia_no_se_evalua(s):
    c = _cliente(s)
    e = R.efectivas({"propias": [REGLA_FICHAS]})
    muro = _censo(s, c, fichas_4xx=50, finish_reason="bloqueado_por_waf")
    assert "muro" in R.evaluar_propias(s, muro, e, None)["motivo"]
    rival = _perfil(s, c, "rival", rol="competidor")
    suyo = _censo(s, c, fichas_4xx=50, perfil=rival, dia=3)
    r = R.evaluar_propias(s, suyo, e, None, rival)
    assert r["disparadas"] == [] and "competencia" in r["motivo"]


# --- Que sale por el webhook --------------------------------------------------

CRIT = {"regla": "rotas", "severidad": "critical"}
WARN = {"regla": "pierden_contenido", "severidad": "warning"}


def test_avisar_sigue_lo_que_marque_el_cliente_y_el_silencio_lo_para_todo():
    propia = {"regla": "cliente:x", "avisar": True}
    callada = {"regla": "cliente:y", "avisar": False}
    assert R.a_avisar(None, [CRIT, WARN], [propia]) == [CRIT]
    e = R.efectivas({"comparacion": {"rotas": {"avisar": False},
                                     "pierden_contenido": {"avisar": True}}})
    assert R.a_avisar(e, [CRIT, WARN], [propia, callada]) == [WARN, propia]
    e["silenciado_hasta"] = "2026-10-20"
    assert R.a_avisar(e, [CRIT, WARN], [propia], hoy=date(2026, 10, 20)) == []
    assert R.a_avisar(e, [CRIT, WARN], [propia], hoy=date(2026, 10, 21)) == [WARN, propia]


# --- El worker, ejecutado -----------------------------------------------------

def test_el_worker_evalua_las_reglas_y_avisa_aunque_no_haya_censo_anterior(s, monkeypatch):
    import worker

    import shared.avisos
    import shared.comparacion
    import shared.database

    c = _cliente(s, {"propias": [REGLA_FICHAS]})
    job = _censo(s, c, fichas_4xx=25)
    s.commit()
    enviados = []
    monkeypatch.setattr(shared.database, "SessionLocal", lambda: s)
    monkeypatch.setattr(shared.comparacion, "comparar_con_el_anterior", lambda *a, **k: None)
    monkeypatch.setattr(shared.avisos, "avisar_de_alertas",
                        lambda jid, nombre, alertas: enviados.append(alertas))
    monkeypatch.setattr(s, "close", lambda: None)
    worker._comparar_con_el_censo_anterior(job.id)  # Postgres acepta el texto; SQLite no
    s.refresh(job)
    assert [a["id"] for a in job.alertas_cliente["disparadas"]] == ["fichas-rotas"]
    assert [[a["regla"] for a in lote] for lote in enviados] == [["cliente:fichas-rotas"]]


def test_el_worker_sin_reglas_del_cliente_solo_avisa_de_las_criticas(s, monkeypatch):
    import worker

    import shared.avisos
    import shared.comparacion
    import shared.database

    from shared.models import Job

    job = Job(id=uuid.uuid4(), name="sin cliente", status="completed", seeds=["https://x.com/"],
              config={})
    s.add(job)
    s.commit()
    enviados = []
    monkeypatch.setattr(shared.database, "SessionLocal", lambda: s)
    monkeypatch.setattr(shared.comparacion, "comparar_con_el_anterior",
                        lambda *a, **k: {"comparable": True, "alertas": [
                            {**CRIT, "paginas": 30, "de": 100, "pct": 30.0}, WARN]})
    monkeypatch.setattr(shared.avisos, "avisar_de_alertas",
                        lambda jid, nombre, alertas: enviados.append(alertas))
    monkeypatch.setattr(s, "close", lambda: None)
    worker._comparar_con_el_censo_anterior(job.id)  # Postgres acepta el texto; SQLite no
    assert [[a["regla"] for a in lote] for lote in enviados] == [["rotas"]]
    s.refresh(job)
    assert job.alertas_cliente is None


# --- Resumen semanal ----------------------------------------------------------

from shared import resumen_semanal as RS  # noqa: E402


def test_el_corte_es_el_lunes_a_las_8_en_la_zona_del_cliente():
    # Domingo 18-oct-2026 a las 23:00 en Madrid (21:00 UTC): el ultimo corte
    # es el lunes 12 a las 8:00.
    ahora = datetime(2026, 10, 18, 21, 0, tzinfo=timezone.utc)
    corte = RS.ultimo_corte(1, 8, "Europe/Madrid", ahora)
    assert (corte.date(), corte.hour, corte.isoweekday()) == (date(2026, 10, 12), 8, 1)
    # El lunes 19 a las 8:30 de Madrid ya es el del 19
    ahora = datetime(2026, 10, 19, 6, 30, tzinfo=timezone.utc)
    assert RS.ultimo_corte(1, 8, "Europe/Madrid", ahora).date() == date(2026, 10, 19)


def test_toca_dentro_de_la_gracia_y_no_despues(s):
    c = _cliente(s, zona_horaria="Europe/Madrid")
    lunes_830 = datetime(2026, 10, 19, 6, 30, tzinfo=timezone.utc)
    assert RS.toca(c, lunes_830, s) is None   # sin nada programado no hay semana que resumir
    semanal = _perfil(s, c, "semanal")
    assert RS.toca(c, lunes_830, s) is None   # un perfil a mano tampoco
    _programar(s, c, semanal)
    assert RS.toca(c, lunes_830, s) is not None
    assert RS.toca(c, lunes_830 + timedelta(hours=13), s) is None
    c.reglas_alerta = {"resumen_semanal": {"activo": False}}
    assert RS.toca(c, lunes_830, s) is None


def test_el_resumen_cuenta_la_semana_y_dice_lo_que_no_paso(s):
    c = _cliente(s, nombre="Acme")
    semanal = _perfil(s, c, "semanal")
    _programar(s, c, semanal)
    _programar(s, c, _perfil(s, c, "mensual"), nombre="mensual")   # sin rastreo esta semana
    _programar(s, c, _perfil(s, c, "rival", rol="competidor"), nombre="rival")
    job = _censo(s, c, perfil=semanal, fichas_4xx=3, dia=14)
    job.comparacion = {"comparable": True, "alertas": [
        {"regla": "rotas", "severidad": "critical", "paginas": 30, "de": 100, "pct": 30.0,
         "que_decide_google": "Sale del índice."}]}
    job.alertas_cliente = {"disparadas": [{"texto": "Fichas rotas: 25"}]}
    s.flush()
    corte = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    r = RS.generar(s, c, corte)
    nombres = [p["nombre"] for p in r["contenido"]["perfiles"]]
    assert nombres == ["mensual", "semanal"]   # la competencia no sale
    assert "mensual: no se ha rastreado esta semana." in r["contenido"]["destacado"]
    assert any("30 de 100" in t for t in r["contenido"]["destacado"])
    assert "semanal: Fichas rotas: 25" in r["contenido"]["destacado"]
    assert r["texto"].startswith("*Acme* · resumen del 12/10 al 19/10")


def test_un_resumen_por_semana_aunque_lo_intenten_dos_replicas(s):
    c = _cliente(s)
    corte = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    mandados = []
    assert RS.guardar_y_enviar(s, c, corte, enviar=lambda t, cont: mandados.append(t) or True)
    assert not RS.guardar_y_enviar(s, c, corte, enviar=lambda t, cont: mandados.append(t) or True)
    assert len(mandados) == 1
    from shared.models import ResumenSemanal

    (fila,) = s.query(ResumenSemanal).all()
    assert fila.enviado is True and fila.semana == date(2026, 10, 19)


def test_silenciado_se_guarda_pero_no_se_manda(s):
    c = _cliente(s, {"silenciado_hasta": "2026-10-31"})
    mandados = []
    assert RS.guardar_y_enviar(s, c, datetime(2026, 10, 19, 8, tzinfo=timezone.utc),
                               enviar=lambda t, cont: mandados.append(t) or True)
    assert mandados == []


def test_unas_reglas_de_comparacion_escritas_mal_a_mano_no_rompen_nada():
    e = R.efectivas({"comparacion": {"salen_del_indice": {"pct_sitio": "5", "min_paginas": 3},
                                     "rotas": ["x"]}})
    # El valor de texto invalida esa entrada entera; lo demas, de serie.
    assert e["comparacion"]["salen_del_indice"]["pct_sitio"] == 5.0
    assert e["comparacion"]["salen_del_indice"]["min_paginas"] == 20
    assert e["comparacion"]["rotas"]["activa"] is True
    R.umbrales_de_comparacion(e)   # no revienta
    assert R.efectivas({"comparacion": ["x"]})["comparacion"] == R.de_serie()["comparacion"]


def test_un_umbral_de_cero_no_se_acepta():
    with pytest.raises(R.ReglaInvalida):
        R.validar({"comparacion": {"rotas": {"pct_sitio": 0}}})


def test_en_una_lista_mandan_todas_las_reglas():
    """Decision 128: el veredicto de un QA no depende de que el cliente apagara
    una regla ruidosa para sus censos completos."""
    u = R.umbrales_de_comparacion(R.efectivas({"comparacion": {"rotas": {"activa": False}}}))
    a = [_pag(i) for i in range(N)]
    b = [_pag(i, status_code=404, indexability_status="Client Error (404)") if i < 3
         else _pag(i) for i in range(N)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B, umbral_absoluto=1, umbrales=u)
    assert [x["regla"] for x in r["alertas"]] == ["rotas"]


def test_un_resumen_no_enviado_se_reintenta(s):
    c = _cliente(s)
    corte = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    assert RS.guardar_y_enviar(s, c, corte, enviar=lambda t, cont: False)
    mandados = []
    assert not RS.guardar_y_enviar(s, c, corte, enviar=lambda t, cont: mandados.append(t) or True)
    assert len(mandados) == 1
    from shared.models import ResumenSemanal

    assert s.query(ResumenSemanal).one().enviado is True


def test_las_cifras_del_resumen_no_se_comparan_con_un_censo_truncado(s):
    c = _cliente(s)
    semanal = _perfil(s, c)
    from shared.evolucion import registrar

    antes = _censo(s, c, perfil=semanal, dia=10)
    ahora = _censo(s, c, perfil=semanal, fichas_4xx=10, dia=14)
    registrar(s, antes.id)
    registrar(s, ahora.id)
    ahora.comparacion = {"comparable": True, "concluyente": False,
                         "comparado_con": str(antes.id), "alertas": []}
    s.flush()
    r = RS.generar(s, c, datetime(2026, 10, 19, 8, tzinfo=timezone.utc))
    (p,) = r["contenido"]["perfiles"]
    assert all("cambio" not in v for v in p["ultimo"]["cifras"].values())
    ahora.comparacion = {**ahora.comparacion, "concluyente": True}
    s.flush()
    r = RS.generar(s, c, datetime(2026, 10, 19, 8, tzinfo=timezone.utc))
    assert r["contenido"]["perfiles"][0]["ultimo"]["cifras"]["urls"]["cambio"] == 0


def test_la_cabecera_usa_las_fechas_de_la_zona_del_cliente(s):
    from zoneinfo import ZoneInfo

    c = _cliente(s, nombre="Acme")
    corte = datetime(2026, 10, 19, 0, 30, tzinfo=ZoneInfo("Europe/Madrid"))
    assert RS.generar(s, c, corte)["texto"].startswith("*Acme* · resumen del 12/10 al 19/10")  # en UTC decia 11/10


def test_una_programacion_sin_perfil_es_una_serie_del_resumen(s):
    """Produccion, 18-oct-2026: ningun cliente tenia perfiles y los censos
    semanales iban por programacion. Contando solo perfiles, el resumen no
    habria salido para nadie."""
    from shared.models import Programacion

    c = _cliente(s, nombre="Lopesan")
    lunes_830 = datetime(2026, 10, 19, 6, 30, tzinfo=timezone.utc)
    assert RS.toca(c, lunes_830, s) is None
    prog = Programacion(id=uuid.uuid4(), nombre="lopesan.com (semanal)", cliente_id=c.id,
                        seeds=["https://x.com/"], config={}, cron="0 3 * * 3", activa=True)
    s.add(prog)
    s.flush()
    assert RS.toca(c, lunes_830, s) is not None
    semanal = _censo(s, c, dia=14, programacion_id=prog.id)
    _censo(s, c, dia=15)   # uno a mano: va aparte
    s.flush()
    r = RS.generar(s, c, datetime(2026, 10, 19, 8, tzinfo=timezone.utc))
    (serie,) = r["contenido"]["perfiles"]
    assert (serie["nombre"], serie["rastreos"], serie["ultimo"]["job_id"]) == (
        "lopesan.com (semanal)", 1, str(semanal.id))
    assert r["contenido"]["rastreos_sin_perfil"] == 1
    prog.activa = False
    s.flush()
    assert RS.toca(c, lunes_830, s) is None


def test_un_perfil_a_mano_solo_sale_si_se_lanzo(s):
    """Produccion, 18-oct-2026: el perfil «lista» de Lopesan (QA tras cada
    despliegue, se lanza a mano) salia cada semana como «no se ha rastreado
    esta semana», que no es un hallazgo: nadie espera que corra cada lunes."""
    c = _cliente(s, nombre="Lopesan")
    _programar(s, c, nombre="lopesan.com (semanal)")
    lista = _perfil(s, c, "lista")
    corte = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)
    r = RS.generar(s, c, corte)
    assert [p["nombre"] for p in r["contenido"]["perfiles"]] == ["lopesan.com (semanal)"]
    assert not any(t.startswith("lista:") for t in r["contenido"]["destacado"])
    job = _censo(s, c, perfil=lista, dia=15)
    job.comparacion = {"comparable": True, "alertas": [
        {"regla": "rotas", "severidad": "critical", "paginas": 3, "de": 52, "pct": 5.8,
         "que_decide_google": "Sale del índice."}]}
    s.flush()
    r = RS.generar(s, c, corte)
    fila = next(p for p in r["contenido"]["perfiles"] if p["nombre"] == "lista")
    assert (fila["rastreos"], fila["programado"]) == (1, False)
    assert any(t.startswith("lista: 3 de 52") for t in r["contenido"]["destacado"])
