# Qué contarle a cada cliente, y con qué números

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

## Lo que yo propondría

- **Lopesan**: avisar de las 51 que quedan, y aprovechar para re-emitir con los
  números buenos. Tienen una mejora real que enseñar (27,5% → 78,6%).
- **Saunier Duval**: re-emitir. Lo que cambia la conversación no es la nota,
  son los 695 duplicados exactos que no habíamos visto.
- **En los dos**: decir que el informe anterior tenía avisos de más por un
  fallo nuestro de medición, ya corregido. Es más barato contarlo que esperar a
  que alguien compare dos informes y lo pregunte.
