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
     rastreó y si no en la URL de id más bajo del host (no depende del orden de
     las filas, lección de la 99). `error` si falló también al cerrar o no se
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
