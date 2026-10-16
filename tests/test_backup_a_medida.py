"""El backup lleva la extraccion a medida y la importacion la devuelve con los
`url_id` del censo NUEVO (decision 104). Ejecutado contra SQLite.

Lo que se sujeta: que `custom_extraction` viaja en el ZIP, que al importar
cada fila apunta a la URL importada (no a la del censo de origen), que
importar dos veces el mismo ZIP no choca con `uq_cext_url_nombre` (son dos
censos distintos), y que un ZIP sin la tabla —uno anterior a esta
funcionalidad— se importa igual.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, CustomExtraction, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    import shared.database
    from api import backup

    engine = create_engine(f"sqlite:///{tmp_path}/b.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"],
              config={"custom_extraction": {"precio": {"tipo": "css", "expresion": ".p"}},
                      "custom_search": {"gtm": {"patron": "GTM-"}}})
    s.add(job)
    s.flush()
    for ruta, precio in (("/p/1", "19,95"), ("/p/2", None)):
        u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=f"h{ruta}", host="x.com",
                path=ruta, is_internal=True, is_html=True, status_code=200, status_group="2xx")
        s.add(u)
        s.flush()
        s.add(CustomExtraction(job_id=job.id, url_id=u.id, nombre="precio", clase="extractor",
                               estado="ok" if precio else "sin_coincidencia",
                               n=1 if precio else 0, valor=precio, valor_clave=precio,
                               valor_num=19.95 if precio else None, truncado=False,
                               render=False))
        s.add(CustomExtraction(job_id=job.id, url_id=u.id, nombre="gtm", clase="busqueda",
                               estado="ok", n=2, valor="...GTM-AB12...",
                               valor_clave="...GTM-AB12...", valor_num=2.0, truncado=False,
                               render=False))
    s.commit()
    yield Sesion, job.id
    s.close()
    engine.dispose()


def _filas(Sesion, jid) -> dict[tuple[str, str], tuple]:
    with Sesion() as s:
        return {(u.path, c.nombre): (c.estado, c.valor, c.valor_num, c.n)
                for c, u in (s.query(CustomExtraction, Url)
                             .join(Url, Url.id == CustomExtraction.url_id)
                             .filter(CustomExtraction.job_id == jid))
                if u.job_id == jid}


def test_ida_y_vuelta_reasigna_las_urls_y_se_puede_importar_dos_veces(entorno):
    from api import backup

    Sesion, jid = entorno
    datos = b"".join(backup.stream_backup_zip(jid))
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert "custom_extraction.jsonl" in z.namelist()
        assert len(z.read("custom_extraction.jsonl").splitlines()) == 4

    origen = _filas(Sesion, jid)
    importados = []
    for _ in range(2):
        with Sesion() as s:
            r = backup.import_backup_zip(io.BytesIO(datos), False, s)
            nuevo = uuid.UUID(str(r["new_job_id"]))
            assert nuevo != jid
            importados.append(nuevo)
        assert _filas(Sesion, nuevo) == origen
    assert importados[0] != importados[1]
    # El censo original no se ha tocado
    assert _filas(Sesion, jid) == origen


def test_un_zip_sin_la_tabla_se_importa_igual(entorno):
    """Un backup anterior a la extraccion a medida no trae el fichero."""
    from api import backup

    Sesion, jid = entorno
    datos = b"".join(backup.stream_backup_zip(jid))
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(datos)) as z, zipfile.ZipFile(buf, "w") as w:
        for nombre in z.namelist():
            if nombre != "custom_extraction.jsonl":
                w.writestr(nombre, z.read(nombre))
    with Sesion() as s:
        r = backup.import_backup_zip(io.BytesIO(buf.getvalue()), False, s)
    nuevo = uuid.UUID(str(r["new_job_id"]))
    assert _filas(Sesion, nuevo) == {}
    with Sesion() as s:
        assert s.query(Url).filter(Url.job_id == nuevo).count() == 2
    assert json.loads(json.dumps(r, default=str))  # la respuesta es serializable
