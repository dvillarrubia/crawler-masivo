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
