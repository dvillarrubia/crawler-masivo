# Qué cambia en un censo ya entregado al re-analizarlo con el código de M1

El 7 de octubre de 2026 se cerró la parte determinista de M1. La pregunta que
quedaba, y que no responde ningún test, es: **¿los informes que ya están en
manos de un cliente decían cosas que hoy sabemos que son falsas?**

Esto lo responde con datos, no con suposiciones.

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
