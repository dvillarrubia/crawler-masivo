# Decisiones: enlazado interno, pagerank y profundidad

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d27"></a>
27. **Las redirecciones las sigue el spider, no Scrapy** — toda peticion de
   pagina lleva `dont_redirect`. Si las sigue el `RedirectMiddleware`, la
   peticion al destino pasa por el dupefilter y, si el destino ya se habia
   visto, se descarta con la 301 dentro: el salto no llegaba a `parse`, no se
   guardaba, y los enlaces que apuntaban a el desaparecian del grafo (faltaban
   la mayoria de las 301 internas: http→https, barra final, www). Ahora cada
   salto es su propia fila con `redirect_url`, el destino se pide con la
   MISMA profundidad (una redireccion no es un clic: sumar uno le quitaba un
   nivel entero al rastreo de una semilla `http://x.com`), y la meta refresh
   con URL se trata igual. El PageRank anade la arista salto → destino con
   peso 1 y el destino de una redireccion interna ya no sale huerfano. Con
   render JS la redireccion la sigue el navegador: se registra el salto sin
   codigo (`Redirect (JS)`). La profundidad la respeta
   `middlewares.DepthMiddleware`, que sustituye a la de Scrapy porque esa la
   pisaba (las URLs del sitemap entraban a profundidad 3 en vez de 1).

<a id="d29"></a>
29. **PageRank: la repeticion pone techo, no sustituye a la posicion** — el
   peso por posicion se adivina por etiquetas y clases y falla con menus en
   `div`, Tailwind o facetas (salian `content`, peso 1). Ahora se mide la
   repeticion de cada enlace (destino + anchor) en el sitio y en su seccion
   (host + primer segmento, desde 10 paginas), y lo repetido no pesa mas que
   un enlace de menu: techo `0,1 / repeticion`, nunca por debajo de 0,25. #24
   proponia `1 - sqrt(rep)`; se descarto porque lo que recibe un destino
   (rep x peso) CAE a partir del 44%: estar enlazado desde todo el sitio
   restaba. Con el techo el total nunca baja (hay test). Ademas: variante →
   canonical (si la canonica se rastreo con 200) y salto → destino con peso 1;
   el teletransporte y la masa colgante van solo a paginas 200 indexables, y
   `jobs.pagerank_resumen` guarda cuanto acaba en cada tipo de URL. Medido en
   seobide, workoholics, Lopesan y tucanaldesalud: las paginas legales siguen
   arriba cuando todo el sitio las enlaza desde el pie; es un dato del sitio,
   separarlas es trabajo del tipo de pagina (#12). Si la home baja, mirar de
   donde le llegaba: en re-magazine era 1.a solo por el logo de un aviso de
   navegador antiguo (`body > div.deprecation-notice`, fuera de header/nav/
   footer, clasificado `content`) presente en 181 de 183 paginas; con techo
   es 13.a y arriba quedan los tags, que ademas del menu reciben enlaces
   dentro de los articulos. Es B1 funcionando. Coste: 4-5 s con 150-180 k
   aristas, 400 s en Quironsalud (7,7 M enlaces). Materializar los enlaces con el
   anchor ya calculado es obligatorio: unir por `lower(btrim(anchor))` dejaba
   al planificador sin estimacion y tardaba entre 30 y 80 veces mas.

<a id="d34"></a>
34. **El PageRank en tres columnas, porque son tres preguntas** — la escala
   0-10 lineal no distingue la pagina 250 de la 25.000: la mas fuerte vale 10
   y el resto se apelotona en 0,00xx (medido en blogs.uoc.edu: 29.483 de
   34.704 por debajo de 0,1, el 85% indistinguible). Ahora
   `pagerank_raw` es la probabilidad —por el numero de nodos da "veces la
   pagina media", comparable entre sitios de tamano distinto—, y
   `pagerank_score` es 0-100 logaritmica anclada al MAXIMO del sitio, donde
   cada 25 puntos son una decada. Es la que hay que leer y ordenar.
   `pagerank` se queda en 0-10 por compatibilidad con informes entregados.
   El 0-100 es lo que se ve en la tabla de URLs y en la ficha de una URL; el
   0-10 solo aparece en la ficha, en gris, rotulado «antiguo». Que la columna
   de la tabla siguiera siendo la de 0-10 hacia invisible el arreglo entero:
   las nueve primeras filas de blogs.uoc.edu se leian
   `0,4 0,0 0,0 0,2 0,0 0,3 0,0 3,2 7,1` y ahora `65 38 38 58 20 63 20 88 96`.
   La primera version la ancle con min-max sobre los logaritmos y el 78% de
   las paginas acabo en el decil mas alto: el minimo de la distribucion es una
   pagina aislada que estira la escala entera. Con el maximo como ancla, el
   mismo censo cubre los diez tramos. Las cuatro decadas no son arbitrarias:
   el maximo es 393 veces la mediana y 4.515 veces el percentil 25.

<a id="d35"></a>
35. **El aviso de que el grafo no es fiable viaja CON el dato, tambien en
   pantalla** — si
   `jobs.js_check.grafo_fiable` es false hay plantillas que montan sus enlaces
   con JavaScript y el PageRank sale de un grafo incompleto. Antes eso solo
   existia como WARNING en el log del worker y la cifra se entregaba como si
   nada. Ahora esta en `jobs.pagerank_resumen` y en una columna del CSV
   (`pagerank_fiable`), porque quien ordena por PageRank en una hoja de
   calculo no abre el endpoint del job.
   Ojo al ORDEN, que durante un tiempo dejo esa columna vacia en todas las
   filas: el analisis escribe `pagerank_resumen` **antes** de que el worker
   lance la comprobacion de render, asi que en un rastreo recien terminado la
   clave existe con valor `None`. El CSV miraba si la clave estaba, no su
   valor, y devolvia ese None sin llegar a consultar el `js_check`. Medido en
   el censo de progym: 4.320 filas sin el aviso teniendo
   `js_check.grafo_fiable = true`. Un `None` es «aun no se sabe». Y con
   `render_js` la comprobacion no llega a correr —existe para los rastreos SIN
   render—, asi que ahi la respuesta es que si: un rastreo que renderiza ya ha
   visto los enlaces que monta el JavaScript, que es lo unico que mide esta
   columna. Y en el frontend, que es donde se mira
   un rastreo: un aviso en la ficha del job cuando `grafo_fiable` es false, con
   las plantillas afectadas, y otro distinto cuando es null (no se pudo
   comprobar porque Chromium no recibio contenido). Ademas la tabla pinta
   `pagerank_score` —la escala 0-100 de la decision 34— en vez de la 0-10 con
   cuatro decimales, que era la unica que habia: con esa, el 85% de las paginas
   de un censo queda en 0,00xx y no se distingue la 250 de la 25.000. Para eso
   hacia falta exponer `pagerank_score` y `pagerank_raw` en la respuesta de
   `/urls`, que solo llevaba `pagerank`.

<a id="d36"></a>
36. **Un aviso por hallazgo, no dos** — las URLs del sitemap sin inlinks
   recibian `orphan_page` y `sitemap_orphan` a la vez. Dicen lo mismo y el
   segundo es mas fuerte (esta declarada para indexar y aun asi nadie la
   enlaza), asi que el informe parecia tener el doble de problemas. Medido:
   en www.uoc.edu, 8.045 + 7.475 filas para unas 8.000 paginas pasan a 15 +
   6.498. `orphan_page` se reserva para las que NO estan en el sitemap, y de
   esas se siguen excluyendo los destinos de redireccion (555 de 570 en ese
   censo) para no llenar el informe de huerfanas falsas tras una migracion.

<a id="d37"></a>
37. **El job se filtra en `links`, nunca uniendo con `urls`** — la tabla de
   enlaces guarda los de TODOS los rastreos: 46 GB y 181 millones de filas en
   la instalacion de produccion. Poner el `job_id` en el lado de `urls` deja
   el filtro fuera del alcance del indice y Postgres recorre la tabla entera.
   Medido con EXPLAIN en la consulta nueva de `high_outlink`: coste 6.189.456
   uniendo, 122.247 filtrando por `links.job_id`. Cincuenta veces, y en tiempo
   real `analyze_links` paso de minutos a 1,7 s en blogs.uoc.edu y 11,2 s en
   www.uoc.edu. Lo pille porque un re-analisis que antes tardaba 219 s llevaba
   doce minutos sin terminar.

<a id="d38"></a>
38. **Una pagina `noindex` no enlaza: ni como entrante ni en el PageRank**
   (C3 de #24, criterio decidido el 7-oct-2026). Google acaba tratando los enlaces de
   una noindex como nofollow, asi que una pagina cuyos unicos enlaces vienen de
   ahi no esta enlazada a efectos de buscador: cuelga de paginas que el
   buscador va a dejar de rastrear. El `noindex` se materializa en
   `urls.noindex`, separado de `indexable` —que tambien es False por canonical
   o por codigo— y no se puede deducir en SQL: buscar la subcadena marcaria
   `noindex/nofollow`, que Google NO interpreta (decision 23). Si el dato no se
   conoce (NULL) el enlace cuenta: no se descarta por desconocimiento.
   **Ojo al re-analizar un censo anterior a la columna**: `urls.noindex` la
   materializa `analyze_indexability`, asi que en un rastreo viejo estaba a
   NULL —y `NULL IS NOT TRUE` es cierto, o sea que esas paginas SI repartian—.
   Al re-analizarlo se rellena y salen del grafo: en Druni, un censo de hace
   dos meses, aparecieron **9.771 paginas noindex** y con ellas se fueron sus
   aristas. El PageRank de un censo viejo CAMBIA al re-analizarlo, y no es un
   fallo: antes se calculaba con todas repartiendo. Si se vuelve a entregar un
   informe de enlazado de un censo anterior, las cifras no cuadraran con las de
   la primera entrega y la explicacion es esta.
   Medido en blogs.uoc.edu: 1.813 paginas noindex, y 992 URLs cuyos unicos
   enlaces venian de ellas — 824 con parametros (busquedas internas, trampas de
   rastreo) y 168 sin ellos, que son el hallazgo: categorias con 400-800
   palabras, fuera del sitemap, colgando solo de su paginacion noindex.

   El mismo criterio vale en el PageRank: una noindex **recibe pero no
   reparte**. Lo que ya tenia acumulado no se pierde —queda colgante y su masa
   va al teletransporte, o sea al conjunto de indexables— pero no va a los
   destinos concretos que ella enlazaba. Que SI reciba es deliberado: un enlace
   a una noindex es autoridad que se tira, y eso es justo lo que mide
   `reparto["no_indexable"]`. Cuantas paginas son fuente de verdad del grafo
   cambia muchisimo por sitio y no se puede estimar con `outlinks_count` (que no
   filtra por `follow`): medido con los enlaces del grafo, blogs.uoc.edu tiene
   **1.758 paginas noindex que enlazan 203.380 veces** a 7.400 destinos,
   www.uoc.edu solo 44 (10.775 enlaces) y progym 3 (519). Por eso el efecto
   tambien cambia: en www.uoc.edu quita 7.496 aristas de 2.013.337 y mueve el
   reparto 7 centesimas (84,02% -> 84,09% indexable), sin que entre ni salga
   nadie del top 50 y con 2 puestos de movimiento maximo; en progym, 474 aristas
   de 582.205 y el reparto no se mueve; pero en blogs.uoc.edu quita **127.360
   aristas de 1.517.272** (el 8,4% del grafo) y pasa **seis puntos** de PageRank
   de las noindex a las indexables (no_indexable 8,46% -> 2,29%, indexable
   87,22% -> 93,12%). Son sus 1.758 paginas de paginacion de categoria
   enlazandose entre ellas: la autoridad circulaba en un circuito cerrado que
   Google va a dejar de rastrear. El ORDEN casi no se mueve en ninguno de los
   tres —del top 50 no entra ni sale nadie— pero los valores absolutos suben (el
   1.º de blogs pasa de 108,3x a 119,9x la pagina media).

<a id="d41"></a>
41. **Los conteos de enlaces leen `links` UNA vez, no cuatro** (#52) — la
   tabla guarda los enlaces de TODOS los rastreos (46 GB y 181.695.835 filas en
   produccion) y el planificador la recorre entera para sacar los de un job:
   cuatro agregados eran cuatro recorridos. Ahora las aristas del censo se
   materializan una vez en una temporal —lo mismo que ya hacia
   `compute_pagerank`— con el id del DESTINO ya resuelto, asi que los conteos
   no vuelven a `urls` ni filtran por job. Medido en penguin (10,3 M enlaces
   del job): **400,8 s -> 57,0 s, siete veces**, y las tres cifras de control
   (6.227.726 inlinks, 19.495 paginas sin entrantes, 9.600.373 outlinks) salen
   IDENTICAS: es una optimizacion, no un cambio de criterio disfrazado.

   La temporal se construye con tipos de SQLAlchemy y no con SQL a mano: el
   `job_id` es un UUID y comparado como cadena no liga igual en todos los
   motores — la primera version no actualizaba ni una fila en los tests contra
   SQLite. Los indices de la temporal van con nombre, porque
   `CREATE INDEX ON tabla (col)` es sintaxis solo de Postgres.

   Cronometrado tambien en Druni (60.399 URLs, **37,5 millones de enlaces**,
   3,6 veces penguin): `compute_link_counts` **299,3 s** y el `run_all` entero
   **2.585,8 s (43 min)**. O sea que el agregado de enlaces escala razonable
   (3,6x las aristas -> 5,2x el tiempo) y en un censo de ese tamano **el coste
   dominante ya no es este sino `compute_pagerank`**: unos 35 de esos 43
   minutos se van en montar SU propia materializacion de aristas
   (`pr_lk_tmp` -> `pr_rep_tmp` -> `pr_edges_tmp`). Las dos materializan casi
   la misma tabla dos veces en el mismo analisis, **pero compartirla ahorraria
   poco**: cronometrando cada sentencia de `_aristas_de_enlaces` en Druni, el
   recorrido de `links` (`pr_lk_tmp`) son **114,9 s de 1.770,9 — el 6,5%**,
   mientras que las ventanas de repeticion (`pr_rep_tmp`) son 781,6 s (44%) y
   la agregacion final por (origen, destino) con el peso (`pr_edges_tmp`) otros
   862,3 s (49%). El coste esta en las dos agregaciones sobre 37,5 millones de
   filas, no en leer la tabla. La direccion que si tendria efecto —anotada en
   #52— es que `pr_lk_tmp` guarda una fila por INSTANCIA de enlace y la tabla
   final es por (origen, destino), que son muchisimas menos: agregar antes del
   join atacaria los dos pasos caros a la vez, pero toca el criterio y necesita
   su propia comprobacion de que las cifras no se mueven.

<a id="d57"></a>
57. **Un enlace desde una página rota no enlaza nada** — un 404 o un 500 sale
   del índice y se lleva sus enlaces con él, así que lo que solo cuelga de ahí no
   está enlazado a efectos de buscador. Es el mismo criterio que la decisión 38
   aplicó a las `noindex`, y faltaba para los códigos de error. Medido en
   www.uoc.edu: **503 páginas que devuelven HTTP 500**, cada una con su menú
   completo, aportaban **264.991 enlaces** a 2.775 destinos, y **137 URLs tenían
   TODOS sus entrantes internos ahí** — huérfanas de hecho que salían enlazadas y
   por tanto no aparecían en el informe de huérfanas. En progym, 0 casos, que es
   lo que tiene que pasar en un sitio sano. Se aplica en `compute_link_counts`
   (el origen tiene que ser 200) y en `compute_pagerank` (una página que no
   responde 200 no entra como fuente, así que no reparte autoridad que no tiene).
   El modelo de nofollow (C2) y el de `noindex,follow` en el PageRank siguen
   siendo decisión abierta. Re-analizado el censo con el arreglo: **240.543
   enlaces entrantes menos** (el 6,7% del total) y **19 páginas más** que
   aparecen con cero entrantes; `orphan_page` se queda en 370 porque esas 19
   están declaradas en el sitemap y por eso van a `sitemap_orphan` (decisión 36:
   un aviso por hallazgo, y ese es el más fuerte de los dos).
   Lo mismo con `rel="ugc"` y `rel="sponsored"`, que Google documenta como la
   misma pista que `nofollow`: se leía solo `nofollow`, así que un enlace
   patrocinado sumaba autoridad. Medido en cuatro censos (28,4 M enlaces): 224
   con `ugc`, ninguno con `sponsored` y solo 12 internos — corrección de
   criterio, no de volumen.

<a id="d58"></a>
58. **Un menú en un `<div class="main-menu">` no es la cabecera** — la
   auditoría midió que `link_position` fallaba en **13 de 24** plantillas
   realistas. Al escribir esos 24 casos como test salían ya **20/24** (los
   arreglos de octubre —tokens por palabra entera, camelCase, landmarks— habían
   resuelto la mayoría) y los cuatro que quedaban se arreglaron: el token `menu`
   y sus variantes entran en la lista de navegación —`main-menu`,
   `primary-menu`, `mobile-menu` y `menu-item` salían como **contenido**, y un
   menú contado como editorial es justo lo que hacía saltar
   `high_outlink_count` en todas las páginas—; `id="secondary"` es la barra
   lateral de los temas de WordPress, pero solo como **id**, porque como clase
   el `btn-secondary` de Bootstrap marcaría de barra lateral los enlaces de
   cualquier botón; y un `<aside>` dentro de `article`/`main` es contenido, la
   misma regla de landmark de las decisiones 9 y 45.
   Medido en las 58 páginas de control: `nav` 3.467 → 7.113 y `header` 3.772 →
   325, casi todo el movimiento siendo **header → nav** (3.447 enlaces del menú
   que vive dentro del `<header>`). Para el PageRank eso no cambia nada —`nav` y
   `header` pesan 0,25 los dos— y `content` se mueve 2 enlaces, así que
   `high_outlink_count` tampoco; lo que se arregla es la posición que se
   **exporta** y con la que se analiza el anchor text: un enlace de menú ahora
   dice que es de menú. Lo que sí cambia peso son los 199 enlaces que pasan de
   footer (0,15), sidebar (0,4) o content (1,0) a nav.
   Riesgo conocido y asumido, escrito en el test: el `class="menu"` de una carta
   de restaurante se clasifica como navegación. Equivocarse al otro lado afecta a
   todas las páginas del sitio; este caso, a una.

<a id="d60"></a>
60. **Un `nofollow` no diluye, y está medido que da igual** (C2 de #24) — hoy
   los enlaces `nofollow` no entran en el grafo ni en el denominador, así que los
   `follow` de esa página se reparten el 100%. Es el *PageRank sculpting* que
   Google desactivó en 2009; su criterio sería que el `nofollow` consume su parte
   y se tira. Probados los dos modelos sobre los tres censos
   (`docs/experimentos/c2_nofollow.py`), incluido un e-commerce donde el **28,7%**
   de los enlaces internos son `nofollow`: el peso que se movería es del 0,9% al
   2,0%, el top 50 coincide en 49 de 50 y `pagerank_score` cambia **2-3 puntos
   sobre 100** como máximo, sin una sola página moviéndose 5. No se toca. Si
   alguna vez se toca, la masa perdida va al teletransporte, igual que la de las
   `noindex` (decisión 29 y C3).

<a id="d63"></a>
63. **Lo que solo se alcanza por un salto tampoco puede quedarse sin aviso**
   — excluir los destinos de redireccion de `orphan_page` (decision 36) evita
   llenar el informe de huerfanas falsas tras una migracion, pero abrio un
   agujero: una pagina **indexable, con 200 y cero enlaces internos**, a la que
   solo se llega a traves de un 301, no recibia NINGUN aviso. Medido sobre los
   tres censos: **522 en www.uoc.edu (363 declaradas en el sitemap)**, 109 en
   blogs.uoc.edu y 1 en progym; ninguna con un solo issue. La de progym es
   ademas la pagina **mas fuerte del sitio por PageRank**: hereda el de la URL
   vieja, que es la que todo el mundo sigue enlazando, y en el CSV se leia como
   «PageRank 100, enlaces entrantes 0».
   No es `orphan_page` —la pagina es alcanzable y por el salto le llega casi
   toda la senal—: lo que hay que arreglar es concreto y distinto, que los
   enlaces internos apunten a la URL final, porque Google acaba dejando de
   rastrear la vieja. Si ademas esta en el sitemap, se declara para indexar algo
   que el propio sitio no enlaza: `warning`; sin sitemap, `info`.

<a id="d90"></a>
90. **La profundidad se cuenta en clics desde la home, no desde las semillas**
   — `crawl_depth` son los saltos del rastreador desde donde empezo: cada
   semilla es 0 y lo del sitemap 1. Con un censo sembrado desde la home se
   parece a la profundidad de verdad; con una lista de semillas no significa
   nada, y era la que se pintaba como «Prof.». Medido en el censo semanal de
   Lopesan, sembrado con 3.040 URLs: **2.867 de 3.237 indexables a
   profundidad 0**. `urls.click_depth` la calcula el analisis en
   `compute_click_depth`, sobre las mismas aristas que los enlaces entrantes
   (sin otra lectura de `links`, decision 41) y por niveles en el servidor, no
   cargando el grafo en Python. Criterio:
   - se parte de la **raiz de cada host de las semillas y de las portadas de
     idioma que la home declara en su hreflang**. Contando solo desde `/es/`,
     todo lo de `/en/` y `/de/` salia varios clics mas lejos —llegaba cruzando
     de idioma a media navegacion—: con las portadas, el mismo censo pasa de
     1.710 indexables a 4-6 clics a 386, y de 707 a 7 o mas a 9. El hreflang de
     cualquier otra pagina no hace portada a su destino;
   - **una redireccion no es un clic** (como en el spider, decision 27): la
     raiz de Lopesan es un 302 a `/es/`, y `/es/` es la 0;
   - se avanza solo por enlaces internos **follow** desde paginas **200 no
     noindex** (decisiones 38 y 57). Al reves que en los entrantes, el
     `nofollow` no es un camino: el enlace existe, pero no se sigue;
   - **sin camino = NULL**, y si la pagina es indexable y alguien la enlaza
     emite `not_reachable_from_home` (warning): una isla que solo se alcanza
     desde semillas o desde el sitemap. Si nadie la enlaza ya es `orphan_page`
     o `sitemap_orphan` (decision 36). En Lopesan: **461 indexables sin
     camino**, 45 enlazadas desde otras islas —sobre todo posts del blog— y 416
     huerfanas. Si no se rastreo la raiz de ningun host, la columna queda vacia
     y no se avisa de nada: sin home no hay de donde contar.
   `crawl_depth` se queda: es la que aplica `max_depth`. La tabla, la ficha, el
   explorador y el CSV muestran la de clics, y la ficha tambien la de rastreo,
   en gris. No hace falta re-rastrear: basta **re-analizar** (3,8 s el
   agregado de enlaces con la profundidad incluida, en ese censo).

<a id="d94"></a>
94. **Un enlace repetido en la misma pagina es UNA fila, no tres** — `links`
   guardaba una fila por aparicion, y el megamenu sale dos o tres veces por
   pagina (escritorio, movil, desplegable). Medido en la BD local, censo de
   Druni (60.399 URLs): **139 M filas, el 97,3% de menu**, unas 2.400 por
   pagina y ~30 GB solo ese censo, de los 48 GB de toda la tabla. Agrupando las
   apariciones IDENTICAS de cada pagina (mismo destino, ancla, posicion, rel,
   follow, target, alt y tipo) quedan **42 M filas y 9,0 GB**, medido creando la
   tabla agrupada. `agrupar_enlaces()` lo hace en el spider antes de emitir los
   `LinkItem` —el seguimiento de enlaces sigue usando la lista entera— y
   `links.repeticiones` guarda cuantas eran.
   **No cambia ninguna cifra**: los conteos por aparicion (inlinks, outlinks,
   externos, las estadisticas de /insights) pasan de `COUNT(*)` a
   `SUM(repeticiones)`; los inlinks unicos cuentan origenes distintos y el
   PageRank ya deduplicaba antes de sus dos pasos caros (`pr_lk_tmp` es
   `DISTINCT`), asi que ninguno de los dos mira las repeticiones. Un enlace de
   menu y el mismo en el cuerpo siguen siendo DOS filas, porque la posicion
   difiere: la paridad con Screaming Frog (dos inlinks, un inlink unico) se
   mantiene. Lo cubre un test que analiza el mismo sitio guardado de las dos
   formas y exige los mismos conteos.
   Los censos viejos se compactan con `scripts/compactar_enlaces.py` (sin
   `--aplicar` solo cuenta), sin re-analizar. Postgres no devuelve el espacio
   al disco: queda libre dentro de la tabla para los rastreos siguientes, y
   para encoger el fichero hace falta `VACUUM FULL links`, que bloquea la tabla
   entera — ventana de mantenimiento, sin rastreos.
   Lo que queda por ganar, por orden: cada fila sigue llevando la URL de
   destino entera (53 bytes de media) **y** su SHA-256 en hexadecimal (65), mas
   el `job_id` (16) que ya se deduce del origen. Y lo que queda tras agrupar
   sigue siendo sobre todo el menu, repetido entre paginas: guardarlo una vez
   por plantilla es el siguiente salto, pero toca el modelo de datos entero.

<a id="d95"></a>
95. **Los enlaces de un censo superado se archivan en Parquet** — la decision
   94 hace cada censo unas tres veces mas pequeno, pero `links` seguia
   creciendo con CADA rastreo: con una programacion semanal, un sitio como
   Druni (9 GB por censo agrupado) llena el VPS en unas semanas. Los enlaces
   de un censo se escriben una vez y no se tocan, y en Parquet con zstd ocupan
   una fraccion: medido, **Druni 139 M filas → 68 MB** y **www.uoc.edu 4,8 M
   filas → 40 MB** (archivar 105 s, restaurar 253 s, cifras identicas a la
   vuelta).
   Se archiva un censo **superado**: hay otro completado, mas nuevo y del mismo
   host cuyas semillas cubren las suyas —la contencion con la que
   `comparacion.censo_anterior` elige la referencia (decisiones 70 y 86)—, no
   es el ultimo de su programacion (78) y termino hace mas de
   `ARCHIVAR_DIAS_MINIMOS` (14). Asi nunca se archiva un censo que pueda ser la
   referencia de una comparacion. Uno fallido, cancelado o estancado basta con
   que tenga un completado mas nuevo del mismo host. El worker lo hace solo al
   cerrar cada rastreo completado (`ARCHIVAR_ENLACES=0` lo apaga);
   `scripts/archivar_enlaces.py` lo lista en seco o lo aplica a mano.
   El orden lo hace seguro: el fichero se escribe a un temporal, se relee y
   solo si cuadran filas Y suma de repeticiones con la BD se renombra; la marca
   `jobs.enlaces_archivados` y el borrado van en la misma transaccion.
   **Lo archivado se sigue leyendo**: ficha de URL, `GET /links`, los dos CSV,
   los origenes del explorador, `/insights` (con las sumas guardadas al
   archivar) y el backup salen del fichero, y un test exige que la API devuelva
   lo MISMO antes y despues. Una diferencia, asumida: el CSV de enlaces sale
   agrupado por destino (el orden del fichero, el que necesita la paginacion) y
   no por id. **Lo que no se puede es re-analizarlo**: el analisis calcula
   entrantes, PageRank y profundidad con SQL sobre `links`, y sin filas los
   pondria a cero con todo el censo huerfano. `run_all` lanza
   `EnlacesArchivados`; se restaura con `--restaurar <job_id>`. El backup de un
   censo archivado lleva sus enlaces y el job importado vuelve a tenerlos en
   `links`.
   Lo que NO hace: encoger un censo vivo (eso es la fase 2, que los enlaces no
   entren nunca en Postgres y el analisis los lea del Parquet) ni devolver el
   espacio al disco: Postgres lo reutiliza para los rastreos siguientes, y para
   que el fichero encoja hace falta `VACUUM FULL links` en una ventana sin
   rastreos. El volumen `archivo` (`/data/archivo`) lo comparten worker y API.

<a id="d106"></a>
106. **Lo que Google no puede seguir se guarda aparte, y lo de la plantilla se
   avisa una vez** (ola 2.3 del plan). Cuatro avisos sobre enlaces internos
   (`enlace_no_rastreable`, `enlace_interno_a_error`,
   `enlace_interno_a_redireccion` y `utm_en_enlace_interno`) y una tabla nueva. Google solo sigue un `<a>` con un `href` que resuelve a una URL
   («Make your links crawlable»), y `extract_links` tiraba en silencio todo lo
   demas: un selector de idioma hecho con `onclick` no aparecia en ningun
   informe.
   - **Tabla propia, no una columna en `links`.** El plan decia «`crawlability`
     en `links`». `links` es el grafo: la leen mas de veinte consultas
     —entrantes, PageRank, profundidad, el explorador, los CSV, la comparacion,
     el archivo en Parquet (95)—, y una fila que no es arista obligaria a
     filtrarla en todas; la que se olvide cambia el PageRank sin avisar, que es
     como acaban dos criterios discrepando (23, 39). `enlaces_no_rastreables`
     (con `job_id`, decision 37) lleva la columna `crawlability` y sale de una
     funcion nueva, `extract_enlaces_no_rastreables`, disjunta de
     `extract_links` por construccion: nada de esto entra en el grafo, en los
     entrantes ni en la frontera. Comprobado en 71 paginas de control: la salida
     de `extract_links` es **identica a la de master en las 71**. Coste:
     2,2 ms por pagina frente a los 48 de `extract_links`.
   - **La taxonomia es la de Google**, con sus ejemplos como tests:
     `javascript_href`, `onclick_sin_href`, `a_sin_href` (`routerLink`,
     `data-href`, `ng-href`), `href_fuera_de_a` (`<span href>`),
     `onclick_en_otra_etiqueta` (la tarjeta clicable sin `<a>` dentro) y
     `fragmento_como_ruta` (`#/x`, `#!x`). **No se guarda**, y es lo que mas
     pesaba: `href="#"`/`""` sin manejador (98 en las 71 paginas), `#ancla`
     (215) y un `onclick` que no navega (55 «Leer mas», `scrollTo`, modales,
     que la primera version contaba como enlaces). Todo eso es la forma de un
     boton y no se sabe si va a algun sitio; avisarlo seria afirmar lo que no se
     sabe (67, 89). Tampoco el `href="#!"` a secas (el conmutador de menu de
     Materialize) ni un elemento con guion en el nombre (`<ion-item href>`,
     `<sl-button href>`): un componente web suele pintar un `<a href>` en su
     shadow DOM, que Google aplana al renderizar, y sin render no se sabe. El
     `href="javascript:..."` si se guarda aunque no diga a donde va: es un
     `href` que Google intenta resolver y lo documenta expresamente; sale como
     `info`. El destino se saca del codigo **solo si NAVEGA** con la URL entera
     (`location=`, `location.assign/replace`, `window.open(...)`, y
     `goTo`/`navigate`/`redirect` con la URL como unico argumento; nada con un
     `+` o un `${}` detras): el `goTo('products')` del ejemplo de Google no dice
     a donde va, y una cadena con forma de ruta tampoco (ver la revision, abajo).
     **Antes y despues en las 71 paginas** (8 sitios; progym solo dejo bajar la
     home, el resto 403): 0 → 220 enlaces no rastreables en 32 paginas —110
     `javascript_href`, 57 `onclick_sin_href`, 52 `onclick_en_otra_etiqueta`, 1
     `a_sin_href`—, 95 con destino interno (111 antes de la revision).
     `href_fuera_de_a` y `fragmento_como_ruta`: 0, no hay ningun sitio asi en el
     corpus.
   - **Por pagina de origen, salvo lo que es de plantilla.** La unidad de
     trabajo es la pagina donde se edita el enlace: una incidencia por pagina
     con la cuenta y ejemplos, nunca una por enlace. Pero un enlace del menu
     se arregla una vez, y por origen salia en TODAS las paginas — lo que la
     decision 64 dice que deja de ser un aviso. Un enlace presente en
     `enlaces_plantilla_min_paginas` paginas o mas (10, el umbral de seccion
     de la 29; en `AnalysisThresholdsConfig`) se avisa una vez. Medido sobre
     los censos locales, con `compute_link_counts` en una transaccion deshecha:

     | censo (HTML 200) | a redireccion: por origen → con plantilla | a 4xx/5xx |
     |---|---|---|
     | Lopesan (4.318) | **4.317** → 1.205 + 367 | 37 → 35 |
     | www.uoc.edu (21.826) | 13.652 → 1.661 + 333 | 7.478 → 1.983 |
     | penguin (28.454) | **28.454** → 727 + 50 | 785 → 352 |

     (Cifras tras la revision: la UOC baja de 2.505 + 714 y penguin de 752 + 51
     porque ya no se repite lo que dice `solo_enlazada_por_redireccion`, ver
     abajo; Lopesan no tiene ninguna.)

     Por origen a secas, el aviso de redirecciones salia en el 99,98% y el 100%
     de las paginas de dos sitios. Que va en cada caso: a un 3xx de plantilla,
     un aviso en la URL que redirige (`alcance: plantilla`, con cuantas paginas
     la enlazan); a un 4xx/5xx de plantilla, **ninguno nuevo**: la URL rota ya
     tiene su `4xx_error`/`5xx_error` y sus entrantes dicen desde donde se
     enlaza (36) — son los 143 destinos rotos de plantilla de la UOC. Origen
     solo HTML con 200 (un 500 con su menu ya tiene su aviso, 57); el `noindex`
     si entra, porque el enlace roto lo pulsa una persona igual. Severidad:
     error → `warning`, redireccion → `info` (Google la sigue y pasa la senal).
   - **En los no rastreables, la plantilla se reconoce por el componente, no
     por el destino.** El selector de idioma de Lopesan es
     `onclick="location.href='/en/<esta pagina>'"`: el destino cambia en cada
     pagina y por destino salian 201 avisos por un solo componente. La firma es
     (tipo, posicion, ancla —o el atributo si no hay—). En un canario real de
     400 URLs sin render: **Lopesan, 216 paginas con alguno → 15 avisos** (13 de
     plantilla: los cuatro del selector de idioma, `warning`, 201 paginas y 201
     destinos distintos cada uno, y nueve `javascript:void(0)` del menu —
     «Hoteles», «Marcas», «Descubre»—, `info`); **cst.gov.sa, 248 → 10** (las
     ocho entradas del menu, en arabe e ingles, son `javascript:`). Severidad:
     `warning` si detras hay una URL interna, `info` si el codigo no dice a
     donde va. Los detalles cuentan los destinos que **no estan en el censo**
     (ni el rastreo ni Google llegaron a ellos por otro camino), y la cifra va a
     `None` en un censo truncado: ahi no estar no dice nada (67).
   - **`utm_en_enlace_interno` va por DESTINO**: la URL con `utm` es el hallazgo
     y el mismo banner es la misma correccion en tres paginas o en tres mil. Si
     se rastreo, el aviso va en ella y **sustituye a su `url_has_parameters`**,
     que dice lo mismo con menos (36): 78 de 1.201 en la UOC. `warning` si esa
     URL es indexable (un duplicado que el propio sitio reparte), `info` si se
     canonicaliza o no se rastreo. Lopesan 21, UOC 84, penguin 28, todos
     `info`; en Lopesan son `?utm_source=chatgpt.com` pegados en enlaces del
     blog.
   - **Ninguna lectura nueva de `links`.** Los avisos de respuesta salen de la
     temporal de aristas de `compute_link_counts` (decision 41), que ganan una
     columna `utm_hash` NULL salvo en los enlaces con `utm`; se unen con `urls`
     por clave primaria, despues de filtrar por job. Coste de los avisos: 1,8 s
     en Lopesan, 3,3 s en la UOC y 5,9 s en penguin, dentro de un
     `compute_link_counts` de 42-120 s (no medido aparte el `CASE` de la
     columna en el `INSERT`).
   - **Que pide cada uno**: los no rastreables, **re-rastrear** (el censo viejo
     no los guardo); los de respuesta y `utm`, basta **re-analizar**. La tabla
     nueva la crea `create_all` (ningun `ALTER` sobre `links` ni `urls`) y pesa
     poco: en el canario de Lopesan (repetido con las mismas 15 incidencias),
     2.051 filas y 616 kB frente a 54.109 filas y 15,3 MB de `links`, un 4%. Entra
     en el backup, el borrado en masa y el panel de espacio. Queda fuera:
     ensenarlos en la ficha de la URL, en el explorador y en el CSV.
   - **Revision (16-oct).** Lo que cambio, con su caso:
     - **Una ruta en un `onclick` no es un destino.** La primera version tomaba
       la primera cadena con forma de ruta: `addToCart('/cart/add')`,
       `ga('send','event','/promo')`, `this.src='/b.png'` o
       `$('#m').load('/ajax/modal')` salian como enlace interno que Google se
       pierde, con `warning`. En otra etiqueta, sin navegacion es un boton y no
       se guarda; en un `<a>` sin `href` (que tiene forma de enlace) se guarda
       **sin destino**, `info`. Es tambien el caso del helper con varios
       argumentos: en el canario de Lopesan, 16
       `getDetalleNoticia('/press/.../detalle-noticia/', '<slug>')` salian con
       el PREFIJO como destino —una URL que no existe— y «1 destino fuera del
       censo»; son 16 noticias distintas que no se saben sin ejecutar el
       codigo, y el aviso de esa pagina pasa de `warning` a `info`. En las 71
       paginas de control las filas no cambian (220) y pierden el destino 11:
       10 `iqitSocialPopup('//…/authenticate?provider=google')` de penguin (el
       popup del login social) y un `obre(450,200,'http://…')` de la UOC (una
       ventana emergente). El canario de Lopesan, reconstruido a partir del
       codigo guardado en la tabla (no re-rastreado): 15 incidencias antes y
       despues, y los cuatro avisos del selector de idioma siguen igual
       (navegan con `location.href=`). Lo que no se ha medido: cuantas tarjetas
       de otra etiqueta con una ruta y sin navegacion habia en un censo real —en
       el corpus, ninguna; las 52 `onclick_en_otra_etiqueta` navegan todas—.
     - **Un censo truncado se sabe al analizar.** `jobs.finish_reason` se
       escribia DESPUES del analisis, asi que en el automatico de cada rastreo
       valia NULL y «destinos fuera del censo» salia con cifra en un censo
       cortado por `max_urls` (en el canario de 400 URLs, 201 en los cuatro
       avisos del selector de idioma; con el motivo puesto, `None`). El worker
       escribe ya el motivo al pasar a `analyzing` (`_motivo_de_fin`, el mismo
       calculo que al cerrar, que es quien consume la marca de Redis).
     - **Origen 200 y HTML tambien en los no rastreables**, como en los avisos de
       respuesta (57): un 500 con su menu ya tiene su aviso, y una pagina
       re-rastreada que ahora falla no conserva los de cuando respondia (la
       tuberia ademas los borra, como `_borrar_custom` en la decision 104).
     - **Sin la tabla no se pierde el rastreo.** Si `enlaces_no_rastreables`
       aun no existe —la crea el `create_all` de la API, que en un despliegue
       arranca despues del worker, y con `up -d --build crawler` no llega a
       crearse—, el `DELETE` fallaba y el rollback se llevaba las paginas sin
       confirmar del lote: **1 de 100 paginas y 60 de 6.000 enlaces**
       guardados con la tuberia real contra Postgres, y como algo se guardaba
       el job no salia `persistence_failed` (28). Ahora la tuberia mira si la
       tabla existe (y vuelve a mirar cada minuto) y guarda estas filas en un
       SAVEPOINT: un fallo se lleva esas filas, no las paginas (100/100 y
       6.000/6.000 en las dos pruebas).
     - **Un aviso por hallazgo (36), en dos cruces.** Un enlace con `utm` a una
       URL que redirige o falla ya lo cuentan los avisos de respuesta, y la
       correccion es la misma: en la UOC, 20 de las 104 URLs con `utm` (10 3xx,
       10 4xx) dejan de llevar `utm_en_enlace_interno` (y 78 en vez de 88
       sustituyen a su `url_has_parameters`). Y la URL que redirige a una pagina
       con `solo_enlazada_por_redireccion` (63) no lleva el de redireccion: los
       dos piden apuntar los enlaces a la URL final, y el de la 63 es el mas
       fuerte. El criterio de esa pagina final vive en una funcion
       (`criterio_solo_por_redireccion`) que leen los dos (23). En la UOC, las
       **381** redirecciones de plantilla que repetian el hallazgo (714 → 333) y
       844 avisos por pagina (2.505 → 1.661); en penguin, 752 + 51 → 727 + 50;
       Lopesan no cambia (no tiene ninguna `solo_enlazada`). `redirect_chain`
       NO se excluye, a proposito: pide otra cosa —que la primera URL salte
       directa a la final, una regla del servidor— y el enlace a la URL vieja
       es un arreglo aparte, en otra pieza.
     - **El anclaje no depende del orden de rastreo.** Lo que no tiene pagina
       propia (el no rastreable de plantilla, el `utm` sin rastrear) se ancla en
       la pagina de URL menor y no en la de id menor: la comparacion identifica
       el hallazgo por (tipo, URL, clave), y otro orden de rastreo lo daba por
       resuelto y por nuevo a la vez (88). Las imagenes y los datos
       estructurados tienen el mismo problema y no se tocan aqui.
     - **En la interfaz**: nombre en castellano para los cuatro tipos
       (`ISSUE_LABEL_EXTRA`), entran en el ambito «Enlazado» del explorador y
       sus detalles se leen («de plantilla», «páginas que la enlazan», el
       reparto por tipo, un ejemplo).

   - **Esquemas que no son web (#149).** El filtro de `extract_links` distinguia
     mayusculas: `href="JavaScript:void(0)"` entraba en `links` y ADEMAS en
     `enlaces_no_rastreables`, que es justo la disyuncion que promete esta
     decision, y `Mailto:`/`TEL:` salian como enlaces externos. Ahora el atajo
     compara en minusculas y detras hay una **lista blanca** (`es_url_web`,
     `http`/`https`): `sms:`, `whatsapp:`, `intent:`, `skype:`, `file:`,
     `itms-apps:`, `ftp://` y `ws://` no son aristas. `is_internal_url` tampoco
     da por interna una URL que no es web: `ftp://x.com/f.zip` y `ws://x.com/s`
     salian internas, el BFS las pedia y acababan como una peticion FTP o un
     error de esquema. Un `Location:` (o meta refresh) a `intent://…` queda
     como destino del salto, pero no se sigue. Como `mailto:` y `tel:`, los
     esquemas nuevos **no** van a `enlaces_no_rastreables`: no son paginas.
     **No se arregla re-analizando** (los enlaces ya estan en `links`), y en un
     censo nuevo bajan los externos y, si habia `ftp://` internos, las URLs: la
     comparacion con el anterior (68) puede sacar «desaparecidas» que eran
     nuestras. Tests: `tests/test_esquemas_y_credenciales.py` y
     `tests/test_spider_cabeceras_y_saltos.py`.

<a id="d122"></a>
122. **Paginacion: lo que importa es la pagina 2+ que no cuenta y la siguiente a
   la que no se llega por un `<a>`** (ola 1.4 del plan, #150). `rel_next` y
   `rel_prev` se guardaban desde siempre y el analizador no los leia. Google
   dejo de usar `rel=next/prev` en 2019, asi que una «secuencia rota» casi no
   tiene consecuencia; lo que la tiene es la pagina 2+ en `noindex` o con el
   canonical a la 1 (deja de contar, y con ella lo que solo se enlaza desde
   ahi) y la paginacion que no es un `<a href>` (un «cargar mas»: Google no
   descubre las paginas 2+). Siete tipos, en `analysis/avisos_de_paginacion.py`:
   - **De plantilla** (64), una fila por (host, forma de la serie
     —`firma_de_ruta` de la ruta sin el marcador, mas el marcador—) con
     `series`, `paginas_afectadas` y ejemplos, anclada en la pagina de URL
     menor y con la plantilla (sin el host) en `group_key` (88):
     `paginacion_canonical_a_la_primera` (warning); `paginacion_noindex`
     (info, y **warning cuando deja indexables colgando solo de ella**, con
     `indexables_que_solo_cuelgan_de_ella` y ejemplos: el arreglo es ese
     `noindex` de plantilla, no enlazar ficha a ficha); 
     `solo_enlazada_desde_paginacion_profunda` (info: indexables a las que
     solo enlazan paginas 2+ que cuentan); `paginacion_sin_enlace` (warning:
     la pagina anuncia la siguiente —`rel=next` o un boton de la 106— y no
     tiene NINGUN `<a>` a ella); `paginacion_siguiente_rota` (warning, `rel` a
     un 4xx/5xx rastreado, sin el 429 ni los muros de la 103) y
     `paginacion_rel_incorrecto` (info: `rel` a un 3xx, fuera de la serie, o
     distinto del `<a>` que la paginacion SI pinta). Una pagina 2+ con
     `noindex` y canonical a la 1 va solo al primero (36). El `noindex_page`
     de cada pagina no se toca: es el dato de la pagina, y esto es el patron.
   - **Por pagina**: `solo_enlazada_desde_paginacion_excluida`, la indexable a
     la que solo llegan enlaces **seguibles** desde paginas de la paginacion
     que no cuentan (2+ en `noindex` o canonicalizadas a la 1, y la pagina 1
     en `noindex` de una serie conocida). Es el hallazgo de la decision 38.
     Sustituye a `orphan_page`, `sitemap_orphan` y `not_reachable_from_home`
     (la paginacion va antes de la profundidad de clic) y cede ante
     `canonical_sin_enlazar` (101). Es `info` cuando la serie de la que cuelga
     ya lleva el `warning`, y `warning` si no (una serie entera en `noindex`
     a proposito: lo que hay que hacer es enlazar la pagina desde otra parte).
     Un `nofollow` desde fuera no es un camino (90) y se dice en
     `otros_entrantes`; una no indexable del sitemap no lo lleva (100). Una
     pagina 2+ excluida que solo cuelga de su propia serie se cuenta en ella
     (`solo_enlazadas_por_la_serie`) y deja de ser `orphan_page`: «nadie la
     enlaza» era falso. Lo que no cumple nada de eso conserva su aviso de
     siempre: ninguna pagina pierde el suyo sin otro (medido, abajo).
     La canonicalizada SI cuenta como entrante y en el PageRank: la 38 se
     mantiene, las columnas nuevas no tocan `origen_cuenta`.
   - **Deteccion** (`shared/paginacion.py`, funcion pura): marcadores fuertes
     (`?page=`, `?pagina=`, `?pg=`, `?paged=`, el `?e-page-<id>=` de
     Elementor, `/page/N`, `/pagina/N`, `/pag/N`) y, con evidencia, los
     debiles (`?p=N`, `/N` final que no va detras de otro numero —`/2023/04/`
     es un archivo por mes—). Evidencia es que algo anuncie con `rel` la
     pagina 2 de esa serie, o que esten la 2 **y** la 3; que las paginas
     lleven un `rel` cualquiera no basta, porque las entradas de WordPress
     (`/?p=38`) lo llevan entre ellas. La serie lleva el resto de parametros
     (`?orden=precio&page=2` no es de la serie sin orden) y no distingue la
     barra final. Una pagina sin marcador con `rel=prev` es 2+ y su 1 es donde
     acaba la cadena; una cadena de solo-`rel` cuyas paginas 2+ enlaza el
     resto del sitio (mas de la mitad) es una cadena de entradas
     (`adjacent_posts_rel_link`) y se descarta. Una variante canonicalizada a
     OTRA serie (`?x=ooo` canonicalizada a la limpia, «ver todo») no es del
     listado. Una paginacion que empieza en 0 (Drupal) se lee con una pagina
     de retraso.
   - **Sin ninguna lectura nueva de `links`** (#52 sigue abierta; 37, 41).
     `analizar_paginacion` lee `urls` + `html_meta` del job (como
     `analyze_indexability`) y los botones de `enlaces_no_rastreables` por su
     `job_id`, va justo antes de `compute_link_counts` y no emite nada; deja
     en memoria las paginas excluidas, las 2+ que cuentan y lo que cada pagina
     anuncia como siguiente, y `compute_link_counts` las sube a una temporal
     pequena (`paginacion_tmp`, por clave primaria y con `ANALYZE`) que se une
     en la MISMA lectura de `links` que materializa `aristas_tmp`, con tres
     columnas: `origen_pag_excluida`, `origen_pag_profunda` y
     `rel_destino_url` (la URL solo de los destinos SIN rastrear de las
     paginas que anuncian una siguiente: el spider no sigue el `rel`). Las
     consultas de lo que cuelga entran por esa temporal y el indice de origen,
     y el EXISTS que busca «un camino de fuera» va sobre los destinos ya
     agrupados. Lo comprueba `test_la_paginacion_no_anade_lecturas_de_links`
     contando las sentencias que nombran `links` con el paso y sin el
     (`run_all` entero en Postgres): las mismas, y solo cambia la de las
     aristas. El Parquet (95) no necesita nada: `run_all` ya se niega a
     analizar un censo archivado.
   - **Medido** en censos reales con la transaccion deshecha (sin `ALTER
     TABLE`: las columnas que le faltan a la base local salen de vistas
     temporales), pasada A = sin el paso (master) y B = con el:

     | censo | `orphan_page` | `sitemap_orphan` | solo desde paginacion excluida | avisos de plantilla (filas / paginas) | coste del paso |
     |---|---|---|---|---|---|
     | blogs.uoc.edu | 985 → **130** | 379 → 374 | 5 (warning) | noindex 17 / 857, profunda 53 / 1.124, rel incorrecto 29 / 140, sin enlace 1 / 1 | 6 s (`compute_link_counts` 125 → 131 s) |
     | Druni | 4.848 → **2.079** | 9.224 → **3.612** | **5.612** (57 warning, 5.555 info) | **noindex 218 / 5.447 (192 warning)**, profunda 16 / 678 | 95 s (741 → 826 s, +11 %) |
     | progym | = | = | 0 | profunda 7 / 930, rel incorrecto 1 / 3 | 2 s |
     | Lopesan | = | = | 0 | rel incorrecto 3 / 45 | < 1 s |

     `not_reachable_from_home` no cambia en ninguno, y en los cuatro la
     cuenta de paginas que pierden `orphan_page`/`sitemap_orphan`/
     `not_reachable_from_home` sin recibir otro aviso es **0**. Druni es el
     hallazgo: su paginacion `?p=N` de categoria lleva `noindex` con la 1
     indexable, y 5.612 productos indexables (casi todos en el sitemap) solo
     se enlazan desde ahi; antes salian como huerfanos, lo que mandaba a
     enlazarlos uno a uno en vez de quitar un `noindex` de 192 plantillas.
     **Las «168» de la decision 38**, con su consulta repetida hoy, son 169
     URLs: 108 son paginas 2+ en `noindex` de archivos por mes enteros en
     `noindex` (`/2025/10/page/2/`), que ahora se cuentan en su serie; 5 salen
     como `solo_enlazada_desde_paginacion_excluida` (las enlaza solo una
     busqueda paginada en noindex); 54 son el subsitio `catedra-randstad`
     entero en noindex, que no es paginacion, y siguen como `orphan_page`; 1
     queda en `sitemap_orphan` y 1 sin aviso de enlazado (tampoco lo tenia).
     Coste: las cifras de `compute_link_counts` son de pasadas en
     transacciones separadas (repetir la B en la misma transaccion que la A
     la medía sobre la tabla `urls` hinchada por la A deshecha: 426 s en
     blogs.uoc.edu, todo en la profundidad de clic). Por debajo del 2x que el
     plan fija para estos checks.
   - **Lo que no se afirma**: `paginacion_sin_enlace` dice «esta pagina no
     enlaza con `<a>` a su siguiente, con ninguna URL», no que la siguiente
     sea inalcanzable (67, 70). Un `rel` a una URL sin rastrear no es «roto».
     Huecos en la numeracion, bucles de `rel` y la pagina 1 duplicada
     (`/blog/` y `/blog/page/1/`, ya lo cubren el duplicado y el canonical) no
     se avisan.
   - **Re-analizar cambia las cifras** de los censos entregados: salen los
     siete tipos y miles de `orphan_page`/`sitemap_orphan` pasan a
     `solo_enlazada_desde_paginacion_excluida` o se cuentan en su serie.
     `analisis_version` lo deja registrado (72) y la comparacion por
     incidencias se niega a comparar censos analizados con codigo distinto
     (91). Entre dos censos con el mismo codigo, los seis de plantilla se
     casan por (tipo, host, `group_key`) y no por la URL del ancla, que se
     mueve en cuanto se rastrea otra pagina de la serie
     (`shared.comparacion.ANCLADOS_POR_GRUPO`); si el ancla vieja no se volvio
     a visitar sigue siendo «no se sabe» (89).
   - **En la interfaz**: ficha y nombre de los siete (`incidencias.js`, sin
     decir que Google usa `rel=next`) y nombre de cada clave de los detalles
     (un test ejecuta los escenarios y lo comprueba), ambito «Paginación» en
     el explorador con las columnas `rel=next` y `rel=prev` (tambien en el
     selector y en el CSV de la vista), y
     `solo_enlazada_desde_paginacion_excluida` tambien en «Enlazado». La nota
     de Enlazado de `/insights` cuenta paginas distintas con `orphan_page`,
     `sitemap_orphan` o `solo_enlazada_desde_paginacion_excluida`: contando
     solo la primera, **subia** en el censo donde se encuentra el problema.
     `test_catalogo_incidencias.py` lee tambien `avisos_de_enlaces.py` y este
     modulo: sus tipos se le escapaban.
   - **Revision** (2026-10-18): habia una segunda implementacion sin mergear
     (rama `claude/ola1-paginacion`, modulo propio, medida en seis censos). Se
     tomo esta como base y se porto el comportamiento, cada punto con un test
     que falla contra el codigo de la primera version de esta PR: cadenas de
     entradas de WordPress; boton «cargar mas» con y sin pagina 2 (y la 106
     sin repetirlo); el `warning` en la serie; la pagina 2+ contada en su
     serie; el `nofollow` de fuera y `not_reachable_from_home`; la
     comparacion por `group_key`; la nota de `/insights`; las profundas y el
     `rel` incorrecto; Elementor; las variantes canonicalizadas a otra serie;
     «sin enlace» solo sin NINGUN `<a>` a la siguiente. Midiendo salieron
     cuatro mas, tambien con test: `?p=N` sin anuncio de su pagina 2 (dos
     entradas de unescochair salian «canonicalizadas a la 1»), los archivos
     por mes leidos como `/N`, la pagina 1 `noindex` como origen que no cuenta
     y el `/page/6/?page=0` que ES la pagina 6. Medida la primera version en
     los mismos censos: Lopesan sacaba **72 paginas en
     `paginacion_sin_enlace`** que si enlazan la siguiente (con
     `?e-page-<id>=2`), progym 3 y blogs.uoc.edu **1.019**; y en
     blogs.uoc.edu `orphan_page` se quedaba en 985. Ya lo resolvia (quedan
     como test): el muro en el destino del `rel`, la `noindex` de fuera que no
     es camino y la no indexable del sitemap.

<a id="d123"></a>
123. **Los enlaces externos se comprueban con una sonda, no con
   `follow_external`, y lo que no se puede afirmar no se avisa** (#150, punto 1).
   Los enlaces a otros sitios se guardaban en `links` y no se pedian nunca: un
   enlace roto a un tercero, o a un dominio caducado, no salia en ningun informe.
   La casilla «Verificar recursos ext.» (`resource_types.check_external_resources`)
   estaba en el formulario y no la leia ningun fichero de `crawler/`.
   - **No sobre `follow_external`.** Con `follow_external=true` los externos ya
     se piden, pero entran en el CENSO: gastan `max_urls`, sus 404 salen como
     `4xx_error` y `/insights` los cuenta como «paginas con errores HTTP» del
     sitio, y se siguen los enlaces de las paginas externas (la condicion de
     `parse` no mira si el origen es interno). La comprobacion es una **sonda**
     con el patron de la decision 105 (`crawler/seo_crawler/sonda_respuestas.py`,
     `SondaRespuestasMixin`): callback y errback propios (nunca `parse` ni
     `handle_error`, que cuentan), sin `PageItem`, `dont_filter`, prioridad
     -100 (detras de las paginas), su propio tope y **sin `_anotar_salud`**: el
     403 de Cloudflare delante de LinkedIn no puede parar la auditoria (103).
     La cola final de sondas no tiene paginas y el latido del spider va por
     paginas: la sonda late por tiempo (cada 60 s) y mira la cancelacion, o el
     vigilante del worker daria el rastreo por estancado.
   - **Clave nueva, apagada:** `crawl_behavior.comprobar_enlaces_externos`, con
     `enlaces_externos_max` (5.000 URLs distintas) y `sondas_por_host` (50 por
     host externo, enlaces y recursos juntos). No se reutiliza
     `check_external_resources`: habla de recursos, y es la de la 124. Las dos
     casillas dicen en el formulario que hacen. **Sin medir el coste en censos
     reales**: la guia pedia contar destinos externos distintos en tres censos
     antes de fijar los topes, y esta rama no tiene acceso a produccion ni a
     una base con censos; los topes son los propuestos y se revisan con el
     canario de abajo.
   - **`HEAD`, y `GET` cuando el `HEAD` no es de fiar.** Ante 400, 401, 403,
     404, 405, 406, 409, 410, 429, 501 o 5xx se repite una vez con `GET` y
     `Range: bytes=0-1023` (la lista de FreeCrawl mas el 404 y el 410: antes de
     afirmar «roto» con un `HEAD` se confirma, porque hay CMS que solo enrutan
     GET; cuesta solo lo que sale roto). Un servidor que corta la conexion a un
     `HEAD` tambien se repite con `GET`. Muchos ignoran el `Range`: el `GET` va
     con `MAXFILESIZE` de curl a 10 MB, y lo que anuncia mas existe. Las
     redirecciones las sigue la sonda (hasta 10 saltos, como Google), con el
     codigo de la URL enlazada y el final. `dont_retry`: el `RetryMiddleware`
     repetia tres veces el 429 de un tercero.
   - **Nada del job a un tercero.** Va encima de la 114: cabeceras, cookies y
     Basic solo a los hosts de las semillas. Comprobado con el arnes, mirando
     lo que LLEGA al servidor del tercero (sin `Authorization`, sin la cabecera
     propia, sin la cookie). La sonda no anade nada suyo. Y no pide a la red
     interna: un enlace a `10.x`, `169.254.169.254`, `localhost` o `.local` no
     se pide (`shared/red.py`, SSRF; solo por la forma del host, sin cubrir un
     nombre publico que resuelva a una IP privada). Desde la revision de la
     154 tambien un host de una sola etiqueta (los servicios de Docker) y los
     IPv4 en forma numerica, hexadecimal, octal o abreviada que curl convierte.
   - **El robots.txt del tercero, siempre.** El `robots_mode` del job es lo que
     el cliente decide sobre SU sitio. Cada origen externo pide su robots.txt
     una vez (con `dont_obey_robotstxt` en la sonda, para que el middleware de
     Scrapy no lo pida otra vez) y lo prohibido queda `prohibido_por_robots`,
     sin pedirlo: «no evaluado», no «bien».
   - **Un dominio muerto cuesta una peticion.** El robots.txt es tambien la
     prueba de vida del origen. curl dice «Could not resolve host» igual para
     un NXDOMAIN que para un resolver que no contesta: se pregunta a
     `getaddrinfo` en un hilo y solo `EAI_NONAME`/`EAI_NODATA` es
     `dominio_inexistente`; entonces el origen se da por caido y sus URLs se
     anotan sin pedirlas (`host_caido`). Si el robots falla por la red se prueba
     UNA URL, y un segundo fallo de conexion lo da por caido. Comprobado con el
     arnes: `x.invalid` no recibe ninguna peticion de URL, y de un host que
     rechaza conexiones se pide una y la otra no.
   - **Veredictos** (`shared/sondas.py`, compartido por spider, analizador y
     API): `ok`, `roto` (404, 410 y los 4xx que no son de permisos),
     `redirige_a_error`, `error_servidor` (5xx), `no_verificable` (401, 403,
     406, 407, 429, 451, 999 de LinkedIn, un desafio que
     `clasificar_respuesta` reconoce aunque venga con 200 o 503),
     `prohibido_por_robots`, `dominio_inexistente`, `sin_conexion`,
     `tiempo_agotado`, `tls_invalido`. **Solo `roto` y `redirige_a_error`
     avisan.** Un 5xx puede ser una caida de un minuto y un 403/429 es lo que
     responde un WAF a un rastreador: afirmarlo seria lo que prohiben la 67, la
     89 y la 91. Salen como dato en la tabla.
   - **Tabla nueva `respuestas_sondeadas`**, una fila por URL distinta del job
     (unica por `job_id, url_hash`, el mismo hash que `links.to_url_hash`), con
     `job_id` para filtrar y borrar sin unir con nada (37). Va en
     `TABLAS_POR_JOB`, en `borrar_job` (96), en el backup (sin url FK: solo se
     remapea el job) y la crea create_all (`migrar_respuestas_sondeadas` en
     `init_db.py` asegura el indice). Sin fila = no comprobada (sondas
     apagadas, tope, tope del host, red interna), que no es «responde bien»
     (104). La tuberia la escribe por lotes con un upsert en un SAVEPOINT
     (nada de un `UPDATE` por sonda como el soft 404), y mira si la tabla
     existe como con la 106. Al reanudar, lo ya sondeado no se repite y gasta
     su presupuesto.
   - **Avisos** (`analysis/avisos_de_sondas.py`): `enlace_externo_roto`
     (warning) **uno por destino, no por aparicion ni por pagina**, con
     `paginas_afectadas`, `enlaces` (`SUM(veces)`, 94), ejemplos de origen y
     `misma_casa` (el propio blog con `crawl_subdomains=false` pesa mas).
     `dominio_externo_muerto` (warning, `group_key` del host) uno por host: hace
     falta una fila con `host_caido` y un fallo de DNS o de conexion, y ninguna
     respuesta HTTP en todo el host (un 404 dice que el dominio existe). Sus URLs
     no llevan ademas `enlace_externo_roto` (36). Lo que **NO** se copia de
     FreeCrawl (`project-db.ts:10055-10085`): alli un host es muerto con un 80%
     de `status >= 400`, asi que un 403/429 de WAF lo mata, y al exigir
     `status_code IS NOT NULL` deja fuera justo el fallo de DNS; y lo emite por
     pagina de origen. Si «no existe» mas de la mitad de los hosts externos (y
     al menos 10), lo que fallo es el resolver del rastreo: no se afirma
     ninguno. Origen: HTML que responde 200 (57); un `noindex` y un `nofollow`
     SI cuentan, el enlace lo pulsa una persona. Una URL que esta en el censo
     (con `follow_external`) ya tiene su `4xx_error` y no se repite. Sin pagina
     propia, se anclan en la pagina de URL menor (88). Los enlaces salen de la
     temporal de aristas (columna `externo_hash`, solo si el censo tiene
     sondas), sin otra lectura de `links` (37, 41).
   - **El resumen** va a `jobs.sondas_resumen` (lo escribe el analisis):
     destinos externos distintos, comprobados, rotos y muertos, y el reparto
     por veredicto. Con «comprobados < distintos», que no haya aviso no dice
     que no haya enlaces rotos.
   - **Limitaciones conocidas.** Un dominio caducado y aparcado responde 200
     con una pagina de parking: por codigo no se detecta. El UA de las sondas
     es el del job (un preset de Googlebot desde una IP que no es de Google
     saca mas `no_verificable`). Un `HEAD` va sin cuerpo, asi que un desafio
     de WAF servido con 200 solo se ve si lo dicen las cabeceras.
   - **Al re-analizar** no cambia nada de lo ya entregado: sin filas en
     `respuestas_sondeadas` no se emite nada nuevo. Los censos rastreados con
     la clave encendida llevan tipos nuevos y `analisis_version` distinto (72,
     91). **Pendiente de verificar en un canario** (13): `lanzar_job.py
     <cliente> --canary --set crawl_behavior.comprobar_enlaces_externos=true`
     en dos sitios con muchos externos, revisar a mano 20 `roto` y 10
     `dominio_externo_muerto` con `curl` desde fuera y anotar aqui el numero de
     sondas, el tiempo anadido y el porcentaje de `no_verificable`.
   - **Revision (2026-10-18)**, cuatro fallos reproducidos ejecutando el mixin
     y el arnes (`tests/test_sondas_revision.py` y
     `test_el_crawl_delay_del_tercero_espacia_sus_sondas`; los cuatro fallan
     contra la version anterior):
     - **SSRF por redireccion.** El filtro de `shared/red.py` miraba la URL
       ENLAZADA y no los saltos: un tercero que responde
       `302 Location: http://169.254.169.254/...` hacia que el rastreo pidiera
       los metadatos de la nube del VPS. Ahora cada salto pasa por el filtro y
       uno a la red interna queda `no_verificable` («redirige a la red
       interna»), sin pedirlo. Lo mismo con el robots.txt del tercero, que lo
       redirigia el `RedirectMiddleware` de Scrapy a donde dijera el tercero:
       ahora lleva `dont_redirect` y sus saltos (cinco, como Google) los sigue
       la sonda con el mismo filtro; uno a la red interna se trata como «sin
       robots».
     - **Un resolver que no contesta no es un dominio muerto.** Un fallo de
       DNS que `getaddrinfo` da por temporal (`EAI_AGAIN`) salia
       `sin_conexion`, que esta en `MUERTOS`, y con dos fallos el host acababa
       en `dominio_externo_muerto`: un fallo del rastreo afirmado como un
       hallazgo del tercero, y fuera del alcance del guardia del resolver, que
       solo mira los `dominio_inexistente`. Ahora es `no_verificable`; el
       origen se sigue dejando de pedir tras dos fallos (se cuentan por tipo de
       fallo de red, no por veredicto), pero no se afirma nada.
     - **El `Crawl-delay` del tercero no se aplicaba.** Su robots.txt se
       respetaba en los `Disallow` y no en el retraso, porque la sonda lo pide
       con `dont_obey_robotstxt` y el `RobotsCrawlDelayMiddleware` nunca lo
       veia: con `Crawl-delay: 1`, cuatro sondas al mismo host salian en el
       mismo milisegundo (medido en el arnes). Ahora se pone como retraso del
       slot del host, con el tope `ROBOTS_CRAWL_DELAY_MAX` y reaplicado tras
       cada respuesta (AUTOTHROTTLE lo bajaba), como la decision 81. Siempre,
       sea cual sea el `robots_mode`: el robots del tercero no es decision del
       cliente. Peor caso: 50 sondas x 10 s = 8 min de cola por host, en
       paralelo entre hosts.
     - **Un `max_urls` deja sondas en la cola sin decirlo.** Van detras de las
       paginas (prioridad -100) y el cierre por `max_urls` las descarta: en un
       arnes de 60 paginas con `max_urls=15`, 4 de 15 se quedaron sin fila, y
       en un censo con frontera grande seran casi todas. El log decia
       «15 pedidas». Ahora, al cerrar, un WARNING dice cuantas quedaron sin
       respuesta y por que; `sondas_resumen` ya lo deja ver
       (`comprobados < distintos`). No se cambia la prioridad: adelantarlas
       quitaria concurrencia a las paginas con render (8 peticiones en total).
     Sin reproducir, y por eso sin tocar: que una IP del rastreo bloqueada por
     un cortafuegos que RECHAZA conexiones (no que las descarta) saldria como
     `dominio_externo_muerto` —desde un solo punto de la red no se distingue
     de un servidor apagado—; y el coste en Postgres, medido con datos
     sinteticos (10 M aristas, 30% externas, 5.000 sondas): 5,2 s el recuento
     de destinos distintos y ~1 s cada lote de 2.000, sin indice (crearlo
     costaria 7,7 s y ahorraria medio segundo por lote).

<a id="d147"></a>
147. **Una página que el sitio enlaza casi siempre con «leer más» no recibe
   contexto de su propio sitio** (Ola 8, «anclas: agregado por destino y
   lista determinista de anclas genéricas por idioma»). El texto del enlace es
   una de las pistas con las que Google entiende el destino (decisión 61), y un
   listado de tarjetas con «Leer más» o un widget con «See more» se la quita a
   todas las páginas que enlaza. `ancla_generica` (info), **por destino**: al
   menos 3 páginas de origen distintas con ancla genérica y que sean la mitad o
   más de las que la enlazan; solo destinos HTML 200 indexables y orígenes que
   pasan la señal (200, no noindex: decisiones 38 y 57). En los detalles,
   cuántas y qué anclas.
   - La lista (`ANCLAS_GENERICAS`) es determinista: frases de «leer más»,
     «aquí», «ver más», «más información», «read more», «see more», «mehr»,
     «llegir més», «en savoir plus»... en los idiomas de los clientes, con las
     variantes de puntuación habituales al final (« »», «…», «→»). Se compara
     en minúsculas y sin espacios a los lados. No es un clasificador: lo que no
     está en la lista no avisa.
   - **Sin otra lectura de `links`** (decisión 41): una columna más en la
     temporal de aristas de `compute_link_counts`, solo con valor cuando el
     ancla es genérica.
   Medido con una consulta equivalente sobre producción en solo lectura:
   **Lopesan, 126 destinos**: las entradas del blog en inglés, enlazadas desde
   563 páginas siempre con «see more» (el widget de destacadas), 4,5 s;
   **Saunier, 21** («más información», «ver más» en las fichas de producto).

<a id="d150"></a>
150. **Un censo completado archiva sus enlaces al acabar su analisis, y
   re-analizarlo los restaura y los vuelve a archivar** (fase 2 del espacio de
   `links`). La 95 solo archivaba censos **superados** y con mas de
   `ARCHIVAR_DIAS_MINIMOS` (14) dias, y nunca el ultimo de una programacion:
   `links` seguia guardando, de cada sitio, al menos un censo entero y todo lo
   de las dos ultimas semanas. Con un sitio como Druni (9 GB por censo
   agrupado) eso no cabe en el VPS. Las tres condiciones existian por una sola
   razon: un censo archivado no se podia re-analizar, y la referencia de la
   comparacion se re-analiza cada vez que cambia el analizador (93), que con
   despliegues diarios es casi siempre.
   - **Ahora `run_analysis` restaura, analiza y vuelve a archivar**, todo bajo
     el candado del job (el mismo que toma `archivar`, que recibe
     `candado_tomado=True` para no pedirlo dos veces). Quitado ese freno, un
     censo completado se archiva en el cierre del worker, justo despues de la
     comparacion, sin mirar alcance, edad ni programacion. `links` queda para
     los rastreos en curso y para lo que se esta re-analizando.
   - **La restauracion va por COPY** (formato de texto, NULL como `\N`, barra,
     tabulador y saltos de linea escapados). Con el INSERT de la 95 restaurar
     www.uoc.edu eran 253 s; el re-analisis de la referencia es ahora el caso
     normal y no puede costar mas que el propio analisis.
   - **Si el re-analisis revienta**, se vuelve a archivar igual (en el
     `finally`); si lo que falla es el archivado, los enlaces se quedan en
     `links`, que es un estado valido, y el cierre del siguiente rastreo lo
     archiva (best-effort con el `try` entero, decision 74).
   - **Un rastreo fallido, cancelado o estancado sigue la regla de la 95**: se
     puede reanudar y la reanudacion lee la frontera de `links`
     (`_iter_frontier`), asi que solo se archiva con un completado mas nuevo
     del mismo host y pasados `ARCHIVAR_DIAS_MINIMOS`.
   - **La comparacion ya no se calla ante una referencia archivada**: antes
     devolvia el motivo y los dos comandos para arreglarlo a mano; ahora pide
     el re-analisis como con cualquier otra.
   - **`run_all` sigue negandose** sobre un censo archivado: quien lo llama
     directamente (tests, scripts) no pasa por la restauracion.
   - **Lo que NO hace**: que los enlaces no entren nunca en Postgres. El
     pipeline los sigue escribiendo durante el rastreo, porque la reanudacion
     y el borrado por pagina al re-rastrear los leen de ahi, y no hace falta
     para el disco: Postgres reutiliza el espacio que deja cada archivado. El
     fichero de `links` solo encoge con `VACUUM FULL links` en una ventana sin
     rastreos, que hay que hacer una vez tras desplegar esto.
   - Lo que cuesta: la ficha de una URL lee sus salientes del Parquet, que
     esta ordenado por destino, asi que filtrar por `from_url_id` recorre la
     columna entera (pyarrow lee solo esa columna); y `compactar_enlaces.py` y
     `check_content_quality.py` siguen negandose sobre un censo archivado.
   - **Medido** (2026-10-21, BD local con `links` de 187 M filas y 49 GB, censo
     de penguin `ad5c837b`, 10.352.878 enlaces, 30.287 URLs): analizar con los
     enlaces en `links`, 5:43; restaurar por COPY, 3:27 (unas 50.000 filas/s,
     con los tres indices); volver a archivar, 2:20 a 2:55 (87 MB). El
     re-analisis completo de un censo archivado, 11:11, el doble que sin
     archivar. Entrantes, entrantes unicos, salientes, profundidad y PageRank
     de las 30.287 URLs **identicos** (mismo md5) y el Parquet final del mismo
     tamano que el primero. A esa velocidad un censo como Druni (139 M) tardaria
     unos 45 min en volver: solo pasa cuando cambia el analizador y su censo es
     la referencia de una comparacion (93), y lo paga el worker, no el usuario.

<a id="d152"></a>
152. **Las aristas del PageRank se agregan con claves enteras y una fila por
   (origen, destino, ancla)** (#52) — tras la 41 y el DISTINCT de d527af7, el
   coste de `_aristas_de_enlaces` seguia en ordenar texto: cada fila de
   `pr_lk_tmp` arrastraba el hash de la URL de destino, el ancla y la seccion
   (~140 bytes) y la collation `en_US` los comparaba letra a letra. En
   penguin-libros `pr_rep_tmp` volcaba **710 MB a disco** para 5,1 M de filas
   con el `work_mem` de 4 MB de produccion.
   - **Claves enteras**: el destino se resuelve a su `urls.id` al montar
     `pr_lk_tmp`, la seccion es un entero (`dense_rank` en `pr_fuentes_tmp`) y el
     ancla su `md5` como `uuid` (16 bytes; una colision es 2^-128, a diferencia
     de `hashtext`, de 32 bits).
   - **Una fila por (origen, destino, ancla) con el MAYOR peso de posicion**. Es
     exacto, no una aproximacion: la repeticion cuenta origenes distintos, que
     sobre esa tabla es un `COUNT(*)` sin `DISTINCT`, y el peso de la arista es
     `max(min(posicion, techo))`, que es `min(max(posicion), techo)` porque el
     techo no depende de la posicion. `pagerank.sql_techo_por_repeticion` sale
     de las mismas constantes que `peso_arista`.
   - **Solo destinos internos que estan en el censo**: los demas no dan arista,
     y su repeticion es por destino, asi que quitarlos antes no mueve la de
     ningun otro.
   - **Medido** (2026-10-23, copia local de penguin-libros `d3fda8a5`, 87.570
     URLs y 7,8 M de enlaces, `work_mem` 4 MB): `_aristas_de_enlaces`
     **25,5 s -> 12,3 s** y `compute_pagerank` 50,9 s -> 38,1 s. En un censo
     sintetico de cuatro copias de penguin-libros con hosts distintos (31,2 M de
     enlaces, la escala de Druni): **152,7 s -> 67,3 s**. Las aristas
     (4.551.434 y 18.205.736) y el `pagerank_raw` y `pagerank_score` de las
     87.570 URLs salen **identicos byte a byte**.
   - El cruce con `urls` y el md5 van ahora sobre cada fila de `links`, antes de
     agrupar. Con los duplicados de antes de la decision 94 (Druni: 3,4 filas por
     cada distinta) eso pesaria; desde la 94 el spider guarda una fila por
     aparicion identica y `compactar_enlaces.py` dejo asi los censos viejos, y en
     penguin-libros cada fila es ya un par origen-destino distinto.
   - Lo que queda caro es montar `pr_lk_tmp` (44 s de los 67): el md5 por fila y
     la agregacion, que con 4 MB se parte en 256 lotes a disco. Ya no es el
     cuello de botella del analisis.
   - `test_el_mismo_enlace_en_dos_posiciones_es_un_origen` fija que la tabla no
     se vuelva a partir por posicion: con la pagina contada dos veces, el techo
     baja de 0,333 a 0,3 y el test falla (comprobado con el mutante).
