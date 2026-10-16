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

   Fuera de esta decisión (parte B de 1.3): la tabla `sitemap_entries`
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
