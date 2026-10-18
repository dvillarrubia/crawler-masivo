# Decisiones: extracción de contenido, titulares, imágenes y anclas

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d6"></a>
6. **Headings dedup** — `extract_headings` skips headings inside `<template>`, `<noscript>`, `<svg>` to avoid SSR/framework duplicates.

<a id="d9"></a>
9. **Landmark-aware content extraction** — `_strip_boilerplate_html` removes
   `<header>`/`<footer>` only when they are the page banner/contentinfo (not
   nested in `main`/`article`/`section`, or explicit ARIA role). A hero
   `<header>` inside `<main>` (Astro/Next/Nuxt pattern) is content; stripping
   it blindly reduced whole pages to two words. Node removal keeps tail text.
   The trafilatura-vs-fallback decision compares against the words in the
   stripped main container (not body `word_count`), threshold 0.7; the
   fallback flattens block by block and collapses marquee/animation repeats.

<a id="d10"></a>
10. **Hero outside `<main>` is still content** — some landings put the `<h1>`
   and the main claim in a `<section>` that is a *sibling* of `<main>` (often
   wrapped in `<figure><figcaption>`). trafilatura and the fallback both stay
   inside the chosen container, so that block used to vanish from
   `page_content` — the page's headline and commercial promise, on exactly the
   most important pages. `_hero_outside_container` recovers it when the `<h1>`
   is outside the container, prepending only the lines not already present. A
   `<figcaption>` containing headings is unwrapped to a `<div>`: it is a hero,
   not a photo caption.

<a id="d10b"></a>
10b. **El hero tambien se pierde estando DENTRO del contenedor** — el caso
   contrario al anterior y mas comun: `<main><article><div class=hero><h1>`.
   trafilatura lo descarta por su pinta de cabecera mientras conserva el resto
   del articulo, asi que la comprobacion de share no salta (0.75 > 0.7) y el
   titular desaparece sin aviso. `_hero_dentro_perdido` sube desde el `<h1>`
   por los ancestros mientras el bloque siga siendo una fraccion pequena del
   contenedor (30% / 60 palabras), para arrastrar la categoria o el subtitulo
   del hero pero nunca el articulo. El titular se da por presente solo si
   aparece como LINEA propia: como subcadena da falsos positivos (la marca
   reaparece a media frase). Medido: 3/20 paginas de control de otros clientes
   cambian, y en las tres el cambio es recuperar el h1 que faltaba.

<a id="d44"></a>
44. **El texto que no se pinta no es contenido, y el menú no es contenido
   de la página** — tres medidas que decidían cosas distintas estaban mal:
   `word_count` contaba con `split()`, así que una página japonesa entera daba
   **1 palabra** y todo sitio CJK salía como thin content; `text_ratio` medía
   sobre `extract_visible_text`, que incluía los nodos de solo espacios, así
   que medía la indentación de la plantilla (31% medido en la home de un
   cliente, 6,7% real); y el recuento era del `<body>` entero, de modo que el
   megamenú y el pie —boilerplate para Google— tapaban las páginas escasas:
   997 avisos de 29.808 páginas midiendo el body contra **10.433** midiendo el
   contenido propio, 4.736 de ellas indexables. Ahora un solo recorrido del
   árbol (`_lineas_visibles`) alimenta `word_count`, `text_ratio` y
   `page_content`: fuera lo que el navegador no pinta (`hidden`,
   `display:none` en línea, `title` de un svg, respaldo de un `iframe`),
   `hidden="until-found"` sí es contenido, y las fronteras de bloque evitan que
   `Zapa<span>tillas` cuente como dos palabras. Nueva columna
   `urls.content_word_count`. Medido en 51 páginas de control de tres clientes:
   10 cambian su contenido guardado y en las 10 lo único que sale es interfaz
   oculta (modal de login, menú de cuenta, promo de la app). `low_text_ratio` y
   `very_low_text_ratio` dejan de emitirse: el ratio texto/HTML no es una señal
   de Google y, bien medido, salta en 21 de 30 páginas de control.

<a id="d45"></a>
45. **El limpiador de plantilla no puede borrar la página, y deduplicar no
   puede desalinear una tabla** — tres fallos del extractor de contenido, los
   tres reproducidos: (a) las listas de nombres de plantilla casaban por
   **subcadena sobre cualquier elemento, `body` incluido**, así que
   `<body class="cookie-bar-active">` dejaba la página en **0 palabras**, y
   `cookie-policy` o `privacy-notice-content` borraban el cuerpo de las propias
   páginas legales —las que tienen que estar indexadas tal cual—; (b) `<form>`
   se quitaba siempre, y en ASP.NET WebForms la página entera va dentro de
   `<form id="aspnetForm">`: 0 palabras otra vez; (c) `_dedupe_lines` miraba una
   ventana de 4 líneas sin saber de dónde venían, así que de 8 celdas `Sí` de
   una tabla comparativa quedaba 1 y **las filas salían desplazadas**: el
   informe decía lo contrario que la página. Ahora: `_TAGS_INTOCABLES`
   (`html`, `body`, `main`, `article`) no se borran nunca; un bloque cuyo nombre
   casa pero que se lleva más del 40% de las palabras de la página es la página,
   no plantilla (`_MAX_SHARE_PLANTILLA`); un `<aside>` o un
   `role="complementary"` DENTRO de `main`/`article`/`section` es contenido —la
   misma regla de landmark de la decisión 9— y se desenvuelve a `<div>` porque
   trafilatura tira todo `<aside>` por su cuenta; y una repetición solo se
   colapsa si no viene de una celda o un item (`_TAGS_DATO`) y si entre las dos
   apariciones hay 2 líneas distintas como mucho, que es la forma de una
   marquesina (`A A A`) o de una animación (`A B A B`), no la de una tabla.
   Además lo que no se pinta se quita también del HTML que lee trafilatura, no
   solo al contar: `d-none` (Bootstrap), `hidden` (Tailwind), `is-hidden`,
   `hide` e `invisible` se casan por token completo, de modo que `d-md-none`
   —que oculta solo a partir de cierto ancho— sigue siendo contenido. Medido en
   las mismas 58 páginas de control: cambia el contenido de 41, se recuperan
   hasta **+1.414 palabras** en los listados de blog (las fichas repetidas que
   el deduplicador borraba) y se van hasta **−7.117** de megamenú oculto en un
   artículo de comercio electrónico, con **0 líneas de prosa perdidas**.

<a id="d46"></a>
46. **Un titular oculto no es un titular, y un icono con `aria-label` no es un
   enlace sin ancla** — cuatro cosas que el extractor leía mal y que acababan en
   el informe: (a) los clones móvil/escritorio del mismo `<h1>` (uno con
   `d-none`, otro con `aria-hidden`) contaban como tres h1 y producían un
   `h1_multiple` falso, mientras que los de `template`/`noscript` se tiraban en
   silencio —al revés de lo que se hacía con sus enlaces e imágenes—; ahora se
   guardan TODOS con una marca `headings.oculto` y los checks de h1 y el CSV
   cuentan solo los que se pintan; (b) el texto del titular se armaba uniendo
   nodos con espacio, así que `Zapa<span>tillas` daba `"Zapa tillas"` y un
   `<script>` dentro del `<h1>` acababa en el texto; un `<h1>` que solo lleva el
   logo ahora usa el `alt` de la imagen, que es de donde lo lee Google;
   (c) `role="heading"` con `aria-level` cuenta como titular de ese nivel (2 por
   defecto, según ARIA); (d) un enlace de icono sin texto usa su `aria-label`,
   su `title` o la etiqueta de su `<svg>` — es lo que anuncia un lector de
   pantalla y lo que Google toma como ancla: **3.438 → 1.276 anclas vacías** en
   58 páginas de control (2.915 → 1.056 contando solo los enlaces internos). El
   ancla pesa en el agrupamiento por repetición del PageRank (decisión 29) y en
   todo análisis de anchor text. *(Esas cifras corrigen las de la primera
   versión de esta nota —«1.783 → 155»—, que no se reproducen con ninguna de
   las dos definiciones; estas salen de volver a pasar cada versión del
   extractor sobre las mismas 58 páginas. Ver la decisión 61, que baja las
   1.276 a 208.)* Además, lo que vive
   en `<template>` o `<noscript>` ya no aporta enlaces ni imágenes: no está en el
   DOM, y el `<noscript><img>` de la carga diferida duplicaba la imagen real
   (583 de 4.409 imágenes en las mismas 58 páginas). El hero fuera del
   contenedor (decisión 10) rechaza el bloque cuando más de la mitad de sus
   palabras están dentro de enlaces: con `<div class="top-bar"><h1 class=logo>`
   se antepone el megamenú entero al contenido.

<a id="d52"></a>
52. **Las imágenes que se auditaban no eran las de la página** — cuatro
   fallos que se tapaban entre sí: un `<source>` de `<picture>` se registraba
   como imagen sin alt (no existe el atributo en `<source>`: el alt que cuenta
   es el del `<img>`), el placeholder `data:` de la carga diferida se guardaba
   en lugar de la imagen real —medido en 58 páginas de control: **142 `data:`
   URIs guardadas como imágenes y 140 imágenes reales que nadie veía**—, un
   `srcset` con un `data:` se partía en su coma interna, y la deduplicación se
   quedaba con la primera aparición aunque fuera la que no lleva alt. Además el
   contenido mixto no se detectaba con `HTTP://` en mayúsculas ni en `<video>`,
   `<audio>`, `link[rel=preload|icon]` o un `url()` en línea (0 → 3 en las
   mismas páginas), `width="100%"` se leía como 100 px, y un pixel de 1×1 —un
   contador, nunca contenido— salía como imagen sin alt.

<a id="d61"></a>
61. **El alt de la imagen es el ancla del enlace de imagen** — Google lo
   documenta («if the link is an image, the alt text acts as the anchor text») y
   es el caso de la ficha de un listado, donde el enlace envuelve la foto y el
   nombre del producto está en el `alt`, y del logo que enlaza a la home. El
   respaldo del ancla llegaba hasta `aria-label`, `title` y la etiqueta del
   `<svg>` (decisión 46) y ahí se paraba. Medido sobre las mismas 58 páginas de
   control: **1.276 → 208 enlaces sin ancla**, y los 208 que quedan son iconos
   de redes sociales sin `alt`, sin `aria-label` y sin `title` — no los rotula
   nada. Contando solo los internos, **1.056 → 39**. Ninguna ancla que ya tenía
   texto cambia, y el `link_type` tampoco: un `<a><img></a>` sigue siendo
   `image` aunque su ancla salga del `alt`, porque el tipo describe el marcado.
   El `alt` va en último lugar a propósito: `aria-label` y `title` los escribe
   alguien para anunciar el ENLACE, el `alt` describe la IMAGEN.
   De paso, el mismo `alt` repetido deja de duplicarse: un enlace con el clon
   de móvil y el de escritorio de la misma imagen daba
   `"voto femenino voto femenino"` — 7 de las 58 páginas, y 504 valores de
   `links.alt_text` corregidos.

<a id="d79"></a>
79. **Un `<dialog>` cerrado es contenido salvo que se le note la plantilla**
   (#27) — `dialog` estaba entre los selectores que se quitan siempre, así que
   un FAQ en modal, una guía de tallas o las condiciones de envío desaparecían
   de `page_content`. Lo cerrado está en el DOM y Google lo indexa, igual que
   `hidden="until-found"` (decisión 44). Ahora solo se quita sin preguntar el
   `<dialog open>` —servido abierto se pinta encima de la página al cargar, que
   es la forma de un interstitial— y el cerrado se juzga como el resto de la
   plantilla (decisión 45), en `_dialog_es_plantilla`: fuera si su nombre casa
   con las listas de plantilla y promo o con las de interfaz propias de un
   modal (`login`, `cart`, `search`, `menu`…, que solo se aplican a `<dialog>`
   porque sobre un `<div>` cualquiera `search-results` o `account-types` son
   contenido), si su texto lleva una frase de consentimiento o reclamo
   —en un modal basta con que la DIGA: el que pide «aceptar cookies» es el de
   cookies aunque el texto legal que lo acompaña sea largo, que es justo lo que
   el filtro por párrafo no caza—, o si lleva campos de formulario (login,
   newsletter, buscador: un modal de contenido se lee, no se rellena). Lo que
   queda se desenvuelve a `<div>`. Sin medir sobre páginas de control: no hay
   un corpus guardado en el repo; lo cubren los tests de contenido, con cookies
   de texto largo, sin nombre y con nombre. Lo que no cambia: un `<dialog>`
   fuera de `<main>` sigue sin entrar, porque trafilatura se queda dentro del
   contenedor elegido; y `role="dialog"` sobre un `<div>` sigue siendo
   plantilla, porque ahí no hay forma de saber si está abierto.

<a id="d121"></a>
121. **Un titular vacio no es un titular, un titulo de relleno se dice una
   vez, y un enlace sin nombre sale de la temporal de aristas** (#150, parte B,
   puntos 1-3). Re-analizar, sin re-rastrear.
   - **`h1_vacio`** (warning, la consecuencia de `h1_missing`): la pagina tiene
     h1 visibles (`oculto IS NOT TRUE`) y ninguno con texto. El extractor ya
     guardaba `text=NULL` (`<h1></h1>`, `<h1><img src=logo></h1>` sin alt), y el
     analizador lo contaba como h1 presente. Mismo ambito que `h1_missing`
     (HTML 2xx, sin muros). **`h1_multiple` cuenta solo los que tienen texto**:
     uno bueno y uno vacio es un titular, y solo vacios es `h1_vacio` (36).
     **Guardia de epoca**: la regla del alt y `headings.oculto` entraron juntas
     (46); si ningun titular del job tiene `oculto` escrito, el censo es
     anterior y `h1_vacio` no se emite (un logo CON alt se guardo vacio). Los
     **saltos de nivel no son incidencia**: Google no documenta el orden como
     senal (sin verificar la cita); es accesibilidad, y va con axe en la Ola 8.
   - **`title_generico`** (warning): el titulo, o lo que queda al quitarle la
     marca, es un placeholder de una lista cerrada en es/en/ca/eu/fr/de/pt/it
     (`shared/titulos.py`: «Sin título», «Untitled document», «Nueva página»,
     la pagina por defecto de nginx o Apache...). La marca es el segmento
     (separador del soft 404) que aparece en mas de la mitad de los titulos
     indexables del host; con un solo titulo no se sabe cual es y el titulo
     tiene que ser el placeholder entero. «Inicio | Marca» y «Home» no estan en
     la lista. Se compara sin caja, acentos ni puntuacion en los extremos
     («Título» = «título.»). Solo indexables, una fila por valor con
     `paginas_afectadas`, y **tapa** el `title_duplicate` de ese grupo y el
     `title_too_short` de esas paginas: el arreglo es el mismo (36). **El
     titulo de una palabra se descarta**: lo que no caza `title_too_short`
     («Electrodomésticos») no es un error demostrable, y la regla del espacio
     da una palabra en cualquier titulo chino. El juicio semantico es de #16.
   - **`description_igual_title`** (info): la description, normalizada igual,
     es el titulo. Una que solo empieza por el no. Solo indexables y con
     description (sin ella ya es `description_missing`); una fila por (host,
     `firma_de_ruta`) porque suele ser el valor por defecto del CMS (64).
   - **`enlace_sin_ancla`** (info): enlace interno sin texto, sin
     `aria-label`/`title`/svg (que el extractor ya pone en el ancla, 46) y sin
     alt (61). Sale de tres columnas nuevas de `aristas_tmp`, rellenas solo en
     esas filas (el patron de `utm_hash`), en la unica lectura de `links` de
     `compute_link_counts` (37, 41); un test lo cuenta. Solo desde un origen
     que cuenta (200 y no noindex, 38 y 57). El mismo componente (posicion,
     destino) en `enlaces_plantilla_min_paginas` paginas o mas es una fila
     `alcance: plantilla` en la de URL menor (88); el resto, una por origen con
     `SUM(veces)` (94). `tipo: imagen` cuando es un `<a><img></a>`: puede salir
     tambien `image_missing_alt`, y el arreglo es el mismo. Misma guardia de
     epoca que `h1_vacio`: antes de la 46 el ancla no leia `aria-label` ni
     `title` y habria falsos positivos. Las anclas genericas («leer más») son
     de la Ola 8 y #13.

   Cambian cifras al re-analizar (72, 91): `h1_multiple` baja y salen tipos
   nuevos. **Sin medir en censos reales** (no habia ninguno en esta rama): la
   decision 46 cita 208 enlaces sin ancla en 58 paginas de control, 39
   internos, casi todos iconos de redes sociales. Tests:
   `tests/test_titulares_titulos_anclas_db.py`.

   **Revision (#159).** Dos fallos reproducidos, arreglados con su test:
   - **La nota de contenido de `/insights` SUBIA con `title_generico`.**
     Es una fila por valor y tapa el `title_duplicate` y el `title_too_short`
     de sus paginas, y la nota cuenta `url_id` distintos: tres paginas
     «Sin título | Marca» pasaban de restar tres a restar cero (el tipo no
     estaba en la lista) o una. Es el caso de la soft 404 de la decision 105.
     Ahora la nota y «Optimizar titulos» cuentan `title_generico` con sus
     `paginas_afectadas` (`_paginas_de_mas_en_grupos`); una de esas paginas
     con ademas `title_too_long` contaria dos veces, acotado por `max(0, …)`.
     `test_un_titulo_de_relleno_no_mejora_la_nota_de_contenido`.
   - **Una soft 404 seguia llevando su `h1_vacio`.** `h1_missing` esta en
     `TAPADAS` (105) y `h1_vacio` dice lo mismo: entra.
     `test_la_soft_404_tampoco_lleva_su_h1_vacio`.

   **Medido** re-analizando con `ROLLBACK`, base (#154) contra esta rama, en
   copias de cuatro censos locales (incidencias totales y lo que cambia):

   | censo | base | rama | cambia |
   |---|---|---|---|
   | progym 12-oct (4.319 URLs) | 4.946 | 4.947 | +1 `description_igual_title` (24 fichas), +1 `enlace_sin_ancla`, −1 `h1_multiple` |
   | progym 7-oct nofollow (15.026) | 15.817 | 15.812 | +1 `description_igual_title` (16), +1 `noindex_y_canonical_a_otra` (6 paginas), −6 `noindex_page`, −1 `h1_multiple` |
   | Lopesan (5.751) | 28.742 | 28.735 | −16 `h1_multiple`, +9 `noindex_y_canonical_a_otra` (−11 `noindex_page`), +14 `redireccion_a_noindex` (−3 `redirect_chain`) |
   | blogs.uoc.edu (34.704) | 93.615 | 93.575 | −47 `h1_multiple`, +4 `description_igual_title`, +3 `redireccion_a_noindex` |

   Ningun tipo nuevo sale en todas las paginas (64): el mayor son 14 filas.
   `title_generico`, `meta_refresh_redireccion` y
   `canonical_html_y_cabecera_distintos` no salen en ninguno (no hay
   placeholders, meta refresh ni canonical en cabecera en esos censos), y
   `h1_vacio` / `enlace_sin_ancla` solo se evaluan en los dos de progym: los
   otros son anteriores a la decision 46 y la guardia los salta. Los
   `h1_multiple` que caen en Lopesan son un segundo `<h1><div></div></h1>`
   vacio de verdad (comprobado en la pagina servida hoy), no un logo con alt.
   Tiempo de `run_all`: sin cambio apreciable (51/64 s Lopesan, 171/163 s
   blogs, con ruido de una maquina compartida).
   **Sin reproducir**: en un censo anterior a la 46, `h1_multiple` deja de
   contar un h1 guardado vacio que pudo ser un logo con alt (la guardia solo
   cubre `h1_vacio`); en Lopesan los 16 casos son vacios reales.
