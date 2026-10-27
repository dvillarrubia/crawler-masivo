"""Nada llama a un modelo de pago por su cuenta (#7, decision 84; #40, decision 85).

El unico disparo es el boton de #40: `POST /api/jobs/{id}/enrichment` (con
token) crea una ejecucion, la encola en `enrichment:pending` y el worker la
corre. Por eso solo dos sitios fuera de `shared/` pueden llegar a la capa, y
solo a traves de `shared.enriquecido`: el router de enriquecido y, en el
worker, las dos funciones que atienden esa cola (los modulos de rastreo
#10/#14/#20 se activaran por job cuando existan). Se comprueba en el peor caso —`DECISIONS_ENABLED=1` y una clave
puesta— EJECUTANDO un analisis completo y un rastreo real, con una trampa que
anota cualquier cliente de OpenRouter que se construya y cualquier intento de
salir hacia el. Ademas, una comprobacion de forma: ningun modulo del crawler,
del analisis ni de los scripts importa la capa (decision 74: mirar el fuente
acompaña a ejecutar, no lo sustituye).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
HARNESS = RAIZ / "tests" / "spider_harness.py"
MODULOS_DE_LA_CAPA = {"shared.llm", "shared.decisiones", "shared.enriquecido"}
# Quien puede importar `shared.enriquecido` (y NADA mas de la capa), y desde
# que funcion. None = desde cualquier sitio del fichero.
PERMITIDOS = {
    "api/routers/enriquecido.py": None,
    "crawler/worker.py": {"_ejecutar_enriquecido", "_recuperar_enriquecidos"},
}
# La UNICA excepcion directa a la capa (decision 159): pasar el juego de
# evaluacion de #8 por Jev, a mano, con --confirmar, clave y ENRICHMENT_TOKEN.
# Por fichero y funcion: cualquier otro import del mismo script sigue fallando.
PERMITIDOS_DIRECTOS = {
    ("scripts/evaluar_decisiones.py", "responder"): {"shared.decisiones"},
    # El registro de modulos, para construir los estados del juego: no llama a nadie.
    ("scripts/evaluar_decisiones.py", "modulo_de"): {"shared.enriquecido"},
}


def _imports_de_la_capa(fichero: Path) -> list[tuple[str, str | None]]:
    """(modulo, funcion que lo importa) por cada import de la capa."""
    arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    salida = []

    def visitar(nodo, funcion):
        for hijo in ast.iter_child_nodes(nodo):
            f = hijo.name if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef)) else funcion
            if isinstance(hijo, ast.Import):
                salida.extend((a.name, f) for a in hijo.names if a.name in MODULOS_DE_LA_CAPA)
            elif isinstance(hijo, ast.ImportFrom):
                mod = hijo.module or ""
                if mod in MODULOS_DE_LA_CAPA:
                    salida.append((mod, f))
                elif mod == "shared":
                    salida.extend((f"shared.{a.name}", f) for a in hijo.names
                                  if f"shared.{a.name}" in MODULOS_DE_LA_CAPA)
            visitar(hijo, f)

    visitar(arbol, None)
    return salida


def test_ni_el_crawler_ni_el_analisis_ni_los_scripts_importan_la_capa():
    culpables = []
    for carpeta in ("crawler", "analysis", "scripts", "api"):
        for f in (RAIZ / carpeta).rglob("*.py"):
            ruta = str(f.relative_to(RAIZ))
            for modulo, funcion in _imports_de_la_capa(f):
                permitido = (ruta in PERMITIDOS and modulo == "shared.enriquecido" and (
                    PERMITIDOS[ruta] is None or funcion in PERMITIDOS[ruta])) or (
                    modulo in PERMITIDOS_DIRECTOS.get((ruta, funcion), ()))
                if not permitido:
                    culpables.append(f"{ruta}:{funcion or '<modulo>'} -> {modulo}")
    assert culpables == [], (
        "La capa de decisiones solo se dispara desde #40 (via shared.enriquecido) "
        f"o desde un modulo activado por job; la importan: {culpables}")


def test_el_worker_no_carga_la_capa_al_arrancar():
    """Importar el worker (lo que pasa en cada arranque) no trae la capa:
    solo la carga quien atiende la cola de enriquecido."""
    codigo = ("import sys, worker; "
              "print(sorted(m for m in sys.modules if m in "
              f"{sorted(MODULOS_DE_LA_CAPA)!r}))")
    proc = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                          timeout=60, cwd=str(RAIZ / "crawler"),
                          env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


class _ColaQueApunta:
    def __init__(self):
        self.claves = []

    def rpush(self, clave, valor):
        self.claves.append(clave)


def test_un_rastreo_programado_no_lanza_el_enriquecido(monkeypatch, tmp_path):
    """Un perfil o una programacion NO lanzan el analisis enriquecido (#40):
    lo que se encola es el rastreo, en su cola, y no se crea ninguna
    ejecucion. La opcion por perfil de repetirlo esta pendiente y, cuando
    llegue, ira desactivada por defecto."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, EnrichmentRun, Programacion

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    import worker

    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-trampa")
    engine = create_engine(f"sqlite:///{tmp_path}/p.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr("shared.database.SessionLocal", Sesion, raising=False)
    s = Sesion()
    s.add(Programacion(id=uuid.uuid4(), nombre="semanal", client_id="acme",
                       seeds=["https://acme.com/"], config={}, cron="0 3 * * 1",
                       zona_horaria="Europe/Madrid", activa=True,
                       proxima_ejecucion=datetime.now(timezone.utc) - timedelta(minutes=1)))
    s.commit()
    cola = _ColaQueApunta()
    worker._lanzar_programados(cola)
    assert cola.claves == ["jobs:programados"]
    assert s.query(EnrichmentRun).count() == 0
    s.close()


URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.mark.parametrize("motor", [
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.skipif(
        not URL_PG, reason="sin PAGERANK_TEST_DATABASE_URL")),
])
def test_un_analisis_completo_no_llama_a_nadie(monkeypatch, tmp_path, motor):
    """En SQLite con un job vacio (el PageRank es SQL de Postgres y no corre
    ahi, igual que en `test_comparacion`); en Postgres, con paginas, enlaces
    y contenido, que es donde `run_all` recorre todos sus chequeos."""
    pytest.importorskip("numpy")
    pytest.importorskip("httpx")
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    import shared.llm
    from shared.models import Base, Decision, HtmlMeta, Job, Link, PageContent, Url

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-trampa")
    construidos = []
    original = shared.llm.ClienteOpenRouter.__init__

    def _init(self, *a, **k):
        construidos.append(1)
        original(self, *a, **k)

    monkeypatch.setattr(shared.llm.ClienteOpenRouter, "__init__", _init)

    from analysis.analyzer import SEOAnalyzer

    engine = create_engine(URL_PG if motor == "postgres" else f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    jid = uuid.uuid4()
    s.add(Job(id=jid, name="trampa llm", status="analyzing", seeds=["https://x.com/"],
              config={}))
    s.flush()
    if motor == "postgres":
        urls = []
        for i, ruta in enumerate(["/", "/aviso-legal", "/producto"]):
            u = Url(job_id=jid, url=f"https://x.com{ruta}", url_hash=f"{jid}-{i}",
                    host="x.com", path=ruta, scheme="https", is_internal=True,
                    status_code=200, status_group="2xx", is_html=True,
                    resource_type="html", content_type="text/html", word_count=300,
                    crawl_depth=0 if ruta == "/" else 1)
            s.add(u)
            s.flush()
            urls.append(u)
            s.add(HtmlMeta(url_id=u.id, title=f"Pagina {ruta}", meta_description="d" * 80))
            s.add(PageContent(url_id=u.id, content_text=f"Texto de {ruta} " * 50))
        for u in urls[1:]:
            s.add(Link(job_id=jid, from_url_id=urls[0].id, to_url=u.url,
                       to_url_hash=hashlib.sha256(u.url.encode()).hexdigest(), anchor_text="x",
                       is_internal=True, follow=True, link_position="content"))
    s.commit()
    try:
        SEOAnalyzer(s, jid).run_all()

        assert construidos == []
        s.expire_all()
        assert s.get(Job, jid).decisiones_resumen is None
        assert s.query(Decision).filter(Decision.job_id == jid).count() == 0
    finally:
        if motor == "postgres":
            s.rollback()
            s.query(Link).filter(Link.job_id == jid).delete()
            s.query(Url).filter(Url.job_id == jid).delete()
            s.query(Job).filter(Job.id == jid).delete()
            s.commit()
        s.close()
        engine.dispose()


def test_un_rastreo_real_no_llama_a_nadie():
    pytest.importorskip("scrapy")
    entrada = {"seeds": ["http://localhost:{port}/"], "config": {"max_depth": 2},
               "trampa_llm": True}
    proc = subprocess.run([sys.executable, str(HARNESS), json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    assert sum(1 for i in items if i["_tipo"] == "PageItem") > 3  # rastreo de verdad
    trampa = json.loads(proc.stdout.split("@@LLM@@", 1)[1].split("@@PETICIONES@@", 1)[0])
    assert trampa == {"clientes": 0, "red": []}
