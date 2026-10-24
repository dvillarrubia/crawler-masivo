# Roadmap

Qué hace hoy el crawler y qué viene después, contado por funcionalidades. El
detalle técnico de cada punto está en la issue de GitHub enlazada.

¿Sin conocimientos técnicos? La versión en lenguaje llano, con el estado de
cada cosa de un vistazo, está en [`ROADMAP_SENCILLO.md`](ROADMAP_SENCILLO.md).

Estado a 17 de octubre de 2026.

Este documento cuenta **qué hay y qué viene**, en lenguaje de producto.
`docs/PRIORIDADES.md` cuenta **por qué ese orden y con qué cifra**, y los
milestones de GitHub son la secuencia viva. Si los tres no coinciden, manda el
milestone.

---

## Lo que ya funciona

### Rastreo

- Rastrea sitios grandes, con o sin JavaScript, y aguanta muchos cortafuegos
  (WAF) haciéndose pasar por un navegador real.
- Se puede seguir en directo, cancelar, pausar y reanudar. Al reanudar repite
  las páginas que se perdieron o fallaron, y para solo si se atasca.
- Respeta robots.txt y usa los sitemaps.
- Sigue bien las redirecciones, incluidas las meta refresh, y guarda cada salto
  de la cadena.
- Configuración por cliente: reglas de plantillas de cada sitio.
- Topes de URLs por subdominio o carpeta, aparte del máximo de URLs: cada blog
  de un multisite con su cupo. Lo que no cabe no se pide, el rastreo dice
  cuántas URLs dejó en cada parte y, si alguno corta, el informe no afirma
  huérfanas ni páginas sin camino en ninguna parte del censo (24 de octubre,
  decisión 172).

### Auditoría SEO automática

- Códigos de respuesta, titles, descriptions, encabezados, canonicals,
  hreflang, datos estructurados e indexabilidad.
- Hreflang completo: retorno, autorreferencia, x-default, idioma repetido,
  versiones que apuntan a una página no indexable y códigos que no casan entre
  versiones; solo sobre páginas indexables, que son las que Google lee.
- **Hreflang del sitemap** (24 de octubre, decisión 167): el `<xhtml:link>` de
  cada entrada se guarda y se suma al del `<head>` y la cabecera `Link`; dos
  fuentes que dan URLs distintas para el mismo idioma son
  `hreflang_fuentes_discrepan`, una por host. Con el sitemap leído a medias no
  se afirman ausencias. Medido en Typeform: 37 destinos rotos que solo declara
  el sitemap y que el análisis no veía.
- Indexabilidad calculada sobre la URL final tras redirecciones; las páginas
  bloqueadas por robots.txt no cuentan como indexables.
- **robots.txt por bot** (18 de octubre, decisión 129): qué bloquea el
  robots.txt de cada sitio a Googlebot, Bing y diez bots de IA, separando los
  de entrenamiento (GPTBot, ClaudeBot, Google-Extended…: bloquearlos es una
  decisión legítima) de los de búsqueda (OAI-SearchBot, Claude-SearchBot,
  PerplexityBot: bloquearlos saca al sitio de esas respuestas). Se avisa solo si
  un buscador de IA tiene cerrado el sitio entero. Y la ficha de una URL
  bloqueada dice **la línea de robots.txt** que la bloquea. Sale re-analizando
  los censos que ya guardan su robots.txt.
- Duplicados exactos y casi duplicados (páginas que comparten el 90 % del texto
  o más).
- Cadenas de redirección, imágenes sin alt, seguridad, contenido escaso, URLs
  problemáticas, páginas huérfanas y cobertura del sitemap, incluidas las URLs
  que el sitemap declara y no se pueden indexar (saltos, errores, noindex,
  canonicalizadas), con el motivo de cada una.
- **De qué sitemap viene cada URL** (18 de octubre, decisión 126): la ficha de
  una URL dice en qué fichero(s) de sitemap está declarada y con qué fecha de
  modificación (lastmod). Y tres avisos sobre los propios ficheros: el que pasa
  de los límites de Google (50.000 URLs o 50 MB), el que lleva fechas de
  modificación en el futuro, y las URLs repetidas en varios ficheros (una vez
  por grupo de ficheros, no por URL). Necesita un rastreo nuevo.
- **Soft 404** (16 de octubre, decisión 105): páginas que responden 200 pero son
  una página de error. Al empezar cada rastreo se pide una URL que no existe por
  sitio y por tipo de página, y lo que el sitio responde es su huella de «no
  encontrado»; además se leen los titulares de error en ocho idiomas y las
  búsquedas internas sin resultados. Lo dudoso (un artículo sobre el error 404,
  un listado vacío, un producto descatalogado) queda como **posible soft 404**
  para revisar, sin afirmarlo: es lo que mirará la fase con modelo
  ([#11](https://github.com/dvillarrubia/crawler-masivo/issues/11)). Una
  página que es soft 404 no lleva además los avisos de su contenido (escaso,
  título duplicado, sin description, sin h1), y en el Informe SEO resta en la
  nota de contenido con su propia recomendación.
- PageRank interno:
  - las redirecciones y los canonicals pasan la autoridad a la página correcta;
  - los menús y enlaces repetidos no cuentan como contenido;
  - mide cuánto PageRank se desperdicia en errores y redirecciones.
- Aviso automático cuando el JavaScript esconde enlaces y el PageRank puede no
  ser fiable.
- HTML que Google no llega a leer: páginas de más de 2 MB (Googlebot corta
  ahí) y cabeceras `<head>` que se cortan antes de tiempo y dejan el robots,
  el canonical o el título donde Google no los lee (decisión 102). Lo del
  tamaño sale en los censos ya hechos sin JavaScript; lo de la cabecera, al
  volver a rastrear.

### Contenido y entregables

- Extracción del texto principal de cada página, con recuperación del titular
  cuando se perdía.
- Informe con puntuaciones y recomendaciones.
- Exportaciones: listado de URLs con 75 columnas, enlaces, contenido y copia de
  seguridad.
- HTML de cada pagina guardado a peticion (`store_raw_html`), para re-extraer
  contenido sin volver a rastrear y para auditar por que una pagina salio
  vacia. Apagado por defecto: son 170 kB por pagina.

### Fiabilidad del dato (6 de octubre)

- Los tests se ejecutan en cada cambio y **bloquean el despliegue** si fallan.
  Antes se desplegaba a produccion sin ejecutar ninguno.
- Dos analisis del mismo rastreo ya no se pisan ni duplican incidencias.
- Un sitemap comprimido gigante no puede tumbar al worker.
- El aviso de «demasiados enlaces salientes» cuenta solo los enlaces del
  contenido, no el menu: pasa de avisar en 7.011 paginas de un censo a 51.
- Las paginas huerfanas ya no se tapan con sus propios autoenlaces: en el censo
  de blogs.uoc.edu aparecieron 4.783 paginas de adjunto de WordPress sin un
  solo enlace editorial.
- Los datos estructurados se validan contra los requisitos reales de Google,
  distinguiendo lo obligatorio (sin eso no hay resultado enriquecido) de lo
  recomendado (sale, pero peor).

### Fiabilidad del dato (7 de octubre)

Una pasada entera sobre lo que el crawler **lee** y lo que **reporta**, con la
medición de cada arreglo sobre censos reales o sobre 58 páginas de control de
tres clientes. Lo que se arregló, en lenguaje de producto:

- **Páginas que salían vacías.** Una clase como `cookie-bar-active` en el
  `<body>`, o una página de ASP.NET (donde todo va dentro de un formulario),
  dejaban la página guardada con **0 palabras**. Y el limpiador que corre en el
  navegador llegaba a borrar la página entera —texto, enlaces y titulares— sin
  que nada lo marcara.
- **El menú ya no cuenta como contenido de la página.** El aviso de contenido
  escaso se mide sobre el texto propio: pasa de avisar en 997 páginas de un
  censo a **10.433**, y se limita a las indexables (4.736), que son las que
  Google puede posicionar.
- **Chino, japonés y tailandés.** Una página japonesa entera contaba como
  **1 palabra**, así que cualquier sitio en esos idiomas salía como contenido
  escaso de principio a fin.
- **Tablas y listados.** El deduplicador borraba celdas repetidas: de 8 «Sí» de
  una tabla comparativa quedaba 1 y las filas salían desplazadas, o sea que el
  informe decía lo contrario que la página.
- **El título se corta por píxeles, no por letras.** El 29% de los avisos de
  «título demasiado largo» era falso: títulos de 65 letras estrechas que en el
  resultado de Google caben enteros.
- **Duplicados que importan.** Los grupos de título y description duplicados
  incluían variantes con `?utm` y páginas en noindex, que no compiten con nadie:
  los avisos bajan a menos de la mitad. Y el duplicado exacto se mide ahora por
  contenido y no por bytes: en una red de blogs pasó de encontrar **0** a
  encontrar **745 páginas**, entre ellas la misma política de privacidad
  publicada e indexable en decenas de blogs.
- **WordPress ya no es invisible.** Los datos estructurados que genera Yoast
  (un bloque con varias entidades dentro) se guardaban como una sola fila sin
  tipo: el 100% de las páginas de un censo. Ahora cada entidad se guarda y se
  valida por separado.
- **Imágenes.** Se auditaba la imagen de relleno de la carga diferida y no la
  real; un `<picture>` generaba avisos de «imagen sin alt» en todas las páginas
  con imágenes responsive.
- **266.000 avisos que eran uno por sitio.** Las cabeceras de seguridad son del
  servidor, no de cada página: ahora va un aviso por sitio.
- **Lo que robots.txt bloquea aparece en el listado**, como en Search Console.
- **El informe de un sitio `.co.uk` ya no incluye a la competencia** (el
  alcance por subdominios se calculaba mal).
- **Sitemaps de texto, RSS y Atom.** Daban cero URLs, y con cero el crawler
  creía que ninguna página estaba en el sitemap.
- **El peso de las páginas estaba dividido por 16** en los sitios con
  compresión: se confundían el tamaño de la página y los bytes que viajan.
- **Un tercio de las descargas se tiraba.** El filtro de «qué tipos de archivo
  rastrear» se aplicaba después de descargar, así que una imagen o un CSS
  excluidos se pedían, se descargaban y se descartaban. Medido en un rastreo de
  e-commerce en marcha: de 5.269 respuestas, **1.878 (el 35,6%) no dejaban
  ninguna fila**, a 2,7 s cada una. El rastreo había bajado de 50 a 10 páginas
  por minuto.

### El informe de penguin: 959.633 incidencias, y por qué todavía no sé cuántas son reales (9 de octubre)

Tercer censo entregado que revisamos, y el más grande: 87.531 páginas. Casi
dos tercios de lo que listaba el informe era ruido nuestro, ya arreglado.

**Pero ese censo solo está re-analizado, no re-rastreado**, y eso tiene un
límite que conviene decir antes que nada: volver a analizar arregla lo que
decide el análisis, no lo que se guardó mal al rastrear. Ese censo es de julio
y no tiene ni una sola de las columnas de contenido que añadimos después, así
que «contenido escaso» se sigue midiendo sobre el cuerpo entero —megamenú
incluido— y el duplicado exacto no encuentra nada. La cifra de abajo sirve
para ver **qué clase de ruido había**, no como recuento para un cliente.

| | en el informe | real |
|---|---|---|
| imágenes sin texto alternativo | 445.718 | **45.745** |
| datos estructurados | 226.651 | **4** |
| cabeceras de seguridad | 168.607 | **1** |
| «demasiados enlaces salientes» | 27.656 | **0** |
| páginas huérfanas | 26.116 | **639** |

«Demasiados enlaces salientes» saltaba en **todas** las páginas porque
contábamos el megamenú como enlaces del texto. Las huérfanas eran un 98%
falsas. Y las 226.651 de datos estructurados decían **cuatro cosas**: es el
mismo bloque de la plantilla contado una vez por página, la misma cura que ya
aplicamos a las imágenes y a las cabeceras.

Lo que sí hay que contarle al cliente está en `docs/PARA_CLIENTES.md`, y son
hallazgos que he comprobado **pidiendo las páginas hoy**, no fiándome del censo
de julio: 19.109 páginas indexables sin canonical en un sitio con seis
variantes de español, y 1.144 páginas de autor cuyo título es literalmente
`| Penguin Libros ES`, con el nombre sin rellenar.

### Accesibilidad con axe, por muestra de plantilla (24 de octubre)

Opcional en cada rastreo («Accesibilidad (axe): páginas» en el modal, apagado
de serie): una página por plantilla se vuelve a abrir con el navegador y se le
pasa axe-core, la herramienta de referencia, con las reglas WCAG 2.x A y AA.
Sale un aviso por regla y plantilla («Accesibilidad: fallo WCAG crítico o
grave» o «moderado o leve»), el ámbito **Accesibilidad** en el Explorador y
una tarjeta en el resumen con las reglas más graves. No cambia la nota SEO. Lo
que axe no puede decidir solo (un contraste sobre una imagen) se lista «a
revisar a mano» y no se afirma, y la tarjeta recuerda que lo automático cubre
un 30-40 % de WCAG. Probado en BOE, INE y UOC: en la UOC, atributos ARIA no
permitidos en 5 de 6 plantillas; cuesta 1-2 s por página muestreada.
[Decisión 174](docs/decisiones/render-y-descarga.md#d174).

### Alertas, resumen e informes en el Slack de cada cliente (19 de octubre)

Cada cliente tiene su canal de Slack (ficha del cliente → Configuración →
Slack). Llegan ahí las alertas en cuanto termina un rastreo que las tiene, el
resumen semanal y, al terminar cada rastreo programado, un mensaje con sus
cifras, lo nuevo y lo resuelto y el enlace al informe. Un solo bot sirve para
todos los clientes; se pega su token una vez y se elige el canal de una lista.
Si el bot no está invitado al canal, la ficha lo dice y cómo arreglarlo.
[Decisión 149](docs/decisiones/comparacion-y-alertas.md#d149).

### Más comprobaciones: valores de datos estructurados y anclas genéricas (19 de octubre)

Dos avisos nuevos. Los datos estructurados ya no solo tienen que estar: Google
tiene que poder leer el valor (fechas en formato estándar, precio con punto y
sin símbolo, moneda de tres letras, disponibilidad y valoraciones válidas); en
penguin, 55 reseñas tienen la fecha como «28/08/2020». Y se avisa de las
páginas a las que el propio sitio enlaza casi siempre con «leer más», «ver más»
o «see more», que no les dice nada a Google de qué tratan: en Lopesan, 126
entradas del blog en inglés solo reciben «see more».
[Decisión 146](docs/decisiones/datos-estructurados.md#d146) y
[decisión 147](docs/decisiones/enlazado-y-pagerank.md#d147).

### Preguntarle al crawler desde Claude (19 de octubre)

Un servidor MCP de solo lectura (`mcp_servidor/`) para que Claude Code o Cowork
contesten con los datos del crawler: qué clientes tienen alertas abiertas, qué
arreglar primero en el último censo, qué páginas con tráfico de Google dan
error, qué cambió entre dos rastreos. Se instala en el ordenador de quien lo
usa con el mismo token que la interfaz.
[Decisión 148](docs/decisiones/api-clientes-y-frontend.md#d148).

Y desde el mismo día, sin instalar nada: el servidor vive en producción
(`https://crawler-masivo.srv817047.hstgr.cloud/mcp`) y se añade como conector
en claude.ai, Cowork, Claude Desktop o el móvil, o con una línea en Claude
Code; se autoriza con la clave de la interfaz. Además de consultar cualquier
dato, lanza rastreos y canarios, los cancela y los programa o pausa, siempre
enseñando antes qué va a hacer y qué está rastreando ya producción.
[Decisión 151](docs/decisiones/api-clientes-y-frontend.md#d151).

### Google dice qué tiene indexado y qué canonical ha elegido (19 de octubre)

Con Search Console conectado, cada día se comprueban unas 500 URLs una a una
con la inspección de URLs de Google: si las tiene indexadas (y si no, por qué)
y qué canonical ha elegido. Avisa de páginas que el rastreo da por indexables y
Google no tiene, de canonicals que Google ignora, y de páginas de las que
Google aún guarda un canonical que ya no declaran (se arregla pidiendo la
reindexación). En Lopesan aparecen fichas en alemán que Google aún ve
canonicalizadas a la versión en castellano o al servidor de pruebas.
[Decisión 144](docs/decisiones/incidencias.md#d144).

### El rastreo en Excel (19 de octubre)

En la ficha de un rastreo, Exportar → **Excel (varias hojas)**: un fichero que
se puede mandar tal cual, con el resumen, qué arreglar primero (cada
incidencia con qué es, qué decide Google y cómo se arregla), todas las URLs con
sus columnas útiles —también clics e impresiones de Google—, las incidencias y
las URLs con tráfico que el rastreo no encontró.
[Decisión 145](docs/decisiones/api-clientes-y-frontend.md#d145).

### Search Console cruzado con el rastreo (19 de octubre)

Con Search Console conectado, cada rastreo se cruza con los 90 días de datos
de Google anteriores a él. Cada URL tiene sus clics, impresiones y posición
(columnas en el Explorador y vista «Tráfico de Google»), y salen avisos nuevos:
páginas con tráfico que hoy dan error o tienen noindex, URLs que redirigen o
están canonicalizadas y Google sigue mostrando, huérfanas con tráfico,
indexables sin impresiones y tráfico que se llevan variantes con parámetros.
La prioridad tiene en cuenta los clics, y el informe para el cliente tiene una
sección de tráfico. En Lopesan: el 28 % de los clics de Google van a URLs con
`?partner=` de 18 fichas de hotel, y 952 URLs con tráfico no las encontró el
rastreo. [Decisión 142](docs/decisiones/incidencias.md#d142).

### Un enlace para que el cliente vea sus informes (19 de octubre)

En la ficha del cliente, Configuración → **Enlace para el cliente**: crea una
dirección que se le puede mandar. Con ella ve el informe de cada uno de sus
censos —nota, cambios, qué arreglar primero, tráfico de Google— y nada más de
la herramienta, sin usuario ni contraseña. Caduca cuando se elija (un año por
defecto), se puede revocar y se ve cuándo lo ha abierto.
[Decisión 143](docs/decisiones/api-clientes-y-frontend.md#d143).

### Search Console por cliente (19 de octubre)

En la ficha del cliente, Configuración → **Search Console**: se sube la llave
de la cuenta de servicio (una por cliente o una para todos), se elige la
propiedad de entre las que ve esa cuenta, y desde ahí se descargan solos cada
noche los clics, las impresiones y la posición de cada URL (la primera vez, 90
días hacia atrás). La llave se guarda en el servidor, nunca en la base de
datos, y solo se enseña su email. Si algo falla, dice qué hacer («añade la
cuenta X como usuario en la propiedad Y»). El siguiente paso es cruzarlo con
el rastreo: tráfico en páginas rotas o fuera del índice, huérfanas con tráfico.
[Decisión 141](docs/decisiones/api-clientes-y-frontend.md#d141).

### Las alertas tienen estado: no repiten y se cierran solas (18 de octubre)

Una alerta de un rastreo programado ya no avisa cada semana mientras dure el
fallo: avisa la primera vez, se queda **abierta** y se **cierra sola** cuando un
rastreo posterior —que pueda comprobarlo de verdad: completo, comparable, sin
muro— ya no la ve. En la pestaña **Alertas** del cliente se puede marcar como
vista o silenciar hasta una fecha; el panel de cartera dice cuántas hay abiertas
por cliente y el resumen semanal dice desde cuándo sigue abierta cada una.
[Decisión 139](docs/decisiones/comparacion-y-alertas.md#d139).

### Lo que cambia entre el HTML del servidor y el render (18 de octubre)

En los rastreos con JavaScript, una muestra de páginas por plantilla se pide
también sin navegador y se compara con lo renderizado. Avisa de un `noindex`
que solo está en el HTML inicial (Google ni siquiera renderiza esa página), de
un canonical que cambia el JavaScript, y del texto o los enlaces que solo
aparecen al renderizar (los rastreadores de IA no los ven nunca). Vista
**JavaScript** en el Explorador. En la UOC, el megamenú —unos 480 enlaces por
página— solo existe tras el render.
[Decisión 140](docs/decisiones/render-y-descarga.md#d140).

### Informe para el cliente en PDF (18 de octubre)

En la ficha de un rastreo, Exportar → **Informe para el cliente** abre una
página lista para imprimir o guardar como PDF. Lleva la nota del sitio y por
qué, las cifras, lo que ha cambiado desde el rastreo anterior, las diez cosas
que hay que arreglar primero (cada una con qué es, qué decide Google y cómo se
arregla, y ejemplos) y, arriba, un aviso si el rastreo no cubre el sitio
entero. El de Lopesan sale en tres páginas A4 sin tocar nada.
[Decisión 138](docs/decisiones/api-clientes-y-frontend.md#d138).

### Qué arreglar primero (18 de octubre)

En Problemas, «Ordenar: Prioridad» pone arriba lo que hay que arreglar
primero. No basta con la gravedad: cuenta también lo importantes que son las
páginas afectadas (su PageRank, a cuántos clics de la home están y si el
sitemap las declara). Un aviso en la home y en todas las fichas pasa por
delante de un error en tres etiquetas perdidas. En el censo de Lopesan, arriba
quedan el error de datos estructurados de la plantilla y los 51 canonicals a
otro dominio. El equipo SEO tiene que validar ese orden antes de ponerlo por
defecto. [Decisión 136](docs/decisiones/incidencias.md#d136).

### Páginas que se niegan a la IA de Google sin querer (18 de octubre)

Google usa las mismas directivas para el fragmento del resultado y para AI
Overviews / AI Mode. Ahora avisa una página indexable con `nosnippet` (sale sin
texto y fuera de las respuestas de IA) o con un `max-snippet` corto, y el
Explorador tiene una vista **IA** con eso y con los buscadores de IA bloqueados
en robots.txt. En Salle salieron tres páginas con todo bloqueado menos el
`noindex`, que era lo que querían. [Decisión 137](docs/decisiones/indexabilidad.md#d137).

### `data-nosnippet` y robots.txt de prueba (24 de octubre)

El rastreo mide ahora lo que `data-nosnippet` esconde al fragmento (y a AI
Overviews): avisa por plantilla cuando una indexable lo pone sobre el H1 o sobre
la mayor parte del contenido, y anota el atributo en elementos donde Google no
documenta que se aplique. Pide re-rastrear. [Decisión 170](docs/decisiones/indexabilidad.md#d170).
Y en el resumen del rastreo, bajo «robots.txt por bot», se puede pegar un
robots.txt (o partir del guardado), unas URLs y unos agentes, y ver el veredicto
y la línea que decide con la misma lógica que el crawler, antes de subirlo.
[Decisión 171](docs/decisiones/indexabilidad.md#d171).

### Alertas a medida de cada cliente y resumen semanal (18 de octubre)

Ola 5.2 del plan (decisión 135). En la ficha del cliente hay una pestaña
**Alertas**:

- **Reglas del cliente.** Una cifra del rastreo, opcionalmente dentro de un
  segmento, comparada con un umbral. Por ejemplo: «URLs 4xx en fichas: el
  valor > 20» o «Páginas indexables: el cambio en % frente al anterior ≤
  −10». Se puede medir:
  - URLs por código: 3xx, 4xx, 5xx y bloqueadas.
  - Indexables, e indexables sin camino desde la home.
  - Páginas con errores o avisos.
  - Las páginas con una incidencia concreta.
- **Las reglas de la comparación** de siempre, por cliente: se pueden apagar,
  cambiar sus umbrales y decidir cuáles avisan.
- **Valores de serie.** Todo cliente arranca con ellos, que reproducen lo de
  antes, más dos reglas propias: «caen las indexables un 10 %» y «suben las
  URLs con error».
- **«Probar contra el último rastreo»** dice qué reglas saltarían, sin guardar
  nada.
- **Silenciar hasta una fecha** para los avisos fuera de la interfaz.
- **Resumen semanal.** Por defecto, cada lunes a las 8:00, con lo que hay que
  mirar de la semana, las cifras y su cambio, y los perfiles que no se han
  rastreado. Queda guardado en la ficha y sale por el webhook de avisos si
  hay uno configurado.

Las reglas que han saltado salen arriba en la ficha del rastreo. Las de
cambio no se evalúan contra un censo truncado, detrás de un muro o analizado
con otra versión: lo dicen, con el motivo, en vez de afirmar un cambio falso.

Los incidentes con estado y los canales por cliente (Slack) ya están: ver
arriba.

### Evolución de cada cliente y panel de cartera (18 de octubre)

Ola 5.3 del plan (decisión 130), en revisión. Para la pregunta del seguimiento
mensual: **«¿cómo va este sitio desde que lo llevamos?»**.

- **Gráficas en la ficha del cliente**, una serie por perfil: errores, URLs
  4xx y 5xx, páginas indexables, PageRank desperdiciado y clics medios desde la
  home, con la flecha frente al censo anterior.
- **Lo que no es un censo entero no cuenta**: un rastreo fallido, cortado, un
  canario, una prueba o una lista salen abajo con una × y su motivo, fuera de la
  línea y de la escala.
- **Donde cambió nuestro código se ve**: si entre dos censos cambió la versión
  del análisis, la línea sale discontinua con una «v», y la tendencia no dice
  «sube» ni «baja»: el salto puede ser nuestro, no del sitio.
- **Portada en tabla**: cada cliente con su último censo, sus errores y la
  tendencia de errores en una línea pequeña.

Las cifras de cada rastreo se guardan al terminar (sin leer los enlaces), así
que la ficha no recorre los censos. Los rastreos anteriores se rellenan con
`scripts/rellenar_evolucion.py`: medido en local, 46 rastreos en 4,6 segundos.
### Comparar rastreos: enlazado, datos estructurados, hreflang y migraciones (18 de octubre)

Ola 5.4 del plan (decisión 131, [#32](https://github.com/dvillarrubia/crawler-masivo/issues/32)). La pestaña
**Comparar** y la comparación automática miran ahora también:

- **Enlazado**: páginas que se alejan dos clics o más de la home (o se quedan
  sin camino), que pierden la mitad de las páginas que las enlazan, o que bajan
  una década de PageRank. Solo con los dos rastreos enteros: con uno cortado,
  «pierde enlaces» podría ser que no se rastreó quien la enlazaba.
- **Datos estructurados, hreflang y meta description**: qué páginas pierden un
  tipo de marcado (adiós al resultado enriquecido) o un idioma del hreflang.
- **Incidencias nuevas en dos grupos**: en páginas que ya existían (algo se ha
  roto) y en páginas nuevas (algo se ha publicado mal).
- **Migraciones**: se escriben reglas «ruta vieja ⇒ ruta nueva» (expresiones
  regulares) y la comparación empareja cada URL vieja con la nueva, diciendo
  qué porcentaje casa. Probado con una migración simulada de progym: sin
  reglas casaba el 10 % y parecía que el sitio había desaparecido; con dos
  reglas, el 100 %.

Con dos rastreos reales de progym separados por un día, nada de esto da una
alerta: el ruido normal (un clic arriba o abajo, unos pocos enlaces de un
bloque de relacionados) queda por debajo de los umbrales.
### El crawler se presenta como un Chrome actual, y entero (18 de octubre)

Sin JavaScript, el crawler se hace pasar por un Chrome con su «huella» de
conexión. Era la de Chrome 124, de hace dos años y medio, y ahora es la de
Chrome 150, con el User Agent de esa misma versión: si solo se cambia uno, el
otro se ajusta solo, para no parecer un navegador que no existe. Medido contra
UOC, progym y Lopesan: la versión de la huella no cambia nada en ninguno; lo
que sí bloquea el cortafuegos de progym es el User Agent de Chrome de Mac, que
es el que usaban las herramientas que revisan un rastreo después — por eso
alguna daba «aquí se ha perdido el contenido» sobre progym sin que fuera
verdad. Los clientes que tenían fijado el User Agent antiguo siguen igual que
antes. Decisión 132. Probado en producción con dos canarios por sitio, uno
con cada huella: los mismos resultados en los tres (UOC, progym y Lopesan).

### Modo lista y veredicto pasa/falla para QA de despliegues (18 de octubre)

Ola 5.5 del plan (decisión 128). Para la pregunta de después de cada
despliegue: **«¿estas 50 URLs clave siguen bien?»**.

- **Rastreo en modo lista**: profundidad máxima 0 = solo las URLs de la lista
  (y el destino si alguna redirige), sin seguir enlaces y sin leer el sitemap
  salvo que se pida. Antes la API no aceptaba el 0 y el formulario lo convertía
  en 3.
- **Alertas en listas pequeñas**: en una lista basta **una** URL afectada
  (configurable por cliente, perfil o rastreo); antes hacían falta 20, así que
  10 de 50 URLs clave en noindex no avisaban.
- **`GET /api/jobs/{id}/veredicto`** devuelve `pasa`, `falla` o
  `no_concluyente` con los motivos, pensado para que un CI de despliegue lo
  lea: falla si una URL de la lista no responde 200, pasa a noindex o cambia de
  canonical, o si hay una alerta crítica. Lo que no se puede saber (sin censo
  anterior, rastreo cortado o detrás de un muro) es «no concluyente», **nunca**
  «pasa».

Probado sobre dos censos reales de progym con un día de diferencia: una lista de
las 50 páginas con más PageRank da `pasa`, y con 10 de ellas puestas en noindex,
`falla`. Falta la pantalla: hoy se lanza con profundidad 0 desde el formulario
o la API y el veredicto se lee por la API.

### Enlaces que Google no puede seguir, enlaces a errores y utm internos (16 de octubre)

Ola 2.3 del plan (decisión 106). Cuatro avisos nuevos sobre el enlazado interno:

- **Enlaces que Google no puede seguir**: los que una persona pulsa y el
  buscador no ve —`href="javascript:…"`, un `<a>` con `onclick` y sin `href`, un
  `<span href>`, una tarjeta con `onclick`, una ruta `#/…`—, que antes se tiraban
  sin dejar rastro. En Lopesan sale, por ejemplo, que el **selector de idioma**
  del menú es un `onclick` en 201 de 216 páginas: Google no llega por ahí a la
  versión en inglés ni en alemán.
- **Enlaces internos a errores y a redirecciones** (dos avisos), por página
  donde hay que cambiarlos. Lo que ya dice otro aviso no se repite: la
  redirección a una página que solo se alcanza por ella, o el `utm` de un enlace
  que ya va a un error.
- **Parámetros `utm_` en enlaces internos**, que en Analytics reinician la
  sesión y abren URLs duplicadas.

Lo que está en el menú o en el pie se avisa **una vez** y no en cada página: en
penguin, los enlaces a redirecciones salían en las 28.454 páginas del sitio y
ahora son 50 avisos de plantilla más 727 de página. Ninguno de estos enlaces
entra en el PageRank ni en los entrantes: no ha cambiado ni una cifra del
grafo. Los no rastreables piden volver a rastrear; los otros dos salen al
volver a analizar. Queda para después enseñarlos en la ficha de la URL y en el
CSV.

### Comparar lo extraído entre rastreos, y alertas por segmento (18 de octubre)

Lo que se saca a medida de cada página —el precio, la disponibilidad, el SKU, si
lleva el tag de GTM— se compara ahora entre un rastreo y el anterior. Si cambia
de golpe en una parte grande de las páginas (el precio de todas las fichas, la
disponibilidad que pasa a «sin stock», el tag de analítica que desaparece de una
plantilla) sale una alerta en la ficha del rastreo, con cuántas páginas de
cuántas, cuánto suben o bajan los precios y qué valores pasan a cuáles. Solo se
compara una regla que exista igual en los dos rastreos, y una página que en uno
de los dos no se pudo evaluar no cuenta como cambio. Además, las alertas de la
comparación se reparten ya por segmento de negocio: el fallo de Lopesan se lee
como «hoteles: 2.151 de 2.167» en vez de por la forma de la URL.

### Segmentos de negocio: «¿qué le pasa a las fichas?» (16 de octubre)

Cada cliente puede definir sus segmentos —«fichas de producto», «blog»,
«categorías»— con reglas sobre la ruta, sobre cualquier dato de la URL (código,
indexable, tipo…) o sobre un dato extraído a medida (el `og:type`, el precio), y
combinarlas con Y / O / NO; la primera regla que casa manda. Lo que no casa con
ninguna se agrupa por la forma de su ruta, así se ve qué falta por cubrir. Con
eso, Problemas dice qué porcentaje de cada segmento tiene cada incidencia, y el
Explorador y su CSV se pueden filtrar y agrupar por segmento. Es retroactivo:
cambiar las reglas re-segmenta un censo viejo en un par de segundos, sin volver
a rastrear. En Druni: el 56 % de las fichas tiene el título demasiado largo y el
82 % de las categorías es casi duplicada. Una página de la que no se sabe el dato
(bloqueada, sin extracción) no se mete en un segmento por descarte: se cuenta
aparte. Los enlaces a otros sitios y las imágenes o CSS no entran en ningún
segmento aunque su ruta case: no son páginas del cliente y falsearían los
porcentajes. Queda la pantalla para editar las reglas (las alertas por segmento ya están, arriba).

### Extracción a medida: precio, SKU, disponibilidad y «¿tiene el tag de GTM?» (16 de octubre)

[#34](https://github.com/dvillarrubia/crawler-masivo/issues/34). Cada rastreo
puede llevar sus propias reglas para sacar un dato de cada página —un selector
CSS, una XPath o una expresión regular: el precio, el SKU, la disponibilidad,
el autor— y búsquedas de «contiene / no contiene», como el tag de Google Tag
Manager o un «sin stock». Las reglas se ponen en la configuración del cliente,
del perfil o del rastreo (se heredan como el resto), y salen como columnas en el
Explorador —filtrables, ordenables, en su propia pestaña y en el CSV de la
vista— y al final del CSV completo. Probado contra fichas reales de Druni: precio,
SKU y disponibilidad salen bien en las cuatro, una de ellas agotada, y cuesta
menos del 1% del tiempo que ya se dedica a cada página. De once páginas reales de
clientes, la única sin el tag de GTM es la portada de cst.gov.sa.

Dos cosas que se cuidan: una página que no se pudo evaluar (un PDF, un muro del
WAF) **no cuenta como «no contiene»**, y un precio que puede leerse de dos
maneras (`1.234`) se marca como dudoso en vez de adivinarlo. Antes de rastrear,
las reglas se pueden probar contra el HTML de una página.

**Pantalla y prueba en vivo (24 de octubre, decisión 166).** El modal de Nuevo
rastreo tiene una pestaña «Extracción» para escribir las reglas, con las del
cliente y del perfil marcadas como heredadas (se pueden cambiar o quitar solo
para ese rastreo), y un botón «Probar» que descarga una URL de verdad y enseña
qué saca cada regla. La descarga la hace el rastreador, no la API, y no puede
apuntar a la red interna del servidor ni aunque una redirección lo intente. Sin
JavaScript renderizado todavía (lo avisa). Además, un censo ya rastreado con el
HTML guardado se puede re-extraer con reglas nuevas sin volver a rastrear, y la
columna «Cambio frente al anterior» del Explorador dice si cambió el precio o
dejó de estar el tag.

### Rastreos detrás de un muro (16 de octubre)

Cuando un WAF (Cloudflare, Akamai, Imperva, F5…) o un captcha no nos dejaba
ver una página, su desafío se guardaba como si fuera la página: con su
«Just a moment...» como título, indexable y llena de incidencias, y el rastreo
acababa como «completado», que es como se lee un sitio limpio. Ahora cada
respuesta dice si era la página o un muro, el muro no cuenta como indexable ni
lleva incidencias de página —va **una** por sitio y tipo de muro, con cuántas
páginas—, y si las últimas 50 respuestas son casi todas muros, el rastreo se
para solo y la ficha lo dice con la causa (bloqueado por un WAF o por límite
de peticiones). Un rastreo así no se analiza ni se compara con el anterior,
para que no salten alertas de un sitio que no se ha visto. Probado con el muro
real de progym. Es la versión sin modelo de
[#10](https://github.com/dvillarrubia/crawler-masivo/issues/10); la que usa
Jev para los bloqueos que no se anuncian sigue en cola. Decisión 103.

### Borrar rastreos viejos en masa (15 de octubre)

La base de datos crecía sin que nada lo dijera —57 GB para 50 rastreos en la
instalación local antes de agrupar los enlaces, y el 86% era la tabla de
enlaces— y limpiar significaba
entrar en la ficha de cada rastreo, teclear «eliminar» y volver, así que no se
limpiaba nunca. Ahora, en la lista de rastreos, hay una casilla por fila y un
botón de **eliminar los seleccionados**. Antes de borrar se ve la lista con lo
que ocupa cada uno y lo que se libera en total, y un botón arriba dice cuánto
ocupa la base entera y en qué tablas.

Dos cosas no se borran salvo que se insista: un rastreo **en marcha**, y el
**censo con el que se compara la próxima ejecución de una programación** —sin
él, ese rastreo semanal dejaría de avisar de lo que cambie y el silencio se lee
igual que «no ha cambiado nada»—. Cada caso dice su motivo con todas las
letras.

Y lo que se libera se cuenta como es: el espacio queda disponible **dentro** de
la base de datos y se reutiliza en los siguientes rastreos, pero el fichero del
disco no se encoge. Lo que se gana es que deje de crecer. La excepción son los
enlaces de los rastreos ya archivados: viven en un fichero aparte, se cuentan
aparte y ese espacio sí vuelve al disco al borrar.

### Profundidad en clics desde la home (13 de octubre)

La columna de profundidad contaba los saltos desde donde empezó el rastreo, y
cada URL de la lista de partida contaba como cero: en el censo semanal de
Lopesan, sembrado con 3.040 URLs, casi todo el sitio salía a profundidad 0.
Ahora la profundidad son los **clics desde la home**, como la cuenta Google:
desde la portada y desde las portadas de cada idioma, siguiendo los enlaces
internos que Google sigue y sin contar una redirección como un clic. Las
páginas a las que no se llega desde la home tienen su propio aviso. En
Lopesan son 45 indexables enlazadas solo desde otras páginas sin camino,
sobre todo posts del blog. Para los rastreos ya hechos basta re-analizarlos.

### Análisis enriquecido: la base y el botón (13 de octubre)

[#7](https://github.com/dvillarrubia/crawler-masivo/issues/7) y
[#40](https://github.com/dvillarrubia/crawler-masivo/issues/40). En la ficha de
un rastreo hay un botón **Análisis enriquecido**: se eligen los módulos y a qué
páginas aplicarlos, se ve antes de lanzar cuántas llamadas, cuánto dinero y
cuánto tiempo costará frente al tope mensual del cliente, y se lanza con un
presupuesto. Corre en el worker por una cola propia, así que no quita sitio a
los rastreos; al llegar a cualquier tope se para y se queda con lo hecho.
Repetirlo sin que haya cambiado el sitio sale gratis: las respuestas se guardan
y se reutilizan. Lanzar exige un token, y sin token configurado en el servidor
el botón está desactivado: nada de pago se puede disparar por accidente.

Hoy solo hay un módulo de ejemplo («¿es una página legal?»). Los que dan valor
—tipo de página, soft 404, intención— salen del catálogo de preguntas
([#9](https://github.com/dvillarrubia/crawler-masivo/issues/9)) y ninguno se
usa para decidir nada sin pasar antes por el juego de evaluación
([#8](https://github.com/dvillarrubia/crawler-masivo/issues/8)).

### Clientes como entidad (13 de octubre)

Primera fase de [#36](https://github.com/dvillarrubia/crawler-masivo/issues/36).
Hay una sección **Clientes**: se da de alta un cliente con sus dominios, sus
notas y una configuración de rastreo por defecto, y debajo sus **perfiles**
(el sitio principal, el blog, otro idioma, un competidor), cada uno con sus
semillas y lo que cambie respecto al cliente. Un rastreo lanzado desde un
perfil sale ya configurado: hereda lo del cliente, lo del perfil y lo que se
toque en el modal, y queda guardado así en el rastreo, de modo que cambiar la
configuración del cliente mañana no altera lo que dice un censo de ayer.

En el modal de nuevo rastreo el cliente **se elige de una lista** en vez de
escribirse a mano: con texto libre, una errata creaba otro cliente. Los rastreos
antiguos que tenían un cliente escrito a mano se cuelgan de su cliente solos al
desplegar, y si varios textos distintos acaban en el mismo cliente («Acme»,
«acme », «Ácme») se avisa para revisarlo, no se fusiona a ciegas.

Cada cliente puede declarar sus **integraciones** (Search Console, DataForSEO,
Ahrefs, Semrush…), que es lo que permitirá cruzarlas con el rastreo en las
fases siguientes. **Las claves no se guardan en el crawler**: la aplicación no
tiene usuarios ni contraseña, así que cualquier cosa guardada ahí la podría leer
quien llegue a ella. Se apunta el *nombre* de la variable del servidor que tiene
la clave y la ficha dice si está puesta o no, nunca su valor.

### Qué arregló el cliente desde la vez pasada (13 de octubre)

Cierra [#32](https://github.com/dvillarrubia/crawler-masivo/issues/32). La
comparación entre dos rastreos ya decía qué cambió en el sitio; ahora dice
también **qué incidencias se arreglaron, cuáles son nuevas y cuáles siguen
ahí**, con el desglose por tipo y los errores arriba. Es lo que se mira en un
seguimiento mensual.

Lo difícil no era contarlas, era saber **cuándo dos avisos de censos distintos
son el mismo**: un hreflang mal escrito genera un aviso por cada idioma, y un
título duplicado pertenece a un grupo que puede cambiar de miembros sin dejar
de existir.

Y una cosa que estuvo a punto de acabar en un informe: probándolo con dos
censos reales, decía **4.794 incidencias resueltas**. Eran falsas. Entre los
dos rastreos desaparecieron 1.205 páginas, y los avisos de una página que ya
no se visita no están resueltos — **no se sabe**. Las resueltas de verdad eran
**125**. Decirle a un cliente que arregló cuatro mil cosas que no tocó es peor
que no darle ninguna cifra.

### El aviso sale del panel (9 de octubre)

Cuando un rastreo encuentra algo grave —media plantilla fuera del índice, una
sección entera apuntando a otro servidor— ya no se queda esperando a que
alguien abra el panel: se manda fuera.

Es un **webhook**, no un correo ni una integración con Slack. Como el servidor
ya ejecuta n8n, desde ahí se reenvía a correo, a Slack o a donde haga falta sin
meter contraseñas en el crawler. Se configura con una variable
(`ALERTA_WEBHOOK_URL`); vacía, no se avisa.

El aviso dice **qué pasa y sobre cuántas páginas**, no «hay 3 alertas». Así
llegaría el de Lopesan:

> **lopesan.com (censo v4)**: algo ha cambiado respecto al rastreo anterior.
> • **2319** de 4193 páginas (55,3%) — Google consolida estas páginas en una
> URL de OTRO host: dejan de aparecer en resultados y su autoridad se va fuera
> del sitio.

Solo salen las **críticas**: un aviso que llega cada semana con cosas que no
hay que mirar se deja de leer, y entonces no avisa de nada.

### Rastreos programados (9 de octubre)

Resto de [#36](https://github.com/dvillarrubia/crawler-masivo/issues/36). Desde
la ficha de un rastreo que ha salido bien, un botón **Programar** lo repite cada
semana (o cada día, o cada mes) con las mismas semillas y la misma
configuración. Hay una vista con todas las programaciones, cuándo toca la
próxima y cuándo fue la última.

Con esto se cierra la cadena: rastreo semanal → se compara solo con el anterior
→ si media plantilla se ha ido del índice, el aviso está esperándote. Hasta
ahora las alertas existían pero dependían de que alguien se acordara de lanzar
el rastreo; el fallo de Lopesan lo pillamos de casualidad.

Lo construí sobre una librería para no escribir a mano el día 31 en meses de 30
y el domingo del cambio de hora — **y la librería tenía una trampa**: su
función para leer expresiones cron numera los días de la semana distinto, así
que «todos los lunes» le salía **martes**. Un rastreo que se dispara un día
tarde, todas las semanas, no lo nota nadie. Está contrastado con otra librería
sobre quince expresiones.

### El rastreo se compara solo con el anterior (9 de octubre)

Primer trozo de [#36](https://github.com/dvillarrubia/crawler-masivo/issues/36).
Al terminar un rastreo ya no hay que ir a compararlo con nada: se compara solo
con el censo anterior del mismo sitio y, si algo ha cambiado a lo grande, lo
dice **arriba del todo en la ficha del rastreo**, antes que ninguna cifra.

Es el remate de lo de abajo. Una comparación que hay que ir a pedir no sirve
para enterarse de que media sección se ha ido del índice: hay que estar
mirando. Ahora te lo encuentras.

### Comparar dos rastreos del mismo sitio, y que avise solo (9 de octubre)

Cierra [#32](https://github.com/dvillarrubia/crawler-masivo/issues/32). En la
ficha de un rastreo hay una pestaña **Comparar**: se elige otro rastreo
completado del mismo sitio y se ve qué cambió entre los dos.

Lo que lo motivó: en Lopesan, **2.367 páginas** pasaron de un día para otro a
declarar como suya una URL de un servidor de pruebas, lo que para Google
significa sacarlas del índice. Estaba en nuestro informe, como un aviso de
prioridad baja entre 315.119 incidencias de las que el 91% eran imágenes sin
texto alternativo. Nadie lo iba a ver.

Ahora un cambio que afecta a una parte grande de las páginas que pueden
posicionar sale **arriba del todo y en rojo**, con qué decide Google con eso y
sobre cuántas páginas se mide: «2.367 páginas» no dice nada sin «de 2.379».
Probado contra seis parejas de censos reales: dos rastreos del mismo sitio con
un día de diferencia **no disparan nada**, y con un mes tampoco; el fallo de
Lopesan dispara con **2.319 de 4.193 páginas (55,3%)** y plantillas enteras al
100%.

Tres cosas que salieron al construirlo:

- **El informe dependía de cuál eligieras primero.** La misma pareja de censos
  daba 1.639 páginas con el texto cambiado en un sentido y 439 en el otro. Y lo
  mismo con el rótulo «antes»: elegir el censo viejo en el desplegable daba la
  vuelta al informe entero. Ahora el orden lo pone la fecha.
- **Al comparar se emparejaban mal las URLs**, y solo en la parte del programa
  que atiende a la web: faltaba una librería y, sin ella, `/pagina/` y
  `/pagina` pasaban por la misma (normalmente una redirige a la otra) mientras
  que la misma dirección con los parámetros en otro orden pasaba por dos.
- **Y faltaba otra librería en los tres sitios**, la que distingue un dominio
  de verdad de un sufijo como `.co.uk`. Sin ella, la regla que vuelve es la que
  metía a la competencia en el informe del cliente.

### Lo que salió al comparar dos censos del mismo sitio (7 de octubre, tarde)

Re-rastreamos progym con el código ya arreglado y comparamos **las mismas
3.581 páginas** contra el censo de la víspera. De esa comparación salieron
cuatro cosas más:

- **El nombre del producto volvía a la ficha.** En un listado, el enlace
  envuelve la foto y el nombre del producto está en el `alt` de la imagen —que
  es de donde Google lo lee—, pero el enlace se guardaba **sin texto**. En 58
  páginas de control, los enlaces sin anclaje pasan de **1.276 a 208**, y los
  que quedan son iconos de redes sociales que no llevan ninguna etiqueta. En
  progym se recuperan **9.502** anclajes en el próximo rastreo.
- **El aviso de «este PageRank no es de fiar» no llegaba al Excel.** La columna
  salía vacía en las 4.320 filas del CSV aunque la comprobación sí tuviera
  respuesta: es un problema de orden entre el análisis y la comprobación de
  JavaScript. Quien ordena por PageRank en una hoja de cálculo no abre la ficha
  del trabajo, así que el aviso tiene que ir en cada fila.
- **La tabla de URLs mostraba el PageRank que no se puede leer.** Las nueve
  primeras filas de un censo se leían `0,4 0,0 0,0 0,2 0,0 0,3 0,0 3,2 7,1` y
  ahora `65 38 38 58 20 63 20 88 96`. La escala buena ya existía; la pantalla
  seguía enseñando la vieja.
- **La herramienta que avisa de pérdidas de contenido se avisaba a sí misma.**
  Si el sitio le contesta con un muro de WAF (un `Just a moment...` con 200 y
  cero palabras), lo comparaba como si fuera la página y decía que se había
  perdido todo. Ahora lo distingue y lo dice: «no se pudo comprobar», que es
  distinto de «está mal».

Y dos de higiene: el lanzador avisa si la API ha descartado alguna clave de la
configuración del cliente (pasaba en silencio, y el rastreo salía con otros
ajustes), y la lista de trabajos ya no dice «en curso» de un trabajo
terminado.

---

## En cola

**Desde el 2026-10-16 el orden lo marca [`docs/PLAN_MEJOR_CRAWLER.md`](docs/PLAN_MEJOR_CRAWLER.md)**:
la comparación con Screaming Frog, Sitebulb, Lumar, Botify, JetOctopus y
Oncrawl, los huecos verificados contra el código y las olas de trabajo, cada
una con su criterio de hecho. Los bloques de abajo siguen valiendo como detalle.

El orden de los bloques es el orden recomendado: primero lo que corrige datos,
después lo nuevo.

### 1. Arreglos urgentes

- [#37](https://github.com/dvillarrubia/crawler-masivo/issues/37) La cola de
  trabajos va al revés, se ignora la opción «usar sitemap» y un sitio bloqueado
  por robots.txt da un trabajo «completado» con 0 URLs.
- [#149](https://github.com/dvillarrubia/crawler-masivo/issues/149) **Fallos
  encontrados al compararnos con FreeCrawl** (17 de octubre). El más grave: una
  página con `noindex` en la cabecera HTTP sale como indexable cuando esa
  cabecera lleva antes otra directiva con valor (`max-snippet:-1, noindex`) o
  nombra dos buscadores. Además: los casi duplicados cuentan páginas noindex y
  canonicalizadas, el rastreo guarda las cookies que pone el servidor (Google no
  lo hace), un robots.txt que falla se trata como «todo permitido» cuando Google
  lo trata como «todo bloqueado», y otros menores. La issue lleva una guía de
  implementación punto por punto. En revisión, en cinco PRs:
  [#152](https://github.com/dvillarrubia/crawler-masivo/pull/152) (la cabecera
  `X-Robots-Tag`),
  [#153](https://github.com/dvillarrubia/crawler-masivo/pull/153) (casi
  duplicados),
  [#154](https://github.com/dvillarrubia/crawler-masivo/pull/154) (esquemas,
  credenciales en la URL, hreflang, canonical múltiple y cadenas largas),
  [#156](https://github.com/dvillarrubia/crawler-masivo/pull/156) (credenciales
  y cookies) y
  [#157](https://github.com/dvillarrubia/crawler-masivo/pull/157) (robots.txt).

### 2. Arreglos pendientes de la auditoría

Índice: [#23](https://github.com/dvillarrubia/crawler-masivo/issues/23).

De #24 a #30, **la parte determinista está hecha** (ver «Fiabilidad del dato,
7 de octubre» arriba). Lo que queda en cada una es juicio —decidir si un
carrusel es contenido, si un parámetro de URL es legítimo, qué tipo de página
es cada plantilla— y va con
[#15](https://github.com/dvillarrubia/crawler-masivo/issues/15) y
[#12](https://github.com/dvillarrubia/crawler-masivo/issues/12).

- [#24](https://github.com/dvillarrubia/crawler-masivo/issues/24) Rematar el
  PageRank: mostrarlo en una escala más fácil de leer y avisar en el propio dato
  cuando no es fiable por el JavaScript.
- [#25](https://github.com/dvillarrubia/crawler-masivo/issues/25) Rastreo:
  patrones de exclusión que rompen el rastreo, subdominios que se escapan y URLs
  bloqueadas por robots.txt que no aparecen en el listado.
- [#26](https://github.com/dvillarrubia/crawler-masivo/issues/26)
  Indexabilidad: directivas específicas de Googlebot, varias cabeceras robots y
  canonical enviado en cabecera.
- [#27](https://github.com/dvillarrubia/crawler-masivo/issues/27) Contenido:
  páginas que se quedan a 0 palabras, tablas desordenadas, recuento de palabras
  en japonés o chino y texto oculto.
- [#28](https://github.com/dvillarrubia/crawler-masivo/issues/28) Imágenes,
  datos estructurados y seguridad.
- [#29](https://github.com/dvillarrubia/crawler-masivo/issues/29) Restos del
  análisis automático.
- [#30](https://github.com/dvillarrubia/crawler-masivo/issues/30) Scripts de
  control.

### 3. Funcionalidades nuevas

- [#36](https://github.com/dvillarrubia/crawler-masivo/issues/36) **Clientes
  como centro**: rastreos programados, alertas cuando algo se rompe, informe por
  cliente con su evolución, y rastreos incrementales que solo vuelven a pedir lo
  que cambia.
- [#31](https://github.com/dvillarrubia/crawler-masivo/issues/31) **Backlinks**
  de Ahrefs o DataForSEO: un PageRank con la autoridad que entra desde fuera, y
  mapas de redirección para recuperar enlaces perdidos.
- [#34](https://github.com/dvillarrubia/crawler-masivo/issues/34) **Extracción
  personalizada**: hecha la base, la pantalla y la prueba en vivo (arriba). Falta
  probar con render, buscar en el HTML de origen cuando el rastreo renderiza y
  avisar como incidencia (comparar valores entre censos ya está).
- **Segmentos de negocio** (ola 3.3, decisión 125, en revisión en
  [#172](https://github.com/dvillarrubia/crawler-masivo/pull/172)): «fichas»,
  «blog», «categorías» definidos por cliente, con el porcentaje de cada
  incidencia por segmento. Queda la pantalla para editar las reglas; las
  alertas de la comparación por segmento ya están (decisión 127).
- [#35](https://github.com/dvillarrubia/crawler-masivo/issues/35) **Logs del
  servidor**: qué rastrea Googlebot de verdad, cruzado con el rastreo.
- [#33](https://github.com/dvillarrubia/crawler-masivo/issues/33)
  **WordPress**: cruzar el rastreo con lo que el propio WordPress dice que tiene
  publicado. Hecha la lectura de la REST API durante el rastreo y cuatro avisos
  (contenido publicado que el rastreo no encuentra, páginas de adjunto
  indexables, categorías y etiquetas casi vacías, paginación que no acaba;
  decisión 161). Con interfaz propia: casilla «Analizar WordPress» al lanzar,
  tarjeta en el Resumen (versión, plugin SEO, lo publicado frente a lo
  rastreado por tipo, y plugins y tema por los ficheros que cargan las
  páginas), «Solo en WordPress» en el Explorador y la entidad en la ficha de
  cada URL (decisión 168). Falta verlo en un censo WordPress entero de
  producción.

### 3a. Lo que FreeCrawl tiene y nos falta (17 de octubre)

[#150](https://github.com/dvillarrubia/crawler-masivo/issues/150), con dos guías
de implementación verificadas contra el código. FreeCrawl tiene 171 checks y
nosotros 78; en los que coinciden, nuestra definición suele ser la buena. De lo
que les copiamos, la idea, no su definición. Por olas de
[`docs/PLAN_MEJOR_CRAWLER.md`](docs/PLAN_MEJOR_CRAWLER.md):

- **Con lo que ya guardamos, sin volver a rastrear**, como la ola 1: el
  canonical del HTML distinto del de la cabecera, `noindex` junto a un
  canonical a otra URL, redirecciones que acaban en noindex, avisar del uso de
  meta refresh, H1 vacío (hoy cuenta como presente), enlaces internos sin
  anchor, title de plantilla sin rellenar y description igual al title
  ([#159](https://github.com/dvillarrubia/crawler-masivo/pull/159)). Y la
  paginación (página 2 o siguientes en noindex o canonicalizadas a la 1, y
  páginas siguientes sin `<a>`), que es la ola 1.4 —lo único de la ola 1 que
  quedaba— y va junto con el punto de #150 en
  [#160](https://github.com/dvillarrubia/crawler-masivo/pull/160).
- **Con un rastreo nuevo**: meta viewport, `lang` del HTML, charset, canonical
  relativo y Open Graph; redirecciones por JavaScript sin necesidad del
  navegador ([#155](https://github.com/dvillarrubia/crawler-masivo/pull/155));
  tiempo de respuesta real y HTML sin comprimir (hoy no se puede saber);
  certificado TLS caducado o a punto de caducar; respetar `Retry-After` cuando
  el servidor pide esperar
  ([#158](https://github.com/dvillarrubia/crawler-masivo/pull/158)).
- **Peticiones aparte, sin gastar el límite de URLs**: enlaces externos rotos y
  dominios muertos, junto con los recursos rotos de
  [#101](https://github.com/dvillarrubia/crawler-masivo/issues/101)
  ([#162](https://github.com/dvillarrubia/crawler-masivo/pull/162)). Antes hay
  que dejar de enviar a otros dominios las cookies y la contraseña de un
  staging (#149, #156).
- **Móvil frente a escritorio** (ola 7.4): pedir una muestra con el
  user-agent de smartphone y comparar lo que cambia
  ([#161](https://github.com/dvillarrubia/crawler-masivo/pull/161)). Y
  (decisión 173) el preset «Chrome - Android (móvil)» para rastrear entero
  como un móvil coherente, la comparación automática que no cruza perfiles y
  capturas de pantalla por muestra de plantilla, en uno o los dos perfiles,
  visibles en la ficha de la URL. Quedan las capturas de página entera y
  compararlas entre censos.
- **Los restos** ([#184](https://github.com/dvillarrubia/crawler-masivo/issues/184),
  decisiones 153-156): titulares que saltan de nivel (basta re-analizar); y con
  un rastreo nuevo, el tamaño real de la `og:image` (una muestra acotada,
  encendida de serie) y la tarjeta de X, los vídeos de cada página frente al
  sitemap de vídeo, y las versiones AMP y móvil que anuncia la página.
- **Descartado**: legibilidad y palabras más frecuentes, accesibilidad sin
  navegador (va con axe por muestra, ola 8), validador de AMP y «hreflang que
  nadie enlaza» (excluido a propósito en la decisión 99).

### 3b. Que la interfaz se entienda (milestone M6)

La app tiene casi todo, pero ordenado alrededor del rastreo y no de lo que se
hace con ella. Auditoría y criterio en `docs/AUDITORIA_UX.md`.

- [#119](https://github.com/dvillarrubia/crawler-masivo/issues/119) **Arreglos rápidos**: la ficha que mide 114.000 px, la
  pestaña de enlaces que tarda dos minutos, formatos de cifras y fechas.
- [#120](https://github.com/dvillarrubia/crawler-masivo/issues/120) **Navegación**: enlaces que se pueden pegar, el botón atrás,
  la misma barra en todas partes y menos pestañas.
- [#121](https://github.com/dvillarrubia/crawler-masivo/issues/121) **Problemas como centro**: agrupados por tipo, con qué es y
  cómo se arregla, y una nota que no diga «Bueno» con errores graves abiertos.
- [#122](https://github.com/dvillarrubia/crawler-masivo/issues/122) **El cliente como entrada**: portada por cliente, con su
  último censo y sus alertas.
- [#123](https://github.com/dvillarrubia/crawler-masivo/issues/123) **Pulido**: teclado, tildes, diálogos propios, móvil.

### 3c. Search Console cruzado con el rastreo y con Jev (17 de octubre)

Índice: [#163](https://github.com/dvillarrubia/crawler-masivo/issues/163). Sale del cajón de aparcados con una investigación propia
([`docs/INVESTIGACION_SEARCH_CONSOLE.md`](docs/INVESTIGACION_SEARCH_CONSOLE.md)):
qué da hoy Google, qué no (datos de AI Overviews, búsquedas anonimizadas,
tope de filas, impresiones infladas de mayo de 2025 a abril de 2026) y 21
combinaciones. Los datos van por cliente y por día, con una cuenta de servicio
cuya llave vive en el servidor; «sin datos» nunca se lee como «cero». Por
cliente y no por rastreo, como pide #164: el dato de Google no depende de
cuándo rastreamos. Cambia lo que decía la ola 4.1 del plan («tabla por job»).

- [#164](https://github.com/dvillarrubia/crawler-masivo/issues/164) **Base**: conexión por cliente, descarga nocturna en segundo plano,
  columnas en el explorador, la ficha y el CSV; se retira la conexión antigua
  de la prueba semántica, que guardaba la llave en la base de datos.
- [#165](https://github.com/dvillarrubia/crawler-masivo/issues/165) **Lo que cuesta tráfico**: no indexables con clics, huérfanas con
  tráfico, URLs con clics que el rastreo no encontró, indexables sin
  impresiones, y el informe ordenado por tráfico.
- [#166](https://github.com/dvillarrubia/crawler-masivo/issues/166) **Oportunidades**: posiciones 4-20 con poco enlazado (con las
  páginas desde las que enlazar), CTR bajo para su posición, canibalización por
  búsqueda, rendimiento por plantilla y sitemaps según Google. *Código hecho*
  (decisión 157, PR #219; tablas `gsc_consultas` y `gsc_sitemaps` documentadas,
  umbrales `gsc_*` y `marcas` explicados en notas `_…` de `projects/_ejemplo`, sin claves reales que pisen las del cliente). Falta: la revisión a mano
  en UOC, verlo en producción con un censo real y declarar `marcas` de cada
  cliente.
- [#167](https://github.com/dvillarrubia/crawler-masivo/issues/167) **Alertas con la causa**: la caída de clics agrupada por lo que
  cambió entre dos rastreos. *Código hecho* (decisiones 158 y 165): ficha del
  rastreo y del cliente, Slack al acabar y otra vez, una sola, cuando la
  comparación esperaba a los datos (nunca de la competencia); contenido por `content_hash` con la misma versión del crawler y config de extracción; la caída
  general antes que los grupos. Falta: el cambio de plantilla como grupo
  (decisión del usuario) y reproducir una caída conocida en dos clientes.
- [#168](https://github.com/dvillarrubia/crawler-masivo/issues/168) **Inspección de URL por muestra**: el canonical que elige Google,
  indexables no indexadas y nuestro veredicto frente al de Google.
- [#169](https://github.com/dvillarrubia/crawler-masivo/issues/169) **Jev con búsquedas reales**: intención, embudo, canibalización
  juzgada, cobertura de las oportunidades y snippet. Amplía [#39](https://github.com/dvillarrubia/crawler-masivo/issues/39), [#18](https://github.com/dvillarrubia/crawler-masivo/issues/18) y
  [#22](https://github.com/dvillarrubia/crawler-masivo/issues/22).
  *Punto 3 (canibalización juzgada), código hecho* (decisión 175): módulo
  `canibalizacion` sobre los avisos de la 157, por grupo, con «¿sustitutas?» y
  la recomendación en la misma llamada (0,017 $ los 300 grupos de Lopesan).
  Falta: etiquetar y medir el juego (#8), el umbral y encender el efecto en el
  aviso.
- [#170](https://github.com/dvillarrubia/crawler-masivo/issues/170) **Más adelante**: exportación a BigQuery, GA4 y DataForSEO.

### 4. Análisis enriquecido con Jev

Índice: [#6](https://github.com/dvillarrubia/crawler-masivo/issues/6).

Siempre bajo demanda: un botón en el trabajo, con el coste estimado antes de
lanzarlo y un presupuesto máximo
([#40](https://github.com/dvillarrubia/crawler-masivo/issues/40)). Nada de pago
se lanza solo.

- Detectar los bloqueos que no se anuncian ([#10](https://github.com/dvillarrubia/crawler-masivo/issues/10);
  los que sí se anuncian ya se detectan sin modelo, decisión 103)
  y soft 404 ([#11](https://github.com/dvillarrubia/crawler-masivo/issues/11)).
- Tipo de página ([#12](https://github.com/dvillarrubia/crawler-masivo/issues/12)).
- Intención de búsqueda y embudo TOFU/MOFU/BOFU, contrastados con las keywords
  ([#39](https://github.com/dvillarrubia/crawler-masivo/issues/39)).
- Checks on-page nuevos
  ([#16](https://github.com/dvillarrubia/crawler-masivo/issues/16)), coherencia
  del schema con lo visible
  ([#17](https://github.com/dvillarrubia/crawler-masivo/issues/17)),
  canibalización
  ([#18](https://github.com/dvillarrubia/crawler-masivo/issues/18)) y contenido
  hackeado ([#19](https://github.com/dvillarrubia/crawler-masivo/issues/19)).
- Ordenar el informe por lo que importa en cada página
  ([#22](https://github.com/dvillarrubia/crawler-masivo/issues/22)).
- Validar los datos extraídos y parar el rastreo si dejan de tener sentido
  ([#20](https://github.com/dvillarrubia/crawler-masivo/issues/20)).
- Resto: [#13](https://github.com/dvillarrubia/crawler-masivo/issues/13),
  [#14](https://github.com/dvillarrubia/crawler-masivo/issues/14),
  [#15](https://github.com/dvillarrubia/crawler-masivo/issues/15),
  [#21](https://github.com/dvillarrubia/crawler-masivo/issues/21). La base
  técnica está en [#7](https://github.com/dvillarrubia/crawler-masivo/issues/7),
  [#8](https://github.com/dvillarrubia/crawler-masivo/issues/8) y
  [#9](https://github.com/dvillarrubia/crawler-masivo/issues/9).
- **Hecho, la base ([#7](https://github.com/dvillarrubia/crawler-masivo/issues/7))**:
  el cliente de OpenRouter para Jev con caché (un recrawl no vuelve a pagar lo
  mismo), presupuesto por ejecución, mensual por cliente y global, y la
  observabilidad del gasto en cada trabajo.
- **Hecho, el botón ([#40](https://github.com/dvillarrubia/crawler-masivo/issues/40))**:
  «Análisis enriquecido» en el detalle de un rastreo completado, con el coste
  estimado (lo que ya está en caché no cuenta) y el gasto del mes del cliente
  a la vista antes de confirmar, un presupuesto máximo que corta en seco, y una
  pestaña «Enriquecido» con el progreso, lo gastado, Cancelar y los resultados.
  Lanzar exige un token configurado en el servidor. De momento solo hay un
  módulo, el de la pregunta de ejemplo: los de verdad llegan con #8 y #9.
  Pendiente: comprobar con una clave real que la estimación cae dentro del
  ±30% del coste, la opción por perfil de repetirlo en cada rastreo programado,
  y los resultados como columnas del explorador y del CSV.

---

## Aparcado

- Google Analytics 4 por su cuenta (va con Search Console en [#170](https://github.com/dvillarrubia/crawler-masivo/issues/170)).
- Análisis semántico (`POC_centro_semantico/`): era una prueba y no se va a
  continuar.
- La rama `v2-experimental` (6 de octubre). Su motor ya estaba en master y mas
  nuevo; de sus arreglos se rescataron cuatro y tres no aplicaban
  ([#42](https://github.com/dvillarrubia/crawler-masivo/issues/42) tiene el
  inventario). Queda archivada en la etiqueta `archivo/v2-experimental`. Lo que
  tenia en exclusiva —GSC/GA4, entidades, grafo— esta aparcado o descrito mejor
  en [#36](https://github.com/dvillarrubia/crawler-masivo/issues/36) y
  [#6](https://github.com/dvillarrubia/crawler-masivo/issues/6).
