"""Cuanto ocupa un censo y cuanto ocupa la base (decision 96).

La cuenta se parte en dos a proposito: una funcion PURA que convierte filas en
bytes, que es donde esta el criterio, y unas consultas a Postgres que solo
sacan los datos. Asi lo que importa —que un «no se sabe» no se convierta en un
cero, que es lo que haria creer que un censo no ocupa nada— se comprueba sin
base de datos.

El camino de Postgres se destapa con `ALMACENAMIENTO_TEST_DATABASE_URL` (o la
de `PAGERANK_TEST_DATABASE_URL`, que es la que ya pone CI). Nunca apuntarlas a
`crawler_db`: aqui se crean tablas.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.almacenamiento import (
    NOTA_DE_ESPACIO,
    TABLAS_DEL_CENSO,
    TABLAS_POR_URL,
    bytes_por_fila,
    es_postgres,
    estimar_bytes,
    resumen_de_disco,
    tamano_de_jobs,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# --------------------------------------------------------------------------
# La cuenta, sin base de datos
# --------------------------------------------------------------------------
def test_los_bytes_salen_de_las_filas_de_cada_tabla():
    filas = {"urls": 1_000, "links": 500_000, "issues": 2_000}
    por_fila = {"urls": 2_000.0, "links": 120.0, "issues": 300.0}
    # 2 MB de urls + 60 MB de enlaces + 0,6 MB de incidencias.
    assert estimar_bytes(filas, por_fila) == 2_000_000 + 60_000_000 + 600_000


def test_las_tablas_hijas_se_reparten_por_url():
    """`page_content` y compania cuelgan de `url_id`, no de `job_id`.

    Contarlas por censo obligaria a unir con `urls`, que es exactamente lo que
    la decision 37 prohibe en las tablas grandes. Se reparten por URL con la
    proporcion global, y eso es la mayor parte del peso de un censo: el texto
    de la pagina.
    """
    assert estimar_bytes({"urls": 10, "links": 0}, {"urls": 100.0},
                         por_url_hijas=9_000.0) == 10 * 9_100


def test_sin_bytes_por_fila_el_tamano_es_desconocido_y_no_cero():
    """Un 0 se leeria como «este censo no ocupa nada», que es lo contrario."""
    assert estimar_bytes({"urls": 5_000, "links": 1_000_000}, {}) is None


def test_sin_los_enlaces_no_hay_cifra_aunque_se_sepa_todo_lo_demas():
    """`links` son 49 de los 57 GB de la base: sin ellos la cifra miente.

    Los dos casos en que faltan son reales: que no se llegara a contar sus
    filas dentro del presupuesto (censo grande) y que no se sepa lo que pesa
    cada una (tabla sin ANALYZE).
    """
    assert estimar_bytes({"urls": 10, "issues": 3},
                         {"urls": 1_000.0, "links": 120.0, "issues": 300.0}) is None
    assert estimar_bytes({"urls": 10, "links": 99},
                         {"urls": 1_000.0, "issues": 300.0}) is None
    # Y lo que no es imprescindible si puede faltar: `issues` es 905 MB contra
    # 49 GB, asi que no vale perder la cifra entera por eso.
    assert estimar_bytes({"urls": 10, "links": 100},
                         {"urls": 1_000.0, "links": 10.0}) == 11_000
    # Un censo con CERO enlaces si tiene tamano, aunque no se sepa lo que pesa
    # una fila de `links`: no hay ninguna. Sin esto, un censo de una sola
    # pagina se quedaba sin cifra.
    assert estimar_bytes({"urls": 10, "links": 0}, {"urls": 1_000.0}) == 10_000


# Las tablas que cuelgan de un censo, preguntadas a los modelos EJECUTANDOLOS,
# pero en otro proceso. Importar `shared.semantic_models` aqui registraria sus
# tablas de pgvector en el `MetaData` compartido, y el `create_all` de
# `test_pagerank_db.py` contra el `postgres:16-alpine` de CI —que no trae la
# extension— se caeria por culpa de este test. Mirar el fuente con una regex
# tampoco vale (decision 74): se perderia una tabla declarada de otra forma.
_PREGUNTA = """
import json
import shared.models as m, shared.semantic_models  # noqa: F401
CLAVES = {"job_id", "url_id", "analysis_id", "url_dominant_id"}
print(json.dumps(sorted(
    n for n, t in m.Base.metadata.tables.items() if CLAVES & set(t.columns.keys()))))
"""


def test_toda_tabla_que_cuelga_de_un_censo_esta_contemplada():
    """Si una tabla de censo no entra aqui, su peso es invisible en el panel.

    La primera version de este test miraba solo `shared.models` y daba por
    completa una lista a la que le faltaban CINCO tablas —entre ellas
    `semantic_pages`, con un vector de 1.024 dimensiones por pagina—, porque
    las del semantico y de Search Console se declaran en otro modulo. Pasaba
    en verde.
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    # Los modelos del semantico necesitan pgvector, que no esta en la imagen
    # del crawler. Alli este test se salta; donde cuenta es en la de la API.
    pytest.importorskip("pgvector")

    salida = subprocess.run([sys.executable, "-c", _PREGUNTA], capture_output=True,
                            text=True, cwd=Path(__file__).resolve().parent.parent)
    assert salida.returncode == 0, salida.stderr
    del_censo = set(json.loads(salida.stdout))
    assert del_censo, "no se ha leido ninguna tabla"
    assert del_censo <= set(TABLAS_DEL_CENSO), del_censo - set(TABLAS_DEL_CENSO)


# --------------------------------------------------------------------------
# Fuera de Postgres: se dice que no se sabe
# --------------------------------------------------------------------------
@pytest.fixture()
def sqlite(tmp_path):
    from shared.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_en_sqlite_el_resumen_dice_que_no_sabe_el_tamano(sqlite):
    d = resumen_de_disco(sqlite)
    assert es_postgres(sqlite) is False
    assert d["total_bytes"] is None and d["tablas"] == []
    # La nota sobre lo que un borrado libera de verdad viaja siempre: es lo
    # unico que evita leer «liberados 8 GB» como «el disco baja 8 GB».
    assert "no se encoge" in d["nota"] and d["nota"] == NOTA_DE_ESPACIO


def test_en_sqlite_se_cuentan_las_filas_aunque_no_haya_bytes(sqlite):
    import uuid

    from shared.models import Job, Link, Url

    jid = uuid.uuid4()
    sqlite.add(Job(id=jid, name="x", status="completed", seeds=["https://x.com/"], config={}))
    sqlite.flush()
    for n in range(4):
        u = Url(job_id=jid, url=f"https://x.com/{n}", url_hash=f"h{n}")
        sqlite.add(u)
        sqlite.flush()
        sqlite.add(Link(job_id=jid, from_url_id=u.id, to_url="https://x.com/", to_url_hash="h"))
    sqlite.commit()

    d = tamano_de_jobs(sqlite, [jid])[jid]
    # `custom_extraction` (decision 104) lleva job_id y se cuenta como las demas.
    assert d["filas"] == {"urls": 4, "links": 4, "issues": 0, "custom_extraction": 0}
    assert d["bytes"] is None


# --------------------------------------------------------------------------
# Postgres de verdad
# --------------------------------------------------------------------------
URL_PG = (os.getenv("ALMACENAMIENTO_TEST_DATABASE_URL")
          or os.getenv("PAGERANK_TEST_DATABASE_URL"))
pg = pytest.mark.skipif(not URL_PG, reason="sin ALMACENAMIENTO_TEST_DATABASE_URL")


@pytest.fixture()
def postgres():
    """Solo las tablas que hacen falta, nombradas una a una.

    Un `create_all` entero no vale aqui: basta con que cualquier test del
    mismo proceso haya importado `api.main` —que carga el router semantico—
    para que el `MetaData` compartido lleve las tablas de pgvector, y entonces
    esto revienta contra un Postgres sin la extension, que es el que pone CI.
    Pasa de verdad: con la suite completa de la API, este fixture fallaba en
    el montaje.
    """
    from shared.models import Base

    engine = create_engine(URL_PG)
    # Con sus dependencias: `jobs` apunta a `clients` y a `crawl_profiles`, y
    # sin ellas el CREATE se cae por la clave ajena. Se cierra el conjunto
    # siguiendo las FK en vez de escribir la lista a mano, que es lo que se
    # queda viejo en cuanto alguien anade una.
    pendientes = ["jobs", "urls", "links", "issues", "page_content"]
    nombres: set[str] = set()
    while pendientes:
        n = pendientes.pop()
        if n in nombres:
            continue
        nombres.add(n)
        pendientes += [fk.column.table.name
                       for fk in Base.metadata.tables[n].foreign_keys]
    tablas = [Base.metadata.tables[n] for n in nombres]
    # El DROP va ENTERO (y solo toca lo que existe): si otro test del mismo
    # proceso dejo creadas `html_meta` y demas, borrar solo este subconjunto
    # falla porque `urls` tiene dependientes. El CREATE si va acotado, que es
    # donde estaba el problema de pgvector.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine, tables=tablas)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


@pg
def test_en_postgres_se_mide_el_tamano_y_las_tablas_vacias_no_mienten(postgres):
    import uuid

    from shared.models import Job, PageContent, Url
    from sqlalchemy import text

    jid = uuid.uuid4()
    postgres.add(Job(id=jid, name="x", status="completed", seeds=["https://x.com/"], config={}))
    postgres.flush()
    for n in range(200):
        u = Url(job_id=jid, url=f"https://x.com/{n}", url_hash=f"h{n}")
        postgres.add(u)
        postgres.flush()
        postgres.add(PageContent(url_id=u.id, content_text="palabra " * 500))
    postgres.commit()
    # `reltuples` lo rellena ANALYZE; recien escrito vale -1 ("nunca
    # analizada"), y de ahi que `bytes_por_fila` deje fuera esas tablas en vez
    # de dividir por cero.
    postgres.execute(text("ANALYZE"))
    postgres.commit()

    por_fila = bytes_por_fila(postgres)
    assert por_fila["urls"] > 0 and por_fila["page_content"] > 0

    medida = tamano_de_jobs(postgres, [jid])[jid]
    assert medida["filas"]["urls"] == 200
    # El texto de las paginas son unos 4 kB por URL, asi que el censo tiene
    # que pesar bastante mas que sus filas de `urls` a secas.
    assert medida["bytes"] > 200 * por_fila["urls"]

    d = resumen_de_disco(postgres)
    assert d["total_bytes"] > 0 and d["censo_bytes"] > 0
    assert all(t["bytes"] > 0 for t in d["tablas"])
    # De mas a menos: el panel se lee de arriba abajo y la primera fila tiene
    # que ser la que hay que mirar.
    assert [t["bytes"] for t in d["tablas"]] == sorted(
        (t["bytes"] for t in d["tablas"]), reverse=True)


# --------------------------------------------------------------------------
# Censos archivados en Parquet (decision 95)
# --------------------------------------------------------------------------
def _censo(s, jid, *, enlaces=4, archivado=None):
    from shared.models import Job, Link, Url

    s.add(Job(id=jid, name="x", status="completed", seeds=["https://x.com/"], config={},
              enlaces_archivados=archivado))
    s.flush()
    for n in range(enlaces):
        u = Url(job_id=jid, url=f"https://x.com/{n}", url_hash=f"{jid}{n}")
        s.add(u)
        s.flush()
        if not archivado:
            s.add(Link(job_id=jid, from_url_id=u.id, to_url="https://x.com/",
                       to_url_hash="h", repeticiones=5))
    s.commit()


def test_las_filas_de_links_son_filas_y_no_apariciones(sqlite):
    """Decision 94: lo que ocupa es la fila, aunque agrupe cinco apariciones."""
    import uuid

    jid = uuid.uuid4()
    _censo(sqlite, jid)
    assert tamano_de_jobs(sqlite, [jid])[jid]["filas"]["links"] == 4


def test_un_censo_archivado_tiene_tamano_aunque_no_quede_presupuesto(sqlite, tmp_path,
                                                                     monkeypatch):
    """Sin filas en `links` se SABE que son cero: no puede quedarse «sin medir»
    por no haber llegado a contarlas, y lo que ocupa es su Parquet, medido."""
    import uuid

    from shared import archivo_enlaces

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    archivado, vivo = uuid.uuid4(), uuid.uuid4()
    _censo(sqlite, archivado, archivado={"filas": 4, "bytes": 999})
    _censo(sqlite, vivo)
    fichero = archivo_enlaces.ruta(archivado)
    fichero.parent.mkdir(parents=True)
    fichero.write_bytes(b"x" * 1234)

    medidas = tamano_de_jobs(sqlite, [vivo, archivado], presupuesto_s=0)
    assert medidas[archivado]["filas"]["links"] == 0
    # Del fichero, no de lo anotado al archivar: es lo que se libera.
    assert medidas[archivado]["archivado"] is True
    assert medidas[archivado]["archivo_bytes"] == 1234
    # El vivo, sin presupuesto, se queda sin contar sus enlaces: «no se sabe».
    assert "links" not in medidas[vivo]["filas"]
    assert medidas[vivo]["archivado"] is False and medidas[vivo]["archivo_bytes"] == 0

    # Sin el fichero (la API sin el volumen), lo anotado al archivar.
    fichero.unlink()
    assert tamano_de_jobs(sqlite, [archivado])[archivado]["archivo_bytes"] == 999


def test_bytes_en_disco_no_inventa_un_cero(tmp_path, monkeypatch):
    import uuid

    from shared import archivo_enlaces

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    assert archivo_enlaces.bytes_en_disco(uuid.uuid4(), None) is None
    assert archivo_enlaces.bytes_en_disco(uuid.uuid4(), {"fichero": "x"}) is None


def test_el_resumen_cuenta_los_parquet_fuera_de_la_base(sqlite, tmp_path, monkeypatch):
    from shared import archivo_enlaces

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    d = resumen_de_disco(sqlite)
    assert d["archivo_bytes"] == 0 and d["archivo_ficheros"] == 0
    archivo_enlaces.directorio().mkdir(parents=True)
    (archivo_enlaces.directorio() / "a.parquet").write_bytes(b"x" * 10)
    (archivo_enlaces.directorio() / "b.parquet").write_bytes(b"x" * 20)
    # Un temporal a medio escribir no es un archivo.
    (archivo_enlaces.directorio() / "c.parquet.tmp").write_bytes(b"x" * 99)
    d = resumen_de_disco(sqlite)
    assert d["archivo_bytes"] == 30 and d["archivo_ficheros"] == 2
