"""Reglas del adaptador WordPress (decision 161): lo que se lee de la REST API
y como se guarda, sin red.

- `shared/wordpress.py`: raiz anunciada, URLs de la REST API con y sin
  enlaces permanentes, tipos y taxonomias publicos, pagina N de un archivo.
- La tuberia guarda las entidades en `adapter_entities`, y una reanudacion
  (otro proceso de Scrapy, que vuelve a leer la REST API entera) empieza
  borrando las de antes.
- El backup las lleva y la importacion las cuelga del job nuevo, con el
  resumen de `jobs.adapters`.
"""

from __future__ import annotations

import io
import types
import uuid
import zipfile

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared import wordpress as W
from shared.models import AdapterEntity, Base, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# --- shared/wordpress.py ----------------------------------------------------

def test_la_raiz_anunciada_en_la_cabecera_link():
    # La cabecera real de blogs.uoc.edu (24-oct-2026).
    assert W.raiz_de_cabecera('<https://blogs.uoc.edu/wp-json/>; rel="https://api.w.org/"',
                              "https://blogs.uoc.edu/") == "https://blogs.uoc.edu/wp-json/"
    # Varias en la misma cabecera, relativas, y la que no es la API.
    assert W.raiz_de_cabecera(
        '<https://x.com/wp-json/wp/v2/pages/7>; rel="alternate"; type="application/json", '
        '</blog/wp-json/>; rel="https://api.w.org/"', "https://x.com/blog/") \
        == "https://x.com/blog/wp-json/"
    assert W.raiz_de_cabecera('<https://x.com/?p=7>; rel=shortlink', "https://x.com/") is None
    assert W.raiz_de_cabecera(None, "https://x.com/") is None


def test_url_rest_con_y_sin_enlaces_permanentes():
    assert W.url_rest("https://x.com/wp-json/", "wp/v2/posts", {"per_page": 100, "page": 2}) \
        == "https://x.com/wp-json/wp/v2/posts?per_page=100&page=2"
    assert W.url_rest("https://x.com/?rest_route=/", "wp/v2/posts", {"per_page": 100}) \
        == "https://x.com/?rest_route=/wp/v2/posts&per_page=100"
    assert W.url_rest("https://x.com/blog/wp-json/", "", {"_fields": "name,home"}) \
        == "https://x.com/blog/wp-json/?_fields=name,home"
    assert W.raiz_por_defecto("https://x.com/blog/?a=1") == "https://x.com/wp-json/"
    assert W.raiz_por_defecto("mailto:a@b.c") is None


def test_el_indice_y_el_plugin_seo():
    assert W.es_indice({"namespaces": ["oembed/1.0", "wp/v2"]})
    assert not W.es_indice({"namespaces": ["oembed/1.0"]})
    assert not W.es_indice([])
    assert W.plugin_seo(["wp/v2", "rankmath/v1"]) == "Rank Math"
    assert W.plugin_seo(["wp/v2", "yoast/v1"]) == "Yoast SEO"
    assert W.plugin_seo(["wp/v2"]) is None


def test_tipos_y_taxonomias_publicos():
    # Lo que devuelve blogs.uoc.edu, recortado.
    tipos = {
        "post": {"rest_base": "posts", "rest_namespace": "wp/v2"},
        "page": {"rest_base": "pages", "rest_namespace": "wp/v2"},
        "attachment": {"rest_base": "media", "rest_namespace": "wp/v2"},
        "nav_menu_item": {"rest_base": "menu-items", "rest_namespace": "wp/v2"},
        "wp_font_face": {"rest_base": "font-families/(?P<font_family_id>[\\d]+)/font-faces",
                         "rest_namespace": "wp/v2"},
        "event": {"rest_base": "event", "rest_namespace": "wp/v2"},
        "producto": {"rest_base": "products", "rest_namespace": "wc/v3"},
    }
    assert W.tipos_de_contenido(tipos) == [("page", "pages"), ("post", "posts"),
                                           ("event", "event")]
    taxs = {
        "category": {"rest_base": "categories", "types": ["post"]},
        "post_tag": {"rest_base": "tags", "types": ["post"]},
        "nav_menu": {"rest_base": "menus", "types": ["nav_menu_item"]},
        "wp_pattern_category": {"rest_base": "wp_pattern_category", "types": ["wp_block"]},
        "marca": {"rest_base": "marcas", "types": ["producto"]},
    }
    assert W.taxonomias(taxs, {"post", "page"}) == [("category", "categories"),
                                                    ("post_tag", "tags")]


def test_la_pagina_n_de_un_archivo():
    assert W.url_de_pagina("https://x.com/category/a/", 5) == "https://x.com/category/a/page/5/"
    assert W.url_de_pagina("https://x.com/category/a", 5) == "https://x.com/category/a/page/5/"
    assert W.url_de_pagina("https://x.com", 2) == "https://x.com/page/2/"
    # Sin enlaces permanentes: `paged`.
    assert W.url_de_pagina("https://x.com/?cat=3", 4) == "https://x.com/?cat=3&paged=4"
    assert W.url_de_pagina(None, 2) is None
    assert W.entero(" 1112 ") == 1112 and W.entero("x") is None and W.entero(None) is None


# --- Tuberia y reanudacion --------------------------------------------------

@pytest.fixture()
def base(tmp_path, monkeypatch):
    import shared.database

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="t", status="running", seeds=["https://x.com/"],
                  config={"max_depth": 5}))
        s.commit()
    return Sesion, jid


def _entidades(n=3):
    return [{"tipo": "post", "id_externo": str(i), "url": f"https://x.com/e{i}/",
             "url_hash": f"h{i}", "en_alcance": True, "estado": "publish",
             "datos": {"x": "y"}} for i in range(n)]


def _proceso(jid, items):
    pytest.importorskip("scrapy")
    from seo_crawler.items import EntidadesAdaptadorItem
    from seo_crawler.pipelines import PostgresPipeline

    spider = types.SimpleNamespace(job_id=str(jid))
    tub = PostgresPipeline(batch_size=2, commit_every=50)
    tub.open_spider(spider)
    for entidades in items:
        tub.process_item(EntidadesAdaptadorItem(job_id=str(jid), adaptador="wordpress",
                                                entidades=entidades), spider)
    tub.close_spider(spider)


def test_la_tuberia_guarda_y_una_reanudacion_empieza_de_cero(base):
    pytest.importorskip("scrapy")
    Sesion, jid = base
    _proceso(jid, [_entidades(3), [{"tipo": "sonda_paginacion", "url": "https://x.com/page/9/",
                                    "url_hash": "p9", "status_code": 200}]])
    with Sesion() as s:
        assert s.query(AdapterEntity).filter(AdapterEntity.job_id == jid).count() == 4

    # Otro proceso: el spider borra lo de antes al arrancar el adaptador.
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id=str(jid))
    sp.job_config = {"max_depth": 5}
    sp.max_depth = 5
    sp._iniciar_wordpress()
    with Sesion() as s:
        assert s.query(AdapterEntity).filter(AdapterEntity.job_id == jid).count() == 0
    _proceso(jid, [_entidades(2)])
    with Sesion() as s:
        filas = s.query(AdapterEntity).filter(AdapterEntity.job_id == jid).all()
    assert sorted(f.url for f in filas) == ["https://x.com/e0/", "https://x.com/e1/"]
    assert all(f.adaptador == "wordpress" and f.en_alcance for f in filas)


def test_apagado_no_borra_nada(base):
    pytest.importorskip("scrapy")
    Sesion, jid = base
    _proceso(jid, [_entidades(1)])
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id=str(jid))
    sp.job_config = {"adapters": {"wordpress": {"modo": "off"}}}
    sp.max_depth = 5
    sp._iniciar_wordpress()
    assert not sp._wp_activo()
    with Sesion() as s:
        assert s.query(AdapterEntity).filter(AdapterEntity.job_id == jid).count() == 1


def test_la_configuracion_se_declara_y_tiene_topes():
    """Pydantic v2 tira lo que no esta declarado (decision 33)."""
    pytest.importorskip("fastapi")
    from api.schemas import JobConfig

    c = JobConfig(adapters={"wordpress": {"modo": "off", "muestra_adjuntos": 5}})
    assert c.model_dump()["adapters"]["wordpress"]["modo"] == "off"
    assert JobConfig().adapters.wordpress.modo == "auto"
    assert JobConfig().adapters.wordpress.max_concurrencia == 2
    with pytest.raises(ValueError):
        JobConfig(adapters={"wordpress": {"max_concurrencia": 3}})
    with pytest.raises(ValueError):
        JobConfig(adapters={"wordpress": {"usuarios": True}})


# --- Backup -----------------------------------------------------------------

def test_el_backup_lleva_las_entidades_y_el_resumen(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    import shared.database
    from api import backup

    engine = create_engine(f"sqlite:///{tmp_path}/b.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    with Sesion() as s:
        job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"],
                  config={}, adapters={"wordpress": {"detectado": True, "rest": "abierta"}})
        s.add(job)
        s.flush()
        s.add(Url(job_id=job.id, url="https://x.com/", url_hash="h", host="x.com",
                  path="/", is_internal=True, is_html=True, status_code=200))
        s.add(AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="post", id_externo="1",
                            url="https://x.com/e1/", url_hash="h1", estado="publish"))
        s.commit()
        jid = job.id
    datos = b"".join(backup.stream_backup_zip(jid))
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert len(z.read("adapter_entities.jsonl").splitlines()) == 1
    with Sesion() as s:
        r = backup.import_backup_zip(io.BytesIO(datos), False, s)
        nuevo = uuid.UUID(str(r["new_job_id"]))
        assert r["rows_imported"]["adapter_entities"] == 1
    with Sesion() as s:
        assert s.get(Job, nuevo).adapters["wordpress"]["rest"] == "abierta"
        (fila,) = s.query(AdapterEntity).filter(AdapterEntity.job_id == nuevo).all()
        assert (fila.tipo, fila.url_hash) == ("post", "h1")
