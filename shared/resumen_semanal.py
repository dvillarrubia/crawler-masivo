"""Resumen semanal de un cliente (Ola 5.2, decision 135).

Las alertas avisan de lo que se rompe; el resumen contesta la otra pregunta
del seguimiento: «¿como ha ido la semana?». Por cada perfil propio y activo
del cliente que se rastrea (programado, o lanzado a mano esa semana), su
ultimo rastreo de la semana: si termino bien, sus alertas
(las de la comparacion y las reglas propias), las incidencias nuevas y las
resueltas frente al censo anterior, y cuatro cifras con su cambio. Y lo que
NO paso: un perfil PROGRAMADO sin rastreo en toda la semana es un hallazgo
(una programacion parada no avisa de nada por si sola). Uno sin programacion
—una lista de QA que se lanza tras cada despliegue— no tiene por que correr
cada semana: sale si se lanzo y, si no, no dice nada.

Lo genera el worker (`_quizas_resumir`) el dia y la hora que diga el cliente,
lo guarda en `resumenes_semanales` y, si hay webhook, lo manda. Lo que no se
puede afirmar se dice con su motivo, igual que en la comparacion: las
resueltas solo si la comparacion pudo afirmarlas (decision 89), y las cifras
solo frente a un censo comparable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from shared.reglas import _referencia_valida, efectivas, silenciado

logger = logging.getLogger(__name__)

TERMINADOS = ("completed", "failed", "stalled", "cancelled")
# Cuanto puede llegar tarde un resumen: lo mismo que un disparo perdido de una
# programacion (decision 75). Cubre un despliegue o un reinicio, no un fin de
# semana caido: un resumen del lunes que llega el miercoles ya no es de la
# semana.
GRACIA = timedelta(hours=12)

CIFRAS = ("urls", "indexables", "errores", "urls_con_error")
ETIQUETAS = {"urls": "URLs", "indexables": "indexables", "errores": "errores",
             "urls_con_error": "URLs con error"}


def _zona(nombre: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(nombre or "Europe/Madrid")
    except Exception:
        return ZoneInfo("Europe/Madrid")


def ultimo_corte(dia: int, hora: int, zona: str | None, ahora: datetime) -> datetime:
    """El ultimo (dia de la semana ISO, hora) en la zona del cliente que no es
    posterior a `ahora`. Devuelve un datetime con zona."""
    tz = _zona(zona)
    local = ahora.astimezone(tz)
    candidato = local.replace(hour=hora, minute=0, second=0, microsecond=0)
    candidato -= timedelta(days=(candidato.isoweekday() - dia) % 7)
    if candidato > local:
        candidato -= timedelta(days=7)
    return candidato


def toca(cliente, ahora: datetime, db=None) -> datetime | None:
    """El corte de esta semana si toca generarlo ahora (dentro de la gracia),
    o None. No mira si ya existe: eso lo decide la restriccion unica."""
    e = efectivas(getattr(cliente, "reglas_alerta", None))
    rs = e["resumen_semanal"]
    if not rs.get("activo"):
        return None
    if not _tiene_serie(cliente, db):
        # Sin un perfil propio y activo ni una programacion activa no hay
        # semana que resumir: un «sin alertas» de un cliente que no se rastrea
        # es ruido.
        return None
    corte = ultimo_corte(int(rs["dia"]), int(rs["hora"]),
                         getattr(cliente, "zona_horaria", None), ahora)
    return corte if ahora - corte <= GRACIA else None


def _tiene_serie(cliente, db=None) -> bool:
    """Si el cliente tiene algo que se rastrea solo: una programacion activa,
    vaya por un perfil o no. En produccion, el 18-oct-2026, ningun cliente
    tenia perfiles y los censos semanales iban por programacion: contando solo
    perfiles, el resumen no habria salido para nadie. Y un perfil sin
    programacion tampoco basta: se lanza a mano, y un «sin alertas» semanal de
    algo que nadie ha lanzado es ruido."""
    if db is None:
        return False
    return bool(_perfiles_programados(db, cliente) or _programaciones_sueltas(db, cliente))


def _perfiles_programados(db, cliente) -> set:
    """Los perfiles del cliente con alguna programacion activa: los que se
    espera ver rastreados cada semana."""
    from shared.models import Programacion

    filas = (db.query(Programacion.profile_id)
             .filter(Programacion.cliente_id == cliente.id, Programacion.activa.is_(True),
                     Programacion.profile_id.isnot(None)).all())
    return {f[0] for f in filas}


def _programaciones_sueltas(db, cliente) -> list:
    """Las programaciones activas del cliente que no van por un perfil (las
    que si, ya salen con su perfil)."""
    from shared.models import Programacion

    return (db.query(Programacion)
            .filter(Programacion.cliente_id == cliente.id, Programacion.activa.is_(True),
                    Programacion.profile_id.is_(None))
            .order_by(Programacion.nombre).all())


def _fila_evolucion(db, job_id) -> dict[str, Any] | None:
    from shared.models import ProfileRun

    if not job_id:
        return None
    fila = db.query(ProfileRun).filter(ProfileRun.job_id == job_id).one_or_none()
    if fila is None:
        return None
    return {k: getattr(fila, k, None) for k in CIFRAS} | {
        "analisis_version": fila.analisis_version}


def _como_uuid(v):
    import uuid

    if v is None or isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except ValueError:
        return None


def _resumen_de_rastreo(db, job) -> dict[str, Any]:
    comp = job.comparacion if isinstance(job.comparacion, dict) else None
    propias = job.alertas_cliente if isinstance(getattr(job, "alertas_cliente", None), dict) else {}
    alertas = [{"regla": a.get("regla"), "severidad": a.get("severidad"),
                "paginas": a.get("paginas"), "de": a.get("de"), "pct": a.get("pct"),
                "que_decide_google": a.get("que_decide_google")}
               for a in (comp or {}).get("alertas") or []]
    salida: dict[str, Any] = {
        "job_id": str(job.id), "nombre": job.name, "status": job.status,
        "finish_reason": job.finish_reason,
        "fecha": (job.completed_at or job.started_at or job.created_at).isoformat()
        if (job.completed_at or job.started_at or job.created_at) else None,
        "alertas": alertas,
        "reglas_cliente": [a.get("texto") for a in propias.get("disparadas") or []],
        "comparado_con": (comp or {}).get("comparado_con"),
        "comparable": bool((comp or {}).get("comparable")),
    }
    inc = (comp or {}).get("incidencias") if comp else None
    if isinstance(inc, dict) and inc.get("comparable"):
        salida["incidencias"] = {
            "nuevas": (inc.get("nuevas") or {}).get("total"),
            "resueltas": (inc.get("resueltas") or {}).get("total")
            if inc.get("resueltas") else None,
            "siguen": (inc.get("siguen") or {}).get("total"),
        }
    elif isinstance(inc, dict):
        salida["incidencias_motivo"] = inc.get("motivo")

    ahora = _fila_evolucion(db, job.id)
    # Las cifras se comparan con el mismo criterio que las reglas: nunca contra
    # un censo truncado, con otro alcance o detras de un muro.
    referencia, _motivo = _referencia_valida(job, comp)
    antes = _fila_evolucion(db, _como_uuid(referencia)) if referencia else None
    if ahora:
        cifras = {}
        for k in CIFRAS:
            v = ahora.get(k)
            cifras[k] = {"valor": v}
            if antes and antes.get(k) is not None and v is not None:
                # Entre dos analisis distintos el cambio de errores puede ser
                # nuestro (decision 91): se da la cifra, no el cambio.
                if k == "urls" or (ahora.get("analisis_version")
                                   and ahora.get("analisis_version") == antes.get("analisis_version")):
                    cifras[k]["cambio"] = v - antes[k]
        salida["cifras"] = cifras
    return salida


def componer(db, cliente, desde: datetime, hasta: datetime) -> dict[str, Any]:
    """El contenido del resumen de [desde, hasta)."""
    from shared.models import CrawlProfile, Job

    perfiles = (db.query(CrawlProfile)
                .filter(CrawlProfile.cliente_id == cliente.id)
                .order_by(CrawlProfile.nombre).all())
    fecha = Job.completed_at
    jobs = (db.query(Job)
            .filter(Job.cliente_id == cliente.id, Job.status.in_(TERMINADOS),
                    ((fecha >= desde) & (fecha < hasta))
                    | (fecha.is_(None) & (Job.created_at >= desde) & (Job.created_at < hasta)))
            .order_by(Job.created_at).all())

    programados = _perfiles_programados(db, cliente)
    salida_perfiles = []
    for p in perfiles:
        if p.rol == "competidor" or not p.activo:
            continue
        suyos = [j for j in jobs if j.profile_id == p.id]
        if not suyos and p.id not in programados:
            # A mano y sin lanzar esta semana: no es un hallazgo.
            continue
        fila: dict[str, Any] = {"perfil_id": str(p.id), "nombre": p.nombre,
                                "rastreos": len(suyos), "programado": p.id in programados}
        if suyos:
            fila["ultimo"] = _resumen_de_rastreo(db, suyos[-1])
            fila["fallidos"] = sum(1 for j in suyos if j.status != "completed")
        salida_perfiles.append(fila)

    # Una programacion sin perfil es una serie igual que un perfil: lo que se
    # rastrea cada semana sin que nadie lo lance.
    for prog in _programaciones_sueltas(db, cliente):
        suyos = [j for j in jobs if j.profile_id is None and j.programacion_id == prog.id]
        fila = {"programacion_id": str(prog.id), "nombre": prog.nombre, "rastreos": len(suyos)}
        if suyos:
            fila["ultimo"] = _resumen_de_rastreo(db, suyos[-1])
            fila["fallidos"] = sum(1 for j in suyos if j.status != "completed")
        salida_perfiles.append(fila)
    en_serie = {f.get("programacion_id") for f in salida_perfiles if f.get("programacion_id")}

    sin_perfil = [j for j in jobs if j.profile_id is None
                  and str(j.programacion_id or "") not in en_serie]
    # Lo que salto en rastreos sin perfil tambien se lee: un rastreo lanzado a
    # mano tras un despliegue es justo donde aparece un fallo.
    sueltos = []
    for j in sin_perfil:
        r = _resumen_de_rastreo(db, j)
        if any(a["severidad"] == "critical" for a in r["alertas"]) or r["reglas_cliente"]:
            sueltos.append(r)
    contenido = {
        "cliente": {"id": str(cliente.id), "nombre": cliente.nombre, "slug": cliente.slug},
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "perfiles": salida_perfiles,
        "rastreos_sin_perfil": len(sin_perfil),
        "rastreos_sin_perfil_fallidos": sum(1 for j in sin_perfil if j.status != "completed"),
        "sin_perfil_con_alertas": sueltos,
    }
    contenido["destacado"] = _destacado(contenido)
    return contenido


def _destacado(c: dict[str, Any]) -> list[str]:
    """Lo primero que hay que leer, en orden: lo critico, lo que no paso."""
    lineas: list[str] = []
    for p in c["perfiles"]:
        u = p.get("ultimo")
        if not p["rastreos"]:
            lineas.append(f"{p['nombre']}: no se ha rastreado esta semana.")
            continue
        if u["status"] != "completed":
            lineas.append(f"{p['nombre']}: el último rastreo terminó como "
                          f"«{u['status']}» ({u.get('finish_reason') or 'sin motivo'}).")
        criticas = [a for a in u["alertas"] if a["severidad"] == "critical"]
        for a in criticas:
            lineas.append(f"{p['nombre']}: {a['paginas']} de {a['de']} páginas "
                          f"({a['pct']}%) — {a['que_decide_google']}")
        for t in u.get("reglas_cliente") or []:
            lineas.append(f"{p['nombre']}: {t}")
    for u in c.get("sin_perfil_con_alertas") or []:
        for a in u["alertas"]:
            if a["severidad"] == "critical":
                lineas.append(f"{u['nombre']}: {a['paginas']} de {a['de']} páginas "
                              f"({a['pct']}%) — {a['que_decide_google']}")
        for t in u.get("reglas_cliente") or []:
            lineas.append(f"{u['nombre']}: {t}")
    return lineas


def _num(n) -> str:
    return "—" if n is None else f"{int(n):,}".replace(",", ".")


def texto(c: dict[str, Any]) -> str:
    """El resumen en texto, para el webhook (Slack, n8n → correo)."""
    # En la zona del cliente: en UTC un corte a las 0:00 de Madrid cae el dia
    # anterior y la cabecera decia una semana que no era.
    d = datetime.fromisoformat(c.get("desde_local") or c["desde"]).date()
    h = (datetime.fromisoformat(c.get("hasta_local") or c["hasta"]) - timedelta(seconds=1)).date()
    lineas = [f"*{c['cliente']['nombre']}* · resumen del {d:%d/%m} al {h:%d/%m}"]
    if c["destacado"]:
        lineas.append("")
        lineas.append("*Lo que hay que mirar:*")
        lineas += [f"• {t}" for t in c["destacado"]]
    else:
        lineas.append("Sin alertas esta semana.")
    for p in c["perfiles"]:
        u = p.get("ultimo")
        if not u:
            continue
        partes = []
        for k, v in (u.get("cifras") or {}).items():
            cambio = v.get("cambio")
            partes.append(f"{_num(v.get('valor'))} {ETIQUETAS[k]}"
                          + (f" ({'+' if cambio > 0 else ''}{_num(cambio)})" if cambio else ""))
        inc = u.get("incidencias")
        if inc:
            partes.append(f"{_num(inc.get('nuevas'))} incidencias nuevas"
                          + (f", {_num(inc['resueltas'])} resueltas"
                             if inc.get("resueltas") is not None else ""))
        lineas.append("")
        lineas.append(f"_{p['nombre']}_ · {p['rastreos']} rastreo(s): " + ("; ".join(partes) or "sin cifras"))
    if c.get("rastreos_sin_perfil"):
        lineas.append("")
        lineas.append(f"Otros rastreos del cliente: {c['rastreos_sin_perfil']}"
                      + (f" ({c['rastreos_sin_perfil_fallidos']} sin completar)"
                         if c.get("rastreos_sin_perfil_fallidos") else ""))
    return "\n".join(lineas)


def generar(db, cliente, corte: datetime) -> dict[str, Any]:
    """El resumen de la semana que termina en `corte` (sin guardar)."""
    desde = corte - timedelta(days=7)
    c = componer(db, cliente, desde.astimezone(timezone.utc), corte.astimezone(timezone.utc))
    c["desde_local"], c["hasta_local"] = desde.isoformat(), corte.isoformat()
    return {"semana": corte.date(), "desde": desde, "hasta": corte,
            "contenido": c, "texto": texto(c)}


def guardar_y_enviar(db, cliente, corte: datetime, enviar=None) -> bool:
    """Genera, guarda y (si hay webhook y no esta silenciado) manda. False si
    ya existia el de esa semana —otra replica llego antes— o no se guardo."""
    from sqlalchemy.exc import IntegrityError

    from shared.models import ResumenSemanal

    ya = db.query(ResumenSemanal).filter(ResumenSemanal.cliente_id == cliente.id,
                                         ResumenSemanal.semana == corte.date()).first()
    if ya is not None:
        # Guardado pero no enviado (webhook caido, proceso muerto entre medias):
        # se reintenta mientras dure la gracia, que es cuando se llama a esto.
        if not ya.enviado and enviar is not None and not silenciado(
                efectivas(getattr(cliente, "reglas_alerta", None)), corte.date()):
            if enviar(ya.texto, ya.contenido):
                ya.enviado = True
                db.commit()
        return False
    r = generar(db, cliente, corte)
    fila = ResumenSemanal(cliente_id=cliente.id, semana=r["semana"],
                          desde=r["desde"], hasta=r["hasta"],
                          contenido=r["contenido"], texto=r["texto"], enviado=False)
    db.add(fila)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    e = efectivas(getattr(cliente, "reglas_alerta", None))
    if enviar is not None and not silenciado(e, corte.date()):
        if enviar(r["texto"], r["contenido"]):
            fila.enviado = True
            db.commit()
    return True


