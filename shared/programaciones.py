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


def hay_rastreo_vivo(db, programacion) -> bool:
    """Si esta programación ya tiene un rastreo sin terminar.

    Un rastreo semanal que tarda nueve dias encolaria el siguiente encima del
    que corre. En una maquina de 2 vCPU eso no es ir mas lento: es que se caen
    los dos.
    """
    from shared.models import Job

    if not programacion.ultimo_job_id:
        return False
    estado = (
        db.query(Job.status).filter(Job.id == programacion.ultimo_job_id).scalar()
    )
    return estado in ("pending", "running", "analyzing")


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
        "seeds": list(programacion.seeds or []),
        "config": dict(programacion.config or {}),
    }
