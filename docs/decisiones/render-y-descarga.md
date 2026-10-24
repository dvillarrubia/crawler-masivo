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
   - **AWS WAF detrás de CloudFront** (añadido el 24-oct-2026): la página de
     error de CloudFront con «Request blocked.» y «Generated by cloudfront» es un
     muro. Las dos cosas juntas, porque la misma plantilla sirve los 502/504 del
     origen, que son la respuesta del sitio. blogs.uoc.edu bloquea así la IP
     del servidor y su canario salía «completado» con la portada en 403 como
     `contenido`.


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

   **Revisión (2026-10-18): una pausa no puede esconder un muro.** La frase
   «la ventana de salud de la 103 no se toca: respetar la pausa baja los 429»
   era falsa con un host que responde 429 a TODO. Con la frontera llena, el
   reintento va con menos prioridad que los primeros intentos, así que cada
   primer intento salía espaciado por la pausa y el primer 429 definitivo no
   llegaba a `parse` —ni a la ventana— hasta haber pedido toda la frontera.
   Reproducido con el arnés de la 103 (300 URLs en 429): **5,4 s** y
   `rate_limited` sin el middleware; **más de 10 minutos sin cerrar** con él y
   una pausa de solo 2 s. Con el `Retry-After: 30` real y 300 URLs son horas, y
   con 20.000 en la frontera el rastreo moría por `max_runtime_hours` ocupando
   el hueco del worker: el latido de cada pausa impedía además que el
   vigilante lo cortara. Ahora, si un host devuelve `RETRY_AFTER_MAX_SEGUIDAS`
   (5) respuestas 429/503 a peticiones que salieron **con la pausa ya
   abierta** y sin ninguna de otra clase entre medias, deja de pausarse hasta
   que responda otra cosa; la ventana de la 103 lo cierra como antes. Las que
   estaban en vuelo al llegar el primer 429 no cuentan (salieron antes de la
   pausa: con 8-16 por host, un límite pasajero las devuelve todas a la vez),
   y se distinguen por `download_latency`. Como mucho son 5 pausas (5 minutos
   con el tope de 60 s); queda en `jobs.health["retry_after"]["abandonos"]`.
   Test: `test_un_429_masivo_para_tambien_respetando_el_retry_after` (el arnés
   con el middleware, < 60 s; sin el arreglo agota los 180 s) y tres unitarios.
   De paso, `crawl_behavior.retry_after_max_s` pasa a `None` por defecto: los
   defaults del schema se copian en `jobs.config`, así que con un 60 el worker
   pasaba siempre `-s RETRY_AFTER_MAX=60` y la variable de entorno no hacía
   nada.

<a id="d112"></a>
112. **Un robots.txt que responde 5xx, 429 o no responde se guarda y se avisa
   por host, y el rastreo sigue** (sección 6 de #149, primer paso de la Ola
   7.3). Scrapy monta el parser con el cuerpo sea cual sea el código —Protego,
   sobre el HTML de un 503, lo permite todo— y si la descarga lanza una
   excepción deja el parser a `None` y deja pasar todo; nada llegaba a la base
   de datos ni al informe (solo `robotstxt/response_status_count/<código>` en
   las stats y un ERROR en el log). Google, en cambio, con un 5xx o un 429 da
   el **sitio entero por bloqueado** mientras dure (los errores de red y los
   timeouts cuentan como 5xx; pasados 30 días usa la última copia en caché), y
   con un 4xx —401 y 403 incluidos— lo da todo por permitido.
   - **Se guarda por host y job**, en la tabla `robots_txt` (la que #36 espera
     para el historial): código, error, `response_class`, intentos (con los
     reintentos de Scrapy), bytes, SHA-256 y, solo de un 2xx, el cuerpo
     recortado a los 500 KiB que lee Google (`recortado`). Lo anota
     `RobotsTxtRegistroMiddleware` (prioridad 90, por debajo de Retry y
     Redirect, así que ve la respuesta FINAL) reconociendo la descarga por la
     URL original `/robots.txt`: la de `RobotsTxtMiddleware` en `respect` y
     `audit` y la del spider para los sitemaps, que es la única en `ignore`. No
     se tocan `_parse_robots` ni `_robots_error`, privados de Scrapy (la
     decisión 48 ya pagó replicar su middleware). Solo hosts internos y como
     mucho 200 por job (`ROBOTS_TXT_MAX_HOSTS`): un multisite con
     `crawl_subdomains` no llena la tabla, y lo que pase del tope queda «no
     evaluado». La primera descarga de cada ejecución es la que cuenta
     (con la que Scrapy decidió); reanudar sustituye la fila, no la duplica.
   - **Se pide otra vez al cerrar**: si falló, cuando el spider se queda sin
     trabajo (`spider_idle`) se repite UNA vez sin reintentos y se anota en
     `comprobacion`. Nuestra IP no es la de Google, y un 503 puntual a mitad
     de rastreo no es lo mismo que uno que dura. Si a la segunda respondió, su
     cuerpo es el que se evalúa (decisión 113): es lo más parecido a la copia
     que Google guarda de un robots.txt que va y viene. La espera tiene tope
     (`DOWNLOAD_TIMEOUT` + 15 s): no puede dejar un rastreo sin cerrar.
   - **`robots_txt_inaccesible`, una por host**, anclada en la raíz si se
     rastreó y si no en la URL MENOR del host (no depende del orden de las
     filas ni del de rastreo, lección de la 99; ver la revisión al final). `error` si falló también al cerrar o no se
     llegó a repetir (un rastreo cortado por `max_urls` no pasa por
     `spider_idle`); `warning` si a la segunda respondió (`intermitente`) o si
     es un **429**: para Google es un 5xx, pero un 429 a nuestra IP suele ser
     un límite para el rastreador (es `rate_limited`, decisión 103) y Googlebot
     puede no recibirlo. Un 4xx no avisa (es «sin robots.txt», también para
     Google); un **muro** delante (403 de Cloudflare, captcha) tampoco: es «no
     evaluado», no el 4xx del sitio que Google leería como «todo permitido»
     (103). Un `IgnoreRequest` (tope de redirecciones) tampoco dice nada del
     sitio.
   - **El rastreo sigue pidiendo las URLs del host**, como hasta ahora (todo
     permitido), y **no queda `failed`**. Seguir a Google al pie de la letra
     sería tratarlo como `Disallow: /`: cero páginas con respuesta y un job
     `failed` (48), cuando es un hallazgo del SITIO y no un fallo del dato
     (28, 32), y desde una IP que no es la de Google. No es tampoco un muro
     (103): no cuenta para la salud del job. La cortesía la siguen poniendo los
     reintentos, AUTOTHROTTLE y el `Crawl-delay` de cuando sí responde (81).
   - **La indexabilidad no cambia por el 5xx**: no sabemos si Google tiene
     copia en caché ni si el fallo es pasajero. Se avisa; no se afirma (67).
     `urls.bloqueada_googlebot` queda NULL en ese host.
   Los censos anteriores no tienen filas: no se avisa nada ni se cambia nada,
   y para tenerlo hay que **re-rastrear**. Tests: arnés real con 503, 500,
   429, timeout, 404, el desafío real de Cloudflare y 200
   (`tests/test_spider_robots_txt.py`, uno contra Postgres que comprueba la
   fila y que reanudar la sustituye) y el analizador contra SQLite
   (`tests/test_analyzer_robots_db.py`: un aviso por host aunque haya 500
   URLs, dos hosts dos avisos, ancla estable con las filas desordenadas,
   `indexable` sin cambios). Pendiente de medir en producción: cuántos hosts
   de la cartera responden hoy 5xx/429 en `/robots.txt` (decisión 13: es hoy,
   no el día del rastreo).
   **Revisión (2026-10-18): el ancla es la URL MENOR, no la de id más bajo.**
   «No depende del orden de las filas» era verdad dentro de un censo, pero el
   id sigue al orden de RASTREO: dos censos iguales rastreados en otro orden
   anclaban el aviso en páginas distintas, y la comparación, que casa por
   (tipo, url, group_key) (decisión 88), daba el mismo hallazgo por resuelto y
   por nuevo a la vez. Ahora `_ancla_de_origen` elige por URL, como
   `avisos_de_html` (108) y `avisos_de_sitemaps` (126); también entre la raíz y
   su variante con parámetros, que tienen el mismo `path`. Vale para los tres
   avisos que cuelgan de ahí (`robots_txt_inaccesible`,
   `robots_difiere_para_googlebot`, `bot_busqueda_ia_bloqueado`), y la muestra
   por plantilla de la 129 y los ejemplos de `robots_difiere_para_googlebot`
   son ahora las URLs menores, no las primeras que llegan: con otra muestra un
   bot de IA podía pasar de «todo» a «parte» y el aviso salir o no. Buscando el
   mismo patrón salieron seis avisos agregados más que anclaban en la primera
   fila leída (`order_by(Url.id)` o una consulta sin orden, que en la práctica
   es el de inserción): `respuesta_no_valida` (103), `structured_data_*` (73),
   `head_cortado` y `meta_fuera_del_head` (102), `image_missing_alt` (64b) y las
   cuatro cabeceras de seguridad por host (53); sus `paginas_ejemplo` también
   van por URL. Test: `tests/test_anclas_por_url_db.py` monta el mismo censo en
   orden de URL y al revés, ejecuta los checks y exige los mismos (tipo, URL,
   clave) y los mismos detalles; falla sin el arreglo. Los censos ya
   analizados cambian de ancla al re-analizarlos: la primera comparación de un
   censo nuevo contra uno viejo sin re-analizar puede dar estos avisos por
   movidos una vez (y la 91 ya exige re-analizar el viejo si el código
   difiere).
   En la revisión de esa PR salieron dos más: `soft_404_candidato` agrupado
   (la huella de una sonda que casa con una plantilla) se anclaba en el id
   menor de sus páginas —`sorted()` sobre ids— y ahora va por URL; y las
   cabeceras de seguridad se anclaban en la URL menor del HOST, que con una
   página en `http://` (menor que `https://`) dejaba `missing_hsts` justo en
   la página donde HSTS no aplica y que no entra en su cuenta. Cada tipo se
   ancla ahora en la URL menor de las páginas a las que les falta ESA
   cabecera.

<a id="d114"></a>
114. **Las credenciales del job solo van al sitio, y el rastreo no guarda
   cookies** — `HttpConfigMiddleware` ponía las cabeceras propias, las cookies
   y el `Authorization: Basic` del bloque `http` en **todas** las peticiones.
   Medido con el arnés y el código de `6126fd5` (semilla en `localhost`,
   `127.0.0.1` como tercero, curl_cffi 0.16.3, Chromium 1194): el Basic, la
   cabecera propia y la cookie configurada llegaban al tercero en su
   robots.txt, en el destino de un 302 (el staging que manda a un SSO) y en un
   enlace externo con `follow_external`. Con render, además, Chromium conserva
   las cabeceras que pone `route.continue_` al seguir una redirección, también
   a otro host: el Basic llegaba al SSO dentro de la misma navegación, antes
   de que el spider pudiera hacer nada. Y `use_scrapy_headers` da las
   cabeceras de Scrapy a toda navegación, iframes de terceros incluidos
   (revisado en el código de scrapy-playwright 0.0.48).
   - **A quién.** A los hosts de las semillas, con y sin www, y a sus
     subdominios con `crawl_subdomains`: el mismo criterio que
     `_is_internal` (`url_en_hosts`, con el host en IDNA), pero con una foto
     tomada al cargar el job. El host al que salta una semilla pasa a ser
     interno para el rastreo (se sigue y se enlaza), **no** para las
     credenciales: ese salto es justo el del staging a su SSO. Si el sitio de
     verdad está en el destino, se añade como semilla. A las demás peticiones
     se les quita lo del job que les llegue copiado (el `RedirectMiddleware`
     de Scrapy, el de robots.txt, copia cabeceras). `Accept-Language` no es un
     dato del cliente sino cómo se mide, y va a todas.
   - **Redirecciones.** Sin render las sigue el spider (decisión 27): cada
     salto es una petición nueva y decide en el middleware. Con render no se
     puede impedir que Chromium siga un 3xx, y el salto ni pasa por
     `page.route`, así que lo sensible no va nunca en la petición de Scrapy
     de una página con render: las cookies se ponen en el contexto con
     `add_cookies` atadas a los dominios del sitio (Chromium no las manda a
     otro), y las cabeceras propias y el Basic, solo en el documento
     principal, que `render.preparar_pagina` descarga con `route.fetch(...,
     max_redirects=0)`. Un 3xx se le entrega a Chromium **sin** `Location`
     (que no lo siga) y el destino queda en `meta["_location_render"]`: lo
     sigue el spider, como sin render. Contrapartida: `route.fetch` sale por
     la red de Playwright y no por la de Chromium, con otra huella TLS; solo
     pasa en los jobs con render **y** cabeceras propias o Basic, que son los
     staging. Recursos, iframes y navegaciones de la propia página van sin
     nada del job.
   - **Cookies del servidor.** Googlebot no guarda cookies, y una de idioma,
     geo, A/B o consentimiento cambiaba lo que medíamos frente a lo que ve
     Google: el jar de Scrapy estaba encendido (`COOKIES_ENABLED` no se fijaba
     y Scrapy lo trae a `True`) y con render las del sitio y las puestas por JS
     se acumulaban en el contexto (medido: `/c/lee` recibía `servidor=1;
     js=1`). Ahora `COOKIES_ENABLED = False` en settings.py, y la clave nueva
     `http.conservar_cookies` (por defecto `false`, en `HttpConfig`, en el
     formulario y en `projects/_ejemplo`) lo enciende con `-s` desde el
     worker. Con render y sin conservar, cada pestaña borra del contexto, por
     nombre y dominio, las cookies que no son las configuradas: un
     `clear_cookies()` a secas dejaría sin la cookie del staging a otra de
     las 4 pestañas que esté navegando. `localStorage` y `sessionStorage` no
     se vacían (Google tampoco los conserva entre cargas): queda por medir.
   - **Las configuradas se siguen mandando** (son las de los staging y del
     consentimiento, plan, «Qué NO hacer» 7). Sin jar, como cabecera `Cookie`
     escrita por el middleware: por `request.cookies` las pasa
     scrapy-impersonate al motor de cookies de curl, que las descarta para un
     host sin punto como `localhost` (medido). Con jar, por `request.cookies`
     para que el `CookiesMiddleware` las junte con las del servidor, y
     `CookiesTrasElJarMiddleware` (705) vacía `request.cookies` después: con
     las dos cosas, curl_cffi 0.16.3 mandaba `configurada=abc;
     configurada=abc; servidor=1` (la guía de #149 decía que las sustituía;
     con esta versión las concatena). **Con render no llegaban nunca**:
     Chromium ignora la cabecera `Cookie` de scrapy-playwright (medido:
     `/c/inicio` sin cookie, `/c/lee` solo con las del sitio). Ahora llegan.
   - **La preparación de la pestaña no corría.** `PLAYWRIGHT_PAGE_INIT_CALLBACK`
     no es un setting de scrapy-playwright (0.0.48 solo lee
     `meta["playwright_page_init_callback"]`), así que `preparar_pagina` no se
     ejecutaba en producción y el contador de peticiones en vuelo de la
     decisión 12b no estaba instalado: el laboratorio
     (`scripts/prueba_espera_render.py`) lo inyecta él mismo. Va ahora en
     `_playwright_meta`. Efecto esperado en los rastreos con render: la
     espera deja de resolver con un XHR en vuelo (+500/900 ms por página
     medidos en 12b, acotado por el tope) y pueden salir más enlaces y texto.
   - **Lo que cambia en los censos.** Hay que **re-rastrear**: nada de esto se
     arregla re-analizando. En sitios con cookie de idioma, geo, A/B o
     consentimiento, la comparación con el censo anterior (decisión 68)
     puede sacar cambios que son nuestros y no del sitio; con render, además,
     por el contador de 12b. Repetirlo con la config vieja antes de atribuirlo
     (decisión 13): `--set http.conservar_cookies=true`. La comparación de
     configuración entre censos (#36) debe contar `conservar_cookies`.
   Tests: `tests/test_credenciales_y_cookies.py`, con el SeoSpider real, sus
   middlewares y la descarga de producción (curl_cffi y Playwright), y el
   servidor anotando lo que le llega. Contra `6126fd5`, 10 de los 16 que
   había entonces fallan.
   **Revisión (2026-10-17).** El host no basta: un enlace `http://` al host
   de una semilla `https` (los viejos, que luego redirigen con un 301) llevaba
   el Basic, las cabeceras y las cookies **en claro**, antes del salto. Un
   navegador ata la autenticación al origen. Ahora `_lleva_credenciales` exige
   además el esquema de alguna semilla, y con render las cookies se ponen con
   `secure` si ninguna semilla es `http`. Comprobado sin reproducir fallo: con
   `add_cookies` y un dominio sin punto, Chromium la trata como cookie de host
   (medido: `sso.a.test` no la recibe), así que el SSO de un subdominio no la
   ve; y un puerto distinto del mismo host sigue recibiéndolas, que se deja
   así. Tests: `test_con_semillas_https_no_van_en_claro_por_http` y
   `test_render_cookies_con_secure_si_solo_hay_semillas_https`, que fallan
   sin el arreglo.


<a id="d119"></a>
119. **La paridad móvil se mide con una muestra por plantilla, en la misma
   ventana y con el dispositivo entero; lo que cambia solo no se afirma**
   (#150, Ola 7.4 de `docs/PLAN_MEJOR_CRAWLER.md`). Google indexa lo que
   recibe su UA de smartphone, y el censo se rastrea de escritorio: un sitio
   con servido dinámico o con m-dot podía servir a un móvil otro canonical,
   un `noindex` o un menú sin enlaces y no lo veía nada. Con
   `crawl_behavior.paridad_movil_muestra` (0 de serie, apagado; hasta 500) el
   spider vuelve a pedir, como mucho `paridad_movil_por_plantilla` (3) por
   plantilla, las páginas internas HTML 2xx que no son un muro, con el **otro
   perfil**: móvil si el UA del job es de escritorio, escritorio si ya es de
   móvil (`shared/perfiles.py`, `es_ua_movil`). La plantilla es la de
   `config.templates` del job o, sin ellas, `shared.plantillas.firma_de_ruta`
   (la `plantilla_de` de la rama de #158 no está en esta base).
   - **Un perfil es el dispositivo entero, no la cabecera.** Sin render, el UA
     de móvil y la huella TLS `chrome131_android` de curl_cffi (si la versión
     instalada no la tiene, se dice en el log y sale con la del job:
     `scrapy-impersonate` no está fijado). Con render, un contexto de
     Playwright con nombre por perfil (`perfil_movil`, creado con
     `playwright_context_kwargs`: 412×915, `device_scale_factor` 2,625,
     `is_mobile`, `has_touch` y el UA) y `PLAYWRIGHT_MAX_CONTEXTS` de 3 a 4.
     Y de paso el contexto `custom` del censo lleva el UA del job: se
     construía al importar `settings.py` con el del ENTORNO, así que el `-s
     USER_AGENT` del worker cambiaba la cabecera pero no `navigator.userAgent`
     ni la ventana (leído; `SeoSpider.update_settings` lo corrige cuando el UA
     llega por `-s`, y con un UA de móvil el contexto pasa a ser de móvil).
     Por defecto el móvil es un Chrome de Android y no Googlebot smartphone
     (`paridad_movil_ua`): con el token de Googlebot desde nuestra IP, un WAF
     que verifica bots lo bloquea o le sirve otra cosa (decisión 98) y se
     mediría el WAF. La cadena de Googlebot smartphone vive ahora también en
     `shared/perfiles.py`, y un test comprueba que es la del preset del
     formulario.
   - **Lo que se compara** (`shared/paridad.py`, con las mismas funciones de
     extracción que el censo): title, primer H1 que se pinta, description,
     canonical efectivo (`misma_url`; relativo frente a absoluto y «sin
     canonical» frente a autocanónico son lo mismo), `noindex` y `nofollow`
     por directiva (no la cadena de robots), palabras del contenido
     PRINCIPAL (diferencia desde un 30 % con la mayor por encima de 50; el
     30 % es el punto de partida de FreeCrawl, sin medir aún aquí) y destinos
     internos seguibles (faltan al menos 5 y al menos el 20 % de los de la
     base, con 10 ejemplos). Las dos capturas van en el **mismo modo**
     (render las dos o ninguna): si no, se mediría 7.1 y no el dispositivo.
   - **Lo que no se copia de FreeCrawl.** No sigue redirecciones: si la
     versión móvil redirige (a `m.` o a otra ruta), es un hallazgo propio
     (`estado='redirige'`), se pide el destino UNA vez con el mismo perfil y
     se mira la anotación bidireccional (`rel=alternate media` en escritorio
     hacia el destino y canonical del destino a la de escritorio). No compara
     contra lo guardado horas antes: la línea base viaja en `meta` y la sonda
     sale en segundos (prioridad 10). La muestra es por plantilla y no por
     entrantes, que se lleva la home y el menú. Un muro en la sonda es «no
     evaluada», no una diferencia.
   - **Volatilidad (decisión 13).** Si algo difiere, se pide la página otra
     vez con el perfil del censo; lo que también cambia entre las dos de
     escritorio (un banner rotativo, «relacionados» al azar) va a `volatiles`
     y no se afirma. Si ese control falla, la fila queda `no_evaluada`: sin
     control no se sabe qué es del sitio. Lo mismo con otro CÓDIGO: si la
     versión móvil responde un 500 o un 403 sin firma de muro, se pide otra
     vez con el perfil del censo y, si también falla, queda `no_evaluada`
     (lo que se ha caído es el sitio, no el dispositivo). Coste: como mucho
     2× la muestra (3× con un m-dot), fuera de `max_urls`.
   - **Sonda, no URL del censo** (patrón de la decisión 105): callback y
     errback propios, sin `PageItem`, `dont_filter`, robots.txt respetado (lo
     prohibido queda `no_evaluada` con el motivo), sin tocar la salud del
     rastreo, jar de cookies propio si el job lo enciende (decisión 114) y
     latido por tiempo cada 60 s. Al reanudar, lo ya muestreado cuenta.
   - **Almacenamiento: `capturas_alternativas`**, pensada también para 7.1
     (`perfil='crudo'`). Una fila por URL y perfil AUNQUE no difiera: «no
     difiere» es un dato y sin fila es «no evaluada» (como la decisión 104).
     Con `job_id` (decisión 37), en `TABLAS_POR_JOB`, `borrar_job` (96) y el
     backup. La ficha de la URL (`GET /urls/{id}`) la trae en
     `capturas_alternativas`.
   - **Avisos agrupados por plantilla** (36, 53, 64, 88): una fila por
     (perfil, plantilla, campo) con `paginas_afectadas` sobre
     `paginas_muestreadas`, ejemplos y cuántas no llevan `Vary: User-Agent`
     (servido dinámico sin `Vary`: una caché o CDN puede dar la versión
     equivocada; va en los detalles, no como tipo), anclada en la URL menor.
     Severidad por consecuencia: `paridad_movil_indexacion` es **error** con
     `noindex` o canonical distintos y aviso con `nofollow`;
     `paridad_movil_contenido` es aviso con title, H1, palabras o enlaces e
     informativa con la description; `paridad_movil_respuesta` es aviso con
     otro código o una redirección, e informativa si es un m-dot con la
     anotación completa, que es una configuración válida.
   - **Entre censos.** La paridad no se compara entre censos rastreados con UA
     distinto: el perfil de base es otro (decisión 70). Y la comparación avisa
     cuando un censo es de perfil móvil y el otro de escritorio
     (`resumen_de_job` lleva `perfil`): en un sitio con servido dinámico, la
     diferencia puede ser del perfil. Los tipos nuevos cambian
     `analisis_version` y la comparación por incidencias se niega a cruzar
     censos analizados con código distinto (decisión 91).
   - **Lo que cambia en los censos.** Nada si no se enciende: re-analizar no
     da avisos nuevos sin la tabla llena. Hay que **re-rastrear** con la clave
     puesta. Pendiente de medir en un sitio con servido dinámico conocido y en
     uno responsive como control (que debe dar 0 diferencias no volátiles), y
     repetirlo al día siguiente (13). El viewport no se compara todavía: la
     extracción del `<meta viewport>` va en otra rama (#155). Medido de paso
     con Chromium 1194: un móvil sin `<meta viewport>` maqueta a 980 px de
     ancho (`innerWidth`), así que una regla de CSS por ancho no ve el móvil.
   Tests: `tests/test_paridad_movil.py` (el SeoSpider real con curl_cffi y,
   si arranca el Chromium instalado, Playwright, contra un sitio que sirve
   otro HTML a un UA de móvil; y la tubería real contra Postgres) y
   `tests/test_paridad_movil_analisis.py` (la comparación, los perfiles, el
   contexto `custom` y los avisos contra SQLite).
   **Revisión** (antes de fusionar #161), tres fallos reproducidos con test:
   (a) el destino del m-dot pasaba por `_should_follow`, así que con un
   `include_patterns` acotado al host de escritorio —lo normal en un cliente—
   no se pedía nunca y un m-dot BIEN anotado salía como aviso «redirige sin
   anotar»; ahora el destino es una sonda y solo se exige http(s) (robots.txt
   se sigue respetando); (b) otro código en móvil se afirmaba sin control, y
   un sitio que se cae a media faena daba `paridad_movil_respuesta`; ahora
   lleva la petición de control de arriba; (c) si el destino del m-dot es un
   muro (decisión 103) o no se pudo pedir (error, robots), la anotación no se
   sabe y el analizador ya no lo cuenta como m-dot sin anotar.

<a id="d132"></a>
132. **La huella TLS de serie es `chrome150`, y el UA va con ella: lo que delata
   no es la version de la huella sino un UA que no casa** (0.4 del plan). Medido
   el 2026-10-18 **desde fuera de produccion** (una IP de oficina, no la del
   VPS) con curl_cffi 0.16.3 a pelo: 15 URLs con 200 por sitio sacadas de
   censos locales, 2 s entre peticiones, una sesion por variante, tres pasadas
   (la segunda en orden inverso, la tercera solo con las variantes que
   decidian). «Pasa» = 200 y `clasificar_respuesta` no dice muro (decision 103);
   el tamano mediano coincide al byte con el de la variante de produccion en
   todos los sitios, asi que es la pagina y no otra cosa.

   | variante (perfil + cabeceras) | UOC (AWS WAF) | progym (Cloudflare) | Lopesan | 3 de control |
   |---|---|---|---|---|
   | produccion hasta hoy: `chrome124` + UA Chrome 124 de Windows + cabeceras de Scrapy | 45/45 | 45/45 | 45/45 | 105/105 |
   | `chrome150` + UA Chrome 150 de Windows + plataforma `"Windows"` + cabeceras de Scrapy (**la nueva**) | 30/30 | 30/30 | 30/30 | 60/60 |
   | `chrome124` / `chrome150` + UA de Windows de su version, sin cabeceras de Scrapy | 30/30 c.u. | 30/30 c.u. | 30/30 c.u. | 60/60 c.u. |
   | `chrome150` + UA Chrome 150 de **Linux** | 15/15 | 15/15 | 15/15 | 15/15 |
   | `chrome150` + UA Chrome 124 de Windows (huella y UA descasados) | 30/30 | 30/30 | 30/30 | 90/90 |
   | `chrome124`, `136`, `142`, `146`, `150` con el UA **de curl_cffi** (Chrome de macOS) | 30/30 c.u. | **0/30 c.u. (403 de desafio)** | 30/30 c.u. | 90/90 c.u. |
   | `chrome150` + UA Chrome de macOS con plataforma `"Windows"` | 15/15 | **0/15** | 15/15 | 15/15 |
   | `firefox147`, `safari260` con su UA | 30/30 c.u. | 30/30 c.u. | 30/30 c.u. | 90/90 c.u. |

   (c.u. = cada una; `chrome150` con el UA de macOS se midio en las tres pasadas: 45/45, **0/45** y 45/45 en UOC, progym y Lopesan.)

   Tres conclusiones, las tres repetidas en dos y tres pasadas (decision 13):
   - **Ningun WAF de los tres distingue la version de la huella.** De 124 a 150
     da lo mismo; tiempos y bytes iguales (Lopesan, mediana 0,80 s frente a
     0,67 s; UOC y progym salen de cache de CDN en 0,02-0,11 s). Por la regla de
     0.4 —igual o mejor en los tres— se cambia: un perfil de hace dos anos y
     medio es el que un WAF aprende primero, y la regla «igual» ya se cumple.
   - **Lo que bloquea el Cloudflare de progym es el UA de Chrome de macOS**,
     con cualquier huella y con cualquier `sec-ch-ua-platform`: es la cadena
     que manda curl_cffi cuando no se le da otra. El rastreo nunca la mandaba
     (el UA lo pone Scrapy), pero **las herramientas de control si**:
     `descargar_crudo` (`check_js_templates.py`, `check_content_quality.py`) y
     `scripts/sonda_soft404.py` bajaban sin UA. Es el «`Just a moment...` desde
     mi IP» de la decision 62: no era la IP, era el UA. Ahora bajan con el UA
     de su huella (`huella_y_cabeceras`).
   - **El descasado no lo penaliza ninguno de los tres hoy** (UA 124 con huella
     150 pasa en todos), pero es un navegador que no existe y se corrige igual:
     ademas produccion mandaba un Chrome de Windows con
     `sec-ch-ua-platform: "macOS"`, que es lo que curl_cffi pone de serie.

   Lo que cambia:
   - **Una sola constante**: `shared.perfiles.IMPERSONATE_POR_DEFECTO`, y de ella
     sale `UA_POR_DEFECTO` (= `shared.config.DEFAULT_USER_AGENT`) con
     `ua_para_huella`. `settings.py`, el handler, la sonda de paridad,
     `descarga_cruda` y los scripts la leen; no queda un `"chrome124"` literal.
     Subir la huella sube el UA.
   - **`JobConfig.impersonate` se valida** contra `PERFILES_IMPERSONATE`, la
     lista de curl_cffi 0.16.3 copiada a mano (la imagen de la API no lleva
     curl_cffi; un test en la del crawler la contrasta con la instalada). Sin
     alias (`chrome`, `safari`): se mueven solos al actualizar y la huella deja
     de casar con el UA, el mismo motivo que el `*-latest` de la decision 84.
   - **Si solo se fija uno de los dos, el otro se deduce** (`_ua_y_huella`,
     sobre la config FUSIONADA y por `__dict__`, como `_lista_sin_sitemap`): un
     config con el UA de Chrome 124 y sin huella sigue saliendo con `chrome124`
     —asi siguen los `projects/*/config.json` y los clientes que fijaron el UA,
     sin cambio de comportamiento—, uno de Firefox sale con `firefox147` y no
     con la huella de Chrome, y una huella sin UA lleva el de su navegador. Un
     bot no cambia nada. **Si se fijan los dos, mandan los dos** y el spider lo
     dice en WARNING (`incoherencia_ua_huella`): rechazarlo dejaria sin lanzar
     una programacion guardada asi.
   - **`sec-ch-ua-platform` sale del UA** (`_casar_client_hints` en el handler,
     solo sin render y si la peticion no la trae): Windows con el UA de serie.
   - **El UA de la paridad movil** pasa de `Chrome/155` (evergreen) a
     `Chrome/131`, la version de su huella (`chrome131_android`, decision 119).
     Googlebot smartphone sigue con la evergreen: es un bot.
   - **El formulario** ofrece «Automatica» por defecto (no manda la clave y la
     API la deduce del UA), y al cambiar el UA vuelve a automatica; los presets
     de Chrome pasan a la 150. El pool de UA de respaldo deja macOS fuera.
   - No hace falta re-rastrear ni re-analizar nada: cambia como se piden las
     paginas a partir de ahora, no lo guardado.

   **Lo que esta medida no dice**: es desde otra IP. El WAF de produccion puede
   puntuar la del VPS de otra manera, y el Cloudflare de progym ya ha servido
   desafios a esa IP (decision 62). Canario tras el merge, en produccion: los
   tres sitios (UOC, progym, Lopesan) con un canario corto cada uno, y la tasa
   de 200 con contenido (`response_class`) no puede ser peor que la del ultimo
   censo de cada uno. Si lo es en alguno, `impersonate: chrome124` en su
   `projects/<cliente>/config.json` lo devuelve a lo de antes sin desplegar.
   **Hecho el 2026-10-18 desde el VPS**, como A/B y no contra el ultimo
   censo: por sitio, dos canarios de 100 URLs seguidos con las mismas
   semillas, uno con `chrome124` + UA 124 y otro con `chrome150` + UA 150
   (los dos de Windows). Identicos en los tres: UOC 99/99 HTML con 200 y
   contenido en los dos, progym 96/96, Lopesan 56/56, con los mismos codigos
   (los 302 de Lopesan son sus saltos de idioma) y `health` sin una sola
   respuesta no valida. La huella nueva se queda.
   **Ojo, que el cambio de serie no llega a todo**: los `projects/*/config.json`
   de UOC, blogs-uoc, progym y workoholics fijan YA los dos (`chrome124` y el UA
   de 124), y una programacion guarda su config entera al crearse
   (`model_dump`), asi que la serie semanal de Lopesan sigue con `chrome124`.
   Un canario lanzado con `lanzar_job.py <cliente> --canary` sin mas mide lo de
   antes; para medir lo nuevo hay que pasar los DOS con `--set impersonate=chrome150
   --set "user_agent=<UA_POR_DEFECTO>"` (solo la huella da un descasado, que
   se respeta), o quitar las dos claves del config del cliente.
   Y dos presets del formulario: el de Firefox pasa a la 147 (con la 124 la
   huella automatica salia de la 133); el de **Edge** queda descasado —el ultimo
   perfil de Edge de curl_cffi es `edge101`, de 2022, y con el UA de Edge 124 el
   spider avisa—. No se sube ni se baja el UA: un Edge 101 es un navegador
   viejo que un WAF puntua peor que el descasado; queda abierto.
   Tests: `tests/test_huella_en_la_peticion.py` (el SeoSpider real con curl_cffi
   contra un servidor que anota lo que LLEGA: UA, `sec-ch-ua` y plataforma; y
   `descargar_crudo`) y `tests/test_huella_y_ua.py` (la lista contra curl_cffi,
   la validacion, la deduccion y la herencia, en las dos imagenes).

<a id="d140"></a>
140. **El HTML del servidor frente al render, por muestra de plantilla** (Ola
   7.1 de `docs/PLAN_MEJOR_CRAWLER.md`). Google lee una página dos veces —el
   HTML que responde el servidor y, después, lo que deja el JavaScript— y lo
   que solo está en una de las dos se lee distinto. El rastreo guarda una sola
   (la del modo del job), así que nada de esto se veía: un `noindex` en el HTML
   inicial que el JavaScript quita (Google no renderiza esa página y la saca
   del índice), un canonical que el JavaScript cambia, o un menú y un texto que
   solo existen tras el render y que **ningún rastreador de IA ve** (GPTBot,
   ClaudeBot y PerplexityBot no ejecutan JavaScript).
   - **Sonda** `crawler/seo_crawler/sonda_render.py` (`RenderCrudoMixin`), con
     el patrón de la paridad móvil (decisión 119), de la que reutiliza la
     medición (`huella_paridad`) y la comparación (`shared.paridad.diferencias`):
     una muestra por plantilla pedida en el OTRO modo, fuera de `max_urls`, sin
     seguir redirecciones, y si algo difiere se repite en el modo del censo
     para quitar lo que también cambia entre dos peticiones iguales (decisión
     13). Va a `capturas_alternativas` con `perfil` `crudo` (censo con render)
     o `render` (censo sin render), que el modelo ya preveía.
   - **Por defecto solo con `render_js`**: ahí la otra versión es una petición
     sin render, barata. `crawl_behavior.render_crudo_muestra` (None = 30 con
     render, 0 sin él) y `render_crudo_por_plantilla` (3). Sin render, la otra
     versión es un render por página y hay que pedirla; `js_check` (decisión
     35) ya cubre los enlaces de esos rastreos.
   - **Las diferencias se guardan orientadas** (`render`, `crudo`) sea cual sea
     el modo del censo: en los enlaces, `faltan` son los que solo aparecen al
     renderizar. Y los volátiles se miran en las dos direcciones
     (`campos_que_cambian`): `diferencias` solo cuenta los enlaces que faltan
     en el segundo argumento.
   - **Análisis** `analysis/avisos_de_render.py`, una fila por (plantilla, tipo)
     con `paginas_afectadas` sobre `paginas_muestreadas`:
     `noindex_solo_en_crudo` (error), `noindex_solo_al_renderizar`,
     `canonical_cambia_al_renderizar`, `nofollow_cambia_al_renderizar`,
     `contenido_solo_con_js` y `respuesta_cambia_al_renderizar` (warning), y
     `cambia_al_renderizar` (info: title, H1, description, o un render con
     MENOS texto). `analyze_paridad_movil` filtra ahora sus perfiles: sin eso
     habría leído estas capturas como diferencias de dispositivo. Vista
     **JavaScript** en el Explorador, y `contenido_solo_con_js` también en la
     vista IA (decisión 137).
   Medido en local con el spider real contra **www.uoc.edu** (render, 40 URLs,
   2 por plantilla): 19 páginas muestreadas, todas comparadas, **14 con unos
   480 enlaces internos que solo aparecen al renderizar** (530 en el render
   frente a 40-60 en el HTML del servidor: el megamenú), y en una ficha de
   diplomas el render con menos texto que el servidor (460 frente a 742
   palabras); 0 volátiles. Coste: **74 s frente a 53 s** del mismo rastreo sin
   la sonda; casi todo son los renders de control de las páginas que difieren,
   y con el tope de 30 por censo el extra queda acotado a unos 30 renders.
   Tests: `tests/test_render_crudo.py` (spider real con y sin render contra un
   sitio cuyo JavaScript quita un noindex, cambia el canonical o monta el
   menú; los de render necesitan Chromium y en CI se saltan) y
   `tests/test_render_crudo_analisis.py`.

<a id="d172"></a>
172. **Topes de URLs por subdominio o carpeta: un cupo aparte de `max_urls`, lo que no cabe no se pide, y ahí no se afirman ausencias**

   **Problema.** En un multisite (blogs.uoc.edu, con un blog por carpeta de primer
   nivel: 14 en su portada el 24-oct) o en un sitio con una parte enorme y poco
   valiosa (un archivo, un buscador interno), el único freno era `max_urls`, que es
   global: lo gasta lo que el recorrido encuentra antes, y lo que queda fuera no
   deja rastro. Y si se corta, todo el censo queda truncado (decisión 67).

   **Decisión.** `JobConfig.topes_por_carpeta`: `{"regla": tope}` con la sintaxis
   de `shared/topes_carpeta.py`, el único sitio con la regla (la usan `JobConfig`,
   el spider y el analizador; decisiones 23, 39 y 64):

   - `"*"` = cada carpeta de primer nivel de cada host; las páginas sueltas de la
     raíz (`/`, `/contacto`) no están en ninguna; `"/blog/"` = ese prefijo, en cada
     host por separado (`/blog` cuenta como su portada); `"blogs.uoc.edu"` o
     `"blogs.uoc.edu/*"` = el host entero; `"blogs.uoc.edu/blog/"` y
     `"blogs.uoc.edu/*/"` = lo mismo solo en ese host.
   - Una URL tiene como mucho **dos cupos**: el de su host y el de su ruta, que es
     la regla de ruta más concreta (un prefijo antes que un comodín; entre
     prefijos, el más largo; a igual longitud, el que nombra el host). Así
     `{"/blog/": 500, "*": 300}` da 500 al blog y 300 al resto de carpetas, y
     `{"blogs.uoc.edu/*": 2000, "*": 300}` acota cada blog y el host a la vez.
   - Es un **dict**: en la herencia cliente → perfil → job se fusiona por regla y
     `null` la quita (`shared/clientes.fusionar_config`). La regla se reconoce por
     su clave **normalizada** (`topes_carpeta.clave_normal`): si el cliente pone
     `"blogs.x.com"` y el perfil `"blogs.x.com/*"`, el perfil la sobrescribe (o la
     quita con `null`) en vez de quedar las dos y rechazarse el job. Dos grafías de
     la misma regla en UNA capa siguen siendo un error.
   - Una regla que no se entiende (`"blog/"` sin barra inicial, un comodín en
     medio, un tope 0, un host con puerto, que nunca casaría porque la URL se
     compara por `hostname`) **se rechaza** al validar el config; `"/*/"` sin host
     es `"*"` y con las dos juntas se rechaza por repetida. Si llegara al spider
     el job no se rastrea (`config_invalida`, como las reglas a medida): rastrear
     sin el tope pedido es justo el censo que alguien quiso evitar.

   En el **spider**, la pregunta se hace al ENCOLAR (enlaces, sitemap, destino de
   una redirección y frontera al reanudar): lo que no cabe **no se pide**, no basta
   con no guardarlo. Cuenta URLs del censo de cualquier tipo (un salto o una imagen
   de la carpeta también gastan cupo; con `"*"` en un WordPress, `/wp-content/` es
   una carpeta más, y un `"/wp-content/"` más alto lo saca del comodín). Las
   semillas se piden siempre y gastan cupo. Las sondas (soft 404, respuestas,
   WordPress) no pasan por aquí. Al reanudar, lo ya rastreado gasta su cupo desde la
   BD y lo dejado se vuelve a contar al recorrer la frontera (son sus enlaces
   pendientes). Al cerrar escribe `jobs.topes_carpeta`: por regla (grupos, grupos
   cortados, rastreadas, sin rastrear) y por grupo cortado; `sin_rastrear` son las
   URLs **descubiertas** y no pedidas, una vez cada una, así que es un mínimo (lo
   enlazado solo desde lo no pedido ni se llegó a ver).

   **Lo que no se puede afirmar** (67, 70). Si algún tope dejó URLs sin pedir y la
   frontera se agotó, el worker escribe `finish_reason = tope_por_carpeta`
   (`_con_topes`, leyendo `jobs.topes_carpeta`), que entra en `MOTIVOS_TRUNCADO`:
   así lo reutiliza todo lo que ya trataba el truncado por `max_urls` (la
   comparación y sus alertas, la evolución, el veredicto del modo lista, los
   destinos fuera del censo de `enlace_no_rastreable`, WordPress, el informe y
   `truncated` en el progreso). Un corte por `max_urls`, por tiempo o un
   estancamiento mandan sobre él. Además, en el **análisis**, con algún tope
   cortado (`topes_carpeta.corto`, el mismo criterio que el worker) **ninguna URL
   del censo** lleva `orphan_page`, `sitemap_orphan`, `not_reachable_from_home`,
   `canonical_sin_enlazar`, `solo_enlazada_por_redireccion` ni
   `solo_enlazada_desde_paginacion_excluida` (`SEOAnalyzer._topes_cortaron`). No
   basta con callarlos en el grupo cortado: las páginas que el tope no pidió
   también enlazan a otras carpetas (un post de /blog/ sin pedir puede ser el único
   que enlaza a /producto/x), así que cualquier ausencia de entrantes o de camino
   es una ausencia en un censo truncado. La primera versión los callaba solo en el
   grupo, y una revisión lo reprodujo: `/producto/x` salía `sitemap_orphan` en un
   censo `tope_por_carpeta`. El informe y la ficha lo dicen. `not_in_sitemap` no
   cambia: compara una página rastreada con el sitemap leído, y eso no depende de
   lo que se dejó.

   **Memoria.** El presupuesto recuerda cada URL admitida (para no gastar cupo dos
   veces por la misma, enlazada desde cien páginas) y las dejadas. Con `"*"` es
   casi todo el censo, y un `set` de ints son unos 70 bytes por URL (350 MB por
   subproceso en un rastreo de 5 M). `ClavesCompactas` guarda las claves de 64 bits
   en un `array('Q')` con direccionamiento abierto: medido, **36 MB por cada 2 M de
   claves frente a 139 MB**, unas 3 veces más lento por operación (0,6 µs contra
   0,2), nada frente a una petición.

   **Medida.** El spider real contra blogs.uoc.edu desde local, `max_depth` 2,
   `max_urls` 45 y solo HTML (unas 45 peticiones por pasada): con `{"*": 3}` las 45
   URLs se reparten en 17 carpetas (3 como mucho cada una) y `jobs.topes_carpeta`
   cuenta **1.248 URLs descubiertas sin pedir en 15 blogs** (411 en
   `llenguailiteratura`, 202 en `comunicacio`); sin topes, 13 carpetas de hasta 4 y
   ningún rastro de lo que quedó fuera. Tests: `tests/test_topes_carpeta.py` (las
   reglas, la herencia, el presupuesto, el worker y el `SeoSpider` real con el arnés:
   lo que no cabe no se pide, también desde el sitemap o tras un salto, y las
   semillas sí), `tests/test_analyzer_topes_carpeta_db.py` y
   `tests/test_topes_carpeta_api.py` (informe y ficha).

   **Queda fuera.** La comparación entre censos trata el censo topado como
   truncado entero, como con `max_urls`: afinarla por grupo (afirmar ausencias
   fuera de las carpetas cortadas) es trabajo aparte. El proxy por cliente tampoco
   entra aquí.
<a id="d174"></a>
174. **Accesibilidad con axe-core, por muestra de plantilla y sin afirmar lo
   que axe no decide** (Ola 8 de `docs/PLAN_MEJOR_CRAWLER.md`). Se vende a la
   UOC y al sector público (EAA, RD 1112/2018: WCAG 2.1 AA), y pasar axe a
   todo el censo es el «qué NO hacer» 5 del plan: un render por URL para una
   señal que casi siempre es de plantilla.
   - **Sonda** `crawler/seo_crawler/sonda_axe.py` (`SondaAxeMixin`), con el
     patrón de las decisiones 119 y 140: una página del censo por plantilla
     (`config.templates` o `shared.plantillas.firma_de_ruta`) se vuelve a
     pedir con Chromium, fuera de `max_urls`, sin seguir redirecciones,
     respetando robots.txt, y cuando el DOM se calma (la misma espera del
     render del censo) se ejecuta axe. Va a `capturas_alternativas` con
     `perfil='axe'`: el borrado, el tamaño del censo, el backup y la ficha de
     la URL ya la cubren, y render y paridad filtran sus perfiles. Una fila por
     página muestreada aunque no haya violaciones; sin fila, «no evaluada».
   - **Apagada de serie**: `crawl_behavior.axe_muestra` (0; tope 500) y
     `axe_por_plantilla` (1), declaradas en `JobConfig` y en el modal (solo se
     mandan si se cambian, como las demás sondas). Cada página es un render
     más, también en un censo sin render, que es por lo que no va encendida.
   - **axe-core vendorizado** (4.14.0, MPL-2.0, con su licencia y la de
     terceros, versión, integridad npm y sumas en
     `seo_crawler/vendor/axe/README.md`): nada se baja de un CDN durante el
     rastreo. Se inyecta con `page.evaluate`, que no pasa por la CSP de la
     página (medido con `script-src 'self'`) ni por un cargador AMD, con los
     textos de ayuda en castellano (`locales/es.json`). Solo las reglas WCAG
     2.0/2.1/2.2 A y AA (`shared.accesibilidad.ETIQUETAS`), sin iframes, con
     un tope de 20 s dentro de la página y 90 s de descarga.
   - **No se quitan banners ni modales** (el render del censo sí, con
     `_BOILERPLATE_REMOVAL_JS`): son parte de la página que recibe quien la
     visita. Lo que el render
     bloquea (imágenes, fuentes, analítica) no entra: un contraste sobre una
     imagen de fondo lo deja axe en «incomplete».
   - **Lo que axe deja en `incomplete` no se afirma** (67, 89): se guarda y la
     tarjeta lo lista como «a revisar a mano», sin aviso. Una regla marcada
     `experimental` va con ellas aunque falle. Una página no evaluada (muro,
     redirección, otro código, axe que no terminó, error) no cuenta en las
     muestreadas, y una tarjeta sin páginas evaluadas dice que no se puede
     afirmar nada, nunca «sin fallos».
   - **Análisis** `analysis/avisos_de_accesibilidad.py`: **una fila por (regla,
     plantilla)** con páginas afectadas sobre muestreadas, elementos, criterios
     WCAG, nivel y ejemplos con su selector, anclada en la URL menor (36, 64).
     `accesibilidad_grave` (impacto crítico o grave de axe) es `warning` y
     `accesibilidad_menor` (moderado o leve), `info`: **nunca `error`**, que
     topa la nota SEO (92), y la accesibilidad no es un factor de
     posicionamiento. Best-effort entero (40). Ámbito «Accesibilidad» en el
     Explorador, `GET /api/jobs/{id}/accesibilidad` (lo mismo que los avisos
     por regla, más las incompletas y el coste, con la misma consulta
     `shared.accesibilidad.filas_evaluadas`) y una tarjeta en el resumen con
     las reglas más graves.
   - **Qué cubre**: lo automatizable de WCAG 2.x A/AA, en torno a un 30-40 %
     de los criterios (por volumen de incidencias, Deque midió ~57 % en 2021).
     **Qué no**: el teclado y el foco, el orden de lectura, si un texto
     alternativo describe la imagen, subtítulos y audiodescripción, el
     contenido de iframes y de estados que piden interacción (menús abiertos,
     modales), y los contrastes sobre imagen. Una plantilla sin avisos no es
     una plantilla accesible; lo dicen la ficha y la tarjeta.
   Medido en local con el spider real (sin render, 12 URLs, `axe_muestra` 8 y
   1 por plantilla, `DOWNLOAD_DELAY` 1 s, robots.txt respetado) el
   2026-10-24: **www.boe.es** 5 plantillas, `target-size` (2.5.8, grave) en
   todas y 18-39 contrastes en «incomplete»; **www.ine.es** 8 plantillas,
   `aria-hidden-focus` (4.1.2) en las 8 y `color-contrast` (1.4.3) en 5;
   **www.uoc.edu** 6 plantillas, `aria-allowed-attr` (crítico) en 5,
   `image-alt` en la home, `list`, `label-content-name-mismatch` y
   `target-size`. Coste: axe tarda **60-580 ms** por página (la home de la
   UOC, la más pesada) y la descarga entera (render, espera y axe) **0,8-1,7
   s**; el rastreo de 12 URLs pasó de 36,0 a 37,4 s (BOE), de 38,1 a 39,5 s
   (INE) y de 36,2 a 41,5 s (UOC), porque las sondas van en paralelo con el
   censo. Con 30 páginas por censo, del orden de 30-50 s de render repartidos.
   Tests: `tests/test_accesibilidad.py` (resumen, agregación y el análisis
   contra SQLite), `tests/test_sonda_axe_spider.py` (spider real con Chromium:
   fallos conocidos, CSP, AMD, fallo solo tras el JavaScript, tope total y por
   plantilla, sin gastar `max_urls`), `tests/test_accesibilidad_api.py`
   (trabajo de la API) y `tests/test_accesibilidad_frontend.py`.
