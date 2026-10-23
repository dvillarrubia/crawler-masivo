"""Ancho y alto de una imagen a partir de sus primeros bytes (decision 154).

La sonda de la `og:image` pide la imagen con un `Range` y no la baja entera:
los formatos de la web llevan las dimensiones en la cabecera, casi siempre en
los primeros cientos de bytes. Un JPEG con EXIF (la miniatura de la camara, el
perfil de color) las lleva despues, y por eso el rango es de 64 KB y no de 1 KB.

Funciones puras, sin Pillow ni nada que la imagen del analisis no tenga: el
spider mide y la API podria volver a leer lo guardado (decisiones 23 y 39).

**Lo que no se puede leer no se adivina** (67): un formato que no se reconoce,
un JPEG cuyo marcador de tamano cae fuera de lo descargado o un SVG (sin
tamano intrinseco fiable) devuelven None, que es «no medido», nunca «pequena».
"""

from __future__ import annotations

import struct

# Lo que pide la sonda. Medido en 12 og:image reales (2026-10-23): 11 dicen su
# tamano antes de 1,2 KB; un JPEG de WordPress con EXIF y miniatura, a los
# 52.700 bytes. Con 1 KB, como la sonda de recursos, ese se quedaba sin medir.
BYTES_NECESARIOS = 65536

# Un ancho o alto por encima de esto es un fichero corrupto o un marcador mal
# leido, no una imagen.
_MAX_LADO = 100_000

# Marcadores SOF de JPEG que llevan el tamano (no DHT=C4, JPG=C8 ni DAC=CC).
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _valido(ancho: int, alto: int) -> tuple[int, int] | None:
    if 0 < ancho <= _MAX_LADO and 0 < alto <= _MAX_LADO:
        return ancho, alto
    return None


def formato(cuerpo: bytes) -> str | None:
    """`png`, `gif`, `jpeg`, `webp`, `avif`/`heif` o `bmp` por la firma; None si no."""
    if not cuerpo:
        return None
    if cuerpo.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cuerpo[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if cuerpo.startswith(b"\xff\xd8"):
        return "jpeg"
    if cuerpo[:4] == b"RIFF" and cuerpo[8:12] == b"WEBP":
        return "webp"
    if cuerpo[4:8] == b"ftyp":
        marca = cuerpo[8:12]
        if marca in (b"avif", b"avis"):
            return "avif"
        if marca in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
            return "heif"
    if cuerpo[:2] == b"BM":
        return "bmp"
    return None


def dimensiones(cuerpo: bytes | None) -> tuple[int, int] | None:
    """`(ancho, alto)` en pixeles, o None si no se puede leer."""
    try:
        return _dimensiones(cuerpo or b"")
    except Exception:  # noqa: BLE001 -- un fichero raro es «no medido», nunca un fallo
        return None


def _dimensiones(c: bytes) -> tuple[int, int] | None:
    fmt = formato(c)
    if fmt == "png":
        # IHDR es el primer chunk: ancho y alto en los bytes 16-24.
        if c[12:16] != b"IHDR":
            return None
        return _valido(*struct.unpack(">II", c[16:24]))
    if fmt == "gif":
        return _valido(*struct.unpack("<HH", c[6:10]))
    if fmt == "bmp":
        ancho, alto = struct.unpack("<ii", c[18:26])
        return _valido(ancho, abs(alto))
    if fmt == "webp":
        return _webp(c)
    if fmt == "jpeg":
        return _jpeg(c)
    if fmt in ("avif", "heif"):
        return _ispe(c)
    return None


def _webp(c: bytes) -> tuple[int, int] | None:
    trozo = c[12:16]
    if trozo == b"VP8 ":
        # Con perdida: tras la firma 9D 01 2A, 14 bits de ancho y de alto.
        if c[23:26] != b"\x9d\x01\x2a":
            return None
        ancho, alto = struct.unpack("<HH", c[26:30])
        return _valido(ancho & 0x3FFF, alto & 0x3FFF)
    if trozo == b"VP8L":
        if c[20] != 0x2F:
            return None
        b0, b1, b2, b3 = c[21], c[22], c[23], c[24]
        ancho = 1 + (((b1 & 0x3F) << 8) | b0)
        alto = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
        return _valido(ancho, alto)
    if trozo == b"VP8X":
        ancho = 1 + int.from_bytes(c[24:27], "little")
        alto = 1 + int.from_bytes(c[27:30], "little")
        return _valido(ancho, alto)
    return None


def _jpeg(c: bytes) -> tuple[int, int] | None:
    i = 2
    n = len(c)
    while i + 4 <= n:
        if c[i] != 0xFF:
            return None  # fuera de sincronia: no se adivina
        marcador = c[i + 1]
        if marcador == 0xFF:  # relleno
            i += 1
            continue
        if marcador in (0xD8, 0x01) or 0xD0 <= marcador <= 0xD7:
            i += 2
            continue
        if marcador in (0xD9, 0xDA):  # fin, o empiezan los datos: ya no hay SOF
            return None
        largo = struct.unpack(">H", c[i + 2:i + 4])[0]
        if marcador in _SOF:
            if i + 9 > n:
                return None
            alto, ancho = struct.unpack(">HH", c[i + 5:i + 9])
            return _valido(ancho, alto)
        if largo < 2:
            return None
        i += 2 + largo
    return None


def _ispe(c: bytes) -> tuple[int, int] | None:
    """AVIF y HEIF: la caja `ispe` (dentro de `meta`/`iprp`/`ipco`) lleva el
    tamano de la imagen. Con varias (miniatura, alfa), la mayor es la imagen."""
    mejor = None
    i = c.find(b"ispe")
    while i != -1:
        # caja: tamano(4) 'ispe' version+flags(4) ancho(4) alto(4)
        if i + 16 <= len(c):
            ancho, alto = struct.unpack(">II", c[i + 8:i + 16])
            par = _valido(ancho, alto)
            if par and (mejor is None or par[0] * par[1] > mejor[0] * mejor[1]):
                mejor = par
        i = c.find(b"ispe", i + 4)
    return mejor
