# Roadmap

Qué hace hoy el crawler y qué viene después, contado por funcionalidades. El
detalle técnico de cada punto está en la issue de GitHub enlazada.

Estado a 7 de octubre de 2026.

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

### Auditoría SEO automática

- Códigos de respuesta, titles, descriptions, encabezados, canonicals,
  hreflang, datos estructurados e indexabilidad.
- Indexabilidad calculada sobre la URL final tras redirecciones; las páginas
  bloqueadas por robots.txt no cuentan como indexables.
- Duplicados exactos y casi duplicados (páginas que comparten el 90 % del texto
  o más).
- Cadenas de redirección, imágenes sin alt, seguridad, contenido escaso, URLs
  problemáticas, páginas huérfanas y cobertura del sitemap.
- PageRank interno:
  - las redirecciones y los canonicals pasan la autoridad a la página correcta;
  - los menús y enlaces repetidos no cuentan como contenido;
  - mide cuánto PageRank se desperdicia en errores y redirecciones.
- Aviso automático cuando el JavaScript esconde enlaces y el PageRank puede no
  ser fiable.

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

El orden de los bloques es el orden recomendado: primero lo que corrige datos,
después lo nuevo.

### 1. Arreglos urgentes

- [#37](https://github.com/dvillarrubia/crawler-masivo/issues/37) La cola de
  trabajos va al revés, se ignora la opción «usar sitemap» y un sitio bloqueado
  por robots.txt da un trabajo «completado» con 0 URLs.

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
  personalizada**: sacar campos concretos de cada página (precio, SKU,
  autor…).
- [#35](https://github.com/dvillarrubia/crawler-masivo/issues/35) **Logs del
  servidor**: qué rastrea Googlebot de verdad, cruzado con el rastreo.
- [#33](https://github.com/dvillarrubia/crawler-masivo/issues/33)
  **WordPress**: cruzar el rastreo con lo que el propio WordPress dice que tiene
  publicado.

### 4. Análisis enriquecido con Jev

Índice: [#6](https://github.com/dvillarrubia/crawler-masivo/issues/6).

Siempre bajo demanda: un botón en el trabajo, con el coste estimado antes de
lanzarlo y un presupuesto máximo
([#40](https://github.com/dvillarrubia/crawler-masivo/issues/40)). Nada de pago
se lanza solo.

- Detectar bloqueos ([#10](https://github.com/dvillarrubia/crawler-masivo/issues/10))
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

---

## Aparcado

- Google Search Console y Google Analytics 4.
- Análisis semántico (`POC_centro_semantico/`): era una prueba y no se va a
  continuar.
- La rama `v2-experimental` (6 de octubre). Su motor ya estaba en master y mas
  nuevo; de sus arreglos se rescataron cuatro y tres no aplicaban
  ([#42](https://github.com/dvillarrubia/crawler-masivo/issues/42) tiene el
  inventario). Queda archivada en la etiqueta `archivo/v2-experimental`. Lo que
  tenia en exclusiva —GSC/GA4, entidades, grafo— esta aparcado o descrito mejor
  en [#36](https://github.com/dvillarrubia/crawler-masivo/issues/36) y
  [#6](https://github.com/dvillarrubia/crawler-masivo/issues/6).
