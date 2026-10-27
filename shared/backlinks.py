"""Enlaces externos (backlinks) subidos en CSV, y su cruce con un censo (#31, decision 212).

Lo comparten la API (subir el CSV de un cliente) y el analizador (el PageRank
con la autoridad que entra de fuera y los avisos de lo que se pierde).

- **De un CSV exportado, sin API de pago.** Ahrefs, Semrush y Majestic cada
  uno con sus columnas: se reconocen por nombre, sin mayusculas ni espacios,
  y el separador y la codificacion (Ahrefs exporta UTF-16 con tabuladores) se
  detectan. Lo que no es dofollow o esta perdido no se guarda.
- **La aportacion es la de cada backlink**, no la autoridad de la pagina de
  destino: la de destino (UR, Page Authority) ya incluye los enlaces internos
  del sitio vistos por el proveedor y se contaria dos veces. Por backlink:
  autoridad de la pagina de origen / (enlaces externos de esa pagina + 1),
  como reparte el PageRank. Sin autoridad, 1; sin enlaces externos, sin
  dividir.
- **El cruce no pasa la ruta a minusculas**: `/Producto` y `/producto` son dos
  URLs en casi cualquier servidor. Si cuenta: el esquema, el `www.`, la barra
  final, el fragmento y la codificacion (`%C3%B1` es `ñ`). Si la URL exacta con
  su query no esta en el censo, se prueba sin la query (un `?utm_` de una
  campaña).
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

# Lo que se acepta de un fichero: un export de Ahrefs de un sitio mediano son
# unos pocos MB; 200.000 filas son de sobra para el PageRank de un censo.
MAX_BYTES = 60 * 1024 * 1024
MAX_FILAS = 200_000

# Nombres de columna (sin mayusculas, espacios ni signos) -> campo.
_COLUMNAS = {
    "origen": ("referringpageurl", "sourceurl", "urlfrom", "referringurl", "source", "linkfrom", "fromurl"),
    "destino": ("targeturl", "urlto", "target", "linkto", "tourl", "destinationurl"),
    # La autoridad de la PAGINA de origen. Majestic no tiene una de pagina con
    # el mismo sentido: su Citation Flow de la fuente es lo mas parecido.
    "autoridad": ("ur", "urlrating", "pageascore", "sourcecitationflow", "sourcetrustflow",
                  "pageauthority", "pa", "rank", "pagerank"),
    "externos": ("externallinks", "externallinkscount", "outlinks", "sourceoutlinks",
                 "externaloutlinks", "linkeddomains"),
    "nofollow": ("nofollow", "flagnofollow", "isnofollow"),
    "tipo": ("type", "linktype", "linkattributes", "rel"),
    "perdido": ("lost", "lostlink", "loststatus", "lostdate", "flagdeleted", "deleted"),
}

FUENTES = ("ahrefs", "semrush", "majestic", "generico")


class CsvNoValido(ValueError):
    pass


def _norm_columna(nombre: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (nombre or "").strip().lower())


def _decodificar(crudo: bytes) -> str:
    if crudo.startswith((b"\xff\xfe", b"\xfe\xff")):
        return crudo.decode("utf-16")
    if crudo.startswith(b"\xef\xbb\xbf"):
        return crudo[3:].decode("utf-8")
    # UTF-16 sin BOM: la mitad de los bytes a cero.
    muestra = crudo[:4000]
    if muestra and muestra.count(b"\x00") > len(muestra) // 4:
        return crudo.decode("utf-16-le" if muestra[1:2] == b"\x00" else "utf-16-be")
    try:
        return crudo.decode("utf-8")
    except UnicodeDecodeError:
        return crudo.decode("latin-1")


def _separador(texto: str) -> str:
    cabecera = texto.split("\n", 1)[0]
    return max(("\t", ",", ";"), key=cabecera.count)


def _verdad(valor: str | None) -> bool | None:
    v = (valor or "").strip().lower()
    if v in ("true", "1", "yes", "si", "sí", "nofollow", "y"):
        return True
    if v in ("false", "0", "no", "", "dofollow", "follow", "n"):
        return False if v else None
    return None


def _numero(valor: str | None) -> float | None:
    v = (valor or "").strip().replace(" ", "")
    if not v:
        return None
    # «1.234» / «1,234» / «12,5»: un solo separador con 3 cifras detras es de
    # miles; con otra cantidad, decimal.
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", v):
        v = re.sub(r"[.,]", "", v)
    else:
        v = v.replace(",", ".")
    try:
        n = float(v)
    except ValueError:
        return None
    return n if math.isfinite(n) else None


def fuente_de(cabeceras: Iterable[str]) -> str:
    c = {_norm_columna(x) for x in cabeceras}
    if "referringpageurl" in c or "ur" in c:
        return "ahrefs"
    if "pageascore" in c:
        return "semrush"
    if "sourcecitationflow" in c or "flagnofollow" in c:
        return "majestic"
    return "generico"


@dataclass
class Lectura:
    fuente: str
    filas: list[dict[str, Any]] = field(default_factory=list)
    leidas: int = 0
    descartadas_nofollow: int = 0
    descartadas_perdidas: int = 0
    descartadas_sin_url: int = 0
    cortado: bool = False
    columnas: dict[str, str] = field(default_factory=dict)


def leer_csv(crudo: bytes, *, fuente: str | None = None, max_filas: int = MAX_FILAS) -> Lectura:
    """Las filas dofollow y vivas de un export, con su aportacion.

    `CsvNoValido` si no hay columnas de origen y destino.
    """
    if len(crudo) > MAX_BYTES:
        raise CsvNoValido(f"El fichero pasa de {MAX_BYTES // (1024 * 1024)} MB")
    texto = _decodificar(crudo)
    lector = csv.reader(io.StringIO(texto), delimiter=_separador(texto))
    try:
        cabecera = next(lector)
    except StopIteration as exc:
        raise CsvNoValido("El fichero está vacío") from exc
    normal = [_norm_columna(c) for c in cabecera]
    indice: dict[str, int] = {}
    for campo, alias in _COLUMNAS.items():
        for a in alias:
            if a in normal:
                indice[campo] = normal.index(a)
                break
    if "origen" not in indice or "destino" not in indice:
        raise CsvNoValido("No se reconocen las columnas de la página que enlaza y la enlazada "
                          "(«Referring page URL» y «Target URL» en Ahrefs; «Source url» y "
                          "«Target url» en Semrush; «Source URL» y «Target URL» en Majestic)")
    lec = Lectura(fuente=fuente if fuente in FUENTES else fuente_de(cabecera),
                  columnas={k: cabecera[i] for k, i in indice.items()})

    def celda(fila, campo):
        i = indice.get(campo)
        return fila[i] if i is not None and i < len(fila) else None

    for fila in lector:
        if not any(fila):
            continue
        lec.leidas += 1
        origen, destino = (celda(fila, "origen") or "").strip(), (celda(fila, "destino") or "").strip()
        if not origen.startswith(("http://", "https://")) or not destino.startswith(("http://", "https://")):
            lec.descartadas_sin_url += 1
            continue
        nofollow = _verdad(celda(fila, "nofollow"))
        tipo = (celda(fila, "tipo") or "").lower()
        if nofollow or "nofollow" in tipo or "ugc" in tipo or "sponsored" in tipo:
            lec.descartadas_nofollow += 1
            continue
        perdido = (celda(fila, "perdido") or "").strip().lower()
        if perdido and perdido not in ("0", "false", "no", "-", "live", "active", "not lost"):
            lec.descartadas_perdidas += 1
            continue
        if len(lec.filas) >= max_filas:
            lec.cortado = True
            continue
        autoridad = _numero(celda(fila, "autoridad"))
        externos = _numero(celda(fila, "externos"))
        aporte = (autoridad if autoridad is not None and autoridad > 0 else 1.0) / (
            (externos if externos is not None and externos >= 0 else 0.0) + 1.0)
        lec.filas.append({"url_from": origen[:2000], "url_to": destino[:2000],
                          "dominio_from": (urlsplit(origen).hostname or "")[:255],
                          "aportacion": round(aporte, 6)})
    return lec


def clave_backlink(url: str) -> str | None:
    """La URL tal como se cruza con el censo, sin pasar la ruta a minusculas."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    ruta = unquote(p.path or "/")
    if len(ruta) > 1:
        ruta = ruta.rstrip("/") or "/"
    clave = host + ruta
    return clave + ("?" + unquote(p.query) if p.query else "")


def sin_query(clave: str) -> str:
    return clave.split("?", 1)[0]


@dataclass
class Cruce:
    """Lo que llega de fuera a cada URL del censo, y lo que no casa."""
    por_url: dict[int, dict[str, Any]]
    filas: int
    casadas: int
    aportacion_total: float
    aportacion_casada: float
    no_rastreadas: list[dict[str, Any]]

    @property
    def parte_casada(self) -> float | None:
        return round(self.casadas / self.filas, 4) if self.filas else None


def cruzar(filas: Iterable[tuple[str, str, str, float]], urls: Iterable[tuple[int, str]],
           *, max_no_rastreadas: int = 50, max_origenes: int = 5) -> Cruce:
    """`filas`: (url_to, url_from, dominio_from, aportacion). `urls`: (id, url)
    de las internas del censo."""
    por_clave: dict[str, int] = {}
    por_ruta: dict[str, int] = {}
    for uid, url in urls:
        k = clave_backlink(url)
        if k is None:
            continue
        por_clave.setdefault(k, uid)
        por_ruta.setdefault(sin_query(k), uid) if "?" not in k else None
    por_url: dict[int, dict[str, Any]] = {}
    fuera: dict[str, dict[str, Any]] = {}
    n = casadas = 0
    total = casada = 0.0
    for url_to, url_from, dominio, aporte in filas:
        n += 1
        aporte = float(aporte or 0)
        total += aporte
        k = clave_backlink(url_to)
        uid = None
        if k is not None:
            uid = por_clave.get(k)
            if uid is None and "?" in k:
                uid = por_ruta.get(sin_query(k))
        if uid is None:
            f = fuera.setdefault(url_to, {"url": url_to, "backlinks": 0, "aportacion": 0.0, "dominios": set()})
            f["backlinks"] += 1
            f["aportacion"] += aporte
            f["dominios"].add(dominio)
            continue
        casadas += 1
        casada += aporte
        d = por_url.setdefault(uid, {"backlinks": 0, "aportacion": 0.0, "dominios": set(), "origenes": []})
        d["backlinks"] += 1
        d["aportacion"] += aporte
        d["dominios"].add(dominio)
        d["origenes"].append((aporte, url_from))
    for d in por_url.values():
        d["origenes"] = [u for _, u in sorted(d["origenes"], reverse=True)[:max_origenes]]
        d["dominios"] = len(d["dominios"])
    no_rastreadas = sorted(fuera.values(), key=lambda f: -f["aportacion"])[:max_no_rastreadas]
    for f in no_rastreadas:
        f["dominios"] = len(f["dominios"])
        f["aportacion"] = round(f["aportacion"], 4)
    return Cruce(por_url=por_url, filas=n, casadas=casadas, aportacion_total=total,
                 aportacion_casada=casada, no_rastreadas=no_rastreadas)


def ultima_importacion(session, cliente_id):
    """La importacion mas reciente del cliente, o None."""
    from shared.models import BacklinkImport

    if not cliente_id:
        return None
    return (session.query(BacklinkImport).filter(BacklinkImport.cliente_id == cliente_id)
            .order_by(BacklinkImport.created_at.desc(), BacklinkImport.id.desc()).first())


def filas_de(session, importacion) -> list[tuple[str, str, str, float]]:
    from shared.models import ExternalLink as E

    return [tuple(f) for f in session.query(E.url_to, E.url_from, E.dominio_from, E.aportacion)
            .filter(E.import_id == importacion.id).all()]


def agrupar_por_destino(filas: Iterable[dict]) -> dict[str, int]:
    """Cuantos backlinks hay por URL de destino (para el resumen de la subida)."""
    c: dict[str, int] = defaultdict(int)
    for f in filas:
        c[f["url_to"]] += 1
    return dict(c)
