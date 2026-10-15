# Decisiones: render con playwright, descarga y robots.txt

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d8"></a>
8. **JS rendering auto-cap** — When a job has `render_js=true`, the worker automatically caps `CONCURRENT_REQUESTS` and `CONCURRENT_REQUESTS_PER_DOMAIN` to lower values (env-configurable) to prevent Chromium memory exhaustion. Jobs without JS are unaffected. See "Deployment: Local vs VPS" section.

<a id="d11"></a>
11. **Browser TLS impersonation must be requested per-request** —
   `scrapy-impersonate` only routes through curl_cffi when the request carries
   `meta["impersonate"]`; the composite handler sets it for every non-Playwright
   request. Without it everything silently falls back to Twisted's TLS and a
   WAF like F5 BIG-IP ASM answers with binary garbage, ending the job with 1
   URL and no error that looks like a block.

<a id="d12"></a>
12. **La espera de render va por el DOM, no por reloj** — antes eran 2 s
   fijos por pagina, y eran la mayor parte del tiempo de render: en el censo
   del 2026-09-10, 44 de los 62 minutos se fueron esperando a paginas que ya
   habian terminado. Ahora `_JS_ESPERAR_DOM_QUIETO` espera a que el DOM lleve
   400 ms sin mutar, con tope en los mismos 2000 ms (el peor caso es lo que
   costaba antes). 30% menos de tiempo con el mismo texto y los mismos
   enlaces en 8 plantillas de 5 sitios. Va como `evaluate` y no como
   `wait_for_load_state("networkidle")` porque un PageMethod no puede capturar
   excepciones: un sitio cuya red no calla nunca se caeria por timeout. La
   promesa siempre resuelve.

<a id="d12b"></a>
12b. **Mirar solo el DOM tiene un punto ciego: la peticion EN VUELO** —
   mientras un XHR viaja no hay mutaciones, asi que el DOM parecia "quieto" y
   la espera resolvia antes de que llegase nada. No se ve en los datos: la
   pagina se guarda con 200, titulo y texto, solo que sin el modulo que monta
   ese XHR. Medido en www.uoc.edu: de 9.895 noticias, 6.555 quedaron con 5-6
   bloques de datos estructurados y solo 1.965 con los 7 de la pagina montada
   — faltaba el de relacionadas y con el ~20 enlaces y ~20% del texto. Que
   unas si y otras no, en el MISMO rastreo, es la firma de una carrera, no de
   un cambio del sitio. Ahora `seo_crawler/render.py` instala un contador de
   fetch/XHR en vuelo con `add_init_script` (antes de navegar, para ver
   tambien la primera peticion) y la espera solo resuelve con ese contador a
   cero, ademas de reiniciarse con cada recurso que termina
   (`PerformanceObserver`) y de un piso de `PLAYWRIGHT_MIN_WAIT_MS`. El tope
   sigue acotandolo todo. Comprobable: `scripts/prueba_espera_render.py`
   (laboratorio con XHR retardado: con 700 ms la espera vieja se dejaba los 10
   enlaces, la nueva los coge). Coste medido en 3 paginas reales: +500/900 ms
   por pagina. Si una plantilla necesita mas que el tope, `render_wait_ms`.

<a id="d13"></a>
13. **Los sitios varian solos: medir una vez no es medir** — al bajar la
   espera, cst.gov.sa parecia perder enlaces; repitiendo 5 veces por modo se
   vio que varia solo (90/99/90/90/90 con la espera de SIEMPRE). Y una pagina
   del canario cayo de 156 a 52 enlaces: con espera fija y con 5 s daba
   tambien 52 — habia cambiado el sitio. Antes de atribuir una diferencia a un
   cambio del crawler, repetirla contra el estado ACTUAL del sitio con la
   configuracion vieja.

<a id="d14"></a>
14. **Los métodos de página de Playwright toleran una navegación** —
   `PageMethod("evaluate", js)` reventaba con "Execution context was destroyed"
   cuando la página redirigía por JS tras `domcontentloaded` (portales legado,
   `index.html` que saltan a la sección). Tras unos cuantos cierres de pestaña
   así, el navegador dejaba de servir páginas y el rastreo seguía "vivo" a 0
   páginas/min. `_evaluar_tolerante` (callable como PageMethod) espera al nuevo
   documento y reintenta. Medido en la UOC: 19 fallos y cuelgue total en 40 min.

<a id="d17"></a>
17. **Página de error de Chromium = repetir sin render** — cuando Playwright
   acaba en `chrome-error://`, la respuesta llegaba como un 307 con destino
   `chrome-error://chromewebdata/` y la URL real quedaba sin estado. Pasa tras
   una redirección que Chromium no sigue (http → `https://host:443/...` con el
   puerto explícito). El spider repite la petición sin render (`_sin_render`),
   que deja el código y la cadena de redirecciones reales.

<a id="d18"></a>
18. **La espera de render se ajusta por cliente** — los listados montados por
   XHR/GraphQL (AEM) pintan sus enlaces 2-3 s después de `domcontentloaded`;
   con el tope de 2 s se guardaban categorías con 0 enlaces a items y los
   huérfanos salían inflados sin aviso. `crawl_behavior.render_wait_ms` fija
   el tope por job y `crawl_behavior.recrawl_patterns` permite repetir solo
   esa plantilla al reanudar. Medir con Playwright a 0/2/3/5/8 s antes de
   afirmar que una página no enlaza algo.

<a id="d19"></a>
19. **robots.txt también en el destino de una redirección con render** — Chromium
   sigue los 3xx por su cuenta, así que el host de destino nunca pasaba por el
   middleware de robots: se guardaron 22 páginas de un SSO con `Disallow: /`.
   Si el render acaba en otro host no interno, la petición se repite sin
   render (misma ruta que `_sin_render`); y si robots corta una cadena de
   redirecciones, `handle_error` registra los saltos con su código en vez de
   dejar que la URL original desaparezca del informe.

<a id="d49"></a>
49. **El alcance del rastreo va por la Public Suffix List, y una `<base href>`
   no navegable se ignora** — `crawl_subdomains` tomaba como raíz las dos
   últimas etiquetas del host, así que para una semilla `.co.uk` la raíz era
   `co.uk` y **cualquier** sitio `.co.uk` entraba en el rastreo y en el informe
   del cliente. Ahora `dominio_registrable()` usa la PSL con `tldextract`, que
   ya viene con Scrapy, sin red (`suffix_list_urls=()`) y con los sufijos
   privados activados, de modo que `usuario.github.io` y `cliente.myshopify.com`
   son sitios distintos y `localhost`/`127.0.0.1` se devuelven tal cual.
   Y una `<base href="javascript:void(0)">` —común en portales antiguos y en
   SPA— dejaba TODOS los enlaces relativos de esa página sin resolver, marcados
   como externos y sin rastrear: el grafo interno de esas páginas desaparecía.
   Comprobado en Chromium: el navegador ignora una base `javascript:` o `data:`
   y mantiene la URL del documento, así que es exactamente lo que se hace ahora
   (solo se acepta una base que resuelva a http/https).

<a id="d50"></a>
50. **Un sitemap de texto, un RSS o un Atom daban cero URLs, y los recursos
   filtrados gastaban presupuesto** — el protocolo de sitemaps.org incluye el
   fichero de texto plano (una URL por línea) y Google acepta además RSS 2.0 y
   Atom como sitemap. `parse_sitemap` solo entendía `<urlset>` y
   `<sitemapindex>`, así que con cualquiera de los otros tres la lista salía
   **vacía** — y con ella `in_sitemap` en falso para TODAS las URLs del sitio,
   de donde salen los huérfanos inflados y una lista de «URLs del sitemap sin
   rastrear» vacía. El de texto exige que la línea empiece por http(s) y no
   tenga espacios ni `<`, para que un HTML de error no pase por sitemap.
   Aparte: el contador de páginas se incrementaba ANTES del filtro por tipo de
   recurso, así que un PDF o una fuente que el job excluye gastaba `max_urls`
   sin dejar una sola fila (medido contra el código anterior: el contador subía
   a 1 con un PDF filtrado). Y `crawl_svg` no hacía nada, porque el SVG se
   clasificaba como `image` tanto por `Content-Type` como por extensión: ahora
   es su propio tipo. Y los dos tamaños estaban **cambiados**: Scrapy
   descomprime el cuerpo pero no toca la cabecera, así que `Content-Length` son
   los bytes del cable y `len(response.body)` el recurso descomprimido —medido:
   143 contra 28.055 en una respuesta gzip—. Se guardaba el comprimido como
   `content_length` y el descomprimido como `transfer_size`. En el censo de
   www.uoc.edu la media de `content_length` era **45 kB** para páginas de
   **760 kB**, con 15.330 filas donde el «transferido» superaba al «tamaño» más
   del doble.

<a id="d59"></a>
59. **Lo que la extensión ya dice no hace falta descargarlo** (R12 de #25) —
   el filtro por tipo de recurso necesita el `Content-Type`, así que se aplica
   **después** de la descarga: se pedía la imagen, se descargaba y se tiraba.
   Visto en vivo en el re-rastreo de progym: el sitio responde a **2,7 s de
   media** y, con 2 peticiones por dominio, **28 de cada 60 respuestas** eran
   recursos que se descargaban para descartarlos — el ritmo cayó de 50 a 10
   páginas por minuto y el contador de items de Scrapy se quedó clavado mientras
   el de páginas seguía subiendo, que es la firma de esto. Ahora, cuando la
   extensión identifica el tipo sin dudas y el job lo excluye, la petición no se
   hace.
   La mitad importante del arreglo es `tipo_por_extension()` devolviendo
   **None** cuando no sabe: la clasificación por extensión respondía `"other"`
   por defecto, así que cualquier URL con un punto en el último tramo
   —`producto-2.5-kg`, `v1.2-guia`— habría pasado por recurso y se habría dejado
   de pedir. Perder páginas de producto en silencio es mucho peor que descargar
   una imagen.

<a id="d81"></a>
81. **El `Crawl-delay` se respeta en `respect`, con tope, y no lo deshace
   AUTOTHROTTLE** (R6 de #25) — Googlebot lo ignora, así que no es una señal
   de SEO; pero este crawler dice respetar robots.txt, y un `Crawl-delay`
   suele ser lo que escribió el administrador después de que un rastreador le
   tumbara el servidor: saltárselo es la forma más rápida de acabar bloqueado
   por el WAF a mitad de censo. `RobotsCrawlDelayMiddleware` lo lee del mismo
   parser que decide los `Disallow` (Protego, `crawl_delay(ua)`, con el mismo
   UA) y lo pone como retraso del **slot de descarga del host**: vale igual con
   render y sin él y no toca la concurrencia ni el auto-cap de render
   (decisión 8). Tres cosas lo habrían deshecho en silencio y están cubiertas:
   AUTOTHROTTLE reescribe `slot.delay` tras cada respuesta y lo bajaba a
   `DOWNLOAD_DELAY` (0) —se vuelve a subir al piso en `response_downloaded`,
   que este middleware conecta después que AutoThrottle; AUTOTHROTTLE puede
   seguir SUBIÉNDOLO si el servidor va lento—; el jitter de ±50% de Scrapy daba
   intervalos de la mitad —en esos slots se apaga—; y el recolector de slots
   inactivos los borra a los 60 s —el retraso queda en `per_slot_settings`—.
   Tope `ROBOTS_CRAWL_DELAY_MAX` = 10 s: un `Crawl-delay: 30` en un sitio de
   20.000 páginas son 7 días de rastreo, y eso no es cortesía sino no poder
   auditar; con 10 s es largo pero terminable. Un valor que no es un número
   positivo y finito no se aplica, y el `request_delay` del job no se baja. Se
   registra en INFO (y cuándo se recorta). En `ignore` y `audit` no se aplica:
   rastrean a propósito lo que robots.txt pide no tocar. Test con el spider
   real y AUTOTHROTTLE encendido midiendo el hueco entre peticiones en el
   servidor (`test_crawl_delay_espacia_las_peticiones_de_cada_host`).

<a id="d98"></a>
98. **Un preset de bot cambia también qué reglas de robots.txt se obedecen, y
   no engaña a un WAF** — el formulario ofrece ahora Googlebot smartphone,
   Bingbot, GPTBot, OAI-SearchBot, ClaudeBot y PerplexityBot (`UA_PRESETS` en
   `frontend/app.js`) con las cadenas que publica cada proveedor, comprobadas
   el 2026-10-16: Google (`google-common-crawlers`), Bing (anuncio de abril de
   2022), OpenAI (`developers.openai.com/api/docs/bots`) y Perplexity
   (`docs.perplexity.ai/guides/bots`); Anthropic publica solo el token
   `ClaudeBot`, y la cadena es la que se ve en los logs. Google y Bing escriben
   `Chrome/W.X.Y.Z` y lo sustituyen por la versión actual: va
   `CHROME_EVERGREEN` (la estable de Chrome de esa fecha, 155.0.8059.84) en un
   solo sitio. El preset `googlebot` de antes (cadena de escritorio sin Chrome)
   se conserva para que los configs guardados con él sigan casando.
   Dos consecuencias que el formulario dice con un aviso cuando el UA —de
   preset o escrito a mano— lleva un token de bot:
   - **robots.txt se evalúa con ese UA.** El `RobotsTxtMiddleware` de Scrapy
     (prioridad 100) corre antes que nuestro `UserAgentMiddleware` (400), así
     que lee el `USER_AGENT` que el worker pasa con `-s`, y Protego busca el
     token del grupo dentro de la cadena entera: con GPTBot se aplica
     `User-agent: GPTBot` y no `*`. El `Crawl-delay` (decisión 81) sale del
     mismo grupo. `tests/test_ua_presets.py` lo comprueba con el middleware y
     el parser reales y las cadenas leídas del `app.js`, para cada bot.
   - **Un WAF puede verlo como bot falso.** La petición sale con la huella TLS
     de un navegador (decisión 11) y desde nuestra IP, no la del proveedor:
     un WAF que verifica bots (DNS inverso, rangos publicados) lo detecta y
     puede bloquearlo o servirle otra cosa. Lo que se mida así puede no ser lo
     que ve el bot de verdad.
   `user_agent` ya era una clave de `JobConfig`: los presets son solo del
   formulario y no añaden claves (`test_jobconfig_claves` sigue igual).
