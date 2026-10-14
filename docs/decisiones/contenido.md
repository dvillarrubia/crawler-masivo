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
