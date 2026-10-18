# Hoja de ruta en lenguaje llano

Para seguir el proyecto sin saber programar. Cuenta qué hace la herramienta,
qué está a punto de llegar y qué viene después, sin tecnicismos. El detalle
técnico está en [`ROADMAP.md`](ROADMAP.md) y en cada issue enlazada.

**Última actualización: 18 de octubre de 2026.**

Cómo leer cada punto:

- ✅ Funciona hoy en la herramienta.
- ⏳ En cola, sin empezar.
- 💤 Aparcado: no se va a hacer de momento.

---

## En una frase

La herramienta ya rastrea y audita sitios grandes como Screaming Frog, compara
cada rastreo con el anterior y avisa sola cuando algo se rompe. El 17 y el 18
de octubre se publicó una tanda grande: todo lo que salió de compararnos con
FreeCrawl, los segmentos de negocio, la evolución de la cartera, el modo lista
para revisar despliegues y la comparación ampliada entre rastreos. Lo
siguiente es Search Console, y después vigilar la cartera y los entregables.
La inteligencia artificial va por su cuenta en cuanto haya clave.

---

## ✅ Lo que ya funciona

**Rastreo**

- Rastrea sitios grandes, también los que necesitan JavaScript para mostrarse.
- Pasa muchos cortafuegos anti-bots haciéndose pasar por un navegador normal, y
  cuando uno nos bloquea lo dice, en vez de dar el sitio por limpio.
- Se sigue en directo, se puede pausar, cancelar y reanudar.
- Respeta el robots.txt y usa los sitemaps.
- Puede rastrear como Googlebot móvil, Bingbot o los bots de buscadores de IA,
  obedeciendo las reglas de robots.txt de ese bot.

**Auditoría SEO**

- Lo básico: códigos de respuesta, titles, descriptions, encabezados,
  canonicals, hreflang, datos estructurados e indexabilidad.
- Duplicados y casi duplicados, cadenas de redirección, imágenes sin alt,
  contenido escaso, páginas huérfanas y cobertura del sitemap.
- Canonicals que apuntan a errores, a redirecciones o a páginas no indexables,
  y canonicals en cadena; redirecciones que acaban en error.
- Hreflang completo: retorno, autorreferencia, x-default e idiomas repetidos.
- URLs del sitemap que no se pueden indexar, con el motivo.
- Páginas que Google corta por pasar de 2 MB o por una cabecera mal cerrada.
- Páginas que dicen «no existe» pero responden como si existieran (soft 404).
- Profundidad en clics desde la home.
- PageRank interno: qué páginas reciben más fuerza del enlazado interno, sin
  contar los menús como si fueran contenido, y cuánta se pierde en errores y
  redirecciones.
- Enlaces que Google no puede seguir y enlaces internos que apuntan a errores o
  llevan parámetros utm.

**Clientes y seguimiento**

- Ficha por cliente, con sus sitios (principal, blog, otros idiomas,
  competidores) y su configuración.
- Rastreos programados: cada semana, cada día o cada mes.
- Cada rastreo se compara solo con el anterior: qué incidencias se arreglaron,
  cuáles son nuevas y cuáles siguen. Si algo grave cambia, avisa arriba del
  todo.

**Datos y entregables**

- Explorador de URLs con vistas tipo Screaming Frog (títulos, descripciones,
  canonicals, enlazado…), filtros, vistas guardadas y exportación a CSV.
- Informe con puntuaciones y recomendaciones; cada incidencia explica qué es y
  cómo se arregla.
- Extracción a medida: sacar de cada página el precio, el SKU, el stock o
  comprobar si tiene el código de Google Tag Manager.
- Borrar rastreos viejos en masa.

---

## ✅ Publicado el 17 y el 18 de octubre

Ya está en la herramienta. Falta comprobar algunas cifras con rastreos reales
de un par de clientes (sobre todo lo de Googlebot en robots.txt, la
comparación móvil/escritorio y cuántos enlaces externos se comprueban) antes de
fiarnos del todo.

**Arreglos de cosas que hacíamos mal** ([#149](https://github.com/dvillarrubia/crawler-masivo/issues/149))

- ✅ Algunas páginas con noindex enviado «por cabecera» salían como
  indexables.
- ✅ Los casi duplicados contaban páginas que Google no indexa.
- ✅ Si el robots.txt de un sitio fallaba, lo tratábamos como «todo permitido»;
  Google lo trata como «todo bloqueado».
- ✅ Ahora se distingue lo que el robots.txt le prohíbe a Googlebot en
  concreto.
- ✅ La contraseña de un sitio en pruebas ya no se envía a otros dominios, y
  ya no guardamos las cookies que pone el servidor (Google tampoco).
- ✅ Cadenas de redirección muy largas, varios canonicals distintos en la misma
  página y hreflang repetido o enviado por cabecera.

**Comprobaciones nuevas** ([#150](https://github.com/dvillarrubia/crawler-masivo/issues/150))

- ✅ Canonicals que se contradicen, o noindex junto a una canonical a otra
  página.
- ✅ Redirecciones que acaban en una página noindex, y redirecciones hechas con
  meta refresh o con JavaScript.
- ✅ H1 vacíos, titles de plantilla sin rellenar («Untitled», «Home»…) y
  descriptions iguales al title.
- ✅ Enlaces internos sin texto.
- ✅ Paginación mal hecha: páginas 2, 3… en noindex o canonicalizadas a la 1,
  o a las que no se llega con un enlace normal.
- ✅ Viewport para móvil, idioma de la página, codificación y etiquetas para
  redes sociales (Open Graph).
- ✅ Velocidad de respuesta del servidor, páginas sin comprimir y certificado
  de seguridad caducado o a punto de caducar.
- ✅ Diferencias entre la versión móvil y la de escritorio de cada plantilla.
- ✅ Enlaces a otros sitios que están rotos y dominios que ya no existen.
- ✅ Imágenes, estilos o scripts rotos dentro de las páginas
  ([#101](https://github.com/dvillarrubia/crawler-masivo/issues/101)).

**Segmentos de negocio** ([#172](https://github.com/dvillarrubia/crawler-masivo/pull/172))

- ✅ Cada cliente define sus secciones («fichas», «blog», «categorías») y
  Problemas dice qué porcentaje de cada una tiene cada fallo. Sirve también
  para los rastreos ya hechos, sin volver a rastrear.

**Seguimiento de clientes**

- ✅ **Evolución y panel de cartera** ([#181](https://github.com/dvillarrubia/crawler-masivo/pull/181)): cómo evoluciona
  cada sitio de rastreo en rastreo (errores, indexables, profundidad,
  PageRank desperdiciado) y un panel con todos los clientes.
- ✅ **Comparación ampliada entre rastreos** ([#183](https://github.com/dvillarrubia/crawler-masivo/pull/183)): avisa si
  las páginas se alejan de la home, pierden enlaces internos, PageRank, datos
  estructurados o hreflang; separa los fallos de páginas que ya existían de los
  de páginas recién publicadas, y entiende los cambios de ruta de una
  migración.
- ✅ **Lo extraído a medida, comparado entre rastreos** ([#175](https://github.com/dvillarrubia/crawler-masivo/pull/175)):
  por ejemplo, cuántos precios suben o bajan, o cuántas fichas pasan de «en
  stock» a «agotado», con alertas por segmento.
- ✅ **Modo lista para revisar un despliegue** ([#176](https://github.com/dvillarrubia/crawler-masivo/pull/176)): se le da
  una lista de URLs y dice si pasa o falla frente a la vez anterior.

**Rastreo**

- ✅ **robots.txt por bot de IA** ([#177](https://github.com/dvillarrubia/crawler-masivo/pull/177)): qué dejan pasar a
  Googlebot, Bingbot, ChatGPT, Claude, Perplexity y los bots de entrenamiento,
  y qué línea concreta bloquea cada URL.
- ✅ **Sitemaps fichero a fichero** ([#178](https://github.com/dvillarrubia/crawler-masivo/pull/178)): de qué sitemap viene
  cada URL, su fecha de modificación y avisos por fichero: sitemaps que pasan
  del límite de Google, fechas de modificación en el futuro y URLs repetidas
  en varios sitemaps.
- ✅ **Huella de navegador actualizada** a Chrome 150 ([#182](https://github.com/dvillarrubia/crawler-masivo/pull/182)),
  para pasar mejor los cortafuegos anti-bots.
- ✅ La ficha del rastreo enseña el certificado, los soft 404, los enlaces
  rotos y los sitemaps, y el formulario trae las casillas de enlaces y
  recursos externos ([#180](https://github.com/dvillarrubia/crawler-masivo/pull/180)).

---

## ⏳ Lo siguiente: funcionalidades sin inteligencia artificial

En el orden del plan. Search Console tiene su propio apartado, más abajo.

- ⏳ **Extracción a medida y segmentos, segunda parte**
  ([#34](https://github.com/dvillarrubia/crawler-masivo/issues/34)): una
  pantalla para crear las reglas sin tocar configuración, probarlas contra una
  URL en vivo y que salgan como incidencia. Compararlas entre rastreos ya
  está hecho.
- ⏳ **Vigilar la cartera**
  ([#36](https://github.com/dvillarrubia/crawler-masivo/issues/36)): alertas
  que se abren y se cierran solas y reglas propias por cliente («más de 20
  errores en las fichas»). La evolución por cliente y el modo lista ya están.
- ⏳ **Entregables y acceso del cliente**: contraseñas y enlaces de solo
  lectura por cliente, el informe mensual en PDF sin retoques, Excel y Google
  Sheets para Looker Studio, envío automático tras cada rastreo programado y
  conexión con Claude y Cowork.
- ⏳ **JavaScript y buscadores de IA**: lo que cambia entre la página tal como
  llega y tal como se ve en el navegador, y si la página deja que los
  buscadores de IA la citen. Lo de robots.txt por bot de IA ya está.
- ⏳ **Sitios enormes más rápidos**
  ([#52](https://github.com/dvillarrubia/crawler-masivo/issues/52)): el análisis
  de un sitio con millones de enlaces todavía tarda.
- ⏳ **Pulido de la interfaz**
  ([#123](https://github.com/dvillarrubia/crawler-masivo/issues/123)): atajos de
  teclado, uso en el móvil y detalles.
- ⏳ **Restos de la comparación con FreeCrawl**
  ([#184](https://github.com/dvillarrubia/crawler-masivo/issues/184)): saltos de nivel en los encabezados, tamaño real de
  la imagen para redes sociales, vídeos y AMP.
- ⏳ Restos menores de auditorías anteriores
  ([#107](https://github.com/dvillarrubia/crawler-masivo/issues/107)).
- ⏳ **Más adelante**, cuando haya hueco: logs del servidor
  ([#35](https://github.com/dvillarrubia/crawler-masivo/issues/35), cuando un
  cliente los dé), velocidad de carga por plantilla, textos de enlace genéricos,
  accesibilidad, backlinks como entrada del PageRank
  ([#31](https://github.com/dvillarrubia/crawler-masivo/issues/31)), WordPress
  ([#33](https://github.com/dvillarrubia/crawler-masivo/issues/33)) y, lo
  último, el rastreo incremental que solo vuelve a pedir lo que cambió
  ([#36](https://github.com/dvillarrubia/crawler-masivo/issues/36)).

---

## ⏳ Search Console: el rastreo con los datos de Google

Índice: [#163](https://github.com/dvillarrubia/crawler-masivo/issues/163). Investigación en
[`docs/INVESTIGACION_SEARCH_CONSOLE.md`](docs/INVESTIGACION_SEARCH_CONSOLE.md).

El rastreo dice cómo está cada página; Search Console, qué consigue en Google.
Juntos dicen qué arreglar primero porque cuesta tráfico y qué oportunidades
hay a mano.

- ⏳ **Conectar Search Console** de cada cliente y traer sus datos cada noche,
  visibles en el explorador, la ficha y el CSV ([#164](https://github.com/dvillarrubia/crawler-masivo/issues/164)).
- ⏳ **Tráfico en riesgo**: páginas con clics que no se pueden indexar o dan
  error, huérfanas con tráfico, URLs con clics que el rastreo no encontró,
  indexables que nunca salen en Google, y el informe ordenado por tráfico
  ([#165](https://github.com/dvillarrubia/crawler-masivo/issues/165)).
- ⏳ **Oportunidades**: páginas en posiciones 4-20 con poco enlazado, diciendo
  desde qué páginas enlazarlas; CTR bajo para su posición; canibalización por
  búsqueda; qué secciones rinden; sitemaps que Google no lee ([#166](https://github.com/dvillarrubia/crawler-masivo/issues/166)).
- ⏳ **Alertas que explican la causa**: «han caído los clics en estas 40
  páginas, y en las 40 cambió el canonical» ([#167](https://github.com/dvillarrubia/crawler-masivo/issues/167)).
- ⏳ **Preguntar a Google por cada URL** (2.000 al día): qué canonical ha
  elegido, si la tiene indexada y dónde no estamos de acuerdo con él
  ([#168](https://github.com/dvillarrubia/crawler-masivo/issues/168)).
- ⏳ **Con Jev**: si la página responde a la intención de las búsquedas que la
  traen, el embudo con tráfico real y la canibalización juzgada ([#169](https://github.com/dvillarrubia/crawler-masivo/issues/169)).
- ⏳ **Más adelante**: BigQuery para clientes grandes, GA4 para priorizar por
  ventas y DataForSEO para explicar el CTR ([#170](https://github.com/dvillarrubia/crawler-masivo/issues/170)).

---

## ⏳ Análisis con inteligencia artificial

Índice: [#6](https://github.com/dvillarrubia/crawler-masivo/issues/6).

Siempre bajo demanda: un botón en el rastreo que enseña cuánto va a costar
antes de lanzarlo, con un tope de gasto. Nada de pago se lanza solo.

- ✅ La base: el botón «Análisis enriquecido», la estimación de coste, el tope
  de gasto y la caché (lo ya analizado no se vuelve a pagar).
- ⏳ **Juego de pruebas revisado a mano**
  ([#8](https://github.com/dvillarrubia/crawler-masivo/issues/8)): antes de
  fiarnos de la IA, medir cuánto acierta en cada pregunta.
- ⏳ **Bloqueos y soft 404 difíciles**
  ([#10](https://github.com/dvillarrubia/crawler-masivo/issues/10),
  [#11](https://github.com/dvillarrubia/crawler-masivo/issues/11)): los que no
  se pueden detectar con reglas fijas.
- ⏳ **Tipo de página**
  ([#12](https://github.com/dvillarrubia/crawler-masivo/issues/12)): ficha,
  categoría, artículo, landing…, para juzgar cada una con su vara de medir.
- ⏳ **Intención de búsqueda y etapa del embudo** (TOFU/MOFU/BOFU) de cada
  página, contrastadas con la intención de sus keywords
  ([#39](https://github.com/dvillarrubia/crawler-masivo/issues/39)).
- ⏳ **Comprobaciones con criterio**
  ([#16](https://github.com/dvillarrubia/crawler-masivo/issues/16),
  [#17](https://github.com/dvillarrubia/crawler-masivo/issues/17)): si el
  title, el H1 y el contenido hablan de lo mismo, alts que no describen la
  imagen, contenido caducado y datos estructurados que dicen cosas que no
  están en la página.
- ⏳ **Canibalización**
  ([#18](https://github.com/dvillarrubia/crawler-masivo/issues/18)): páginas que
  compiten por la misma búsqueda.
- ⏳ **Seguridad e higiene**
  ([#19](https://github.com/dvillarrubia/crawler-masivo/issues/19)): contenido
  hackeado, entornos de pruebas indexables, ventanas emergentes que tapan la
  página.
- ⏳ **Prioridad**
  ([#22](https://github.com/dvillarrubia/crawler-masivo/issues/22)): ordenar las
  incidencias por lo que importa en cada página, con menos ruido en el informe.
- ⏳ **Enlazado más fino**
  ([#13](https://github.com/dvillarrubia/crawler-masivo/issues/13)): que en el
  PageRank pese distinto un enlace del menú que uno dentro del texto, y detectar
  anchors genéricos tipo «leer más».
- ⏳ **Scraping con control**
  ([#20](https://github.com/dvillarrubia/crawler-masivo/issues/20)): comprobar
  que los datos extraídos tienen sentido y parar el rastreo si dejan de tenerlo.
- ⏳ Resto: rastreo más inteligente
  ([#14](https://github.com/dvillarrubia/crawler-masivo/issues/14)), calidad del
  texto extraído ([#15](https://github.com/dvillarrubia/crawler-masivo/issues/15))
  y comparación de contenidos con la competencia
  ([#21](https://github.com/dvillarrubia/crawler-masivo/issues/21)).

---

## 💤 Aparcado

- 💤 El análisis semántico (la prueba anterior): no se continúa.

---

<sub>Para quien mantiene el proyecto: cuando una funcionalidad cambie de estado
en `ROADMAP.md`, cambia también su icono aquí y la fecha de arriba, en el
mismo lenguaje llano.</sub>
