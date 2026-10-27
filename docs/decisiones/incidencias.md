# Decisiones: incidencias del análisis: criterios, severidad y duplicados

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d7"></a>
7. **URL issues as SEO problems** — Junk/malformed URLs are crawled and reported as SEO issues (not filtered), because if a crawler finds them, Google can too.

<a id="d24"></a>
24. **Casi duplicados con MinHash, no con simhash** — la nota antigua decía
   "near-duplicates via simhash", y simhash es efectivamente más barato (un
   entero por página). El problema es que su distancia no se le puede enseñar
   a un cliente: medido sobre un texto de 300 palabras, cambiar 3 da 0,92 de
   "similitud" y cambiar 10 ya da 0,81 — un informe que dice "estas dos se
   parecen un 81%" cuando comparten el 97% del texto no se sostiene en una
   reunión. La firma MinHash estima la Jaccard de los trigramas, que sí
   significa lo que parece; contrastado contra la Jaccard exacta, el error se
   queda en 1-3 puntos. Se mide sobre `page_content.content_text` (contenido
   sin plantilla): con el body entero, cabecera y pie hacen que todo el sitio
   salga duplicado de todo.

<a id="d25"></a>
25. **El reparto de bandas del LSH sigue al umbral** — con bandas fijas
   pensadas para el 90%, bajar el umbral a 0,6 no encuentra ni una pareja más:
   esas parejas no llegan siquiera a medirse, porque el filtro previo no las
   propone. `reparto_bandas` elige entre 8×32, 16×16, 32×8 y 64×4 el de filas
   más anchas que aún proponga el 95% de las parejas que están justo en el
   umbral. Por debajo de 0,6 (`UMBRAL_MINIMO_FIABLE`) el recall cae y se
   registra un WARNING: se mide igual, pero el recuento ya no es de fiar.

<a id="d26"></a>
26. **La firma son 256 muestras porque 64 cambiaban el veredicto** — el error
   típico del estimador es sqrt(s(1-s)/n). Con 64 muestras, una pareja al 93%
   real se reportaba al 87,5% y se caía del umbral del 90%: el mismo censo
   marcaba o no marcaba una página según el ruido del muestreo. Con 256 el
   desvío baja a ~2 puntos. Cuesta lineal y se midió: firmar 2.000 páginas de
   400 palabras pasa de 1 s a 3,8 s (≈40 s en un censo de 20.000).

<a id="d30"></a>
30. **Los porcentajes de /insights van sobre paginas HTML 2xx y cuentan
   paginas** — dividian por todas las URLs internas (saltos, PDFs, 404: donde
   `indexable` es NULL) y sumaban incidencias, asi que una pagina con dos
   problemas de title contaba doble y `pct_thin` pasaba del 100%. En i18n,
   `return_tag_ok`/`lang_valid` NULL es "sin verificar", no fallo: un censo sin
   verificar (Lopesan) salia con nota 0 y dos recomendaciones falsas de
   prioridad alta. `h1_missing` ya no se emite en 4xx/5xx.

<a id="d42"></a>
42. **Los avisos sobre la forma de la URL casaban mal, y uno no salto nunca**
   (#29). Cuatro cosas, medidas en blogs.uoc.edu (34.704 URLs):

   - `url_non_ascii` comprobaba la URL **escapada**, que es ASCII puro, asi que
     no salto en ningun censo: 0 -> **337 paginas**. Hay que decodificar antes.
   - La regex de escapes de JavaScript pedia `%5Cu\d{4}` —solo digitos— asi que
     `%5Cu002F`, el ejemplo de su propio comentario, NO casaba. Son
     hexadecimales.
   - `/-/categories/123`, la faceta de verdad de Liferay, no la cazaba ninguna
     alternativa; y `/elem_entry_list/` si la cazaba por `/ELEM_ENTRY` con
     IGNORECASE. Un listado de contenidos no es una faceta, y marcarlo manda al
     cliente a bloquear algo que quiere indexar.
   - Los checks se aplicaban a CUALQUIER fila: saltos de redireccion, imagenes,
     CSS. En un salto no hay nada que arreglar —la URL se esta yendo— y en una
     imagen la forma de la URL no es una decision editorial. Ahora solo
     documentos internos (HTML y PDF, que Google indexa). Efecto: `url_too_long`
     4.984 -> 4.623, `url_underscores` 1.950 -> 1.545, `url_uppercase` 693 ->
     563, `url_multiple_slashes` 8 -> 0.

<a id="d43"></a>
43. **Una cadena de redirecciones empieza en DOS saltos** — el umbral por
   defecto era 2 con la comparacion `hops > 2`, asi que hacian falta TRES y
   A->B->C, la cadena mas comun y la que Google pide evitar, no se reportaba
   nunca. Medido en el mismo censo: habia 2.731 redirecciones de un salto
   (normales), **372 de dos** y 3 de tres o mas; los avisos pasan de 3 a 375. El
   valor manda desde `api/schemas.py`, no desde la config del cliente: ahi
   estaba el 2.

<a id="d47"></a>
47. **Un duplicado solo importa entre páginas que Google puede posicionar, y
   se mide por contenido, no por bytes** — los grupos de título, description y
   h1 duplicados incluían las variantes `?utm`, las ordenaciones de un listado y
   los `/page/2`, que comparten título a propósito y están canonicalizadas o en
   noindex: no compiten con nadie y enterraban las que sí. Medido en tres censos,
   las páginas avisadas por título duplicado pasan de 5.936 a 2.788, de 14.580 a
   5.299 y de 5.557 a 1.417; las de description de 2.807 a 659; las de h1 de
   15.235 a 10.838. Por eso `analyze_indexability` corre ahora ANTES de títulos y
   descripciones en `run_all`: estaba después, así que `Url.indexable` era NULL
   cuando se agrupaba. Y el duplicado exacto se compara con `urls.content_hash`
   —SHA-256 del contenido principal con caja y espacios normalizados— en vez de
   `body_hash`, que es el de los bytes de la respuesta: con un token CSRF o un
   nonce de CSP dentro, dos páginas idénticas nunca coincidían. En blogs.uoc.edu
   el hash de bytes encontraba **0** duplicados y el del contenido encuentra
   **745 páginas en 304 grupos**, entre ellos la misma política de privacidad de
   652 palabras publicada e indexable en decenas de blogs del multisite.
   `body_hash` se conserva: responde a otra pregunta (si la página cambió entre
   dos rastreos). Los rastreos anteriores a la columna caen a `body_hash` con un
   aviso en el log.
   El casi duplicado (`near_duplicate_content`) sigue el mismo alcance desde la
   decisión 118: medía también noindex, canonicalizadas y externas.

<a id="d53"></a>
53. **Las cabeceras de seguridad son del servidor, no de la página** — HSTS,
   CSP, X-Frame-Options y X-Content-Type-Options son la misma respuesta en las
   29.808 páginas del sitio y no afectan al posicionamiento, pero se emitían una
   vez por página: en los censos guardados, **131.096 avisos de
   `unsafe_crossorigin`, 93.545 de `missing_csp` y 41.863 de
   `missing_x_frame_options`**, tapando los hallazgos reales. Ahora va un aviso
   por HOST con el número de páginas en los detalles, y `unsafe_crossorigin`
   desaparece: desde 2021 todos los navegadores aplican `noopener` por defecto a
   `target="_blank"`, así que no había nada que arreglar. `http_url` y
   `mixed_content` siguen siendo por página, porque sí lo son. Y el extractor
   daba por bueno un HSTS con `max-age=0` (que le dice al navegador que OLVIDE la
   política) y un `X-Frame-Options` vacío, y pedía X-Frame-Options aunque la CSP
   ya llevara `frame-ancestors`.

<a id="d54"></a>
54. **El título se corta por píxeles, no por caracteres** — Google trunca el
   título del resultado hacia los 580 px y el fragmento hacia los 985: 65 letras
   estrechas caben y 55 en mayúsculas no. Medido en tres censos, **8.801 títulos
   pasaban de 60 caracteres sin pasar del ancho en píxeles** —o sea, sin
   truncarse—, el 29% de los avisos de título; al contrario solo ocurría en 3
   páginas de más de 80.000. `title_too_long` y `description_too_long` van ahora
   por `*_pixel_width`, cayendo a caracteres en los rastreos que no lo midieron.
   Y la estimación de ancho estaba ciega fuera del latino: 9,6 px por carácter
   daba 202 px para un título japonés de 21 caracteres que mide unos 420, así que
   **ningún título CJK salía como truncado**. Hay tabla por rango Unicode
   (ideogramas y kana de ancho completo, hangul, tailandés, árabe, hebreo,
   emoji) y un emoji compuesto cuenta como un glifo.

<a id="d64"></a>
64. **La severidad es la consecuencia, y un aviso que sale en todas las
   páginas deja de ser un aviso** — las dos salieron del mismo sitio: un
   informe entregado de 315.119 incidencias donde el hallazgo más grave del
   sitio estaba archivado como `info`.
   (a) **`canonical_cross_domain` se decide por dominio REGISTRABLE, no por
   host.** A otro subdominio de la misma casa (`comein.uoc.edu` desde
   `www.uoc.edu`) es consolidación de contenido: `info`. A otro dominio
   registrable la página se saca del índice en favor de un sitio que no es el
   suyo: **`error`**. Medido: en www.uoc.edu hay 3.551 canonicals a otro host y
   **3.550 se quedan dentro de uoc.edu** —con el criterio de host eran 3.411
   líneas de ruido—, mientras que en Lopesan los 2.367 iban a
   `*.lfr.cloud`, el servidor de origen de Liferay, y eran el **99,5% de su
   sección de hoteles**. Para eso `dominio_registrable()` se muda a
   `shared/dominios.py`: lo necesitan el spider (alcance del rastreo) y el
   analyzer (si un canonical se va de casa), y la imagen del análisis solo
   copia `shared/` y `analysis/` — el mismo motivo que la decisión 23.
   (b) **`image_missing_alt` va por IMAGEN, no por aparición.** La unidad de
   trabajo es la imagen: ponerle el alt al logo del pie se hace una vez, no
   10.990 —que es las veces que salía, repartido en cinco variantes responsive
   del MISMO logo—. Medido en cinco censos: **287.175 → 19.287 (−93%)** en el
   peor y entre −30% y −75% en los otros. No se pierde nada: la fila lleva
   `paginas_afectadas` y `resources` conserva todas las apariciones. Misma cura
   que la decisión 53 con las cabeceras.
   Efecto conjunto sobre el informe entregado de Lopesan: **315.119 → 38.922
   incidencias**, y los errores pasan a ser los cinco que hay que leer
   (4.716 de datos estructurados, 2.367 de canonical, 32 4xx, 12 hreflang
   rotos, 4 canonical rotos). **Las notas de `/insights` no se mueven** (88 y
   77 antes y después): esto no cambia la valoración, cambia qué se ve.

<a id="d92"></a>
92. **La nota global del Informe SEO no puede decir «Bueno» con errores
   abiertos** (UX-05 de `docs/AUDITORIA_UX.md`). Era una media ponderada de
   seis categorías, y una media esconde justo lo que importa: el censo de
   Lopesan sacaba **88 «Bueno»** con 2.367 páginas canonicalizadas a
   `*.lfr.cloud` —la mitad del sitio fuera del índice, decisión 64—. Ahora la
   media se recorta: con cualquier URL con una incidencia de gravedad `error`,
   como mucho **79** («Mejorable»); si las URLs con error son el **10 %** de las
   internas o más, como mucho **49** («Crítico»). Lopesan: 2.449 de 5.751 URLs
   (42,6 %) → **49**. La respuesta lleva `overall_score_sin_tope` y `tope` (con
   las cifras y el motivo), y la interfaz lo dice encima de la nota: un 88 que
   baja a 49 no es lo mismo que un 52 que baja a 49. Las notas por categoría no
   cambian. Los umbrales (`TOPE_CON_ERRORES*` en `api/routers/results.py`) son
   un juicio, no una medida: lo que sí está medido es que sin tope la nota
   contradecía a Problemas y al Resumen del mismo censo.

<a id="d100"></a>
100. **Una URL del sitemap que no es indexable es una contradicción, y se dice
   por qué** (PR 1.3A de `docs/PLAN_MEJOR_CRAWLER.md`). El sitemap es la lista
   de URLs canónicas que el sitio pide indexar: Google pide incluir las URLs
   que se quieren ver en los resultados, y lista el sitemap como una de las
   señales de canónica, así que un sitemap con la URL A y un `rel=canonical` a
   B son dos señales que no coinciden. Había `sitemap_orphan` (en el sitemap
   y sin enlaces) y `not_in_sitemap` (indexable y fuera), pero nada miraba lo
   que el sitemap declara y la página desmiente. `sitemap_no_indexable`: una
   incidencia por URL interna con `in_sitemap = true` e `indexable` no
   verdadero, con `details.motivo` = `redireccion` · `4xx` · `5xx` ·
   `noindex` · `canonicalizada` · `bloqueada_robots` · `sin_contenido`, el
   código, el `indexability_status` y, en saltos y canonicalizadas, el
   `destino`. El motivo sale del prefijo de `indexability_status`, que es la
   única función de indexabilidad (decisión 39): no hay un segundo criterio.
   Criterios:
   - **Una por URL, `group_key` NULL.** La entrada que hay que quitar (o la
     directiva que hay que corregir) es la de esa URL; el par (tipo, URL) ya
     identifica el hallazgo entre censos (decisión 88). Lo agregado es el
     desglose por motivo, que va en el log del análisis y sale del resumen por
     tipo.
   - **`warning` en todos los motivos.** Ninguno saca del índice una página
     indexable —eso es lo que reserva `error` (decisiones 64 y 92)— y ninguno
     es solo informativo: cada entrada es una URL que el sitio pide indexar y
     la propia página desmiente. (Que un sitemap sucio le haga a Google
     fiarse menos de él NO está en su guía de sitemaps —solo lo dice del
     `lastmod`—; es observación nuestra, y por eso no va en la ficha.)
     El 404 o el 500 ya llevan su `4xx_error` / `5xx_error` (`error`) por la
     página; esta dice otra cosa y se arregla en otro sitio (el generador del
     sitemap), igual que Search Console los separa como «URL enviada…».
   - **Sustituye a `sitemap_orphan` en las no indexables** (decisión 36).
     `sitemap_orphan` le decía a un noindex del sitemap «enlázala desde su
     sección», que es el consejo contrario. Ahora exige `indexable` no falso.
   - **Lo que no se sabe no se afirma.** Un **429** o un **503** es el sitio
     frenando al rastreador, no lo que verá Google —Google documenta los dos
     como la misma señal temporal de «frena», y Scrapy los reintenta igual
     (`RETRY_HTTP_CODES`)—. Un 500 sí se emite: es la página rota, no un
     «vuelve luego». En los censos medidos no hay ni un 503 en el sitemap
     (los 509 5xx son todos 500), así que las cifras no cambian. «Sin
     respuesta» tampoco dice nada de la página. Ninguno de los tres se emite:
     se cuentan en el log como «sin comprobar». Un 403
     sí se emite: es lo que respondió el sitio, y no hay forma de distinguir
     un WAF de un permiso. Las URLs externas del sitemap tampoco: son otro
     problema (un sitemap solo puede listar su host).
   - **Sitemap leído a medias** (decisión 50): las que no se leyeron están a
     NULL y no se juzgan; las que sí se leyeron están declaradas igual, así que
     se avisan. Sin ningún `in_sitemap = true` no corre nada.
   - **No lee `links`**, solo `urls` y `html_meta`: vale igual en un censo con
     los enlaces archivados (decisión 95).
   - **Convive con `noindex_page` y con el hreflang hacia esa URL**, a
     propósito. Una `/en/hotels` canonicalizada a `/es/hoteles` y declarada en
     el sitemap da este aviso en ella y `hreflang_destino_no_indexable` en la
     página que la anota (decisión 99, que tras la revisión se ancla en la
     fuente y no en el destino); en UOC, 8 noindex del sitemap dan además
     `noindex_page`. Lo que hay que corregir puede ser el mismo canonical o el
     mismo noindex, pero también puede ser lo contrario —que esa URL no deba
     estar en el sitemap, o que el hreflang deba apuntar a la canónica—, y cada
     aviso dice dónde está la contradicción: es el patrón de un destino de
     hreflang roto, con su `4xx_error` en su URL y `hreflang_broken_target`
     en quien lo anota (decisión 56).

   Medido el 2026-10-16 ejecutando `analyze_sitemap` sobre los censos de la BD
   local dentro de una transacción que acaba en `ROLLBACK`:

   | censo | URLs en sitemap | `sitemap_no_indexable` | motivos | `sitemap_orphan` antes → después |
   |---|---|---|---|---|
   | penguin | 25.785 | **5.906** | 5.828 noindex, 56 canonicalizada, 13 4xx, 8 5xx, 1 salto | 19.036 → 13.187 |
   | www.uoc.edu | 15.644 | **1.404** | 782 salto, 501 5xx, 87 4xx, 34 noindex | 6.835 → 6.815 |
   | Druni | 45.005 | **135** | 119 salto, 16 canonicalizada | 9.266 → 9.265 |
   | Lopesan | 68 | **59** | 44 salto, 15 canonicalizada | 0 → 0 |
   | progym, CST | — | 0 | sin datos de sitemap (`in_sitemap` NULL): no corre | — |

   Fuera quedan 122 URLs de penguin con 429 y 78 de Druni y 24 de www.uoc.edu
   sin respuesta. En Lopesan, **59 de sus 68 entradas** no son indexables:
   el sitemap entero está sucio. En penguin, las 5.849 que dejan de ser
   `sitemap_orphan` son las noindex y canonicalizadas que pasan a
   `sitemap_no_indexable` (5.825 + 24): ningún hallazgo se pierde, cambia el
   consejo.

   Revisados a mano (estado del censo y, el 16-oct, `curl` al sitio):
   - Druni `/marcas/brow-tint` → 301 a `/marcas/syoss`: **cierto**, sigue
     igual. Son fichas de producto o marca retiradas que el sitemap sigue
     declarando.
   - www.uoc.edu `/portal/en/25-anys/index.html` → 404: **cierto**, sigue
     igual, y la enlazan 9 páginas.
   - penguin `/es/991398-david-sanglas` (ficha de autor) → `noindex, nofollow`
     en el sitemap: **cierto**, sigue igual. Son las 5.828: el generador
     incluye fichas de autor que la plantilla pone en noindex.
   - Lopesan `/en/hotels` canonical a `/es/hoteles` en el censo del 6-oct:
     **cierto en el censo, ya no en el sitio**: hoy sirve canonical a sí
     misma. Era el canonical lo que estaba mal, no el sitemap, que es por lo
     que la ficha dice «decidir cuál de las dos está mal».
   - Lopesan `/es/politica-empresa` → 301 a `/es/corporativa/politica-esg`, y
     sus versiones `/en/` y `/de/` canonicalizadas a ella: **cierto** en el
     censo; cadena de contradicciones dentro del propio sitemap.

   Fuera de esta decisión (parte B de 1.3, hecha en la decisión 126): la tabla `sitemap_entries`
   (`url_hash`, fichero, `lastmod`), conectar `parse_sitemap_entradas`, y con
   ella `en_varios_sitemaps`, `sitemap_supera_limite` y `lastmod_futuro`, más
   el fichero de origen en la ficha de cada URL. Exige re-rastrear y entra en
   `shared/almacenamiento.py` y en el borrado en masa. Hoy no se sabe **de qué
   fichero** viene cada entrada, así que el aviso no puede decir «arregla el
   sitemap de autores». Y una pista sin comprobar: Lopesan tiene 68 URLs en el
   sitemap de un sitemapindex de Liferay con decenas de hijos, y 1.813
   `not_in_sitemap`; conviene mirar si la pertenencia casa bien (por ejemplo,
   `robots.txt` lo declara como `https://lopesan.com:443/sitemap.xml`).

<a id="d102"></a>
102. **HTML que Google trunca o ignora: más de 2 MB y un `<head>` que se
   corta antes de tiempo** (PR 1.5 del plan). Tres tipos nuevos, de
   `analyze_html_ignorado`, sobre HTML interno 2xx:
   - **`html_supera_2mb`** (warning, por página, solo indexables). Google:
     «Googlebot crawls the first 2MB of a supported file type [...] The file
     size limit is applied on the uncompressed data» y, pasado el corte, solo
     indexa lo descargado (página de Googlebot, actualizada el 2026-02-03). No
     dice si son 2.000.000 o 2.097.152 bytes: se usa el **mayor**, para no
     afirmar un corte que quizá no hay. El tamaño es `urls.html_bytes_servidos`
     (nueva); en un censo anterior **sin render** sale re-analizando del
     **mayor** de `content_length` y `transfer_size`, porque la decisión 50 los
     cambió de sitio el 2026-10-07 y el descomprimido es el mayor en las dos
     épocas. **Con render no**: ahí `content_length` es el DOM montado, y no
     se afirma nada (WARNING en el log con cuántas quedan sin medir). Medido en
     Druni, las 5 primeras con 2,2-3,9 MB de DOM sirven HOY 1,5-2,2 MB de HTML:
     cuatro de cinco serían falsos. Para los rastreos nuevos con render, un
     `PageMethod` lee `decodedBodySize` de Navigation Timing (comprobado con un
     HTML gzip de 3,4 MB, con la interceptación de scrapy-playwright, con y sin
     `Content-Length`).
   - **`head_cortado`** (agrupado por elemento que corta + metadatos
     perdidos, con `paginas_afectadas` y `group_key`). **Severidad por lo que
     se pierde** (64): `error` si detrás queda el `robots`, el `googlebot` o el
     canonical —deciden si la página entra en el índice y con qué URL—, y
     `warning` si solo título, description o hreflang. Google: «once Google
     detects an invalid element in the <head>, it assumes the end of the
     <head> element and stops reading» (valid-page-metadata, 2025-12-10). Solo
     se emite si detrás del corte queda algo que importa: `title`, `robots`,
     `googlebot`, `description`, `canonical` o `hreflang`. Si lo que queda en
     el body es el canonical o el hreflang, `canonical_in_body` y
     `hreflang_in_body` **no** se emiten además en esa página: el hallazgo es
     el corte (36). **Ni `canonical_missing`**: el extractor solo toma el
     canonical del head tal como lo parsea lxml, y lxml también cierra el head
     en un `<img>` o un `<div>`, así que esa página llega con `canonical_href`
     vacío y salía «no declara canonical» (`info`) encima del `error` del
     corte. Lo cazó un test con el spider de verdad; el que había ponía a la
     vez `canonical_href` y `canonical_in_body`, una combinación que el
     extractor no produce. **La indexabilidad no cambia**: sigue leyendo ese robots
     como aplicado, porque no está comprobado qué hace Google con un robots en
     el body, y cambiarla sin medir metería o sacaría páginas del índice y del
     PageRank (38). (El canonical detrás del corte, en cambio, NO se aplica,
     igual que cualquier canonical del body: eso sí lo documenta Google —un
     `rel=canonical` fuera del head se ignora— y es lo que ya hacía la
     indexabilidad antes de esta decisión. La asimetría es la de lo que está
     comprobado, y la incidencia marca los dos como `directiva_en_duda`.)
     Lo que se afirma es que la directiva está donde Google
     deja de leer, y la incidencia lo marca (`directiva_en_duda`) y la ficha
     lo dice: el mismo informe que la da por noindex avisa de que eso está en
     duda, en vez de contradecirlo en silencio.
   - **`meta_fuera_del_head`** (**info**, agrupado): título, description o
     robots en el body **sin** corte. Google dice que van en el head y no
     dice qué hace con ellos fuera; no se afirma que los ignore. Y hay quien
     los pone ahí a propósito: **Next.js 15.2+ con streaming metadata** sirve
     el `<title>`, la description, el robots y el canonical al final del
     `<body>` de toda página con `generateMetadata` dinámico, los sube al head
     con JavaScript y documenta que Googlebot los interpreta bien
     (nextjs.org/docs/app/api-reference/functions/generate-metadata#streaming-metadata).
     Con `warning` y «la directiva puede no aplicarse», cada plantilla de un
     sitio así habría salido con un fallo que su framework documenta como
     correcto. Es un dato que mirar (con la inspección de URL), no un fallo. Canonical y hreflang
     no entran (tienen su tipo), ni un segundo `<title>` o `description` con el
     bueno en el head, que ya es `title_multiple` / `description_multiple`.

   **El head se lee con un tokenizador propio** (`seo_crawler/head_html.py`)
   que sigue a Chromium con scripting activado, y no con un árbol. Contrastado
   en 17 casos con el Chromium de la imagen (`tests/test_head_html.py`, que lo
   repite en CI, y ahora 19 con los dos de `<foreignObject>`): **lxml** falla en 3 (deja en el head un meta detrás de un
   `<svg>` o de un elemento personalizado, y saca de los dos uno detrás de
   `</head>`), y **html5lib en 3** distintos: corta el head con
   `<noscript><img>` y `<noscript><iframe>` —el píxel de Facebook y el GTM mal
   pegado, en media web— porque parsea con scripting desactivado, y no conoce
   `<template>` en el head. Además cuesta 0,44-0,54 s por 2 MB frente a 54 ms
   del tokenizador (10 ms en una página de 369 kB; 3,5 ms en una de 13 MB,
   porque deja de leer cuando ya no quedan candidatos). Por eso se descarta.
   Solo mira los primeros 2 MB: lo de detrás Google ni lo descarga.

   **Lo que dice `has_meta_outside_head` no es una incidencia**: cuenta
   cualquier `<meta>` del body, incluidos los `itemprop` de microdatos, que son
   válidos ahí. Salta en **28.335 de 28.454** páginas HTML 2xx de penguin, 35.871
   de 56.544 en Druni, 15.506 de 22.020 en UOC, 2.427 de 4.318 en Lopesan y 675
   de 3.581 en progym. Se queda la columna y se añaden dos que sí dicen algo:
   `html_meta.head_cortado_por` (`img`, `div`, `#texto`…; NULL si el head acabó
   donde debía) y `html_meta.fuera_del_head` (lista; `''` = medido y nada,
   NULL = no medido). **Con render no se miden** (el spider las deja a
   NULL y el analizador no emite ninguno de los dos): el DOM serializado ya no
   conserva el corte pero sí lo que el navegador movió al body, así que un
   head cortado por un píxel saldría como `meta_fuera_del_head` —«sin corte»,
   «súbelos al head»—, con la causa y el arreglo equivocados y sin suprimir
   `canonical_in_body`; y lo que Next.js sube con JavaScript ya está en el
   head del DOM. El motivo que se guarda es un nombre de etiqueta válido de
   hasta 40 caracteres o `#otro`: HTMLParser toma por una sola etiqueta una
   errata como `<metaproperty="og:title"content="...">`, y guardada entera en
   el `VARCHAR(64)` tumbaba el INSERT de `html_meta` en Postgres y lo
   pendiente del lote con él. Dentro de un `<foreignObject>` se vuelve a leer
   HTML y al cerrarlo se sigue en el SVG (el `<title>` del icono que viene
   después no es el de la página). **Head o body múltiples** no son un tipo aparte: un
   segundo `<body>` el navegador lo funde sin consecuencia, y un segundo
   `<head>` dentro del body deja sus metadatos en el body, que es lo que ya
   cuenta `fuera_del_head`.

   **Qué sale re-analizando y qué exige re-rastrear.** `html_supera_2mb` sale
   re-analizando en los censos sin render; `head_cortado` y
   `meta_fuera_del_head` necesitan las columnas nuevas, que solo rellena un
   rastreo. Medido (consulta de solo lectura que replica el check):

   | censo | render | `html_supera_2mb` | HTML > 2 MiB sin filtrar |
   |---|---|---|---|
   | penguin | no | **17** | 24 (7 son `noindex`) |
   | Lopesan | no | **0** | 12, todas canonicalizadas a `*.lfr.cloud` |
   | progym | no | **0** | 0 |
   | UOC, Druni, CST | sí | sin medir | 2 / 2.434 / 5 de DOM |

   Para el head, sin columnas guardadas, una muestra viva (2026-10-16, curl_cffi
   como el rastreo): 70 páginas indexables de Lopesan, 70 de UOC, 70 de
   penguin, unas 80 de Druni (el resumen de la medición dice 78 y la primera
   versión de esta nota 79; no queda el registro para desempatar, y el
   resultado, 0, no depende de ello), 40 de blogs.uoc.edu y 39 de Saunier, **0 con un head
   cortado que pierda algo**. progym (403) y CST (404 en 11 de 20) no se
   pudieron mirar. Ejemplos revisados a mano:
   - penguin `/es/217497-ficcion` (indexable, 5,9 MB hoy): 20 de las 24 fichas
     de producto y 216 de 555 enlaces están detrás de los 2 MB — **cierto**.
   - penguin `/es/976987-ebooks` (13,6 MB): 546 de 552 enlaces y todas las
     fichas detrás del corte, por 8.966 casillas de filtro; es `noindex` y no se
     avisa — **correcto no avisar**.
   - penguin `/es/64-libros-autoayuda` (2,26 MB): lo de detrás son 35 enlaces
     del pie y scripts, ninguna ficha — **cierto, de poca consecuencia**.
   - penguin, listados: el head lo corta un `<div>` de los filtros, pero el
     title, el canonical y el robots van antes — **sin aviso, correcto**.
   - penguin `/…/fragmento` (552 páginas): llevan un capítulo EPUB entero, con
     su `<head><title>cap-2</title>`, dentro del body. Es un segundo título, no
     uno fuera de sitio: lo dirá `title_multiple` al re-rastrear, no este check.
     Los `<title>` de los iconos SVG (cuatro por página) no cuentan.

   Fuera: dónde cae un metadato concreto respecto de los 2 MB (un head de más
   de 2 MB deja fuera el canonical aunque esté en el head); y la indexabilidad
   sigue leyendo el `meta robots` esté donde esté, porque no está comprobado
   que Google lo ignore en el body.

<a id="d105"></a>
105. **Soft 404 determinista: la página de error del sitio se pide, no se
   adivina, y lo dudoso no se afirma** (ola 2.2 de `docs/PLAN_MEJOR_CRAWLER.md`;
   [#11](https://github.com/dvillarrubia/crawler-masivo/issues/11) sigue viva
   para la fase con modelo, que recibe los candidatos). Un soft 404 es una
   página que responde **200** y es una página de error: Google la saca del
   índice por su cuenta, Search Console la cuenta como «Soft 404» (y, si estaba
   en el sitemap, como «La URL enviada parece un soft 404», que archivaba como
   error), y cada enlace interno que llega a ella es un enlace roto disfrazado.
   Ningún check lo veía: un 200 con título y texto pasa por página sana.
   - **La sonda.** Al empezar el rastreo se pide una URL que no puede existir
     (`shared/soft404.mangle_path`: el último tramo cambiado por un token
     determinista por job, conservando tramos padre, extensión y barra final) por
     host de las semillas, y otra por cada plantilla de ruta nueva
     (`firma_de_ruta`), hasta `crawl_behavior.soft404_sondas` (12; 0 las apaga).
     Y la de la raíz de **cada host interno** la primera vez que sale una página
     suya, no solo la de las semillas: si la semilla redirige a otro host (sin
     www → www), el censo vive en un host sin sonda. Una sonda que **solo cambia
     de esquema o de host** (`http://x.com/zz…` → `https://x.com/zz…`) es la
     canonicalización del sitio, no su respuesta: se repite una vez en el
     destino.
     Lo que responde el sitio es SU página de error. Va con su propio callback
     (`crawler/seo_crawler/sonda_soft404.py`): no pasa por `parse`, así que **no
     gasta `max_urls`**; no emite ningún item, así que **no es una URL del
     censo**; y su resultado vive en `jobs.soft404["sondas"]`, que no entra en
     ningún recuento, CSV, grafo ni comparación. Respeta robots.txt, no sigue
     redirecciones y usa el mismo render que el rastreo.
   - **Qué sonda deja huella.** Un 200 (soft 404 de serie) y también un 404/410
     con cuerpo: la página de error que el sitio sirve bien es la misma que otras
     URLs sirven con 200, y ese fue el caso real (abajo). **Una que redirige, no**:
     el destino es una página de verdad —la home casi siempre— y tomarlo por «la
     página de error» marcaría la home. Un muro de WAF tampoco (títulos de la
     decisión 62): con su huella, cada página bloqueada saldría soft 404, y eso
     es un bloqueo.
   - **La huella** es el contenido principal sin el eco de la URL pedida
     («La página /es/zz… no existe»; `quitar_eco`). La sonda guarda los
     primeros 4.000 caracteres (`MAX_TEXTO_SONDA`), pero el hash y el recuento
     van sobre el texto ENTERO (`huella_hash`, `huella_palabras`), y el parecido
     se mide contra el mismo principio de la página: con el texto recortado,
     una página de error de más de 4.000 caracteres —las de e-commerce con
     carrusel de productos— no casaba nunca. Casa por igualdad exacta
     —con menos de 5 palabras, solo si además coincide el título— o por MinHash
     ≥ 0,9 (`soft404_similitud`), que solo se calcula con 30 palabras o más en los
     dos lados (`firma()` devuelve None por debajo): con 14 palabras no se
     afirma nada por parecido. Dos guardias que no se pueden afirmar: si la
     huella casa con **más de la mitad** de su alcance (host o plantilla,
     `soft404_max_proporcion_huella`, con 3 coincidencias como mínimo), es la
     plantilla —un esqueleto de SPA, una plantilla vacía—, no un error; y si casa
     con una **portada** (`/`, `/es`), el sitio sirve la home para lo que no
     existe. En los dos casos va UN `soft_404_candidato` agrupado
     (`paginas_afectadas`, muestra de 20 y `group_key` por sonda), nunca uno por
     página. **Las sondas que casan con las mismas páginas (o tienen la misma
     huella) se juzgan juntas**, y la proporción se mide contra el host Y contra
     cada forma de ruta en la que caen: si una ve la plantilla, ninguna afirma.
     Antes cada sonda se medía solo contra su alcance, y la de la raíz, contra el
     host entero: un esqueleto de SPA que era el 100% de su sección y el 40% del
     host salía afirmado en las cuatro páginas, que llevaban además el candidato
     agrupado de la sonda de la plantilla.
   - **El titular dice que es un error** (`senal_titulo`, frases en
     es/en/ca/eu/fr/de/pt/it). Seguro si el h1 entero, o un segmento del título
     separado por la marca (`|`, ` - `, `–`), es la frase con dos palabras de
     relleno como mucho; una frase corta sin «página/page/404» («no
     encontrado», «not found») solo si no hay nada más que relleno de error
     («Paraíso no encontrado» es un libro). **Y solo con menos de
     `min_word_count` palabras propias**: una página de error no tiene contenido.
     Entre 30 y `min_word_count` (200) el titular solo no basta —cabe una
     entrada de glosario «Error 404 - Glosario SEO» de 150 palabras, que con
     el h1 «Error 404» y en el sitemap salía `error`—: hace falta que el título
     sea el de la página de error que dio la sonda (`titulo_de_la_sonda`); si
     no, candidato. Por debajo de 30 palabras no hay contenido que diga otra
     cosa. No había ningún caso en los censos locales (0 páginas 200 con 30-199
     palabras y un título de error): es un riesgo de la regla, no una cifra, y
     las 23 de CST tienen 16-21 palabras y el mismo título que su sonda.
     Las dos reglas salieron de revisar a mano: con los dos puntos como
     separador y sin el recuento, «Inauguración JJOO 2012: ERROR 404», un post de
     blogs.uoc.edu sobre la ceremonia, salía **`error`** (estaba en el sitemap).
     Ahora es candidato.
   - **Búsqueda interna sin resultados** (`es_busqueda` + frase): seguro, es el
     ejemplo que pone la documentación de Google. Con dos guardias: la frase
     tiene que estar en el título o el h1, o la página tener menos de
     `min_word_count` palabras (una búsqueda vacía no tiene más que la frase;
     con contenido, «sin resultados concluyentes» es prosa y «Marca X (0
     resultados)» la faceta de una búsqueda que sí los tiene); y el tramo de
     búsqueda tiene que ser el PRIMERO de la ruta (tras el idioma), con
     `resultados`, `cerca` y `busca` solo si además hay consulta, y `term`, `k`
     o `keyword` solo con una ruta de búsqueda: `/es/resultados/informe-2025`
     (blogs.uoc.edu tiene una así, de publicaciones), `/hotel/cerca/playa` y
     `/glosario?term=seo` pasaban por búsquedas. Las 12 de blogs.uoc.edu
     tienen 13-14 palabras y siguen saliendo. La misma frase fuera de una
     búsqueda (un listado que dice «no hay productos») y con poco texto, solo
     candidato.
   - **Agotado no es soft 404.** Google pide mantener la página de un producto
     agotado y marcar la disponibilidad, así que `OutOfStock`/`SoldOut` no se
     emite (se cuenta en el resumen: 7.700 en Druni, 789 en progym, que habrían
     sido ruido puro). `Discontinued` —todas las ofertas— es candidato: algo que
     no volverá, que Google puede acabar tratando como soft 404 si la página no
     ofrece nada más. En los 8 censos no salió ninguno.
   - **Severidad = consecuencia** (decisión 64): `error` si es indexable y está
     en el sitemap; `warning` si es indexable; `info` si ya está fuera del índice
     por noindex o canonical, donde lo que queda es el enlace roto disfrazado.
     Excluirlas escondía todo lo de www.uoc.edu: sus 19 páginas de error con 200
     estaban canonicalizadas. Los candidatos, solo en indexables. Un aviso por
     hallazgo: varias señales van en una incidencia, y una `soft_404` no lleva
     además lo que describe su contenido (`TAPADAS`: contenido escaso, título,
     description y h1 que faltan, sobran o se repiten, duplicados exactos y
     casi duplicados). Es el mismo hallazgo visto peor y manda a arreglar otra
     cosa: `title_duplicate` pedía hacer distinto el título de 23 páginas de
     error de CST que tienen que responder 404. Un grupo de duplicados que se
     queda con una sola página al quitarlas también sobra. Lo que dice algo de
     la URL y no de lo que sirve (forma de la URL, enlazado, sitemap,
     directivas) se queda. En `/insights` la soft 404 cuenta como fallo en cada
     criterio de la nota de contenido y lleva su propia recomendación (alta si
     hay indexables): sin eso, al perder su `low_word_count` la nota **subía**
     por un hallazgo peor. El tope de la decisión 92 no cambia: lo dispara la
     de severidad `error` (en el sitemap), que es la consecuencia.
   - **Medido** en 8 censos locales, con sondas tomadas en vivo el 2026-10-16
     con `scripts/sonda_soft404.py` (renderizadas en los censos con
     `render_js`) y `analyze_soft_404()` sobre una copia del censo. Antes: 0 en
     todos, el tipo no existía.

     | censo | páginas 200 | sondas: respuesta | `soft_404` | candidatos | tapadas | s |
     |---|---|---|---|---|---|---|
     | Lopesan | 4.318 | 11×404, 1×302 | 0 | 0 | 0 | 3,0 |
     | penguin | 28.454 | 8×3xx, 4×404 vacío | 0 | 0 | 0 | 7,1 |
     | progym | 3.581 | 12×403 (Cloudflare) | 0 | 0 | 0 | 1,1 |
     | Druni | 56.544 | 11×404, 1 redirección | 0 | 0 | 0 | 28,9 |
     | www.uoc.edu | 21.826 | 12×404 | **19** (info; huella **y** título en las 19) | 0 | 76 | 25,7 |
     | CST | 8.049 | 8×404, 4×200 que redirigen por JS | **23** (warning; título) | 0 | 92 | 3,0 |
     | blogs.uoc.edu | 29.808 | 12×404 | **12** (info; búsquedas vacías) | 4 | 24 | 20,2 |
     | Saunier Duval | 1.285 | 11×404, 1×200 | **1** (error; huella y título) | 0 | 1 | 0,7 |

     «Tapadas» son las incidencias que la `soft_404` sustituye: en CST 23
     `low_word_count`, 23 `title_duplicate`, 23 `description_missing` y 23
     `h1_missing` (antes de la revisión solo se quitaban las 23 primeras); en
     www.uoc.edu 19 de cada una de `description_missing`, `h1_missing`,
     `near_duplicate_content` y `title_too_long`. Las cifras de `soft_404` y
     candidatos son **las mismas** antes y después de la revisión de abajo: sus
     arreglos cierran casos que estos ocho censos no tienen (un esqueleto de
     SPA, páginas de error largas, una semilla que cambia de host, glosarios),
     y no se llevaron ninguno de los 21 aciertos revisados a mano.

     La huella funcionó donde podía: en www.uoc.edu y en Saunier la página del
     censo es **idéntica** a la que el sitio da hoy con 404 (eco quitado). En
     CST no casa —la página de error de hoy no es la del censo, y con 14-18
     palabras no se mide por parecido— y el título las encuentra igual. La sonda
     también enseña cosas del sitio que no son de ninguna página: CST responde
     200 y redirige por JS en dos plantillas, y el servicio técnico de Saunier
     responde 200 a cualquier `/sats/<lo-que-sea>`; queda en `resumen.detalle_sondas`.
   - **Revisión a mano: 25 casos**, descargados en vivo
     (curl_cffi como `shared/descarga_cruda.py`, Chromium para CST) y contrastados
     con lo guardado. **21 `soft_404`: 21 aciertos** (5 CST, 5 www.uoc.edu, 1
     Saunier, 10 búsquedas de blogs.uoc.edu). 19 se confirman hoy tal cual
     —las de www.uoc.edu y 4 de CST responden ya **404** con la misma página de
     error, la de Saunier sigue en 200 diciendo «la página no existe», 9
     búsquedas siguen vacías—; 2 cambiaron después del censo (una página de CST
     hoy publicada, una búsqueda que hoy tiene resultados) y lo guardado
     confirma que eran error en su día. **4 candidatos: 1 es un soft 404** (la
     portada en inglés de un blog, «Not found any results») **y 3 no** (un
     artículo sobre la obra «File not found 404», un listado cuyo primer post se
     titula así, y el post de los JJOO). Para eso son candidatos: lo afirmado no
     tuvo ni un fallo tras el arreglo, y lo dudoso es la lista para el modelo.
     Antes del arreglo hubo **1 falso positivo** entre los seguros, y con
     severidad `error`.
   - **Censos anteriores**: el análisis funciona sin sondas (títulos,
     búsquedas, disponibilidad); con `scripts/sonda_soft404.py <job> --escribir`
     se le añaden sondas de HOY, marcadas `origen: "sonda_posterior"`, que viaja
     a la incidencia (decisión 13: el sitio puede haber cambiado su página de
     error). Una `soft_404` en el sitemap es `error`, así que también pesa en el
     tope de la nota (decisión 92).
   - **Revisión (2026-10-16)**, cada arreglo con un test que falla sin él:
     las sondas se juzgan juntas y por forma de ruta (arriba); la huella va
     sobre el texto entero; la búsqueda y el titular con sus guardias; la sonda
     del host al que redirige la semilla y la que solo cambia de esquema; lo
     tapado y `/insights`. Y dos de coste, medidos contra la base local (1,6 M
     bloques de datos estructurados de todos los censos): los productos
     descatalogados se buscaban con `url_id IN (subconsulta)` y Postgres
     recorría `structured_data` **entera** convirtiendo cada JSON a texto —9,4 a
     12,4 s en Druni, 8,9 a 11,2 s en penguin, y creciendo con lo que guarda la
     instalación—; ahora va por lotes de 1.000 ids por el índice de `url_id`
     (con 5.000, el planificador estima 144.000 bloques y vuelve a recorrer la
     tabla), con una barrera `OFFSET 0` y un `lower()` con tres LIKE en vez de
     tres ILIKE: **3,1 s en Druni y 3,4-3,5 s en penguin**, con las mismas
     filas. Y la primera pasada cargaba con `.all()` todas las páginas con
     1.500 caracteres de texto: ahora va por lotes y no guarda el texto. Pico
     de memoria de `analyze_soft_404` medido en el mismo proceso: **Druni
     +351 MB → +75 MB, penguin +124 MB → +37 MB**. El test de `run_all` ya no
     mira el fuente: lo ejecuta (decisión 74).
   - **Fuera**: los soft 404 por redirección (URLs que mandan a la home; la sonda
     lo anota, ningún check lo usa todavía), la ficha del job no enseña las
     sondas (`JobResponse` no lleva `soft404`: cada sonda guarda hasta 4.000
     caracteres de texto) y todo lo que necesita leer la página con un modelo
     (#11).

<a id="d108"></a>
108. **Lo que el HTML declara de sí mismo se guarda en crudo y se avisa por
   plantilla, con la severidad de lo que rompe: nada de esto saca una página
   del índice** (#150, partes A3, A6 y B6-B8 y B10). Doce columnas nuevas en
   `html_meta`, que exigen **re-rastrear**, y doce tipos en un módulo propio,
   `analysis/avisos_de_html.py`. Las reglas que el spider y el analizador
   comparten (forma de una URL, viewport, idioma principal, normalización del
   charset) viven en `shared/metadatos_html.py` (23, 39 y 64), sin w3lib: la
   imagen del análisis no lo instala.
   - **Lo que se guarda.** `viewport` (el `content` del último
     `<meta name=viewport>`, `""` si va vacío) y `viewport_count`; `html_lang`
     (`lang` o, si no, `xml:lang` del primer `<html>`; `""` = atributo vacío,
     NULL = no está) y la cabecera `content_language`; `canonical_crudo`, el
     `href` del canonical tal como se escribió —`canonical_href` lo guarda
     resuelto, y la forma se perdía al extraer (`AUDITORIA_Y_VERIFICACION`,
     sección 10)—; `og_image_forma` (solo la forma: la URL resuelta ya va en
     `og_image`) y `twitter_image` como dato; `redireccion_js`; y el charset
     del `<meta>` en los **1.024** primeros bytes (el «prescan» del navegador;
     w3lib mira 4.096), el del BOM, si el cuerpo es solo ASCII y si el título
     o el texto llevan U+FFFD (`texto_mal_codificado`).
   - **Lo que no se midió no se afirma** (67, 89). NULL es «rastreo
     anterior», nunca «falta»: `viewport_count` es la marca de medido del
     viewport y del lang (0 = no hay viewport) y `charset_meta` la del charset
     y la redirección JS. **Con render**, el charset y la redirección JS quedan
     a NULL, como `head_cortado_por` (102): el cuerpo es el DOM ya ejecutado
     —si la página redirigía por JS, ya es el destino— y scrapy-playwright lo
     vuelve a codificar. Viewport, lang y OG sí se leen del DOM: es lo que ve
     Google tras renderizar. `og_ausente` es la excepción y vale para los
     censos ya guardados: `og_title` y `og_image` existen desde siempre.
   - **Agrupado** (36, 53, 64, 73): una fila por (host, `firma_de_ruta`) —por
     host lo que es del servidor: `charset_incoherente` y `charset_ausente`—
     con `paginas_afectadas`, 5 `paginas_ejemplo` y `group_key`, anclada en la
     página de **URL menor** del grupo (88). Solo HTML interno 2xx con
     respuesta válida (103) y sin las meta refresh con destino, que son una
     redirección; viewport, lang y OG solo en indexables (47). La plantilla es
     la forma de la ruta: las reglas `config.templates` del cliente las usa por
     ahora solo `scripts/check_js_templates.py`.
   - **Severidad.** `warning`: `viewport_ausente` y `viewport_incorrecto`
     (ancho fijo, sin `width=device-width` ni `initial-scale`, o sin
     `content`) —el móvil pinta a 980 px y encoge; la indexación mobile-first
     indexa la versión del rastreador de smartphone con o sin viewport, así que
     no es un error de indexación—; `texto_mal_codificado`, porque el carácter
     roto es lo que acaba en el índice y el snippet (va solo: la incoherencia
     que lo causa sale en su detalle, 36); y `canonical_con_fragmento` cuando,
     quitado el fragmento, apunta a OTRA página (el enrutado `/#/producto/1`
     consolida en la portada). `info`: el fragmento que apunta a la propia
     página, `canonical_relativo` (Google admite la ruta relativa; lo que rompe
     es un `<base href>` o un dominio espejo), `og_ausente` (faltan `og:title`
     **y** `og:image`) y `og_image_relativa` (redes sociales, no
     posicionamiento), `html_lang_ausente` (WCAG 3.1.1; Google saca el idioma
     del contenido), `html_lang_no_coincide_hreflang` (solo el idioma principal
     frente al código con que la página se anota **a sí misma**, sin
     `x-default` ni el hreflang del body; no se sabe cuál de los dos está mal
     hasta tener el idioma real del contenido, #16), `charset_incoherente`
     (cabecera, meta y BOM normalizados con las equivalencias de WHATWG, que
     hacen `latin1` = `iso-8859-1` = `windows-1252`; el texto sale bien) y
     `charset_ausente` (nada declarado **y** bytes no ASCII).
     `user-scalable=no` y `maximum-scale` no hacen incorrecto un viewport: son
     accesibilidad, no adaptación al móvil.
   - **Redirección por JavaScript, sin render: solo la que corre siempre.**
     `location = "…"`, `location.href = "…"` y `location.replace/assign("…")`
     con un literal, en un `<script>` en línea que se ejecuta (no `ld+json`,
     `<noscript>` ni `<template>`), **a nivel superior** del script: un escáner
     mínimo salta cadenas, comentarios y regex y cuenta llaves y paréntesis;
     dentro de una función, un `if`, un temporizador o un evento no se afirma,
     ni tras un `)` (un `if (x)` sin llaves), un `else` o un `=>`, ni con el
     destino concatenado, `#…`, `javascript:` o la propia página. La traducción
     de las regex de FreeCrawl daba 4 falsos positivos en 5 casos (repro de la
     guía); con esto, ninguno. Se guarda el destino absoluto y normalizado, y
     `redireccion_por_javascript` (`info`, **por página**: cada página puente
     es su hallazgo) lleva el destino, las palabras del contenido y el código
     del destino si el rastreo llegó a él por otro camino; si no, «destino no
     comprobado». **No se sigue**: no es un enlace ni un salto y no gasta
     `max_urls`. Con render no se emite: el salto «Redirect (JS)» que ya se
     registra no lleva aviso, porque antes hay que reproducir con Chromium si
     una meta refresh de 0 s acaba ahí también (guía de #150).
   - **Una meta refresh a `javascript:` no es una redirección** (B3 de #150):
     `extract_meta_refresh_target` devuelve None si el destino no es http(s);
     antes la página quedaba «Redirect (meta refresh)» con
     `redirect_url = javascript:void(0)` y el spider intentaba seguirla.
   - **Re-analizar cambia cifras** (72, 91): `og_ausente` sale ya en los
     censos guardados al re-analizarlos; el resto, solo en rastreos nuevos. No
     entran en las notas de `/insights` (92). **Sin medir en un censo real**
     (13): la sesión que lo implementó no tenía censos ni podía lanzar
     rastreos; antes de mergear, un canario sin render y otro con render del
     mismo sitio, y 5 páginas por tipo contrastadas a mano con `curl`.
   - **Fuera**: las dimensiones reales de la `og:image` (van con #101, con la
     misma sonda de recursos), `twitter_missing` (X usa OG de respaldo), la
     comparación del canonical del HTML con el de la cabecera (parte A de
     #150, re-análisis) y el `viewport` en `_META_RELEVANTES` del head cortado
     (cambiaría las cifras de la 102).
   - **Revisión** (2026-10-18), dos fallos reproducidos y arreglados:
     - **Con varios viewports manda el ÚLTIMO, no el primero.** Comprobado en
       Chromium con emulación móvil a 390 px: `device-width` seguido de
       `width=1024` pinta a 1.024 px, y al revés a 390. Guardando el primero,
       la plantilla que se ve bien salía `viewport_incorrecto` y la que se ve
       mal se callaba. `viewport` guarda ahora el `content` del último.
     - **El script envuelto en un comentario HTML** (`<script><!-- …
       //--></script>`, la forma de los portales antiguos, que son justo los
       que tienen páginas puente) no se veía: el escáner leía `<!--` como
       operadores y la sentencia siguiente quedaba «abierta». En JavaScript es
       un comentario de una línea (Anexo B de ECMAScript, y Chromium ejecuta el
       script); `-->` también, pero solo a principio de línea (`a-->b` es un
       decremento y una comparación).

<a id="d117"></a>
117. **Una cadena de redirecciones es un aviso, y pasados 10 saltos es un
   error** (#149, duplicado en #150). Dos cosas de `redirect_chain` (decision 43):
   - **Una fila por cadena.** Cada origen intermedio de A → B → … → M es
     tambien una cadena, y salia una fila por cada uno: una cadena de 12 saltos
     daba **11 `redirect_chain`** (hops 12, 11, …, 2), todas `warning`. El
     arreglo es una regla del servidor —que todas salten directas a la final—,
     asi que el aviso va en el primer origen de la cadena (el interno al que no
     redirige ningun otro origen con cadena), con `paginas_afectadas` (los
     origenes de la cadena que lo son, el primero incluido) y hasta 20
     `intermedias`, como lo agrupado de las decisiones 36 y 64. Dos cadenas que
     se juntan son dos avisos: tienen dos primeros saltos distintos. Los bucles
     y `redireccion_a_error` siguen por origen (decision 101): un salto a un 404
     se arregla en su propia regla.
   - **`LIMITE_SALTOS_GOOGLE = 10`.** Google sigue hasta 10 redirecciones; si no
     llega a contenido, Search Console da un error de redireccion. Leido de su
     documentacion y sin verificar contra Google: 10 saltos seguidos de
     contenido llegan, 11 no. Con `hops > 10` es `redirect_chain` con severidad
     **`error`** y `supera_limite_google: true` —el mismo tipo con la severidad
     por la consecuencia, decision 64—, sea cual sea el umbral del job
     (`max_redirect_chain_length`). Si ademas acaba en un 4xx/5xx manda el
     limite: Google ni llega al error. Va en el mismo aviso con `final_status`,
     no tambien como `redireccion_a_error` de ese origen. Los origenes
     intermedios que quedan a 10 saltos o menos de ese error siguen siendo cada
     uno su `redireccion_a_error`.
   - **El spider tiene tope**: sin render, cada salto es una peticion nueva y
     solo el dupefilter y `max_urls` paraban una cadena de URLs siempre
     distintas. `MAX_SALTOS_SEGUIDOS = 20` (el de Chromium y Firefox; con render
     ya cortaba Chromium): el salto 21 queda registrado con su destino, pero no
     se sigue. Cortar en 10, como FreeCrawl, deja sin saber donde acaba. Una
     cadena cortada acaba en una URL sin rastrear: `final_status` None, y no se
     afirma ningun error.

   **Que pide cada cosa**: el aviso agrupado y el error se aplican re-analizando
   (cambian cifras ya entregadas: un censo con cadenas largas pierde las filas
   de sus intermedias); el tope, re-rastrear. Sin medir en censos reales; la
   consulta para hacerlo, con el analisis en ROLLBACK, es
   `count(*) FROM issues WHERE job_id = :j AND issue_type = 'redirect_chain'`
   antes y despues, y `AND (details->>'hops')::int > 10`. Tests:
   `tests/test_cadenas_largas_db.py` (contra SQLite) y
   `tests/test_spider_cabeceras_y_saltos.py` (una cadena de 25 URLs guarda 21
   filas).

<a id="d118"></a>
118. **Un casi duplicado se mide entre las mismas páginas que el duplicado
   exacto, y una escritura sin espacios se parte en caracteres**
   ([#149](https://github.com/dvillarrubia/crawler-masivo/issues/149)). Dos
   fallos de `near_duplicate_content`, que la ficha de `incidencias.js` ya
   describía bien («Páginas indexables…») y el código no cumplía.
   - **El alcance.** `analyze_near_duplicates` solo pedía 200 y HTML: entraban
     las noindex, las canonicalizadas y **las externas**. Con cuatro URLs del
     mismo texto —`/p`, `/p?orden=precio` (canonicalizada), `/p?page=2`
     (noindex) y `https://otro.com/p`— salían **4 avisos**, y el de `/p` citaba
     como casi duplicadas a su propia variante, a su paginación y a la otra web.
     Ahora pide lo mismo que `analyze_duplicates` (decisión 47): `is_internal`,
     `indexable` (que ya excluye canonicalizadas, noindex, bloqueadas y muros,
     decisión 103) con `is_(True)` en las dos, para que no discrepen. Con eso
     una variante canonicalizada no puede salir como casi duplicada de su
     canónica: ni se firma. En el caso de arriba, **0 avisos** y `/p` medida
     con 0. Se filtra, no se descartan «parejas variante → canónica»: la
     misma regla quita de una vez todo lo que Google no va a posicionar.
   - **Lo que no se mide queda en NULL.** `_bulk_update_near_duplicates` solo
     escribía las páginas con firma, así que al re-analizar una página que
     ahora es noindex (o cuyo texto ya no llega al mínimo) conservaba el
     `near_duplicate_count` del análisis anterior. Antes de escribir se ponen a
     NULL («no medido», `init_db.py`) las del job que tenían valor y no se han
     firmado esta vez. Y con una sola página medible se escribe su 0: antes,
     con menos de dos firmas, no se escribía nada.
   - **El `--dry-run` de `scripts/near_duplicates.py`** tenía su propia copia
     de la consulta —sin el alcance— y llamaba a `Select.yield_per`, que en
     SQLAlchemy 2.x no existe: fallaba siempre. Ahora usa
     `SEOAnalyzer.firmas_casi_duplicados()`, la misma que el análisis.
   - **Las escrituras sin espacios.** `tokenizar` partía por `[^\w]+` de `re`.
     En chino y japonés eso deja **una frase por token**; en tailandés, trozos
     de sílaba cortados en cada marca vocálica, y en hindi —y en cualquier
     escritura con matras o virama— la palabra se rompe porque `\w` no casa las
     marcas combinantes:

     | Texto | Antes | Ahora |
     |---|---|---|
     | `東京は日本` | `東京は日本` | `東, 京, は, 日, 本` |
     | `हिन्दी भाषा` | `ह, न, द, भ, ष` | `हिन्दी, भाषा` |
     | `ภาษาไทยเป็น` | `ภาษาไทยเป, น` | un token por carácter, marcas incluidas |
     | `한국어는 띄어쓰기` | igual | igual (lleva espacios) |
     | `１２３ＡＢＣ` | `１２３ａｂｃ` | `123abc` |

     El tokenizador (librería `regex`, que ya traían el crawler y la API y se
     declara ahora en `analysis/requirements.txt`) toma como token un carácter
     de Han, hiragana, katakana (con `ー`), tailandés, lao, jemer o birmano, o
     una racha de `[\p{L}\p{N}\p{M}_]`: las marcas van dentro de la palabra.
     Los shingles no cambian (`K_SHINGLE = 3`): en esas escrituras son
     trigramas de caracteres, lo habitual para medir parecido en CJK. Medido
     con dos textos japoneses de 40 frases con una palabra cambiada en 3: la
     Jaccard pasa de **0,617 a 0,970** (firma: 0,617 → 0,977) y dos textos
     distintos siguen en 0. Nueve frases japonesas eran nueve «palabras» y no
     tenían firma (quedaban «no medidas», no «sin duplicados»); ahora sí.
   - **El mínimo.** Un carácter dice menos que una palabra (una palabra china
     tiene entre 1,5 y 2 caracteres de media), así que cuenta como media
     (`CARACTERES_POR_PALABRA = 2`): un texto CJK necesita 60 caracteres para
     tener firma, 25 no la tienen, y un texto mixto suma las dos cosas.
     `MIN_PALABRAS` sigue en 30 —`soft404.py` lo usa como recuento de
     palabras propias (`MAX_PALABRAS_SOLO_TITULO`)—. Sin calibrar: no hay
     ningún censo CJK, tailandés ni hindi en la BD. El tailandés y el jemer
     cuentan también sus marcas como caracteres, así que llegan al mínimo con
     menos palabras que el chino.
   - **El texto latino no cambia de firma.** Sin marcas ni esas escrituras,
     `[\p{L}\p{N}_]` es exactamente el `\w` de `re` (comprobado sobre todos
     los puntos de código asignados), así que da los mismos tokens; un test
     fija el sha256 de la firma de un texto en castellano con la del código
     anterior. Se normaliza a NFC (un texto en NFD, el de macOS, se partía en
     cada tilde) y a NFKC **solo el bloque de ancho completo y medio ancho**:
     NFKC entero cambiaría `1º` por `1o` y `m²` por `m2`, y con eso la firma de
     casi todo texto en castellano. Lo que cambia en latino es lo que estaba
     roto: texto en NFD y la `İ` turca (`lower()` le añade un punto combinante).
   - **Coste**: firmar una página de 400 palabras pasa de 1,0 a 1,37 ms (el
     tokenizador de `regex` es más lento que `re.split`): unos 7 s más en un
     censo de 20.000 páginas.
   - **Al re-analizar un censo ya hecho** (no hace falta re-rastrear: todo sale
     de `page_content`) desaparecen los `near_duplicate_content` de las
     páginas no indexables y externas, sus `near_duplicate_count` y
     `closest_similarity` pasan a NULL (columnas del CSV), y salen de los
     `matches` de las indexables, cuyos recuentos bajan. En un censo latino
     indexable no cambia nada más; en uno CJK, tailandés o hindi aparecen
     firmas y parejas que antes no había. Es exactamente el caso de las
     decisiones 70 y 91: un censo analizado antes de esto no se compara por
     incidencias con uno analizado después, y la referencia de una serie se
     re-analiza sola (93). Ojo con `scripts/near_duplicates.py`: reescribe las
     incidencias de casi duplicados **sin** tocar `jobs.analisis_version`, así
     que un censo pasado por el script sigue diciendo que se analizó con el
     código viejo; si se va a comparar, re-análisis completo
     (`python -m analysis.analyzer <job>`). La firma de la huella del soft 404
     (decisión 105) es la misma función: en textos no latinos su huella ahora
     tiene firma con 60 caracteres.
   - **Fuera**: dos indexables con el mismo texto siguen sacando
     `duplicate_content` y `near_duplicate_content` (similitud 1,0), dos avisos
     por un hallazgo (decisión 36); va aparte, con su medida. Y `word_count`,
     `content_word_count` y `low_word_count` cuentan por espacios: en CJK dan
     cifras absurdas.
   - **Revisión (2026-10-18).** La promesa «el latino no cambia» no se cumplía
     con emojis: una marca `\p{M}` podía EMPEZAR un token, así que el selector
     de variación de `❤️` (U+FE0F) o el keycap de `1️⃣` salían como palabra
     propia y movían los shingles. Ahora una marca solo continúa una palabra,
     solo cuentan las que escriben (Mn y Mc; las envolventes no) y los
     selectores de variación se quitan antes de tokenizar. Lo que sigue
     cambiando en latino es lo que estaba roto: NFD, letras matemáticas con
     tilde combinante y los trozos en japonés o chino dentro de un post.
     Medido con el código de `master` y el de esta rama sobre los mismos datos
     guardados (transacción de solo lectura; la escritura, con ROLLBACK):

     | censo | firmas | avisos | se van por |
     |---|---|---|---|
     | Lopesan | 4.298 → 1.818 | **1.940 → 4** | 1.848 canonicalizadas, 84 indexables cuyas parejas no lo eran, 4 noindex |
     | blogs.uoc.edu | 25.244 → 21.830 | **3.545 → 1.835** | 1.018 canonicalizadas, 474 indexables, 218 noindex |
     | progym | 3.574 → 3.533 | 1.578 → 1.559 | 10 canonicalizadas, 5 indexables, 4 noindex (medido antes de la revisión) |

     Ninguna pareja nueva en los tres (son latinos). Firmas latinas distintas
     de las de `master`: 0 en Lopesan y 93 de 25.151 en blogs.uoc.edu (169 con
     la versión sin revisar). En Lopesan casi todas las canonicalizadas son las
     2.367 que apuntan a `*.lfr.cloud` (decisión 64): no son variantes, pero
     ese hallazgo ya es un `canonical_cross_domain` de gravedad `error`, y un
     aviso por hallazgo (36). Al escribir, las URLs con recuento y no
     indexables pasan de 1.852 a 0, que es la limpieza a NULL funcionando.

<a id="d126"></a>
126. **De qué fichero viene cada URL del sitemap, y los avisos son del
   fichero, no de la URL** (PR 1.3B de `docs/PLAN_MEJOR_CRAWLER.md`). La
   membresía era un conjunto de hashes sin procedencia: `parse_sitemap` tiraba
   el `lastmod` y el fichero, así que ningún aviso podía decir «arregla el
   sitemap de autores» (decisión 100). Ahora el spider lee cada fichero con
   `leer_sitemap` (el `parse_sitemap_entradas` de siempre más el formato, las
   entradas declaradas contadas ENTERAS y el tamaño descomprimido) y guarda:
   - **`sitemap_entries`**: una fila por (fichero, URL interna) con su
     `lastmod` tal cual, por `SitemapEntradasItem` y la tubería. Las mismas
     URLs que marcan `in_sitemap`; las de otro host no entran. Lleva la `url`
     además del `url_hash` porque una URL del sitemap que no se llegó a rastrear
     tiene fila aquí y no en `urls`. Cada proceso de Scrapy vuelve a leer todos
     los sitemaps, también al reanudar, así que la tubería borra las del job la
     primera vez que llega un fichero: reanudar no duplica (test).
   - **`jobs.sitemaps`**: por fichero, código, formato, `declaradas`, `bytes`,
     si se cortó el gzip, hijos, internas, entradas guardadas y `leido_at`; y si
     la vista quedó parcial o recortada por el tope.

   Tres incidencias nuevas (`analysis/avisos_de_sitemaps.py`), las tres con la
   unidad en la que se arreglan, ancladas en la raíz del host del fichero o, si
   no se rastreó, en la URL MENOR de ese host o del censo, y con
   `group_key` del fichero o de la combinación (88):
   - **`sitemap_supera_limite`** (warning, uno por FICHERO): más de 50.000
     entradas o más de 50 MB sin comprimir, los límites de Google para un
     sitemap y para un índice. Los límites son los de Google
     (`shared/sitemaps.py`), no `MAX_URLS_PER_SITEMAP` ni `MAX_SITEMAP_BYTES`,
     que protegen al worker y se tocan por entorno. Para poder afirmarlo el
     parser ya no corta el bucle en el tope: cuenta lo que pasa de él aunque no
     lo devuelva (antes un fichero de 80.000 se leía como uno de 50.000). Un
     gzip que pasa del tope se sigue sin leer, pero ahora se sabe que pasaba.
     `warning` y no `error`: no saca del índice ninguna página (64, 92).
   - **`lastmod_futuro`** (warning, uno por FICHERO, con cuántas, el máximo y
     cinco ejemplos): `lastmod` posterior a la hora a la que se LEYÓ ese
     fichero más **un día** de margen. El margen es por las zonas horarias: un
     sitio en UTC+14 puede escribir «mañana» sin mentir, y una fecha sin hora se
     lee como las 00:00 UTC. Se compara contra `leido_at` y no contra «hoy»:
     re-analizar un censo de hace un año no puede volver futuras fechas que ya
     habían pasado. El `lastmod` se guarda crudo y se juzga en el análisis, así
     que el criterio cambia re-analizando. Lo que no es W3C Datetime
     («18/10/2026») no se juzga: se cuenta en `lastmod_ilegibles`. Es el único
     de los tres cuyo efecto Google documenta: usa el lastmod «si es coherente y
     comprobable», y una fecha futura no lo es.
   - **`en_varios_sitemaps`** (info, uno por COMBINACIÓN de ficheros, con
     `paginas_afectadas`, cuántas de ellas están en el censo, ejemplos y cuántas
     llevan un `lastmod` distinto en cada fichero): Google no lo documenta como
     error y las procesa igual, de ahí `info`. Lo que sí es un hallazgo es el
     `lastmod_distinto`: el sitio se contradice sobre cuándo cambió la página.
     Por combinación y no por URL por lo mismo que las imágenes (64b) y los
     datos estructurados (73): que el sitemap de páginas y el de entradas de un
     WordPress repitan 63 URLs es UN ajuste del generador. Se ancla en la URL
     afectada MENOR que esté en el censo; si ninguna lo está, en el host del
     primer fichero. Por URL y no por id (como la decisión 108): el id depende
     del orden de rastreo, y con él la comparación entre censos (88) daba el
     mismo hallazgo por «resuelto» y «nuevo» a la vez (test). Con más de 200 combinaciones se avisan las 200
     mayores y el resto va al log.

   Sin `jobs.sitemaps` (censo anterior) no se emite nada, y sin la tabla
   tampoco: «no se sabe» no es «sitemap limpio». Una URL repetida dentro del
   MISMO fichero no cuenta (el parser ya las deduplica), y dos formas que
   normalizan igual son una sola entrada.

   **Tope de filas**: `SITEMAP_ENTRIES_MAX` = 250.000 por proceso de rastreo.
   Pasado el tope se sigue leyendo el sitemap (membresía, semillas,
   `jobs.sitemaps`) pero no se guardan más entradas, y
   `jobs.sitemaps.entradas_recortadas` lo dice; `en_varios_sitemaps` lo lleva en
   el detalle porque su cuenta pasa a ser un mínimo. Medido: **464 bytes por
   fila** (127.006 filas sintéticas con la forma de las de penguin en un
   Postgres 16 de pruebas: 59 MB, de ellos 21,7 MB del índice `(job_id,
   url_hash)`), así que el tope son ~116 MB por censo, frente a los 49 GB de
   `links`. El mayor sitemap medido tiene 127.006 entradas (penguin): el tope es
   el doble. El análisis de los tres avisos sobre esas 130.000 filas tarda
   **0,8 s**. La tabla entra en `TABLAS_POR_JOB`, en el borrado en masa (94) y en
   el backup (con `jobs.sitemaps`, que la importación no copiaba).

   La ficha de una URL (`GET /urls/{id}`) lleva `sitemaps`: `[{fichero,
   lastmod}]`, por el índice `(job_id, url_hash)`, y la interfaz lo pinta en las
   dos fichas («Sitemap: … · lastmod …»).

   **Medido el 2026-10-18** leyendo con el parser nuevo el árbol de sitemaps
   actual de siete sitios de la BD local (GET secuenciales con la huella del
   rastreo, sin rastrear nada; `progym` respondió 403 al sitemap y no cuenta):

   | sitio | ficheros | entradas internas | mayor fichero | `supera_limite` | `lastmod_futuro` | `en_varios_sitemaps` |
   |---|---|---|---|---|---|---|
   | penguin | 22 | 127.006 | 39.986 URLs · 10,2 MB | 0 | 0 | 0 |
   | Druni | 18 | 45.968 | 5.000 · 1,0 MB | 0 | 0 | **3** (3 URLs, 2 con lastmod distinto) |
   | www.uoc.edu | 14 | 15.636 | 10.766 · 4,1 MB | 0 | 0 | 0 |
   | blogs.uoc.edu | 399 | 20.915 | 1.003 · 0,5 MB | 0 | 0 | **42** (109 URLs, las 109 con lastmod distinto) |
   | Saunier Duval | 4 | 1.496 | 1.134 · 0,2 MB | 0 | 0 | **1** (la home) |
   | Lopesan | 396 | 64 | 395 hijos · 66 kB | 0 | 0 | **1** (`/es/`) |

   Ningún sitio pasa de los límites ni tiene lastmod futuro: son avisos que en
   un sitio sano no salen, y salen las cifras que había que comprobar (que no
   hay falsos positivos por zona horaria ni por formato: 0 ilegibles en
   211.085 entradas). En blogs.uoc.edu la agrupación es la que se buscaba: 109
   URLs son **42 avisos**, el mayor de 63 páginas (`extreballadors`, el
   `page-sitemap` y el `post-sitemap` de Yoast declarando las mismas).

   Lo que salió por el camino y queda **fuera** (no hay aviso todavía, pero el
   dato ya se guarda en `jobs.sitemaps`):
   - **La pista de la decisión 100, resuelta**: Lopesan tiene un índice de
     Liferay con 395 hijos y **370 responden 200 con el cuerpo VACÍO** (0
     bytes). La membresía estaba bien: el sitemap de verdad son 64 URLs. Es un
     hallazgo del sitio («sitemap vacío»), no un fallo nuestro.
   - **Hijos que no son un sitemap**: el índice de www.uoc.edu declara
     `https://www.uoc.edu/en` (una página HTML), y en dos lecturas del mismo
     día el de penguin declaraba hijos en `url.us.m.mimecastprotect.com`
     (enlaces reescritos por el antivirus de un correo, que devuelven HTML); a
     la tercera lectura ya no (decisión 13). Los dos salen en `jobs.sitemaps`
     con `formato: desconocido`.
   - `changefreq` y `priority` no se guardan: Google los ignora.
   - El hreflang de los sitemaps (`<xhtml:link>`) se parsea pero sigue sin
     llegar a la tabla `hreflang`.

   Hace falta **re-rastrear**: los censos anteriores no tienen ni la tabla ni
   `jobs.sitemaps`. No se pudo medir sobre un rastreo nuevo de Lopesan (no se
   lanzan rastreos en producción desde aquí): es el canario de después del
   merge.

   **Revisión (2026-10-18), dos restos:**
   - **Reanudar sin entradas dejaba las del proceso anterior.** La tubería
     borra las del job con el PRIMER fichero que trae entradas; si al reanudar
     ninguno trae (el sitemap ya solo lista URLs de otro sitio, o responde
     404), no se borraba nada mientras `jobs.sitemaps` se sobrescribía con lo
     leído ahora, y los avisos cruzaban los ficheros de un proceso con las
     entradas de otro. Ahora el spider, al escribir `jobs.sitemaps` con cero
     entradas guardadas en este proceso, borra las del job en la misma
     transacción (en un SAVEPOINT: sin la tabla, el resumen se guarda igual).
     Test contra Postgres con el arnés real:
     `test_al_reanudar_no_quedan_entradas_del_proceso_anterior` (sitemap solo
     externo, 404 y, de control, con entradas); los dos primeros fallan sin el
     arreglo.
   - **`lastmod_futuro` no decía que su cuenta es un mínimo** con el tope de
     entradas alcanzado, como sí lo decía `en_varios_sitemaps`. Lleva
     `entradas_recortadas`, y por FICHERO: es `true` solo si de ese fichero se
     guardaron menos entradas internas de las que tenía (`entradas_guardadas` <
     `internas` en `jobs.sitemaps`), porque un fichero leído entero antes del
     tope da una cifra exacta aunque el censo se recortara después. Sin las dos
     cifras (un resumen anterior) vale la marca global. Tests en
     `tests/test_avisos_de_sitemaps_db.py`.

<a id="d136"></a>
136. **Qué se arregla primero: gravedad × importancia de las páginas, con el
   sitio entero de tope** (Ola 4.3 de `docs/PLAN_MEJOR_CRAWLER.md`). Problemas
   ordenaba por gravedad y luego por número de páginas, así que un error en
   tres etiquetas perdidas iba por delante de un aviso en la home y en todas
   las fichas. `GET /api/jobs/{id}/issues/resumen` devuelve ahora en cada tipo
   `importancia` (la suma de la importancia de las páginas afectadas,
   «páginas equivalentes») y `prioridad` (peso de la gravedad × importancia), y
   con `?orden=prioridad` ordena por ella. La vista de Problemas tiene el
   selector «Ordenar: Gravedad / Prioridad»; por defecto sigue por gravedad
   hasta que el equipo valide el orden.
   - **Importancia de una página, entre 0,1 y 1** (`shared/prioridad.py`):
     `pagerank_score` (0,25 + 0,75 × pr/100), por un factor de clics desde la
     home (hasta 2: 1; 3: 0,85; 4: 0,7; 5 o más, o sin camino: 0,55) y por
     0,85 si el sitemap no la declara. `indexable` **no** entra: muchas
     incidencias graves son la causa de que la página deje de ser indexable, y
     bajarle el peso por eso hundiría justo ese hallazgo. La misma regla está
     en Python y en SQL, y un test compara las dos ejecutando el endpoint.
   - **Cada página cuenta una vez por tipo** (un hreflang sin retorno emite una
     incidencia por idioma). Un hallazgo de muchas páginas (decisiones 53, 64,
     73 y 106) cuenta `paginas_afectadas` veces la importancia de su página de
     anclaje, que es la única que se conoce.
   - **Tope: el sitio entero**, la importancia de todas las páginas HTML
     internas con 2xx. Sin él, los hallazgos de plantilla cuentan las mismas
     páginas una vez por hallazgo: en el censo semanal de Lopesan,
     `image_missing_alt` sumaba 18.527 páginas en un sitio de 3.848.
   - **Cada gravedad pesa diez veces la siguiente** (error 100, aviso 10,
     info 1). Es un juicio, como los topes de la decisión 92. Con tres veces,
     en ese censo un `info` que cubre todo el sitio (`missing_csp`, que no
     afecta al posicionamiento, decisión 53) quedaba por encima de los 51
     canonicals a otro dominio.
   Orden resultante en Lopesan (censo del 14-oct-2026), los diez primeros:
   `structured_data_error` (2.496 páginas por plantilla),
   `structured_data_warning`, `image_missing_alt`, `canonical_cross_domain`
   (51), `enlace_sin_ancla`, `missing_csp`, `url_too_long` (424),
   `not_in_sitemap`, `low_word_count` (249) y `4xx_error` (35). Por gravedad,
   los diez primeros eran los seis tipos de error y cuatro avisos por número
   de páginas.
   **Punto débil conocido**: una página canonicalizada pasa su PageRank a la
   canónica (decisión 29), así que la que tiene un canonical a otro dominio
   puntúa baja justo por el problema que se avisa (media 0,55 en esas 51). No
   se compensa todavía: haría falta la importancia de la canónica, y eso es
   otro criterio que medir. **Pendiente** de la 4.3: que el equipo SEO valide
   el top 10, el campo `tipo` (problema, oportunidad o aviso) de
   `incidencias.js` y, cuando exista Search Console (4.1), los clics en la
   importancia.

<a id="d142"></a>
142. **Search Console cruzado con el rastreo: ventana de 90 días, lo que se
   pierde primero y nada afirmado sin cubrir la ventana** (Ola 4.2 de
   `docs/PLAN_MEJOR_CRAWLER.md`). Con los datos diarios que baja el worker
   (decisión 141), el análisis (`analysis/avisos_de_gsc.py`, después de la
   indexabilidad y de los entrantes) lee los **90 días que acaban el día del
   rastreo**, no «los últimos 90»: re-analizar un censo viejo da las mismas
   cifras que el día que se hizo.
   - **Columnas** `urls.gsc_clics`, `gsc_impresiones` y `gsc_posicion` (media
     ponderada por impresiones), emparejadas con `clave_url` (decisión 39). Una
     URL del censo sin fila en Search Console es **0** si los datos cubren la
     ventana entera y **NULL** si no: «0 clics» y «no se sabe» no son lo mismo.
     En el Explorador, grupo «Search Console (90 días)» y vista «Tráfico de
     Google»; en el CSV de la vista.
   - **Cinco incidencias**, por consecuencia: `trafico_en_pagina_perdida`
     (error: clics y hoy 4xx, 5xx, noindex o bloqueada), `trafico_en_redireccion`
     (info: normal tras una migración, señal si dura), `huerfana_con_trafico`
     (warning: una página que el análisis ya marcó como huérfana —`orphan_page`
     o `sitemap_orphan`, con sus exclusiones— y que recibe clics; sustituye a
     aquel aviso, decisión 36. Con reglas propias salían 739 en Lopesan: 828 de
     sus 953 indexables sin entrantes son semillas de un censo sembrado con
     3.040 URLs, y el resto destinos de 301), `indexable_sin_impresiones`
     (info) y `trafico_en_variante_con_parametros` (warning: Google muestra la
     URL con `?partner=` o `?utm_` y el tráfico se reparte entre duplicados; a
     partir de 10 clics y el 20 % del de la página). **«Sin impresiones» solo se
     afirma si los datos empiezan al principio de la ventana y Google no
     recortó ningún día en el tope de filas** (decisiones 67 y 141).
   - **`jobs.gsc_resumen`**: ventana, clics e impresiones, cuántos caen en URLs
     del censo y en variantes, y las URLs con clics que el rastreo **no
     encontró** (las 25 primeras): huérfanas de verdad o fuera del alcance; no
     se afirma cuál. Tarjeta en la ficha del rastreo.
   - **Prioridad** (decisión 136): los clics multiplican la importancia por
     tramos (1, 10, 100 y 1.000 clics: ×1,1, ×1,3, ×1,6 y ×2). Por tramos y no
     con un logaritmo para que la misma regla valga en SQLite; sin datos, nada
     cambia.
   - **Informe** (decisión 138): sección «Tráfico de Google» con los clics de
     la ventana, las páginas con tráfico que se pierde (y cuántos clics), las
     huérfanas con tráfico, las variantes y lo que el rastreo no encontró.
   Medido en el censo semanal de Lopesan del 14-oct con los 90 días reales de
   su Search Console (293.327 filas, copiados en solo lectura a una base local;
   el paso tarda 0,5 s): **583.680 clics** en la ventana, 411.667 en URLs del
   censo y **164.593 —el 28 %— en variantes con `?partner=` de 18 fichas de
   hotel** (la de Costa Bávaro: 37.776 clics en variantes); **952 URLs con
   clics que el rastreo no encontró** (7.420 clics: un `?utm_` de Google
   Business Profile, `/en/login`, PDFs de menús); **231 páginas canonicalizadas
   o que redirigen y que Google sigue mostrando** (una oferta canonicalizada con
   1.649 clics: Google no está aceptando ese canonical); 47 indexables sin
   impresiones; 1 página con tráfico que hoy da 404; y 0 huérfanas con tráfico,
   porque el análisis no marca ninguna huérfana en ese censo.

<a id="d144"></a>
144. **La inspección de URLs de Google, por muestra diaria: lo que el rastreo
   no puede saber, y sin confundir lo que Google vio con lo que hay hoy** (Ola
   4.4 y combinaciones 12-14 de `docs/INVESTIGACION_SEARCH_CONSOLE.md`). La
   API de inspección dice, de una URL, si Google la tiene indexada (y por qué
   no) y qué canonical ha elegido. Da 2.000 al día por propiedad y cada una
   tarda unos 7 s (medido con Lopesan), así que es una **muestra rotatoria**
   (`shared/gsc_inspeccion.py`): primero las URLs del último censo con clics
   que no son indexables, después las indexables por impresiones, las
   indexables sin impresiones y el resto; nada inspeccionado hace menos de 7
   días. `SEARCH_CONSOLE_INSPECCIONES_DIA` (500; 0 la apaga), en 6 hilos: 30
   URLs reales en 34 s, unos 10 minutos al día. Va en el mismo hilo del
   worker, después de la descarga diaria; un 401/403/429 corta la tanda y
   queda en `gsc_estado.inspeccion_motivo`. Tabla `gsc_inspecciones`, la
   última por URL, con la cobertura en castellano (`languageCode: es`).
   - **`indexable_no_indexada_por_google`** (warning): el rastreo la da por
     indexable y Google no la tiene, con su motivo («Rastreada: actualmente sin
     indexar», «Google no reconoce esta URL»...).
   - **Lo que Google vio frente a lo que hay hoy.** `userCanonical` es el
     canonical que Google vio en SU última visita, no el de hoy. Si difiere del
     que capturó el rastreo, es **`google_tiene_canonical_antiguo`** (info: se
     arregla con su siguiente visita o pidiendo la reindexación); solo si
     coincide y aun así Google eligió otra URL es
     **`google_elige_otro_canonical`** (warning: Google ignora la señal).
     Medido en Lopesan: fichas de habitación en alemán que Google vio
     canonicalizadas a la versión en castellano, y una a
     `webserver-lopesan-prd.lfr.cloud` —el fallo de octubre ya arreglado—, que
     hoy son autocanónicas. Con una sola regla, el informe habría dicho que
     Google ignora un canonical que la página ya no tiene.
   - El análisis usa las inspecciones de 30 días antes a 14 después del
     rastreo; `jobs.gsc_resumen.inspeccion` cuenta inspeccionadas, indexadas,
     no indexadas y los dos casos de canonical, y el informe lo dice en una
     línea. La tarjeta de Search Console del cliente enseña la última tanda.
   Muestra real de Lopesan (30 URLs): las 10 indexables con más impresiones,
   indexadas; de 10 indexables **sin** impresiones, solo 4 indexadas (3 como
   alternativa de otra, 2 duplicadas con otro canonical elegido por Google, 1
   que Google no conoce); de 10 con clics y no indexables, 9 alternativas bien
   resueltas y la oferta canonicalizada a `lfr.cloud` indexada igualmente con
   la URL de Lopesan.

<a id="d157"></a>
157. **Search Console: lo que puede traer tráfico, medido contra la curva del
   propio sitio y sin contar como competencia lo que es la misma página**
   (#166, Ola 4 de `docs/PLAN_MEJOR_CRAWLER.md`). Tras lo que cuesta tráfico
   (142), las oportunidades.
   - **Datos nuevos, aparte de la serie de páginas** (`shared/gsc_detalle.py`):
     búsqueda × página por día (`gsc_consultas`, sin la URL en texto ni índice
     único: ~29.000 filas al día en Lopesan, **558 MB los 90 días**) y los
     sitemaps que Google tiene enviados (`gsc_sitemaps`). Con su propia serie en
     `gsc_estado.detalle`, de modo que se rellena hacia atrás aunque las páginas
     ya estén bajadas; mismo solape y tope de filas que las páginas. Una vez al
     día en el hilo de Search Console del worker; un error espera 6 h. Se borra
     lo que pasa de `SEARCH_CONSOLE_DETALLE_DIAS` (180). Por **dispositivo no**:
     se probó, y la curva por página la deforman los enlaces de sitio (CTR
     mediano del 0,1 % en posición 2 en escritorio en Lopesan) y sin la búsqueda
     no se pueden quitar las de marca.
   - **`oportunidad_posicion`** (info): indexable en 4-20 con impresiones de
     sobra y poco enlazada, **relativo al sitio**: PageRank en su cuartil bajo
     (incluido el empate en el cuartil, salvo que meta a más de la mitad: es un
     entero 0-100 con muchos empates), o más lejos de la home que su cuartil
     alto (y al menos 4 clics). Con valores fijos salían 1.503 en Lopesan: el 59 % de sus
     indexables está en 4-20 con más de 500 impresiones y la mitad a 5 clics o
     más. Se avisan las **100 con más clics por ganar** si llegaran al CTR
     mediano de las páginas del sitio en el top 3. Propone hasta cinco fuentes:
     indexables, con más PageRank, del **mismo idioma** (tramo de la ruta), que
     no la enlazan ya (una lectura de `links` filtrada por job, 37 y 41), y
     primero las que salen por sus mismas búsquedas; si no, de su plantilla.
   - **`canibalizacion_por_consulta`** (una por búsqueda, `group_key` de la
     búsqueda): dos o más URLs con al menos el 20 % de los clics de una búsqueda
     con 10 o más (o de las impresiones de una sin clics con 1.000 o más).
     **Antes se junta lo que es la misma página** (union-find): canonical y
     redirección, la variante con parámetros y su URL limpia (eso es
     `trafico_en_variante_con_parametros`) y las alternativas de hreflang
     (Google enseña a cada usuario la suya). Sin esto, las 15 búsquedas con más
     clics de Lopesan eran la ficha de un hotel con `?partner=` frente a la
     limpia, o en /es/ frente a /en/. Solo compiten URLs **del censo con 200**:
     una que solo está en Search Console puede ser la vieja de un cambio de slug
     con 301 a mitad de ventana, que precede a la nueva y no compite con ella. La
     home en búsquedas de marca (`marcas` en la configuración) no cuenta. Se
     ancla en la URL menor del grupo, no en la de más clics, que es justo lo que
     se invierte en una canibalización. Gravedad por consecuencia (92): warning desde
     100 clics, info por debajo. No decide fusionar o diferenciar (#18).
   - **`ctr_bajo_para_posicion`** (info): la curva de CTR por tramo de posición
     sale de las filas búsqueda × página con 50 impresiones o más, sin las de
     marca si están declaradas. Para cada página, lo esperado es la suma de
     impresiones × CTR mediano del tramo de **cada** búsqueda suya; salta si
     tiene menos clics que el cuartil bajo así sumado **o que la mitad de lo
     esperado**, y le faltan 20 o más. Solo el cuartil bajo, como pedía la
     issue, no sirve en un sitio con mucha cola larga sin clics: en Lopesan
     (marcas fuera) el cuartil bajo es 0 en casi todos los tramos y no salía
     ninguna. Tope de 100. Lleva al lado las incidencias de snippet de la
     página y no afirma la causa: puede ser la SERP, que no vemos.
   - **Sitemaps según Google**: `sitemap_no_enviado_a_google` (info: la API solo
     lista los enviados, así que **no se afirma que Google no lo lea**: puede
     leerlo del robots.txt), `sitemap_no_leido_por_google` (warning: enviado y
     sin descargar en los 30 días anteriores al censo) y
     `sitemap_con_errores_en_google`. Solo los índices que leyó el rastreo (o
     todos si no leyó ninguno): los hijos de un índice no salen en la lista de
     Google. Sin la lista de Google, nada.
   - **Fuera de la comparación de incidencias** (`comparacion.NO_COMPARABLES`):
     oportunidades, CTR bajo y canibalización dependen del tráfico de la ventana
     y de un ranking con tope; que una pase del puesto 100 al 101 no es «resuelta».
   - **Que no tumbe nada**: el paso va en un savepoint con su try entero (74); un
     `rollback()` a mitad de `run_all` deshacía las incidencias y el PageRank ya
     escritos. Los href del HTML se convierten en clave sin lanzar
     (`https://[lang].x.com/`, puertos imposibles). La descarga guarda el motivo
     de cualquier error para no reintentar cada minuto, y no impide la
     inspección del día. Solo las propiedades activas del cliente: una vieja que
     sigue en la base contaría los clics dos veces. «No leído» exige además que
     se enviara hace más de 30 días.
   - **Rendimiento por plantilla y por profundidad**, y la curva, en
     `jobs.gsc_resumen.oportunidades` y en la tarjeta de Search Console del
     rastreo.
   **Medido** (23-oct-2026, censo de Lopesan `1e7ea651` del 21-oct en una base
   local, con sus 90 días reales de búsquedas bajados de Google en solo
   lectura): 100 oportunidades, de las que se revisaron las 10 primeras con sus
   fuentes (todas existen, indexables con 200, y ninguna enlaza ya a la
   página; 74 por PageRank bajo); 459 canibalizaciones (62 warning), las 10 primeras sin pares
   canonical ni hreflang: la ficha de un hotel frente a su galería en
   «… fotos», la de spa frente a la del hotel; 30 páginas con CTR bajo con
   `marcas: ["lopesan"]` (un artículo del blog con 60.422 impresiones y 208
   clics) y 4 sin marcas, todas de búsquedas de marca. Ningún aviso de
   sitemaps: el índice que leyó el rastreo Google lo descargó el 20-oct. El
   análisis completo, 18-24 s. Revisado por un agente aparte: nueve hallazgos,
   todos arreglados con su test. Lopesan no tiene `marcas` declaradas: conviene
   ponérselas.

<a id="d161"></a>
161. **WordPress por su REST API pública: lo que el sitio dice que publica
   frente a lo que el rastreo encontró, leído por el spider y afirmado solo en
   un censo entero** (#33).
   - **Dónde se lee: en el spider, a la vez que el rastreo**
     (`crawler/seo_crawler/sonda_wordpress.py`), no en el worker antes del
     análisis ni, nunca, en un request de la API. Así cada petición hereda lo
     que el rastreo ya hace y una descarga aparte tendría que repetir: UA y
     huella TLS del navegador (11, 132; un WAF tipo F5 corta a `urllib`),
     proxy, cabeceras y credenciales solo hacia el sitio (114), robots.txt en
     `respect`, `Crawl-delay` (81) y `Retry-After` (111). Corre a la vez que
     las páginas y con prioridad sobre ellas, así que en un sitio con ritmo
     normal no alarga el job; con un `Crawl-delay` alto la cola puede seguir
     cuando ya no quedan páginas, y entonces **sí lo alarga, como mucho
     `max_minutos` (20)** desde la primera petición. El tope se mira al
     encolar y al soltar cada petición: la página 1 de un listado encola de
     golpe hasta 1.000, y mirándolo solo al encolar, con `Crawl-delay` 10 esa
     cola seguía unas 2,8 h (y con `max_runtime_hours` bajo el job cerraba
     truncado). Pasado el tope no se suelta nada más y la lectura queda
     `cortada: tope_de_tiempo`. Cada respuesta escribe el
     latido y mira la cancelación como las otras sondas (`LATIDO_S` 60): sin
     eso, 180 peticiones con `Crawl-delay` 10 pasaban de los 30 minutos del
     vigilante y el job se reencolaba como estancado (revisión de #226).
     Como las demás sondas (105, 123, 124): callback propio, no gasta
     `max_urls`, no cuenta para la salud y no es una URL del censo.
   - **Dos peticiones a la vez como mucho, por una cola propia y en el slot
     del HOST**, no en uno aparte (`DOWNLOAD_SLOTS`): un slot propio sumaría
     dos peticiones más al sitio y se saltaría su `Crawl-delay`, que se
     guarda por slot. Prioridad 5, para que la lectura acabe pronto y un tope
     de URLs no la corte. Si una petición se pierde sin callback, `spider_idle`
     suelta la cola.
   - **Detección = la petición misma.** Con la respuesta de cada semilla (la
     URL final), se pide `/wp-json/` en su origen y la raíz que anuncie la
     cabecera `Link` o el `<link rel="https://api.w.org/">` (subcarpeta,
     `?rest_route=/`); solo se sigue si responde un índice con `wp/v2`. Se
     prueba `/wp-json/` aunque la home no la anuncie: blogs.uoc.edu no lleva
     el `<link>` y su REST API responde. **No se pide al arrancar** porque el
     `RobotsTxtMiddleware` de Scrapy 2.19 da el parser solo a las DOS primeras
     peticiones que esperan el robots.txt de un host y `None` (todo permitido)
     a las demás: medido en el arnés, con `Disallow: /wp-json/` la petición
     desde `start_requests` salía igual. Al llegar la respuesta de la semilla,
     el robots.txt de su host ya está leído. (El fallo de Scrapy lo arregla
     la decisión 164 para todas las peticiones; pedir desde la semilla se
     queda porque además es cuando se ve la raíz que anuncia.)
   - **Qué se lee**: el índice (nombre, home, `show_on_front`, namespaces →
     plugin SEO), `wp/v2/types` y `wp/v2/taxonomies` para saber qué tipos y
     taxonomías hay (fuera los internos del núcleo: menús, patrones,
     plantillas, fuentes), cada tipo con `per_page=100` y `_fields` mínimos,
     **paginando por `X-WP-TotalPages`** (más allá es un 400, y una página
     puede venir con menos de 100 aunque haya más: en wordpress.org/news,
     `media?per_page=1` devuelve `[]` con 1.685 en `X-WP-Total`), los medios
     hasta `max_medios` (1.000; el total sale de la cabecera) y los términos
     ordenados por `count`. Nunca `/wp/v2/users`. Topes en
     `adapters.wordpress` del `JobConfig` (`max_peticiones` 1.000,
     `max_entidades_por_tipo` 20.000). Un 429 o 503 (tras los reintentos de
     Scrapy) para la lectura entera, también en una sonda; en el índice es
     «limitada» (no se sabe si está abierta), y un 401/403/404 o un HTML es
     «cerrada». El rastreo sigue. Una lista (`max_depth` 0) no la lee.
   - **Solo el sitio** (como las sondas de la 123, `shared/red.py`): una raíz
     anunciada o un salto solo se piden si son internos del job, y si su host
     es de red privada, solo si es un host del job tal cual (la semilla o
     aquel al que saltó). Las redirecciones no las sigue Scrapy: se siguen a
     mano (tres como mucho) con esa misma comprobación, y la raíz pasa a ser
     la del destino con su ruta (`/wp-json/` → `/es/wp-json/` con WPML). Una
     raíz anunciada fuera del sitio (un WordPress headless en `cms.x.com`) se
     anota en `intentos` como `raiz_fuera_del_sitio` y se prueba `/wp-json/`
     del origen; si tampoco responde, `rest: "raiz_fuera_del_sitio"`, no
     «cerrada». Si la semilla no anuncia raíz, se prueba antes la que confirmó
     una ejecución anterior. Un
     `<link rel="https://api.w.org/" href="http://169.254.169.254/">` no se
     pide.
   - **Dónde va**: `adapter_entities` (una fila por entidad y por sonda, sin
     FK a `urls`: que no tenga fila allí es el hallazgo; se empareja al
     analizar por `(job_id, url_hash)` o por `url_hash_alt`, la misma URL con
     el otro esquema) y el resumen en `jobs.adapters["wordpress"]`. **Una
     reanudación no borra lo leído al arrancar**: si la ejecución anterior la
     leyó entera (`completa`) no se repite; si no, se vuelve a pedir empezando
     por la raíz que confirmó (`raiz`: una subcarpeta o `?rest_route=/` no se
     ven sin la semilla), y lo anterior se borra solo cuando ESTA ejecución
     confirma el índice. Si no llega a confirmarlo, se queda el resumen y las
     entidades de la anterior con una nota (`reanudacion`), nunca
     «no detectado». `en_alcance` = la URL pasa los filtros del job. En
     `TABLAS_POR_JOB`, `borrar_job` y el backup.
   - **Sondas** (hallazgos 2 y 5), sin seguir redirecciones: hasta
     `muestra_adjuntos` (20) páginas de adjunto, y la página `N+1` de hasta
     `sondas_paginacion` (5) archivos: la home de entradas (solo con
     `show_on_front=posts`) con `N` = `X-WP-Total` de entradas, y los términos
     con más entradas. Una categoría muestra también las de sus hijas, así
     que su `count` no es un tope: solo hojas, y solo si la primera página
     trae todas las categorías.
   - **Los avisos** (`analysis/avisos_de_wordpress.py`, en un savepoint con
     el try entero, 74), todos agrupados (36, 53, 64, 73):
     **`wp_publicado_no_rastreado`** (warning, por host y tipo): publicado, en
     alcance, sin fila en el censo y sin enlace desde ninguna página rastreada
     (o solo nofollow). **Solo en un censo entero** (67, 70, 89): ni truncado
     (`MOTIVOS_TRUNCADO`) ni cortado por `max_depth` (alguna URL llegó a la
     profundidad máxima: lo que cuelga de ella no se vio; con el valor por
     defecto, 3, es lo normal en un blog) ni con filtros de URL
     (`exclude_patterns`, `include_patterns`, `url_filters`): pueden dejar
     fuera la paginación o los archivos desde los que se llega a una entrada
     que sí pasa el filtro (70). En esos casos la cifra va al
     resumen (`no_afirmable`) y la lista a `GET /wordpress/entidades`, sin
     incidencia. Lo rastreado y sin entrantes no se repite: ya es
     `orphan_page`. Un tipo propio solo cuenta si el rastreo encontró alguna
     entrada suya (los autores invitados de Co-Authors Plus tienen REST API y
     no página). **`wp_adjunto_indexable`** (warning, por host): del censo o
     de la muestra, con `estado_indexabilidad` (39): en blogs.uoc.edu las
     páginas de adjunto dan 200 con canonical al fichero, y eso no es
     indexable. **`wp_taxonomia_anemica`** (info, por host y taxonomía):
     archivo rastreado e indexable con `wp_taxonomia_anemica_max` (1)
     entradas o menos. **`wp_paginacion_sin_fin`** (warning si alguna es
     indexable, si no info, por host): la página N+1 responde 200 en HTML;
     si el host tiene soft 404 de serie (105) ya lo dice el soft 404.
   - **El hallazgo 4 (sitemap frente a contenido) no se hace**: en la
     dirección que se puede afirmar (indexable y fuera del sitemap) ya es
     `not_in_sitemap`; para lo no rastreado no se sabe si es indexable (Yoast
     quita del sitemap lo que lleva noindex, y eso es correcto).
   - **Medido** (24-oct-2026), con el spider de verdad (curl_cffi y huella
     de Chrome, robots.txt en `respect`) en local contra tres WordPress
     públicos, a una petición por segundo (`DOWNLOAD_DELAY` 1, una a la vez
     por host) y con un tope de 30-40 URLs; la base, la de pruebas:
     **wordpress.org/news** (raíz anunciada `/news/wp-json/`; la del origen
     es otro WordPress, el de wordpress.org): 63 peticiones del adaptador,
     1.112 entradas de 1.112 (`X-WP-Total`), 2 páginas, 86 de un tipo propio
     (`podcast`), 22 categorías, 272 etiquetas, 966 medios leídos de 1.685
     (tope de 1.000 en 10 páginas, que vienen cortas), cuatro tipos de Jetpack
     con 401 (anotados, sin error). Las 20 páginas de adjunto de la muestra:
     301 al fichero. Las 5 sondas de paginación (p. ej.
     `/category/releases/page/485/` con 484 entradas): 404. **rankmath.com**
     (sin cabecera `Link`; `<link>` en el head): Rank Math detectado, 72
     peticiones, 327 entradas, 66 páginas, 1.325 de tipos propios (base de
     conocimiento, changelog, productos), 557 medios leídos de 29.554; 20
     adjuntos con 301 y 5 sondas con 404. **blogs.uoc.edu** (Yoast, sin
     `<link>` ni cabecera en la home): detectado por `/wp-json/` en el origen,
     33 peticiones, 6 páginas, 51 medios; las 20 páginas de adjunto responden
     200 con `index, follow` y canonical al fichero: no indexables, sin aviso.
     Ningún aviso emitido en los tres, como debe ser: los censos de prueba
     están truncados (`no_afirmable`: 1.074, 363 y 5 publicadas sin afirmar)
     y ninguna sonda dio 200. Lo que dicen los cuatro avisos cuando saltan
     está ejecutado en `tests/test_avisos_de_wordpress_db.py`, y la lectura,
     contra un WordPress falso con dos peticiones a la vez como mucho, en
     `tests/test_wordpress_spider.py`. Falta verlos en un censo entero de
     producción.

<a id="d168"></a>
168. **La interfaz del adaptador WordPress (decision 161), y los plugins y el
   tema por la ruta de sus ficheros, con el `?ver=` solo cuando tiene forma
   de version** (#33).
   - **La casilla «Analizar WordPress (REST API)»** del modal Nuevo rastreo
     (pestana Spider, junto a las sondas): marcada es `auto`, sin marcar es
     `off`. Como las sondas (123, 124), **solo se manda si se cambia**
     respecto a lo que cargo el modal: sin tocarla, el job hereda del cliente
     y el perfil; desmarcarla frente a un perfil que la tiene manda `off`, y
     marcarla frente a un `off` heredado manda `auto`. Se manda solo
     `adapters.wordpress.modo`: `fusionar_config` es profunda y los topes del
     perfil siguen. Un `on` heredado se pinta marcado y no se repite. Al
     volver a «Sin cliente» se compara con lo de serie (el servidor ya no
     hereda): lo que se ve es lo que se lanza.
   - **La tarjeta «WordPress» del Resumen sale solo si el job trae
     `jobs.adapters.wordpress` y hubo rastro de WordPress** (`detectado` o
     alguna `evidencia`, p. ej. el meta generator con la REST API cerrada). Un
     rastreo anterior o con el adaptador apagado no la pinta, y que no salga
     no dice «no es WordPress». Lee el resumen tal cual, sin una consulta
     mas: version, plugin SEO, estado de la REST API (con el codigo si no
     esta abierta), y por tipo **publicadas** (`X-WP-Total`), **leidas**, **en
     el censo** y **solo en WordPress** (leidas sin fila en el censo, de
     `analisis.por_tipo`). «Solo en WordPress» no es un hallazgo y la cabecera
     lo dice; si el analisis no lo afirmo (`no_afirmable`, decision 67), la
     tarjeta dice cuantas y por que. Sin analizar, «en el censo» es «—», no 0.
     Los plugins con rutas en la REST API salen del primer tramo de cada
     namespace (`yoast/v1`, `wc/store/v1` → `yoast`, `wc`), sin los del
     nucleo, y diciendo que no todos los plugins registran rutas.
   - **Plugins y tema por los CSS y JS** (`GET /wordpress/assets`, bajo
     demanda al pintar la tarjeta): el nombre sale de la carpeta
     (`/wp-content/plugins/<slug>/`, `/wp-content/themes/<slug>/`), y eso es
     fiable: el fichero esta ahi. **El `?ver=` no**: lo pone quien encola el
     fichero, y si no pasa version WordPress pone la suya; WP Rocket sirve
     copias en `/wp-content/cache/` con un numero de cache, y hay `ver=1` y
     sellos de tiempo. Medido en produccion (24-oct-2026) en el blog de
     Lopesan: `jet-engine` solo en copias de cache (`ver=1791341166`),
     `sitepress-multilingual-cms` con `ver=1` y `ver=495000`, `astra-child`
     con sellos de tiempo. Por eso solo cuenta como version un `ver` con un
     punto (`^\d+(\.\d+)+`, con sufijos como `-rc.1`) y fuera de `/cache/`, y si coincide con la de
     WordPress se marca «(la de WordPress)». El plugin sale igual, sin
     version. **En una muestra**: las primeras 2.000 paginas HTML por
     `crawl_depth` (las menos profundas, que cubren las plantillas; a igual
     profundidad, por `id`); con el censo entero, Lopesan (5.700 paginas)
     tardaba 3 s. No por `id` solo: con `ORDER BY id LIMIT`, Postgres puede
     recorrer `urls_pkey` filtrando el job y leer las filas de todos los jobs
     con ids anteriores (en produccion, 20.891 de otro job para un censo
     grande), un coste que crece con la base y no con el censo; sin indice
     que de el orden por profundidad, entra por `job_id` y ordena lo del job. Los ids se piden
     primero y luego `url_id IN (...)` por lotes de 1.000: con la subconsulta
     dentro, Postgres estimaba mal y recorria `resources` entera (9,7 M
     filas, 2,6 s); con la lista va por `ix_resources_url_id` (0,75 s). Lo
     que no sale no se afirma que no exista (un plugin sin CSS ni JS, uno que
     solo carga en una plantilla fuera de la muestra, un `wp-content`
     renombrado), y la tarjeta lo dice.
   - **«Solo en WordPress» en el Explorador**: una entrada propia en la barra
     (solo con la REST API abierta) que cambia la tabla de URLs por un panel
     sobre `GET /wordpress/entidades?solo_no_rastreadas=true`, con filtro por
     tipo, busqueda en la URL y paginas de 100, y en la ruta
     (`#/rastreos/<id>/explorador?wordpress=<tipo|todo>`): se puede pegar. No
     es una pestana mas de `EXP_TABS` porque no son filas de `urls`: el
     filtro, el orden, las columnas y la ficha de la tabla no valen para
     ellas. La cifra de cada tipo en la tarjeta enlaza aqui.
     `solo_no_rastreadas` deja fuera las sondas (`sonda_paginacion`,
     `sonda_adjunto`): son peticiones del adaptador, no entidades del sitio, y
     una pagina N+1 «sin fila en el censo» no es contenido que falte.
   - **La entidad en la ficha de la URL** (`UrlDetailResponse.wordpress`):
     tipo, ID, estado, `modified_gmt` y, en un termino, cuantas entradas
     tiene. Se empareja como el analisis, por `url_hash` o por `url_hash_alt`
     (la misma URL con el otro esquema), sin las sondas, y sin tabla es una
     lista vacia, como los sitemaps (126). `url_hash_alt` tiene su indice
     (`ix_adent_job_hash_alt`, job_id delante): sin el, el OR no podia ir por
     `ix_adent_job_hash` y Postgres filtraba todas las entidades del job
     (decenas de miles en un WordPress grande) cada vez que se abria una
     ficha. La migracion lo crea en `migrar_adapter_entities()`.
   - **Con «Solo en WordPress» abierto, las flechas del teclado no mueven la
     tabla de URLs**, que esta oculta: antes pedian la ficha de una fila
     invisible y bloqueaban el desplazamiento de la lista, y al volver a la
     tabla aparecia abierta una ficha que nadie eligio.
   - Probado con un censo WordPress sembrado en local (capturas antes y
     despues de Resumen, ficha de URL, Explorador y modal) y los endpoints
     contra SQLite y Postgres (`tests/test_wordpress_ui_api.py`); la tarjeta
     y la casilla, ejecutando `app.js` en Chromium
     (`tests/test_wordpress_frontend.py`). No hay todavia un censo WordPress
     analizado en produccion (el unico job con `adapters` es de un sitio que
     no lo es): la tarjeta no se ha visto con datos reales de un cliente.

<a id="d190"></a>
190. **Una plantilla que responde 200 a una URL que no existe es UN aviso
   agrupado por host (`soft_404_plantilla`), aunque ninguna página del censo
   sea la página de error** (revisión de UX del 25-oct, 2.11; V17).

   **Problema.** La sonda de la decisión 105 pide, por host y por plantilla de
   ruta, una URL inventada. Si responde 200, el sitio tiene soft 404 de serie
   en esa plantilla; pero el análisis solo emitía incidencias en PÁGINAS que
   llevaban la huella de la sonda. En Lopesan (censo del 21-oct, analizado el
   24-oct) las 7 sondas de las categorías del blog (`/blog/category/:slug`,
   `/blog/de/category/…`, `/blog/en/category/…`) respondían 200 y ninguna página
   del censo casaba con su huella: el Resumen decía «Soft 404: 0 páginas» en
   verde y Problemas no decía nada. Lo que hay que pedir al cliente —que sus
   categorías devuelvan 404 a lo que no existe— no aparecía en ningún sitio.

   **Decisión.** `analysis/soft404.py` emite `soft_404_plantilla`, `warning`,
   UNA fila por host (decisiones 53 y 64: es un arreglo de servidor o de CMS,
   uno por plantilla), con `plantillas` (plantilla, URL pedida, código y cuántas
   páginas del censo la usan), `plantillas_afectadas`, `paginas_afectadas` (las
   páginas 200 HTML del censo con esas plantillas; con la raíz respondiendo 200,
   todas las del host), `raiz` y `paginas_ejemplo`. Anclada en la URL menor de
   esas páginas (88) con `group_key` por host, y añadida a `ANCLADOS_POR_GRUPO`
   de la comparación: la URL de anclaje cambia de un censo a otro y el hallazgo
   es el mismo.
   - **Solo lo que se puede afirmar** (67): cuenta una sonda con código 2xx que
     no redirige, sin error, que no es un muro (clase de respuesta de la 103 o
     título de bloqueo de la 62). Una que redirige no dice qué respondió el
     destino.
   - **`warning` y no `error`**: ninguna página del censo sale del índice por
     esto; lo que pasa es que cualquier enlace viejo o mal escrito a esas
     plantillas es para Google una página 200 vacía («Soft 404» en Search
     Console) y rastreo gastado.
   - Convive con `soft_404` y `soft_404_candidato`: aquellos son páginas del
     censo que SON la página de error; este es cómo responde el servidor a lo
     que no conoce. El resumen de `jobs.soft404` lleva
     `plantillas_responden_200` y `sondas_responden_200` (las URLs pedidas que
     cuentan). El Resumen y la lista desplegada en Problemas pintan esa lista,
     no las sondas con 2xx a secas: un muro de WAF servido con 200 saldría como
     «plantilla que hay que corregir» justo donde el análisis se niega a
     afirmarlo. Para los censos analizados antes, `resumenSoft404` repite el
     mismo filtro (sin redirección ni error, 2xx, clase de respuesta válida,
     sin título de bloqueo, una por host y plantilla).
   - El tipo va también en `TIPOS_ANCLADOS_POR_GRUPO` de `analysis/soft404.py`,
     que el test de la r9 (`test_analyzer_paginacion_db.py`) compara con
     `ANCLADOS_POR_GRUPO`.
   - **Cuándo se calcula**: en el análisis (la sonda se hace durante el
     rastreo y se guarda en `jobs.soft404.sondas`). Los censos ya analizados
     no lo tienen hasta volver a analizarse; el Resumen lo dice igualmente
     desde las sondas («7 plantillas responden 200…») y deja de pintar «0
     páginas» en verde.
   - En Problemas la fila cuenta **plantillas** (de `plantillas_responden_200`),
     no «1 página», y despliega la lista de plantillas con la URL pedida. Con
     una sección o ruta elegida no se da esa cifra (es del sitio entero, no de
     la sección): la fila dice que el hallazgo no se reparte.

   Tests: `tests/test_soft404_plantilla.py` (el caso Lopesan, lo que no se
   afirma, la raíz, una fila por host y que casa entre censos con otra URL de
   anclaje), ejecutando el análisis contra SQLite.

<a id="d210"></a>
210. **Lo que Search Console no cuenta se dice junto a sus cifras, y la parte de clics sin búsqueda se mide en el sitio**

   **Problema.** #163 pedía decir en la interfaz lo que la API de Google no da.
   El bloque «Google» del Resumen solo avisaba del tope de filas y de la
   ventana incompleta. Se leían clics, impresiones y oportunidades por
   búsqueda como si fueran todo, y no lo son:
   - La API no separa AI Overviews ni AI Mode. AI Mode cuenta dentro de Web.
   - Las búsquedas raras o sensibles van anonimizadas: la suma por búsqueda
     nunca da el total.
   - Hay dos fechas en las que Google cambió lo que cuenta: las impresiones
     infladas del 13-05-2025 al 27-04-2026, sin corregir en el histórico, y
     el fin de `num=100` en septiembre de 2025.

   **Decisión.**
   - **Medido, no supuesto.** El análisis guarda en
     `gsc_resumen.oportunidades.sin_busqueda` la parte de los clics que llega
     sin su búsqueda:
     - Es la diferencia entre `gsc_paginas` (por página, con todas sus
       búsquedas) y `gsc_consultas` (por página y búsqueda, sin las
       anonimizadas).
     - Se cuenta solo donde las dos series cubren lo mismo: por propiedad
       activa, los días que su serie de búsquedas tiene descargados (de
       `primer_dia` a `ultimo_dia`, dentro de la ventana) y sin los días en
       que la serie de páginas se cortó en el tope de filas. La revisión del
       PR encontró dos maneras de inflarla sin estas reglas, y las dos son
       reales:
       - La serie de búsquedas va por detrás de la de páginas (el relleno
         inicial tarda horas): con tres días de retraso salía el 85 % en vez
         del 40 %.
       - Una propiedad sin serie de búsquedas sumaba sus páginas: salía el
         94 %.
     - Si algún día de búsquedas se cortó en el tope de 50.000 filas, la
       diferencia mezcla las dos causas y el texto lo dice
       (`con_dias_cortados`). Por debajo del 1 % se dice que las cifras
       cuadran. Si la medida falla, `None`, y las oportunidades siguen
       (decisión 40).
     - Sin serie de búsquedas no se afirma ninguna parte (`None`), y la
       interfaz da la cifra general del 40-50 % como «en muchos sitios», no
       como la del cliente.
   - **Dicho junto a las cifras**, en un desplegable del bloque «Google»
     («Lo que Search Console no cuenta», `limitesGoogle`):
     - **Siempre:** la IA sin separar y las anonimizadas.
     - **Las dos fechas, solo si la ventana de los datos las cruza.** Un censo
       de octubre de 2026 no las cruza: decírselo sería ruido.
   - **Fuera:** el filtro de marca y no marca de la interfaz de Google, que
     tampoco está en la API. La marca ya la cubre `job.config.marcas`
     (decisión 157).

   Tests:
   - `tests/test_gsc_oportunidades.py`: la parte medida solo en los días de
     las dos series, con la serie de búsquedas atrasada, con una propiedad sin
     serie de búsquedas, con un día de páginas cortado y otro de búsquedas
     cortado, sin serie de búsquedas y cuando la medida revienta.
   - `tests/test_google_limpieza_frontend.py` (`limitesGoogle` en Chromium):
     la cifra medida frente a la general, y cada fecha solo cuando la ventana
     la cruza.

<a id="d213"></a>
213. **Los logs del servidor se suben por cliente, los lee el worker en streaming, solo cuenta el Googlebot cuya IP es de Google, del log solo queda lo agregado de los bots, y no se afirma nada si el log no es del mismo host, de la época del censo o no ve a Googlebot** (#35)

   **Problema.** El rastreo dice lo que SE PUEDE encontrar; el log, lo que
   Googlebot VISITA. Sin él no se sabía qué páginas importantes no visita, en
   qué se le va el presupuesto ni qué visita que el rastreo no encontró. Hay
   tres dificultades:
   - Un log de un mes son gigas.
   - Cualquiera pone «Googlebot» en su user-agent.
   - El log tiene IPs de personas.

   **Decisión.**
   - **Por cliente, como los backlinks (212) y Search Console (141).** Se
     sube en la Configuración del cliente (sección «Logs del servidor»): en
     claro o `.gz` (detectado por los bytes), hasta 5 GB, cortando al pasar el
     tope y no después de copiarlo entero.
     - Se guardan las tres últimas importaciones, y las fallidas se van
       primero.
     - Al borrar el cliente se borran también los ficheros sin leer.
   - **El worker lo lee, no la API.** La API copia el fichero al volumen de
     archivo (`ARCHIVO_DIR/logs`, compartido con el worker; comprobado en el
     VPS) y lo encola en `logs:pending`.
     - El worker lo lee en un hilo, de uno en uno y sin parar la cola de
       rastreos.
     - Lo coge con un UPDATE que mira el estado, así que no lo leen dos
       procesos.
     - **Late mientras lee** (`latido_at`). La recuperación periódica solo
       devuelve a la cola lo que lleva 15 minutos sin latir: en la primera
       versión devolvía cualquier lectura de más de 5 minutos, y otro proceso
       lo habría leído dos veces.
     - Medido: unas 150.000 líneas por segundo. Un log de 1 GB tarda pocos
       minutos.
   - **Del log solo queda lo agregado**: una fila por (ruta, bot, verificado)
     en `log_urls`, con visitas, códigos, primera y última y la parte con el
     Googlebot de móvil.
     - Lo de personas se cuenta y no se guarda.
     - Hay un tope de 400.000 rutas distintas (unos 250 MB: el worker comparte
       memoria con los rastreos). Lo que no cabe se cuenta en `resumen.fuera_del_tope`.
     - Una URL entera de otro host (una petición de proxy) no es de este log
       y se aparta.
     - El fichero se borra al acabar, también con error o si el cliente ya
       no existe.
   - **Formatos.** `combined` (con las comillas escapadas de Apache dentro
     del referer o el user-agent) y una regex propia con grupos con nombre
     (obligatorios `ip`, `url`, `codigo` y `ua`), validada al subir. `common`
     no se ofrece: no lleva user-agent y no hay bot que ver. Las líneas que no
     casan se cuentan y se guardan las 50 primeras **con las IPs tapadas**,
     porque casi siempre son de personas.
   - **Un bot se verifica, no se cree.**
     - Los rangos que publican Google (sus tres ficheros), Bing, OpenAI y
       Perplexity se bajan ANTES de leer. Cada IP se comprueba al leer su
       línea, con caché, así que no se guardan IPs.
     - Solo `False` es «seguro que no». Si de un proveedor no se bajaron todos
       sus ficheros, lo que no casa queda `None` (sin comprobar).
     - También es `None` una IP que no se puede leer (`ip:puerto`, una lista
       de X-Forwarded-For) y un proveedor que no publica rangos (Anthropic).
     - La IPv4 mapeada en IPv6 se lee como IPv4.
     - Solo cuenta el Googlebot verificado. Los falsos se dicen aparte.
   - **Cruzado con el censo** (`analysis/avisos_de_logs.py`), con la misma
     regla de cruce y desempate que los backlinks (`shared.backlinks.IndiceCenso`)
     y **solo un log del mismo host que el censo** (con o sin `www.`):
     - `urls.googlebot_visitas` y `googlebot_ultima`: 0 si el log no la vio,
       NULL sin log de ese host.
     - `jobs.logs_resumen` y el bloque «Googlebot en los logs» del Resumen:
       periodo, cobertura de las indexables, en qué se va el presupuesto, lo
       que visita y no está en el censo, los falsos y los otros bots.
     - `indexable_sin_visitas_googlebot` (warning) y `googlebot_recibe_error`
       (warning), **uno por plantilla** con sus páginas y ejemplos (36 y 53).
       Casan entre censos por la plantilla (`ANCLADOS_POR_GRUPO`) y no se
       comparan como incidencias nuevas o resueltas (`NO_COMPARABLES`).
     - El «cuarto con más PageRank» es el cuarto de arriba **por posición**:
       con empates en una escala entera, `>= p75` podía ser el mínimo y entraban
       todas.
   - **Lo que no se puede afirmar no se afirma** (67, 70, 89):
     - Los dos avisos solo salen si el log es de la época del censo: se solapa
       con él o le queda a 30 días como mucho.
     - La ausencia pide además 7 días de log, 100 visitas de Googlebot
       verificado como mínimo y que el log vea al menos el 10 % de las
       indexables. Detrás de una CDN, con otro host o con una regex que coge
       otra IP, todo sale «falso», y avisar en todo el cuartil alto sería
       culpar al sitio de un problema del log.
     - El resumen dice por qué no se afirma (`no_se_afirma`).

   **Fuera.**
   - Repartir el presupuesto por tipo de página (espera a #12).
   - El DNS inverso de respaldo: si los rangos no se pueden bajar, se dice
     «sin comprobar». Es más honesto que esperar minutos a un resolver lento
     que puede dar falsos negativos.
   - Probarlo con un log real de 1 GB y revisarlo a mano con un cliente.

   Tests:
   - `tests/test_logs_servidor.py`:
     - los bots y su orden, `combined` con comillas escapadas, `.gz`, la regex
       propia y que no hay `common`;
     - la URL de otro host, la verificación (rangos completos, parciales, IP
       ilegible, IPv4 mapeada, sin rangos), las IPs tapadas y el tope de rutas;
     - el procesado de punta a punta (el fichero se borra también con error,
       no se lee dos veces) y la recuperación que no toca lo que late.
   - `tests/test_logs_api.py`: subir y encolar, lo que se rechaza ya, el
     dominio, las tres que se guardan y borrar.
   - `tests/test_logs_db.py`, contra Postgres:
     - las visitas, el resumen y los dos avisos agrupados;
     - el falso que no cuenta y pocos días;
     - el log que no ve a Googlebot, el de otro host y el de otra época;
     - sin logs a NULL.
   - `tests/test_logs_frontend.py`: el bloque del Resumen.
