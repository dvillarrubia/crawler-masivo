"""Ancho y alto de una imagen por sus primeros bytes (decision 154).

Cabeceras construidas a mano, byte a byte, con la especificacion de cada
formato: lo que se comprueba es que la lectura acierta y que lo que no se puede
leer da None, nunca un tamano inventado.
"""

from __future__ import annotations

import struct

from shared.dimensiones_imagen import BYTES_NECESARIOS, dimensiones, formato


def _png(ancho: int, alto: int) -> bytes:
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", ancho, alto) + b"\x08\x02\x00\x00\x00" + b"\x00" * 4)


def _segmento(marcador: int, datos: bytes) -> bytes:
    return bytes([0xFF, marcador]) + struct.pack(">H", len(datos) + 2) + datos


def _jpeg(ancho: int, alto: int, exif: int = 0, progresivo: bool = False) -> bytes:
    cuerpo = b"\xff\xd8"
    cuerpo += _segmento(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    while exif > 0:
        # APP1 con EXIF y miniatura (y APP2 con el perfil de color): lo que
        # empuja el SOF lejos del principio. Un segmento no pasa de 64 KB.
        trozo = min(exif, 60_000)
        cuerpo += _segmento(0xE1, b"Exif\x00\x00" + b"\x00" * trozo)
        exif -= trozo
    cuerpo += _segmento(0xDB, b"\x00" + b"\x01" * 64)
    sof = 0xC2 if progresivo else 0xC0
    cuerpo += _segmento(sof, b"\x08" + struct.pack(">HH", alto, ancho) + b"\x03" + b"\x01\x11\x00" * 3)
    cuerpo += _segmento(0xDA, b"\x03" + b"\x00" * 9) + b"\xab" * 32 + b"\xff\xd9"
    return cuerpo


def test_png_gif_y_bmp():
    assert dimensiones(_png(1200, 630)) == (1200, 630)
    assert dimensiones(b"GIF89a" + struct.pack("<HH", 256, 100) + b"\x00" * 8) == (256, 100)
    bmp = b"BM" + b"\x00" * 16 + struct.pack("<ii", 640, -480) + b"\x00" * 30
    assert dimensiones(bmp) == (640, 480)  # alto negativo: de arriba abajo


def test_jpeg_base_progresivo_y_con_exif_largo():
    assert dimensiones(_jpeg(1920, 1080)) == (1920, 1080)
    assert dimensiones(_jpeg(800, 600, progresivo=True)) == (800, 600)
    # 40 KB de EXIF antes del SOF: cabe en el rango que pide la sonda.
    grande = _jpeg(1200, 630, exif=40_000)
    assert len(grande) < BYTES_NECESARIOS
    assert dimensiones(grande) == (1200, 630)


def test_jpeg_cortado_antes_del_sof_no_se_adivina():
    cortado = _jpeg(1200, 630, exif=70_000)[:BYTES_NECESARIOS]
    assert dimensiones(cortado) is None


def test_webp_en_sus_tres_variantes():
    vp8 = (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8 " + b"\x00" * 4 + b"\x00" * 3
           + b"\x9d\x01\x2a" + struct.pack("<HH", 1024, 768) + b"\x00" * 10)
    assert dimensiones(vp8) == (1024, 768)
    # VP8L: 14 bits de (ancho - 1) y 14 de (alto - 1) tras la firma 0x2F.
    a, h = 300 - 1, 157 - 1
    bits = a | (h << 14)
    vp8l = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8L" + b"\x00" * 4 + b"\x2f" + struct.pack("<I", bits)
    assert dimensiones(vp8l) == (300, 157)
    vp8x = (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8X" + b"\x00" * 8
            + (2000 - 1).to_bytes(3, "little") + (1000 - 1).to_bytes(3, "little"))
    assert dimensiones(vp8x) == (2000, 1000)


def test_avif_por_la_caja_ispe_y_gana_la_mayor():
    ftyp = struct.pack(">I", 20) + b"ftypavif" + b"\x00" * 8
    miniatura = struct.pack(">I", 20) + b"ispe" + b"\x00" * 4 + struct.pack(">II", 160, 90)
    imagen = struct.pack(">I", 20) + b"ispe" + b"\x00" * 4 + struct.pack(">II", 1600, 900)
    cuerpo = ftyp + b"meta" + miniatura + imagen
    assert formato(cuerpo) == "avif"
    assert dimensiones(cuerpo) == (1600, 900)


def test_lo_que_no_es_una_imagen_conocida_es_none():
    assert dimensiones(b"") is None
    assert dimensiones(None) is None
    assert dimensiones(b"<html><body>no</body></html>") is None
    assert dimensiones(b'<svg width="1200" height="630"></svg>') is None
    assert dimensiones(b"\x89PNG\r\n\x1a\n\x00\x00") is None  # cortado
    assert dimensiones(_png(0, 630)) is None  # un lado a cero es un fichero roto
