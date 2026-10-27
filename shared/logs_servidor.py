"""Logs del servidor: lo que los bots visitan de verdad (#35, decision 213).

Lo comparten la API (subir el fichero), el worker (leerlo) y el analizador
(cruzarlo con un censo). El rastreo dice lo que SE PUEDE encontrar; el log, lo
que Googlebot VISITA.

- **Leer en streaming y con la memoria acotada.** Un log de un mes son gigas:
  se lee linea a linea (en claro o `.gz`) y solo se guarda lo AGREGADO por
  (ruta, bot, verificado): visitas, codigos, primera y ultima. Lo de personas
  se cuenta y no se guarda. Con mas de `MAX_CLAVES` rutas distintas (facetas,
  escaneres con UA de Googlebot) lo que no cabe se cuenta aparte: el worker
  comparte memoria con los rastreos.
- **Formatos.** `combined` (Apache y nginx de serie) y una regex propia con
  grupos con nombre. `common` no: no lleva user-agent y no hay bot que ver.
  Las lineas que no casan se cuentan y se guardan las primeras, con las IPs
  tapadas: casi siempre son de otro formato, y son de personas.
- **Un bot se verifica, no se cree.** Cualquiera pone «Googlebot» en su
  user-agent. Los rangos que publica cada proveedor (Google, Bing, OpenAI,
  Perplexity) se bajan ANTES de leer, y cada IP se comprueba al leer su linea
  (con cache): asi no hace falta guardar IPs. Si de un proveedor no se bajaron
  TODOS sus ficheros, lo que no casa con lo bajado no se da por falso:
  `verificado=None` (sin comprobar), igual que con un proveedor que no publica
  rangos o una IP que no se puede leer. Solo `False` es «seguro que no».
"""

from __future__ import annotations

import gzip
import io
import ipaddress
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Apache/nginx `combined`: ip ident user [fecha] "METODO ruta HTTP/x" codigo bytes "referer" "ua".
# Las comillas escapadas (`\"`) de Apache dentro del referer o el UA cuentan.
_CAMPO = r'(?:[^"\\]|\\.)*'
_COMBINED = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<fecha>[^\]]+)\] "(?P<metodo>[A-Z]+) (?P<url>[^" ]+)(?: HTTP/[\d.]+)?" '
    r'(?P<codigo>\d{3}) (?P<bytes>\d+|-) "(?P<referer>' + _CAMPO + r')" "(?P<ua>' + _CAMPO + r')"')
FORMATOS = {"combined": _COMBINED}
FORMATO_FECHA = "%d/%b/%Y:%H:%M:%S %z"
GRUPOS_OBLIGATORIOS = {"ip", "url", "codigo", "ua"}
MAX_ERRORES_GUARDADOS = 50
# Rutas distintas por (bot, verificado) que se guardan. ~400 bytes cada una.
MAX_CLAVES = 400_000
# Bots, del mas especifico al menos: «Googlebot-Image» antes que «Googlebot».
BOTS_UA: list[tuple[str, str, str]] = [
    ("googlebot-imagen", "Googlebot-Image", "google"),
    ("googlebot-video", "Googlebot-Video", "google"),
    ("googlebot-noticias", "Googlebot-News", "google"),
    ("google-inspeccion", "Google-InspectionTool", "google"),
    ("googleother", "GoogleOther", "google"),
    ("adsbot-google", "AdsBot-Google", "google"),
    ("storebot-google", "Storebot-Google", "google"),
    ("googlebot", "Googlebot", "google"),
    ("bingbot", "bingbot", "bing"),
    ("oai-searchbot", "OAI-SearchBot", "openai"),
    ("chatgpt-user", "ChatGPT-User", "openai"),
    ("gptbot", "GPTBot", "openai"),
    ("claude-searchbot", "Claude-SearchBot", "anthropic"),
    ("claude-user", "Claude-User", "anthropic"),
    ("claudebot", "ClaudeBot", "anthropic"),
    ("perplexity-user", "Perplexity-User", "perplexity"),
    ("perplexitybot", "PerplexityBot", "perplexity"),
    ("applebot", "Applebot", "apple"),
    ("ccbot", "CCBot", "commoncrawl"),
    ("yandexbot", "YandexBot", "yandex"),
    ("duckduckbot", "DuckDuckBot", "duckduckgo"),
    ("amazonbot", "Amazonbot", "amazon"),
    ("bytespider", "Bytespider", "bytedance"),
    ("meta-externalagent", "meta-externalagent", "meta"),
    ("ahrefsbot", "AhrefsBot", "ahrefs"),
    ("semrushbot", "SemrushBot", "semrush"),
]
_BOTS_UA_LOWER = [(b, t.lower(), p) for b, t, p in BOTS_UA]

# Rangos publicados por cada proveedor (JSON con `prefixes`: ipv4Prefix/ipv6Prefix).
RANGOS = {
    "google": ("https://developers.google.com/static/search/apis/ipranges/googlebot.json",
               "https://developers.google.com/static/search/apis/ipranges/special-crawlers.json",
               "https://developers.google.com/static/search/apis/ipranges/user-triggered-fetchers.json"),
    "bing": ("https://www.bing.com/toolbox/bingbot.json",),
    "openai": ("https://openai.com/gptbot.json", "https://openai.com/searchbot.json",
               "https://openai.com/chatgpt-user.json"),
    "perplexity": ("https://www.perplexity.ai/perplexitybot.json",
                   "https://www.perplexity.ai/perplexity-user.json"),
}
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}\b")


class FormatoNoValido(ValueError):
    pass


def bot_de(ua: str | None) -> tuple[str, str] | None:
    """(bot, proveedor) de un user-agent, o None si no es un bot conocido."""
    if not ua:
        return None
    u = ua.lower()
    for bot, token, proveedor in _BOTS_UA_LOWER:
        if token in u:
            return bot, proveedor
    return None


def es_movil(ua: str | None) -> bool:
    """Googlebot Smartphone lleva «Mobile» en el user-agent; el de escritorio, no."""
    return bool(ua) and "mobile" in ua.lower()


def regex_de(formato: str, propia: str | None = None) -> re.Pattern:
    if formato in FORMATOS:
        return FORMATOS[formato]
    if formato != "propio" or not propia:
        raise FormatoNoValido(f"Formato desconocido: {formato!r} (combined, o propio con su regex)")
    try:
        r = re.compile(propia)
    except re.error as exc:
        raise FormatoNoValido(f"La regex no compila: {exc}") from exc
    faltan = GRUPOS_OBLIGATORIOS - set(r.groupindex)
    if faltan:
        raise FormatoNoValido("A la regex le faltan los grupos con nombre: " + ", ".join(sorted(faltan)))
    return r


def lineas(fichero) -> Iterator[str]:
    """Las lineas de un fichero en claro o `.gz` (por los bytes, no por el
    nombre), sin cargarlo entero."""
    crudo = fichero if hasattr(fichero, "read") else open(fichero, "rb")
    inicio = crudo.read(2)
    crudo.seek(0)
    flujo = gzip.GzipFile(fileobj=crudo) if inicio == b"\x1f\x8b" else crudo
    for linea in io.TextIOWrapper(flujo, encoding="utf-8", errors="replace"):
        linea = linea.rstrip("\r\n")
        if linea:
            yield linea


def ruta_de(url: str, host: str | None = None) -> str | None:
    """La ruta y la query de la peticion. Una URL entera (por un proxy) solo si
    es del mismo sitio (con o sin `www.`): la de otro host no es de este log."""
    if url.startswith(("http://", "https://")):
        p = urlsplit(url)
        if host and (p.hostname or "").lower().removeprefix("www.") != host.lower().removeprefix("www."):
            return None
        return (p.path or "/") + ("?" + p.query if p.query else "")
    return url.split("#", 1)[0] or "/"


def clave_log(host: str, ruta: str) -> str:
    """Con la que se cruza con el censo: la de `shared.backlinks.clave_backlink`
    (sin pasar la ruta a minusculas)."""
    from shared.backlinks import clave_backlink

    return clave_backlink(f"https://{host}{ruta if ruta.startswith('/') else '/' + ruta}") or ""


def tapar_ips(linea: str) -> str:
    """Una linea de log con las IPs tapadas: las que no casan son de personas."""
    return _IPV6.sub("x:x", _IPV4.sub("x.x.x.x", linea))


_cache_fechas: dict[str, datetime | None] = {}


def _fecha(texto: str | None, formato_fecha: str) -> datetime | None:
    if not texto:
        return None
    if texto in _cache_fechas:
        return _cache_fechas[texto]
    try:
        d = datetime.strptime(texto, formato_fecha)
    except ValueError:
        try:
            d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except ValueError:
            d = None
    if d is not None and d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    if len(_cache_fechas) > 200_000:
        _cache_fechas.clear()
    _cache_fechas[texto] = d
    return d


# ---------------------------------------------------------------------------
# Verificacion de las IPs
# ---------------------------------------------------------------------------
def redes_de_json(datos: dict) -> list:
    redes = []
    for p in (datos or {}).get("prefixes") or []:
        for k in ("ipv4Prefix", "ipv6Prefix"):
            if p.get(k):
                try:
                    redes.append(ipaddress.ip_network(p[k], strict=False))
                except ValueError:
                    continue
    return redes


def bajar_rangos(proveedor: str, *, descargar: Callable[[str], bytes] | None = None) -> tuple[list, bool]:
    """(redes, completas) de un proveedor. `completas` False si alguno de sus
    ficheros no se pudo bajar: entonces lo que no case no se da por falso."""
    if proveedor not in RANGOS:
        return [], False
    if descargar is None:
        import urllib.request

        def descargar(url):
            req = urllib.request.Request(url, headers={"User-Agent": "seo-crawler-lin3s logs"})
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 -- URLs fijas de arriba
                return r.read(5_000_000)
    redes, completas = [], True
    for url in RANGOS[proveedor]:
        try:
            redes.extend(redes_de_json(json.loads(descargar(url))))
        except Exception:  # noqa: BLE001
            completas = False
            logger.warning("Rangos de %s: no se pudo bajar %s", proveedor, url)
    return redes, completas


class Verificador:
    """`(ip, proveedor) -> True | False | None`, con los rangos bajados al
    crearlo y una cache por IP."""

    def __init__(self, rangos: Callable[[str], tuple[list, bool]] = bajar_rangos):
        self._rangos = rangos
        self._redes: dict[str, tuple[list, bool]] = {}
        self._cache: dict[tuple[str, str], bool | None] = {}

    def preparar(self, proveedores: Iterable[str]) -> None:
        for p in proveedores:
            if p not in self._redes:
                self._redes[p] = self._rangos(p)

    def __call__(self, ip: str, proveedor: str) -> bool | None:
        k = (ip, proveedor)
        if k in self._cache:
            return self._cache[k]
        if proveedor not in self._redes:
            self.preparar([proveedor])
        redes, completas = self._redes[proveedor]
        v: bool | None
        if not redes:
            v = None
        else:
            try:
                d = ipaddress.ip_address(ip.strip().split("%", 1)[0])
                d = getattr(d, "ipv4_mapped", None) or d
            except ValueError:
                v = None          # `ip:puerto`, una lista de X-Forwarded-For...: no se lee, no es falsa
            else:
                v = True if any(d in r for r in redes) else (False if completas else None)
        if len(self._cache) < 500_000:
            self._cache[k] = v
        return v


@dataclass
class Agregado:
    lineas_ok: int = 0
    lineas_error: int = 0
    personas: int = 0
    otros_bots: int = 0
    otro_host: int = 0
    fuera_del_tope: int = 0
    errores: list[str] = field(default_factory=list)
    desde: datetime | None = None
    hasta: datetime | None = None
    # (ruta, bot, verificado) -> {"visitas", "codigos", "primero", "ultimo", "movil"}
    por_ruta: dict[tuple[str, str, bool | None], dict[str, Any]] = field(default_factory=dict)


def agregar(lineas_: Iterable[str], regex: re.Pattern, *, verificar: Callable[[str, str], bool | None],
            host: str | None = None, formato_fecha: str = FORMATO_FECHA, cada: int = 0,
            progreso: Callable[[int], None] | None = None, max_claves: int = MAX_CLAVES) -> Agregado:
    a = Agregado()
    for n, linea in enumerate(lineas_, 1):
        if progreso is not None and cada and n % cada == 0:
            progreso(n)
        m = regex.match(linea)
        if m is None:
            a.lineas_error += 1
            if len(a.errores) < MAX_ERRORES_GUARDADOS:
                a.errores.append(tapar_ips(linea[:500]))
            continue
        g = m.groupdict()
        a.lineas_ok += 1
        cuando = _fecha(g.get("fecha"), formato_fecha)
        if cuando is not None:
            if a.desde is None or cuando < a.desde:
                a.desde = cuando
            if a.hasta is None or cuando > a.hasta:
                a.hasta = cuando
        ua = g.get("ua")
        b = bot_de(ua)
        if b is None:
            a.personas += 1
            continue
        bot, proveedor = b
        if (g.get("metodo") or "GET") not in ("GET", "HEAD"):
            a.otros_bots += 1
            continue
        ruta = ruta_de(g["url"], host)
        if ruta is None:
            a.otro_host += 1
            continue
        v = verificar((g.get("ip") or "").strip(), proveedor)
        k = (ruta[:2000], bot, v)
        d = a.por_ruta.get(k)
        if d is None:
            if len(a.por_ruta) >= max_claves:
                a.fuera_del_tope += 1
                continue
            d = a.por_ruta[k] = {"visitas": 0, "codigos": {}, "primero": cuando, "ultimo": cuando, "movil": 0}
        d["visitas"] += 1
        codigo = str(g.get("codigo") or "")
        d["codigos"][codigo] = d["codigos"].get(codigo, 0) + 1
        if cuando is not None:
            if d["primero"] is None or cuando < d["primero"]:
                d["primero"] = cuando
            if d["ultimo"] is None or cuando > d["ultimo"]:
                d["ultimo"] = cuando
        if es_movil(ua):
            d["movil"] += 1
    return a


def filas_por_url(a: Agregado, host: str) -> Iterator[dict[str, Any]]:
    """Una fila por (ruta, bot, verificado), lista para `log_urls`. Generador:
    no se tiene otra copia entera en memoria."""
    for (ruta, bot, v), d in a.por_ruta.items():
        yield {"ruta": ruta, "clave": clave_log(host, ruta), "bot": bot, "verificado": v,
               "visitas": d["visitas"], "codigos": d["codigos"], "movil": d["movil"],
               "primero": d["primero"], "ultimo": d["ultimo"]}


# ---------------------------------------------------------------------------
# El procesado de una importacion (lo llama el worker)
# ---------------------------------------------------------------------------
def directorio():
    from pathlib import Path

    return Path(os.getenv("ARCHIVO_DIR", "/data/archivo")) / "logs"


PROGRESO_CADA = 200_000
# Sin latido en este tiempo, la lectura se da por cortada (un despliegue).
MINUTOS_SIN_LATIDO = 15
PROVEEDORES = tuple(RANGOS)


def procesar(import_id: int, *, sesion_factory, verificador: Verificador | None = None) -> str | None:
    """Lee el fichero de la importacion, guarda lo agregado y borra el fichero.

    Devuelve el estado final, o None si no estaba pendiente (otro proceso la
    cogio, o ya se hizo). Se coge con un UPDATE que comprueba el estado, y
    mientras se lee late (`latido_at`): la recuperacion solo toca lo que lleva
    `MINUTOS_SIN_LATIDO` sin latir. El fichero se borra pase lo que pase.
    """
    from itertools import islice

    from sqlalchemy import update

    from shared.models import LogImport, LogUrl

    s = sesion_factory()
    ruta = None
    try:
        ahora = datetime.now(timezone.utc)
        cogida = s.execute(update(LogImport).where(LogImport.id == import_id, LogImport.estado == "pendiente")
                           .values(estado="procesando", progreso=0, latido_at=ahora)).rowcount
        s.commit()
        if not cogida:
            return None
        imp = s.get(LogImport, import_id)
        ruta, host = imp.ruta_disco, imp.host
        # Una vuelta anterior a medias no deja filas dobles.
        s.query(LogUrl).filter(LogUrl.import_id == import_id).delete(synchronize_session=False)
        s.commit()

        def _latir(n=None):
            valores = {"latido_at": datetime.now(timezone.utc)}
            if n is not None:
                valores["progreso"] = n
            s.execute(update(LogImport).where(LogImport.id == import_id).values(**valores))
            s.commit()

        a = None
        estado, motivo, extra = "hecho", None, {}
        try:
            regex = regex_de(imp.formato, imp.regex)
            v = verificador or Verificador()
            v.preparar(PROVEEDORES)
            _latir()
            with open(ruta, "rb") as f:
                a = agregar(lineas(f), regex, verificar=v, host=host,
                            formato_fecha=imp.formato_fecha or FORMATO_FECHA, cada=PROGRESO_CADA, progreso=_latir)
            if not a.lineas_ok:
                raise FormatoNoValido("Ninguna línea casa con el formato: mira las primeras en «errores»")
            bots: dict[str, dict[str, int]] = {}
            filas = filas_por_url(a, host)
            while True:
                lote = list(islice(filas, 5000))
                if not lote:
                    break
                for f_ in lote:
                    b = bots.setdefault(f_["bot"], {"visitas": 0, "verificadas": 0, "falsas": 0, "sin_comprobar": 0})
                    b["visitas"] += f_["visitas"]
                    clase = ("verificadas" if f_["verificado"] else
                             "falsas" if f_["verificado"] is False else "sin_comprobar")
                    b[clase] += f_["visitas"]
                s.bulk_insert_mappings(LogUrl, [{"import_id": import_id, **f_} for f_ in lote])
                _latir()
            extra = {"lineas_ok": a.lineas_ok, "lineas_error": a.lineas_error, "personas": a.personas,
                     "desde": a.desde, "hasta": a.hasta, "bots": bots,
                     "resumen": {"otro_host": a.otro_host, "fuera_del_tope": a.fuera_del_tope,
                                 "otros_metodos": a.otros_bots}}
        except Exception as exc:  # noqa: BLE001 -- sin esto se quedaria «procesando» para siempre
            s.rollback()
            if not isinstance(exc, (FormatoNoValido, OSError)):
                logger.exception("Log %s: fallo al procesar", import_id)
            estado, motivo = "error", str(exc)[:500]
            s.query(LogUrl).filter(LogUrl.import_id == import_id).delete(synchronize_session=False)
        imp = s.get(LogImport, import_id)
        if imp is not None:        # el cliente pudo borrarse mientras se leia
            imp.estado, imp.motivo = estado, motivo
            imp.errores = a.errores if a is not None else imp.errores
            for k, val in extra.items():
                setattr(imp, k, val)
            imp.terminado_at = datetime.now(timezone.utc)
            imp.ruta_disco = None
        s.commit()
        return estado
    finally:
        # El fichero tiene IPs de personas: no se guarda una vez leido, ni
        # aunque falle algo o el cliente ya no exista.
        if ruta:
            try:
                os.remove(ruta)
            except OSError:
                pass
        s.close()


def recuperar(session, *, minutos: int = MINUTOS_SIN_LATIDO) -> list[int]:
    """Las que llevan `minutos` sin latir en `procesando` (un despliegue las
    corto) vuelven a `pendiente` si su fichero sigue; el worker las reencola.
    Las que estan latiendo no se tocan: las esta leyendo alguien."""
    from shared.models import LogImport

    corte = datetime.now(timezone.utc) - timedelta(minutes=minutos)
    ids = []
    for imp in session.query(LogImport).filter(LogImport.estado == "procesando").all():
        latido = imp.latido_at
        if latido is not None and latido.tzinfo is None:
            latido = latido.replace(tzinfo=timezone.utc)
        if latido is not None and latido > corte:
            continue
        if imp.ruta_disco and os.path.exists(imp.ruta_disco):
            imp.estado = "pendiente"
            ids.append(imp.id)
        else:
            imp.estado, imp.motivo = "error", "el fichero ya no está (se cortó a mitad y no se puede reanudar)"
    session.commit()
    return ids


def borrar_ficheros_de_cliente(session, cliente_id) -> None:
    """Antes de borrar un cliente: sus logs sin leer tienen IPs de personas."""
    from shared.models import LogImport

    for (r,) in session.query(LogImport.ruta_disco).filter(LogImport.cliente_id == cliente_id,
                                                           LogImport.ruta_disco.isnot(None)):
        try:
            os.remove(r)
        except OSError:
            pass
