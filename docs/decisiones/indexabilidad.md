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
