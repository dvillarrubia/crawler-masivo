# Decisiones: extracción y búsqueda a medida

> Lo que el consultor pide a cada página con sus propias reglas (#34, Ola 3 de
> `docs/PLAN_MEJOR_CRAWLER.md`). Aquí irán también los segmentos de negocio
> (3.3) y la comparación de valores extraídos (3.4). Índice de todas las
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
     evaluando. Se rechaza también `//` absoluto dentro de un predicado
     (cuadrático). Las regex van con el módulo `regex` y `timeout` (50 ms), no
     con RE2: conserva lookaround y referencias hacia atrás, y no añade una
     rueda C++ incierta en aarch64. Ojo para los tests: `(a+)+$` **no** es
     catastrófica en `regex` (la optimiza; 0,4 ms); `(a|aa)+$` sí.
   - **Cortacircuitos.** 250 ms por página para todas las reglas
     (`presupuesto_pagina` para las que no caben) y una regla que agota su
     tiempo 3 veces queda `desactivada` el resto del rastreo, con un WARNING
     que sube por `resumir_stderr`. Sin eso, una regex catastrófica paga su
     tope en cada una de las 60.000 páginas: 50 minutos de reactor parado.
   - **Muro = `pagina_bloqueada`**, con los títulos de `TITULOS_DE_BLOQUEO`
     (62): un desafío de Cloudflare no «no contiene» el tag de GTM.
   - **Herencia: una regla se sustituye entera por nombre y `null` la quita.**
     Fusionarla campo a campo dejaría pegado lo que el perfil no repite (un
     `solo_urls` del cliente acotando la regla reescrita). Para eso hizo falta
     tocar `validar_config_parcial`: `_separar_nones` rechazaba
     `{"custom_extraction": {"precio": null}}` como clave desconocida, y una
     regla parcial no se puede validar (`tipo` y `expresion` son
     obligatorios). Al guardar un perfil se valida además la fusión con su
     cliente: una regla `fuente=contenido` sobre un cliente que apaga
     `extract_page_content` es un 422 al guardar, no en el primer programado.
   - **Una regla que el censo no tiene es un 422**, en el listado, el orden y
     los CSV: una vista guardada de otro censo no puede devolver el censo
     entero como si el filtro no existiera. El Explorador lo dice y ofrece
     quitar el filtro.
   - **El CSV escapa fórmulas** (`= + - @`, tabulador, retorno) en los valores
     de texto que vienen de la página; los números no se tocan. Con `todos`, una
     columna por posición («Talla 1», «Talla 2»…), tantas como el máximo de
     coincidencias del censo con el tope de la regla. En `/export` las columnas
     `x_<nombre>` van AL FINAL y solo si hay reglas: las 83 fijas no se mueven.
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
     página (objetivo < 300 ms), por `ix_cext_job_nombre_estado`. La primera
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
