# Decisiones: extracción y búsqueda a medida

> Lo que el consultor pide a cada página con sus propias reglas (#34, Ola 3 de
> `docs/PLAN_MEJOR_CRAWLER.md`), los segmentos de negocio (3.3, decisión 108)
> y, cuando llegue, la comparación de valores extraídos (3.4). Índice de todas las
> decisiones en [`README.md`](README.md).

<a id="d104"></a>
104. **Extracción y búsqueda a medida: una fila por (URL, regla) evaluada, y
   sin fila no es «vacío»** (#34, 3.1 y 3.2). Lo que Screaming Frog llama
   *custom extraction* y *custom search*: reglas CSS, XPath o regex por job
   (`custom_extraction`) y búsquedas «contiene / no contiene» (`custom_search`),
   declaradas en `JobConfig` y heredadas cliente → perfil → job. La lógica vive
   en `shared/extraccion_a_medida.py` porque la usan el spider al rastrear y la
   API al validar y probar reglas (`POST /api/extraccion/probar`): dos copias
   acabarían discrepando (23, 39). Lo que no es obvio:

   - **Sin fila = no evaluada.** La tabla `custom_extraction` tiene una fila por
     (URL, regla) *evaluada* con un `estado` explícito (`ok`,
     `sin_coincidencia`, `incompleta`, `conversion_fallida`, `tiempo_agotado`,
     `presupuesto_pagina`, `desactivada`, `pagina_bloqueada`, `error`). Una URL
     sin fila no es HTML 2xx, cae fuera de `solo_urls` o es de un censo anterior
     a la regla. «No contiene» y «vacío» son exactamente
     `estado='sin_coincidencia'`: `x.gtm=no`, `x.precio_contains=!a` y
     `vacio=x.precio` no devuelven ni los PDF ni las páginas bloqueadas por un
     WAF (62). Una búsqueda que no encuentra nada en una fuente recortada a
     2 MB es `incompleta`, que NO es «no contiene».
   - **La página se reemplaza entera.** El spider emite un item por página con
     todas sus filas y el pipeline hace `DELETE ... WHERE url_id IN (lote)` +
     INSERT en la misma transacción, sin el «solo la primera vez» de los demás
     hijos. Y cuando una URL ya guardada vuelve como 404 o 301 se borran sus
     filas en `_handle_page`: sin HTML 2xx no llega ningún item que las
     reemplace, y una ficha retirada conservaba su precio viejo. Comprobado con
     el spider real y el Postgres de pruebas (`test_spider_a_medida.py`): en una
     reanudación con `recrawl_patterns` el precio que desaparece pasa a
     `sin_coincidencia`, la 404 y la 301 se quedan sin filas, y repetir no
     duplica.
   - **XPath sin EXSLT y regex con `timeout`.** Las XPath se compilan con
     `lxml.etree.XPath(..., regexp=False)` y sin namespaces; el spider nunca usa
     `Selector.xpath`, que registra el `re:` de EXSLT sobre el `re` de Python
     (vulnerable a ReDoS y sin forma de cortarlo). Al implementarlo salió que
     `re:test()` **compila** igual y solo falla al evaluarse sobre un nodo que
     exista, así que la validación mira los prefijos en el texto, no solo
     evaluando. Se rechaza también una ruta absoluta (`/` o `//`) dentro de un
     predicado (cuadrático: `//a[//b]` y `//a[/html//b]`; `.//b` y `*/b` valen). Las regex van con el módulo `regex` y `timeout` (50 ms), no
     con RE2: conserva lookaround y referencias hacia atrás, y no añade una
     rueda C++ incierta en aarch64. Ojo para los tests: `(a+)+$` **no** es
     catastrófica en `regex` (la optimiza; 0,4 ms); `(a|aa)+$` sí.
   - **Cortacircuitos.** 250 ms por página para todas las reglas
     (`presupuesto_pagina` para las que no caben) y una regla que agota su
     tiempo 3 veces queda `desactivada` el resto del rastreo, con un WARNING
     que sube por `resumir_stderr`. Sin eso, una regex catastrófica paga su
     tope en cada una de las 60.000 páginas: 50 minutos de reactor parado.
     **Una XPath o un CSS no se pueden cortar** (lxml no tiene timeout), y la
     primera versión solo contaba como agotada la regex: `div ~ div ~ div`
     pasaba la validación y costaba **11,1 s por página** con 3.000 bloques
     (`//*[preceding::*[. = 'x']]`, 10,5 s), sin desactivarse nunca. Ahora se
     MIDE: una regla que sola pasa del presupuesto de la página (250 ms)
     cuenta como agotada —lo que calculó se conserva, con el tiempo en
     `detalle`— y a la tercera se desactiva; el peor caso son tres páginas
     lentas, no el rastreo entero. Con `cuantos=primero` el texto del resto de
     nodos ya no se calcula (con `div` en 450 kB anidados eran 330 ms por
     página): `n` cuenta desde ahí los nodos que casan. lxml suelta el GIL al
     evaluar (medido: el hilo de al lado avanza al 77 % de su ritmo).
   - **`POST /api/extraccion/probar` admite 2 pruebas a la vez**
     (`CUSTOM_EXTRACTION_PRUEBAS_A_LA_VEZ`); la tercera es un 429. No pide
     autenticación y una XPath no se interrumpe: sin tope, unas decenas de
     peticiones con 5 MB de `<div>` hermanos ocupaban el threadpool y la API
     dejaba de responder a `/api/jobs`. Dentro de una prueba el presupuesto de
     la página corta las reglas que vienen detrás de una lenta.
   - **Sin NUL.** Postgres rechaza `\x00` en un texto; lxml lo quita en CSS y
     XPath, pero una regex sobre el HTML crudo (o el contexto de una búsqueda)
     no, y un solo valor tumbaba el lote del pipeline y, con su rollback, las
     páginas aún sin confirmar. `_fila` lo quita de `valor`, `valores`,
     `valor_clave` y `detalle`.
   - **Muro = `pagina_bloqueada`**, con los títulos de `TITULOS_DE_BLOQUEO`
     (62) **y solo en una página de 64 kB o menos** (`es_muro`): un desafío de
     Cloudflare no «no contiene» el tag de GTM, pero un artículo titulado
     «Error 403 Forbidden: qué es» lleva la plantilla del sitio y se evalúa.
     La decisión 62 aplica la lista junto al código de respuesta; aquí, en
     todo el rastreo, el título solo no basta.
   - **Reglas que no compilan: `failed` con `finish_reason=config_invalida`,
     también al reanudar.** El spider no pide nada y deja la marca
     `job:{id}:config_invalida` (con el motivo) en Redis; el worker la mira
     ANTES que el guardia de cero URLs, porque en una reanudación ya hay filas
     y el job salía `completed` con la frontera sin explorar —y la comparación
     daba por desaparecido lo que no se llegó a pedir (67)—. Pasa si un
     despliegue endurece la validación a mitad de censo o si la imagen del
     crawler resuelve otra versión de `parsel`/`regex` que la de la API. El
     motivo (qué regla y por qué) va al log del **worker** en ERROR, además de
     al de Scrapy; si las reglas compilan, el spider borra una marca vieja.
     Probado ejecutando `_run_job` con Scrapy simulado (`test_job_vacio.py`) y
     el spider real (`test_spider_a_medida.py`).
   - **Un nombre no puede acabar en `_gte`, `_lte`, `_contains` ni `_estado`**:
     son los sufijos de los filtros `x.<nombre>…`, y con `stock` y
     `stock_estado` el filtro de estado de una se leía como el sí/no de la otra.
   - **Herencia: una regla se sustituye entera por nombre y `null` la quita.**
     Fusionarla campo a campo dejaría pegado lo que el perfil no repite (un
     `solo_urls` del cliente acotando la regla reescrita). Para eso hizo falta
     tocar `validar_config_parcial`: `_separar_nones` rechazaba
     `{"custom_extraction": {"precio": null}}` como clave desconocida, y una
     regla parcial no se puede validar (`tipo` y `expresion` son
     obligatorios). El `null` vale también en el JOB (`JobConfig` admite
     `dict[str, regla | None]`): un rastreo puntual apaga una regla del cliente
     sin crear un perfil; `fusionar_config` lo consume y no llega a
     `jobs.config`. Al guardar un perfil se valida además la fusión con su
     cliente, y al cambiar el `config_defecto` de un cliente, la de cada uno de
     sus perfiles (el 422 nombra el perfil): una regla `fuente=contenido` sobre
     un cliente que apaga `extract_page_content` es un 422 al guardar, no en el
     primer programado.
   - **Una regla que el censo no tiene es un 422**, en el listado, el orden y
     los CSV: una vista guardada de otro censo no puede devolver el censo
     entero como si el filtro no existiera. El Explorador lo dice y ofrece
     quitar el filtro; para eso manda el `x.*` aunque no tenga la columna
     (antes lo descartaba en el cliente y el 422 solo salía con el orden).
   - **El CSV escapa fórmulas** (`= + - @`, tabulador, retorno) en los valores
     de texto que vienen de la página; los números no se tocan. Con `todos`, una
     columna por posición («Talla 1», «Talla 2»…), tantas como el máximo de
     coincidencias del censo con el tope de la regla. En `/export` las columnas
     `x_<nombre>` van AL FINAL y solo si hay reglas: las fijas no se mueven.
   - **Números: un caso ambiguo no se adivina.** `1.299,00 €` y `1,234.56` se
     leen bien (el último separador es el decimal); `1.234` o `1,299` sin
     separador declarado salen `conversion_fallida`: inventar uno de los dos
     daría precios mil veces mayores o menores sin que nada lo marque.
   - **Con render, del DOM renderizado y sin banners**; la fila lleva `render`
     (False si la página cayó en `_sin_render`) y el resumen lo avisa. El
     snippet en línea de GTM sigue en el DOM aunque `blocklist.py` aborte
     `gtm.js`, así que la aceptación de 3.2 vale con `fuente=html` en las dos
     formas de rastreo. El HTML crudo con render es la PR 3.

   **Medido** (2026-10-16, fichas reales de Druni descargadas en vivo con
   `shared/descarga_cruda`, 1,1-1,4 MB de HTML cada una, Hyvä/Magento):

   | | precio (CSS) | precio (JSON-LD) | SKU | disponibilidad |
   |---|---|---|---|---|
   | Eucerin Oil Control | 18,95 | 18.95 | 5801415 | InStock |
   | Estée Lauder Nutritious | 24,95 | 24.95 | 1788106 | InStock |
   | Iraltone cápsulas | 26,95 | 26.95 | 5798819 | InStock |
   | Estuche Cacharel | 54,95 | 54.95 | 1644047 | **OutOfStock** |
   | Home (control) | sin coincidencia | sin coincidencia | sin coincidencia | sin coincidencia |

   Reglas: `div[mcclane-data='product-page-price'] .final-price .price .price`
   con `valor=numero`; `"offers":\s*\{[^}]*?"price":\s*([0-9.]+)` con
   `separador_decimal="."`; `"sku":\s*"([^"]+)"` y
   `"availability":\s*"https?://schema\.org/(\w+)"`.
   - **Sobrecoste de `parse`: 0,9 %** (51,7 ms de reglas frente a 5,7 s de la
     extracción que ya hace el spider en las cinco páginas; objetivo < 5 %).
     Lo caro es la búsqueda: `GTM-` **sin caja** sobre 1,3 MB son 3-5 ms y
     encuentra **2.368** coincidencias por ficha, porque Druni tiene cientos de
     atributos `gtm-…` en minúscula; sensible a la caja baja a 0,4 % y da las 2
     reales. Para el tag de GTM: `ignorar_mayusculas: false` o la regex
     `GTM-[A-Z0-9]{4,}`.
   - **«No contiene GTM-» en páginas reales**: de 11 páginas (Druni, blogs y
     www de la UOC, Saunier Duval, Lopesan, Penguin, CST), la única sin el tag
     es la home de **cst.gov.sa**.
   - **Postgres**: 60.000 URLs × 10 reglas (600.000 filas): filtrar
     (`x.r0_gte`, `x.r9=no`) y ordenar por `x.r0` con la función real
     (`_urls_filtradas` + `_ordenar`) son **146-160 ms** contando el total y la
     página (objetivo < 300 ms), por `ix_cext_job_nombre_estado`. El test de
     plan (`test_a_medida_db.py`) ejecuta esas mismas funciones —la primera
     versión copiaba la consulta (74)— y corre en el trabajo de la API, que
     tiene un Postgres de servicio. La primera
     versión del orden unía por (url_id, nombre) sin `job_id` y Postgres
     recorría las filas de esa regla de TODOS los censos (37): el `job_id` va en
     la condición del join. Tamaño en una tabla limpia: **146 MB** para esas
     600.000 filas con valores cortos (254 B/fila, 76 MB de tabla y 69 de
     índices); con contextos de búsqueda de 120 caracteres será más. La de
     pruebas pesaba 1,2 GB porque arrastraba 4,8 M de filas muertas que otra
     transacción retenía: no medir el tamaño sobre una tabla con borrados.

   **Fuera de esta PR** (anotado en `docs/referencia/pendiente.md`): la pestaña
   de edición en el formulario y la prueba con URL en vivo desde el worker
   (PR 2), `fuente=html_crudo` con render y la incidencia agrupada opcional
   (PR 3), la comparación de valores entre censos (PR 4, 3.4) y re-extraer sobre
   `raw_html` sin re-rastrear. Esta PR no emite incidencias.

<a id="d108"></a>
108. **Segmentos de negocio: la primera regla verdadera gana, lo que no se sabe
   no casa, y se materializan en `urls.segmento`** (Ola 3.3). Reglas del
   consultor que ponen a cada URL un nombre de negocio —«fichas de producto»,
   «blog», «categorías»— para que el resto de la herramienta pueda contestar
   «¿qué le pasa a las fichas?». Viven en `config.segmentos`, heredadas
   cliente → perfil → job como el resto (83), y la lógica en
   `shared/segmentos.py`, porque la usan el analizador y la API. Lo que no es
   obvio:

   - **El formato de `templates`, ampliado.** Una regla es `{nombre, patron,
     si}`: `patron` es exactamente el de `TemplateRule` (regex sobre el path,
     sin distinguir mayúsculas), así que la lista de plantillas de un cliente se
     pega tal cual; `si` es un árbol con `todas` (Y), `alguna` (O), `no`, y
     hojas `ruta`/`url` (regex), `campo` (una lista cerrada de columnas de
     `urls`, `CAMPOS`, cada una con sus operadores) y `x` (el valor de una regla
     a medida del mismo config, 104). La **primera** regla verdadera gana. Es una
     LISTA, así que en la herencia se sustituye entera: la precedencia es el
     orden y sumar listas la haría imposible de leer. `templates` NO se usa
     como segmentos implícitos: sirve para muestrear, y que cambiar el muestreo
     cambiara el informe sería una sorpresa. **Que una `x` exista se comprueba
     sobre la configuración FUSIONADA** (`resolver_config`, y `create_job` sin
     cliente), no capa a capa: `JobConfig` solo valida la forma, porque un
     perfil o el override de un job pueden segmentar por una extracción que fija
     el cliente (83), y validar cada capa con sus propias reglas a medida daba
     un 422 a una herencia válida. Lo que ninguna capa define sigue siendo un
     422 que lo nombra, también al dar de alta un cliente.
   - **Tres valores, no dos.** Una condición sobre un dato que no existe —sin
     fila en `custom_extraction`, una página bloqueada por el WAF, un
     `indexable` o un `in_sitemap` a NULL— es **desconocida**, no falsa, y
     `no desconocido` sigue siendo desconocido (lógica de Kleene). Una regla
     solo asigna su segmento si es verdadera, y el resumen
     (`jobs.segmentacion.por_regla[].sin_saber`) cuenta cuántas URLs no se
     pudieron juzgar: «0 fichas» no puede leerse como «no hay fichas» cuando
     es «este censo no tiene la extracción» (67, 89, 104). Con un muro (103) lo
     único que se sabe es la dirección y la propia clase de respuesta; cualquier
     condición sobre el código, las palabras o la indexabilidad es del muro, y es
     desconocida. `vacio` y `estado` (con `no_evaluada`) permiten preguntar por
     lo desconocido a propósito. **Una excepción, porque ahí NULL es un dato:**
     `redirect_type`, que el spider deja a None en toda respuesta que no es un
     salto. Con una respuesta que no es 3xx, NULL es «no redirige», conocido
     —si no, `no {redirect_type igual 301}` no casaba con ninguna 200 y el
     segmento salía vacío con todo el censo «sin saber»—; sin respuesta
     (bloqueada, error de red) sigue sin saberse.
   - **Las reglas solo juzgan páginas del sitio.** Antes de mirar ninguna, las
     URLs externas van a `(externa)` y los recursos internos que no son un
     documento (imagen, SVG, CSS, JS, fuente) a `(recurso)`; los saltos, lo
     bloqueado por robots, lo que falló sin respuesta y los PDF sí son páginas
     (42). La primera versión miraba las reglas antes que `is_internal`, así que
     un enlace saliente a `otro.com/blog/` casaba con `^/blog/` y entraba en el
     blog del cliente, en su filtro y en el denominador de cada porcentaje (con
     cuatro externas en las pruebas, el % de títulos cortos del blog bajaba de
     50 % a 16,67 %). En los censos locales: **215 URLs externas en www.uoc.edu**
     (196 HTML) que cualquier regla por ruta podía recoger; recursos internos,
     solo 3 imágenes en Lopesan y ninguno en los demás, así que esa mitad del
     arreglo es de criterio y no de cifra.
   - **Sin regla que case, la forma de la ruta.** El segmento por defecto es
     `firma_de_ruta`. No un cubo
     «(sin segmento)»: lo que las reglas no cubren sale repartido por plantilla,
     que es justo lo que hace falta ver para escribir la regla que falta. Por eso
     un nombre de regla no puede empezar por `/` ni por `(`: se distingue a la
     vista qué viene de una regla y qué es el repuesto. NULL = censo anterior a
     la columna, y se dice («sin segmentar»), no se inventa.
   - **Materializado, no calculado al consultar.** `urls.segmento` lo escribe el
     analizador al final de `run_all` (sus condiciones pueden leer lo que el
     análisis acaba de escribir: `indexable`, `click_depth`, `in_sitemap`) en
     Python, por tramos de 5.000 URLs y un UPDATE por (tramo, segmento): regex
     con el módulo `regex` y `timeout`, como en 104, y sin atarse a Postgres. Y
     sin el análisis entero: `POST /api/jobs/{id}/segmentos` re-segmenta con
     reglas nuevas, con las del cliente de hoy (`desde_cliente`) o con las del
     censo, las guarda en `jobs.config.segmentos` (para que un re-análisis las
     respete y el censo diga con qué se segmentó) y toma el candado del job. Es
     lo que lo hace **retroactivo**: medido sobre la copia del censo de Druni
     (60.399 URLs, el mayor local), **2,2-2,4 s** con cinco reglas (tres
     corridas) y 2,3 s sin reglas (1.968 formas de ruta). **El tramo va por
     `(job_id, url_hash)`, el índice único `uq_job_url`, no por `id`**: no hay
     índice `(job_id, id)`, y `id > :ultimo ORDER BY id` hacía que Postgres
     recorriese `urls_pkey` —la clave de TODOS los censos— filtrando por job.
     En la copia de Druni no se veía porque la tabla solo tenía ese censo; en
     una base sintética de **6 M de filas** (100 censos de 60.000), el último
     tramo del censo más antiguo pasa de **4,5 s** (`urls_pkey`, 1.980.000 filas
     descartadas por hilo) a **75 ms** (`uq_job_url`), y `segmentar` entero de
     **3,7-8,0 s a 1,6-3,1 s** (dos corridas por censo, el más nuevo y el más
     antiguo). En producción la tabla guarda todos los rastreos; su tamaño no
     está medido aquí. No lee `links`, así que
     funciona también en un censo con los enlaces archivados (95), que el
     análisis entero rechaza. `jobs.segmentacion.huella` dice con qué reglas se
     materializó, y `GET /segmentos` avisa (`al_dia: false`) si cambiaron.
   - **Sin índice nuevo sobre `urls`.** Todo filtro por segmento va con
     `job_id`, que ya tiene los suyos; crear `(job_id, segmento)` en una tabla
     de millones de filas bloquea las escrituras de un rastreo en marcha
     mientras se construye. Medido sin él en la copia de Druni:
     `/issues/resumen?por_segmento=true` 0,78 s, `?segmento=` 0,42-0,48 s,
     `GET /segmentos` 0,20 s.
   - **El denominador va con el porcentaje.** `/issues/resumen?por_segmento=true`
     da, por tipo, los segmentos con más páginas afectadas con
     `{paginas, urls, pct}`, y con `?segmento=` cada fila lleva `pct_paginas` y
     `urls_denominador`: el 12 % sin «de 7.234 URLs del segmento» no dice nada
     (68). El denominador son TODAS las URLs del segmento —con sus 404 y sus
     saltos—, que es lo que una regla dice que es el segmento.
   - **Lo que es de un hallazgo y no de una página no se reparte.** Las
     incidencias que se emiten una vez por imagen, por host, por bloque de
     plantilla o por enlace de plantilla (53, 64, 73, 106) llevan
     `paginas_afectadas` y cuelgan de una página de anclaje; repartirlas por el
     segmento de esa página afirmaría que el hallazgo es de ese segmento. Quedan
     fuera del filtro y del reparto, y cada fila dice cuántas (`agregadas`).
     Tres cosas que la primera versión hacía mal: (a) con `paginas_afectadas`
     **= 1** el hallazgo sí es de una sola página —la foto propia de un
     producto— y es la de anclaje, así que se reparte; son la mayoría de las
     imágenes sin alt en algunos censos (**310 de 354 en progym**, 243 de 19.287
     en Lopesan); (b) con `?segmento=`, un tipo que solo tiene hallazgos de
     varias páginas **desaparecía** del resumen —con él, el aviso de
     `agregadas`—, y Problemas se leía como «las fichas no tienen errores de
     datos estructurados» cuando era «no se pueden repartir» (67, 89): ahora
     sale con 0 páginas y sus `agregadas`; (c) el Explorador, al que lleva
     «Ver en el Explorador», contaba también la página de anclaje: con
     `?segmento=` y `?issue_type=` aplica la misma exclusión, para que la tabla
     y la tarjeta digan lo mismo. Y la lista de Problemas («Ver las N
     incidencias», «Ver todas como lista») conserva el segmento elegido y lo
     dice en su barra.

   Aplicado a todo: `?segmento=` (repetible: O) en `/urls`, su CSV y la vista
   agrupada (vía `_urls_filtradas`), en `/issues` y `/issues/resumen`; columna
   «Segmento» ordenable en el Explorador y en el CSV de la vista;
   `/urls/plantillas?agrupar=segmento`; selector en el Explorador y en
   Problemas (y «Ver en el Explorador» conserva el segmento). `api/uso.py`
   cuenta `filtro:segmento` y `agrupar:<plantilla|segmento>`, nunca el nombre
   del segmento, que lo pone el consultor; la vista agrupada manda `agrupar`
   también por plantilla, que es el valor por defecto y sin él no se contaba. `/stats` no se filtra: su desglose
   por segmento es `GET /segmentos`.

   Medido sobre Druni con cinco reglas escritas para él (`blog` y `tiendas` y
   `marcas` por ruta; `fichas de producto` = `og_type` igual a `product` y
   `categorias` = `og_type` igual a `website` con 2xx). La base local no tiene
   la tabla de 104, así que la extracción `og_type` se simuló a partir de
   `html_meta.og_type` de las HTML 2xx (lo que habría dado
   `meta[property=og:type]@content`), en una copia del censo en una base de
   pruebas; las incidencias son las del análisis guardado del censo, no
   re-analizadas. Resultado: 35.862 fichas, 8.518 marcas, 7.234 categorías,
   5.291 blog, 277 tiendas y 3.217 al repuesto; las fichas tienen **2.116 URLs
   sin saber** (saltos, 404 y no HTML: sin fila de extracción), que es la regla de los tres
   valores trabajando. Lo que se lee de un vistazo y no se veía antes: el
   **56 %** de las fichas tiene el título demasiado largo frente al 0,9 % de
   las categorías; de las categorías, el **82 %** es casi duplicada, el
   **84 %** no tiene meta description, el **71 %** tiene hreflang sin retorno y
   el **60 %** es huérfana; y los 404 están en `marcas` (16 % de sus URLs) y en
   el repuesto (249 en `/:slug (1n)`), no en las fichas — porque una ficha rota no da
   `og_type` y una regla por extracción no puede reconocerla: para eso, una
   regla por ruta.

   **Fuera de esta PR**: que las alertas de la comparación (68) se midan por
   segmento en vez de por forma de ruta, y la evolución por segmento entre
   censos (5.3); la pantalla para editar las reglas (hoy van en la
   configuración del cliente, del perfil o del rastreo, o por la API); la
   columna en el CSV completo de 83 columnas, y que la importación de un backup
   la conserve (se recupera re-segmentando: 2 s).
