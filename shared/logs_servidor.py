"""Logs del servidor: lo que los bots visitan de verdad (#35, decision 213).

Lo comparten la API (subir el fichero), el worker (leerlo) y el analizador
(cruzarlo con un censo). El rastreo dice lo que SE PUEDE encontrar; el log, lo
que Googlebot VISITA.

- **Leer en streaming.** Un log de un mes son gigas: se lee linea a linea (en
  claro o `.gz`) y solo se guarda lo AGREGADO por URL, bot y verificacion
  (visitas, codigos, primera y ultima), nunca cada visita. Lo de personas se
  cuenta y no se guarda.
- **Formatos.** `combined` y `common` (Apache y nginx de serie), y una regex
  propia con grupos con nombre para lo demas (CDN, nginx a medida). Las lineas
  que no casan no se tiran en silencio: se cuentan y se guardan las primeras,
  porque casi siempre son de otro formato y asi se ve que arreglar.
- **Un bot se verifica, no se cree.** Cualquiera puede poner «Googlebot» en
  su user-agent. Se comprueba la IP contra los rangos que publica cada
  proveedor (Google, Bing, OpenAI, Perplexity) y, si no se pudieron bajar, por
  DNS inverso y directo (Google y Bing lo documentan). Lo que no se puede
  comprobar se dice (`verificado=None`), no se da por bueno ni por falso.
"""

from __future__ import annotations

import gzip
import io
import ipaddress
import json
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

# Apache/nginx `combined`: ip ident user [fecha] "METODO ruta HTTP/x" codigo bytes "referer" "ua"
_COMBINED = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<fecha>[^\]]+)\] "(?P<metodo>[A-Z]+) (?P<url>\S+)(?: HTTP/[\d.]+)?" '
    r'(?P<codigo>\d{3}) (?P<bytes>\d+|-)(?: "(?P<referer>[^"]*)" "(?P<ua>[^"]*)")?')
FORMATOS = {"combined": _COMBINED, "common": _COMBINED}
FORMATO_FECHA = "%d/%b/%Y:%H:%M:%S %z"
GRUPOS_OBLIGATORIOS = {"ip", "url", "codigo", "ua"}
MAX_ERRORES_GUARDADOS = 50
# Bots, del mas especifico al menos: «Googlebot-Image» antes que «Googlebot».
# El token es el que aparece en el user-agent (los de control, como
# Google-Extended, no visitan nada y no estan).
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
# Dominios del DNS inverso, para cuando los rangos no se pudieron bajar.
DNS_DE = {"google": ("googlebot.com", "google.com", "googleusercontent.com"),
          "bing": ("search.msn.com",)}
MAX_IPS_DNS = 2000


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
        raise FormatoNoValido(f"Formato desconocido: {formato!r} (combined, common o propio con su regex)")
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


def ruta_de(url: str) -> str:
    """La ruta y la query de la peticion: el log guarda «/a?b», o una URL
    entera si pasa por un proxy."""
    if url.startswith(("http://", "https://")):
        p = urlsplit(url)
        return (p.path or "/") + ("?" + p.query if p.query else "")
    return url.split("#", 1)[0] or "/"


def clave_log(host: str, ruta: str) -> str:
    """Con la que se cruza con el censo: la de `shared.backlinks.clave_backlink`
    (sin pasar la ruta a minusculas)."""
    from shared.backlinks import clave_backlink

    return clave_backlink(f"https://{host}{ruta if ruta.startswith('/') else '/' + ruta}") or ""


def _fecha(texto: str | None, formato_fecha: str) -> datetime | None:
    if not texto:
        return None
    try:
        d = datetime.strptime(texto, formato_fecha)
    except ValueError:
        try:
            d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class Agregado:
    """Lo que se guarda de un log: por (ruta, bot), y las IPs de cada
    proveedor para verificarlas despues."""
    lineas_ok: int = 0
    lineas_error: int = 0
    personas: int = 0
    otros_bots: int = 0
    errores: list[str] = field(default_factory=list)
    desde: datetime | None = None
    hasta: datetime | None = None
    # (ruta, bot) -> {"ips": {ip: visitas}, "codigos": {codigo: n}, "primero", "ultimo", "movil"}
    por_ruta: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    ips_por_proveedor: dict[str, set[str]] = field(default_factory=dict)

    def resumen_bots(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for (_, bot), d in self.por_ruta.items():
            c[bot] = c.get(bot, 0) + sum(d["ips"].values())
        return c


def agregar(lineas_: Iterable[str], regex: re.Pattern, *, formato_fecha: str = FORMATO_FECHA,
            cada: int = 0, progreso: Callable[[int], None] | None = None) -> Agregado:
    a = Agregado()
    for n, linea in enumerate(lineas_, 1):
        if progreso is not None and cada and n % cada == 0:
            progreso(n)
        m = regex.match(linea)
        if m is None:
            a.lineas_error += 1
            if len(a.errores) < MAX_ERRORES_GUARDADOS:
                a.errores.append(linea[:500])
            continue
        g = m.groupdict()
        a.lineas_ok += 1
        cuando = _fecha(g.get("fecha"), formato_fecha)
        if cuando is not None:
            a.desde = cuando if a.desde is None or cuando < a.desde else a.desde
            a.hasta = cuando if a.hasta is None or cuando > a.hasta else a.hasta
        ua = g.get("ua")
        b = bot_de(ua)
        if b is None:
            a.personas += 1
            continue
        bot, proveedor = b
        if (g.get("metodo") or "GET") not in ("GET", "HEAD"):
            a.otros_bots += 1
            continue
        ruta = ruta_de(g["url"])
        d = a.por_ruta.get((ruta, bot))
        if d is None:
            d = a.por_ruta[(ruta, bot)] = {"ips": {}, "codigos": {}, "primero": cuando, "ultimo": cuando,
                                           "movil": 0}
        ip = (g.get("ip") or "").strip()
        d["ips"][ip] = d["ips"].get(ip, 0) + 1
        codigo = str(g.get("codigo") or "")
        d["codigos"][codigo] = d["codigos"].get(codigo, 0) + 1
        if cuando is not None:
            if d["primero"] is None or cuando < d["primero"]:
                d["primero"] = cuando
            if d["ultimo"] is None or cuando > d["ultimo"]:
                d["ultimo"] = cuando
        if es_movil(ua):
            d["movil"] += 1
        a.ips_por_proveedor.setdefault(proveedor, set()).add(ip)
    return a


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


def bajar_rangos(proveedor: str, *, descargar: Callable[[str], bytes] | None = None) -> list | None:
    """Las redes publicadas de un proveedor, o None si no se pudo bajar
    ninguna (y entonces se prueba DNS)."""
    if proveedor not in RANGOS:
        return None
    if descargar is None:
        import urllib.request

        def descargar(url):
            req = urllib.request.Request(url, headers={"User-Agent": "seo-crawler-lin3s logs"})
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 -- URLs fijas de arriba
                return r.read(5_000_000)
    redes, alguna = [], False
    for url in RANGOS[proveedor]:
        try:
            redes.extend(redes_de_json(json.loads(descargar(url))))
            alguna = True
        except Exception:  # noqa: BLE001
            logger.warning("Rangos de %s: no se pudo bajar %s", proveedor, url)
    return redes if alguna else None


def _por_dns(ip: str, dominios: tuple[str, ...]) -> bool | None:
    """DNS inverso que acaba en un dominio del proveedor y directo que vuelve
    a la misma IP (lo que piden Google y Bing). None si no se pudo resolver."""
    try:
        nombre = socket.gethostbyaddr(ip)[0].rstrip(".").lower()
    except (socket.herror, socket.gaierror, OSError):
        return False
    if not any(nombre == d or nombre.endswith("." + d) for d in dominios):
        return False
    try:
        directas = {i[4][0] for i in socket.getaddrinfo(nombre, None)}
    except (socket.gaierror, OSError):
        return None
    return ip in directas


def verificar(ips_por_proveedor: dict[str, set[str]], *,
              rangos: Callable[[str], list | None] = bajar_rangos,
              dns: Callable[[str, tuple[str, ...]], bool | None] = _por_dns) -> dict[str, bool | None]:
    """ip -> True (es del proveedor), False (no lo es) o None (no se pudo
    comprobar: proveedor que no publica rangos, o DNS que no responde)."""
    salida: dict[str, bool | None] = {}
    for proveedor, ips in ips_por_proveedor.items():
        redes = rangos(proveedor)
        if redes is not None:
            for ip in ips:
                try:
                    dir_ = ipaddress.ip_address(ip)
                except ValueError:
                    salida[ip] = False
                    continue
                salida[ip] = any(dir_ in r for r in redes)
            continue
        dominios = DNS_DE.get(proveedor)
        if not dominios:
            for ip in ips:
                salida.setdefault(ip, None)
            continue
        lista = sorted(ips)[:MAX_IPS_DNS]
        with ThreadPoolExecutor(max_workers=32) as ex:
            for ip, r in zip(lista, ex.map(lambda i: dns(i, dominios), lista)):
                salida[ip] = r
        for ip in sorted(ips)[MAX_IPS_DNS:]:
            salida.setdefault(ip, None)
    return salida


def filas_por_url(a: Agregado, verificacion: dict[str, bool | None], host: str) -> list[dict[str, Any]]:
    """Una fila por (ruta, bot, verificado), lista para `log_urls`."""
    filas = []
    for (ruta, bot), d in a.por_ruta.items():
        grupos: dict[bool | None, int] = {}
        for ip, n in d["ips"].items():
            v = verificacion.get(ip)
            grupos[v] = grupos.get(v, 0) + n
        total = sum(grupos.values()) or 1
        for v, n in grupos.items():
            # Los codigos y el movil se reparten en proporcion: no se guardan
            # por IP (el agregado no lo permitiria en un log de gigas).
            filas.append({
                "ruta": ruta[:2000], "clave": clave_log(host, ruta), "bot": bot, "verificado": v,
                "visitas": n,
                "codigos": {c: round(k * n / total) for c, k in d["codigos"].items()} if len(grupos) > 1 else d["codigos"],
                "movil": round(d["movil"] * n / total),
                "primero": d["primero"], "ultimo": d["ultimo"],
            })
    return filas


# ---------------------------------------------------------------------------
# El procesado de una importacion (lo llama el worker)
# ---------------------------------------------------------------------------
def directorio():
    import os
    from pathlib import Path

    return Path(os.getenv("ARCHIVO_DIR", "/data/archivo")) / "logs"


PROGRESO_CADA = 200_000


def procesar(import_id: int, *, sesion_factory, verificar_ips=verificar) -> str | None:
    """Lee el fichero de la importacion, guarda lo agregado y borra el fichero.

    Devuelve el estado final, o None si no estaba pendiente (otra replica la
    cogio, o ya se hizo). La importacion se coge con un UPDATE que comprueba
    el estado: dos workers no la procesan dos veces.
    """
    import os

    from sqlalchemy import update

    from shared.models import LogImport, LogUrl

    s = sesion_factory()
    try:
        cogida = s.execute(update(LogImport).where(LogImport.id == import_id, LogImport.estado == "pendiente")
                           .values(estado="procesando", progreso=0)).rowcount
        s.commit()
        if not cogida:
            return None
        imp = s.get(LogImport, import_id)
        # Una vuelta anterior a medias (un despliegue a mitad) no deja filas dobles.
        s.query(LogUrl).filter(LogUrl.import_id == import_id).delete(synchronize_session=False)
        s.commit()
        a = None
        try:
            regex = regex_de(imp.formato, imp.regex)

            def _progreso(n):
                s.execute(update(LogImport).where(LogImport.id == import_id).values(progreso=n))
                s.commit()

            with open(imp.ruta_disco, "rb") as f:
                a = agregar(lineas(f), regex, formato_fecha=imp.formato_fecha or FORMATO_FECHA,
                            cada=PROGRESO_CADA, progreso=_progreso)
            if not a.lineas_ok:
                raise FormatoNoValido("Ninguna línea casa con el formato: mira las primeras en «errores»")
            verificacion = verificar_ips(a.ips_por_proveedor)
            filas = filas_por_url(a, verificacion, imp.host)
            for i in range(0, len(filas), 5000):
                s.bulk_insert_mappings(LogUrl, [{"import_id": import_id, **f} for f in filas[i:i + 5000]])
            bots: dict[str, dict[str, int]] = {}
            for f in filas:
                b = bots.setdefault(f["bot"], {"visitas": 0, "verificadas": 0, "falsas": 0, "sin_comprobar": 0})
                b["visitas"] += f["visitas"]
                b["verificadas" if f["verificado"] else "falsas" if f["verificado"] is False else "sin_comprobar"] += f["visitas"]
            imp.estado, imp.motivo = "hecho", None
            imp.lineas_ok, imp.lineas_error, imp.personas = a.lineas_ok, a.lineas_error, a.personas
            imp.errores, imp.desde, imp.hasta, imp.bots = a.errores, a.desde, a.hasta, bots
        except Exception as exc:  # noqa: BLE001 -- sin esto se quedaria «procesando» para siempre
            s.rollback()
            if not isinstance(exc, (FormatoNoValido, OSError)):
                logger.exception("Log %s: fallo al procesar", import_id)
            imp = s.get(LogImport, import_id)
            imp.estado, imp.motivo = "error", str(exc)[:500]
            if a is not None:
                imp.errores = a.errores
        imp.terminado_at = datetime.now(timezone.utc)
        s.commit()
        # El fichero tiene IPs de personas: no se guarda una vez leido.
        if imp.ruta_disco:
            try:
                os.remove(imp.ruta_disco)
            except OSError:
                pass
            imp.ruta_disco = None
            s.commit()
        return imp.estado
    finally:
        s.close()


def recuperar(session) -> list[int]:
    """Las que un despliegue dejo `procesando` vuelven a `pendiente` (si su
    fichero sigue): el worker las reencola."""
    import os

    from shared.models import LogImport

    ids = []
    for imp in session.query(LogImport).filter(LogImport.estado == "procesando").all():
        if imp.ruta_disco and os.path.exists(imp.ruta_disco):
            imp.estado = "pendiente"
            ids.append(imp.id)
        else:
            imp.estado, imp.motivo = "error", "el fichero ya no está (se cortó a mitad y no se puede reanudar)"
    session.commit()
    return ids
