# Decisiones: indexabilidad, robots, canonical y hreflang

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d20"></a>
20. **La indexabilidad la decide el analizador, y repara lo ya rastreado** —
   se calculaba dos veces con dos criterios y las dos columnas acababan
   contradiciendose en el mismo CSV. El spider comparaba el canonical contra
   la URL de PARTIDA de la cadena de redirecciones mientras guardaba la de
   destino: toda pagina alcanzada por un 301 salia "Canonicalised" con un
   canonical identico a su propia URL (1.254 de 34.704 en blogs.uoc.edu, 1.329
   en www.uoc.edu; el 100% llegadas por redireccion). Y `indexable` solo
   existia para HTML con metadatos, asi que 404, 3xx y PDFs quedaban en NULL
   en vez de en `false` — el filtro `?indexable=false` los perdia. Ahora el
   spider compara contra la URL final, y `analyze_indexability` escribe
   `indexable` Y `indexability_status` para TODAS las URLs del job,
   respetando los motivos con codigo exacto del rastreo ("Redirect (301)",
   "Client Error (404)"). Re-analizar un job viejo lo corrige sin re-rastrear.

<a id="d22"></a>
22. **La barra en `meta robots` se avisa, no se interpreta** — hay plantillas
   que escriben `index/follow` y `noindex/nofollow` (69 páginas en el censo de
   Saunier Duval). La sintaxis oficial separa por comas, y Google ignora lo que
   no reconoce, así que ese valor llega como un token desconocido: la página se
   indexa y sus enlaces se siguen. Tokenizar por barra "arreglaría" el parser y
   rompería el informe — marcaríamos como noindex una página que Google sí
   indexa, y el cliente se quedaría creyendo que está fuera del índice. Lo que
   se emite es `robots_invalid_syntax`, y su severidad depende de qué se
   pierde: warning si la directiva ignorada era restrictiva (hay una intención
   que no se cumple), info si era `index/follow` (el comportamiento por
   defecto, no se pierde nada). Encaja con la decisión 7: lo roto se reporta,
   no se filtra.

<a id="d23"></a>
23. **Las reglas robots viven en `shared/robots.py`, no en el extractor** — el
   analyzer y el extractor las necesitan los dos y la imagen de `analysis/`
   solo copia `shared/` y `analysis/`. Tenerlas por duplicado costó un
   desacuerdo silencioso: el extractor tokenizaba (correcto) mientras el
   analyzer hacía `"noindex" in valor` por subcadena, así que con
   `noindex/nofollow` la misma página salía indexable en `indexability_status`
   y no indexable en `urls.indexable`. Arreglado al unificar: gana el
   tokenizado.

<a id="d39"></a>
39. **Una sola funcion de indexabilidad, en `shared/`** (#26) — el spider y el
   analyzer repetian las reglas y discrepaban: 1.254 paginas de un censo eran
   "Canonicalised" para uno e "Indexable" para el otro. `shared/indexabilidad.py`
   tiene `estado_indexabilidad()` y una tabla de 32 casos en
   `tests/test_indexabilidad.py`. Lo que arregla, cada cosa con su criterio:
   robots dirigido a un bot (`<meta name="googlebot" content="noindex">` saca
   la pagina del indice aunque el generico diga `index`), TODAS las cabeceras
   `X-Robots-Tag` y no solo la ultima (`noindex` + `noarchive` salia
   indexable), el canonical de la cabecera HTTP `Link` (se extraia y nadie lo
   miraba), y el 204 —una respuesta sin contenido no se indexa— que el spider
   daba por indexable y el analyzer no.

   Comparar URLs tiene su propia regla, y es SEO, no fontaneria: el puerto por
   defecto, la caja del host y el fragmento NO cuentan; `www` frente a sin
   `www` y `http` frente a `https` SI, porque ahi hay una canonicalizacion de
   verdad. Y el escapado por ciento tampoco cuenta: `intel%C2%B7ligencia` y
   `intel·ligencia` son la misma URL, y no normalizarlo marcaba 83 paginas
   autocanonicas de un censo como "Canonicalised" (pasa en catalan y en
   cualquier idioma con acentos en la URL). Cambio real al unificar: 0,2% de
   las filas en blogs.uoc.edu y 0,7% en www.uoc.edu.

   **Una sola funcion no basta si no recibe los mismos datos** (#26): el
   analyzer reescribe `indexable` en todas las URLs y le llegaba menos que al
   spider. El `<meta name="googlebot">` no se guardaba y el canonical de la
   cabecera `Link` se guardaba sin que nadie lo leyera, asi que una pagina
   noindex solo para Google, o canonicalizada por cabecera, salia bien del
   rastreo y volvia a "Indexable" al analizar. Ahora existe
   `html_meta.meta_robots_googlebot` y el analyzer pasa las dos cosas. Y las
   `X-Robots-Tag` se guardan **una por linea**, no unidas por coma: con la coma,
   `bingbot: noindex` + `googlebot: noindex` se leia como una sola cabecera
   dirigida a bingbot y la pagina salia indexable. El nofollow de pagina usa
   las mismas reglas (`es_nofollow`). **Los censos anteriores no se arreglan
   re-analizando**: el googlebot no se guardo y las cabeceras estan unidas por
   coma; hace falta re-rastrear.

   Lo mismo con los **PDF y demas no HTML**, que solo pueden llevar sus
   directivas en la cabecera (`X-Robots-Tag`, y `Link: rel=canonical`, la unica
   forma de canonicalizar un PDF): el spider no las leia fuera del HTML y salian
   "Indexable". Ahora se leen en todo 2xx y se guardan en una fila de
   `html_meta` con solo esas dos columnas —y solo si hay alguna—, porque sin
   fila el analisis las volvia a deshacer. Los checks de pagina (titulo,
   description, canonical, robots) filtran por `is_html`, asi que esa fila no
   los hace avisar de los PDFs.
   Y **un solo normalizador tambien en el analyzer**: `analyze_canonicals` y
   `analyze_hreflang` buscaban el destino con w3lib a secas (`_norm_url`, cuyo
   docstring prometia igualar la barra final y no lo hacia). Ahora usan
   `clave_url`, la clave de `misma_url`: un canonical a `https://x.com:443/b`
   ya encuentra el 404 de `/b`. Ningun test existente cambio de resultado.

<a id="d48"></a>
48. **Lo que robots.txt prohíbe existe, y el modo auditoría estaba muerto** —
   las URLs que el sitio enlaza y robots.txt bloquea no llegaban a la base de
   datos: el `IgnoreRequest` se descartaba y la URL no aparecía en ningún
   informe, ni bloqueada ni enlazada. Google las lista en Search Console y
   Screaming Frog también: o el bloqueo es un error sobre contenido que debería
   posicionar, o es intencionado y entonces son enlaces internos gastando
   presupuesto de rastreo. Ahora se guardan con `status_code` NULL (no se
   pidieron; un 0 en el CSV se lee como «respondió 0»), `status_group="blocked"`
   e `indexability_status="Blocked by robots.txt"`, y el worker distingue
   «filas» de «filas con respuesta» para que un `Disallow: /` siga sin pasar por
   un rastreo correcto (#37). Y el modo `audit` no funcionaba en absoluto:
   `RobotsAuditMiddleware` replicaba a ojo el `__init__` del middleware de
   Scrapy y se dejaba `self._stats`, así que la **primera** petición moría con
   `AttributeError`, el rastreo se quedaba a cero páginas y el vigilante lo
   mataba por estancamiento media hora después. Hay un test que compara los dos
   conjuntos de atributos, de modo que si una versión de Scrapy añade otro lo
   canta el test y no un rastreo de 40 minutos.

<a id="d56"></a>
56. **`rel` es una lista de tokens, y lo que Google ignora hay que decirlo** —
   el canonical, la paginación y el hreflang se buscaban comparando el atributo
   `rel` entero y en minúsculas, así que `rel="Canonical"`, `rel="canonical "` y
   `rel="alternate canonical"` —los tres válidos y los tres escritos por CMS
   reales— daban **None**: la página salía sin canonical y, con ella, sin
   `canonical_missing` tampoco donde debía. Ahora se compara por token entero
   (`canonicalize` no cuenta), `rel="Next"` y `rel="previous"` valen, y
   `http-equiv="REFresh"` también: ese se probaba con tres variantes escritas a
   mano, de modo que `extract_meta_refresh` decía que no había meta refresh
   mientras `extract_meta_refresh_target` sí veía el destino — las dos funciones
   contradiciéndose sobre la misma página.
   Lo que Google ignora ahora se reporta en vez de taparse: un canonical en el
   `<body>` (`canonical_in_body`) y **varios** canonicals (`canonical_multiple`,
   con los que Google los descarta todos y elige por su cuenta) se quedaban en
   «el primero manda» en silencio; hacen falta `html_meta.canonical_count` y
   `canonical_in_body` para poder verlo. Un canonical a `http` desde una página
   `https` es ahora `canonical_a_http`. Y al revés: de `www.x.com` a `x.com` ya
   no es «canonical a otro dominio» —es el mismo sitio resolviendo su variante,
   que es para lo que existe el canonical—, aunque en los tres censos medidos no
   había ni un caso, así que es corrección de criterio y no una cifra.
   El canonical de la cabecera `Link` se parseaba cogiendo el **primer** enlace
   de la cabecera fuera el que fuera: con `</style.css>; rel=preload, <…>;
   rel="canonical"` se guardaba la hoja de estilos como canonical de la página.
   Es la única forma de declarar canonical en un PDF.
   En hreflang: `X-Default` se marcaba como idioma inválido por la caja (lo
   escriben media docena de plugins de WordPress), `en-UK` pasaba por válido
   cuando el código de Reino Unido es `GB` —Google ignora la anotación entera y
   ese idioma se queda sin hreflang—, y un destino que no responde 200 generaba
   **dos** avisos del mismo hallazgo (`hreflang_broken_target` y
   `hreflang_missing_return`): medido, 282 + 38 + 44 páginas con los dos a la
   vez. Si el destino está roto, el retorno no se sabe; lo que hay que arreglar
   es el destino. `uk` como IDIOMA sigue siendo válido: es ucraniano.
   También: `name=" robots"` con un espacio delante no se detectaba, y un
   `<meta property="description">` —que es RDFa, no una meta description—
   contaba como la description de la página.
   Y tres cosas que quedaban (#26): un **hreflang en el `<body>`** se guarda
   marcado (`hreflang.en_body`) y da `hreflang_in_body` una vez por página, sin
   contar como retorno de nadie ni generar más avisos —Google no lo lee—; el
   **idioma** se valida contra ISO 639-1 y no solo por la forma (`eng`, `xx` y
   `sp` pasaban; se aceptan los retirados que Google usa, como `iw`); y **dos
   `<title>` o dos meta description** —vacíos incluidos, que es como llegaba
   el caso típico de dos plugins escribiendo la cabecera— dan `title_multiple` /
   `description_multiple` (`html_meta.title_count`, `meta_description_count`).
   El valor guardado no cambia. Las tres necesitan **re-rastrear**.

<a id="d99"></a>
99. **Hreflang completo, y solo con lo que Google lee: el de una página no
   indexable no cuenta** (PR 1.2 del plan, `docs/PLAN_MEJOR_CRAWLER.md`).
   `analyze_hreflang` emite cinco tipos más, todos sobre fuentes indexables
   y todos desde diccionarios montados con UNA consulta de URLs (estado,
   `indexable`, `indexability_status`, `redirect_url` y `canonical_href`) y
   una de anotaciones: nada por anotación y nada por pareja de páginas.
   - **El clúster** de una página es el conjunto de URLs que anota más ella
     misma, con la clave de `misma_url`. En un clúster bien hecho todas sus
     páginas declaran el mismo conjunto, así que les sale la misma clave sin
     unir nada: con componentes conexas, un «este idioma no existe, mando a la
     portada» une medio sitio en un clúster (pasa en www.uoc.edu, cuyo
     `walkin/` anota las portadas `ca` y `en`).
   - `hreflang_sin_autorreferencia` (warning, por página): anota otras
     versiones y a sí misma no, o solo con `x-default`. No cuenta como falta
     si la autorreferencia lleva un código inválido (ese es
     `hreflang_invalid_lang`). Si se anota con la misma ruta y otros
     parámetros, `variante_propia` lo dice: es el caso de progym, `/compex`
     anotada como `/compex?___store=progym_es_sp`. **Y también cuando esa
     variante se canonicaliza a la propia página** (penguin, `?recomendados=1`):
     ahí es este aviso y no el `error` del destino, porque ningún mercado pierde
     su versión —la página es indexable—; lo que falla es que la
     autorreferencia va a una URL no canónica. Solo si la página no se anota
     además directamente a sí misma: si ya lo hace, la variante canonicalizada
     a ella es OTRA versión fuera del índice (www.uoc.edu, `?lang=ca`
     canonicalizada a `?lang=en`) y sigue siendo el error del destino. Una
     variante de otra ruta que salta o se canonicaliza a ella (`/de/` a `/es/`)
     tampoco cuenta como autorreferencia: el aviso es el del destino.
   - `hreflang_sin_x_default` (info): se decide por clúster —basta con que
     una de sus páginas lo declare— y se avisa **por host**, con
     `clusters_afectados` de `clusters_del_host`. Una fila por clúster eran
     20.557 avisos `info` en penguin diciendo una sola cosa (decisión 64). El
     host de un clúster que cruza hosts es el **menor** de los de sus páginas,
     no el de la primera fila (la consulta no tiene orden y el propio UPDATE
     por lotes reescribe las tuplas), y la fila se ancla en la **raíz** del
     host si se rastreó, no en la URL mínima de las afectadas: con cualquiera
     de las dos cosas el hallazgo cambiaba de URL o de clave entre análisis y
     la comparación (decisión 88) lo daba por resuelto y nuevo a la vez.
     Si la raíz **redirige** se ancla en la página HTML 200 interna a la que
     lleva (Lopesan: 302 a `/es/`; penguin: 301): anclada en el salto, el chip
     de la pestaña decía «1 página» y la lista salía vacía. Si la cadena no
     acaba en una así, cae a la URL mínima de las afectadas.
   - `hreflang_idioma_repetido` (warning, por clúster y código, `group_key`
     = clúster|código): el mismo código hacia dos URLs. Un error de plantilla
     en un clúster de 30 páginas es UNA fila con `paginas_afectadas=30`.
   - `hreflang_destino_no_indexable` (**error**, como el destino roto: la
     consecuencia es la misma, ese mercado se queda sin su versión): una fila
     por ANOTACIÓN hacia un destino canonicalizado, noindex o bloqueado por
     robots, **en la página que la escribe** —como `hreflang_broken_target`,
     con `group_key` = código y el destino en `details.href`—. La primera
     versión daba una fila por destino anclada en él, y el destino puede ser
     externo: en www.uoc.edu **9 de las 19** caían en `blogs.uoc.edu` o
     `recerca.uoc.edu`, que la pestaña Hreflang (HTML interno) no enseña, así
     que el chip decía 19 y la lista 10. Esas 9 solo las anotaban páginas
     externas, que ya no son fuente (abajo), así que salen; las 10 internas
     quedan como **17 anotaciones en 10 páginas**, todas visibles en la
     pestaña. En Lopesan, los 27 destinos son 27 anotaciones en **14** páginas
     `/es/`. Un destino roto sigue siendo solo
     `hreflang_broken_target` (decisión 56).
   - `hreflang_retorno_incoherente` (warning, por anotación): A llama a B
     `en-US`, B devuelve el enlace a A pero se llama a sí misma `en-GB`. Basta
     con que un código coincida: `/es/` para `es-ES` y `es-MX` es legítimo.
     Sin retorno es `hreflang_missing_return`, no este.
   - `group_key`: el clúster en lo que se avisa por clúster, el host en el
     x-default, el código en lo que es por anotación (como sus hermanos) y
     NULL en la autorreferencia, que es una por URL: una clave de clúster ahí
     cambiaría al añadir un idioma y fingiría que el problema se resolvió
     (decisión 88). El clúster va en `details.cluster`.

   **Una página externa tampoco es fuente.** El rastreo lee el HTML de
   algunos subdominios fuera del alcance (en www.uoc.edu, 139 páginas con
   hreflang, como `guia-audiovisual.uoc.edu`, que daba su propio x-default);
   no son el sitio auditado y nada de lo que se diga de ellas sale en la
   pestaña Hreflang. Sus anotaciones se guardan con `return_tag_ok` NULL.
   Medido en www.uoc.edu: el x-default pasa de 2 a **1** y los destinos no
   indexables de 19 a 10 (los 9 de `blogs.uoc.edu` y `recerca.uoc.edu`, que
   solo anotaban páginas de esos mismos subdominios); el resto de tipos no se
   mueve.

   **La variante propia sin retorno es solo `hreflang_sin_autorreferencia`.**
   Si una página se anota con otra URL de su misma ruta (`?p=2` que se llama
   `es-es` con la URL de la página 1), esa variante no le devuelve el enlace,
   y salía también `hreflang_missing_return` sobre la misma anotación: un
   arreglo y dos avisos (decisión 36). La falta de retorno se decide ahora
   después de la autorreferencia, y la anotación hacia `variante_propia` queda
   con `return_tag_ok` NULL. Solo esa: en penguin las 107 páginas con los dos
   avisos anotan OTRA ruta, y ahí el retorno que falta es otro hallazgo.
   Medido con `run_all` en ROLLBACK: progym **140 → 1** `missing_return` (la
   que queda anota otra ruta); penguin 121, UOC 71 y Lopesan 0, sin cambio.

   **La nota de Internacionalización de `/insights` cuenta los destinos
   descartados.** Puntuaba solo con `return_tag_ok` y `lang_valid`, y las
   anotaciones hacia un destino roto o no indexable tienen el retorno a NULL,
   así que salían del denominador: Lopesan pasaba de 96 a 100 con 31 errores
   de hreflang abiertos en Problemas, y UOC de 95 a 100 con 91. Ahora hay una
   tercera parte, `pct_destino_ok` (de las anotaciones verificadas hacia otra
   página, las que no van a un destino descartado), una recomendación `alta`
   con `hreflang_broken_target` + `hreflang_destino_no_indexable` y otra
   `media` con `hreflang_sin_autorreferencia`. Y con uno de esos dos errores
   abiertos la categoría no pasa de `TOPE_CON_ERRORES` (79), el criterio de la
   nota global (decisión 92): en Lopesan son 31 de 3.635 anotaciones y la
   media sola redondeaba a 100. Medido con `run_all` + `/insights` en
   ROLLBACK: **Lopesan 96 (master) → 79, UOC 95 → 79**; penguin 64 y progym
   50, que sin destinos descartados casi no tienen retorno verificado (194.703
   y 21.194 anotaciones con `return_tag_ok` NULL: se anotan a sí mismas o a
   su variante). La nota global no cambia (Lopesan 49 por el tope); las URLs
   con algún `error` en Lopesan quedan en 2.424 (2.405 en master).

   **Lo que cambia de lo que ya había**, y es lo que más se mueve: una página
   no indexable ya **no es fuente**. Google no usa el hreflang de una página
   canonicalizada o noindex, y la variante `/es` canonicalizada a `/es/`
   repite el clúster de su canónica sin que nadie le devuelva el enlace —el
   retorno apunta, bien, a `/es/`—. Medido: **las 296 `hreflang_missing_return`
   de Lopesan venían de páginas canonicalizadas**. Se sigue guardando
   `lang_valid`, con `return_tag_ok` a NULL. Tampoco se afirma la falta de
   retorno cuando el destino no es indexable (el aviso es el del destino) ni
   cuando no respondió (`Sin respuesta`: un timeout nuestro no dice qué vería
   Google, y como no tiene hreflang guardado salía «sin retorno»). Nada de
   «hreflang no enlazado».

   Medido sobre los censos de la BD local, re-ejecutando
   `analyze_indexability` y `analyze_hreflang` dentro de una transacción que
   acaba en ROLLBACK (el check no lee `links`, así que el archivo en Parquet
   de www.uoc.edu no le afecta):

   | censo (anotaciones) | sin autorref. | sin x-default | idioma rep. | destino no indexable | retorno incoh. | missing_return | broken_target |
   |---|---|---|---|---|---|---|---|
   | Lopesan es/en/de (17.268) | 0 | 1: 1 de 620 clústeres | 0 | **27** (en 14 páginas) | 0 | 296 → **0** | 12 → 4 |
   | www.uoc.edu ca/es/en (81.960) | 34 | 1: 21 de 6.038 | 0 | 10 destinos, 17 anotaciones | 0 | 3.281 → **71** | 72 → 72 |
   | penguin 30-jul (194.824) | **783** | 1: 20.557 de 20.557 | 0 | **0** | 0 | 5.957 → 121 | 61 → 10 |
   | penguin 29-jul (315.076) | **1.014** | 1: 21.262 de 21.262 | 0 | **0** | 0 | 6.197 → 5.919 | 13 → 13 |
   | Druni (139.981) | 304 | 1: 16 de 37.059 | 0 | 0 | 0 | 8.197 → 288 | 0 |
   | progym (21.195) | 277 | 0 | 0 | 0 | 0 | 144 → **1** | 0 |
   | CST (23.901) | 42 | 0 | 0 | 0 | 0 | 166 → 32 | 0 |

   `idioma_repetido` y `retorno_incoherente` salen a **0 en todos**, y no por
   el extractor (no deduplica): contado aparte con SQL sobre todos los censos
   de la BD local, no hay ni una página con un código hacia dos URLs ni una
   pareja con retorno y códigos que no casan. Es corrección de criterio,
   cubierta por los tests.

   Revisados a mano:
   - Lopesan: los 27 destinos no indexables son 14 páginas `/en/` y 13 `/de/`
     **canonicalizadas a su equivalente `/es/`** —`/en/` a `/es/`, `/en/hotels`
     a `/es/hoteles`— y anotadas como `en-US`/`de-DE` desde la versión
     española. Cierto, y grave: la versión inglesa y la alemana de esas
     páginas están fuera del índice. **Antes no daba ningún aviso** desde las
     indexables: esas 27 anotaciones tenían `return_tag_ok = true`. Las 36
     «sin retorno» hacia destinos canonicalizados que había venían de fuentes
     NO indexables (7 destinos) y son parte de las 296 falsas.
   - penguin 30-jul: con la primera versión salían 196 «destinos no
     indexables» y eran **todas** autorreferencias por variante —
     `/es/recomendaciones/tendencias/dieta-keto-que-es-y-para-que-sirve` se
     anota `es` y `es-ES` como la misma ruta con `?recomendados=1`,
     canonicalizada a ella—. Ahora son 196 `hreflang_sin_autorreferencia` más
     (587 → 783) con esa URL en `variante_propia`, y 0 errores. En penguin
     29-jul pasa lo mismo con el único que había (1.013 → 1.014). Los 19 de
     www.uoc.edu no cambian: los 7 de `recerca` que tienen esa forma
     (`detalle?lang=ca` canonicalizada a `detalle?lang=en`) vienen de páginas
     que ya se anotan a sí mismas, así que es la versión catalana fuera del
     índice. Cierto, pero de `recerca.uoc.edu`, fuera del alcance del censo, y
     anotada desde ese mismo subdominio: con la regla de que una página
     externa no es fuente (más arriba) esas 7 y las 2 de `blogs.uoc.edu`
     dejan de avisarse, y quedan las 10 de www.uoc.edu.
   - Lopesan, las 296 «sin retorno» que desaparecen: `/es` sin barra,
     canonicalizada a `/es/`, con el clúster de la canónica. Eran falsas.
   - progym `/compex`: se anota como `/compex?___store=progym_es_sp`, que para
     Google es otra URL. Cierto (las 277 son esa plantilla de Magento).
   - www.uoc.edu `walkin/es/subscripcio.html`: anota `ca` → `https://www.uoc.edu/`
     y `en` → `/en`, y a sí misma no. Cierto: la plantilla vieja de walkin
     apunta a las portadas.
   - Lopesan blog `meeting-rooms-in-madrid` (`en` y `de`, sin versión `es`):
     el único de los 620 clústeres sin x-default. Cierto.

   **Tiempo**: en penguin 29-jul (315.076 anotaciones), tres pasadas
   alternas, 10,6 / 11,7 / 9,9 s antes y 12,6 / 13,7 / 10,7 s ahora: ×1,2.
   Proyectado a las 438.957 de producción, unos 24 s frente al tope de 40,6
   (2 × 20,3). Es re-analizar, no re-rastrear.
   Tests: `tests/test_hreflang_completo_db.py` (ejecuta el check contra
   SQLite) y `tests/test_catalogo_incidencias.py` (ningún tipo emitido sin
   ficha en `incidencias.js`).

<a id="d101"></a>
101. **Lo que importa de un canonical es lo que Google encuentra al llegar al
   destino, y de una redireccion, donde acaba** (PR 1.1 de
   `docs/PLAN_MEJOR_CRAWLER.md`). `canonical_broken` metia en el mismo saco un
   301 y un 404 —dos hallazgos con arreglos distintos—, y un destino que
   responde 200 pero esta en `noindex` o canonicalizado a su vez a otro no
   avisaba de nada. Una redireccion que acababa en 404 solo avisaba en el 404,
   nunca en el salto que lleva a el. Y la canonica que el sitio no enlaza salia
   como `orphan_page`, que manda a buscar enlaces que no faltan: los hay, pero
   apuntan a la variante. Seis tipos, uno por hallazgo, con la severidad por la
   consecuencia (decision 64):

   | tipo | cuando | severidad |
   |---|---|---|
   | `canonical_a_error` | el destino da 4xx/5xx, directamente o al final de sus redirecciones, o las redirecciones hacen bucle | error: el contenido se queda sin URL que indexar |
   | `canonical_a_redireccion` | el destino redirige (y no acaba en error) | warning: Google suele quedarse con la final, pero las dos senales no coinciden |
   | `canonical_a_no_indexable` | una pagina que NO es noindex declara canonical a un destino 200 en `noindex` | warning (ver abajo) |
   | `canonical_encadenado` | A → B → C: el destino da 200 y declara otro canonical; incluye el bucle A → B → A | error si la cadena acaba en 4xx/5xx; warning si no |
   | `redireccion_a_error` | una redireccion interna, o su cadena, lleva a OTRA ruta que da 4xx/5xx (no una normalizacion: esquema, `www`, barra final) | error |
   | `canonical_sin_enlazar` | canonica indexable con 0 entrantes y al menos una variante enlazada desde fuera del grupo | warning |

   `canonical_broken` deja de emitirse; su ficha se queda en `incidencias.js`
   para los censos analizados antes, y la comparacion entre censos no casa los
   dos nombres porque dos analisis de codigo distinto ya no se comparan por
   incidencias (decision 91). Criterios:

   - **Solo desde una pagina que da 200.** El canonical de un 404 o de un 500
     Google no lo procesa. Antes se avisaba igual (en UOC, 12 paginas 500 con
     canonical; en penguin, 4 con 429).
   - **Lo que no se sabe no se afirma.** Un destino sin rastrear, bloqueado por
     robots.txt o sin respuesta no emite nada, y el **429 no es un error**: es
     el sitio frenando al rastreador. En penguin, 35 de las 132 redirecciones
     que «acababan en 4xx» acababan en un 429.
   - **Un aviso por hallazgo.** Una cadena que acaba en error da
     `redireccion_a_error` y no tambien `redirect_chain`: redirigir directo a
     una pagina buena arregla las dos. Un canonical a `http` que redirige a la
     propia pagina, o a la version https del mismo canonical, ya es
     `canonical_a_http` y no se repite como redireccion. Una redireccion que
     solo **normaliza** (http → https, `www`, barra final) y cuyo destino da
     404 no es `redireccion_a_error`: esta bien hecha, el error es el destino
     y ya tiene su `4xx_error` — en www.uoc.edu eran **383 de 594**, casi
     todas el 307 interno de Chromium hacia https. Y si el canonical del
     destino B es una variante suya que redirige de vuelta a B (`/b/` → `/b`),
     B es autocanonica de hecho: A → B no es cadena ni bucle, y lo de B avisa
     en B como `canonical_a_redireccion`. La
     canonica sin enlazar sale de `orphan_page` y `sitemap_orphan` —es la
     misma cura que la decision 63 con los destinos de redireccion—; si
     TAMPOCO las variantes estan enlazadas, es huerfana de verdad y lo sigue
     diciendo `orphan_page`. Una variante enlazada solo desde la propia
     canonica o desde otra variante suya (el selector de color o de talla)
     **no cuenta como enlazada**: se cuentan sus entrantes de fuera del grupo
     sobre `links` (filtrando por `links.job_id`, decision 37; con
     SUM(repeticiones), decision 94; origen 200 y no noindex, como
     `inlinks_count`). En www.uoc.edu era una de las 16: una noticia cuyo unico
     enlace a su variante `?idTag=` salia de ella misma.
   - **`canonical_a_no_indexable` solo desde una pagina que no es noindex, y
     es warning y no error**: lo que saca el contenido del indice es el
     `noindex` del destino, que avisa en su propia URL y muchas veces es
     deliberado. Si el origen TAMBIEN es noindex no hay senales que se
     contradigan —no entra en el indice pase lo que pase— y no se emite. La
     primera version no lo miraba y el 100% de lo que encontraba era eso: las
     52 de Druni (variantes con `gclid` de paginaciones `?p=N` en noindex a
     proposito, las dos URLs en noindex), las 3 de UOC y las 2 de penguin. Con
     el criterio, **0 en los seis censos**: el tipo queda para el caso que si
     es un conflicto, y hoy no aparece en ninguno.
   - **El enlazado sigue las decisiones 38 y 57**: `inlinks_count` ya descuenta
     los enlaces desde `noindex` y desde paginas que no dan 200, asi que una
     canonica que solo enlaza una noindex sigue «sin enlazar» (hay test). Lee
     el contador guardado, no `links`, y con `jobs.enlaces_archivados` (decision
     95) no se evalua: se salta con un WARNING en el log.
   - El canonical del destino es el que usa la indexabilidad: el del HTML y, si
     no hay, el de la cabecera `Link`. Las URLs se buscan con la clave de
     `misma_url` (decision 39).

   **Medido** el 2026-10-16. Lopesan, penguin y UOC con `run_all` entero dentro de
   una transaccion que acaba en ROLLBACK (commit anulado e incidencias
   capturadas en memoria); los seis censos tambien con solo los tres checks en
   una transaccion `READ ONLY`. Las cifras de los checks coinciden en los dos
   modos donde se midieron los dos.

   | censo | a_error | a_redireccion | a_no_indexable | encadenado | sin_enlazar | redireccion_a_error |
   |---|---|---|---|---|---|---|
   | Lopesan (5.751) | 0 | 4 | 0 | 2 | 0 | 9 |
   | www.uoc.edu | 0 | 0 | 0 | 8 | 15 | 203 |
   | penguin | 0 | 0 | 0 | 0 | 0 | 97 |
   | Druni | 0 | 0 | 0 | 0 | 41 | 40 |
   | progym | 0 | 0 | 0 | 0 | 0 | 1 |
   | CST | 0 | 0 | 0 | 0 | 1 | 0 |

   (Tras la revision: `canonical_a_no_indexable` 3/2/52 → 0 por el origen
   noindex, `redireccion_a_error` en UOC 594 → 203 por las normalizaciones
   —3 de ellas, cadenas de dos saltos, pasan a `redirect_chain`, 280 → 283— y
   `canonical_sin_enlazar` en UOC 16 → 15. Medido con los tres checks en una
   transaccion `READ ONLY` sobre lo guardado; ninguno de los seis censos tiene
   los enlaces archivados.)

   Los 4 `canonical_broken` de Lopesan son ahora sus 4 `canonical_a_redireccion`.
   En UOC, `redirect_chain` pasa de 401 a 283: las cadenas que acaban en un
   404 de otra ruta son ahora `redireccion_a_error`. Y de 283 a **278** tras
   la revisión de la Ola 1: una cadena o un bucle solo se avisan si **empiezan
   en una URL interna**, el criterio que ya tenía `redireccion_a_error`. Las 5
   que se van empezaban en otro sitio (`http://research.uoc.edu/portal/es/ri/index.html`,
   `http://estudios.uoc.edu/...`, todas 301) y la pestaña Redirecciones, que
   filtra por interna, las contaba en el chip sin enseñarlas en la lista. En
   Lopesan (29), penguin (0) y progym (1) no cambia nada. Los `canonical_sin_enlazar`
   de UOC salen de `orphan_page` y `sitemap_orphan`, exactamente;
   los 41 de Druni, de `sitemap_orphan`. Fuera de esas columnas, las demas
   diferencias del total contra lo guardado (Lopesan 38.922 → 27.332, penguin
   293.847 → 114.851, UOC 78.494 → 73.269) son de las decisiones posteriores
   al ultimo analisis de cada censo, no de esta. `run_all` entero: 116 s en
   Lopesan, 464 s en penguin y 691 s en UOC; los tres checks juntos, 2,0 s en
   UOC y 3,1 s en Druni (61.236 URLs).

   **Revisados a mano**, los cuatro que siguen saliendo, ciertos:
   - Lopesan, `canonical_a_redireccion`: `/de/unternehmen/lopesan-for-good/nachhaltigkeitspolitik`
     declara canonical `/es/corporativa/lopesan-for-good/politica-sostenibilidad`,
     que da 301 a `/es/corporativa/politica-esg`. (Ademas apunta a otro idioma,
     que es cosa del hreflang, PR 1.2.)
   - Lopesan, `canonical_encadenado`: `/es/inicio?gclid=…` → `/es/inicio`
     (200, 4.380 entrantes) → `/es/`. Todo el sitio enlaza `/es/inicio`, que
     se canonicaliza a la home.
   - UOC, `canonical_sin_enlazar`: `/es/estudios/grados/menciones` (en el
     sitemap, 0 entrantes) recibe su senal por la variante
     `?area=sis%3Aareas%2F18`, con **1.815 enlaces** de fuera del grupo. Hasta ahora era un
     `sitemap_orphan` que mandaba a buscar enlaces que si existen.
   - (Descartado en la revision: el ejemplo de `canonical_a_no_indexable` de
     UOC, `precio-descuentos.html` → `precio-descuentos`, tiene el ORIGEN
     tambien en noindex; con el criterio final no sale, y no queda ningun
     caso de ese tipo en los seis censos.)
   - Lopesan, `redireccion_a_error`: `/hoteles/tailandia` → 301 →
     `/de/hoteles/tailandia`, que da 404 (el prefijo de idioma por defecto
     mal resuelto; 9 asi).
   Y uno que es cierto pero pequeno: en Druni, `/cosmetica/geles/ph-neutro` sale
   sin enlazar porque el unico enlace, desde un post del blog, lleva el
   `?srsltid=` que Google anade a sus resultados: se arregla cambiando ese
   enlace.

   Fuera, a proposito: el canonical **relativo o con fragmento** (exige
   guardar el href crudo y re-rastrear), un destino **bloqueado por robots.txt**
   (Google no puede leerlo, pero puede indexar la URL por sus enlaces: no se
   afirma nada sin medirlo), las paginas que solo declaran canonical por
   cabecera `Link` como ORIGEN (siguen el camino de antes) y el **informe de
   cadenas** que mezcla redirecciones y canonicals de una migracion, que es
   una vista y no un tipo.

<a id="d113"></a>
113. **El robots.txt guardado se evalúa también como Googlebot, y si se sabe,
   manda Googlebot** (sección 7 de #149, Ola 7.3). El rastreo obedece
   robots.txt con el user-agent de descarga (`USER_AGENT`, Chrome por
   defecto: decisión 98), así que se aplica el grupo `*` —o uno como
   `User-agent: Mozilla`, que casa con la cadena de Chrome— y no el de
   `Googlebot`. Reproducido con Protego: `User-agent: Googlebot` /
   `Disallow: /solo-google/` deja pasar a Chrome; `User-agent: *` /
   `Disallow: /` + `User-agent: Googlebot` / `Allow: /` bloquea a Chrome y no a
   Google. Una página que rastreamos y Googlebot no puede pedir salía
   indexable: el caso grave, indexable para nosotros e invisible para Google.
   - **El rastreo no cambia**: sigue obedeciendo con el UA de descarga en
     `respect`, que es cortesía, y el `Crawl-delay` sale del mismo grupo (81).
     Evaluar como Googlebot es un DATO del análisis, sobre el cuerpo guardado
     en `robots_txt` (decisión 112): re-analizable, y sin pedir nada al sitio.
   - **Con el token, no con un UA**: `can_fetch(url, "Googlebot")`. Con una
     cadena `Mozilla/5.0 (compatible; Googlebot/2.1…)` Protego casaría el grupo
     `Mozilla`. Un grupo `Googlebot-News` no aplica a Googlebot (como en
     Google). Los ejemplos de precedencia de la documentación de Google
     —gana la regla más larga; con la misma longitud, `Allow`; `$` y `*`—
     dan lo mismo con Protego (`tests/test_robots_txt.py`). Lo que pasa de
     500 KiB no cuenta, como en Google: se evalúa el cuerpo recortado.
   - **`urls.bloqueada_googlebot`** (NULL = no evaluado: censo anterior, host
     sin fila o por encima del tope, robots.txt inaccesible, muro delante, otro
     esquema o puerto —Google trata aparte `http` y `https`—). Un 4xx es
     `False` (todo permitido). Se escribe como `urls.noindex`, por lotes, y se
     pone a NULL antes de cada análisis: con otro código una URL puede dejar
     de estar evaluada.
   - **`estado_indexabilidad` recibe el veredicto de Googlebot si se conoce**;
     si no, lo de siempre (`blocked_by_robots` o el motivo `Blocked…`). Una
     página pedida que Googlebot no puede pedir pasa a `Blocked by robots.txt`
     y deja de ser indexable —y con ella sus entrantes y su PageRank
     (decisión 38)—; en `audit`, una pedida y marcada que Googlebot SÍ puede
     pedir pasa a indexable. Una que no se pidió (bloqueada para nuestro UA)
     sigue sin ser indexable aunque Google pueda: no la hemos visto.
   - **`robots_difiere_para_googlebot`, `warning`, una por host** (nunca una
     por URL, decisión 36): `solo_googlebot` (las rastreamos y Google no puede
     pedirlas) y `solo_rastreador` (no las pedimos y Google sí puede: hay que
     re-rastrear con el preset de Googlebot de la 98), con cinco ejemplos de
     cada. Lo que decidió el rastreo es `blocked_by_robots`, no el motivo que
     escribe este mismo paso: si no, re-analizar se compararía consigo mismo
     (lo cazó el test de re-análisis). En `ignore` no hay veredicto propio y
     no se compara, pero la columna y la indexabilidad sí salen.
   - **Preparado para N bots** (Ola 7.3): `shared/robots_txt.BOTS` (clave →
     token del grupo) y `EvaluadorRobots.permitido(url, bot)`; el resumen por
     host y bot va en `robots_txt.evaluacion` (`{"googlebot": {estado,
     evaluadas, bloqueadas}}`), que es donde irá la tabla de qué bloquea cada
     bot de IA sin una columna por URL y bot. Lo que falta de la 7.3: los bots
     de IA y la línea concreta que bloquea.
   - **Lo que no se puede ver**: el robots.txt guardado es el que nos sirvió a
     NUESTRO UA desde nuestra IP; un sitio que sirve otro a Googlebot no se
     detecta sin la IP de Google (lo dice la ficha).
   - **Dependencia**: `protego` en `analysis/requirements.txt` (decisión 69:
     hoy llegaba solo con Scrapy). Se importa solo al evaluar: sin él no se
     afirma nada (NULL) y el análisis sigue.
   Cambia `indexable` en los censos **nuevos** (con filas en `robots_txt`); los
   anteriores, re-analizados, salen exactamente igual (test). Pendiente de
   medir: en cuántas URLs de la cartera difiere el veredicto entre Chrome y
   Googlebot (robots.txt de hoy contra las URLs del último censo, script de
   solo lectura) y el canario con `count(*) FILTER (WHERE bloqueada_googlebot)`.
