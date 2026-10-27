"""Limpieza: los grupos por texto repetido (decision 198; revision de UX del
25-oct, 3.6), EJECUTADO con TestClient y SQLite.

- `lineas_repetidas` encuentra las lineas que salen en al menos la mitad de
  la muestra y cuanto del texto son.
- `/cleaning/groups` ordena por los caracteres repetidos de todo el grupo, no
  por paginas: una carpeta grande sin plantilla va detras de una pequena con
  menu y pie repetidos. Los grupos de menos de 3 paginas van sin cifra.
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

from api.cleaning_engine import lineas_repetidas  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import Base, Job, PageContent, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


MENU = "Inicio | Hoteles | Ofertas | Contacto"
PIE = "© Lopesan Hotel Group. Todos los derechos reservados."


def test_lineas_repetidas_en_la_mitad_de_la_muestra():
    r = lineas_repetidas([f"{MENU}\nUno\n{PIE}", f"{MENU}\nDos dos\n{PIE}", f"{MENU}\nTres\nOtro pie"])
    assert r["muestra"] == 3
    textos = {x["texto"]: x["paginas"] for x in r["lineas"]}
    # El pie sale en 2 de 3 (la mitad o mas): repetido. «Uno» no.
    assert textos == {MENU: 3, PIE: 2}
    # Las de mas caracteres repetidos primero: el menu (37 × 3) antes que
    # el pie (53 × 2).
    assert [x["texto"] for x in r["lineas"]] == [MENU, PIE]
    assert 0.5 < r["parte"] < 1


def test_sin_dos_paginas_con_texto_no_hay_repeticion():
    assert lineas_repetidas([]) is None
    assert lineas_repetidas(["solo una", None, "  "]) is None
    # Con dos paginas hace falta que la linea salga en las dos.
    assert lineas_repetidas(["a b c\nxyz", "a b c\nqwe"])["lineas"] == [{"texto": "a b c", "paginas": 2}]


def test_una_linea_corta_no_cuenta():
    r = lineas_repetidas(["ab\nhola mundo", "ab\nadios mundo"])
    assert r["lineas"] == [] and r["parte"] == 0


@pytest.fixture
def cliente(tmp_path):
    from api.routers import cleaning

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="j", status="completed", seeds=["https://x.com/"], config={})
    s.add(job)
    s.flush()
    n = [0]

    def pagina(ruta, md):
        n[0] += 1
        u = Url(id=n[0], job_id=job.id, url="https://x.com" + ruta, url_hash=f"h{n[0]}", path=ruta,
                is_html=True, is_internal=True, status_code=200)
        s.add(u)
        s.flush()
        # El markdown lleva la sintaxis (enlaces con su URL) que el texto no
        # tiene: lo que se mide y se propone quitar es del texto.
        s.add(PageContent(url_id=u.id, content_text=md,
                          content_markdown="\n".join(f"* [{ln}](<https://x.com/{i}>)" for i, ln in enumerate(md.split("\n"))),
                          content_length=len(md)))

    # /blog: 6 paginas sin nada repetido. /es: 3 paginas con menu y pie.
    for i in range(6):
        pagina(f"/blog/post-{i}", f"Texto propio numero {i} " * 20)
    for i in range(3):
        pagina(f"/es/hotel-{i}", f"{MENU}\nFicha del hotel {i}: " + "habitaciones con vistas al mar, " * 4 + f"\n{PIE}")
    # /de: 2 paginas, por debajo del minimo: sin cifra, al final.
    for i in range(2):
        pagina(f"/de/x-{i}", f"{MENU}\nSeite {i}\n{PIE}")
    s.commit()
    s.close()
    app = FastAPI()
    app.include_router(cleaning.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), job.id


def test_los_grupos_van_por_texto_repetido_y_no_por_paginas(cliente):
    c, job_id = cliente
    r = c.get(f"/api/jobs/{job_id}/cleaning/groups")
    assert r.status_code == 200
    grupos = r.json()
    assert [g["group_key"] for g in grupos] == ["/es/{s}", "/blog/{s}", "/de/{s}"]
    es, blog, de = grupos
    assert es["repetido_muestra"] == 3 and es["repetido_parte"] > 0.3
    assert {x["texto"] for x in es["lineas_repetidas"]} == {MENU, PIE}
    assert blog["repetido_parte"] == 0 and blog["lineas_repetidas"] == []
    # Por debajo de 3 paginas no se mide: sin cifra, no «0 %».
    assert de["repetido_parte"] is None and de["repetido_muestra"] is None
    assert es["pages"] == 3 and es["cleaned_pages"] == 0


def test_lo_que_propone_quitar_sale_del_texto_que_leen_los_analisis(cliente):
    # Las lineas propuestas son del content_text (lo que leen duplicados,
    # pocas palabras y semantico): aplicadas como reglas de linea exacta, la
    # vista previa quita del texto lo que dice la cifra, no sintaxis del
    # markdown (revision de UX del 25-oct, 3.6).
    c, job_id = cliente
    es = c.get(f"/api/jobs/{job_id}/cleaning/groups").json()[0]
    reglas = [{"type": "line_exact", "value": x["texto"]} for x in es["lineas_repetidas"]]
    r = c.post(f"/api/jobs/{job_id}/cleaning/preview",
               json={"group_key": es["group_key"], "rules": reglas, "targets": "both", "sample_size": 3})
    assert r.status_code == 200, r.text
    muestras = r.json()["samples"]
    assert len(muestras) == 3
    for m in muestras:
        assert MENU not in m["after_excerpt"] and PIE not in m["after_excerpt"]
        assert m["after_excerpt"].startswith("Ficha del hotel")
        # Lo quitado es del texto que se ensena (no se suma el markdown).
        assert m["chars_removed"] == len(m["before_excerpt"]) - len(m["after_excerpt"])
        assert 0 < m["removed_pct"] < 100
