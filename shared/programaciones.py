"""Programar rastreos: cuándo toca el siguiente y si hay que lanzarlo ya.

Vive en `shared/` porque lo necesitan los dos: la API, que crea y edita
programaciones, y el worker, que es quien las dispara.

Las decisiones de criterio, cada una evitando una forma de fallar en silencio:

1. **La hora la calcula una librería, no yo.** Las expresiones cron tienen
   casos que no se adivinan —el día 31 en meses de 30, el domingo de cambio de
   hora en el que las 2:30 no existe o existe dos veces—, y una programación
   que se salta una noche al año no se nota hasta que alguien busca el censo
   que falta. `CronTrigger` de APScheduler ya los resuelve.
2. **Lo que se dispara es un `encolar()`, no un rastreo.** Así el FIFO
   (decisión 31), el candado del análisis y el vigilante de estancamientos
   siguen mandando igual que con un rastreo lanzado a mano. Un planificador que
   llamara al spider por su cuenta tendría que reimplementarlos o ignorarlos.
3. **No se encola si el anterior sigue vivo.** Un rastreo semanal que tarda
   nueve días encolaría el siguiente encima del que corre, y en una máquina de
   2 vCPU eso no es «va más lento», es que se caen los dos.
4. **Un disparo perdido se lanza tarde, dentro de una ventana.** El worker se
   para por dos cosas medidas: un despliegue (2-3 minutos) y un reinicio del
   contenedor. Saltarse el rastreo por eso cuesta una semana de comparación.
   Pasada la ventana ya no se recupera: encolar de golpe lo de tres días es
   peor que empezar limpio. La ventana es un juicio, no una medida.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Cuánto puede llegar tarde un disparo y seguir lanzándose. Doce horas cubre
# de sobra lo que de verdad para al worker (un despliegue, un reinicio) sin
# recuperar lo de un fin de semana caído. Es un juicio, no una medida.
GRACIA_HORAS = float(os.getenv("PROGRAMACION_GRACIA_HORAS", "12"))

ZONA_POR_DEFECTO = os.getenv("PROGRAMACION_ZONA", "Europe/Madrid")


def _zona(nombre: str | None):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(nombre or ZONA_POR_DEFECTO)
    except Exception:
        logger.warning("Zona horaria desconocida %r; se usa UTC", nombre)
        return timezone.utc


def cron_valido(expresion: str, zona: str | None = None) -> bool:
    """Si la expresión se puede interpretar. La API la valida antes de guardar."""
    try:
        disparador(expresion, zona)
    except Exception:
        return False
    return True


# El día de la semana en cron de toda la vida: 0 y 7 son domingo, 1 es lunes.
_DIAS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _dia_de_semana(campo: str) -> str:
    """Traduce el día de la semana de cron al de APScheduler.

    **`CronTrigger.from_crontab` NO interpreta el cron de toda la vida.** Su
    numeración es 0=lunes, así que `0 3 * * 1` —«lunes a las 3:00» en cualquier
    crontab— le sale MARTES, y el 7 (domingo, válido en cron) lo rechaza.
    Reproducido el 2026-10-09 contra `croniter`, que sí implementa el estándar:

        campo  from_crontab   cron real
          0      lunes         domingo
          1      MARTES        lunes
          6      domingo       sábado
          7      error         domingo

    Es el fallo perfecto para no enterarse: la programación se dispara, solo
    que un día tarde, todas las semanas. Por eso los números se convierten a
    nombres (`mon`, `tue`…), que sí significan lo mismo en los dos.
    """
    campo = (campo or "*").strip().lower()
    if campo in ("*", "?") or any(c.isalpha() for c in campo):
        return campo  # `*`, `mon`, `mon-fri`: ya significan lo mismo

    def nombre(numero: str) -> str:
        n = int(numero)
        if not 0 <= n <= 7:
            raise ValueError(f"dia de la semana fuera de rango: {numero}")
        return _DIAS[n % 7]  # el 7 es domingo, igual que el 0

    # `*/2` significa los dias 0,2,4,6 del estandar: hay que desplegarlo antes
    # de traducir, porque un paso sobre otra numeracion no es el mismo dia.
    if campo.startswith("*/"):
        paso = int(campo[2:])
        return ",".join(nombre(str(d)) for d in range(0, 7, paso))

    partes = []
    for trozo in campo.split(","):
        if "-" in trozo:
            desde, hasta = trozo.split("-", 1)
            partes.append(f"{nombre(desde)}-{nombre(hasta)}")
        else:
            partes.append(nombre(trozo))
    return ",".join(partes)


def disparador(expresion: str, zona: str | None = None):
    """El `CronTrigger` de una expresión de cinco campos (`m h dom mes dsem`).

    No se usa `from_crontab`: su día de la semana no es el de cron. Ver
    `_dia_de_semana`.
    """
    from apscheduler.triggers.cron import CronTrigger

    campos = (expresion or "").strip().split()
    if len(campos) != 5:
        raise ValueError(
            f"una expresion cron tiene cinco campos, no {len(campos)}: "
            f"minuto hora dia-del-mes mes dia-de-semana")
    minuto, hora, dia, mes, dsem = campos
    return CronTrigger(
        minute=minuto, hour=hora, day=dia, month=mes,
        day_of_week=_dia_de_semana(dsem), timezone=_zona(zona),
    )


def proxima_desde(expresion: str, zona: str | None, desde: datetime) -> datetime | None:
    """Cuándo toca la siguiente vez después de `desde`, en UTC.

    `desde` tiene que llevar zona: una hora sin zona no se puede comparar con
    una expresión cron que sí la tiene, y el error aparecería como un desfase
    de dos horas en verano y una en invierno.
    """
    if desde.tzinfo is None:
        raise ValueError("`desde` tiene que llevar zona horaria")
    # `desde` va como "el disparo anterior", no como "ahora": asi la siguiente
    # es ESTRICTAMENTE posterior. Con `(None, desde)`, APScheduler incluye el
    # instante exacto —a las 12:00:00 en punto, `*/15` devuelve las 12:00:00—,
    # y como al disparar se recalcula desde la hora actual, un disparo que
    # cayera justo en el minuto se quedaria con su propia hora y se repetiria
    # en cada vuelta del bucle. El cron de toda la vida tambien es estricto.
    siguiente = disparador(expresion, zona).get_next_fire_time(desde, desde)
    return siguiente.astimezone(timezone.utc) if siguiente else None


def toca_lanzar(
    proxima: datetime | None, ahora: datetime, *, gracia_horas: float = GRACIA_HORAS
) -> tuple[bool, str]:
    """Si toca lanzar ya, y por qué no cuando no.

    Devuelve `(lanzar, motivo)`. El motivo se registra en el log: una
    programación que deja de dispararse sin decir por qué es indistinguible de
    una que nadie miró.
    """
    if proxima is None:
        return False, "sin proxima ejecucion calculada"
    if proxima.tzinfo is None:
        raise ValueError("`proxima` tiene que llevar zona horaria")
    if ahora < proxima:
        return False, "todavia no toca"
    retraso = ahora - proxima
    if retraso > timedelta(hours=gracia_horas):
        return False, (
            f"disparo perdido por {retraso}: pasa de la ventana de "
            f"{gracia_horas} h y se espera al siguiente"
        )
    return True, (f"toca (con {retraso} de retraso)" if retraso > timedelta(seconds=60)
                  else "toca")


VIVOS = ("pending", "running", "analyzing")


def hay_rastreo_vivo(db, programacion) -> bool:
    """Si esta programación ya tiene un rastreo sin terminar, o lo tiene su
    perfil (lanzado a mano o por otra programación del mismo perfil).

    Un rastreo semanal que tarda nueve dias encolaria el siguiente encima del
    que corre. En una maquina de 2 vCPU eso no es ir mas lento: es que se caen
    los dos. Y con el perfil como unidad (decision 193), dos rastreos vivos del
    mismo perfil son el mismo sitio rastreado dos veces a la vez.
    """
    from shared.models import Job

    if programacion.ultimo_job_id:
        estado = (
            db.query(Job.status).filter(Job.id == programacion.ultimo_job_id).scalar()
        )
        if estado in VIVOS:
            return True
    perfil = getattr(programacion, "profile_id", None)
    if perfil is None:
        return False
    return db.query(Job.id).filter(
        Job.profile_id == perfil, Job.status.in_(VIVOS)).first() is not None


def job_desde_programacion(programacion) -> dict[str, Any]:
    """El job que crea una programación, con su nombre fechado.

    El nombre lleva la fecha porque la lista de rastreos se mira por nombre:
    veinte filas iguales llamadas «progym (semanal)» no se distinguen, y la
    comparación entre censos necesita que se sepa cuál es cuál.
    """
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "name": f"{programacion.nombre} · {hoy}",
        "client_id": programacion.client_id,
        "cliente_id": getattr(programacion, "cliente_id", None),
        "profile_id": getattr(programacion, "profile_id", None),
        "seeds": list(programacion.seeds or []),
        "config": dict(programacion.config or {}),
    }


# ---------------------------------------------------------------------------
# El perfil como unidad (decision 193, revision de UX del 25-oct, 2.2)
# ---------------------------------------------------------------------------
# Lo que dice el worker cuando una programacion colgada de un perfil no se
# puede lanzar. Van a `ultimo_motivo` y la interfaz los traduce (fmt.motivo).
SIN_PERFIL = "su perfil ya no existe"
PERFIL_DESACTIVADO = "el perfil esta desactivado"
PERFIL_SIN_SEMILLAS = "el perfil no tiene semillas"
# `incidentes.motivo_cierre` de un incidente que se cerro al unir la serie de
# una programacion a la de su perfil (ya habia uno vivo igual en el perfil).
SERIE_UNIDA = "serie_unida"

_DEFECTO: dict | None = None


def config_por_defecto() -> dict[str, Any]:
    """Los valores por defecto de `JobConfig`, para el worker.

    La imagen del worker no lleva la API (ni Pydantic), y un job lanzado por la
    API sale con la config entera, defaults incluidos. Para que un disparo
    salga igual que un rastreo lanzado a mano desde el perfil, la base es esta
    copia en `shared/config_por_defecto.json`; un test la compara con
    `JobConfig()` y falla en cuanto se desfasen.
    """
    global _DEFECTO
    if _DEFECTO is None:
        import json
        from pathlib import Path

        _DEFECTO = json.loads(
            (Path(__file__).with_name("config_por_defecto.json")).read_text("utf-8"))
    import copy

    return copy.deepcopy(_DEFECTO)


def config_del_momento(cliente, perfil, propia: dict | None) -> dict[str, Any]:
    """defaults <- cliente <- perfil <- lo propio de la programacion, AHORA.

    Mismo orden que `api.routers.clientes.resolver_config`. Primero se funden
    las tres capas y despues se ponen debajo los defaults: un `None` de una
    capa quita lo heredado y deja el valor por defecto, como en la API.
    """
    from shared.clientes import fusionar_config

    capas = fusionar_config(
        (getattr(cliente, "config_defecto", None) if cliente is not None else None) or {},
        (perfil.config if perfil is not None else None) or {},
        propia or {},
    )
    return fusionar_config(config_por_defecto(), capas)


def resolver_disparo(db, programacion) -> tuple[dict[str, Any] | None, str | None]:
    """El job que toca crear ahora, o por que no se puede.

    Devuelve `(datos, None)` o `(None, motivo)`. Una programacion de copia
    fija sale como siempre (`job_desde_programacion`). Una colgada de un
    perfil lee el perfil y el cliente EN ESTE MOMENTO: sus semillas y su
    configuracion son las de hoy, no las del dia en que se programo.
    """
    if not getattr(programacion, "hereda_perfil", False):
        return job_desde_programacion(programacion), None

    from shared.models import Client, CrawlProfile

    perfil = (db.get(CrawlProfile, programacion.profile_id)
              if programacion.profile_id else None)
    if perfil is None:
        return None, SIN_PERFIL
    if not perfil.activo:
        return None, PERFIL_DESACTIVADO
    semillas = [u for u in (perfil.seeds or []) if u]
    if not semillas:
        return None, PERFIL_SIN_SEMILLAS
    cliente = db.get(Client, perfil.cliente_id)
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "name": f"{programacion.nombre} · {hoy}",
        "client_id": cliente.slug if cliente is not None else programacion.client_id,
        "cliente_id": perfil.cliente_id,
        "profile_id": perfil.id,
        "seeds": semillas,
        "config": config_del_momento(cliente, perfil, programacion.config),
    }, None


def semillas_efectivas(programacion, perfiles: dict | None = None) -> list[str]:
    """Las semillas que lanzaria hoy: las del perfil si hereda, las suyas si no."""
    if getattr(programacion, "hereda_perfil", False):
        perfil = (perfiles or {}).get(programacion.profile_id)
        return list(perfil.seeds or []) if perfil is not None else []
    return list(programacion.seeds or [])


def repetidas(programaciones, perfiles: dict | None = None) -> dict[Any, list[dict[str, Any]]]:
    """Las programaciones ACTIVAS que repiten trabajo de otra activa: mismo
    perfil o mismas semillas (V06: Lopesan tuvo dos semanales activas que
    rastreaban lo mismo y hacian dos series iguales).

    Devuelve `{id: [{"id", "nombre", "motivo"}]}` solo para las que tienen
    alguna repetida. No bloquea nada: dos del mismo perfil pueden ser a
    proposito (un canario diario y un censo semanal); se avisa y decide quien
    lo ve.
    """
    activas = [p for p in programaciones if p.activa]
    claves = {p.id: frozenset(semillas_efectivas(p, perfiles)) for p in activas}
    salida: dict[Any, list[dict[str, Any]]] = {}
    for a in activas:
        for b in activas:
            if a.id == b.id:
                continue
            motivo = None
            if a.profile_id and a.profile_id == b.profile_id:
                motivo = "mismo perfil"
            elif claves[a.id] and claves[a.id] == claves[b.id]:
                motivo = "mismas semillas"
            if motivo:
                salida.setdefault(a.id, []).append(
                    {"id": b.id, "nombre": b.nombre, "motivo": motivo})
    return salida


def unir_serie_al_perfil(db, programacion, perfil) -> dict[str, int]:
    """Cuelga del perfil lo que ya lanzo una programacion de copia fija: sus
    rastreos y sus incidentes pasan a la serie «perfil:<id>».

    Sin esto, migrar una programacion partiria su historia en dos: los puntos
    viejos seguirian en «programacion:<id>» y los nuevos en «perfil:<id>», y la
    grafica enseñaria dos series del mismo sitio (lo que ya pasaba en V06).

    - Rastreos: los suyos sin perfil reciben `profile_id` (y `cliente_id` si no
      tenian). Los que ya tenian otro perfil no se tocan.
    - Incidentes: los de su serie pasan a la del perfil. Si el perfil ya tiene
      vivo uno con la misma clave, el de la programacion se cierra con motivo
      «serie_unida» (sin afirmar que el fallo se fue) para no dejar dos vivos.
    No hace commit. Devuelve cuantos movio.
    """
    from shared.incidentes import CERRADO, VIVOS as INC_VIVOS
    from shared.models import CrawlProfile, Incidente, Job

    n_jobs = (db.query(Job)
              .filter(Job.programacion_id == programacion.id, Job.profile_id.is_(None))
              .update({Job.profile_id: perfil.id}, synchronize_session=False))
    db.query(Job).filter(Job.programacion_id == programacion.id,
                         Job.cliente_id.is_(None)).update(
        {Job.cliente_id: perfil.cliente_id}, synchronize_session=False)

    vieja, nueva = f"programacion:{programacion.id}", f"perfil:{perfil.id}"
    vivas_perfil = {i.clave for i in db.query(Incidente).filter(
        Incidente.serie == nueva, Incidente.estado.in_(INC_VIVOS))}
    ahora = datetime.now(timezone.utc)
    n_inc = n_cerrados = 0
    for inc in db.query(Incidente).filter(Incidente.serie == vieja).all():
        if inc.estado in INC_VIVOS:
            if inc.clave in vivas_perfil:
                inc.estado = CERRADO
                inc.cerrado_en = ahora
                inc.motivo_cierre = SERIE_UNIDA
                n_cerrados += 1
            else:
                vivas_perfil.add(inc.clave)
        inc.serie = nueva
        inc.nombre_serie = perfil.nombre
        inc.actualizado_en = ahora
        n_inc += 1
    db.flush()

    ultimo = (db.query(Job.id).filter(Job.profile_id == perfil.id)
              .order_by(Job.created_at.desc()).first())
    if ultimo is not None:
        db.query(CrawlProfile).filter(CrawlProfile.id == perfil.id).update(
            {CrawlProfile.last_job_id: ultimo[0]}, synchronize_session=False)
    return {"rastreos": n_jobs, "incidentes": n_inc, "incidentes_cerrados": n_cerrados}

