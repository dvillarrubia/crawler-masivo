# Decisiones: datos estructurados

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d51"></a>
51. **El `@graph` de Yoast dejaba ciego a todo WordPress** — un bloque con
   `@graph` se guardaba como UNA fila con `schema_type` NULL, así que las 2-6
   entidades de dentro no existían: ni filtro por tipo, ni informe por tipo, ni
   validación por entidad. Medido en blogs.uoc.edu: **30.701 bloques en 29.803
   páginas** —el 100% del censo— con el tipo a NULL. Ahora el `@graph` se abre
   en una entidad por nodo. Con él, cuatro arreglos más del mismo sitio: el
   `@type` se normaliza a nombre corto (RDFa lo escribía como IRI completo y
   JSON-LD/microdatos como nombre corto, así que el mismo tipo salía con dos
   nombres y los filtros no casaban); el JSON-LD envuelto en `//<![CDATA[`
   —Drupal, portales antiguos— se lee en vez de desaparecer sin aviso; un nodo
   que solo es una referencia (`{"@id": …}`) no es un bloque roto; y una
   propiedad obligatoria presente pero **vacía** cuenta como ausente
   (`itemListElement: []` daba «ok» a una miga de pan sin eslabones). Las
   entidades anidadas también se validan: un Product dentro de
   `WebPage.mainEntity` es el producto de la página. Y `Article` deja de exigir
   `headline`: Google no documenta ninguna propiedad obligatoria para Article,
   así que es un aviso, no un error.

<a id="d73"></a>
73. **Los datos estructurados también son un aviso por hallazgo** — el mismo
   criterio de la decisión 64b con las imágenes y la 53 con las cabeceras, que
   faltaba aquí. Un bloque de plantilla —el `Organization` del pie, la miga de
   pan— es el MISMO en todo el sitio y se arregla una vez, y se emitía una
   incidencia por bloque. Medido en tres censos, corriendo el código nuevo
   sobre los datos guardados en producción y deshaciendo la escritura:

   | censo | bloques | incidencias antes | después |
   |---|---|---|---|
   | penguin (87.531 páginas) | 377.171 | **226.651** | **4** |
   | Lopesan | 45.774 | 11.186 | **13** |
   | Saunier Duval | 1.685 | 288 | **7** |

   En penguin esas 226.651 filas eran el **39% de las incidencias del censo
   entero** diciendo cuatro cosas: 84.305 páginas con un `Organization` sin
   `sameAs`, 83.764 con uno sin `name`, 58.552 `Product` sin `brand` y 30
   `Review` sin `itemReviewed`. No se pierde nada: cada fila lleva
   `paginas_afectadas` y cinco URLs de ejemplo, y `structured_data` conserva
   todos los bloques con su validación.
   De paso, la validación se escribía con **un UPDATE por bloque**: 226.651
   sentencias para grabar cuatro valores distintos. Ahora va por lotes
   agrupados por veredicto, y el chequeo entero tarda **33,9 s** en ese censo.
   Lo mismo en `analyze_hreflang`, que lo hacía igual y encima escribía DOS
   veces la misma fila cuando el destino estaba roto (la segunda poniendo
   `return_tag_ok` a NULL, que es lo que ya decide la decisión 56). Penguin
   tiene 438.957 anotaciones y de ahí salen seis veredictos. Medido sobre los
   mismos datos: **284,9 s → 20,3 s, catorce veces**, con los veredictos
   IDÉNTICOS (42 / 5.743 / 433.172) y las mismas incidencias — que es lo que
   lo convierte en una optimización y no en un cambio de criterio disfrazado.

<a id="d80"></a>
80. **Un JSON-LD que no es JSON no es un bloque válido, aunque se pueda leer**
   (#28) — Google parsea el JSON-LD como JSON estricto: una coma final o un
   comentario hacen que descarte el bloque ENTERO. extruct reintenta con
   `jstyleson`, que perdona las dos cosas, así que esos bloques se guardaban y
   validaban como buenos: «ok» justo donde Google no ve nada. Ahora el JSON-LD
   se lee en el extractor (`_leer_json_ld`), con el mismo selector y el mismo
   lector permisivo que extruct para no perder el bloque —el tipo y el resto
   del marcado siguen sirviendo para auditar—, y el motivo viaja en
   `structured_data.json_invalido` («coma final antes de '}' (línea 3,
   columna 1)», «comentario dentro del JSON…»). La validación lo pone el
   PRIMERO de la lista como error y sigue validando el resto, para que al
   arreglar la coma no aparezca una segunda tanda por sorpresa. De paso: un
   bloque que ni el permisivo entiende hacía saltar una excepción en extruct
   que se llevaba **todos** los datos estructurados de la página, microdatos y
   RDFa incluidos; ahora sale como bloque con `raw` NULL y error, y el resto
   sigue. El CDATA (decisión 51) no cuenta como inválido, y `strict=False`
   —caracteres de control dentro de una cadena— se mantiene: no está
   comprobado que Google los rechace. Los rastreos anteriores llevan la
   columna a NULL: hay que **re-rastrear**, no basta re-analizar (decisión 66).
   Y la tabla de requisitos tiene ya un test que la compara ENTERA con una
   tabla escrita a partir de la documentación de Google
   (`REQUISITOS_ESPERADOS` en `tests/test_sd_validation.py`, con la página de
   origen de cada tipo). Al escribirlo salieron cuatro obligatorias que Google
   no pide: `Organization.name` (no documenta ninguna, como Article en la
   decisión 51), `VideoObject.description`, `Course.provider`, y
   `SoftwareApplication.aggregateRating`, que es «valoración O reseña» igual
   que en Product. Las cuatro pasan a recomendadas (o a `uno_de`).

<a id="d97"></a>
97. **Un resultado enriquecido que Google ya no muestra no tiene propiedades
   obligatorias** — `FAQPage` exigía `mainEntity` y `HowTo` exigía `name` y
   `step`, así que un FAQ sin preguntas salía como **error**, con el mismo peso
   en el informe que un Product sin precio, y uno completo salía «ok», que se
   lee como «este marcado te da algo en Google». Ninguna de las dos cosas es
   verdad. Comprobado el 2026-10-16 en el registro de cambios de Search Central
   (<https://developers.google.com/search/updates>):
   - **HowTo**: retirado el 2023-09-14 («Removed the How-to structured data
     documentation, as this rich result is no longer shown in search
     results»).
   - **FAQ**: limitado el 2023-09-14 a «well-known, authoritative government
     and health websites»; anunciada la retirada el 2026-05-08 («will no longer
     appear in Google Search starting May 7, 2026») y documentación borrada el
     2026-06-15. Es decir, ya no está «restringido», está **retirado** para
     todo el mundo.
   Salen de `REQUISITOS` y pasan a `RESULTADOS_RETIRADOS`
   (`analysis/sd_validation.py`), con la fecha y la fuente. Un bloque de esos
   tipos —también anidado, p. ej. `WebPage.mainEntity`— lleva un único aviso
   de nivel **`info`** que dice qué pasó, cuándo y dónde está escrito, y no se
   le exige nada más. Es `info` y no `warning` porque no hay nada que arreglar:
   el marcado es schema.org válido, otros buscadores y asistentes pueden
   leerlo, y pedir que se quite sería inventar trabajo. `validation_status`
   gana el valor `info` (orden: error > warning > info > ok) y el análisis
   emite `structured_data_info` con la agrupación de la decisión 73: una fila
   por hallazgo con `paginas_afectadas`, no una por página — un FAQ de
   plantilla en 30 páginas es una incidencia. `QAPage` es otro resultado y
   sigue documentado y vivo (página actualizada el 2026-09-08): no cambia.
   Los tests fijan las dos fechas (`RETIRADOS_ESPERADOS`) y que ningún tipo
   retirado vuelva a la tabla de requisitos. No hace falta re-rastrear: basta
   **re-analizar**.
   Un bloque guarda el PEOR nivel en `validation_status`, pero el aviso de
   retirado es su propio hallazgo: un FAQPage anidado en un WebPage al que le
   falta una recomendada da su `structured_data_info` **y** el
   `structured_data_warning` del WebPage, este en el mismo grupo que el de las
   páginas sin FAQ (`problemas_structured_data` devuelve el nivel de cada
   mensaje). Errores y warnings del mismo bloque siguen siendo una sola
   incidencia, como antes.
   **Ojo al re-analizar un censo ya entregado: sus cifras de `/insights`
   cambian.** Un FAQPage sin `mainEntity` o un HowTo sin `name`/`step` estaban
   en `validation_status='error'`; pasan a `info`. Bajan `sd_errors` y
   `pct_errors` (`_calc_structured_data`), sube la nota de Datos
   Estructurados y puede desaparecer la recomendación «Corregir errores de
   validacion». Y si esa `structured_data_error` era el único error del
   censo, deja de aplicarse el tope de 79 de la decisión 92, así que también
   puede subir la nota global. No es un fallo: esos «errores» eran de un
   resultado que Google ya no muestra. Si las cifras no cuadran con el informe
   entregado, la explicación es esta (como en la decisión 38).

<a id="d146"></a>
146. **Que una propiedad esté no basta: también se mira que Google pueda leer
   su valor** (Ola 8, «formato de valores»). La validación (decisiones 51, 73,
   80 y 97) miraba solo presencia: un `startDate` «19 de octubre», un `price`
   «19,99 €» o un `priceCurrency` «€» pasaban por buenos, y el Rich Results
   Test los da por inválidos. Ahora `_problemas_de_valores`, en las entidades
   de resultado enriquecido (`REQUISITOS`) y bajando a sus objetos (`offers`,
   `aggregateRating`, `reviewRating`...) sin repetir las entidades que se
   validan aparte:
   - **fechas** ISO 8601 (`AAAA-MM-DD`, con hora y zona opcionales): error si
     la propiedad es obligatoria del tipo (el `startDate` de un Event, el
     `uploadDate` de un vídeo), aviso si no;
   - **precio** numérico con punto decimal y sin símbolo, **moneda** ISO 4217
     de tres letras y **`ratingValue`** numérico: error, porque invalidan la
     oferta o la valoración;
   - **disponibilidad** del vocabulario `schema.org/ItemAvailability` (con o sin
     la URL delante) y **recuentos** enteros: aviso.
   **No se validan las URLs**: Google resuelve las relativas contra la de la
   página, y avisar de ellas sería inventarse un problema (la primera versión lo
   hacía y saltaba en las fichas de los propios tests con `"image": "a.png"`).
   Los mensajes **no llevan el valor**, para que el análisis los siga agrupando
   por hallazgo (decisión 73): con el valor dentro, cada página sería uno.
   Medido sobre los últimos censos de producción (hasta 80.000 bloques cada
   uno): **0** en CST, Saunier, Lopesan y Salle; en penguin, **55 reseñas con
   `datePublished` «28/08/2020»**, que Google no puede leer (¿día/mes o
   mes/día?). Basta con re-analizar.
