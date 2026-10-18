"""Reglas de alerta por cliente (Ola 5.2, decision 135).

Hasta ahora las alertas salian de una sola tabla para todos los clientes
(`shared.comparacion.REGLAS`, con el 5% de las indexables, el piso de 20
paginas y el 50% de una plantilla de la decision 68). Eso es un buen punto de
partida y un mal final: para un cliente las fichas de producto son el negocio
y 15 en 404 ya es grave; para otro, un 4% de un blog de 30.000 entradas no le
importa a nadie.

Las reglas viven en `clients.reglas_alerta` (JSON) y se editan desde el panel
del cliente. **Un cliente sin reglas guardadas tiene las de serie**, que son
exactamente el comportamiento de antes mas dos reglas propias razonables: no
configurar nada no puede cambiar lo que se avisaba. Lo guardado es PARCIAL —se
fusiona sobre lo de serie—, asi que una regla nueva de serie llega tambien a
quien ya habia tocado las suyas.

Dos clases de regla:

- **De comparacion** (las de `REGLAS`): por cliente se pueden apagar, cambiar
  sus tres umbrales y decidir si avisan fuera de la interfaz. Siguen
  midiendose sobre las paginas comunes a los dos censos, con todos los
  guardias de la comparacion (truncado, alcance, bloqueo, version).
- **Propias**: una medida (`MEDIDAS`, o `incidencia:<tipo>`), opcionalmente en
  un segmento de negocio (decision 125), un modo —el valor del censo, su cambio
  frente al anterior o su cambio en %—, un operador y un umbral. «URLs 4xx en
  fichas > 20» es `{"medida": "urls_4xx", "segmento": "fichas", "modo":
  "valor", "operador": ">", "umbral": 20}`.

Lo que se niega a evaluar, con el motivo (decisiones 67, 91 y 103): un censo
bloqueado (sus codigos son los del muro), un perfil de la competencia (no
abre alertas, #36 F2) y, en los modos de cambio, un censo sin referencia
comparable, truncado o analizado con otra version: un «baja un 40%» contra un
censo que se paro a la mitad es falso.

Vive en `shared/` porque lo usan el worker (al cerrar el rastreo) y la API (el
panel y la vista previa), y la imagen de la API no lleva `analysis/`.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

from shared.comparacion import MINIMO_PAGINAS, PCT_ALERTA, PCT_ALERTA_PLANTILLA, REGLAS

# --- Lo que se puede medir ----------------------------------------------------

# `segmentable`: si admite el filtro por `urls.segmento`. Las de incidencias
# cuentan PAGINAS distintas con la incidencia, no filas: una pagina con dos
# hreflang rotos es una pagina que arreglar.
MEDIDAS: dict[str, dict[str, Any]] = {
    "urls": {"etiqueta": "URLs internas", "mejor": "sube"},
    "indexables": {"etiqueta": "Páginas indexables", "mejor": "sube"},
    "urls_3xx": {"etiqueta": "URLs que redirigen (3xx)", "mejor": "baja"},
    "urls_4xx": {"etiqueta": "URLs 4xx", "mejor": "baja"},
    "urls_5xx": {"etiqueta": "URLs 5xx", "mejor": "baja"},
    "urls_bloqueadas": {"etiqueta": "URLs bloqueadas por robots.txt", "mejor": "baja"},
    "indexables_sin_camino": {"etiqueta": "Indexables sin camino desde la home",
                              "mejor": "baja"},
    "urls_con_error": {"etiqueta": "URLs con alguna incidencia de error", "mejor": "baja"},
    "urls_con_aviso": {"etiqueta": "URLs con alguna incidencia de aviso", "mejor": "baja"},
}
PREFIJO_INCIDENCIA = "incidencia:"

MODOS = {
    "valor": "el valor del rastreo",
    "cambio": "el cambio frente al anterior",
    "cambio_pct": "el cambio en % frente al anterior",
}
OPERADORES = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
              "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
SEVERIDADES = ("critical", "warning", "info")

# --- Lo de serie --------------------------------------------------------------

def _comparacion_de_serie() -> dict[str, dict[str, Any]]:
    return {regla: {"activa": True, "avisar": sev == "critical",
                    "pct_sitio": round(PCT_ALERTA * 100, 1),
                    "min_paginas": MINIMO_PAGINAS,
                    "pct_plantilla": round(PCT_ALERTA_PLANTILLA * 100, 1)}
            for regla, (sev, _q) in REGLAS.items()}


PROPIAS_DE_SERIE: list[dict[str, Any]] = [
    # Lo que la comparacion por pagina no ve: las indexables que DESAPARECEN
    # del censo no estan en las dos listas, asi que «salen del indice» no las
    # cuenta. Un 10% menos de indexables de una semana a otra es una plantilla,
    # un robots.txt o un despliegue.
    {"id": "caen-indexables", "nombre": "Caen las páginas indexables",
     "medida": "indexables", "segmento": None, "modo": "cambio_pct",
     "operador": "<=", "umbral": -10, "severidad": "critical", "avisar": True,
     "activa": True},
    {"id": "suben-errores", "nombre": "Suben las URLs con error",
     "medida": "urls_con_error", "segmento": None, "modo": "cambio",
     "operador": ">=", "umbral": 50, "severidad": "warning", "avisar": False,
     "activa": True},
]

RESUMEN_DE_SERIE = {"activo": True, "dia": 1, "hora": 8}  # lunes a las 8:00


def de_serie() -> dict[str, Any]:
    return {"comparacion": _comparacion_de_serie(),
            "propias": copy.deepcopy(PROPIAS_DE_SERIE),
            "silenciado_hasta": None,
            "resumen_semanal": dict(RESUMEN_DE_SERIE)}


# --- Validar y fusionar -------------------------------------------------------

class ReglaInvalida(ValueError):
    pass


_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _numero(v, campo, minimo=None, maximo=None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ReglaInvalida(f"{campo} tiene que ser un número")
    if minimo is not None and v < minimo or maximo is not None and v > maximo:
        raise ReglaInvalida(f"{campo} fuera de rango ({minimo}–{maximo})")
    return v


def medida_valida(medida: str) -> bool:
    if medida in MEDIDAS:
        return True
    return (medida.startswith(PREFIJO_INCIDENCIA)
            and bool(re.fullmatch(r"[a-z0-9_]{1,64}", medida[len(PREFIJO_INCIDENCIA):])))


def validar_propia(r: dict[str, Any]) -> dict[str, Any]:
    """Una regla propia completa y saneada, o `ReglaInvalida` con el motivo."""
    if not isinstance(r, dict):
        raise ReglaInvalida("cada regla propia es un objeto")
    desconocidas = set(r) - {"id", "nombre", "medida", "segmento", "modo", "operador",
                             "umbral", "severidad", "avisar", "activa"}
    if desconocidas:
        raise ReglaInvalida(f"claves desconocidas en una regla: {sorted(desconocidas)}")
    rid = str(r.get("id") or "").strip()
    if not _ID.match(rid):
        raise ReglaInvalida("id: minúsculas, números y guiones (hasta 64)")
    nombre = str(r.get("nombre") or "").strip()
    if not nombre or len(nombre) > 120:
        raise ReglaInvalida(f"{rid}: el nombre es obligatorio (hasta 120 caracteres)")
    medida = str(r.get("medida") or "")
    if not medida_valida(medida):
        raise ReglaInvalida(f"{rid}: medida desconocida «{medida}»")
    segmento = r.get("segmento")
    if segmento in ("", None):
        segmento = None
    elif not isinstance(segmento, str) or len(segmento) > 128:
        raise ReglaInvalida(f"{rid}: segmento no válido")
    modo = r.get("modo") or "valor"
    if modo not in MODOS:
        raise ReglaInvalida(f"{rid}: modo desconocido «{modo}»")
    operador = r.get("operador") or ">"
    if operador not in OPERADORES:
        raise ReglaInvalida(f"{rid}: operador desconocido «{operador}»")
    severidad = r.get("severidad") or "warning"
    if severidad not in SEVERIDADES:
        raise ReglaInvalida(f"{rid}: severidad desconocida «{severidad}»")
    return {"id": rid, "nombre": nombre, "medida": medida, "segmento": segmento,
            "modo": modo, "operador": operador,
            "umbral": _numero(r.get("umbral"), f"{rid}: umbral", -1e9, 1e9),
            "severidad": severidad, "avisar": bool(r.get("avisar", False)),
            "activa": bool(r.get("activa", True))}


def validar(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Lo que se guarda: PARCIAL y saneado. None = las de serie."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ReglaInvalida("las reglas son un objeto")
    desconocidas = set(raw) - {"comparacion", "propias", "silenciado_hasta", "resumen_semanal"}
    if desconocidas:
        raise ReglaInvalida(f"claves desconocidas: {sorted(desconocidas)}")
    salida: dict[str, Any] = {}
    if raw.get("comparacion") is not None:
        comp = {}
        for regla, cambios in (raw.get("comparacion") or {}).items():
            if regla not in REGLAS:
                raise ReglaInvalida(f"regla de comparación desconocida «{regla}»")
            if not isinstance(cambios, dict):
                raise ReglaInvalida(f"{regla}: tiene que ser un objeto")
            extra = set(cambios) - {"activa", "avisar", "pct_sitio", "min_paginas", "pct_plantilla"}
            if extra:
                raise ReglaInvalida(f"{regla}: claves desconocidas {sorted(extra)}")
            c = {}
            for k in ("activa", "avisar"):
                if k in cambios:
                    c[k] = bool(cambios[k])
            for k in ("pct_sitio", "pct_plantilla"):
                if k in cambios:
                    c[k] = _numero(cambios[k], f"{regla}.{k}", 0, 100)
            if "min_paginas" in cambios:
                c["min_paginas"] = int(_numero(cambios["min_paginas"], f"{regla}.min_paginas",
                                               1, 1_000_000))
            comp[regla] = c
        salida["comparacion"] = comp
    if raw.get("propias") is not None:
        if not isinstance(raw["propias"], list) or len(raw["propias"]) > 50:
            raise ReglaInvalida("propias: una lista de hasta 50 reglas")
        propias = [validar_propia(r) for r in raw["propias"]]
        ids = [r["id"] for r in propias]
        if len(set(ids)) != len(ids):
            raise ReglaInvalida("hay dos reglas propias con el mismo id")
        salida["propias"] = propias
    if raw.get("silenciado_hasta") not in (None, ""):
        try:
            salida["silenciado_hasta"] = date.fromisoformat(
                str(raw["silenciado_hasta"])[:10]).isoformat()
        except ValueError as exc:
            raise ReglaInvalida("silenciado_hasta: una fecha AAAA-MM-DD") from exc
    if raw.get("resumen_semanal") is not None:
        rs = raw["resumen_semanal"]
        if not isinstance(rs, dict) or set(rs) - {"activo", "dia", "hora"}:
            raise ReglaInvalida("resumen_semanal: {activo, dia (1=lunes…7), hora (0-23)}")
        r2 = {}
        if "activo" in rs:
            r2["activo"] = bool(rs["activo"])
        if "dia" in rs:
            r2["dia"] = int(_numero(rs["dia"], "resumen_semanal.dia", 1, 7))
        if "hora" in rs:
            r2["hora"] = int(_numero(rs["hora"], "resumen_semanal.hora", 0, 23))
        salida["resumen_semanal"] = r2
    return salida


def efectivas(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Las de serie con lo guardado encima. Tolerante: lo que no valida se
    ignora (la BD se puede editar a mano) y las reglas siguen funcionando."""
    e = de_serie()
    if not isinstance(raw, dict):
        return e
    for regla, cambios in (raw.get("comparacion") or {}).items():
        if regla in e["comparacion"] and isinstance(cambios, dict):
            e["comparacion"][regla].update(
                {k: v for k, v in cambios.items() if k in e["comparacion"][regla]})
    if isinstance(raw.get("propias"), list):
        propias = []
        for r in raw["propias"]:
            try:
                propias.append(validar_propia(r))
            except ReglaInvalida:
                continue
        e["propias"] = propias
    if raw.get("silenciado_hasta"):
        e["silenciado_hasta"] = raw["silenciado_hasta"]
    if isinstance(raw.get("resumen_semanal"), dict):
        e["resumen_semanal"].update(
            {k: v for k, v in raw["resumen_semanal"].items() if k in e["resumen_semanal"]})
    return e


def silenciado(e: dict[str, Any], hoy: date | None = None) -> bool:
    """Si los avisos fuera de la interfaz estan en pausa. La interfaz las
    sigue mostrando: silenciar es no molestar, no dejar de mirar."""
    hasta = e.get("silenciado_hasta")
    if not hasta:
        return False
    try:
        return (hoy or datetime.now(timezone.utc).date()) <= date.fromisoformat(str(hasta)[:10])
    except ValueError:
        return False


def umbrales_de_comparacion(e: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Lo que entiende `shared.comparacion.comparar_censos(umbrales=...)`."""
    return {regla: {"activa": c["activa"], "pct_sitio": c["pct_sitio"] / 100,
                    "min_paginas": c["min_paginas"],
                    "pct_plantilla": c["pct_plantilla"] / 100}
            for regla, c in (e.get("comparacion") or {}).items()}


# --- El cliente de un job -----------------------------------------------------

def contexto_de_job(db, job) -> tuple[Any, Any]:
    """(cliente, perfil) del job, o (None, None)."""
    from shared.models import Client, CrawlProfile

    cliente = db.get(Client, job.cliente_id) if getattr(job, "cliente_id", None) else None
    perfil = db.get(CrawlProfile, job.profile_id) if getattr(job, "profile_id", None) else None
    return cliente, perfil


def reglas_de_job(db, job) -> dict[str, Any] | None:
    """Las reglas efectivas que aplican a un job, o None si no aplica ninguna
    del cliente (sin cliente: las de serie de la comparacion, sin propias)."""
    cliente, _perfil = contexto_de_job(db, job)
    if cliente is None:
        return None
    return efectivas(getattr(cliente, "reglas_alerta", None))


# --- Medir --------------------------------------------------------------------

def medir(db, job_id, medida: str, segmento: str | None = None) -> float | None:
    """El valor de una medida en un censo (internas), o None si no se sabe.

    Todo por `job_id` (decision 37): un `count` con filtro sobre `urls` o un
    `count(distinct url_id)` sobre `issues`, nunca `links`.
    """
    from sqlalchemy import func, select

    from shared.models import Issue, Url

    job_id = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
    base = [Url.job_id == job_id, Url.is_internal.is_(True)]
    if segmento:
        base.append(Url.segmento == segmento)

    def contar(*cond):
        return db.execute(select(func.count(Url.id)).where(*base, *cond)).scalar() or 0

    def paginas_con(*cond):
        q = (select(func.count(func.distinct(Issue.url_id)))
             .where(Issue.job_id == job_id, *cond))
        if segmento:
            q = q.join(Url, Url.id == Issue.url_id).where(Url.segmento == segmento)
        return db.execute(q).scalar() or 0

    if medida == "urls":
        return contar()
    if medida in ("urls_3xx", "urls_4xx", "urls_5xx"):
        return contar(Url.status_group == medida[-3:])
    if medida == "urls_bloqueadas":
        return contar(Url.status_group == "blocked")
    indexable = (Url.is_html.is_(True), Url.status_group == "2xx", Url.indexable.is_(True))
    if medida == "indexables":
        return contar(*indexable)
    if medida == "indexables_sin_camino":
        return contar(*indexable, Url.click_depth.is_(None))
    if medida == "urls_con_error":
        return paginas_con(Issue.severity == "error")
    if medida == "urls_con_aviso":
        return paginas_con(Issue.severity == "warning")
    if medida.startswith(PREFIJO_INCIDENCIA):
        return paginas_con(Issue.issue_type == medida[len(PREFIJO_INCIDENCIA):])
    return None


def etiqueta_medida(medida: str) -> str:
    if medida in MEDIDAS:
        return MEDIDAS[medida]["etiqueta"]
    if medida.startswith(PREFIJO_INCIDENCIA):
        return f"Páginas con «{medida[len(PREFIJO_INCIDENCIA):]}»"
    return medida


def _fmt(n) -> str:
    if n is None:
        return "—"
    if float(n).is_integer():
        return f"{int(n):,}".replace(",", ".")
    return f"{n:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def frase(r: dict[str, Any]) -> str:
    """La regla en castellano: «URLs 4xx en fichas: el valor > 20»."""
    que = etiqueta_medida(r["medida"])
    if r.get("segmento"):
        que += f" en «{r['segmento']}»"
    unidad = "%" if r["modo"] == "cambio_pct" else ""
    return f"{que}: {MODOS[r['modo']]} {r['operador']} {_fmt(r['umbral'])}{unidad}"


# --- Evaluar las propias ------------------------------------------------------

def _referencia_valida(job, comparacion: dict[str, Any] | None) -> tuple[Any, str | None]:
    """El id del censo con el que medir un cambio, o (None, motivo)."""
    if not comparacion:
        return None, "no hay censo anterior con el que comparar"
    if not comparacion.get("comparable"):
        return None, comparacion.get("motivo") or "el censo anterior no es comparable"
    if comparacion.get("bloqueado"):
        return None, "uno de los dos censos se hizo detrás de un muro"
    if comparacion.get("concluyente") is False:
        # Truncado o con otro alcance: un cambio de recuento seria del alcance,
        # no del sitio (decisiones 67 y 70).
        return None, "la comparación con el anterior no es concluyente (truncado o alcance distinto)"
    return comparacion.get("comparado_con"), None


# Las que decide el ANALISIS (incidencias, indexabilidad, profundidad). Entre
# dos censos analizados con codigo distinto su cambio puede ser nuestro
# (decision 91); los codigos de estado salen del rastreo y no dependen de eso.
def _depende_del_analisis(medida: str) -> bool:
    return medida not in ("urls", "urls_3xx", "urls_4xx", "urls_5xx", "urls_bloqueadas")


def _misma_version_de_analisis(db, job, referencia) -> bool:
    from shared.models import Job

    ref = db.get(Job, referencia if isinstance(referencia, uuid.UUID)
                 else uuid.UUID(str(referencia)))
    a, b = getattr(job, "analisis_version", None), getattr(ref, "analisis_version", None)
    return bool(a) and a == b


def evaluar_propias(db, job, e: dict[str, Any], comparacion: dict[str, Any] | None,
                    perfil=None) -> dict[str, Any]:
    """Las reglas propias contra un censo terminado.

    Devuelve `{"evaluadas", "disparadas": [alerta], "sin_evaluar": [{id,
    motivo}], "motivo"}`. Un `motivo` global (bloqueado, competencia) deja todo
    sin evaluar: no se afirma nada de un censo que no es del sitio.
    """
    from shared.respuestas import MOTIVOS_BLOQUEO

    salida: dict[str, Any] = {"evaluadas": 0, "disparadas": [], "sin_evaluar": [],
                              "motivo": None, "comparado_con": None}
    activas = [r for r in e.get("propias") or [] if r.get("activa")]
    if not activas:
        return salida
    if perfil is not None and getattr(perfil, "rol", None) == "competidor":
        salida["motivo"] = "perfil de la competencia: no abre alertas"
        return salida
    bloqueo = ((job.finish_reason if job.finish_reason in MOTIVOS_BLOQUEO else None)
               or (getattr(job, "health", None) or {}).get("causa"))
    if bloqueo:
        salida["motivo"] = f"el rastreo se hizo detrás de un muro ({bloqueo})"
        return salida
    if job.status != "completed":
        salida["motivo"] = f"el rastreo no se completó ({job.status})"
        return salida

    referencia, sin_ref = _referencia_valida(job, comparacion)
    salida["comparado_con"] = str(referencia) if referencia else None
    misma_version = (_misma_version_de_analisis(db, job, referencia)
                     if referencia is not None else False)
    cache: dict[tuple, float | None] = {}

    def valor(jid, r):
        clave = (str(jid), r["medida"], r.get("segmento"))
        if clave not in cache:
            cache[clave] = medir(db, jid, r["medida"], r.get("segmento"))
        return cache[clave]

    for r in activas:
        ahora = valor(job.id, r)
        if ahora is None:
            salida["sin_evaluar"].append({"id": r["id"], "motivo": "medida desconocida"})
            continue
        antes = cambio = None
        if r["modo"] == "valor":
            medido = ahora
        else:
            if referencia is None:
                salida["sin_evaluar"].append({"id": r["id"], "motivo": sin_ref})
                continue
            if _depende_del_analisis(r["medida"]) and not misma_version:
                salida["sin_evaluar"].append({"id": r["id"], "motivo": (
                    "los dos censos se analizaron con versiones distintas (o sin "
                    "sello): el cambio podría ser nuestro")})
                continue
            antes = valor(referencia, r)
            if antes is None:
                salida["sin_evaluar"].append({"id": r["id"], "motivo": "medida desconocida"})
                continue
            cambio = ahora - antes
            if r["modo"] == "cambio":
                medido = cambio
            elif antes == 0:
                # De 0 a algo no es un porcentaje: «+∞%» no ayuda a nadie.
                salida["sin_evaluar"].append(
                    {"id": r["id"], "motivo": "el valor anterior es 0: no hay porcentaje"})
                continue
            else:
                medido = round(100 * cambio / antes, 1)
        salida["evaluadas"] += 1
        if OPERADORES[r["operador"]](medido, r["umbral"]):
            salida["disparadas"].append(_alerta_propia(r, ahora, antes, medido))
    return salida


def _alerta_propia(r, ahora, antes, medido) -> dict[str, Any]:
    if r["modo"] == "valor":
        texto = f"{r['nombre']}: {_fmt(ahora)} ({frase(r)})"
    else:
        signo = "+" if medido > 0 else ""
        unidad = "%" if r["modo"] == "cambio_pct" else ""
        texto = (f"{r['nombre']}: de {_fmt(antes)} a {_fmt(ahora)} "
                 f"({signo}{_fmt(medido)}{unidad}; regla: {frase(r)})")
    return {"regla": f"cliente:{r['id']}", "id": r["id"], "nombre": r["nombre"],
            "severidad": r["severidad"], "avisar": r["avisar"], "medida": r["medida"],
            "segmento": r.get("segmento"), "modo": r["modo"], "operador": r["operador"],
            "umbral": r["umbral"], "valor": ahora, "antes": antes, "medido": medido,
            "texto": texto, "frase": frase(r)}


def a_avisar(e: dict[str, Any] | None, alertas_comparacion: list[dict[str, Any]],
             propias: list[dict[str, Any]], hoy: date | None = None) -> list[dict[str, Any]]:
    """Las alertas que salen por el webhook. Sin reglas del cliente: las
    criticas de la comparacion, que es lo de siempre (decision 77)."""
    if e is None:
        return [a for a in alertas_comparacion if a.get("severidad") == "critical"]
    if silenciado(e, hoy):
        return []
    comp = e.get("comparacion") or {}
    salida = [a for a in alertas_comparacion
              if (comp.get(a.get("regla")) or {}).get(
                  "avisar", a.get("severidad") == "critical")]
    return salida + [a for a in propias if a.get("avisar")]
