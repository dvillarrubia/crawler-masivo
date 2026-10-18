"""Secretos subidos desde la interfaz: ficheros en el servidor, nunca en la
base de datos (decision 141).

La decision 83 dejo los secretos en el entorno (`.env` del servidor) y la base
de datos con solo el NOMBRE de la variable. Una llave de cuenta de servicio de
Google es un JSON de 2 kB por cliente que alguien de la agencia descarga y
tiene que poner en el servidor: obligar a editar el `.env` por ssh para cada
cliente es lo que hace que no se configure nunca. Asi que se puede SUBIR, y va
a un fichero en `SECRETOS_DIR` (un volumen de Docker montado en la API y en el
worker), con el mismo nombre que tendria la variable (`GSC_LOPESAN.json`).

Lo que protegia la decision 83 se mantiene:

- **no esta en la base de datos** ni, por tanto, en los backups ni en lo que
  devuelve la API: `clients.integraciones` sigue guardando solo el nombre;
- **no se lee nunca de vuelta por la API**: se puede subir, sustituir, borrar y
  saber si existe; de su contenido solo se ensena lo publico (el email de la
  cuenta de servicio, que es lo que hay que anadir en Search Console);
- **cada integracion solo lee lo de su prefijo** (`GSC_*`), con la misma
  validacion de nombre que las variables (`shared/credenciales.py`).

Si existen las dos cosas, manda la variable de entorno: es la que el operador
puso a mano en el servidor.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TAM_MAXIMO = 64 * 1024


def directorio() -> Path:
    return Path(os.getenv("SECRETOS_DIR", "/secretos"))


def _ruta(nombre: str) -> Path:
    # El nombre ya viene validado (`error_en_variable`): mayusculas, digitos y
    # _. Sin barras ni puntos, no puede salirse del directorio.
    if not nombre or not nombre.replace("_", "").isalnum() or not nombre.isupper():
        raise ValueError("nombre de secreto no valido")
    return directorio() / f"{nombre}.json"


def existe(nombre: str) -> bool:
    try:
        return _ruta(nombre).is_file()
    except ValueError:
        return False


def leer(nombre: str) -> str | None:
    try:
        ruta = _ruta(nombre)
    except ValueError:
        return None
    try:
        return ruta.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return None


def guardar(nombre: str, contenido: str) -> None:
    """Escritura atomica con permisos 600: o queda el fichero entero o el de
    antes, nunca uno a medias que un worker leeria como llave rota."""
    ruta = _ruta(nombre)
    if len(contenido.encode("utf-8")) > TAM_MAXIMO:
        raise ValueError("el secreto es demasiado grande")
    ruta.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".subida-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenido)
        os.chmod(tmp, 0o600)
        os.replace(tmp, ruta)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def borrar(nombre: str) -> bool:
    try:
        _ruta(nombre).unlink()
        return True
    except (FileNotFoundError, ValueError):
        return False


def listar(prefijo: str) -> list[str]:
    """Los nombres guardados que empiezan por `prefijo` (sin `.json`)."""
    d = directorio()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob(f"{prefijo}*.json") if p.is_file())
