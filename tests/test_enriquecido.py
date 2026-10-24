"""Analisis enriquecido (#40, decision 85), EJECUTADO contra SQLite y el FakeLLM.

Lo que hay que sujetar:

- la estimacion no llama a nadie y descuenta lo que ya esta en cache;
- el corte por presupuesto de la ejecucion deja `parcial` SIN pasarse, y el
  tope mensual del cliente tambien corta;
- relanzar sin cambios cuesta ~0 (la cache de #7) y no hace ni una llamada;
- cancelar entre lotes conserva lo pagado;
- reanudar tras un reinicio no repite lo ya hecho;
- el registro de modulos ordena por dependencias y las anade solas.

Ninguna llamada sale a la red: el `FakeLLM` va como transporte de httpx y
`conftest.py` hace fallar cualquier transporte real hacia OpenRouter.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("httpx")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import shared.enriquecido as enr  # noqa: E402
from shared.models import (  # noqa: E402
    Base, Client, Decision, EnrichmentRun, Heading, HtmlMeta, Job, PageContent, Url,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _motor(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/e.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def sembrar(Sesion, n=10, *, tope_cliente=None, extra_no_indexables=True):
    """Un job completado con `n` paginas indexables de contenido distinto y,
    aparte, las que el alcance por defecto tiene que dejar fuera."""
    s = Sesion()
    cliente = Client(nombre="Acme", slug="acme", dominios=["acme.com"],
                     decisiones=({"presupuesto_mensual_usd": tope_cliente}
                                 if tope_cliente is not None else None))
    s.add(cliente)
    s.flush()
    job = Job(name="censo", status="completed", seeds=["https://acme.com/"], config={},
              cliente_id=cliente.id)
    s.add(job)
    s.flush()

    def url(ruta, i, **kw):
        datos = dict(job_id=job.id, url=f"https://acme.com{ruta}", url_hash=f"{i:064x}",
                     host="acme.com", path=ruta, scheme="https", is_internal=True,
                     status_code=200, status_group="2xx", is_html=True, resource_type="html",
                     content_type="text/html", indexable=True)
        datos.update(kw)
        u = Url(**datos)
        s.add(u)
        s.flush()
        return u

    for i in range(n):
        ruta = f"/blog/post-{i}" if i % 2 else f"/tienda/p-{i}"
        u = url(ruta, i)
        s.add(HtmlMeta(url_id=u.id, title=f"Titulo {i}"))
        s.add(Heading(url_id=u.id, tag="h1", position=0, text=f"Oculto {i}", oculto=True))
        s.add(Heading(url_id=u.id, tag="h1", position=1, text=f"H1 {i}"))
        s.add(PageContent(url_id=u.id, content_text=f"Contenido unico numero {i}. " * 40))
    if extra_no_indexables:
        url("/redir", 900, status_code=301, status_group="3xx", indexable=False)
        url("/canon", 901, indexable=False)
        url("/doc.pdf", 902, is_html=False, resource_type="pdf")
        url("https://otro.com/", 903, is_internal=False)
    s.commit()
    jid, cid = job.id, cliente.id
    s.close()
    return jid, cid


@pytest.fixture()
def capa_activa(monkeypatch):
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-de-prueba")
    # Una llamada en vuelo: el exceso maximo de la reserva es el error de
    # estimacion de las llamadas EN VUELO (decision 84), y aqui se quiere
    # comprobar el caso exacto.
    monkeypatch.setenv("LLM_MAX_CONCURRENCIA", "1")
    monkeypatch.delenv("ENRICHMENT_MAX_USD_MES", raising=False)


def _run(Sesion, jid, presupuesto=1.0, alcance=None, modulos=("pagina_legal",)):
    s = Sesion()
    r = EnrichmentRun(job_id=jid, modulos=list(modulos), alcance=alcance or {},
                      presupuesto_usd=presupuesto, estado=enr.PENDIENTE)
    s.add(r)
    s.commit()
    rid = r.id
    s.close()
    return rid


def _leer(Sesion, rid):
    s = Sesion()
    r = s.get(EnrichmentRun, rid)
    s.close()
    return r


def _decisiones(Sesion, rid):
    s = Sesion()
    filas = s.query(Decision).filter(Decision.run_id == rid).all()
    s.close()
    return filas


# --- modulos -----------------------------------------------------------------

def _mod(nombre, *deps):
    return enr.Modulo(nombre=nombre, titulo=nombre, descripcion="", preguntas=("es_pagina_legal",),
                      construir_estados=lambda s, ids: [], depende_de=deps)


def test_las_dependencias_se_anaden_solas_y_van_antes():
    reg = {m.nombre: m for m in (_mod("tipo"), _mod("intencion", "tipo"),
                                 _mod("relevancia", "tipo"), _mod("embudo", "intencion"))}
    orden, anadidos = enr.resolver_modulos(["embudo", "relevancia"], reg)
    assert orden == ["tipo", "intencion", "embudo", "relevancia"]
    assert anadidos == ["tipo", "intencion"]


def test_modulo_desconocido_y_ciclo():
    reg = {m.nombre: m for m in (_mod("a", "b"), _mod("b", "a"))}
    with pytest.raises(enr.ModuloDesconocido):
        enr.resolver_modulos(["nada"], reg)
    with pytest.raises(ValueError, match="circular"):
        enr.resolver_modulos(["a"], reg)


def test_un_modulo_con_una_pregunta_sin_evaluar_va_marcado():
    # Los modulos de verdad se evaluan en #8: uno con alguna pregunta sin
    # umbral tiene que ir marcado (`ejemplo`), para que el dialogo lo diga y
    # nada suyo acabe en un informe (decision 163).
    for m in enr.REGISTRO.values():
        sin_umbral = [p.nombre for p in m.objetos_pregunta() if p.umbral is None]
        assert not sin_umbral or m.ejemplo, f"{m.nombre}: preguntas sin evaluar {sin_umbral}"
    # Subconjunto y no igualdad: cada modulo nuevo se prueba en su fichero
    # (test_tipo_pagina.py...), y varias ramas anaden modulos a la vez.
    assert {"pagina_legal", "soft_404", "pagina_de_utilidad", "canibalizacion",
            "tipo_pagina", "categoria_cliente",
            "intencion_pagina", "intencion_busquedas",
            "title_contenido", "title_generico", "schema_visible"} <= set(enr.REGISTRO)


# --- alcance y estados ---------------------------------------------------------

def test_alcance_por_defecto_html_200_indexables_internas(tmp_path):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=6)
    s = Sesion()
    assert len(enr.urls_del_alcance(s, jid, {})) == 6
    blog = enr.urls_del_alcance(s, jid, {"carpeta": "blog/"})
    assert len(blog) == 3
    assert len(enr.urls_del_alcance(s, jid, {"max_urls": 2})) == 2
    # La muestra es estable: repetirla es la misma (y por eso sale de cache).
    m1 = enr.urls_del_alcance(s, jid, {"muestra": True, "max_urls": 3})
    assert m1 == enr.urls_del_alcance(s, jid, {"muestra": True, "max_urls": 3})
    assert enr.normalizar_alcance({"muestra": True})["max_urls"] == enr.MUESTRA_DEFECTO
    # Un `%` en la carpeta es literal, no un comodin.
    assert enr.urls_del_alcance(s, jid, {"carpeta": "/%"}) == []
    s.close()


def test_el_estado_es_minimo_y_el_h1_oculto_no_cuenta(tmp_path):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=1)
    s = Sesion()
    (uid,) = enr.urls_del_alcance(s, jid, {})
    (e,) = enr.estados_titulo_h1_contenido(s, [uid])
    assert e.url_id == uid
    assert set(e.datos) == {"titulo", "h1", "contenido"}
    assert e.datos["h1"] == "H1 0"
    assert len(e.datos["contenido"]) == enr.CARACTERES_CONTENIDO
    s.close()


# --- estimacion ----------------------------------------------------------------

def _trampa_cliente(monkeypatch):
    import shared.llm

    construidos = []
    original = shared.llm.ClienteOpenRouter.__init__

    def _init(self, *a, **k):
        construidos.append(1)
        original(self, *a, **k)

    monkeypatch.setattr(shared.llm.ClienteOpenRouter, "__init__", _init)
    return construidos


def test_estimar_no_construye_ningun_cliente(tmp_path, monkeypatch, capa_activa):
    construidos = _trampa_cliente(monkeypatch)
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=8)
    s = Sesion()
    e = enr.estimar(s, s.get(Job, jid), ["pagina_legal"], {})
    s.close()
    assert construidos == []
    assert e["urls"] == 8 and e["llamadas"] == 8 and e["aciertos_cache"] == 0
    assert e["coste_estimado_usd"] > 0 and e["tokens_estimados"] > 0
    assert e["presupuesto_sugerido_usd"] >= e["coste_estimado_usd"]
    assert e["cliente"]["nombre"] == "Acme" and e["cliente"]["gastado_mes_usd"] == 0


def test_la_estimacion_descuenta_lo_que_ya_esta_en_cache(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=6)
    fake = fake_llm(coste=0.0001)
    assert enr.ejecutar(_run(Sesion, jid), sesion_factory=Sesion,
                        transport=fake.transport) == enr.COMPLETADA
    s = Sesion()
    e = enr.estimar(s, s.get(Job, jid), ["pagina_legal"], {})
    s.close()
    assert e["llamadas"] == 0 and e["aciertos_cache"] == 6 and e["coste_estimado_usd"] == 0
    assert e["cliente"]["gastado_mes_usd"] == pytest.approx(0.0006)


# --- ejecucion -----------------------------------------------------------------

def test_una_ejecucion_completa_guarda_sus_decisiones_con_run_id(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=7)
    fake = fake_llm(coste=0.0002)
    rid = _run(Sesion, jid)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport, lote=3) == "completada"
    r = _leer(Sesion, rid)
    filas = _decisiones(Sesion, rid)
    assert fake.llamadas == 7 and len(filas) == 7
    assert {f.job_id for f in filas} == {jid}
    # Coste real, de `usage.cost`, y sumado en la ejecucion.
    assert r.gastado_usd == pytest.approx(7 * 0.0002)
    assert sum(f.coste_usd for f in filas) == pytest.approx(r.gastado_usd)
    assert r.progreso["procesadas"] == 7 and r.progreso["total"] == 7
    assert r.started_at and r.finished_at and r.latido_at
    # El estado enviado es el minimo: nada de HTML.
    assert set(fake.peticiones[0]["state"]) == {"titulo", "h1", "contenido"}
    # Y el usuario que viaja a OpenRouter es el SLUG del cliente.
    assert fake.peticiones[0].get("user") == "acme"


def test_el_corte_por_presupuesto_deja_parcial_sin_pasarse(tmp_path, monkeypatch, capa_activa,
                                                          fake_llm):
    # Precio por token alto para que la reserva cubra el coste real: asi el
    # caso a comprobar es el tope, no el error de estimacion.
    monkeypatch.setenv("JEV_PRECIO_USD_MTOK", "5")
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=20)
    fake = fake_llm(coste=0.001)
    rid = _run(Sesion, jid, presupuesto=0.0045)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport, lote=4) == "parcial"
    r = _leer(Sesion, rid)
    assert r.motivo == "presupuesto_ejecucion"
    assert 0 < r.gastado_usd <= 0.0045
    filas = _decisiones(Sesion, rid)
    assert 0 < len(filas) < 20
    # Lo que no se llego a pagar NO se escribe: al relanzar se intentara.
    assert all(f.estado != "sin_decidir" for f in filas)
    assert fake.llamadas == len(filas)


def test_el_tope_mensual_del_cliente_corta(tmp_path, monkeypatch, capa_activa, fake_llm):
    monkeypatch.setenv("JEV_PRECIO_USD_MTOK", "5")
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=20, tope_cliente=0.004)
    # Gasto previo del mes en otro job del mismo cliente.
    s = Sesion()
    otro = Job(name="otro", status="completed", seeds=["https://acme.com/"], config={},
               cliente_id=s.get(Job, jid).cliente_id)
    s.add(otro)
    s.flush()
    s.add(Decision(job_id=otro.id, clave="x" * 64, pregunta="es_pagina_legal",
                   version_pregunta=1, estado="incierto", origen="modelo",
                   modelo="typesafe/jev-1.13", coste_usd=0.002))
    s.commit()
    s.close()
    fake = fake_llm(coste=0.001)
    rid = _run(Sesion, jid, presupuesto=100.0)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport, lote=5) == "parcial"
    r = _leer(Sesion, rid)
    assert r.motivo == "presupuesto_cliente"
    # 0,002 ya gastados + lo de esta ejecucion no pasa de 0,004.
    assert 0.002 + r.gastado_usd <= 0.004 + 1e-12
    assert fake.llamadas < 20


def test_relanzar_sin_cambios_cuesta_cero_y_no_llama(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=9)
    fake = fake_llm(coste=0.0003)
    enr.ejecutar(_run(Sesion, jid), sesion_factory=Sesion, transport=fake.transport)
    llamadas = fake.llamadas
    rid2 = _run(Sesion, jid)
    assert enr.ejecutar(rid2, sesion_factory=Sesion, transport=fake.transport) == "completada"
    r = _leer(Sesion, rid2)
    assert fake.llamadas == llamadas
    assert r.gastado_usd == 0
    filas = _decisiones(Sesion, rid2)
    assert len(filas) == 9 and {f.origen for f in filas} == {"cache"}
    assert r.progreso["por_modulo"]["pagina_legal"]["de_cache"] == 9


def test_cancelar_entre_lotes_conserva_lo_pagado(tmp_path, monkeypatch, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=10)
    fake = fake_llm(coste=0.0001)
    rid = _run(Sesion, jid)
    lotes = []
    original = enr.REGISTRO["pagina_legal"].construir_estados

    def construir(s, ids):
        lotes.append(len(ids))
        if len(lotes) == 2:
            # Lo que haria el PATCH .../cancel mientras corre el primer lote.
            otra = Sesion()
            otra.get(EnrichmentRun, rid).estado = enr.CANCELANDO
            otra.commit()
            otra.close()
        return original(s, ids)

    m = enr.REGISTRO["pagina_legal"]
    monkeypatch.setitem(enr.REGISTRO, "pagina_legal",
                        enr.Modulo(**{**m.__dict__, "construir_estados": construir}))
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport, lote=3) == "cancelada"
    r = _leer(Sesion, rid)
    filas = _decisiones(Sesion, rid)
    # Se cancelo antes del tercer lote: dos lotes pagados y guardados.
    assert len(filas) == 6 and fake.llamadas == 6
    assert r.gastado_usd == pytest.approx(0.0006)


def test_reanudar_no_repite_lo_hecho(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=8)
    fake = fake_llm(coste=0.0001)
    rid = _run(Sesion, jid)
    # Simula un worker que murio a mitad: cuatro URLs ya hechas en ESTA
    # ejecucion y la fila en `en_curso` con el latido viejo.
    s = Sesion()
    ids = enr.urls_del_alcance(s, jid, {})[:4]
    for uid in ids:
        s.add(Decision(job_id=jid, url_id=uid, run_id=rid, clave=f"{uid:064d}",
                       pregunta="es_pagina_legal", version_pregunta=1, estado="incierto",
                       origen="modelo", modelo="typesafe/jev-1.13", coste_usd=0.0001))
    r = s.get(EnrichmentRun, rid)
    r.estado, r.gastado_usd = enr.EN_CURSO, 0.0004
    s.commit()
    assert enr.recuperar(s, minutos=15) == [str(rid)]
    s.close()
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) == "completada"
    assert fake.llamadas == 4
    assert len(_decisiones(Sesion, rid)) == 8
    assert _leer(Sesion, rid).gastado_usd == pytest.approx(0.0008)


def test_solo_un_worker_reclama_la_ejecucion(tmp_path):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=1)
    rid = _run(Sesion, jid)
    s = Sesion()
    assert enr.reclamar(s, rid) is True
    assert enr.reclamar(s, rid) is False
    s.close()


def test_sin_capa_en_el_worker_queda_fallida_y_no_llama(tmp_path, fake_llm):
    # Sin DECISIONS_ENABLED ni clave (el conftest los borra).
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=3)
    fake = fake_llm(prohibido=True)
    rid = _run(Sesion, jid)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) == "fallida"
    r = _leer(Sesion, rid)
    assert r.motivo == "desactivado" and "OPENROUTER_API_KEY" in r.errores[0]
    assert fake.intentos_prohibidos == 0


def test_sin_credito_queda_fallida_con_lo_pagado(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=6)
    fake = fake_llm(guion=[200, 200, 402], coste=0.0001)
    rid = _run(Sesion, jid)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport, lote=2) == "fallida"
    r = _leer(Sesion, rid)
    assert r.motivo == "sin_credito"
    assert len(_decisiones(Sesion, rid)) == 2
    assert r.gastado_usd == pytest.approx(0.0002)


def test_una_cancelada_pendiente_no_se_ejecuta(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=2)
    rid = _run(Sesion, jid)
    s = Sesion()
    s.get(EnrichmentRun, rid).estado = enr.CANCELADA
    s.commit()
    s.close()
    fake = fake_llm(prohibido=True)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) is None


# --- worker ------------------------------------------------------------------

class _ColaDoble:
    def __init__(self, listas):
        self.listas = {k: list(v) for k, v in listas.items()}

    def lpop(self, clave):
        lista = self.listas.get(clave) or []
        return lista.pop(0) if lista else None


class _Futuro:
    def __init__(self):
        self.hecho = False

    def done(self):
        return self.hecho


class _Ejecutor:
    def __init__(self):
        self.enviados = []

    def submit(self, f, *a):
        self.enviados.append((f.__name__, a))
        return _Futuro()


def test_el_worker_saca_de_su_cola_sin_tocar_la_de_rastreos(monkeypatch):
    pytest.importorskip("redis")
    import worker

    cola = _ColaDoble({"enrichment:pending": ["r1", "r2"], "jobs:pending": ["j1"]})
    ejecutor, activos = _Ejecutor(), {}
    monkeypatch.setattr(worker, "MAX_CONCURRENT_ENRIQUECIDOS", 1)
    worker._quizas_enriquecer(cola, ejecutor, activos)
    assert ejecutor.enviados == [("_ejecutar_enriquecido", ("r1",))]
    # Con el hueco ocupado no saca la segunda: espera en la cola, en orden.
    worker._quizas_enriquecer(cola, ejecutor, activos)
    assert len(ejecutor.enviados) == 1 and cola.listas["enrichment:pending"] == ["r2"]
    activos["r1"].hecho = True
    worker._quizas_enriquecer(cola, ejecutor, activos)
    assert ejecutor.enviados[-1] == ("_ejecutar_enriquecido", ("r2",))
    # La de rastreos ni se ha mirado.
    assert cola.listas["jobs:pending"] == ["j1"]


def test_soft_404_solo_pregunta_por_los_candidatos(tmp_path, monkeypatch, capa_activa):
    """Decision 163: el modulo de soft 404 pregunta SOLO por lo que la version
    sin modelo dejo como candidato (decision 105), en la estimacion igual que
    al ejecutar; las afirmadas y el resto del censo no cuestan nada."""
    from shared.models import Issue, Url

    _trampa_cliente(monkeypatch)
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=6)
    s = Sesion()
    ids = sorted(uid for (uid,) in s.query(Url.id).filter(Url.job_id == jid, Url.indexable.is_(True)))
    s.add(Issue(job_id=jid, url_id=ids[0], issue_type="soft_404_candidato", severity="info", details={}))
    s.add(Issue(job_id=jid, url_id=ids[1], issue_type="soft_404_candidato", severity="info", details={}))
    s.add(Issue(job_id=jid, url_id=ids[2], issue_type="soft_404", severity="warning", details={}))
    s.get(Url, ids[3]).content_word_count = 12           # poco contenido: sospechosa
    s.get(Url, ids[2]).content_word_count = 5            # afirmada sin modelo: no se pregunta
    s.get(Url, ids[4]).content_word_count = 800
    s.commit()
    assert enr.candidatos_soft404(s, jid, ids) == [ids[0], ids[1], ids[3]]
    e = enr.estimar(s, s.get(Job, jid), ["soft_404"], {})
    assert e["por_modulo"]["soft_404"]["urls"] == 3 and e["llamadas"] == 3
    (est,) = enr.estados_soft404(s, [ids[0]])
    assert set(est.datos) == {"ruta", "palabras", "titulo", "h1", "contenido"}
    s.close()
