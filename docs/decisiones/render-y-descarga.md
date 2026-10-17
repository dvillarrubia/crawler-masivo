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
   se conserva para que los configs guardados con él sigan casando. Por lo
   mismo, subir `CHROME_EVERGREEN` no deja huérfanos los perfiles guardados:
   `presetDeUserAgent` casa los presets que la llevan
   (`UA_PRESETS_EVERGREEN`) ignorando la versión de Chrome, así que un perfil
   con la 155 se sigue abriendo como «Googlebot smartphone» y se relanza con la
   versión del día, que es lo que hace el bot de verdad.
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

<a id="d103"></a>
103. **Una respuesta que no es la página no se guarda como la página, y un
   rastreo detrás de un muro no es un censo** (PR 2.1 del plan, versión
   determinista de #10). Con `HTTPERROR_ALLOW_ALL` todo llega al spider: un
   desafío de Cloudflare servido con 200, un captcha o el cuerpo binario de un
   F5 (decisión 11) se guardaban como páginas normales, indexables y con sus
   incidencias, y el job acababa `completed`. Ahora cada respuesta final lleva
   `urls.response_class`, de una función pura en `shared/respuestas.py` que
   usan el spider y `shared/descarga_cruda.py` (un solo «esto es un muro»,
   decisiones 62 y 82): `contenido`, `challenge_waf`, `captcha`,
   `rate_limited`, `respuesta_binaria`, `login_requerido` y `desconocido`.
   - **Conservadora, porque un falso positivo esconde una página real.** Una
     clase de muro sale con una señal inequívoca (`cf-mitigated: challenge`,
     un 429, bytes de control en un cuerpo declarado HTML) o con DOS que se
     confirman: un título o un marcador de desafío **y** una página sin
     contenido (menos de 150 palabras o un código de error). Un **503 no es
     inequívoco**, ni con `Retry-After`: es lo que Google pide servir en un
     mantenimiento y lo que manda un origen saturado, o sea un `5xx_error` del
     sitio; solo es muro si el cuerpo lo dice. Las palabras cuentan cada
     carácter CJK, kana, hangul o tailandés como una (decisión 44): con `\w+`
     treinta párrafos de japonés eran 91 y cualquier artículo salía «sin
     contenido». Y un cuerpo con BOM o charset UTF-16/UTF-32 o ISO-2022 lleva
     bytes nulos o ESC de forma legítima: no es binario. Los marcadores son
     los de la página de desafío, no los que el proveedor inyecta en las
     normales: Cloudflare mete `/cdn-cgi/challenge-platform/scripts/jsd/` en
     páginas buenas, así que cuenta `_cf_chl_opt`; e Imperva inyecta
     `/_Incapsula_Resource?SWJIYLWA=` en TODAS, así que cuenta solo «Incapsula
     incident ID» (con el script, un 404 de verdad de imperva.com, 982
     palabras, salía muro: el código de error hacía de segunda señal). Tres títulos —`Access denied`, `403 Forbidden`,
     `Security check`— son también los de un error **honesto** del servidor,
     y con un código de error se quedan en `contenido`: los 12 403 del censo
     de www.uoc.edu son directorios de Apache, y un enlace interno a algo
     prohibido es un hallazgo. Solo cuentan servidos con 2xx y sin contenido.
     Un artículo que habla de captchas o lleva un reCAPTCHA es contenido. El
     título se casa al principio y por palabra entera: «Security checklist»
     ya no casa con «security check», que con la subcadena de la 62 sí. Y
     salvo cinco frases que solo usa un muro («Checking your browser…»,
     «Request unsuccessful…», «Attention required», «Pardon our
     interruption», «Verify you are human»), tiene que ser el título ENTERO:
     la frase, puntuación y como mucho un separador con la marca del WAF o el
     nombre del propio host («Attention Required! | Cloudflare», «Access
     Denied | x.com»). «Captcha Solver Pro - Comprar», «Security check-in en
     el aeropuerto» o «Access Denied (2024) - Ficha» son páginas.
     `login_requerido` (401, o redirección a un login de OTRO host) y
     `desconocido` (2xx HTML con el cuerpo vacío) son la respuesta del sitio:
     no cuentan como muro.
   - **Un muro no es indexable ni lleva incidencias de página.**
     `estado_indexabilidad(response_class=…)` lo saca antes que el código
     («Bloqueada (WAF)», «Bloqueada (captcha)», «Limitada (rate limit)»,
     «Respuesta no valida (binaria)»): un 403 de Cloudflare no es un «Client
     Error (403)» del sitio. El spider no extrae nada de un muro (ni
     metadatos, ni titulares, ni enlaces que seguir). El analizador no le emite
     `4xx_error`, `5xx_error`, `h1_missing` ni `sitemap_orphan`, y un canonical
     o un hreflang que apunta a un muro no está «roto»: el destino no se ha
     visto. En su lugar, **una** `respuesta_no_valida` (warning) por host y
     clase, con las páginas afectadas, los códigos y cinco ejemplos (decisión
     53). Al reanudar, los muros se repiten como los 5xx.
   - **Con render manda el cuerpo.** scrapy-playwright toma el código y las
     cabeceras de la primera respuesta de `page.goto` y el cuerpo del DOM al
     acabar la espera: si Chromium pasa el desafío gestionado, el 403 y el
     `cf-mitigated` son del desafío y el cuerpo es la página. Con
     `renderizada` ni esa cabecera ni el código de error cuentan; un desafío
     que no se resolvió se sigue viendo en el cuerpo. Sin esto, el remedio que
     propone la ficha («prueba con render») no podía funcionar nunca.
   - **La salud del job, por ventana.** El spider mira las últimas
     `salud_ventana` respuestas (50) y, si la fracción `salud_umbral` (0,8)
     no son la página, deja la marca `job:<id>:bloqueo` y para con
     `bloqueado_por_waf` o `rate_limited` (el que más pese). Solo juzga con la
     ventana llena: tres muros al arrancar no paran nada. Solo cuentan las
     URLs **internas**, en la ventana y en el total del worker: con
     `follow_external`, una tanda de enlaces a redes sociales detrás de
     Cloudflare paraba el rastreo de un sitio sano. El rastreo corto que
     acaba entero detrás del muro sin llenar la ventana —el de 1 URL de la
     decisión 11— lo juzga el worker al cerrar sobre el TOTAL de la base de
     datos. Las dos cosas acaban en `failed` con esa causa, sin análisis ni
     comparación (decisiones 28 y 32), y en `jobs.health` (recuentos por
     clase, proporción, causa y la ventana que vio el spider). Las claves van
     en `CrawlBehaviorConfig` (decisión 33), con `parar_si_bloqueado` para
     seguir rastreando y juzgar solo al cerrar.
   - **Sobrevive a las reanudaciones sin bloquearse para siempre.** Un
     estancamiento (decisión 15) de un job bloqueado no se reencola: el worker
     lo decide con la base de datos, que sí sobrevive. La ventana del spider
     NO se siembra con la ejecución anterior: llena de muros, la primera página
     buena tras reanudar la volvería a parar aunque el sitio se hubiera
     recuperado. Por lo mismo, la marca `job:<id>:bloqueo` es de UNA
     ejecución y el worker la borra al marcar el job `running`: si la
     anterior no llegó a la finalización (reanudación pedida, un despliegue,
     un estancamiento sin salud calculada), la marca vieja dejaba `failed` un
     rastreo que había salido limpio.
   - **La comparación no alerta con un censo bloqueado** (#36 F2): se marca
     «NO CONCLUYENTE: censo bloqueado» con la causa y no salen alertas. Y en un
     censo entero, una página que fue muro en cualquiera de los dos queda fuera
     (ni cambia, ni sale del índice, ni desaparece: `sin_comparar_por_muro`), y
     sus incidencias de antes son «no se sabe», no «resueltas».

   Medido el 2026-10-16 desde la IP local, con `curl_cffi` y `chrome124`:
   - **progym.es**: la home sale de la caché de Cloudflare con 200 →
     `contenido`; `/blog/`, `/contacto/` y `/maquinas-de-cardio/` responden
     403 con `cf-mitigated: challenge` y «Just a moment...» → `challenge_waf`
     las tres (el desafío sin la cabecera también: por `_cf_chl_opt`). Es el
     fixture `tests/fixtures/respuestas/cloudflare_desafio_progym.html`.
   - **Contenido de verdad, sin un falso positivo**: www.uoc.edu/es,
     www.uoc.edu/es/estudios, www.lopesan.com/es/ y /es/hoteles/,
     blogs.uoc.edu/. Y tres errores honestos que se parecen a un muro y se
     quedan en `contenido`: una ficha de penguinlibros.com que responde 403
     con la página entera (349 kB, nginx), un 403 de Apache de www.uoc.edu y
     el wp-login de blogs.uoc.edu (403 «403 Forbidden», openresty).
   - **En la base local** (solo lectura): sin cuerpos guardados
     (`raw_html` vacío) no se puede reclasificar, así que se cuenta por
     título y código. **1 página de muro indexable**: el wp-login de
     blogs.uoc.edu, guardado con 200, «403 Forbidden» y 3 palabras. **270
     respuestas 429** en el censo `penguin-estructura-es-2026-07-30` (19.607
     URLs), con 270 `4xx_error` y 152 incidencias más sobre páginas que no se
     vieron; en su peor ventana de 50 hubo 38 (76%), así que con el umbral por
     defecto el rastreo **no** se habría parado —lo que cambia es que esas
     270 dejan de ser «rotas» y se repiten al reanudar—. Los 65 403 de los
     demás censos son respuestas del sitio (45 fichas enteras de penguin, 12
     Apache de la UOC, 8 de blogs) y siguen siendo `contenido`; los 5 401 de
     Saunier, `login_requerido`.
   Fuera, para la fase con modelo de #10: el soft-block que sirve la página
   con otro contenido (precios a cero, «producto no disponible» en todas), la
   caída de tamaño por plantilla y la pausa con reanudación automática más
   tarde. Los censos anteriores no tienen la columna: hay que **re-rastrear**.

<a id="d109"></a>
109. **El tiempo del servidor y la compresión se miden en el handler, y un
   aviso de servidor va por host o por plantilla** (B3 de #150) —
   `response_time_ms` **no es TTFB**: sin render es `download_latency` de
   scrapy-impersonate, que abre una `AsyncSession` nueva por petición y mide
   DNS, TCP, TLS, el servidor y el cuerpo entero; con render, desde `goto`
   hasta el último `PageMethod`, espera de render incluida. Y
   `Content-Encoding` lo quitaban los dos handlers al descomprimir: con lo
   guardado, `transfer_size == content_length` no demostraba «sin comprimir»
   (sin `Content-Length`, `transfer_size` es el cuerpo). Ahora
   `handlers.ImpersonateConMedidas` sobrescribe el `_download_request` de
   scrapy-impersonate (privado: versión **fijada a 1.10.0**, la instalada; la
   guía hablaba de 1.9.0) con `curl_infos` en la sesión y deja en `meta`
   `ttfb_s = STARTTRANSFER − PRETRANSFER` (el servidor sin DNS, TCP ni TLS),
   la cabecera servida (`identity` si no venía), los bytes del cable y la
   versión HTTP — que **cierra la limitación de `http_version` sin render**
   (`CURLINFO_HTTP_VERSION`: 1→1.0, 2→1.1, 3→2, 30→3). Las borra al empezar
   cada descarga: `RetryMiddleware` copia la `meta` y el reintento de un 503
   heredaría su TTFB. Con render, un `PageMethod` de Navigation Timing:
   `responseStart − requestStart` y `encodedBodySize` frente a
   `decodedBodySize` (`comprimido` sin el nombre: scrapy-playwright quita la
   cabecera aunque Chromium la tiene). Comprobado con un servidor local con
   300 ms de espera: curl da 302 ms y Chromium, con la interceptación de
   scrapy-playwright, 301 ms; gzip 157 frente a 34.026 bytes. Una página
   `_sin_render` de un job con render no da TTFB (mezclaría dos fuentes), y
   una sonda de soft 404 no da nada (no es URL del censo). Columnas
   `urls.ttfb_ms` y `urls.content_encoding`; `response_time_ms` se queda como
   está (CSV, filtros, Explorador).
   Avisos (`analysis/avisos_de_servidor.py`), solo sobre HTML interno 200 que
   es la respuesta del sitio (103): `respuesta_lenta` por **(host,
   plantilla)** —`config.templates` y si no, `firma_de_ruta`, ahora
   `shared.plantillas.plantilla_de`, que usa también `check_js_templates.py`—
   con 20 páginas o más, la **mediana** y el p90, `info` por encima de 1.000 ms
   y `warning` por encima de 3.000 ms (`AnalysisThresholdsConfig`; **valores a
   medir en censos antes de darlos por buenos**, 13), con la fuente y la
   concurrencia del job en el detalle: con 32 peticiones a la vez un origen
   lento sale más lento que con 2. Nada de FreeCrawl: ni por página (en un
   sitio lento sale en todas) ni los 600 ms como Core Web Vitals (CrUX mide a
   usuarios). Un censo sin `ttfb_ms` no avisa: caer a `response_time_ms` sería
   afirmar lo que no se midió (67). `html_sin_comprimir`, `info`, por **host**
   (53): solo con la cabecera medida, cuerpos de 1 KB o más, `por_plantilla`
   cuando el host mezcla; `zstd` cuenta como comprimido, y si el job pisa
   `Accept-Encoding` en sus cabeceras no se dice nada. Anclados en la raíz
   del host (o la HTML 200 a la que redirige) y en la URL menor del grupo,
   nunca en el primer id (88). La ficha cita lo que dice Google: un servidor
   lento o sin comprimir se rastrea menos; no habla de posicionamiento.
   **Sin medir todavía en un censo real** (no había rastreos en esta sesión):
   la estabilidad de la mediana entre dos canarios del mismo día (±30%) es la
   condición para fijar los umbrales. Con proxy (`PROXY_LIST`) el TTFB incluye
   el salto al proxy y el detalle no lo distingue.

<a id="d110"></a>
110. **Un certificado inválido se dice, una vez por host, y con el umbral de
   7 días, no 30** (B6 de #150) — curl_cffi rechaza un certificado caducado
   con `CertificateVerifyError` (código 60) y `handle_error` solo conocía las
   excepciones de Twisted: la página quedaba `status_group='error'` con el
   motivo solo en el log. Ahora `tls_error` (curl 35, 51, 58, 59, 60, 83, 90 y
   91, o `net::ERR_CERT_*`/`ERR_SSL_*` de Chromium) con el motivo en
   `status_text` («TLS: curl 60: SSL certificate OpenSSL verify result: self
   signed certificate (18)» es lo que dice curl-impersonate). Y una **sonda
   por host** (`seo_crawler/sonda_tls.py`, el patrón de la 105: sin item, sin
   gastar `max_urls`, a `jobs.tls`): la primera vez que un host https interno
   responde o falla, dos conexiones con `ssl` en un hilo —una verificada con
   el almacén del sistema y el nombre del host, que da el motivo por el
   `verify_code` de OpenSSL (caducado, nombre, autofirmado, cadena
   incompleta…), y si falla, otra sin verificar y admitiendo TLS 1.0 para leer
   el certificado (`binary_form=True` + `cryptography`; sin verificar,
   `getpeercert()` da `{}`) y la versión—. Con render se usa
   `playwright_security_details`, que nadie leía; con proxy se omite (un
   socket directo sale por otra IP). `spider_idle` espera a la sonda en vuelo.
   Tres avisos por host, anclados en la raíz: `tls_certificado_invalido`
   (`error`), `tls_caduca_pronto` (`warning`, **7 días** o menos,
   `tls_dias_aviso`): Let's Encrypt renueva a 30 días y con certificados de 45
   o 47 días un sitio sano estaría siempre por debajo de 30, que es el umbral
   de FreeCrawl; y `tls_version_antigua` (`error`, TLS 1.0/1.1: Chrome no
   conecta desde 2020). Lo negociado es el **máximo** que admite el servidor:
   que además acepte 1.0 no se mide y no se afirma. La firma SHA-1 no tiene
   consecuencia SEO y no se mira. Si el host respondió 2xx por https en el
   rastreo, curl o Chromium sí validaron el certificado: un fallo de la sonda
   es del almacén de CA o de un WAF que corta el handshake, y no se afirma.
   Las páginas `tls_error` de un host con el certificado confirmado inválido
   no repiten `connection_error` (36). Los días son los **del día del
   rastreo** (`dias_restantes` en el JSON): re-analizar no los recalcula.
   Probado con servidores locales: caducado (con una CA propia →
   `caducado`; con el almacén del sistema → `autofirmado`), a 3 días, de otro
   nombre y uno que solo habla TLS 1.0 → `TLSv1`.

<a id="d111"></a>
111. **El `Retry-After` de un 429 o un 503 se respeta en todos los modos, con
   tope y sin que AUTOTHROTTLE lo deshaga** (B5 de #150) — `RetryMiddleware`
   repetía el 429 al instante y AUTOTHROTTLE no frena con un 429 rápido (no
   sube el retraso con respuestas que no son 200 salvo que tarden), así que
   una ráfaga de 429 seguía al mismo ritmo. `RetryAfterMiddleware` generaliza
   la 81 con caducidad y **sin depender de `ROBOTSTXT_OBEY`**: con un
   `Retry-After` de N s (segundos o fecha HTTP, `shared.respuestas.
   retry_after_segundos`, la misma función que usa ya `shared/llm.py`; una
   fecha pasada es 0 y lo ilegible no es espera), el slot del host pasa a N s
   de retraso sin jitter durante N s, acotado a `retry_after_max_s` (60 por
   defecto, máximo 600). Se fija en `response_downloaded`, que llega **antes**
   de que el slot saque la siguiente petición y después de AutoThrottle; se
   reaplica con cada respuesta de la ventana (las 200 que siguen en vuelo lo
   bajarían) y en `process_request`. El reintento sale del mismo slot, así que
   espera. Caducada la ventana, el slot vuelve a `piso_de_slot`: el
   `Crawl-delay` de la 81 o el `DOWNLOAD_DELAY` del job, nunca a 0 (el
   Crawl-delay solo sube un retraso, así que no deshace la pausa). Cada pausa
   estampa el **latido** del spider: con un host en pausa los reintentos no
   pasan por `parse`, el latido va cada 50 URLs y el vigilante de
   estancamiento daba por muerto un rastreo que esperaba a propósito (600 s
   × 3 intentos eran justo los 30 minutos). Lo respetado va a
   `jobs.health["retry_after"]` (`veces`, `max_pedido_s`, `max_aplicado_s`,
   `truncados`, `tope_s`), por Redis y acumulado entre reanudaciones: no es
   una incidencia SEO, es cortesía y confianza del dato. La ventana de salud
   de la 103 no se toca: respetar la pausa baja los 429. Google dice que un
   429 o un 503 le hacen bajar el ritmo; **no** está comprobado que honre
   `Retry-After`, así que esto no se vende como paridad con Googlebot.
   Probado con el spider real contra un servidor local: un 429 con
   `Retry-After: 1` dos veces y un 503 con fecha a 2 s acaban en 200, con los
   huecos medidos en el servidor (≥ 0,9 s y ≥ 1 s) y `veces = 3`.
