"""Cola FIFO de URLs en un fichero temporal, y el conjunto compacto de sus
claves (decision 186).

Las URLs de los sitemaps que no caben en la cola de Scrapy esperan aqui, una
por linea, y se sueltan por tandas. En memoria, una peticion encolada cuesta
~0,7 KB mas su huella en el dupefilter; aqui, nada: lo que queda en RAM es el
desplazamiento de lectura. El fichero se borra solo al cerrarse.
"""

from __future__ import annotations

import heapq
import tempfile
from array import array
from bisect import bisect_left


class ColaEnDisco:
    def __init__(self) -> None:
        self._f = None
        self._leido = 0
        self._escritas = 0
        self._sacadas = 0

    def __len__(self) -> int:
        return self._escritas - self._sacadas

    @property
    def escritas(self) -> int:
        return self._escritas

    def meter(self, url: str) -> None:
        if self._f is None:
            self._f = tempfile.TemporaryFile(prefix="sitemap-pendientes-")
        self._f.seek(0, 2)
        # Una URL normalizada no lleva saltos de linea; si alguna los trajera,
        # partiria la cola en dos URLs falsas.
        self._f.write(url.replace("\n", "").replace("\r", "").encode("utf-8") + b"\n")
        self._escritas += 1

    def sacar(self, n: int) -> list[str]:
        if self._f is None or not len(self):
            return []
        self._f.seek(self._leido)
        salida = []
        while len(salida) < n:
            linea = self._f.readline()
            if not linea:
                break
            salida.append(linea.rstrip(b"\n").decode("utf-8"))
        self._leido = self._f.tell()
        self._sacadas += len(salida)
        return salida

    def cerrar(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None


class ClavesCompactas:
    """Conjunto de enteros de 64 bits a ~8 bytes por clave: un array ordenado
    (busqueda binaria) y un set con las recien llegadas, que se funde en el
    array cuando pasa de la cuarta parte de el. Un `set` de Python son ~90
    bytes por clave: con millones de URLs en los sitemaps, cientos de MB.

    Lo usa el spider para saber si un enlace apunta a una URL del sitemap que
    espera en disco (y entonces va con la profundidad 1 del sitemap, no la del
    enlace) y para no escribir dos veces la misma."""

    def __init__(self, fundir_desde: int = 50000) -> None:
        self._orden: array = array("Q")
        self._nuevas: set[int] = set()
        self._fundir_desde = fundir_desde

    def __len__(self) -> int:
        return len(self._orden) + len(self._nuevas)

    def __contains__(self, clave: int) -> bool:
        if clave in self._nuevas:
            return True
        i = bisect_left(self._orden, clave)
        return i < len(self._orden) and self._orden[i] == clave

    def anadir(self, clave: int) -> bool:
        """True si la clave no estaba."""
        if clave in self:
            return False
        self._nuevas.add(clave)
        if len(self._nuevas) >= max(self._fundir_desde, len(self._orden) // 4):
            self._fundir()
        return True

    def _fundir(self) -> None:
        # heapq.merge a un array nuevo: sin la lista intermedia de enteros
        # (~36 bytes cada uno), el pico es el array viejo mas el nuevo.
        self._orden = array("Q", heapq.merge(self._orden, sorted(self._nuevas)))
        self._nuevas.clear()
