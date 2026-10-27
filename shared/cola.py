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


def en_orden(conexion) -> list[str]:
    """Los jobs que esperan, del primero en salir al ultimo, sin repetidos.

    Para decir en la interfaz «el 2.º en la cola» con el orden REAL de salida
    y no con `created_at`: una reanudacion o un job recuperado tras un
    reinicio se ponen al final aunque se crearan hace dias (revision de UX
    del 25-oct, 2.12). Un id repetido (un job reencolado dos veces) cuenta
    por su primera aparicion, que es la que el worker sacara antes.
    """
    vistos: list[str] = []
    for valor in conexion.lrange(COLA_JOBS, 0, -1) or []:
        texto = valor.decode() if isinstance(valor, bytes) else str(valor)
        texto = texto.strip()
        if texto and texto not in vistos:
            vistos.append(texto)
    return vistos


def pendientes(conexion) -> int:
    """Cuantos jobs esperan. Para logs y para la interfaz."""
    try:
        return int(conexion.llen(COLA_JOBS))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Analisis enriquecido (#40, decision 85)
# ---------------------------------------------------------------------------
# Una lista APARTE y no un tipo de mensaje dentro de `jobs:pending`: en la de
# rastreos todo el codigo (el bucle del worker, el vigilante, las
# reanudaciones, `pendientes()`) da por hecho que cada elemento es el id de un
# job. Meter ahi otra cosa obligaba a tocar todos esos sitios y, si alguno se
# olvidaba, un id de ejecucion acabaria lanzado como rastreo. Con dos listas
# el FIFO de cada una es el mismo de arriba y no se mezclan.
COLA_ENRIQUECIDO = "enrichment:pending"


def encolar_enriquecido(conexion, run_id) -> None:
    conexion.rpush(COLA_ENRIQUECIDO, str(run_id))


def siguiente_enriquecido(conexion):
    """Sin bloquear: el worker ya bloquea en la cola de rastreos, y si
    bloqueara tambien aqui un hueco libre de enriquecido retrasaria cada
    vuelta del bucle (y con ella los rastreos y el vigilante)."""
    valor = conexion.lpop(COLA_ENRIQUECIDO)
    return valor or None
