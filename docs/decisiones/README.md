# Decisiones de diseno

Cada decision explica **por que** el codigo es como es, casi siempre con la medida
que la decidio. Antes de cambiar algo de un area, lee las decisiones de ese fichero:
muchas existen porque la version «obvia» ya se probo y rompio un censo.

## Por tema

| Fichero | Tema | Decisiones |
|---|---|---|
| [`worker-y-cola.md`](worker-y-cola.md) | Worker, cola, subprocesos y programaciones | 1, 2, 3, 4, 15, 16, 21, 28, 31, 32, 40, 75, 76 |
| [`render-y-descarga.md`](render-y-descarga.md) | Render con Playwright, descarga y robots.txt | 8, 11, 12, 12b, 13, 14, 17, 18, 19, 49, 50, 59, 81, 98, 103, 109, 110, 111, 112, 114, 119, 132 |
| [`contenido.md`](contenido.md) | Extracción de contenido, titulares, imágenes y anclas | 6, 9, 10, 10b, 44, 45, 46, 52, 61, 79, 121, 124 |
| [`indexabilidad.md`](indexabilidad.md) | Indexabilidad, robots, canonical y hreflang | 20, 22, 23, 39, 48, 56, 99, 101, 107, 113, 115, 116, 120, 129 |
| [`enlazado-y-pagerank.md`](enlazado-y-pagerank.md) | Enlazado interno, PageRank y profundidad | 27, 29, 34, 35, 36, 37, 38, 41, 57, 58, 60, 63, 90, 94, 95, 106, 122, 123 |
| [`incidencias.md`](incidencias.md) | Incidencias del análisis: criterios, severidad y duplicados | 7, 24, 25, 26, 30, 42, 43, 47, 53, 54, 64, 92, 100, 102, 105, 108, 117, 118, 126 |
| [`datos-estructurados.md`](datos-estructurados.md) | Datos estructurados | 51, 73, 80, 97 |
| [`comparacion-y-alertas.md`](comparacion-y-alertas.md) | Comparación de censos, alertas y avisos | 67, 68, 70, 71, 77, 78, 86, 88, 89, 91, 93, 127, 128, 130, 131, 133, 135 |
| [`api-clientes-y-frontend.md`](api-clientes-y-frontend.md) | API, clientes, CSV, backup y frontend | 5, 33, 65, 83, 87, 96, 134 |
| [`calidad-y-versionado.md`](calidad-y-versionado.md) | Herramientas de control, tests y versionado de censos | 55, 62, 66, 69, 72, 74, 82 |
| [`llm-y-enriquecido.md`](llm-y-enriquecido.md) | Capa de decisiones (LLM) y análisis enriquecido | 84, 85 |
| [`extraccion-a-medida.md`](extraccion-a-medida.md) | Extracción y búsqueda a medida por job (#34) y segmentos de negocio | 104, 125 |

## Por numero

| # | Decision | Fichero |
|---|---|---|
| 1 | Subprocess per crawl | [`worker-y-cola`](worker-y-cola.md#d1) |
| 2 | Settings via CLI flags | [`worker-y-cola`](worker-y-cola.md#d2) |
| 3 | Cancel via Redis | [`worker-y-cola`](worker-y-cola.md#d3) |
| 4 | Batched pipeline | [`worker-y-cola`](worker-y-cola.md#d4) |
| 5 | Streaming CSV | [`api-clientes-y-frontend`](api-clientes-y-frontend.md#d5) |
| 6 | Headings dedup | [`contenido`](contenido.md#d6) |
| 7 | URL issues as SEO problems | [`incidencias`](incidencias.md#d7) |
| 8 | JS rendering auto-cap | [`render-y-descarga`](render-y-descarga.md#d8) |
| 9 | Landmark-aware content extraction | [`contenido`](contenido.md#d9) |
| 10 | Hero outside `<main>` is still content | [`contenido`](contenido.md#d10) |
| 10b | El hero tambien se pierde estando DENTRO del contenedor | [`contenido`](contenido.md#d10b) |
| 11 | Browser TLS impersonation must be requested per-request | [`render-y-descarga`](render-y-descarga.md#d11) |
| 12 | La espera de render va por el DOM, no por reloj | [`render-y-descarga`](render-y-descarga.md#d12) |
| 12b | Mirar solo el DOM tiene un punto ciego: la peticion EN VUELO | [`render-y-descarga`](render-y-descarga.md#d12b) |
| 13 | Los sitios varian solos: medir una vez no es medir | [`render-y-descarga`](render-y-descarga.md#d13) |
| 14 | Los métodos de página de Playwright toleran una navegación | [`render-y-descarga`](render-y-descarga.md#d14) |
| 15 | Un estancamiento reencola el job, no lo cierra | [`worker-y-cola`](worker-y-cola.md#d15) |
| 16 | El log de Scrapy va a fichero, en vivo | [`worker-y-cola`](worker-y-cola.md#d16) |
| 17 | Página de error de Chromium = repetir sin render | [`render-y-descarga`](render-y-descarga.md#d17) |
| 18 | La espera de render se ajusta por cliente | [`render-y-descarga`](render-y-descarga.md#d18) |
| 19 | robots.txt también en el destino de una redirección con render | [`render-y-descarga`](render-y-descarga.md#d19) |
| 20 | La indexabilidad la decide el analizador, y repara lo ya rastreado | [`indexabilidad`](indexabilidad.md#d20) |
| 21 | El contador del job contaba solo el ultimo tramo | [`worker-y-cola`](worker-y-cola.md#d21) |
| 22 | La barra en `meta robots` se avisa, no se interpreta | [`indexabilidad`](indexabilidad.md#d22) |
| 23 | Las reglas robots viven en `shared/robots.py`, no en el extractor | [`indexabilidad`](indexabilidad.md#d23) |
| 24 | Casi duplicados con MinHash, no con simhash | [`incidencias`](incidencias.md#d24) |
| 25 | El reparto de bandas del LSH sigue al umbral | [`incidencias`](incidencias.md#d25) |
| 26 | La firma son 256 muestras porque 64 cambiaban el veredicto | [`incidencias`](incidencias.md#d26) |
| 27 | Las redirecciones las sigue el spider, no Scrapy | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d27) |
| 28 | Un job solo es `completed` si su dato esta completo | [`worker-y-cola`](worker-y-cola.md#d28) |
| 29 | PageRank: la repeticion pone techo, no sustituye a la posicion | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d29) |
| 30 | Los porcentajes de /insights van sobre paginas HTML 2xx y cuentan paginas | [`incidencias`](incidencias.md#d30) |
| 31 | La cola de rastreos es una cola, no una pila | [`worker-y-cola`](worker-y-cola.md#d31) |
| 32 | Un rastreo con cero URLs no es un sitio limpio | [`worker-y-cola`](worker-y-cola.md#d32) |
| 33 | Pydantic v2 tira en silencio lo que no esta declarado | [`api-clientes-y-frontend`](api-clientes-y-frontend.md#d33) |
| 34 | El PageRank en tres columnas, porque son tres preguntas | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d34) |
| 35 | El aviso de que el grafo no es fiable viaja CON el dato, tambien en pantalla | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d35) |
| 36 | Un aviso por hallazgo, no dos | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d36) |
| 37 | El job se filtra en `links`, nunca uniendo con `urls` | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d37) |
| 38 | Una pagina `noindex` no enlaza: ni como entrante ni en el PageRank | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d38) |
| 39 | Una sola funcion de indexabilidad, en `shared/` | [`indexabilidad`](indexabilidad.md#d39) |
| 40 | Un candado no puede tragarse los errores de lo que protege | [`worker-y-cola`](worker-y-cola.md#d40) |
| 41 | Los conteos de enlaces leen `links` UNA vez, no cuatro | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d41) |
| 42 | Los avisos sobre la forma de la URL casaban mal, y uno no salto nunca | [`incidencias`](incidencias.md#d42) |
| 43 | Una cadena de redirecciones empieza en DOS saltos | [`incidencias`](incidencias.md#d43) |
| 44 | El texto que no se pinta no es contenido, y el menú no es contenido de la página | [`contenido`](contenido.md#d44) |
| 45 | El limpiador de plantilla no puede borrar la página, y deduplicar no puede desalinear una tabla | [`contenido`](contenido.md#d45) |
| 46 | Un titular oculto no es un titular, y un icono con `aria-label` no es un enlace sin ancla | [`contenido`](contenido.md#d46) |
| 47 | Un duplicado solo importa entre páginas que Google puede posicionar, y se mide por contenido, no por bytes | [`incidencias`](incidencias.md#d47) |
| 48 | Lo que robots.txt prohíbe existe, y el modo auditoría estaba muerto | [`indexabilidad`](indexabilidad.md#d48) |
| 49 | El alcance del rastreo va por la Public Suffix List, y una `<base href>` no navegable se ignora | [`render-y-descarga`](render-y-descarga.md#d49) |
| 50 | Un sitemap de texto, un RSS o un Atom daban cero URLs, y los recursos filtrados gastaban presupuesto | [`render-y-descarga`](render-y-descarga.md#d50) |
| 51 | El `@graph` de Yoast dejaba ciego a todo WordPress | [`datos-estructurados`](datos-estructurados.md#d51) |
| 52 | Las imágenes que se auditaban no eran las de la página | [`contenido`](contenido.md#d52) |
| 53 | Las cabeceras de seguridad son del servidor, no de la página | [`incidencias`](incidencias.md#d53) |
| 54 | El título se corta por píxeles, no por caracteres | [`incidencias`](incidencias.md#d54) |
| 55 | Los scripts de control mentían, y uno de ellos escribe en la base de datos | [`calidad-y-versionado`](calidad-y-versionado.md#d55) |
| 56 | `rel` es una lista de tokens, y lo que Google ignora hay que decirlo | [`indexabilidad`](indexabilidad.md#d56) |
| 57 | Un enlace desde una página rota no enlaza nada | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d57) |
| 58 | Un menú en un `<div class="main-menu">` no es la cabecera | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d58) |
| 59 | Lo que la extensión ya dice no hace falta descargarlo | [`render-y-descarga`](render-y-descarga.md#d59) |
| 60 | Un `nofollow` no diluye, y está medido que da igual | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d60) |
| 61 | El alt de la imagen es el ancla del enlace de imagen | [`contenido`](contenido.md#d61) |
| 62 | El comprobador de contenido no puede dar la alarma cuando el bloqueado es él | [`calidad-y-versionado`](calidad-y-versionado.md#d62) |
| 63 | Lo que solo se alcanza por un salto tampoco puede quedarse sin aviso | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d63) |
| 64 | La severidad es la consecuencia, y un aviso que sale en todas las páginas deja de ser un aviso | [`incidencias`](incidencias.md#d64) |
| 65 | El backup decía «stream» y montaba el censo entero en memoria, y los duplicados se guardaban al cuadrado | [`api-clientes-y-frontend`](api-clientes-y-frontend.md#d65) |
| 66 | Cada rastreo queda sellado con la versión que lo hizo | [`calidad-y-versionado`](calidad-y-versionado.md#d66) |
| 67 | Comparar dos censos: lo que no se puede afirmar pesa más que lo que sí | [`comparacion-y-alertas`](comparacion-y-alertas.md#d67) |
| 68 | Un cambio que afecta a una parte grande de las indexables no es trabajo editorial | [`comparacion-y-alertas`](comparacion-y-alertas.md#d68) |
| 69 | Una dependencia que falta no puede reaparecer como otro criterio de SEO | [`calidad-y-versionado`](calidad-y-versionado.md#d69) |
| 70 | Dos rastreos del mismo host pueden ser dos censos distintos | [`comparacion-y-alertas`](comparacion-y-alertas.md#d70) |
| 71 | Una alerta a la que hay que ir no es una alerta | [`comparacion-y-alertas`](comparacion-y-alertas.md#d71) |
| 72 | Con qué se rastreó y con qué se ANALIZÓ son dos preguntas | [`calidad-y-versionado`](calidad-y-versionado.md#d72) |
| 73 | Los datos estructurados también son un aviso por hallazgo | [`datos-estructurados`](datos-estructurados.md#d73) |
| 74 | Un test que lee el fuente comprueba que escribiste algo, no que funcione | [`calidad-y-versionado`](calidad-y-versionado.md#d74) |
| 75 | Los rastreos se programan, y el planificador vive DENTRO del worker | [`worker-y-cola`](worker-y-cola.md#d75) |
| 76 | `CronTrigger.from_crontab` de APScheduler NO es cron | [`worker-y-cola`](worker-y-cola.md#d76) |
| 77 | Una alerta que espera a que alguien entre no avisa | [`comparacion-y-alertas`](comparacion-y-alertas.md#d77) |
| 78 | Un rastreo programado se compara con el anterior de SU serie | [`comparacion-y-alertas`](comparacion-y-alertas.md#d78) |
| 79 | Un `<dialog>` cerrado es contenido salvo que se le note la plantilla | [`contenido`](contenido.md#d79) |
| 80 | Un JSON-LD que no es JSON no es un bloque válido, aunque se pueda leer | [`datos-estructurados`](datos-estructurados.md#d80) |
| 81 | El `Crawl-delay` se respeta en `respect`, con tope, y no lo deshace AUTOTHROTTLE | [`render-y-descarga`](render-y-descarga.md#d81) |
| 82 | Las herramientas de control descargan como el rastreo | [`calidad-y-versionado`](calidad-y-versionado.md#d82) |
| 83 | El cliente es una entidad, y sus credenciales viven en el entorno, no en la base de datos | [`api-clientes-y-frontend`](api-clientes-y-frontend.md#d83) |
| 84 | Jev por OpenRouter: una capa que no se dispara sola, no tumba un censo y no vuelve a pagar lo mismo | [`llm-y-enriquecido`](llm-y-enriquecido.md#d84) |
| 85 | El análisis enriquecido se lanza a mano, con token, y lo ejecuta el worker desde su propia cola | [`llm-y-enriquecido`](llm-y-enriquecido.md#d85) |
| 86 | El respaldo elige por ALCANCE, no por fecha | [`comparacion-y-alertas`](comparacion-y-alertas.md#d86) |
| 87 | Un estático sin `Cache-Control` mezcla dos versiones de la aplicación | [`api-clientes-y-frontend`](api-clientes-y-frontend.md#d87) |
| 88 | Comparar páginas no contesta «qué arregló el cliente» | [`comparacion-y-alertas`](comparacion-y-alertas.md#d88) |
| 89 | Una incidencia cuya página ya no está en el censo NO está resuelta | [`comparacion-y-alertas`](comparacion-y-alertas.md#d89) |
| 90 | La profundidad se cuenta en clics desde la home, no desde las semillas | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d90) |
| 91 | Dos censos analizados con código distinto no se comparan por incidencias | [`comparacion-y-alertas`](comparacion-y-alertas.md#d91) |
| 92 | La nota global del Informe SEO no puede decir «Bueno» con errores abiertos | [`incidencias`](incidencias.md#d92) |
| 93 | Callarse no basta: el censo de referencia se pone al día solo | [`comparacion-y-alertas`](comparacion-y-alertas.md#d93) |
| 94 | Un enlace repetido en la misma pagina es UNA fila, no tres | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d94) |
| 95 | Los enlaces de un censo superado se archivan en Parquet | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d95) |
| 96 | Borrar censos viejos: en masa, con ids, y sin llevarse por delante la referencia de una serie | [`api-clientes-y-frontend`](api-clientes-y-frontend.md#d96) |
| 97 | Un resultado enriquecido que Google ya no muestra no tiene propiedades obligatorias | [`datos-estructurados`](datos-estructurados.md#d97) |
| 98 | Un preset de bot cambia también qué reglas de robots.txt se obedecen, y no engaña a un WAF | [`render-y-descarga`](render-y-descarga.md#d98) |
| 99 | Hreflang completo, y solo con lo que Google lee: el de una página no indexable no cuenta | [`indexabilidad`](indexabilidad.md#d99) |
| 100 | Una URL del sitemap que no es indexable es una contradicción, y se dice por qué | [`incidencias`](incidencias.md#d100) |
| 101 | Lo que importa de un canonical es lo que Google encuentra al llegar al destino, y de una redireccion, donde acaba | [`indexabilidad`](indexabilidad.md#d101) |
| 102 | HTML que Google trunca o ignora: más de 2 MB y un `<head>` que se corta antes de tiempo | [`incidencias`](incidencias.md#d102) |
| 103 | Una respuesta que no es la página no se guarda como la página, y un rastreo detrás de un muro no es un censo | [`render-y-descarga`](render-y-descarga.md#d103) |
| 104 | Extracción y búsqueda a medida: una fila por (URL, regla) evaluada, y sin fila no es «vacío» | [`extraccion-a-medida`](extraccion-a-medida.md#d104) |
| 105 | Soft 404 determinista: la página de error del sitio se pide, no se adivina, y lo dudoso no se afirma | [`incidencias`](incidencias.md#d105) |
| 106 | Lo que Google no puede seguir se guarda aparte, y lo de la plantilla se avisa una vez | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d106) |
| 107 | Un `X-Robots-Tag` se lee trozo a trozo: una directiva con valor no es un bot, y el bot dura hasta el siguiente | [`indexabilidad`](indexabilidad.md#d107) |
| 108 | Lo que el HTML declara de sí mismo se guarda en crudo y se avisa por plantilla: viewport, lang, charset, forma del canonical, OG y redirección JS | [`incidencias`](incidencias.md#d108) |
| 109 | El tiempo del servidor y la compresión se miden en el handler, y un aviso de servidor va por host o por plantilla | [`render-y-descarga`](render-y-descarga.md#d109) |
| 110 | Un certificado inválido se dice, una vez por host, y con el umbral de 7 días, no 30 | [`render-y-descarga`](render-y-descarga.md#d110) |
| 111 | El `Retry-After` de un 429 o un 503 se respeta en todos los modos, con tope y sin que AUTOTHROTTLE lo deshaga | [`render-y-descarga`](render-y-descarga.md#d111) |
| 112 | Un robots.txt que responde 5xx, 429 o no responde se guarda y se avisa por host, y el rastreo sigue | [`render-y-descarga`](render-y-descarga.md#d112) |
| 113 | El robots.txt guardado se evalúa también como Googlebot, y si se sabe, manda Googlebot | [`indexabilidad`](indexabilidad.md#d113) |
| 114 | Las credenciales del job solo van al sitio, y el rastreo no guarda cookies | [`render-y-descarga`](render-y-descarga.md#d114) |
| 115 | Varios canonicals son varios DESTINOS, y con dos Google no usa ninguno | [`indexabilidad`](indexabilidad.md#d115) |
| 116 | Hreflang: una anotacion repetida es una, y la cabecera `Link` tambien es fuente | [`indexabilidad`](indexabilidad.md#d116) |
| 117 | Una cadena de redirecciones es un aviso, y pasados 10 saltos es un error | [`incidencias`](incidencias.md#d117) |
| 118 | Un casi duplicado se mide entre las mismas páginas que el duplicado exacto, y una escritura sin espacios se parte en caracteres | [`incidencias`](incidencias.md#d118) |
| 119 | La paridad móvil se mide con una muestra por plantilla, en la misma ventana y con el dispositivo entero; lo que cambia solo no se afirma | [`render-y-descarga`](render-y-descarga.md#d119) |
| 120 | Señales que se contradicen, y una meta refresh es una redirección, no la página | [`indexabilidad`](indexabilidad.md#d120) |
| 121 | Un titular vacío no es un titular, un título de relleno se dice una vez, y un enlace sin nombre sale de la temporal de aristas | [`contenido`](contenido.md#d121) |
| 122 | Paginacion: lo que importa es la pagina 2+ que no cuenta y la siguiente a la que no se llega por un `<a>` | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d122) |
| 123 | Los enlaces externos se comprueban con una sonda, no con `follow_external`, y lo que no se puede afirmar no se avisa | [`enlazado-y-pagerank`](enlazado-y-pagerank.md#d123) |
| 124 | Los recursos embebidos se comprueban con la misma sonda, por recurso y fuera de `max_urls` | [`contenido`](contenido.md#d124) |
| 125 | Segmentos de negocio: la primera regla verdadera gana, lo que no se sabe no casa, y se materializan en `urls.segmento` | [`extraccion-a-medida`](extraccion-a-medida.md#d125) |
| 126 | De qué fichero viene cada URL del sitemap, y los avisos son del fichero, no de la URL | [`incidencias`](incidencias.md#d126) |
| 127 | Lo extraído a medida también se compara entre censos, y las alertas saltan por segmento de negocio, no solo por forma de ruta | [`comparacion-y-alertas`](comparacion-y-alertas.md#d127) |
| 128 | Una lista de URLs clave es un censo, y su veredicto no puede decir «pasa» sin saberlo | [`comparacion-y-alertas`](comparacion-y-alertas.md#d128) |
| 129 | El robots.txt se evalúa también para los bots de IA, separando entrenamiento de búsqueda, y la ficha de una URL bloqueada dice la línea que la bloquea | [`indexabilidad`](indexabilidad.md#d129) |
| 130 | La evolución de un perfil se agrega por ejecución, sin leer `links`, y un punto que no es un censo entero no hace línea | [`comparacion-y-alertas`](comparacion-y-alertas.md#d130) |
| 131 | Comparar también el enlazado, el marcado y el hreflang; separar lo roto de lo publicado mal; y una migración se empareja con reglas | [`comparacion-y-alertas`](comparacion-y-alertas.md#d131) |
| 132 | La huella TLS de serie es `chrome150`, y el UA va con ella: lo que delata no es la version de la huella sino un UA que no casa | [`render-y-descarga`](render-y-descarga.md#d132) |
| 133 | Una URL de la lista que redirige a una página sana es un aviso, no un fallo | [`comparacion-y-alertas`](comparacion-y-alertas.md#d133) |
| 134 | La API pide un token, y sin él está cerrada, no abierta | [`api-clientes-y-frontend`](api-clientes-y-frontend.md#d134) |
| 135 | Las reglas de alerta son del cliente, con valores de serie que reproducen lo de antes | [`comparacion-y-alertas`](comparacion-y-alertas.md#d135) |

## Anadir una decision

1. Reserva el numero con `python3 .claude/hooks/sesiones.py numero-decision <id-sesion>`, no «el mayor de la tabla + 1»: otra sesion puede tener ese mismo hueco en su rama. No reutilices ni renumeres.
   `tests/test_numeracion_decisiones.py` falla si dos chocan o si falta la fila del indice.
2. Escribela en el fichero del tema, en su orden, con el ancla `<a id="dNN"></a>` encima.
3. Anade su fila en las dos tablas de este indice.
4. Si cambia una regla que cualquier sesion tiene que conocer sin abrir nada, anade UNA linea a «Lo que no se puede romper» en `CLAUDE.md`. Solo una linea: el detalle vive aqui.
