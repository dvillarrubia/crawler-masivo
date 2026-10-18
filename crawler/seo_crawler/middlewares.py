"""
Custom Scrapy downloader middlewares for the SEO crawler.

* **UserAgentMiddleware** -- rotates User-Agent strings.
* **ProxyMiddleware**     -- optional proxy rotation.
* **JobConfigMiddleware** -- attaches job config to every request's meta.
* **RobotsCrawlDelayMiddleware** -- respeta el `Crawl-delay` de robots.txt.
* **RobotsTxtRegistroMiddleware** -- guarda lo que respondio el robots.txt de
  cada host (decisiones 112 y 113).
"""

from __future__ import annotations

import itertools
import logging
import math
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from scrapy import Request, signals
from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware
from scrapy.exceptions import DontCloseSpider, NotConfigured
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


class RobotsTxtRegistroMiddleware:
    """Guarda en `robots_txt` lo que respondio el robots.txt de cada host.

    Scrapy monta el parser con el cuerpo sea cual sea el codigo y, si la
    descarga lanza una excepcion, deja pasar todo: un robots.txt que responde
    503, 429 o no responde se rastreaba como «todo permitido» sin que llegara a
    ningun informe, y Google, con eso, deja de rastrear el sitio (decision 112).
    Aqui solo se ANOTA: lo que se pide no cambia.

    Prioridad 90, por debajo del RetryMiddleware (550) y del RedirectMiddleware
    (600): `process_response` y `process_exception` corren de la prioridad mas
    alta a la mas baja, asi que aqui llega el resultado FINAL, tras reintentos y
    redirecciones. Se reconoce la descarga por la URL ORIGINAL (`/robots.txt`
    de raiz), no por la meta: es la que pide `RobotsTxtMiddleware` en `respect`
    y en `audit` y la del spider para los sitemaps (la unica en `ignore`). No se
    toca `_parse_robots` ni `_robots_error`: son privados de Scrapy, y la
    decision 48 ya pago replicar a ojo su middleware.

    Una fila por URL de robots.txt y ejecucion: la primera descarga, que es con
    la que Scrapy decidio. Solo de hosts internos y con tope
    (`ROBOTS_TXT_MAX_HOSTS`): lo que pase de ahi queda «no evaluado». Si fallo
    (5xx, 429, red), al quedarse el spider sin trabajo se pide UNA vez mas
    (`comprobacion`): nuestra IP no es la de Google, y un fallo pasajero no es
    lo mismo que uno que dura todo el rastreo. Todo best-effort, con el `try`
    cubriendo cada entrada: guardar esto no puede tumbar un rastreo.
    """

    PRIORIDAD_DESCARGA = 1000

    def __init__(self, crawler: Crawler):
        from shared.robots_txt import MAX_HOSTS_POR_JOB

        self.crawler = crawler
        self.filas: dict[str, dict] = {}
        self._tope = crawler.settings.getint("ROBOTS_TXT_MAX_HOSTS", MAX_HOSTS_POR_JOB)
        self._comprobar = crawler.settings.getbool("ROBOTS_TXT_COMPROBAR", True)
        self._espera = crawler.settings.getfloat("DOWNLOAD_TIMEOUT", 30.0) + 15.0
        self._avisado_tope = False
        self._comprobado = False
        self._pendientes = 0
        self._limite = 0.0
        crawler.signals.connect(self._spider_idle, signal=signals.spider_idle)

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        return cls(crawler)

    # -- descargas ---------------------------------------------------------
    @staticmethod
    def _url_de_robots(request: Request) -> str | None:
        from shared.robots_txt import es_url_de_robots

        original = (request.meta.get("redirect_urls") or [request.url])[0]
        return original if es_url_de_robots(original) else None

    def process_response(self, request: Request, response: Response, spider=None):
        try:
            url = self._url_de_robots(request)
            if url is not None:
                cabeceras = {
                    k.decode("latin-1").lower(): ", ".join(
                        v.decode("latin-1", "replace") for v in (vs or []))
                    for k, vs in response.headers.items()
                }
                self._registrar(request, url, status_code=response.status,
                                cabeceras=cabeceras, cuerpo=response.body,
                                url_final=response.url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo registrar el robots.txt de %s: %s", request.url, exc)
        return response

    def process_exception(self, request: Request, exception, spider=None):
        try:
            url = self._url_de_robots(request)
            if url is not None:
                self._registrar(request, url,
                                error=f"{type(exception).__name__}: {exception}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo registrar el robots.txt de %s: %s", request.url, exc)
        return None

    def _registrar(self, request: Request, url: str, **datos) -> None:
        from shared.robots_txt import fila_de_respuesta

        clave = url.lower()
        intentos = int(request.meta.get("retry_times") or 0) + 1
        if request.meta.get("robots_txt_comprobacion"):
            fila = self.filas.get(clave)
            if fila is None:
                return
            nueva = fila_de_respuesta(url, intentos=intentos, **datos)
            fila["comprobacion"] = {
                "status_code": nueva["status_code"], "error": nueva["error"],
                "response_class": nueva["response_class"],
                "fecha": nueva["fecha"].isoformat(timespec="seconds"),
            }
            # Si a la segunda respondio, ese es el robots.txt que se evalua:
            # la primera no dejo cuerpo.
            if nueva["cuerpo"] is not None and fila.get("cuerpo") is None:
                for k in ("bytes", "sha256", "cuerpo", "recortado"):
                    fila[k] = nueva[k]
            logger.info("robots.txt %s comprobado al cerrar: %s", url,
                        nueva["status_code"] or nueva["error"])
            self._guardar(fila)
            return
        if clave in self.filas:
            return  # la primera de esta ejecucion es con la que decidio Scrapy
        spider = getattr(self.crawler, "spider", None)
        interno = getattr(spider, "_is_internal", None)
        if callable(interno) and not interno(url):
            return
        if len(self.filas) >= self._tope:
            if not self._avisado_tope:
                self._avisado_tope = True
                logger.warning("robots.txt: tope de %d hosts guardados; los demas quedan "
                               "sin evaluar", self._tope)
            return
        fila = fila_de_respuesta(url, intentos=intentos, **datos)
        self.filas[clave] = fila
        if fila["status_code"] is None or fila["status_code"] >= 400:
            logger.warning("robots.txt %s: %s tras %d intento(s)", url,
                           fila["status_code"] or fila["error"], intentos)
        self._guardar(fila)

    def _guardar(self, fila: dict) -> None:
        """Una fila por (job, URL): la de una ejecucion anterior se sustituye."""
        try:
            from shared.database import SessionLocal
            from shared.models import RobotsTxt

            spider = getattr(self.crawler, "spider", None)
            job_id = getattr(spider, "job_id", None)
            if not job_id:
                return
            sesion = SessionLocal()
            try:
                datos = {k: v for k, v in fila.items() if not k.startswith("_")}
                existente = (sesion.query(RobotsTxt)
                             .filter(RobotsTxt.job_id == job_id, RobotsTxt.url == fila["url"])
                             .one_or_none())
                if existente is None:
                    sesion.add(RobotsTxt(job_id=job_id, **datos))
                else:
                    for k, v in datos.items():
                        setattr(existente, k, v)
                    existente.evaluacion = None
                sesion.commit()
            finally:
                sesion.close()
        except Exception as exc:  # noqa: BLE001 -- el registro no tumba un rastreo
            logger.warning("No se pudo guardar el robots.txt de %s: %s", fila.get("url"), exc)

    # -- comprobacion al cerrar --------------------------------------------
    def _spider_idle(self, spider=None) -> None:
        if self._pendientes > 0 and time.monotonic() < self._limite:
            raise DontCloseSpider
        if self._comprobado or not self._comprobar:
            return
        self._comprobado = True
        try:
            lanzadas = self._lanzar_comprobaciones()
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo comprobar de nuevo robots.txt: %s", exc)
            return
        if lanzadas:
            raise DontCloseSpider

    def _lanzar_comprobaciones(self) -> int:
        from scrapy.http.request import NO_CALLBACK
        from scrapy.utils.defer import deferred_from_coro

        from shared.robots_txt import INACCESIBLE, estado

        engine = self.crawler.engine
        lanzadas = 0
        for fila in list(self.filas.values()):
            if estado(fila["status_code"], fila["error"], fila["response_class"]) != INACCESIBLE:
                continue
            req = Request(fila["url"], priority=self.PRIORIDAD_DESCARGA, callback=NO_CALLBACK,
                          dont_filter=True,
                          meta={"dont_obey_robotstxt": True, "dont_retry": True,
                                "robots_txt_comprobacion": True})
            descarga = getattr(engine, "download_async", None)
            d = deferred_from_coro(descarga(req)) if descarga else engine.download(req)
            d.addBoth(self._fin_comprobacion)
            self._pendientes += 1
            lanzadas += 1
        self._limite = time.monotonic() + self._espera
        return lanzadas

    def _fin_comprobacion(self, _resultado) -> None:
        # La respuesta o el error ya los anoto process_response/process_exception.
        self._pendientes -= 1
        return None


class HttpConfigMiddleware:
    """Cabeceras propias, cookies y Basic del job (bloque ``http``), solo a
    los hosts del sitio (decision 114).

    Lee ``spider._http_config``. Antes se ponian en TODAS las peticiones: con
    una semilla que salta a otro dominio o con ``follow_external``, el usuario
    y la contrasena de un staging llegaban a terceros. Ahora solo las lleva una
    peticion a un host de las semillas (``spider._lleva_credenciales``), y a
    las demas se les quitan si les llegaron copiadas de otra peticion.
    ``Accept-Language`` no es un dato del cliente sino como se mide: va a todas.

    Con render no se ponen en la peticion de Scrapy: scrapy-playwright las
    aplicaria con ``route.continue_`` y Chromium las conserva al seguir una
    redireccion a otro host (medido). Viajan en ``meta["_http_render"]`` y las
    aplica ``render.preparar_pagina``.

    Sin jar (lo normal, decision 114) las cookies van como cabecera ``Cookie``
    escrita aqui: por ``request.cookies`` las pasa scrapy-impersonate al motor
    de cookies de curl, que las descarta para un host sin punto
    (``localhost``; medido con curl_cffi 0.16). Con jar van por
    ``request.cookies``, para que el ``CookiesMiddleware`` de Scrapy las junte
    con las del servidor (ver ``CookiesTrasElJarMiddleware``).
    """

    def __init__(self, con_jar: bool = False):
        self._con_jar = con_jar

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.getbool("COOKIES_ENABLED"))

    def process_request(self, request: Request, spider):
        http_config = getattr(spider, "_http_config", None) or {}
        es_render = bool(request.meta.get("playwright"))
        if not http_config and not es_render:
            return None

        accept_lang = http_config.get("accept_language", "")
        if accept_lang:
            request.headers[b"Accept-Language"] = accept_lang.encode()

        cabeceras = cabeceras_del_job(http_config)
        cookies = {str(k): str(v) for k, v in (http_config.get("cookies") or {}).items()}
        lleva = bool(getattr(spider, "_lleva_credenciales", lambda _u: False)(request.url))

        if es_render or not lleva:
            _quitar_credenciales(request, cabeceras, cookies)
        if es_render:
            request.meta["_http_render"] = {
                "cabeceras": cabeceras if lleva else {},
                "cookies": cookies,
                "conservar": bool(http_config.get("conservar_cookies")),
                "hosts": sorted(getattr(spider, "_hosts_credenciales", ()) or ()),
                "raices": sorted(getattr(spider, "_raices_credenciales", ()) or ()),
                # Sin semillas http, las cookies van con `secure` y Chromium
                # no las manda por http (revision de la decision 114)
                "solo_https": "http" not in (getattr(spider, "_esquemas_credenciales", None)
                                             or {"http"}),
            }
            return None
        if not lleva:
            return None

        for key, value in cabeceras.items():
            request.headers[key.encode()] = value.encode()
        if cookies and self._con_jar:
            for name, val in cookies.items():
                request.cookies[name] = val
        elif cookies:
            request.headers[b"Cookie"] = "; ".join(
                f"{name}={val}" for name, val in cookies.items()).encode()
        return None


def cabeceras_del_job(http_config: dict) -> dict[str, str]:
    """Las cabeceras que solo van a los hosts del sitio: las propias y el Basic."""
    cabeceras = {str(k): str(v) for k, v in (http_config.get("custom_headers") or {}).items()}
    basic_user = http_config.get("basic_auth_user", "")
    if basic_user:
        import base64

        password = http_config.get("basic_auth_password", "")
        cred = base64.b64encode(f"{basic_user}:{password}".encode()).decode()
        cabeceras["Authorization"] = f"Basic {cred}"
    return cabeceras


def _quitar_credenciales(request: Request, cabeceras: dict, cookies: dict) -> None:
    """Quita de *request* lo del job que le haya llegado copiado de otra peticion.

    Una cabecera se quita solo si lleva el valor del job: si el job pone
    `Accept: x`, el `Accept` por defecto de una peticion externa se queda.
    """
    for key, value in cabeceras.items():
        if request.headers.get(key) == value.encode():
            del request.headers[key]
    if cookies and request.headers.get(b"Cookie") == "; ".join(
            f"{n}={v}" for n, v in cookies.items()).encode():
        del request.headers[b"Cookie"]
    if isinstance(request.cookies, dict):
        for name in cookies:
            request.cookies.pop(name, None)
    elif isinstance(request.cookies, list):
        request.cookies = [c for c in request.cookies
                           if not (isinstance(c, dict) and c.get("name") in cookies)]


class CookiesTrasElJarMiddleware:
    """Con el jar de Scrapy encendido, la cabecera ``Cookie`` del jar manda.

    Solo existe con ``COOKIES_ENABLED`` (``http.conservar_cookies``, decision
    114). Va detras del ``CookiesMiddleware`` de Scrapy (700), que ya ha metido
    en el jar las cookies configuradas de ``request.cookies`` y ha escrito la
    cabecera con todas. Si ``request.cookies`` sigue lleno, curl_cffi lo manda
    en lugar de la cabecera y las cookies del servidor no llegaban nunca
    (medido): con jar y cookies configuradas, el jar no servia de nada.
    """

    @classmethod
    def from_crawler(cls, crawler):
        if not crawler.settings.getbool("COOKIES_ENABLED"):
            raise NotConfigured
        return cls()

    def process_request(self, request: Request, spider):
        if request.cookies and b"Cookie" in request.headers \
                and not request.meta.get("playwright"):
            request.cookies = {}
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
