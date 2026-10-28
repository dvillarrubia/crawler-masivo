"""Cola FIFO de URLs en un fichero temporal, y el conjunto compacto de sus
claves (decision 186). Y lo que acota la memoria del resto de la frontera
(#276): el dupefilter con esas claves y un conjunto con tope.

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

from scrapy.dupefilters import RFPDupeFilter


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
    """Conjunto de enteros de 64 bits: un array ordenado (8 bytes por clave,
    busqueda binaria) y un set con las recien llegadas (~70 bytes cada una),
    que se funde en el array cuando pasa de la cuarta parte de el. Medido con
    10 millones: ~15 bytes por clave en reposo y el doble un momento al fundir
    (1,8 s). Un `set` de Python con todas son ~90 bytes por clave.

    Lo usa el spider para saber si un enlace apunta a una URL del sitemap que
    espera en disco (y entonces va con la profundidad 1 del sitemap, no la del
    enlace) y para no escribir dos veces la misma."""

    def __init__(self, fundir_desde: int = 50000) -> None:
        self._orden: array = array("Q")
        self._nuevas: set[int] = set()
        self._fundir_desde = fundir_desde

    @classmethod
    def desde(cls, claves, fundir_desde: int = 50000) -> "ClavesCompactas":
        """De un iterable de claves de una vez, sin el set intermedio: lo ya
        rastreado al reanudar (#276)."""
        conjunto = cls(fundir_desde)
        conjunto._orden = array("Q", sorted(set(claves)))
        return conjunto

    def __bool__(self) -> bool:
        return bool(self._orden) or bool(self._nuevas)

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


class DupefilterCompacto(RFPDupeFilter):
    """El dupefilter de Scrapy con 8 bytes por peticion vista (#276).

    `RFPDupeFilter` guarda la huella entera (20 bytes de sha1 en un `bytes`,
    ~90 con el set) de cada peticion que pasa, y con `JOBDIR` la escribe ademas
    en `requests.seen`, que nadie relee: cada rastreo empieza con un directorio
    nuevo (la reanudacion siembra desde la BD). Aqui van los 64 primeros bits
    en `ClavesCompactas`, ~15 bytes por clave. Dos peticiones distintas con la
    misma clave harian que la segunda no se pidiera: con un millon de URLs,
    ~3e-8 (la paradoja del cumpleanos con 2^64)."""

    @classmethod
    def from_crawler(cls, crawler):
        return cls(None, crawler.settings.getbool("DUPEFILTER_DEBUG"),
                   fingerprinter=crawler.request_fingerprinter)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._vistas = ClavesCompactas()

    def __len__(self) -> int:
        return len(self._vistas)

    def request_seen(self, request) -> bool:
        clave = int.from_bytes(self._fingerprint(request)[:8], "big")
        return not self._vistas.anadir(clave)


class RecientesAcotados:
    """Conjunto con tope que olvida lo mas antiguo (#276).

    Para lo que solo hace falta recordar un rato: los `url_id` cuyos hijos ya
    se limpiaron en esta ejecucion (los lotes de una misma pagina llegan
    seguidos) o la cache url_hash -> url_id del pipeline (un fallo cuesta una
    consulta, no un dato malo). Sin tope crecian con cada URL del rastreo."""

    def __init__(self, tope: int) -> None:
        self._tope = tope
        self._d: dict = {}

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, clave) -> bool:
        return clave in self._d

    def get(self, clave, defecto=None):
        return self._d.get(clave, defecto)

    def __setitem__(self, clave, valor) -> None:
        self._d.pop(clave, None)
        self._d[clave] = valor
        while len(self._d) > self._tope:
            del self._d[next(iter(self._d))]

    def add(self, clave) -> None:
        self[clave] = None

    def update(self, claves) -> None:
        for clave in claves:
            self[clave] = None

    def clear(self) -> None:
        self._d.clear()
