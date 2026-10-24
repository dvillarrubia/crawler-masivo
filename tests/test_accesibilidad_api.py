"""`GET /api/jobs/{id}/accesibilidad` EJECUTADO con TestClient (decision 174).

Lo mismo que los avisos (`shared.accesibilidad.filas_evaluadas`), por regla y
de mas a menos grave; las incompletas aparte y sin afirmar; el coste; y un job
sin capturas (apagado o anterior) que no dice «sin fallos».
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.accesibilidad import resumir  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import Base, CapturaAlternativa, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


CON, SIN = uuid.uuid4(), uuid.uuid4()


def _regla(id_, impacto, n, tags=("wcag2a", "wcag111")):
    return {"id": id_, "impact": impacto, "tags": list(tags), "help": f"ayuda {id_}",
            "helpUrl": f"https://dequeuniversity.com/rules/axe/4.14/{id_}", "n": n,
            "nodes": [{"target": ["#x"], "html": "<x>"}]}


@pytest.fixture()
def api(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    with Sesion() as s:
        s.add(Job(id=CON, name="con", status="completed", seeds=["https://x.com/"],
                  config={"crawl_behavior": {"axe_muestra": 20, "axe_por_plantilla": 2}}))
        s.add(Job(id=SIN, name="sin", status="completed", seeds=["https://x.com/"], config={}))
        s.flush()

        def pagina(ruta, status=200):
            u = Url(job_id=CON, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
                    is_html=True, status_code=status)
            s.add(u)
            s.flush()
            return u

        def axe(u, plantilla, viol=(), inc=(), estado="evaluada", detalle=None):
            s.add(CapturaAlternativa(
                job_id=CON, url_id=u.id, perfil="axe", modo="render", estado=estado,
                plantilla=plantilla, detalle=detalle or {"ms_axe": 700, "ms_total": 2500},
                medidas=resumir({"version": "4.14.0", "violations": list(viol),
                                 "incomplete": list(inc)}) if estado == "evaluada" else None))

        axe(pagina("/f/a"), "/f/:slug", [_regla("label", "critical", 2), _regla("region", "moderate", 9)],
            [_regla("color-contrast", "serious", 3, ("wcag2aa", "wcag143"))])
        axe(pagina("/f/b"), "/f/:slug", [_regla("label", "critical", 1)])
        axe(pagina("/"), "/ (home)", [_regla("link-name", "serious", 4, ("wcag2a", "wcag244"))],
            detalle={"ms_axe": 1500, "ms_total": 4500})
        axe(pagina("/roto", status=404), "/:slug", [_regla("label", "critical", 1)])
        axe(pagina("/muro"), "/:slug", estado="no_evaluada", detalle={"motivo": "respuesta_no_valida"})
        # Otro perfil (paridad movil) no cuenta.
        s.add(CapturaAlternativa(job_id=CON, url_id=pagina("/m").id, perfil="movil", estado="comparada"))
        s.commit()
    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        with Sesion() as s:
            yield s

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app)


def test_reglas_de_mas_a_menos_grave_con_sus_plantillas(api):
    r = api.get(f"/api/jobs/{CON}/accesibilidad")
    assert r.status_code == 200
    d = r.json()
    assert d["pedida"] is True and d["muestra"] == 20 and d["axe"] == "4.14.0"
    # La de la pagina que ya da 404 no cuenta (como en los avisos); la del
    # muro, no evaluada con su motivo.
    assert d["paginas"] == {"evaluadas": 3, "no_evaluadas": 2, "motivos": {
        "respuesta_no_valida": 1, "pagina_del_censo_no_valida": 1}}
    assert d["plantillas"] == 2
    assert [x["regla"] for x in d["reglas"]] == ["label", "link-name", "region"]
    label = d["reglas"][0]
    assert label["impacto"] == "critical" and label["paginas"] == 2 and label["nodos"] == 3
    assert label["plantillas"] == [{"plantilla": "/f/:slug", "paginas": 2, "de": 2}]
    assert label["criterios"] == ["1.1.1"] and label["nivel"] == "A"
    assert [x["regla"] for x in d["incompletas"]] == ["color-contrast"]
    assert d["coste"]["ms_axe_mediana"] == 700 and d["coste"]["ms_axe_max"] == 1500
    assert d["coste"]["s_total"] == 9.5


def test_sin_capturas_no_dice_sin_fallos(api):
    d = api.get(f"/api/jobs/{SIN}/accesibilidad").json()
    assert d["pedida"] is False and d["paginas"]["evaluadas"] == 0
    assert d["reglas"] == [] and d["coste"] is None


def test_job_inexistente_404(api):
    assert api.get(f"/api/jobs/{uuid.uuid4()}/accesibilidad").status_code == 404
