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
