"""Pestaña Enlaces con filtros en el servidor (decision 195; revision de UX del
25-oct, 2.8), EJECUTANDO los endpoints con TestClient contra una base de
verdad (SQLite; Postgres con PAGERANK_TEST_DATABASE_URL) y contra el Parquet de
un censo archivado:

- cada filtro (destino que contiene y exacto, destino por hash, origen, ancla
  o alt, interno/externo, follow, posicion, estado y codigo del destino)
  devuelve lo que debe, y LO MISMO con el censo vivo en `links` que archivado
  en Parquet, pagina a pagina, con lotes y grupos de filas pequenos para que
  las paginas y los destinos crucen de un lote a otro;
- el estado del destino no afirma de mas: un 403 de un tercero es «no
  concluyente», un destino que nadie comprobo es «sin dato», y solo el
  veredicto `roto`/`redirige_a_error` de la sonda es roto;
- por defecto salen primero los enlaces a destinos rotos, no por hash;
- la vista por destino cuenta paginas distintas, enlaces y apariciones
  (SUM(repeticiones), decision 94) con sus anclas mas usadas, y «quien la
  enlaza» devuelve exactamente esos enlaces;
- el resumen cuenta los destinos externos aunque el censo no tenga URLs
  externas (V25: la barra del Explorador decia «Externas 0»);
- los enlaces de otro censo no se cuelan, y un censo archivado sin fichero
  dice que no puede listar en vez de devolver cero.

Las URLs imitan las de Lopesan (hoteles, ofertas, enlaces a redes y a
booking en el pie).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("pyarrow")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared import archivo_enlaces  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import Base, Job, Link, RespuestaSondeada, Url  # noqa: E402

URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")
MOTORES = ["sqlite"] + (["postgres"] if URL_PG else [])

H = "https://www.lopesan.com"
_HACE_UN_MES = datetime.now(timezone.utc) - timedelta(days=30)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# ruta -> codigo en el censo (None = no se pudo pedir: bloqueada por robots)
PAGINAS = {
    "/es/": 200,
    "/es/hoteles/": 200,
    "/es/hoteles/abora-catarina": 200,
    "/es/hoteles/abora-buenaventura": 301,
    "/es/ofertas/verano-2024": 404,
    "/es/reservas/": 503,
    "/es/privado/": None,
}
# Destinos externos: (url, veredicto de la sonda, codigo) o sin sonda
EXTERNOS = {
    "fb": ("https://www.facebook.com/lopesan", "no_verificable", 403),
    "booking": ("https://www.booking.com/hotel/es/abora.html", "ok", 200),
    "viejo": ("https://blog.lopesan-viejo.com/post", "roto", 404),
    "corto": ("https://bit.ly/lopesan", "ok", 301),
    "sinsonda": ("https://www.tripadvisor.es/lopesan", None, None),
}


def _h(clave: str) -> str:
    return "h-" + clave


@pytest.fixture(params=MOTORES)
def entorno(request, tmp_path, monkeypatch):
    from api import enlaces as E
    from api.routers import results

    # Lotes y grupos de filas pequenos: paginas y destinos cruzan de un lote
    # (y de un grupo del fichero) a otro.
    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    monkeypatch.setattr(E, "LOTE", 3)
    monkeypatch.setattr(archivo_enlaces, "_FILAS_POR_GRUPO", 4)
    E.vaciar_cache()

    engine = (create_engine(URL_PG) if request.param == "postgres"
              else create_engine(f"sqlite:///{tmp_path}/e.db"))
    # Solo las tablas de la prueba: si otro test registro los modelos
    # semanticos, create_all pediria el tipo `vector` de pgvector.
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[t] for t in (
        "clients", "crawl_profiles", "jobs", "urls", "links", "respuestas_sondeadas")])
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=[H + "/es/"],
              config={}, started_at=_HACE_UN_MES, completed_at=_HACE_UN_MES)
    otro = Job(id=uuid.uuid4(), name="otro censo", status="completed", seeds=[H + "/es/"],
               config={}, started_at=_HACE_UN_MES, completed_at=_HACE_UN_MES)
    s.add_all([job, otro])
    s.flush()
    sufijo = job.id.hex[:8]
    urls = {}
    for ruta, cod in PAGINAS.items():
        u = Url(job_id=job.id, url=H + ruta, url_hash=_h(ruta) + sufijo, host="www.lopesan.com",
                is_internal=True, is_html=True, status_code=cod)
        s.add(u)
        urls[ruta] = u
    s.flush()

    def enlace(origen, destino_url, destino_hash, **kw):
        datos = dict(job_id=job.id, from_url_id=urls[origen].id, to_url=destino_url,
                     to_url_hash=destino_hash, is_internal=True, follow=True,
                     link_type="hyperlink", link_position="content", repeticiones=1)
        datos.update(kw)
        s.add(Link(**datos))

    def interno(origen, ruta, **kw):
        enlace(origen, H + ruta, _h(ruta) + sufijo, **kw)

    # Menu en las cuatro paginas que dan 200 o 301: inicio y hoteles, dos veces
    # cada uno (escritorio y movil, decision 94).
    con_menu = ["/es/", "/es/hoteles/", "/es/hoteles/abora-catarina", "/es/hoteles/abora-buenaventura"]
    for o in con_menu:
        interno(o, "/es/", anchor_text="Inicio", link_position="nav", repeticiones=2)
        interno(o, "/es/hoteles/", anchor_text="Hoteles", link_position="nav", repeticiones=2)
    # Cuerpo
    interno("/es/", "/es/hoteles/abora-catarina", anchor_text="Abora Catarina")
    interno("/es/hoteles/", "/es/hoteles/abora-catarina", anchor_text="Ver hotel")
    interno("/es/hoteles/", "/es/hoteles/abora-buenaventura", anchor_text="Abora Buenaventura")
    interno("/es/", "/es/ofertas/verano-2024", anchor_text="Ofertas de verano")
    interno("/es/hoteles/abora-catarina", "/es/ofertas/verano-2024", anchor_text=None,
            alt_text="Banner verano", link_type="image")
    interno("/es/hoteles/abora-catarina", "/es/reservas/", anchor_text="Reservar",
            rel="nofollow", follow=False)
    interno("/es/hoteles/", "/es/privado/", anchor_text="Zona privada", link_position="header")
    # Un interno que no esta en el censo (excluido por la configuracion)
    enlace("/es/", H + "/es/buscar?q=hotel", "h-buscar" + sufijo, anchor_text="Buscar",
           link_position="header")
    # Pie: externos, en dos paginas
    for o in ("/es/", "/es/hoteles/"):
        for k, (url, _v, _c) in EXTERNOS.items():
            enlace(o, url, _h(k) + sufijo, anchor_text=k.capitalize(), is_internal=False,
                   link_position="footer", target="_blank")
    # Sondas de los externos (decision 123)
    for k, (url, ver, cod) in EXTERNOS.items():
        if ver:
            s.add(RespuestaSondeada(job_id=job.id, url_hash=_h(k) + sufijo, url=url,
                                    host=url.split("/")[2], uso="enlace", interno=False,
                                    metodo="GET", status_code=cod, veredicto=ver))
    # El otro censo: mismas rutas, enlaces que casarian con todos los filtros
    u_otro = Url(job_id=otro.id, url=H + "/es/", url_hash="otro-" + otro.id.hex[:8],
                 is_internal=True, status_code=200)
    s.add(u_otro)
    s.flush()
    for k, (url, _v, _c) in EXTERNOS.items():
        s.add(Link(job_id=otro.id, from_url_id=u_otro.id, to_url=url,
                   to_url_hash=_h(k) + sufijo, anchor_text=k, is_internal=False,
                   follow=False, link_position="footer", link_type="hyperlink"))
    s.add(Link(job_id=otro.id, from_url_id=u_otro.id, to_url=H + "/es/ofertas/verano-2024",
               to_url_hash=_h("/es/ofertas/verano-2024") + sufijo, anchor_text="Ofertas de verano",
               is_internal=True, follow=True, link_position="content", link_type="hyperlink"))
    s.commit()
    hashes = {**{r: _h(r) + sufijo for r in PAGINAS}, **{k: _h(k) + sufijo for k in EXTERNOS},
              "buscar": "h-buscar" + sufijo}
    s.close()

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    yield TestClient(app), job.id, Sesion, hashes
    E.vaciar_cache()


def _archivar(Sesion, jid):
    with Sesion() as s:
        archivo_enlaces.archivar(s, jid)


def _todas(api, jid, ruta, qs="", page_size=4):
    """Todas las paginas de un listado: (items, respuesta de la primera)."""
    items, primera, p = [], None, 1
    while True:
        r = api.get(f"/api/jobs/{jid}/{ruta}?page={p}&page_size={page_size}&{qs}")
        assert r.status_code == 200, r.text
        d = r.json()
        primera = primera or d
        items.extend(d["items"])
        if p >= d["pages"]:
            return items, primera
        p += 1


CONSULTAS = [
    "",
    "orden=destino",
    "estado=roto",
    "estado=redireccion",
    "estado=no_concluyente",
    "estado=sin_dato",
    "estado=ok",
    "tipo=externo",
    "tipo=interno&posicion=nav",
    "follow=no",
    "posicion=footer&estado=ok",
    "posicion=sin",
    "ancla=verano",
    "origen=abora-catarina",
    "destino=hoteles",
    "destino=https://www.lopesan.com/es/&exacto=true",
    "codigo=404",
    "codigo=301&tipo=externo",
    "estado=roto&orden=destino",
]


def _sin_origen(d):
    return {k: v for k, v in d.items() if k != "archivado"}


def test_vivo_y_archivado_devuelven_lo_mismo_filtro_a_filtro(entorno):
    api, jid, Sesion, _ = entorno

    def todo():
        salida = {}
        for qs in CONSULTAS:
            items, d = _todas(api, jid, "links", qs)
            salida["links?" + qs] = ([(i["id"], i["estado_destino"], i["status_destino"],
                                       i["estado_externo"], i["from_url"]) for i in items],
                                     d["total"], d["por_estado"])
            if "orden=" not in qs:
                items, d = _todas(api, jid, "links/destinos", qs)
                salida["destinos?" + qs] = (items, d["total"], d["por_estado"])
        for orden in ("paginas", "url"):
            salida["destinos-" + orden] = _todas(api, jid, "links/destinos", "orden=" + orden)[0]
        salida["resumen"] = _sin_origen(api.get(f"/api/jobs/{jid}/links/resumen").json())
        return salida

    antes = todo()
    assert antes["links?"][1] > 20, "la prueba necesita varias paginas"
    _archivar(Sesion, jid)
    assert api.get(f"/api/jobs/{jid}/links/resumen").json()["archivado"] is True
    despues = todo()
    for k in antes:
        assert despues[k] == antes[k], k


@pytest.fixture(params=["vivo", "archivado"])
def modo(request, entorno):
    api, jid, Sesion, hashes = entorno
    if request.param == "archivado":
        _archivar(Sesion, jid)
    return api, jid, hashes


def _destinos(items):
    return sorted({i["to_url"] for i in items})


def test_estado_del_destino_no_afirma_de_mas(modo):
    api, jid, h = modo
    rotos, d = _todas(api, jid, "links", "estado=roto")
    assert _destinos(rotos) == sorted([H + "/es/ofertas/verano-2024", H + "/es/reservas/",
                                       EXTERNOS["viejo"][0]])
    # El 403 de Facebook es lo que responde un WAF: ni roto ni bien.
    nc, _ = _todas(api, jid, "links", "estado=no_concluyente")
    assert _destinos(nc) == [EXTERNOS["fb"][0]]
    sin, _ = _todas(api, jid, "links", "estado=sin_dato")
    assert _destinos(sin) == sorted([H + "/es/buscar?q=hotel", H + "/es/privado/",
                                     EXTERNOS["sinsonda"][0]])
    redir, _ = _todas(api, jid, "links", "estado=redireccion")
    assert _destinos(redir) == sorted([H + "/es/hoteles/abora-buenaventura", EXTERNOS["corto"][0]])
    # Los conteos por estado cuadran con el total sin filtro
    total = api.get(f"/api/jobs/{jid}/links").json()
    assert sum(total["por_estado"].values()) == total["total"]
    assert d["por_estado"] == total["por_estado"]
    assert total["por_estado"]["roto"] == len(rotos) == 3 + 2  # verano x2, reservas, viejo x2
    # Cada fila lleva su estado y su codigo; la sonda, solo en externos
    porurl = {i["to_url"]: i for i in rotos}
    assert porurl[EXTERNOS["viejo"][0]]["status_destino"] == 404
    assert porurl[EXTERNOS["viejo"][0]]["estado_externo"] == "roto"
    assert porurl[H + "/es/reservas/"]["status_destino"] == 503
    assert porurl[H + "/es/reservas/"]["estado_externo"] is None


def test_por_defecto_salen_primero_los_destinos_rotos(modo):
    api, jid, _ = modo
    from api.enlaces import RANGO

    items, d = _todas(api, jid, "links")
    assert d["orden"] == "estado"
    rangos = [RANGO[i["estado_destino"]] for i in items]
    assert rangos == sorted(rangos)
    assert items[0]["estado_destino"] == "roto"
    # El orden de antes (solo por destino) sigue disponible
    items_d, _ = _todas(api, jid, "links", "orden=destino")
    assert sorted(i["id"] for i in items_d) == sorted(i["id"] for i in items)
    assert [i["id"] for i in items_d] != [i["id"] for i in items]


def test_cada_filtro_de_enlace(modo):
    api, jid, h = modo

    def lista(qs):
        return _todas(api, jid, "links", qs)[0]

    ext = lista("tipo=externo")
    assert len(ext) == 10 and all(not i["is_internal"] for i in ext)
    assert all(i["is_internal"] for i in lista("tipo=interno"))
    nf = lista("follow=no")
    assert [(i["to_url"], i["follow"]) for i in nf] == [(H + "/es/reservas/", False)]
    assert {i["link_position"] for i in lista("posicion=header")} == {"header"}
    assert len(lista("posicion=header")) == 2
    # El ancla busca tambien en el alt de un enlace de imagen, sin mayusculas
    ancla = lista("ancla=VERANO")
    assert sorted((i["anchor_text"] or "", i["alt_text"] or "") for i in ancla) == [
        ("", "Banner verano"), ("Ofertas de verano", "")]
    org = lista("origen=abora-catarina")
    assert {i["from_url"] for i in org} == {H + "/es/hoteles/abora-catarina"}
    assert len(org) == 4  # menu x2, banner, reservar
    exacto = lista("destino=https://www.lopesan.com/es/&exacto=true")
    assert {i["to_url"] for i in exacto} == {H + "/es/"} and len(exacto) == 4
    contiene = lista("destino=ABORA")
    assert {i["to_url"] for i in contiene} == {H + "/es/hoteles/abora-catarina",
                                              H + "/es/hoteles/abora-buenaventura",
                                              EXTERNOS["booking"][0]}
    assert [i["to_url"] for i in lista("codigo=404")] == [H + "/es/ofertas/verano-2024"] * 2 + \
        [EXTERNOS["viejo"][0]] * 2
    # Un comodin de LIKE en el texto se busca tal cual
    assert lista("destino=%25") == [] and lista("ancla=_") == []
    # Varios a la vez se suman (Y)
    assert len(lista("tipo=externo&estado=ok&origen=/es/hoteles/")) == 1


def test_vista_por_destino_y_quien_lo_enlaza(modo):
    api, jid, h = modo
    destinos, d = _todas(api, jid, "links/destinos")
    assert d["orden"] == "estado"
    por = {x["to_url"]: x for x in destinos}
    inicio = por[H + "/es/"]
    # Cuatro paginas, cuatro enlaces distintos y ocho apariciones (menu x2)
    assert (inicio["paginas"], inicio["enlaces"], inicio["apariciones"]) == (4, 4, 8)
    assert inicio["anclas"] == [{"texto": "Inicio", "veces": 8}]
    assert inicio["estado_destino"] == "ok" and inicio["status_destino"] == 200
    assert inicio["url_id"] is not None and inicio["is_internal"] is True
    verano = por[H + "/es/ofertas/verano-2024"]
    assert verano["estado_destino"] == "roto" and verano["paginas"] == 2
    assert {a["texto"] for a in verano["anclas"]} == {"Ofertas de verano", "Banner verano"}
    assert por[EXTERNOS["fb"][0]]["veredicto_sonda"] == "no_verificable"
    assert por[EXTERNOS["fb"][0]]["url_id"] is None
    assert por[H + "/es/hoteles/abora-catarina"]["anclas"][0]["veces"] == 1
    assert por[H + "/es/reservas/"]["nofollow"] == 1
    # Rotos primero, y entre los que van bien, los mas enlazados
    assert destinos[0]["estado_destino"] == "roto"
    oks = [x for x in destinos if x["estado_destino"] == "ok"]
    assert [x["paginas"] for x in oks] == sorted((x["paginas"] for x in oks), reverse=True)
    assert sum(d["por_estado"].values()) == d["total"] == len(destinos) == 7 + 1 + 5 - 0
    # Quien enlaza a X: exactamente sus enlaces
    quien, _ = _todas(api, jid, "links", f"destino_hash={inicio['to_url_hash']}")
    assert len(quien) == inicio["enlaces"]
    assert sum(i["repeticiones"] for i in quien) == inicio["apariciones"]
    assert len({i["from_url_id"] for i in quien}) == inicio["paginas"]
    # Los filtros de enlace se aplican antes de agrupar
    pie, dp = _todas(api, jid, "links/destinos", "posicion=footer")
    assert len(pie) == 5 and all(not x["is_internal"] for x in pie)
    assert dp["por_estado"] == {"roto": 1, "redireccion": 1, "no_concluyente": 1,
                                "sin_dato": 1, "ok": 1}
    solo_rotos, _ = _todas(api, jid, "links/destinos", "posicion=footer&estado=roto")
    assert [x["to_url"] for x in solo_rotos] == [EXTERNOS["viejo"][0]]


def test_resumen_cuenta_los_externos_aunque_el_censo_no_tenga_urls_externas(modo):
    api, jid, _ = modo
    r = api.get(f"/api/jobs/{jid}/links/resumen").json()
    assert r["externos"] == {"enlaces": 10, "apariciones": 10, "destinos": 5}
    assert r["destinos_por_estado"]["externos"] == {
        "roto": 1, "redireccion": 1, "no_concluyente": 1, "sin_dato": 1, "ok": 1}
    assert r["internos"]["destinos"] == 8
    assert r["destinos_por_estado"]["internos"]["roto"] == 2
    assert r["sondas"] is True
    total = api.get(f"/api/jobs/{jid}/links").json()["total"]
    assert r["enlaces"] == total


def test_los_enlaces_de_otro_censo_no_se_cuelan(modo):
    api, jid, _ = modo
    for qs in ("tipo=externo", "follow=no", "ancla=verano", "codigo=404"):
        items, _ = _todas(api, jid, "links", qs)
        assert {i["job_id"] for i in items} == {str(jid)}, qs
    assert len(_todas(api, jid, "links", "follow=no")[0]) == 1


def test_parametros_invalidos_son_422(entorno):
    api, jid, _, _ = entorno
    for qs in ("posicion=menu", "estado=roto_o_no", "tipo=todos", "orden=hash", "codigo=-1"):
        assert api.get(f"/api/jobs/{jid}/links?{qs}").status_code == 422, qs


def test_archivado_sin_fichero_lo_dice_y_no_devuelve_cero(entorno):
    api, jid, Sesion, _ = entorno
    _archivar(Sesion, jid)
    archivo_enlaces.ruta(jid).unlink()
    for ruta in ("links", "links/destinos", "links/resumen"):
        r = api.get(f"/api/jobs/{jid}/{ruta}")
        assert r.status_code == 409, ruta
        assert "archivados" in r.json()["detail"]
