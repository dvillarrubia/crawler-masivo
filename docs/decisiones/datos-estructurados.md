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
