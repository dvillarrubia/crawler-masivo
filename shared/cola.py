"""La cola de rastreos: un solo sitio donde se decide el orden.

`rpush` + `brpop` trabajan sobre el MISMO extremo de la lista, asi que la cola
era una pila: con varios jobs esperando, el ultimo creado adelantaba a todos
los demas, y lo recuperado tras un reinicio se ponia delante de lo que llevaba
horas esperando. Nadie lo habia notado porque en el uso normal hay un job a la
vez.

Aqui el orden es FIFO y se cumple para TODO, incluidas las reanudaciones y lo
que se recupera tras un reinicio: quien lleva mas tiempo esperando, entra
antes. Reanudar un rastreo es continuar trabajo ya empezado, pero adelantarlo
significaria que un job que se atasca y se reencola varias veces (hasta
`STALL_AUTO_RESUME`) pisa indefinidamente a los que esperan.

El nombre de la cola estaba escrito a mano en cinco sitios.
"""

from __future__ import annotations

COLA_JOBS = "jobs:pending"


def encolar(conexion, job_id) -> None:
    """Pone un job al FINAL de la cola."""
    conexion.rpush(COLA_JOBS, str(job_id))


def siguiente(conexion, timeout: int):
    """Saca el job que lleva mas tiempo esperando, o None al agotar el timeout.

    `blpop` saca por la CABEZA, que es el extremo opuesto al que escribe
    `encolar`: ahi esta la diferencia entre una cola y una pila.
    """
    resultado = conexion.blpop(COLA_JOBS, timeout=timeout)
    if not resultado:
        return None
    _clave, job_id = resultado
    return job_id


def pendientes(conexion) -> int:
    """Cuantos jobs esperan. Para logs y para la interfaz."""
    try:
        return int(conexion.llen(COLA_JOBS))
    except Exception:
        return 0
