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

**Quedan 51 páginas** con el canonical viejo. Es lo primero que hay que
decirles, y es un arreglo de minutos para ellos.

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
