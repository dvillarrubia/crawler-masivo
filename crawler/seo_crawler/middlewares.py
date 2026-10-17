"""
Custom Scrapy downloader middlewares for the SEO crawler.

* **UserAgentMiddleware** -- rotates User-Agent strings.
* **ProxyMiddleware**     -- optional proxy rotation.
* **JobConfigMiddleware** -- attaches job config to every request's meta.
* **RobotsCrawlDelayMiddleware** -- respeta el `Crawl-delay` de robots.txt.
* **RetryAfterMiddleware** -- respeta el `Retry-After` de un 429 o un 503.
"""

from __future__ import annotations

import itertools
import json
import logging
import math
import time
from typing import TYPE_CHECKING

from scrapy import Request, signals
from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware
from scrapy.exceptions import NotConfigured
from scrapy.http import Response
from scrapy.utils.misc import load_object

if TYPE_CHECKING:
    from scrapy.crawler import Crawler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default UA pool
# ---------------------------------------------------------------------------
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class UserAgentMiddleware:
    """
    Set the request User-Agent.

    Priority order:
    1. Per-request ``request.meta["user_agent"]``
    2. Spider-level ``spider.custom_user_agent`` (set from job config)
    3. Round-robin from the configured pool

    NOTE: when ``settings.USER_AGENT`` is set (the default), the pool holds a
    single UA and rotation is effectively disabled *by design*. Non-Playwright
    requests go through curl_cffi with a fixed Chrome TLS fingerprint
    (``IMPERSONATE``); rotating the UA header against a fixed JA3/JA4
    fingerprint would be internally inconsistent and easier for a WAF to flag.
    The multi-UA ``DEFAULT_USER_AGENTS`` pool is only used as a fallback when
    ``USER_AGENT`` is explicitly cleared.
    """

    def __init__(self, user_agents: list[str]):
        self._cycle = itertools.cycle(user_agents)

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        # A single configured UA (the default) keeps the UA header consistent
        # with the curl_cffi TLS fingerprint; clearing USER_AGENT opts into the
        # rotating fallback pool.
        ua_setting = crawler.settings.get("USER_AGENT", "")
        pool = [ua_setting] if ua_setting else DEFAULT_USER_AGENTS
        middleware = cls(pool)
        return middleware

    def process_request(self, request: Request, spider):
        # Per-request override
        if "user_agent" in request.meta:
            request.headers[b"User-Agent"] = request.meta["user_agent"]
            return None

        # Spider-level override (set by job config)
        custom_ua = getattr(spider, "custom_user_agent", None)
        if custom_ua:
            request.headers[b"User-Agent"] = custom_ua
            return None

        # Round-robin
        request.headers[b"User-Agent"] = next(self._cycle)
        return None


class ProxyMiddleware:
    """
    Optionally route requests through a rotating proxy list.

    Set ``PROXY_LIST`` in settings as a comma-separated string of proxy
    URLs (e.g. ``http://proxy1:8080,http://proxy2:8080``).

    If the list is empty or not provided the middleware is a no-op.
    """

    def __init__(self, proxies: list[str]):
        self._proxies = proxies
        self._cycle = itertools.cycle(proxies) if proxies else None

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        raw = crawler.settings.get("PROXY_LIST", "")
        proxies = [p.strip() for p in raw.split(",") if p.strip()]
        if proxies:
            logger.info("ProxyMiddleware loaded with %d proxies", len(proxies))
        return cls(proxies)

    def process_request(self, request: Request, spider):
        if self._cycle is None:
            return None

        # Allow per-request proxy override
        if "proxy" not in request.meta:
            request.meta["proxy"] = next(self._cycle)
        return None


class JobConfigMiddleware:
    """
    Attach the current job's configuration dict to every request so
    that the spider and other middlewares can access it via
    ``request.meta["job_config"]``.

    The spider is expected to expose ``self.job_config`` after loading
    the job from the database.
    """

    def process_request(self, request: Request, spider):
        job_config = getattr(spider, "job_config", None)
        if job_config and "job_config" not in request.meta:
            request.meta["job_config"] = job_config
        return None


class RobotsAuditMiddleware(RobotsTxtMiddleware):
    """
    Robots.txt audit-only middleware. Fetches robots.txt and stamps
    ``request.meta["blocked_by_robots"]`` with True/False, but never
    raises IgnoreRequest -- the request always proceeds.

    Activates when ``ROBOTS_MODE=='audit'`` (independent of
    ``ROBOTSTXT_OBEY``). The worker sets ``ROBOTSTXT_OBEY=False`` in
    audit mode so Scrapy's built-in middleware stays disabled while
    this one runs alongside.
    """

    def __init__(self, crawler):
        # El padre aborta si ROBOTSTXT_OBEY es False, y en modo auditoria es
        # False a proposito, asi que no se puede llamar a su __init__: hay que
        # replicarlo. Replicarlo a ojo es como se quedo fuera `self._stats`, que
        # el padre usa al descargar robots.txt: la PRIMERA peticion moria con
        # `AttributeError: 'RobotsAuditMiddleware' object has no attribute
        # '_stats'`, el rastreo se quedaba a cero paginas y el vigilante lo
        # mataba por estancamiento media hora despues. Reproducido con el
        # arnes: en modo auditoria no se guardaba ni una pagina.
        # Por eso se copian todos los atributos que crea el padre y hay un test
        # que compara los dos conjuntos: si una version de Scrapy anade otro,
        # el test lo canta en vez de que lo cante un rastreo de 40 minutos.
        self._default_useragent = crawler.settings.get("USER_AGENT", "Scrapy")
        self._robotstxt_useragent = crawler.settings.get("ROBOTSTXT_USER_AGENT", None)
        self.crawler = crawler
        self._stats = crawler.stats
        self._parsers = {}
        self._parserimpl = load_object(crawler.settings.get("ROBOTSTXT_PARSER"))
        # Scrapy 2.12 movio la construccion del parser a `build_from_crawler`;
        # `from_crawler` sigue existiendo en el parser, pero se usa la via
        # oficial cuando esta.
        try:
            from scrapy.utils.misc import build_from_crawler

            build_from_crawler(self._parserimpl, crawler, b"")
        except ImportError:  # pragma: no cover - Scrapy < 2.12
            self._parserimpl.from_crawler(crawler, b"")

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        if crawler.settings.get("ROBOTS_MODE") != "audit":
            raise NotConfigured
        return cls(crawler)

    def process_request_2(self, rp, request):
        # Signature aligned with Scrapy 2.13+ (spider arg dropped).
        if rp is None:
            return
        useragent = self._robotstxt_useragent
        if not useragent:
            useragent = request.headers.get(b"User-Agent", self._default_useragent)
        request.meta["blocked_by_robots"] = not rp.allowed(request.url, useragent)
        # Never raise IgnoreRequest -- audit mode always lets the request through.


# Tope del Crawl-delay que se respeta. Un `Crawl-delay: 30` en un sitio de
# 20.000 paginas son 7 dias de rastreo con una peticion cada vez: eso no es
# cortesia, es no poder auditar el sitio. Con 10 s el mismo censo sigue siendo
# largo (unos 2 dias y medio) pero terminable, y el tope se avisa en el log.
CRAWL_DELAY_MAX_SEGUNDOS = 10.0


def crawl_delay_aplicable(rp, useragent, tope: float = CRAWL_DELAY_MAX_SEGUNDOS) -> float | None:
    """Segundos de `Crawl-delay` que robots.txt pide a `useragent`, acotados.

    None si no lo declara, si el valor no es un numero positivo y finito, o si
    el parser no sabe leerlo (`RobotParser.crawl_delay` existe desde Scrapy
    2.18; uno anterior o de terceros puede no tenerlo). Un valor ilegible no se
    convierte en retraso: equivocarse aqui frena un rastreo entero.
    """
    lector = getattr(rp, "crawl_delay", None)
    if not callable(lector):
        return None
    try:
        valor = lector(useragent)
    except Exception:
        return None
    if valor is None:
        return None
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(valor) or valor <= 0:
        return None
    return min(valor, tope)


class RobotsCrawlDelayMiddleware:
    """Respeta el `Crawl-delay` de robots.txt en `robots_mode=respect`.

    Googlebot lo ignora (Google regula su ritmo por la respuesta del servidor),
    asi que no es una senal de SEO. Pero este crawler dice respetar robots.txt,
    y un `Crawl-delay` suele ser lo que el administrador del sitio escribio
    despues de que un rastreador le tumbara el servidor: saltarselo es la forma
    mas rapida de acabar bloqueado por el WAF a mitad de un censo. En `ignore`
    y en `audit` no se aplica: los dos rastrean a proposito lo que robots.txt
    pide no tocar, y medio respetarlo no tendria sentido.

    Como se aplica: el retraso es el del SLOT de descarga del host, que es como
    Scrapy reparte el ritmo por dominio, asi que vale igual con render que sin
    el y no toca el auto-cap de concurrencia de render (decision 8). Dos cosas
    lo deshacian si solo se fijaba una vez:

    - AUTOTHROTTLE reescribe `slot.delay` tras cada respuesta y lo puede bajar
      hasta `DOWNLOAD_DELAY` (0). Por eso tras cada `response_downloaded` se
      vuelve a subir al piso. Esta senal la conecta este middleware, que se
      construye con el motor, DESPUES que la extension AutoThrottle, asi que
      corre detras de ella y antes de que el slot saque la siguiente peticion.
      AUTOTHROTTLE sigue pudiendo SUBIR el retraso si el servidor va lento.
    - El jitter por defecto de Scrapy (+-50%) dejaria intervalos de la mitad
      del pedido. En esos slots se apaga.

    Y el recolector de slots inactivos los borra a los 60 s: el retraso se deja
    tambien en `per_slot_settings`, que es de donde lee el slot que se cree
    despues.
    """

    def __init__(self, crawler: Crawler):
        if not crawler.settings.getbool("ROBOTSTXT_OBEY"):
            raise NotConfigured
        self.crawler = crawler
        self._tope = crawler.settings.getfloat(
            "ROBOTS_CRAWL_DELAY_MAX", CRAWL_DELAY_MAX_SEGUNDOS
        )
        # Mismo UA con el que RobotsTxtMiddleware decide que se puede pedir: si
        # no, el Disallow y el Crawl-delay saldrian de grupos distintos.
        self._useragent = (
            crawler.settings.get("ROBOTSTXT_USER_AGENT")
            or crawler.settings.get("USER_AGENT", "Scrapy")
        )
        # Lo que el job ya pide (`request_delay` -> DOWNLOAD_DELAY) no se baja.
        self._retraso_job = crawler.settings.getfloat("DOWNLOAD_DELAY", 0.0)
        self._retrasos: dict[str, float] = {}
        crawler.signals.connect(self._robots_parsed, signal=signals.robots_parsed)
        crawler.signals.connect(
            self._response_downloaded, signal=signals.response_downloaded
        )

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        return cls(crawler)

    def _robots_parsed(self, robotparser, request) -> None:
        pedido = crawl_delay_aplicable(robotparser, self._useragent, math.inf)
        if pedido is None:
            return
        retraso = min(pedido, self._tope)
        downloader = self.crawler.engine.downloader
        clave = downloader.get_slot_key(request)
        retraso = max(retraso, self._retraso_job)
        self._retrasos[clave] = retraso
        ajustes = downloader.per_slot_settings.setdefault(clave, {})
        ajustes["delay"] = retraso
        ajustes["jitter"] = 0.0
        self._aplicar(clave)
        if pedido > retraso:
            logger.info(
                "robots.txt de %s pide Crawl-delay %.1f s; se aplica el tope de %.1f s",
                clave, pedido, retraso,
            )
        else:
            logger.info("robots.txt de %s: Crawl-delay %.1f s aplicado", clave, retraso)

    def _aplicar(self, clave: str) -> None:
        retraso = self._retrasos.get(clave)
        if retraso is None:
            return
        slot = self.crawler.engine.downloader.slots.get(clave)
        if slot is None:
            return
        if slot.delay < retraso:
            slot.delay = retraso
        slot.jitter = 0.0

    def _response_downloaded(self, response, request, spider=None) -> None:
        clave = request.meta.get("download_slot")
        if clave is not None and clave in self._retrasos:
            self._aplicar(clave)

    def process_request(self, request: Request, spider=None):
        # Red de seguridad: si algo bajo el retraso entre respuestas (otra
        # extension, un slot recreado), la siguiente peticion lo restablece.
        if self._retrasos:
            self._aplicar(self.crawler.engine.downloader.get_slot_key(request))
        return None


def piso_de_slot(crawler: Crawler, clave: str) -> float:
    """El retraso por debajo del cual no puede quedar el slot `clave`.

    `max(DOWNLOAD_DELAY del job, Crawl-delay de robots.txt)`: lo que el job
    pide y lo que respeta `RobotsCrawlDelayMiddleware` (decision 81), que solo
    existe con `ROBOTSTXT_OBEY`. Es a donde vuelve el slot cuando caduca la
    pausa de un `Retry-After` (decision 111): si volviera a 0, el Crawl-delay
    quedaria deshecho hasta la siguiente respuesta. El Crawl-delay nunca baja
    un retraso (solo lo sube), asi que no deshace la pausa.
    """
    piso = crawler.settings.getfloat("DOWNLOAD_DELAY", 0.0)
    gestor = getattr(getattr(crawler, "engine", None), "downloader", None)
    for mw in getattr(getattr(gestor, "middleware", None), "middlewares", ()) or ():
        if isinstance(mw, RobotsCrawlDelayMiddleware):
            piso = max(piso, mw._retrasos.get(clave, 0.0))
    return piso


class RetryAfterMiddleware:
    """Respeta el `Retry-After` de un 429 o un 503, en todos los `robots_mode`.

    Sin esto, `RetryMiddleware` repetia el 429 al instante y AUTOTHROTTLE no
    frena con un 429 rapido (no sube el retraso con respuestas que no son 200
    salvo que tarden), asi que una rafaga de 429 seguia al mismo ritmo hasta
    que la ventana de salud paraba el rastreo (decision 103). El servidor ya
    dijo cuanto esperar.

    Es el mecanismo de la decision 81 generalizado y con caducidad: con un
    `Retry-After` de N s (acotado a `RETRY_AFTER_MAX`), el slot del host pasa
    a un retraso de N s, sin jitter, durante N s. El reintento del 429 sale
    del mismo slot, asi que espera. Se reaplica en cada `response_downloaded`
    —que llega ANTES de que el slot saque la siguiente peticion y despues de
    AutoThrottle, que con las respuestas 200 que siguen en vuelo lo bajaria—
    y en `process_request` como red de seguridad. Pasada la ventana, el slot
    vuelve a `piso_de_slot` (el Crawl-delay o el retraso del job).

    Lo que se respeto va a Redis (`job:<id>:retry_after`) y el worker lo deja
    en `jobs.health["retry_after"]`: si el sitio pedia mas que el tope, el
    informe tiene que poder decirlo. No es una incidencia SEO.

    Una pausa es para un limite PASAJERO. Si un host devuelve
    `RETRY_AFTER_MAX_SEGUIDAS` respuestas 429/503 a peticiones que salieron
    CON la pausa ya abierta, sin una sola de otra clase entre medias, es un
    muro (decision 103), y pausar no lo arregla: lo esconde. Con la
    frontera llena, cada primer intento sale espaciado por la pausa antes de
    que el primer reintento agotado llegue a `parse`, asi que la ventana de
    salud no veia ni un 429 en horas (medido en el arnes con 300 URLs y una
    pausa de 2 s: 5,4 s sin el middleware y mas de 10 minutos con el, sin
    llegar a cerrar). Pasado ese numero el host deja de pausarse hasta que
    responda otra cosa, y la ventana de la 103 cierra el rastreo como antes.
    """

    CODIGOS = (429, 503)

    def __init__(self, crawler: Crawler, reloj=time.monotonic):
        if not crawler.settings.getbool("RETRY_AFTER_ENABLED", True):
            raise NotConfigured
        self.crawler = crawler
        self._reloj = reloj
        self._tope = max(0.0, crawler.settings.getfloat("RETRY_AFTER_MAX", 60.0))
        self._retraso_job = crawler.settings.getfloat("DOWNLOAD_DELAY", 0.0)
        # 429/503 seguidos por slot, y los slots que ya no se pausan (muro).
        self._max_seguidas = max(1, crawler.settings.getint("RETRY_AFTER_MAX_SEGUIDAS", 5))
        self._seguidas: dict[str, int] = {}
        # Cuando se abrio la pausa de la racha de 429/503 en curso, por slot.
        self._racha_desde: dict[str, float] = {}
        self._sin_pausa: set[str] = set()
        # Lo que ya se respeto en ejecuciones anteriores del mismo job (se lee
        # de Redis la primera vez que hay algo que publicar).
        self._base: dict | None = None
        # clave -> (retraso, hasta, jitter que tenia el slot)
        self._pisos: dict[str, tuple[float, float, float | None]] = {}
        self.resumen = {"veces": 0, "max_pedido_s": 0.0, "max_aplicado_s": 0.0,
                        "truncados": 0, "abandonos": 0}
        crawler.signals.connect(
            self._response_downloaded, signal=signals.response_downloaded
        )

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        return cls(crawler)

    def _downloader(self):
        return self.crawler.engine.downloader

    def anotar(self, clave: str, pedido: float) -> float:
        """Abre (o alarga) la pausa del slot `clave`. Devuelve lo aplicado."""
        aplicado = min(pedido, self._tope)
        r = self.resumen
        r["veces"] += 1
        r["max_pedido_s"] = round(max(r["max_pedido_s"], pedido), 3)
        r["max_aplicado_s"] = round(max(r["max_aplicado_s"], aplicado), 3)
        if pedido > aplicado:
            r["truncados"] += 1
        if aplicado > 0:
            ahora = self._reloj()
            previo = self._pisos.get(clave)
            slot = self._downloader().slots.get(clave)
            jitter = previo[2] if previo else (slot.jitter if slot is not None else None)
            retraso = max(aplicado, previo[0] if previo else 0.0)
            hasta = max(ahora + aplicado, previo[1] if previo else 0.0)
            self._pisos[clave] = (retraso, hasta, jitter)
            ajustes = self._downloader().per_slot_settings.setdefault(clave, {})
            ajustes["delay"] = retraso
            ajustes["jitter"] = 0.0
            self._aplicar(clave)
        logger.info(
            "%s pide Retry-After %.1f s; se espera %.1f s%s", clave, pedido, aplicado,
            " (tope RETRY_AFTER_MAX)" if pedido > aplicado else "",
        )
        self._publicar()
        # El latido: el spider solo lo estampa cada 50 URLs, y con un host en
        # pausa (los reintentos no pasan por `parse`) el vigilante de
        # estancamiento daria por muerto un rastreo que esta esperando a
        # proposito. Con cada pausa, el hueco maximo entre latidos es el tope.
        latido = getattr(getattr(self.crawler, "spider", None), "_write_heartbeat", None)
        if callable(latido):
            try:
                latido()
            except Exception:  # noqa: BLE001
                pass
        return aplicado

    def _aplicar(self, clave: str) -> None:
        piso = self._pisos.get(clave)
        if piso is None:
            return
        retraso, hasta, jitter = piso
        slot = self._downloader().slots.get(clave)
        if self._reloj() >= hasta:
            # Caducada: el slot vuelve al piso de siempre (Crawl-delay o el
            # retraso del job), no a 0 ni al retraso de la pausa.
            del self._pisos[clave]
            base = piso_de_slot(self.crawler, clave)
            ajustes = self._downloader().per_slot_settings.setdefault(clave, {})
            if base > 0:
                ajustes["delay"] = base
            else:
                ajustes.pop("delay", None)
                ajustes.pop("jitter", None)
            if slot is not None:
                slot.delay = base
                # Con Crawl-delay el jitter sigue apagado (decision 81).
                if jitter is not None and base <= self._retraso_job:
                    slot.jitter = jitter
            return
        if slot is None:
            return
        if slot.delay < retraso:
            slot.delay = retraso
        slot.jitter = 0.0

    def _response_downloaded(self, response, request, spider=None) -> None:
        clave = request.meta.get("download_slot")
        if clave is None:
            return
        if response.status not in self.CODIGOS:
            self._seguidas.pop(clave, None)
            self._racha_desde.pop(clave, None)
            self._sin_pausa.discard(clave)
            self._aplicar(clave)
            return
        if clave in self._sin_pausa:
            self._aplicar(clave)
            return
        desde = self._racha_desde.get(clave)
        if desde is not None:
            # Solo cuenta lo que SALIO con la pausa ya abierta: los que estaban
            # en vuelo cuando llego el primer 429 no esperaron nada, y con 8 o
            # 16 por host un limite pasajero los devuelve todos a la vez.
            latencia = request.meta.get("download_latency")
            latencia = latencia if isinstance(latencia, (int, float)) and latencia > 0 else 0.0
            if self._reloj() - latencia >= desde:
                self._seguidas[clave] = self._seguidas.get(clave, 0) + 1
                if self._seguidas[clave] >= self._max_seguidas:
                    self._abandonar(clave, self._seguidas[clave])
                    return
        from shared.respuestas import retry_after_segundos

        pedido = retry_after_segundos(response.headers.get(b"Retry-After"))
        if pedido is not None and pedido > 0:
            self._racha_desde.setdefault(clave, self._reloj())
            self.anotar(clave, pedido)
            return
        self._aplicar(clave)

    def _abandonar(self, clave: str, seguidas: int) -> None:
        """Deja de pausar `clave`: sus 429/503 seguidos ya son un muro."""
        self._sin_pausa.add(clave)
        self.resumen["abandonos"] += 1
        piso = self._pisos.get(clave)
        if piso is not None:
            # Caducar la pausa ya: vuelve al piso (Crawl-delay o el del job).
            self._pisos[clave] = (piso[0], self._reloj(), piso[2])
            self._aplicar(clave)
        logger.warning(
            "%s lleva %d respuestas 429/503 seguidas: es un muro, no una pausa; "
            "se deja de respetar su Retry-After para que la ventana de salud "
            "(decision 103) lo vea", clave, seguidas,
        )
        self._publicar()

    def process_request(self, request: Request, spider=None):
        if self._pisos:
            self._aplicar(self._downloader().get_slot_key(request))
        return None

    def _publicar(self) -> None:
        """El resumen a Redis, para `jobs.health`. Best-effort."""
        spider = getattr(self.crawler, "spider", None)
        cliente = getattr(spider, "_redis", None)
        job_id = getattr(spider, "job_id", None)
        if cliente is None or not job_id:
            return
        try:
            clave = f"job:{job_id}:retry_after"
            if self._base is None:
                # Acumulado entre reanudaciones del mismo job.
                try:
                    crudo = cliente.get(clave)
                    previo = json.loads(crudo) if crudo else {}
                except Exception:  # noqa: BLE001
                    previo = {}
                self._base = previo if isinstance(previo, dict) else {}
            base, r = self._base, self.resumen
            fusion = {
                "veces": int(base.get("veces", 0)) + r["veces"],
                "truncados": int(base.get("truncados", 0)) + r["truncados"],
                "max_pedido_s": max(float(base.get("max_pedido_s", 0)), r["max_pedido_s"]),
                "max_aplicado_s": max(float(base.get("max_aplicado_s", 0)), r["max_aplicado_s"]),
                "abandonos": int(base.get("abandonos", 0)) + r["abandonos"],
                "tope_s": self._tope,
            }
            cliente.set(clave, json.dumps(fusion), ex=30 * 86400)
        except Exception:  # noqa: BLE001 -- informativo
            logger.debug("No se pudo publicar el resumen de Retry-After", exc_info=True)


class HttpConfigMiddleware:
    """
    Apply per-job HTTP configuration (custom headers, cookies, auth)
    to every outgoing request.

    Reads ``spider._http_config`` which is populated from the job's
    ``http`` configuration block.
    """

    def process_request(self, request: Request, spider):
        http_config = getattr(spider, "_http_config", None)
        if not http_config:
            return None

        # Custom headers
        for key, value in http_config.get("custom_headers", {}).items():
            request.headers[key.encode()] = value.encode()

        # Accept-Language
        accept_lang = http_config.get("accept_language", "")
        if accept_lang:
            request.headers[b"Accept-Language"] = accept_lang.encode()

        # Cookies
        for name, val in http_config.get("cookies", {}).items():
            request.cookies[name] = val

        # Basic Auth
        basic_user = http_config.get("basic_auth_user", "")
        if basic_user:
            import base64

            password = http_config.get("basic_auth_password", "")
            cred = base64.b64encode(f"{basic_user}:{password}".encode()).decode()
            request.headers[b"Authorization"] = f"Basic {cred}".encode()

        return None


# ---------------------------------------------------------------------------
# Spider middleware
# ---------------------------------------------------------------------------
class DepthMiddleware:
    """Sustituye al DepthMiddleware de Scrapy respetando la profundidad del spider.

    El de Scrapy PISA ``meta["depth"]`` con la del response + 1 en toda
    peticion que sale de un callback. Con los sitemaps eso era robots.txt (0)
    → indice (1) → sitemap hijo (2) → URL (3): las URLs del sitemap entraban
    con profundidad 3 aunque el spider les pusiera 1, y con el ``max_depth=3``
    por defecto no se rastreaba ni un enlace suyo. Afectaba a cualquier sitio
    con indice de sitemap (Yoast, Liferay). Lo mismo con las redirecciones,
    cuyo destino debe conservar la profundidad del origen.

    El spider fija la profundidad de todas sus peticiones; aqui solo se rellena
    la que falte (response + 1, como Scrapy) y se aplica ``DEPTH_PRIORITY``.
    No aplica ``DEPTH_LIMIT``: el tope lo controla el spider con ``max_depth``.
    """

    def __init__(self, prio: int):
        self._prio = prio

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        return cls(crawler.settings.getint("DEPTH_PRIORITY"))

    def _procesar(self, response: Response, item):
        if isinstance(item, Request):
            if "depth" not in item.meta:
                item.meta["depth"] = response.meta.get("depth", 0) + 1
            if self._prio:
                item.priority -= item.meta["depth"] * self._prio
        return item

    def process_spider_output(self, response, result, spider=None):
        for item in result:
            yield self._procesar(response, item)

    async def process_spider_output_async(self, response, result, spider=None):
        async for item in result:
            yield self._procesar(response, item)
