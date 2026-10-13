"""Nada llama a un modelo de pago por su cuenta (#7, decision 84).

Con este PR la capa de decisiones existe pero NADIE la dispara: el disparo es
el boton de #40 (y los modulos de rastreo #10/#14/#20, que se activaran por
job). Se comprueba en el peor caso —`DECISIONS_ENABLED=1` y una clave
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
MODULOS_DE_LA_CAPA = {"shared.llm", "shared.decisiones"}


def _importa_la_capa(fichero: Path) -> bool:
    arbol = ast.parse(fichero.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            if any(a.name in MODULOS_DE_LA_CAPA for a in nodo.names):
                return True
        elif isinstance(nodo, ast.ImportFrom):
            mod = nodo.module or ""
            if mod in MODULOS_DE_LA_CAPA:
                return True
            if mod == "shared" and any(a.name in ("llm", "decisiones") for a in nodo.names):
                return True
    return False


def test_ni_el_crawler_ni_el_analisis_ni_los_scripts_importan_la_capa():
    culpables = [str(f.relative_to(RAIZ))
                 for carpeta in ("crawler", "analysis", "scripts", "api")
                 for f in (RAIZ / carpeta).rglob("*.py") if _importa_la_capa(f)]
    assert culpables == [], (
        "La capa de decisiones solo se dispara desde #40 o desde un modulo "
        f"activado por job; la importan: {culpables}")


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
