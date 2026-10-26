"""El cliente como entidad (#36, F1): slugs, herencia de configuracion y la
migracion de los `client_id` de texto.

Vive en `shared/` porque lo necesitan tres sitios: la API (alta de clientes y
creacion de jobs), `scripts/init_db.py` (la migracion) y la importacion de
backups. Nada de aqui importa Pydantic: la imagen del worker no lo lleva.

Tres criterios, cada uno evitando una forma de fallar en silencio:

1. **El slug quita acentos y caja, y nada mas.** `Ácme`, `acme ` y `ACME` son
   el mismo cliente escrito tres veces; `acme` y `acme-canary` NO, porque eso
   no se puede decidir mirando el texto. La migracion imprime los textos que
   colapsan en el mismo slug para revisarlos a mano: fusionar erratas sin
   decirlo es cambiar el historico de un cliente sin que nadie lo sepa.
2. **La herencia se resuelve AL CREAR el job y se copia en `jobs.config`.**
   El crawler y el analizador siguen leyendo `job.config` sin saber que existe
   el cliente: un job es una foto reproducible, y editar la configuracion del
   cliente manana no puede cambiar lo que dice un censo de ayer.
3. **La fusion es profunda y `None` borra.** Los dicts se fusionan clave a
   clave (`crawl_behavior.render_wait_ms` del perfil no pisa el
   `crawl_behavior.download_timeout` del cliente); listas y escalares, gana la
   ultima capa; un `None` explicito quita la clave y deja que mande el valor
   por defecto. Sin lo ultimo no habria forma de que un job dijera «sin tope de
   URLs» cuando el cliente fija uno.
"""

from __future__ import annotations

# Tipos de rastreo (UX-07, docs/AUDITORIA_UX.md). Lo que se ha marcado a mano
# manda; si no, el nombre: es como se han distinguido siempre a ojo.
TIPOS_DE_RASTREO = ("censo", "canario", "prueba")
# Subcadenas en minusculas. Las mismas que usa el filtro SQL de la API
# (`api/routers/jobs.py::_tipo_sql`): dos reglas distintas darian un tipo en
# la ficha y otro en el filtro.
PISTAS_CANARIO = ("canary", "canario")
PISTAS_PRUEBA = ("prueba", "test", "validacion", "validación", "muestra", "sondeo")


def tipo_de_rastreo(nombre: str | None, tipo: str | None) -> str:
    """El tipo de un rastreo: el marcado o, si no hay, el que dice su nombre."""
    if tipo in TIPOS_DE_RASTREO:
        return tipo
    nombre = (nombre or "").lower()
    if any(p in nombre for p in PISTAS_CANARIO):
        return "canario"
    if any(p in nombre for p in PISTAS_PRUEBA):
        return "prueba"
    return "censo"

import copy
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

SLUG_MAX = 128
_PATRON_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def slugificar(texto: str | None) -> str:
    """`'Ácme Hoteles '` -> `'acme-hoteles'`.

    Sin la extension `unaccent` en Postgres, el slug se calcula en Python con
    `unicodedata`: descomponer y tirar las marcas combinantes deja `a` de `á`
    y `n` de `ñ`. Lo que no sea letra o digito ASCII se vuelve guion.
    """
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")
    return texto[:SLUG_MAX].strip("-")


# Sufijos con los que se han lanzado pruebas de un cliente: `lopesan-canary`,
# `lopesan-canario`, `lopesan-prueba`. Son rastreos DE ese cliente —una
# muestra antes del censo completo—, no clientes distintos. Medido sobre la BD
# de produccion el 2026-10-13: sin plegarlos, 10 clientes donde hay 5, y el
# canario de Lopesan fuera de la ficha de Lopesan, que es donde se compara.
_SUFIJO_PRUEBA = re.compile(r"-(?:canary|canario|prueba|test)$")


def slug_de_cliente(texto: str | None) -> str:
    """El slug del CLIENTE al que pertenece un `client_id` de texto.

    `'Lopesan-Canary'` -> `'lopesan'`. Solo se pliega el sufijo si queda algo
    delante: un cliente que se llame literalmente «test» sigue siendo «test».
    """
    slug = slugificar(texto)
    base = _SUFIJO_PRUEBA.sub("", slug)
    return base or slug


def slug_valido(slug: str) -> bool:
    return bool(slug) and len(slug) <= SLUG_MAX and bool(_PATRON_SLUG.match(slug))


# ---------------------------------------------------------------------------
# Herencia de configuracion
# ---------------------------------------------------------------------------
def fusionar_config(*capas: dict | None) -> dict:
    """Fusion profunda de configuraciones parciales, de la mas general a la
    mas concreta (cliente, perfil, job). No modifica las capas.

    - dict con dict: se fusionan clave a clave;
    - cualquier otra cosa (lista, escalar): gana la ultima capa;
    - `None` explicito: se quita la clave, y manda el valor por defecto de
      `JobConfig` al validar.

    Las listas se SUSTITUYEN, no se suman: si el cliente excluye `/carrito/` y
    el perfil del blog pone sus propios `exclude_patterns`, sumar haria
    imposible quitar un patron del cliente desde el perfil.
    """
    resultado: dict = {}
    for capa in capas:
        if capa:
            _fusionar_en(resultado, capa)
    return resultado


# Dicts de reglas con nombre (decision 104): se suman por NOMBRE, pero cada
# regla se sustituye ENTERA. Fusionarla campo a campo dejaria pegado lo que el
# perfil no repite: un `solo_urls` del cliente seguiria acotando la regla que
# el perfil ha reescrito para otra plantilla, sin que nadie lo viera.
DICTS_DE_REGLAS = ("custom_extraction", "custom_search")

# Dicts cuya clave es una regla que se puede escribir de varias formas
# (`blogs.uoc.edu` y `blogs.uoc.edu/*` son el mismo tope, decision 172): se
# fusionan por la clave NORMALIZADA, para que el perfil sobrescriba o quite
# (`null`) la regla del cliente aunque la escriba con otra grafia. Gana la
# grafia de la capa mas concreta.
def _normalizador(ruta: str):
    if ruta.rstrip(".") == "topes_por_carpeta":
        from shared.topes_carpeta import clave_normal

        return clave_normal
    return None


def _fusionar_en(destino: dict, capa: dict, ruta: str = "") -> None:
    normal = _normalizador(ruta)
    # Solo las claves de capas ANTERIORES: dos grafias de la misma regla en
    # una misma capa siguen siendo un error, y lo dice `compilar` al validar.
    previas = {normal(k): k for k in destino} if normal is not None else {}
    for clave, valor in capa.items():
        aqui = f"{ruta}{clave}"
        if normal is not None:
            vieja = previas.pop(normal(clave), None)
            if vieja is not None and vieja != clave:
                destino.pop(vieja, None)
        if valor is None:
            destino.pop(clave, None)
        elif ruta.rstrip(".") in DICTS_DE_REGLAS:
            destino[clave] = copy.deepcopy(valor)
        elif isinstance(valor, dict) and isinstance(destino.get(clave), dict):
            _fusionar_en(destino[clave], valor, aqui + ".")
        elif isinstance(valor, dict):
            destino[clave] = {}
            _fusionar_en(destino[clave], valor, aqui + ".")
        else:
            destino[clave] = copy.deepcopy(valor)


_FALTA = object()


def diferencia_de_config(base: dict | None, objetivo: dict | None, _ruta: str = "") -> dict:
    """Lo minimo que hay que poner encima de `base` para llegar a `objetivo`:
    `fusionar_config(base, diferencia_de_config(base, objetivo)) == objetivo`.

    Es lo que guarda la migracion de programaciones a perfiles (decision 193):
    de una copia congelada de la config entera se queda solo lo que de verdad
    difiere de lo heredado, para que los cambios del cliente sigan llegando.
    Con las mismas reglas que `fusionar_config`: un dict se baja clave a clave,
    una regla con nombre (`DICTS_DE_REGLAS`) va ENTERA, una lista se sustituye
    y lo que `objetivo` no tiene se quita con `None`.
    """
    base = base or {}
    objetivo = objetivo or {}
    salida: dict = {}
    en_reglas = _ruta.rstrip(".") in DICTS_DE_REGLAS
    for clave, valor in objetivo.items():
        antes = base.get(clave, _FALTA)
        if valor is None:
            if antes is not _FALTA:
                salida[clave] = None
        elif not en_reglas and isinstance(valor, dict) and isinstance(antes, dict):
            sub = diferencia_de_config(antes, valor, f"{_ruta}{clave}.")
            if sub:
                salida[clave] = sub
        elif valor != antes:
            salida[clave] = copy.deepcopy(valor)
    for clave in base:
        if clave not in objetivo:
            salida[clave] = None
    return salida


# ---------------------------------------------------------------------------
# Cliente a partir de un texto (compatibilidad con `client_id`)
# ---------------------------------------------------------------------------
def cliente_por_texto(db, texto: str | None, *, crear: bool = True):
    """El cliente que corresponde a un `client_id` de texto, creandolo si falta.

    Es el camino de compatibilidad: `scripts/lanzar_job.py`, los backups de
    formato 1 y quien llame a la API como antes siguen mandando un texto. Si no
    se resolviera aqui, cada job nuevo lanzado asi quedaria sin cliente hasta
    la siguiente migracion. Devuelve None si el texto no da un slug.
    """
    from shared.models import Client

    texto = (texto or "").strip()
    slug = slug_de_cliente(texto)
    if not slug:
        return None
    cliente = db.query(Client).filter(Client.slug == slug).one_or_none()
    if cliente is None and crear:
        cliente = Client(nombre=texto[:256], slug=slug, dominios=[],
                         config_defecto={}, integraciones=[])
        db.add(cliente)
        db.flush()
        logger.warning(
            "Cliente %r creado a partir del client_id de texto %r", slug, texto)
    return cliente


# ---------------------------------------------------------------------------
# Migracion: los client_id de texto pasan a filas de `clients`
# ---------------------------------------------------------------------------
def migrar_client_id_texto(db) -> dict[str, Any]:
    """Crea un cliente por cada slug distinto de `jobs.client_id` y
    `programaciones.client_id`, y cuelga de el los jobs y programaciones que
    aun no tienen `cliente_id`.

    Idempotente: solo toca filas con `cliente_id` NULL, y un cliente que ya
    existe por slug se reutiliza. No confirma la transaccion: lo hace quien
    llama, para que la migracion entera sea todo o nada.

    Devuelve `{"creados", "jobs", "programaciones", "colisiones"}`;
    `colisiones` son los slugs a los que llegan VARIOS textos distintos
    (`'Acme'`, `'acme '`, `'Ácme'`), para revisarlos a mano.
    """
    from shared.models import Client, Job, Programacion

    textos: set[str] = set()
    for modelo in (Job, Programacion):
        filas = (
            db.query(modelo.client_id)
            .filter(modelo.client_id.isnot(None), modelo.cliente_id.is_(None))
            .distinct()
            .all()
        )
        textos.update(t for (t,) in filas if t and t.strip())

    por_slug: dict[str, set[str]] = {}
    for texto in textos:
        slug = slug_de_cliente(texto)
        if slug:
            por_slug.setdefault(slug, set()).add(texto)

    existentes = {
        c.slug: c for c in db.query(Client).filter(Client.slug.in_(list(por_slug))).all()
    } if por_slug else {}

    creados = 0
    contados = {"jobs": 0, "programaciones": 0}
    for slug, variantes in sorted(por_slug.items()):
        cliente = existentes.get(slug)
        if cliente is None:
            # El nombre: la variante mas frecuente no se sabe sin otra
            # consulta; la mas larga suele ser la que lleva acentos y
            # mayusculas, que es como se escribe el nombre de verdad.
            # Las de prueba (`-canary`) no dan nombre al cliente.
            propias = [v for v in variantes if slugificar(v) == slug] or [slug]
            nombre = max((v.strip() for v in propias), key=lambda v: (len(v), v))
            cliente = Client(nombre=nombre[:256], slug=slug, dominios=[],
                             config_defecto={}, integraciones=[])
            db.add(cliente)
            db.flush()
            creados += 1
        for texto in variantes:
            contados["jobs"] += (
                db.query(Job)
                .filter(Job.client_id == texto, Job.cliente_id.is_(None))
                .update({Job.cliente_id: cliente.id}, synchronize_session=False)
            )
            contados["programaciones"] += (
                db.query(Programacion)
                .filter(Programacion.client_id == texto,
                        Programacion.cliente_id.is_(None))
                .update({Programacion.cliente_id: cliente.id},
                        synchronize_session=False)
            )

    # Una colision es una ERRATA (`Acme`, `acme `, `Ácme`), no un canario: los
    # textos con sufijo de prueba se pliegan a proposito y no se avisan.
    colisiones = {}
    for s, v in por_slug.items():
        propias = sorted(t for t in v if slugificar(t) == s)
        if len(propias) > 1:
            colisiones[s] = propias
    return {"creados": creados, **contados, "colisiones": colisiones}
