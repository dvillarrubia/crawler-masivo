"""Programar rastreos: cuándo toca y si se lanza.

Lo que de verdad hay que sujetar aquí no es «calcula bien la hora» —eso lo
hace `CronTrigger` y no es mío— sino las cuatro decisiones de criterio: qué
pasa con un disparo perdido, qué pasa si el rastreo anterior sigue vivo, que
la próxima se recalcule aunque no se haya lanzado, y que la zona horaria se
respete.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.programaciones import (
    cron_valido, proxima_desde, toca_lanzar,
)


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- El motor de tiempos ---------------------------------------------------

def test_una_expresion_cron_valida_se_acepta_y_una_rota_no():
    """La API valida antes de guardar: una expresion que no se puede
    interpretar es una programacion que no se dispara NUNCA, y eso se lee
    igual que un sitio que no cambia."""
    assert cron_valido("0 3 * * 1")           # lunes a las 3:00
    assert cron_valido("30 2 1 * *")          # dia 1 a las 2:30
    assert not cron_valido("todos los lunes")
    assert not cron_valido("0 3 * *")         # le falta un campo
    assert not cron_valido("")


@pytest.mark.parametrize("expresion", [
    "0 3 * * 0", "0 3 * * 1", "0 3 * * 5", "0 3 * * 6", "0 3 * * 7",
    "0 3 * * 1-5", "0 3 * * 1,4", "0 3 * * */2", "0 3 * * mon",
    "0 3 * * mon-fri", "0 3 1 * *", "30 2 * * *", "*/15 * * * *",
    "0 0 1 1 *", "0 3 29 2 *",
])
def test_el_dia_de_la_semana_es_el_del_cron_de_toda_la_vida(expresion):
    """Contrastado contra `croniter`, que implementa el estandar.

    `CronTrigger.from_crontab` de APScheduler NO lo implementa: su numeracion
    es 0=lunes, asi que `0 3 * * 1` —«lunes a las 3:00» en cualquier crontab—
    le sale MARTES, y el 7 (domingo, valido en cron) lo rechaza con un error.
    Reproducido el 2026-10-09:

        campo  from_crontab   cron real
          0      lunes         domingo
          1      MARTES        lunes
          6      domingo       sabado
          7      error         domingo

    Es el fallo perfecto para no enterarse: la programacion se dispara, solo
    que un dia tarde, todas las semanas, para siempre. Este test falla con
    `from_crontab` en las cinco expresiones de dia de semana numerico.
    """
    croniter = pytest.importorskip("croniter").croniter

    desde = _utc(2026, 10, 9, 12, 0)          # un viernes
    nuestra = proxima_desde(expresion, "UTC", desde)
    estandar = croniter(expresion, desde).get_next(datetime)
    assert nuestra.replace(tzinfo=None) == estandar.replace(tzinfo=None), (
        f"{expresion}: nosotros {nuestra}, cron real {estandar}")


def test_la_zona_horaria_cuenta():
    """Un «lunes a las 3:00» sin zona se desplaza una hora en verano y nadie
    lo nota hasta que falta un censo."""
    desde = _utc(2026, 7, 1, 0, 0)            # verano: Madrid es UTC+2
    en_madrid = proxima_desde("0 3 * * *", "Europe/Madrid", desde)
    en_utc = proxima_desde("0 3 * * *", "UTC", desde)
    assert en_madrid.hour == 1, "las 3:00 de Madrid son la 1:00 UTC en verano"
    assert en_utc.hour == 3
    assert en_madrid != en_utc


def test_el_domingo_del_cambio_de_hora_no_se_salta():
    """El 25 de octubre de 2026 en Madrid, las 2:30 ocurren DOS veces. Es el
    caso que no quiero resolver a mano: aqui solo se comprueba que devuelve
    una hora y que es ese dia, no que elija una u otra."""
    desde = _utc(2026, 10, 24, 12, 0)
    siguiente = proxima_desde("30 2 * * *", "Europe/Madrid", desde)
    assert siguiente is not None
    assert siguiente.astimezone(timezone.utc).date().day in (24, 25)


def test_una_hora_sin_zona_se_rechaza_en_vez_de_adivinar():
    """Comparar una hora sin zona con una expresion que si la tiene da un
    desfase de dos horas en verano y una en invierno, y no se ve."""
    with pytest.raises(ValueError):
        proxima_desde("0 3 * * *", "Europe/Madrid", datetime(2026, 7, 1))


# --- Cuándo se lanza -------------------------------------------------------

def test_todavia_no_toca():
    lanzar, motivo = toca_lanzar(_utc(2026, 1, 2, 3), _utc(2026, 1, 1, 3))
    assert lanzar is False and "todavia no toca" in motivo


def test_cuando_toca_se_lanza():
    lanzar, _ = toca_lanzar(_utc(2026, 1, 1, 3), _utc(2026, 1, 1, 3))
    assert lanzar is True


def test_un_disparo_perdido_por_poco_SI_se_lanza():
    """Es el caso que motiva la ventana: un despliegue para el worker 2-3
    minutos (medido), y saltarse el rastreo por eso cuesta una semana de
    comparacion con el censo anterior."""
    toco = _utc(2026, 1, 1, 3, 0)
    lanzar, motivo = toca_lanzar(toco, toco + timedelta(minutes=4))
    assert lanzar is True
    assert "retraso" in motivo


def test_un_disparo_perdido_por_mucho_se_abandona():
    """Tras una caida larga, encolar de golpe lo de tres dias es peor que
    empezar limpio: son varios clientes a la vez en una maquina de 2 vCPU."""
    toco = _utc(2026, 1, 1, 3, 0)
    lanzar, motivo = toca_lanzar(toco, toco + timedelta(hours=30))
    assert lanzar is False
    assert "disparo perdido" in motivo and "ventana" in motivo


def test_el_motivo_viaja_siempre():
    """Una programacion que deja de dispararse sin decir por que es
    indistinguible de una que nadie miro."""
    for proxima, ahora in [
        (None, _utc(2026, 1, 1)),
        (_utc(2026, 1, 2), _utc(2026, 1, 1)),
        (_utc(2026, 1, 1), _utc(2026, 1, 5)),
        (_utc(2026, 1, 1), _utc(2026, 1, 1)),
    ]:
        _, motivo = toca_lanzar(proxima, ahora)
        assert motivo, "sin motivo no se puede diagnosticar nada"


def test_la_proxima_es_estrictamente_posterior():
    """Si no, una programacion se queda con su propia hora y se repite.

    APScheduler, preguntado con `(None, ahora)`, INCLUYE el instante exacto:
    a las 12:00:00 en punto, `*/15 * * * *` devuelve las 12:00:00. Como al
    disparar se recalcula la proxima desde la hora actual, un disparo que
    cayera justo en el minuto dejaria `proxima_ejecucion` en su propia hora y
    volveria a lanzarse en cada vuelta del bucle, cada minuto. El cron de toda
    la vida tambien es estricto.
    """
    justo = _utc(2026, 10, 9, 12, 0, 0)
    assert proxima_desde("*/15 * * * *", "UTC", justo) == _utc(2026, 10, 9, 12, 15)
    assert proxima_desde("0 3 * * *", "UTC", _utc(2026, 10, 9, 3, 0)) == _utc(
        2026, 10, 10, 3, 0)
