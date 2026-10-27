"""Alertas con estado: abierto, visto, cerrado (Ola 5.1, decision 139).

Una alerta sin estado se repite en cada rastreo mientras dure el fallo: el
canonical de Lopesan a `*.lfr.cloud` habria avisado cada lunes durante meses, y
un aviso que llega cada semana con lo mismo se deja de leer (decision 77).
Aqui cada alerta de una SERIE (el perfil o la programacion que repite el
rastreo) es un incidente que:

- **abre** el primer rastreo que la dispara, y solo entonces se avisa;
- **siguen** los siguientes que la disparan (`veces`, `ultimo_job_id`), sin
  avisar otra vez;
- **cierra** solo el primero que, pudiendo afirmarlo, ya no la ve.

Tres criterios, los tres para no afirmar lo que no se sabe (decisiones 67 y
89):

1. **Solo el rastreo mas reciente de la serie abre o cierra.** Re-analizar uno
   viejo, o que un rastreo atrasado termine despues que el siguiente, no
   reabre lo que ya se cerro ni cierra lo que sigue roto.
2. **No ver una alerta no es verla resuelta.** Una de la comparacion solo se
   cierra si la comparacion de este rastreo es comparable y concluyente: un
   censo truncado, detras de un muro o sin censo anterior no puede decir que
   el fallo se fue. Una regla del cliente, solo si esa regla se evaluo.
3. **Un rastreo sin serie no abre incidentes.** Uno lanzado a mano se queda con
   sus alertas en su ficha y avisa como siempre: no hay un «la semana que
   viene» con el que cerrarlo.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ABIERTO, VISTO, CERRADO = "abierto", "visto", "cerrado"
VIVOS = (ABIERTO, VISTO)


def serie_de_job(job) -> str | None:
    """«perfil:<id>» o «programacion:<id>». El perfil manda: una programacion
    que lanza un perfil es la misma serie que ese perfil lanzado a mano."""
    if getattr(job, "profile_id", None):
        return f"perfil:{job.profile_id}"
    if getattr(job, "programacion_id", None):
        return f"programacion:{job.programacion_id}"
    return None


def clave_de_alerta(a: dict[str, Any]) -> str:
    """La regla; en las de un campo extraido (decision 127) tambien su nombre,
    porque «cambia el precio» y «cambia el SKU» son dos fallos."""
    regla = str(a.get("regla") or "?")
    return f"{regla}:{a['extraccion']}" if a.get("extraccion") else regla


def titulo_de_alerta(a: dict[str, Any]) -> str:
    """La misma frase que el aviso (`shared.avisos._texto_de_alertas`): que
    pasa y sobre cuantas paginas, no el nombre de la regla. La interfaz pinta
    su propio titular a partir de `detalle`."""
    if a.get("texto"):
        return str(a["texto"])
    t = ""
    if a.get("extraccion"):
        t += f"«{a['extraccion']}»: "
    if a.get("paginas") is not None:
        t += f"{a.get('paginas')} de {a.get('de')} páginas ({a.get('pct')}%)"
    que = a.get("que_decide_google")
    return (t + (f" — {que}" if que else "")) if t else (que or str(a.get("regla") or "?"))


def _es_el_mas_reciente(db, job, serie: str) -> bool:
    from shared.models import Job

    q = db.query(Job.id).filter(Job.id != job.id, Job.created_at > job.created_at)
    tipo, _, ident = serie.partition(":")
    if tipo == "perfil":
        q = q.filter(Job.profile_id == job.profile_id)
    else:
        q = q.filter(Job.programacion_id == job.programacion_id, Job.profile_id.is_(None))
    # Un rastreo posterior que sigue en marcha tambien gana: cuando termine,
    # sera el que decida.
    return q.first() is None


def _nombre_de_serie(db, job, serie: str) -> str | None:
    from shared.models import CrawlProfile, Programacion

    tipo, _, _ = serie.partition(":")
    if tipo == "perfil":
        p = db.query(CrawlProfile).filter(CrawlProfile.id == job.profile_id).one_or_none()
        return p.nombre if p else None
    p = db.query(Programacion).filter(Programacion.id == job.programacion_id).one_or_none()
    return p.nombre if p else None


def actualizar(db, job, comparacion: dict[str, Any] | None, propias: dict[str, Any] | None,
               ahora: datetime | None = None) -> list[dict[str, Any]] | None:
    """Abre, sigue y cierra los incidentes de la serie de `job`.

    Devuelve las alertas que ABREN incidente (y no estan silenciadas): son las
    unicas que hay que avisar. None si el rastreo no lleva incidentes (sin
    serie, o no es el mas reciente): entonces se avisa como siempre... salvo
    en el segundo caso, que no avisa nada (ver el worker)."""
    from shared.models import Incidente

    serie = serie_de_job(job)
    if serie is None:
        return None
    # Un rastreo de una programacion que se pauso o se borro mientras corria
    # no abre nada ni avisa: sus incidentes se cerraron al pausarla (decision
    # 188) y, si se abrieran ahora, no los cerraria nadie. Lo que pudiera haber
    # vivo se cierra con el mismo motivo.
    motivo = _serie_sin_rastreos(db, serie)
    if motivo:
        cerrar_serie(db, serie, motivo, ahora)
        db.flush()
        logger.info("Job %s: la serie %s ya no rastrea (%s); no abre incidentes", job.id, serie, motivo)
        return []
    if not _es_el_mas_reciente(db, job, serie):
        logger.info("Job %s: hay un rastreo posterior en %s; no toca los incidentes", job.id, serie)
        return []
    ahora = ahora or datetime.now(timezone.utc)
    hoy = ahora.date()
    comparacion = comparacion if isinstance(comparacion, dict) else {}
    propias = propias if isinstance(propias, dict) else {}

    alertas = [dict(a, _origen="comparacion") for a in comparacion.get("alertas") or []]
    alertas += [dict(a, _origen="cliente") for a in propias.get("disparadas") or []]
    vistas = {clave_de_alerta(a): a for a in alertas}

    vivos = {i.clave: i for i in db.query(Incidente)
             .filter(Incidente.serie == serie, Incidente.estado.in_(VIVOS)).all()}
    nombre = None
    nuevas: list[dict[str, Any]] = []
    for clave, a in vistas.items():
        detalle = {k: v for k, v in a.items() if not k.startswith("_")}
        inc = vivos.get(clave)
        if inc is not None:
            inc.veces = (inc.veces or 1) + 1
            inc.ultimo_job_id = job.id
            inc.detalle = detalle
            inc.titulo = titulo_de_alerta(a)
            inc.severidad = a.get("severidad") or inc.severidad
            inc.actualizado_en = ahora
            continue
        if nombre is None:
            nombre = _nombre_de_serie(db, job, serie)
        db.add(Incidente(
            cliente_id=getattr(job, "cliente_id", None), serie=serie, nombre_serie=nombre,
            clave=clave, regla=str(a.get("regla") or "?"), severidad=a.get("severidad") or "warning",
            titulo=titulo_de_alerta(a), estado=ABIERTO, detalle=detalle, veces=1,
            abierto_job_id=job.id, ultimo_job_id=job.id, abierto_en=ahora, actualizado_en=ahora,
        ))
        nuevas.append(a)

    # Cerrar: solo lo que este rastreo puede afirmar que ya no esta.
    comparacion_firme = bool(comparacion.get("comparable")) and bool(comparacion.get("concluyente"))
    sin_evaluar = {r.get("id") for r in propias.get("sin_evaluar") or []}
    reglas_firmes = not propias.get("motivo") and bool(propias.get("evaluadas"))
    for clave, inc in vivos.items():
        if clave in vistas:
            continue
        if clave.startswith("cliente:"):
            firme = reglas_firmes and clave.split(":", 1)[1] not in sin_evaluar
        else:
            firme = comparacion_firme
        if firme:
            inc.estado = CERRADO
            inc.cerrado_job_id = job.id
            inc.cerrado_en = ahora
            inc.actualizado_en = ahora
    db.flush()

    # El silencio es de (serie, clave), no de una fila: si dura hasta el dia
    # 30, un fallo que se cierra el 10 y vuelve el 17 tampoco avisa. Se mira
    # cualquier incidente de esa clave, cerrado incluido.
    silenciados = {c for (c,) in db.query(Incidente.clave)
                   .filter(Incidente.serie == serie, Incidente.silenciado_hasta >= hoy)}
    return [a for a in nuevas if clave_de_alerta(a) not in silenciados]


SERIE_PAUSADA, SERIE_BORRADA = "serie_pausada", "serie_borrada"


def _serie_sin_rastreos(db, serie: str) -> str | None:
    """SERIE_PAUSADA o SERIE_BORRADA si la serie es una programacion pausada o
    que ya no existe; None si sigue (o es la de un perfil, que se puede lanzar
    a mano mientras exista: un perfil borrado ya no da rastreos con su serie)."""
    import uuid as _uuid

    from shared.models import Programacion

    tipo, _, ident = serie.partition(":")
    if tipo != "programacion":
        return None
    try:
        prog = db.get(Programacion, _uuid.UUID(ident))
    except ValueError:
        return None
    if prog is None:
        return SERIE_BORRADA
    return None if prog.activa else SERIE_PAUSADA


def cerrar_serie(db, serie: str, motivo: str, ahora: datetime | None = None) -> int:
    """Cierra los incidentes vivos de una serie que ya no va a rastrear nadie
    (decision 188): se pauso o se borro su programacion, o se borro su perfil.
    Sin esto quedaban abiertos para siempre, porque solo los cierra un rastreo
    de su serie (el criterio de arriba) y no iba a haber ninguno. El cierre
    dice por que (`motivo_cierre`) y no lleva rastreo: no afirma que el fallo
    se fuera. Si la serie vuelve (se reactiva) y el fallo sigue, el siguiente
    rastreo abre otro incidente y avisa. Devuelve cuantos cerro; no hace
    commit."""
    from shared.models import Incidente

    ahora = ahora or datetime.now(timezone.utc)
    n = 0
    for inc in db.query(Incidente).filter(Incidente.serie == serie, Incidente.estado.in_(VIVOS)):
        inc.estado = CERRADO
        inc.cerrado_en = ahora
        inc.motivo_cierre = motivo
        inc.actualizado_en = ahora
        n += 1
    if n:
        logger.info("Serie %s: %d incidentes cerrados (%s)", serie, n, motivo)
    return n


def a_dict(i) -> dict[str, Any]:
    return {
        "id": str(i.id), "cliente_id": str(i.cliente_id) if i.cliente_id else None,
        "serie": i.serie, "nombre_serie": i.nombre_serie, "clave": i.clave, "regla": i.regla,
        "severidad": i.severidad, "titulo": i.titulo, "estado": i.estado, "detalle": i.detalle,
        "veces": i.veces,
        "abierto_job_id": str(i.abierto_job_id) if i.abierto_job_id else None,
        "ultimo_job_id": str(i.ultimo_job_id) if i.ultimo_job_id else None,
        "cerrado_job_id": str(i.cerrado_job_id) if i.cerrado_job_id else None,
        "abierto_en": i.abierto_en, "visto_en": i.visto_en, "cerrado_en": i.cerrado_en,
        "motivo_cierre": getattr(i, "motivo_cierre", None),
        "actualizado_en": i.actualizado_en,
        "silenciado_hasta": i.silenciado_hasta.isoformat() if isinstance(i.silenciado_hasta, date) else None,
    }
