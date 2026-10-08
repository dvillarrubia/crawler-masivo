# Qué cambia en un censo ya entregado al re-analizarlo con el código de M1

El 7 de octubre de 2026 se cerró la parte determinista de M1. La pregunta que
quedaba, y que no responde ningún test, es: **¿los informes que ya están en
manos de un cliente decían cosas que hoy sabemos que son falsas?**

Esto lo responde con datos, no con suposiciones.

## Antes de leer nada: qué prueba esto y qué no

**Los cuatro censos comparados son anteriores a los arreglos del extractor**
(Lopesan 6-oct, Saunier 10-sep, penguin 13-jul, CST 25-jun; los arreglos
entraron el 7-oct). Y un re-análisis **solo aplica los arreglos del análisis**:
lo que el extractor guardó mal el día del rastreo sigue guardado mal.

| | lo arregla un re-análisis | hace falta re-rastrear |
|---|---|---|
| duplicados solo entre indexables | ✅ | |
| cabeceras de seguridad, una por host | ✅ | |
| el ratio texto/HTML, retirado | ✅ | |
| alcance de los avisos de URL, cadenas de redirección | ✅ | |
| enlaces desde páginas rotas o `noindex` | ✅ | |
| severidad del canonical, agrupación de imágenes | ✅ | |
| **el texto propio de la página** (sin menú ni pie) | | ❌ |
| **títulos por anchura en píxeles** | | ❌ |
| **el `@graph` abierto en entidades** | | ❌ |
| **imágenes reales en vez del placeholder** | | ❌ |
| **URLs bloqueadas por robots, formatos de sitemap** | | ❌ |

Así que lo que miden estas comparaciones es **«qué diría hoy el análisis sobre
los mismos datos»**, que no es lo mismo que «qué diría un rastreo de hoy».

**Lo que sí se puede afirmar:** los avisos que DESAPARECEN son casi todos del
análisis, así que esos informes sí tenían esas líneas de más.

**Lo que NO se puede afirmar:** que las notas nuevas sean las que saldrían hoy.
En particular —y esto corrige una lectura que hice primero— **la subida de la
categoría «Contenido» no significa que el contenido estuviera infravalorado**.
La nota usa `pct_thin`, que cuenta las páginas con `low_word_count`,
`low_text_ratio` y `very_low_text_ratio`; al retirar los dos del ratio (que no
son señal de Google) la nota sube sola. Ninguno de los tres censos tiene la
columna del texto propio (0 de 8.049, 0 de 4.318 y 0 de 1.304 páginas), así que
el escaso se sigue midiendo sobre el body **con el menú dentro**. Cuando se
mide bien, aparecen MÁS páginas escasas, no menos: medido en otro censo, 997
por body contra 10.433 por contenido propio. **Un rastreo nuevo probablemente
baje esa nota, no la suba.**

## Cómo está medido

Sin tocar producción. Para cada censo:

1. Se congela lo que el VPS devuelve **hoy** (`/stats` e `/insights`): eso es lo
   que el cliente tiene.
2. Se descarga el censo entero (`GET /api/jobs/{id}/backup`) y se importa en
   local con un id nuevo.
3. Se comprueba que la copia es **fiel** —mismas notas en las seis categorías—
   antes de tocar nada.
4. Se re-analiza en local con el código de M1 y se compara.

El paso 3 no es ceremonia: sin él, cualquier diferencia podría ser del
transporte y no del análisis.

Está automatizado en
[`docs/experimentos/diff_censo_entregado.py`](experimentos/diff_censo_entregado.py),
que aborta si la copia no sale fiel:

```bash
python docs/experimentos/diff_censo_entregado.py <job_id_del_vps>
```

---

## Saunier Duval · `b8ea3e43` · 2.876 URLs · rastreo con render JS

Copia verificada: las seis categorías idénticas al VPS antes de re-analizar.

### La nota

| categoría | entregado | ahora | |
|---|---|---|---|
| **GLOBAL** | **72** | **77** | +5 |
| Rastreabilidad | 90 | 100 | +10 |
| **Contenido** | **23** | **43** | **+20** |
| Enlaces | 94 | 91 | −3 |
| **Datos Estructurados** | **97** | **82** | **−15** |
| Seguridad | 85 | 85 | — |
| Internacionalización | 50 | 50 | — |

### Las incidencias: 15.550 → 12.043 (−3.507, el 23%)

**Lo que desaparece — avisos falsos que el cliente leyó como problemas:**

| incidencia | entregado | ahora |
|---|---|---|
| `unsafe_crossorigin` | 1.299 | **0** |
| cabeceras de seguridad (4 tipos) | 1.500 | **12** |
| `low_text_ratio` + `very_low_text_ratio` | 735 | **0** |
| `url_too_long` | 755 | 535 |
| `title_too_long` | 343 | 197 |
| `h1_duplicate` | 967 | 814 |
| `title_duplicate` | 897 | 818 |
| `description_duplicate` | 419 | 281 |
| `high_outlink_count` | 94 | **0** |

Por qué: `unsafe_crossorigin` no era un problema desde 2021 (los navegadores
aplican `noopener` solos); las cabeceras de seguridad son del servidor y se
emitían una por página; el ratio texto/HTML no es una señal de Google y se
medía sobre la indentación de la plantilla; los títulos se cortan por píxeles y
no por caracteres; y los duplicados solo importan entre páginas que Google
puede posicionar.

**Lo que aparece — problemas reales que eran invisibles:**

| incidencia | entregado | ahora |
|---|---|---|
| `near_duplicate_content` | **0** | **724** |
| `structured_data_error` | **0** | **181** |
| `structured_data_warning` | 6 | 71 |
| `orphan_page` | 1 | 44 |

Esta es la mitad incómoda. El informe entregado le puso un **97 sobre 100** a
los datos estructurados de un sitio que hoy saca **82**, con 181 errores que
nadie vio: el `@graph` se guardaba como un bloque sin tipo, así que no había
nada que validar.

### Lectura

El informe entregado tenía **~3.500 problemas fantasma** y, a la vez, **se
dejaba 724 contenidos casi duplicados y 181 errores de datos estructurados**.
Las dos cosas a la vez, y las dos importan: la primera hace perder tiempo al
cliente, la segunda le deja un problema sin tocar.

**Recomendación: re-emitir.** Y al hacerlo, avisar de que la nota **sube** (72 →
77) pero que una categoría **baja** (datos estructurados 97 → 82), porque esa
conversación es mejor tenerla con el dato delante.

---

## Lopesan v4 · `b4b1b081` · 5.751 URLs

Copia verificada: nota 82 en el VPS y 82 en la copia antes de re-analizar.

### La nota

| categoría | entregado | ahora | |
|---|---|---|---|
| **GLOBAL** | **82** | **88** | +6 |
| **Contenido** | **64** | **97** | **+33** |
| **Datos Estructurados** | **100** | **85** | **−15** |
| Rastreabilidad / Enlaces / Seguridad / i18n | 73 / 99 / 85 / 96 | iguales | — |

Mismo patrón que Saunier Duval: el contenido estaba infravalorado y los datos
estructurados **sobrevalorados**. A este cliente se le entregó un **100 sobre
100** en marcado; hoy salen **4.716 errores y 6.942 avisos** que antes no se
veían porque el `@graph` se guardaba como un bloque sin tipo.

Y los duplicados se desploman al mirarlos solo entre páginas indexables:

| | entregado | ahora |
|---|---|---|
| `h1_duplicate` | 2.111 | **2** |
| `title_duplicate` | 1.955 | **2** |
| `description_duplicate` | 1.931 | **0** |

No es que el sitio no tenga títulos repetidos: es que **las páginas que los
repiten no las puede posicionar Google**, así que no compiten con nadie.

### Lo que apareció al mirar el PageRank

| a dónde va la autoridad interna | |
|---|---|
| páginas **indexables** | **27,5%** |
| páginas **no indexables** | **55,7%** |
| redirecciones | 16,2% |

Para comparar, en los otros censos medidos: blogs.uoc.edu 93,1%, progym 90,2%,
Saunier Duval 90,8%, www.uoc.edu 84,1%. **Lopesan es el único donde la mayoría
de la autoridad no llega a ninguna página indexable.**

La causa, mirando qué páginas se la llevan: **2.367 de las 4.318 páginas HTML
(el 55%) tienen el canonical apuntando a `https://webserver-lopesan-prd.lfr.cloud/…`**,
el servidor de origen de Liferay, no al dominio público. El sitio le está
diciendo a Google que la versión buena de cada página está en un host que no es
el suyo.

### Lo importante: eso SÍ estaba en el informe entregado

`canonical_cross_domain: 2.367`. Estaba. Y no lo vio nadie, porque:

- su severidad está puesta a **`info`**, y
- el informe tenía **315.119 incidencias**, de las cuales **287.175 (el 91%)
  son `image_missing_alt`** — **68,6 por página** en 4.185 páginas.

El hallazgo más grave que puede tener un sitio estaba archivado como
«informativo» dentro de un informe donde nueve de cada diez líneas son la misma
advertencia sobre imágenes.

## Lo que esto dice del producto

Las dos cosas que propongo a raíz de esto, **sin implementar**, porque son
criterio:

1. **La severidad tiene que reflejar la consecuencia.** Un canonical a otro
   dominio registrable saca a la página del índice; eso no es `info`. La regla
   que ya se aplicó en otros sitios —la severidad depende de lo que se pierde—
   aquí no está aplicada.
2. **Un aviso que salta en casi todas las páginas deja de ser un aviso.** Es la
   misma enfermedad que la decisión 53 curó con las cabeceras de seguridad
   (266.504 → una por host): `image_missing_alt` con 287.175 líneas no es
   accionable, entierra el resto, y pide un resumen por plantilla o por imagen
   repetida en vez de una línea por aparición.

---

## El caso Lopesan, cerrado: una regresión de horas cazada por casualidad

Al revisar lo anterior con el cliente resultó que **no era una configuración
vieja: era un error que acababa de entrar en producción**. Esto es lo que el
censo permite afirmar con fecha y hora.

**Qué pasó.** El canonical de las fichas de hotel apuntaba a
`https://webserver-lopesan-prd.lfr.cloud/…`, el servidor de origen de Liferay,
en vez de al dominio público.

**A qué afectó** — prácticamente la sección de hoteles entera, en los tres
idiomas:

| idioma | sección | con canonical al origen | páginas de la sección |
|---|---|---|---|
| es | `/hoteles` | **772** | 776 |
| en | `/hotels` | **716** | 720 |
| de | `/hotels` | **681** | 685 |

Más ~86 de corporativa y 9 de políticas de privacidad. **2.367 en total**, de
las que **2.169 son fichas de hotel** — el 99,5% de esa sección. Para una
cadena hotelera, las páginas que venden.

**Cuándo.** El rastreo corrió el **2026-10-06 de 09:00:04 a 11:30:56 UTC** y
las páginas afectadas se vieron entre las **09:49:08 y las 11:25:58**.

**Por qué era grave y no cosmético.** `webserver-lopesan-prd.lfr.cloud`
**responde HTTP 200 al público**. No era un canonical a un host inaccesible que
Google acabaría ignorando: era una invitación a indexar el host de origen en
lugar de la marca.

**Estado actual.** Comprobado en vivo el 2026-10-07: las seis páginas afectadas
con más enlaces entrantes sirven ya el canonical correcto. Está arreglado.

### La lección, que es de producto

Esto se cazó **por casualidad**: el rastreo coincidió con la ventana en que el
error estuvo vivo. Un fallo que dura unas horas y se arregla no deja rastro en
ningún sitio salvo que alguien esté rastreando justo entonces.

Es exactamente el caso de uso de
[#36](https://github.com/dvillarrubia/crawler-masivo/issues/36) —rastreos
programados y alertas cuando algo se rompe—, y este incidente es el argumento
más fuerte que hay para priorizarlo: el valor no estuvo en el informe, estuvo
en **tener un censo del momento exacto**.

---

## CST · `e96d7f44` · 12.112 URLs · `www.cst.gov.sa/en`, render JS

El único sitio no europeo que hay, y por eso el que más interesaba: es donde
los arreglos de escrituras no latinas se encuentran con algo real en vez de con
un test escrito por quien hizo el arreglo.

Copia verificada: nota 74 en el VPS y 74 en la copia.

| categoría | entregado | ahora | |
|---|---|---|---|
| **GLOBAL** | **74** | **77** | +3 |
| **Contenido** | **17** | **33** | **+16** |
| Enlaces | 100 | 95 | −5 |
| Datos Estructurados | 100 | 100 | — |
| Rastreabilidad / Seguridad | 83 / 92 | iguales | — |
| Internacionalización | 100 | 99 | −1 |

**88.986 → 49.207 incidencias.** `image_missing_alt` 27.705 → 2.585, el ratio
texto/HTML 8.045 → 0, las cabeceras 8.051 → 1. Y aparecen 1.595 casi
duplicados, 166 hreflang sin retorno y **3.939 páginas a las que solo se llega
por una redirección**.

### El susto, y lo que enseñó

El primer re-análisis dio **`structured_data_error`: 0 → 88.838** y tumbó la
categoría de 100 a 60. Son 88.838 de los 88.842 bloques del censo: el 99,99%.
Un sitio con el 100% del marcado roto no existe; eso era un falso positivo
nuestro.

Mirando el dato guardado, los 88.838 son de `format=rdfa` y sin `@type`:

- **81.023** son atributos de accesibilidad — `<div role="alert">` deja un nodo
  con `http://www.w3.org/1999/xhtml/vocab#role`;
- **7.815** son Open Graph — `<meta property="og:title">` deja
  `http://ogp.me/ns#title`, que además ya se extrae aparte a `html_meta.og_*`.

Ninguna de las dos cosas es marcado de schema.org: es lo que un lector de RDFa
saca de cualquier página normal. **El extractor de hoy ya no los guarda**
(comprobado con las dos clases), pero el censo es de junio y los lleva dentro.

Arreglado en el validador: un nodo cuyas propiedades son todas de vocabularios
ajenos (XHTML, Open Graph, Dublin Core) no es un bloque roto, no es un bloque.
Con eso, CST vuelve a **100 en datos estructurados y 0 errores**, y su nota
global sube como la de los demás en vez de bajar.

### La lección, que vale para todo este documento

**Un re-análisis arregla lo que decide el análisis; lo que el extractor guardó
mal sigue mal.** Ya había pasado con `urls.noindex` (que al materializarse
cambia el PageRank de un censo viejo) y vuelve a pasar aquí. Al re-analizar un
censo anterior a un arreglo del extractor hay que mirar si lo que aparece es un
hallazgo del sitio o un resto de cómo se guardó — y la pista es siempre la
misma: **una cifra que sale al 99% de algo no es un hallazgo, es un error de
medida.**

---

## La prueba que faltaba: re-rastrear Saunier Duval

Todo lo anterior compara **lo entregado** con **lo que dice re-analizar los
mismos datos**. Faltaba la tercera cifra: **lo que dice un rastreo de hoy**.
Se lanzó con las 1.678 semillas y las 17 claves de configuración originales,
cambiando solo el nombre (job `40ea596f`, 2.916 URLs frente a 2.876: +1,4%).

| categoría | entregado | re-analizado | **rastreo nuevo** |
|---|---|---|---|
| **GLOBAL** | **72** | 77 | **76** |
| Rastreabilidad | 90 | 100 | 100 |
| **Contenido** | **23** | 43 | **38** |
| Enlaces | 94 | 91 | 88 |
| **Datos Estructurados** | **97** | 82 | **85** |
| Seguridad | 85 | 85 | 86 |
| Internacionalización | 50 | 50 | 50 |

| | entregado | re-analizado | **rastreo nuevo** |
|---|---|---|---|
| **incidencias** | **15.550** | 10.637 | **8.612** |

### Qué contesta esto

**1. El re-análisis es una buena aproximación, no la respuesta.** La nota
global se queda a un punto (77 contra 76), pero por categoría se desvía hasta
**5 puntos**. Sirve para decidir si merece la pena re-rastrear; no para
entregarlo.

**2. La corrección sobre «Contenido» era correcta.** Se avisó arriba de que la
subida a 43 no significaba que el contenido estuviera infravalorado, sino que
habíamos retirado dos avisos que no son señal de Google, y que al medir el
texto propio aparecerían **más** páginas escasas. El rastreo nuevo lo confirma:
**43 → 38**. Sigue muy por encima del 23 entregado, pero cinco puntos por
debajo de lo que sugería el re-análisis.

**3. Hay hallazgos que SOLO aparecen re-rastreando.** Los dos más claros:

| incidencia | entregado | re-analizado | rastreo nuevo |
|---|---|---|---|
| `image_missing_alt` | 4.849 | 3.338 | **73** |
| `duplicate_content` | 0 | 0 | **695** |

- Las imágenes sin alt eran **casi todas un artefacto del extractor viejo**
  (`<source>` de `<picture>` y placeholders de carga diferida contados como
  imágenes). El re-análisis solo pudo agruparlas; hacía falta volver a mirar la
  página para que desaparecieran. De 4.849 avisos entregados, los reales son
  **73**.
- Los **695 duplicados exactos** no podían verse de ninguna forma sin
  re-rastrear: el hash del contenido es una columna que no existía. Son 695
  páginas con el contenido principal idéntico a otra.

### La regla práctica

Para **decidir** si un informe entregado merece revisión: re-analizar, que
cuesta minutos. Para **entregar** cualquier cifra: re-rastrear. Y nunca
presentar como hallazgo del sitio algo que un re-análisis hace aparecer, sin
comprobar antes que no es un resto de cómo se guardó.

---

## Lopesan re-rastreado: el arreglo del cliente, medido

El censo `08e6d5eb` repite el de `b4b1b081` con las 3.040 semillas y las 20
claves de configuración originales, dos días después y con el código de M1.
En medio, el cliente arregló el canonical.

| categoría | entregado | re-analizado | **rastreo nuevo** |
|---|---|---|---|
| **GLOBAL** | **82** | 88 | **90** |
| Rastreabilidad | 73 | 73 | **84** |
| Contenido | 64 | 97 | **93** |
| Datos Estructurados | 100 | 85 | **88** |
| **incidencias** | **315.119** | 38.922 | **23.161** |

### Lo primero: el arreglo funcionó, y se puede demostrar

| | entregado | rastreo nuevo |
|---|---|---|
| `canonical_cross_domain` | **2.367** | **51** |
| páginas indexables | 46,6% | **84,1%** (3.237 de 3.848) |
| PageRank que llega a páginas indexables | **27,5%** | **78,6%** |

De volcar casi tres cuartas partes de su autoridad interna en páginas que no
podía indexar, a que el 78,6% llegue donde tiene que llegar. **Eso es lo que
valía el arreglo**, y sin volver a rastrear no habría forma de ponerle número.

**Pero quedan 51 páginas** con el canonical apuntando todavía a
`webserver-lopesan-prd.lfr.cloud`. El mismo fallo, sin terminar de limpiar.

### Lo segundo: arreglar algo hace aparecer lo que tapaba

| | re-analizado | rastreo nuevo |
|---|---|---|
| `title_duplicate` | 2 | **178** |
| `h1_duplicate` | 2 | **249** |

No es un empeoramiento: es que esas páginas **antes no las podía posicionar
Google** —estaban canonicalizadas fuera— y por eso sus títulos repetidos no
competían con nadie (decisión 47). Ahora que son indexables, sí. Es el efecto
secundario normal de arreglar un canonical, y conviene contarlo antes de que
el cliente lo lea como una regresión.

### Y la confirmación de lo que ya se sabía

`image_missing_alt`: **287.175 entregados → 19.287 agrupando → 3.997 reales**.
El 98,6% de lo que se entregó al cliente sobre imágenes no existía.
