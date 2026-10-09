# Qué contarle a cada cliente, y con qué números

## Importante antes de usar ninguna cifra: re-analizar no es re-rastrear

**Un re-análisis arregla lo que decide el análisis, no lo que el extractor
guardó mal.** Dos de los cuatro censos están solo re-analizados, y sus cifras
no son comparables con las de los otros dos:

| cliente | entregado | hoy | cómo se obtuvo |
|---|---|---|---|
| **Lopesan** | 315.119 | **11.988** | **re-rastreado** el 8-oct con el código actual |
| **Saunier Duval** | 15.550 | **8.399** | **re-rastreado** el 8-oct con el código actual |
| penguin (87.531 págs) | 959.633 | ~347.936 | *solo re-analizado*; el rastreo es del 13-jul |
| CST, árbol en castellano | 97.524 | ~54.095 | *solo re-analizado*; el rastreo es del 25-jun |

Las dos primeras son sólidas. Las dos últimas **no**, y está medido:

- **No tienen ni una sola** de las columnas de contenido de M1: `content_word_count`
  y `content_hash` están a cero en las 87.531 páginas de penguin y en las 8.838
  de CST. Eso significa que «contenido escaso» se sigue midiendo sobre el cuerpo
  entero —megamenú y pie incluidos— y que el duplicado exacto cae al hash de los
  bytes, que no encuentra nada (decisiones 44 y 47).
- En CST, **el 89% de los títulos tiene hoy un ancho en píxeles distinto** del
  guardado, con desviaciones de hasta 205 px sobre un umbral de 580. Es un sitio
  en árabe y el estimador viejo estaba ciego fuera del alfabeto latino
  (decisión 54). Lo escribe el spider, así que re-analizar no lo toca.

**Para esos dos clientes no hay informe correcto sin volver a rastrear.** Lo
que sigue de penguin se mantiene porque lo he comprobado contra el sitio en
vivo, una por una, y no depende de nada que el extractor guardara mal.

Material para decidir si se re-emiten los informes. **Nada de esto se ha
enviado**: son los hechos ordenados, con la fuente de cada cifra.

Las dos revisiones salen de comparar tres estados del mismo sitio: el informe
entregado, el mismo censo re-analizado con el código corregido, y un rastreo
nuevo hecho con la configuración original. El método y sus límites están en
[`DIFF_CENSOS_ENTREGADOS.md`](DIFF_CENSOS_ENTREGADOS.md).

---

## Lopesan

### 1. Tuvieron un fallo grave, lo arreglaron, y podemos demostrar lo que valía

Entre el 6 y el 8 de octubre, **2.367 páginas** —el 99,5% de la sección de
hoteles en los tres idiomas— declaraban como versión canónica una URL del
servidor de origen (`webserver-lopesan-prd.lfr.cloud`) en lugar de la suya.
Dicho simple: **le estaban diciendo a Google que no indexara sus fichas de
hotel, sino copias alojadas en otro sitio**. Ese servidor responde
públicamente, así que no era inocuo.

Lo arreglaron. Medido con el mismo rastreo, misma configuración:

| | antes | después |
|---|---|---|
| páginas afectadas | 2.367 | **51** |
| páginas que Google puede indexar | 46,6% | **84,1%** |
| fuerza interna que llega a páginas indexables | 27,5% | **78,6%** |

**Quedan 51 páginas** con el canonical viejo (rastreo del 8 de octubre). Es lo
primero que hay que decirles, y es un arreglo de minutos para ellos.

El detalle que lo hace fácil de explicar: en las 51 **la ruta es idéntica y
solo cambia el host**. Cada una declara como canónica *a sí misma, pero en el
servidor de pruebas*. No se están consolidando en otra página: se están
consolidando en su propia copia alojada donde no toca.

Están repartidas casi a partes iguales entre los tres idiomas —18 en alemán, 17
en inglés, 16 en castellano—, así que son más o menos las mismas 17 páginas en
sus tres versiones: 13 de habitaciones, 3 de gastronomía y el resto ofertas y
landings (bodas, excursiones en barco, pago seguro, informes de
sostenibilidad).

<details><summary>Las 51 rutas</summary>

- `/de/angebote-geburstag-om-thalasso-villa-del-conde`
- `/de/angebote-om-spa-costa-meloneras-geburstag`
- `/de/angebote-pool-meer-blick-om-thalsso-villa-del-conde`
- `/de/angebote-wellness-pakete-om-spa-costa-meloneras`
- `/de/angebote-wellness-pakete-om-thalasso-villa-del-conde`
- `/de/boots-ausflug`
- `/de/boots-ausflug/`
- `/de/hochzeiten`
- `/de/hochzeiten/`
- `/de/hotels/dominikanische-republik/punta-cana/playa-bavaro/splash-cove/zimmer/junior-suite-swim-up-queen`
- `/de/hotels/spanien/gran-canaria/meloneras/baobab-resort/zimmer/doppelzimmer-standard-blick`
- `/de/hotels/spanien/gran-canaria/playa-del-ingles/abora-buenaventura/zimmer`
- `/de/hotels/spanien/gran-canaria/playa-del-ingles/abora-catarina/gastronomie`
- `/de/hotels/spanien/gran-canaria/playa-del-ingles/abora-catarina/zimmer`
- `/de/hotels/spanien/gran-canaria/san-agustin/abora-interclub-atlantic/zimmer/angepasstes-doppelzimmer-familie`
- `/de/sicher-online-bezahlen`
- `/de/sicher-online-bezahlen/`
- `/de/unternehmen/lopesan-for-good/nachhaltigkeitsberichte`
- `/en/boats-trips`
- `/en/boats-trips/`
- `/en/corporate/lopesan-for-good/sustainability-reports`
- `/en/hotels/dominican-republic/punta-cana/playa-bavaro/splash-cove/rooms/junior-suite-swim-up-queen`
- `/en/hotels/spain/gran-canaria/playa-del-ingles/abora-buenaventura/rooms`
- `/en/hotels/spain/gran-canaria/playa-del-ingles/abora-catarina/gastronomy`
- `/en/hotels/spain/gran-canaria/playa-del-ingles/abora-catarina/rooms`
- `/en/hotels/spain/gran-canaria/san-agustin/abora-interclub-atlantic/rooms/double-family-adapted`
- `/en/offer-birthday-om-thalasso-villa-del-conde`
- `/en/offer-om-spa-costa-meloneras-birthday`
- `/en/offer-packs-wellness-om-spa-costa-meloneras`
- `/en/offer-pool-sea-view-om-thalsso-villa-del-conde`
- `/en/offer-wellness-packs-om-thalasso-villa-del-conde`
- `/en/online-secure-payment`
- `/en/online-secure-payment/`
- `/en/weddings`
- `/en/weddings/`
- `/es/bodas`
- `/es/bodas/`
- `/es/corporativa/lopesan-for-good/memorias-sostenibilidad`
- `/es/hoteles/espana/gran-canaria/playa-del-ingles/abora-buenaventura/habitaciones`
- `/es/hoteles/espana/gran-canaria/playa-del-ingles/abora-catarina/gastronomia`
- `/es/hoteles/espana/gran-canaria/playa-del-ingles/abora-catarina/habitaciones`
- `/es/hoteles/espana/gran-canaria/san-agustin/abora-interclub-atlantic/habitaciones/doble-familiar-adaptada`
- `/es/hoteles/republica-dominicana/punta-cana/playa-bavaro/splash-cove/habitaciones/junior-suite-swim-up-queen`
- `/es/nautica-excursiones-en-barco`
- `/es/nautica-excursiones-en-barco/`
- `/es/oferta-cumpleanos-om-thalasso-villa-del-conde`
- `/es/oferta-om-spa-costa-meloneras-cumpleanos`
- `/es/oferta-packs-de-bienestar-om-spa-costa-meloneras`
- `/es/oferta-packs-de-bienestar-om-thalasso-villa-del-conde`
- `/es/oferta-piscina-vista-mar-om-thalsso-villa-del-conde`
- `/es/pago-seguro-online`

</details>

### 2. El informe que tienen exagera, y mucho

El informe entregado listaba **315.119 incidencias**. El recuento correcto es
**23.161**. La mayor parte de la diferencia es un solo error nuestro:

| | en el informe | real |
|---|---|---|
| imágenes sin texto alternativo | 287.175 | **3.997** |

Contábamos como imágenes cosas que no lo son (variantes responsive de la misma
imagen, placeholders de carga diferida) y repetíamos el aviso una vez por
página en lugar de una vez por imagen. El logo del pie salía 10.990 veces.

### 3. Un detalle que hay que explicar antes de que lo lean mal

Al arreglar el canonical, **aparecen 178 títulos y 249 H1 duplicados** que
antes no salían. No es un empeoramiento: esas páginas antes estaban
canonicalizadas fuera y Google no las posicionaba, así que sus títulos
repetidos no competían con nada. Ahora que vuelven al índice, sí.

---

## Saunier Duval

### El informe entregado tiene ruido, y se le escapaban cosas

| | en el informe | según un rastreo de hoy |
|---|---|---|
| incidencias totales | 15.550 | **8.612** |
| imágenes sin texto alternativo | 4.849 | **73** |
| contenido duplicado exacto | 0 | **695** |
| contenido casi duplicado | 0 | **749** |

Dos cosas a la vez: **le dimos ~7.000 problemas que no existían** (el mismo
fallo de las imágenes, avisos de seguridad repetidos una vez por página en vez
de una por servidor, y una métrica de texto que no es señal de Google), y **no
vimos 695 páginas con contenido idéntico a otra**, que sí lo es.

La nota global pasa de **72 a 76**.

---

## Penguin Libros

El censo entregado (87.531 páginas, julio) listaba **959.633 incidencias**.
Re-analizado con el código de hoy quedan **347.936**, y el censo guardado
ya lleva esa cifra sellada con la versión que la calculó. Casi todo lo que sobra era ruido
nuestro:

| | en el informe | real |
|---|---|---|
| imágenes sin texto alternativo | 445.718 | **45.745** |
| cabeceras de seguridad | 168.607 | **1** |
| «demasiados enlaces salientes» | 27.656 | **0** |
| páginas huérfanas | 26.116 | **639** |
| datos estructurados | 226.651 | **4** |

Lo de «demasiados enlaces salientes» saltaba en **todas** las páginas porque
contábamos el megamenú como enlaces editoriales. Y las huérfanas eran un 98%
falsas.

### Lo que sí hay que contarles (comprobado contra el sitio en vivo)

Estos tres los he verificado pidiendo las páginas hoy, no fiándome del censo de
julio. El recuento total de incidencias, en cambio, no es de fiar hasta
re-rastrear.

**Cuatro problemas de datos estructurados, y los cuatro son de plantilla:**

- **83.764 páginas** declaran un `Organization` **sin `name`**. Sin esa
  propiedad Google no genera el resultado enriquecido: es el bloque entero
  tirado a la basura.
- 84.305 páginas con un `Organization` sin `sameAs` ni `contactPoint` (sale,
  pero más pobre).
- 58.552 fichas de producto sin `brand`.
- 30 reseñas sin `itemReviewed`.

**1.144 páginas de autor con el título vacío.** El `<title>` es literalmente
`| Penguin Libros ES`: la plantilla es `{autor} | Penguin Libros ES` y el
nombre no se rellena. Hay además 64 con el literal «Autor sin nombre».

**19.109 páginas indexables de `/es` no declaran canonical.** Es lo más
accionable de todo el censo: el sitio tiene **438.957 anotaciones de hreflang**
a seis variantes de español (es-ES, es-PE, es-UY, es-CL, es-CO, es-AR) con
contenido casi idéntico, y sin canonical es Google quien decide cuál de las
seis posiciona en cada búsqueda. Son 24.906 páginas sin canonical en total y
24.900 están bajo `/es`.

**50.760 páginas indexables compiten entre sí por el título** — 14.396 parejas
y 6.089 grupos de tres a cinco. En una librería esto es esperable (la misma
obra en varias ediciones), pero es decisión suya cuál debe posicionar: hoy
Google elige por su cuenta.

---

## Lo que yo propondría

- **Lopesan**: avisar de las 51 que quedan, y aprovechar para re-emitir con los
  números buenos. Tienen una mejora real que enseñar (27,5% → 78,6%).
- **Saunier Duval**: re-emitir. Lo que cambia la conversación no es la nota,
  son los 695 duplicados exactos que no habíamos visto.
- **En los dos**: decir que el informe anterior tenía avisos de más por un
  fallo nuestro de medición, ya corregido. Es más barato contarlo que esperar a
  que alguien compare dos informes y lo pregunte.
