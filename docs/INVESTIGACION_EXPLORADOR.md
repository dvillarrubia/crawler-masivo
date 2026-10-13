# El Explorador: para qué sirve en SEO y qué le falta

> Investigación del 2026-10-14 sobre el Explorador de URLs (`#/rastreos/<id>/explorador`).
> Hecha con el censo de Lopesan (`e77ac4f6`, 5.751 URLs) en una API local con el
> código de `master`, recorriéndolo con Playwright, leyendo `frontend/app.js`
> (`EXP_COLUMN_DEFS`, `EXP_TABS`, `_buildExplorerParams`) y
> `api/routers/results.py` (`list_urls`), y comparándolo con
> `screaming_frog_complete_fields_reference.md`. Complementa a
> `docs/AUDITORIA_UX.md`, que miraba la aplicación entera.

## Resumen

El Explorador es una **tabla técnica bien hecha**: filtros por columna con
operadores, orden, 44 columnas a elegir, navegación con flechas y un panel de
detalle. Pero está organizado como la pestaña «Internal» de Screaming Frog
—por tipo de recurso, código de estado e indexabilidad— y **no por las
preguntas que hace un SEO**: «¿qué títulos se cortan?», «¿qué plantilla genera
estos 404?», «¿desde dónde se enlazan?». De **17 preguntas habituales**, contesta
bien **5**, a medias **6** y no contesta **6** (tabla de abajo).

Lo que más pesa, en orden:

1. **El CSV del Explorador exporta el censo entero.** El botón «CSV» de la
   barra llama a `exportCSV()` → `/export`: las 83 columnas de todas las URLs,
   sin la pestaña, sin los filtros y sin las columnas elegidas. Quien filtra
   «4xx con enlaces entrantes» y exporta para mandárselo a desarrollo recibe
   5.751 filas.
2. **No habla con Problemas.** El servidor ya filtra `/urls` por `issue_type` y
   `severity` (comprobado: 2.367 URLs con `canonical_cross_domain`), pero la
   interfaz no lo ofrece. Problemas dice *qué* pasa; el Explorador debería ser
   donde se ve *en qué páginas*, con sus columnas, y no lo es.
3. **No agrupa por plantilla.** En SEO técnico casi nada se arregla URL a URL:
   se arregla una plantilla. En la vista 4xx de Lopesan salen
   `/de/null`, `/en/null`, `/es/null`, `/en/corporate/null`… y
   `/de/%7B%22es_ES…`, `/en/%7B%22es_ES…`: **dos fallos de plantilla que se
   leen como 12 URLs sueltas**. La agrupación por forma de URL ya existe en la
   pestaña Limpieza (`/blog/{s}/{s}` · 1.154 páginas) y en
   `shared/plantillas.py`, pero no en el Explorador.
4. **Datos que ya llegan y no se ven.** Cada fila trae `in_sitemap`,
   `title_pixel_width`, `meta_description_pixel_width`, `x_robots_tag`,
   `canonical_header`, `meta_refresh` y `has_meta_outside_head`, y ninguno es
   columna. Para «títulos que Google corta» la única columna es la longitud en
   **caracteres**, que es justo el criterio que la decisión 54 descartó (8.801
   títulos de más de 60 caracteres no se cortaban).
5. **Pesa diez veces más de lo que usa.** Cada página de 100 filas son
   **1,6 MB, y el 89 % es el texto completo de cada página** (`page_content`,
   `joinedload` en `list_urls`), que la tabla no pinta y que el panel de detalle
   vuelve a pedir con `/urls/<id>`.

## Qué preguntas SEO contesta

Comprobado contra la herramienta, no supuesto. «Parcial» = se puede, pero con
un rodeo que hay que saber.

| # | Pregunta | ¿Contesta? | Cómo / qué falta |
|---|---|---|---|
| 1 | ¿Qué URLs dan 4xx/5xx y cuántos enlaces reciben? | Sí | Pestaña 4xx/5xx, columna Inlinks |
| 2 | ¿Desde qué páginas se enlazan esos 4xx? | Parcial | Solo URL a URL (panel → Inlinks). No hay columna «enlazada desde» ni exportación de los orígenes de la vista |
| 3 | ¿Qué redirecciones siguen recibiendo enlaces internos? | Sí | 3xx + Inlinks ≥ 1 + «Redirige a» |
| 4 | ¿Cadenas de redirección? | No | Solo en Problemas (`redirect_chain`) |
| 5 | ¿Indexables con poco contenido? | Sí | Indexables + «Palabras contenido» ≤ 200 (la columna no viene por defecto en esa pestaña) |
| 6 | ¿Títulos / descripciones que Google corta? | No | Solo longitud en caracteres; el ancho en píxeles llega y no es columna |
| 7 | ¿Títulos / descripciones duplicados? | No | No hay filtro de duplicado (sí en Problemas) |
| 8 | ¿Páginas sin título o sin description? | Parcial | No hay filtro «vacío»; hay que ordenar por longitud ascendente |
| 9 | ¿Canonicalizadas a otra URL / a otro dominio? | Parcial | «Razón noindex» contiene «Canonicalised»; para el dominio, «Canonical» contiene el host |
| 10 | ¿Noindex por meta o por cabecera X-Robots-Tag? | Parcial | Meta robots es columna; X-Robots-Tag llega y no lo es |
| 11 | ¿Páginas fuertes (PageRank) no indexables o lejos de la home? | Sí | PageRank (0-100) ≥ X + Indexable = No / Clics desde home ≥ 4 |
| 12 | ¿Huérfanas y fuera del sitemap? | Parcial | Inlinks = 0 sí; «en el sitemap» llega y no es columna |
| 13 | ¿Casi duplicadas? | Sí | Casi duplicadas ≥ 1, Similitud máx. (pero ordenar por similitud no hace nada, ver abajo) |
| 14 | ¿Imágenes sin alt o pesadas? | Parcial | Peso sí (pestaña Imágenes); alt no (vive en `resources` y en Problemas) |
| 15 | ¿Hreflang y datos estructurados de un conjunto de páginas? | No | Solo en el panel de una URL |
| 16 | ¿Qué plantilla concentra un problema? | No | No hay agrupación por forma de URL |
| 17 | ¿Qué ha cambiado en estas URLs desde el censo anterior? | No | Vive en Comparar, sin cruce con el Explorador |

Cinco **sí** (1, 3, 5, 11, 13), seis **parciales** (2, 8, 9, 10, 12, 14) y
seis **no** (4, 6, 7, 15, 16, 17).

## Lógica SEO: dos modelos mentales

| | Screaming Frog | Nuestro Explorador |
|---|---|---|
| Eje de las pestañas | Elemento SEO: Page Titles, Meta Description, H1, Canonicals, Directives, Hreflang, Structured Data, Images… | Tipo de recurso, código de estado, interna/externa, indexabilidad |
| Filtros dentro de una pestaña | Del elemento: *Missing*, *Duplicate*, *Over X Pixels*, *Canonicalised*, *Noindex*, *Outside &lt;head&gt;* | Genéricos por columna: contiene, ≥, ≤, = |
| De dónde salen esos filtros | Del propio rastreo | **Ya los calcula nuestro analizador**: cada filtro de SF es casi un `issue_type` (`title_too_long`, `title_duplicate`, `canonical_multiple`, `canonical_in_body`…) |

Es decir: **lo difícil ya está hecho**. Los 60 tipos de incidencia de
`frontend/incidencias.js` son, agrupados por ámbito, las pestañas de Screaming
Frog. Lo que falta es que el Explorador los use como filtros.

Y hay una diferencia a nuestro favor que hoy no se aprovecha: Screaming Frog
no sabe de plantillas, de PageRank interno ponderado ni de censos anteriores.
Nosotros sí. Herramientas de rastreo a escala como Botify u OnCrawl organizan
todo por **segmentos** (grupos de páginas por plantilla o por sección), porque
con decenas de miles de URLs la unidad de trabajo no es la URL.

## UX y UI

Lo que funciona y hay que conservar:

- Filtros por columna con operador (≥, >, =, ≤, <) debajo de cada cabecera.
- ↑ ↓ para recorrer filas y el panel de detalle partido sin perder la tabla.
- Columnas elegidas por pestaña, guardadas en el navegador.
- Contadores en cada pestaña lateral.

Lo que falla:

| Qué | Efecto |
|---|---|
| El estado de la vista no está en la URL (solo `/explorador`) | Pestaña, filtros, orden y columnas no se pueden compartir, ni sobreviven a una recarga o al botón atrás |
| Cambiar de pestaña lateral borra filtros y búsqueda (`setExplorerTab`) | Pasar de «4xx» a «3xx» con el mismo filtro obliga a reescribirlo |
| El buscador de arriba y el filtro de la columna URL usan el mismo parámetro (`search`) | El segundo pisa al primero sin avisar |
| Selector de 44 columnas sin agrupar ni buscador, con tres PageRank (0-100, «x media», 0-10 antiguo) y «% texto» | Ruido. La decisión 34 dice que la que se lee es la 0-100, y la 44 que el ratio de texto no es una señal de Google |
| «Similitud máx.» se puede ordenar en la interfaz pero no está en `SORT_COLUMNS` | El clic no hace nada y no lo dice |
| No hay filtro «vacío / no vacío» | «Sin title» o «sin canonical» no se pueden pedir directamente |
| El panel de detalle abre en «Contenido» (markdown) | Lo primero que se busca al seleccionar una fila es título, canonical, indexabilidad y problemas: la ficha de URL ya se abre en SEO desde la fase 2 |
| La barra de ayuda («Filtra cada columna debajo de su nombre…») ocupa una fila fija | Bien la primera vez; después es espacio perdido |

## Rendimiento

| Medida | Valor |
|---|---|
| Una página de 100 filas | **1.604.992 bytes**, 0,40 s en local |
| De ellos, `page_content` | **1.427.118 bytes (89 %)** |
| Lo usa la tabla | No (ninguna columna lo lee) |
| Lo usa el panel | No: pide `/urls/<id>` aparte |

Quitar `page_content` del listado deja la página en unos 180 kB. Ojo: puede
haber consumidores externos de `/urls` que lean el contenido (scripts, n8n),
así que mejor un parámetro (`?ligero=1` o `?campos=`) que cambiar el valor por
defecto sin avisar.

## Uso real: no hay datos

No se ha podido medir cómo se usa. La API no deja rastro duradero: los logs del
contenedor se borran en cada despliegue (el de esta noche dejaba cero
peticiones a `/urls`) y no hay un proxy delante con logs propios. **Antes de
invertir en las partes grandes conviene medirlo**: un log ligero de qué pestaña,
filtros, orden y columnas pide cada consulta del Explorador, guardado en una
tabla o en Redis con caducidad. En dos semanas de uso diría qué pestañas no se
abren nunca y qué filtros se escriben a mano una y otra vez.

## Recomendaciones

Por orden de impacto entre coste. Cada una dice qué decisión SEO desbloquea.

### Rápidas (horas, sin cambiar el modelo)

1. **CSV de la vista.** `/export` acepta los mismos filtros que `/urls` y las
   columnas visibles. *Desbloquea:* mandar a desarrollo exactamente la lista
   que hay que arreglar.
2. **Listado ligero.** `/urls?ligero=1` sin `page_content` para el Explorador.
   *Desbloquea:* paginar 50.000 URLs sin descargar 800 MB.
3. **Filtro por problema.** Selector «Con el problema…» que usa `issue_type` y
   `severity` (el servidor ya lo admite) y un botón «Ver en el Explorador» en
   cada tipo de la vista agrupada de Problemas. *Desbloquea:* pasar de «2.367
   canonicals externos» a esas 2.367 URLs con sus columnas.
4. **Columnas que ya llegan:** ancho en píxeles de título y descripción, en el
   sitemap, X-Robots-Tag, canonical por cabecera y meta refresh. Agrupar las de
   PageRank y esconder por defecto la 0-10 y «% texto». *Desbloquea:* las
   preguntas 6, 10 y 12.
5. **La vista en la URL:** pestaña, filtros, orden y columnas en el hash, como
   ya hace Problemas. *Desbloquea:* «mira esto» con un enlace.
6. **Arreglos pequeños:** ordenar por similitud en el servidor; buscador global
   y filtro de URL separados; filtro «vacío / no vacío»; mantener los filtros
   compatibles al cambiar de pestaña; abrir el panel en un resumen SEO.

### De fondo (días)

7. **Vistas SEO al estilo Screaming Frog, construidas con lo que ya hay.**
   Pestañas Títulos, Descripciones, Encabezados, Canonicals, Directivas,
   Hreflang, Datos estructurados, Imágenes, Enlazado (huérfanas, profundidad,
   solo por redirección) y Contenido (escaso, duplicado, casi duplicado). Cada
   una es *(filtro + columnas + chips con los tipos de incidencia de su ámbito y
   su recuento)*. *Desbloquea:* las preguntas 4, 7, 8, 9 y 15 sin aprender a
   combinar filtros.
8. **Agrupar por plantilla.** Un modo «por forma de URL» que reutilice
   `shared/plantillas.py`: cada fila es una plantilla con sus URLs, cuántas
   indexables, cuántos errores y la mediana de palabras, PageRank y profundidad;
   un clic la despliega. *Desbloquea:* la pregunta 16 y la forma correcta de
   presentar un hallazgo («la plantilla de ficha de hotel en alemán genera 404
   en `/null`»).
9. **De dónde vienen los enlaces.** Para las vistas 3xx/4xx, columna «enlazada
   desde» (el origen más frecuente y cuántos más) y exportación de los enlaces
   entrantes de las URLs de la vista. *Desbloquea:* la pregunta 2, que es la que
   convierte un 404 en una tarea.
10. **Medir el uso** (ver arriba) antes de 7 y 8.

### Después

11. Vistas guardadas por cliente, en el servidor, para repetir el mismo análisis
    en cada censo.
12. Columna «cambio desde el censo anterior» (pregunta 17), cruzando con lo que
    ya calcula `shared/comparacion.py`.
13. Filtros multivalor y por expresión regular en URL.

## Cómo se comprobó cada cifra

- Tamaño de página: `curl …/urls?page_size=100` sobre Lopesan en la API local.
- Filtro por problema en el servidor: `…/urls?issue_type=canonical_cross_domain` → `total: 2367`.
- CSV: `exportCSV()` en `frontend/app.js` abre `/api/jobs/<id>/export` sin parámetros.
- Columnas sin uso: diferencia entre las claves de una fila de `/urls` y `EXP_COLUMN_DEFS`.
- Orden por similitud: `closest_similarity` está en `_RANGE_FILTERS` y no en `SORT_COLUMNS`.
- Logs: `docker compose logs api` en el VPS justo después del despliegue → 0 líneas de `/urls`.

## Estado (2026-10-14)

Todo lo recomendado está hecho y desplegado (#132-#136), en una noche:

| Recomendación | PR | Medido |
|---|---|---|
| CSV de la vista | #132 | Lopesan, «HTML sin título»: 32 filas, las mismas que en pantalla |
| Listado ligero | #132 | 1,6 MB → 250 kB por página de 100 filas |
| Filtro por problema y «Ver en el Explorador» | #132 | Canonical externo → 2.367 URLs |
| Columnas que ya llegaban (píxeles, sitemap, X-Robots-Tag…) | #132 | — |
| Vista en la URL, filtros que sobreviven al cambio de pestaña | #132 | Recarga y atrás comprobados |
| Ordenar por similitud, URL ≠ buscador, vacío/con valor, panel en resumen SEO | #132 | — |
| Medir el uso | #133 | `GET /api/uso/explorador` |
| Agrupar por plantilla | #134 | 32 errores 4xx de Lopesan: 12 son dos fallos (`{"es_ES":""}` × 6, `null` × 6) |
| Enlazada desde + CSV de enlaces entrantes | #134 | `/de/unternehmen/null` ← `/de/unternehmen/lopesan-geschichte` × 3 |
| Vistas por ámbito SEO | #135 | 10 vistas con chips por tipo de incidencia |
| Vistas guardadas por cliente | #136 | — |
| Cambio frente al censo anterior | #136 | Con los avisos de versión encima de la tabla |
| Filtros `a|b`, `!a`, varios códigos | #136 | «4xx sin null»: 26 de 32 |

Lo siguiente con fundamento es **leer `/api/uso/explorador` dentro de un par
de semanas** y quitar lo que no se use: diez vistas por ámbito y 52 columnas
son muchas, y ahora se puede saber cuáles sobran.
