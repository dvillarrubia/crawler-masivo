"""Credenciales de las integraciones de un cliente: el secreto vive en el
entorno del servidor, nunca en la base de datos (decision 83).

La API no tiene autenticacion y el CORS esta abierto: cualquier cosa que se
guarde en `clients` la puede leer y cambiar cualquiera que llegue al puerto, y
viaja en cada backup. Asi que una integracion (GSC, DataForSEO, Ahrefs,
Semrush...) guarda solo:

- el tipo;
- los parametros que no son secretos (la propiedad de GSC, la ubicacion y el
  idioma de DataForSEO);
- el **nombre** de la variable de entorno que tiene la clave, por ejemplo
  `DATAFORSEO_LIN3S`.

Quien vaya a llamar al proveedor (F2+, #31, #35) pide el secreto con
`credencial(integracion)`, en el proceso que lo necesita y en el momento en que
lo necesita. La API, como mucho, dice si la variable esta puesta
(`esta_configurada`), jamas su valor.

**Cada integracion solo puede leer variables de SU espacio de nombres**
(`DATAFORSEO_*` para DataForSEO, `GSC_*` para GSC...). Sin esa regla, como
cualquiera puede escribir en `clients`, bastaria con declarar una integracion
cuya variable fuera `POSTGRES_PASSWORD` para que el codigo que llama a
DataForSEO mandara la contrasena de la base de datos como credencial. El
prefijo convierte «que variable lee» en algo que no se puede elegir mal.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

# Tipo de integracion -> prefijo obligatorio de su variable de entorno.
PREFIJOS: dict[str, str] = {
    "gsc": "GSC_",
    "dataforseo": "DATAFORSEO_",
    "ahrefs": "AHREFS_",
    "semrush": "SEMRUSH_",
    "majestic": "MAJESTIC_",
    "pagespeed": "PAGESPEED_",
    # El token del bot de Slack (decision 149): xoxb-...
    "slack": "SLACK_",
}
TIPOS = tuple(PREFIJOS)

# Un identificador de entorno de los de siempre, en mayusculas. Lo que no
# cumpla esto no es un nombre: casi seguro es el secreto pegado por error.
PATRON_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]*$")
VARIABLE_MAX = 128

# Claves de `parametros` que delatan un secreto. Si alguien escribe la clave
# de la API en un parametro, se rechaza al guardar en vez de dejarla en la
# base de datos y en los backups.
_PARECE_SECRETO = re.compile(
    r"(pass|secret|token|api_?key|apikey|credencial|credential|auth|private)",
    re.IGNORECASE,
)


class CredencialNoDisponible(RuntimeError):
    """La integracion no tiene variable, o la variable no esta en el entorno.

    El mensaje nombra la variable y nunca su valor.
    """


def error_en_variable(tipo: str, nombre: str | None) -> str | None:
    """Por que `nombre` no vale como variable de `tipo`, o None si vale."""
    if nombre is None:
        return None
    if tipo not in PREFIJOS:
        return f"tipo de integracion desconocido: {tipo!r}"
    if len(nombre) > VARIABLE_MAX or not PATRON_VARIABLE.match(nombre):
        return (
            "eso no es un nombre de variable de entorno "
            "(mayusculas, digitos y _, empezando por letra). Aqui va el NOMBRE "
            "de la variable, no el secreto: el secreto se pone en el .env del "
            "servidor"
        )
    prefijo = PREFIJOS[tipo]
    if not nombre.startswith(prefijo) or nombre == prefijo:
        return (
            f"la variable de una integracion {tipo} tiene que empezar por "
            f"{prefijo} (por ejemplo {prefijo}NOMBRECLIENTE): asi una "
            "integracion no puede leer variables que no son suyas"
        )
    return None


def parametro_sospechoso(parametros: Mapping[str, Any] | None) -> str | None:
    """La primera clave de `parametros` que tiene pinta de secreto."""
    for clave in (parametros or {}):
        if _PARECE_SECRETO.search(str(clave)):
            return str(clave)
    return None


def _variable(integracion: Mapping[str, Any]) -> str | None:
    tipo = integracion.get("tipo")
    nombre = integracion.get("credencial_env")
    if not nombre:
        return None
    # Se vuelve a validar al LEER, no solo al guardar: la base de datos se
    # puede editar a mano, y un nombre fuera de su prefijo no se lee nunca.
    if error_en_variable(tipo, nombre):
        return None
    return nombre


def esta_configurada(integracion: Mapping[str, Any], entorno: Mapping[str, str] | None = None) -> bool:
    """Si la variable de la integracion existe y no esta vacia en ESTE proceso.

    Ojo: es el entorno del proceso que pregunta. La API y el worker leen el
    mismo `.env` en el compose, pero un `docker run` suelto puede no tenerlo.
    """
    nombre = _variable(integracion)
    if not nombre:
        return False
    entorno = os.environ if entorno is None else entorno
    if entorno.get(nombre):
        return True
    # O subida desde la interfaz a `SECRETOS_DIR` (decision 141).
    from shared import secretos

    return secretos.existe(nombre)


def credencial(integracion: Mapping[str, Any], entorno: Mapping[str, str] | None = None) -> str:
    """El secreto de la integracion, leido del entorno.

    Lanza `CredencialNoDisponible` si no hay variable declarada o no esta
    puesta. No devuelve None a proposito: una llamada a un proveedor sin
    credencial falla igual, pero mas lejos y con un 401 que no dice que
    variable faltaba.

    Formato del valor: lo que pida el proveedor. Para DataForSEO, que
    autentica con usuario y contrasena, `login:password`.
    """
    nombre = _variable(integracion)
    if not nombre:
        raise CredencialNoDisponible(
            f"la integracion {integracion.get('tipo')!r} no declara una variable "
            "de entorno valida")
    entorno = os.environ if entorno is None else entorno
    valor = entorno.get(nombre)
    if not valor:
        # La subida desde la interfaz (decision 141): un fichero en el
        # volumen de secretos con el mismo nombre. Manda la variable si
        # existen las dos: la puso a mano el operador.
        from shared import secretos

        valor = secretos.leer(nombre)
    if not valor:
        raise CredencialNoDisponible(
            f"la credencial {nombre} no esta ni en el entorno de este proceso ni "
            "subida (se define en el .env del servidor o se sube desde la ficha "
            "del cliente)")
    return valor


def integracion_de(cliente, tipo: str) -> dict | None:
    """La primera integracion de `tipo` del cliente, o None."""
    for integ in (getattr(cliente, "integraciones", None) or []):
        if isinstance(integ, dict) and integ.get("tipo") == tipo:
            return integ
    return None
