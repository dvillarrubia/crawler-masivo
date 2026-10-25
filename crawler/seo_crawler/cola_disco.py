"""Cola FIFO de URLs en un fichero temporal (decision 186).

Las URLs de los sitemaps que no caben en la cola de Scrapy esperan aqui, una
por linea, y se sueltan por tandas. En memoria, una peticion encolada cuesta
~0,7 KB mas su huella en el dupefilter; aqui, nada: lo que queda en RAM es el
desplazamiento de lectura. El fichero se borra solo al cerrarse.
"""

from __future__ import annotations

import tempfile


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
