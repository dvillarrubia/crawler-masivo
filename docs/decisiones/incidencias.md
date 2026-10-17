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
   decisión 107: medía también noindex, canonicalizadas y externas.

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

<a id="d107"></a>
107. **Un casi duplicado se mide entre las mismas páginas que el duplicado
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
