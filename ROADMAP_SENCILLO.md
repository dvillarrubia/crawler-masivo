# Hoja de ruta en lenguaje llano

Para seguir el proyecto sin saber programar. Cuenta qué hace la herramienta,
qué está a punto de llegar y qué viene después, sin tecnicismos. El detalle
técnico está en [`ROADMAP.md`](ROADMAP.md) y en cada issue enlazada.

**Última actualización: 28 de octubre de 2026.**

Cómo leer cada punto:

- ✅ Funciona hoy en la herramienta.
- 🟡 Hecho y publicado, pendiente de probarlo con datos reales de un cliente.
- ⏳ En cola, sin empezar.
- 💤 Aparcado: no se va a hacer de momento.

---

## En una frase

La herramienta rastrea y audita sitios grandes como Screaming Frog, vigila la
cartera de clientes y avisa sola cuando algo se rompe. Además cruza cada
rastreo con lo que dice Google: Search Console, la inspección de URLs, los
backlinks y los logs del servidor. Lo que queda es sobre todo probar con
clientes lo último que ha entrado, el rastreo incremental y la parte de
inteligencia artificial, que espera a que una persona revise sus respuestas.

---

## ✅ Lo que ya funciona

**Rastreo**

- Rastrea sitios grandes, también los que necesitan JavaScript para mostrarse
  (y solo renderiza donde hace falta).
- Se hace pasar por un navegador normal y pasa muchos cortafuegos anti-bots.
  Cuando uno nos bloquea, lo dice en vez de dar el sitio por limpio.
- Se sigue en directo y se puede pausar, cancelar y reanudar.
- Respeta el robots.txt de cada bot, también el de los buscadores de IA, y
  dice qué línea bloquea cada página.
- Lee los sitemaps (de qué fichero viene cada URL) y, en los WordPress, lo que
  el propio WordPress dice que tiene publicado.
- Topes por carpeta o subdominio, para que un blog enorme no se coma el
  rastreo entero.
- Extracción a medida: el precio, el SKU, el stock o si tiene el código de
  Google Tag Manager, con una pantalla para crear las reglas y probarlas contra
  una URL.

**Auditoría SEO**

- Lo básico: códigos de respuesta, titles, descriptions, encabezados,
  canonicals, hreflang, datos estructurados e indexabilidad.
- Duplicados y casi duplicados, cadenas de redirección, imágenes, contenido
  escaso, páginas huérfanas, paginación y sitemaps.
- Páginas que dicen «no existe» pero responden como si existieran (soft 404).
- Lo que cambia entre la página tal como llega y tal como se ve en el
  navegador, y entre la versión móvil y la de escritorio.
- Accesibilidad por plantilla.
- Velocidad del servidor, compresión y certificado.
- Enlaces rotos a otros sitios e imágenes, estilos o scripts rotos.
- **Trampas de rastreo**: calendarios que no acaban nunca, filtros que se
  combinan sin fin, la sesión metida en la URL, la búsqueda interna enlazada.
  Dice cuántas páginas genera cada una y qué parte del rastreo se lleva.
- Cada incidencia explica qué es, qué hace Google y cómo se arregla, y va
  ordenada por lo que importa: gravedad por la importancia de las páginas
  afectadas.
- Segmentos de negocio: cada cliente define sus secciones («fichas», «blog») y
  se ve qué porcentaje de cada una tiene cada fallo.

**Enlazado y autoridad**

- PageRank interno: qué páginas reciben más fuerza del enlazado, sin contar
  los menús como contenido, y cuánta se pierde en errores y redirecciones.
- 🟡 **Autoridad desde fuera**: con un fichero de backlinks de Ahrefs, Semrush
  o Majestic, por dónde entra la autoridad de otros sitios y dónde se pierde
  (enlaces que llegan a páginas rotas, en noindex o bloqueadas).

**Lo que dice Google**

- **Search Console** de cada cliente, cada noche: clics, impresiones y
  posición de cada página.
- Lo que cuesta tráfico: páginas con clics que no se pueden indexar o dan
  error, y huérfanas con tráfico.
- 🟡 Oportunidades: páginas en posiciones 4-20 con poco enlazado (y desde qué
  páginas enlazarlas), CTR bajo para su posición, canibalización por búsqueda
  y sitemaps que Google no lee.
- Lo que Search Console no cuenta, dicho junto a sus cifras: la IA de Google
  sin separar y la parte de clics que Google anonimiza, medida en el sitio.
- Preguntar a Google por las URLs cada día: qué canonical ha elegido y si la
  tiene indexada.
- Cuando caen los clics entre dos rastreos, qué cambió en esas páginas.
- 🟡 **Logs del servidor**: qué visita Googlebot de verdad (comprobando que es
  Google y no alguien que se hace pasar por él), qué páginas importantes no
  visita y en qué se le va el tiempo.

**Clientes y seguimiento**

- Ficha por cliente con sus perfiles de rastreo (principal, blog, idiomas,
  competidores) y su configuración, que heredan los rastreos.
- Rastreos programados (cada día, semana o mes) y «Lanzar ahora». Nunca dos
  rastreos del mismo sitio a la vez, y lo programado espera a lo que se lanza
  a mano.
- Cada rastreo se compara con el anterior: qué incidencias se arreglaron,
  cuáles son nuevas y cuáles siguen.
- Alertas que se abren y se cierran solas, con reglas propias por cliente, en
  la herramienta y en el canal de Slack del cliente, más un resumen semanal.
- La evolución de cada cliente en gráficas y un panel de toda la cartera.
- Modo «comprobar esta lista de URLs tras un despliegue», que dice si pasa o
  falla.

**Entregables**

- Informe para el cliente que se imprime a PDF, Excel de varias hojas y CSV de
  cualquier vista.
- Un enlace de solo lectura para que cada cliente vea sus informes.
- Conexión con Claude y Cowork: preguntarle a la herramienta y lanzar o
  programar rastreos desde allí.

---

## 🟡 Inteligencia artificial: hecha la base, falta que una persona la revise

Índice: [#6](https://github.com/dvillarrubia/crawler-masivo/issues/6).

Siempre bajo demanda: un botón en el rastreo que enseña cuánto va a costar
antes de lanzarlo, con un tope de gasto. Nada de pago se lanza solo.

- ✅ La base: el botón, la estimación de coste, el tope de gasto y la caché.
- ✅ Preguntas hechas: soft 404 difíciles, tipo de página, categoría del
  cliente, intención de búsqueda y etapa del embudo, canibalización juzgada,
  title genérico o que no describe la página, y datos estructurados que dicen
  cosas que no están en la página.
- 🟡 **Revisar a Jev**: una pantalla para que una persona diga si Jev acertó,
  sin ver antes su respuesta.
- ⏳ **Lo que falta para que cuente**: revisar suficientes respuestas de cada
  pregunta para saber cuánto acierta y fijarle un umbral
  ([#8](https://github.com/dvillarrubia/crawler-masivo/issues/8)). Hasta
  entonces, nada de lo que dice la IA llega a un informe.
- ⏳ Después: higiene y seguridad
  ([#19](https://github.com/dvillarrubia/crawler-masivo/issues/19)), calidad
  del texto extraído ([#15](https://github.com/dvillarrubia/crawler-masivo/issues/15)),
  control del scraping ([#20](https://github.com/dvillarrubia/crawler-masivo/issues/20)),
  poda de contenido ([#198](https://github.com/dvillarrubia/crawler-masivo/issues/198))
  y preparación para respuestas de IA ([#199](https://github.com/dvillarrubia/crawler-masivo/issues/199)).

---

## ⏳ Lo siguiente

- ⏳ **Probar con clientes lo último que ha entrado**: los backlinks, los logs
  del servidor, las oportunidades de Search Console y el WordPress, con datos
  reales y revisado a mano.
- ⏳ **Rastreo incremental**
  ([#36](https://github.com/dvillarrubia/crawler-masivo/issues/36)): que el
  rastreo semanal solo vuelva a pedir lo que cambió.
- ⏳ **Rastreo más listo**
  ([#14](https://github.com/dvillarrubia/crawler-masivo/issues/14)): frenar las
  trampas mientras se rastrea (hoy se detectan después) y pedir antes lo que
  más vale.
- ⏳ **Más datos de fuera**: backlinks por la API de DataForSEO
  ([#31](https://github.com/dvillarrubia/crawler-masivo/issues/31)), y BigQuery,
  GA4 y DataForSEO para Search Console
  ([#170](https://github.com/dvillarrubia/crawler-masivo/issues/170)).
- ⏳ **Entregables que faltan**: el Excel adjunto en Slack y Google Sheets para
  Looker Studio.
- ⏳ Restos menores de auditorías anteriores
  ([#107](https://github.com/dvillarrubia/crawler-masivo/issues/107)).

---

## 💤 Aparcado

- 💤 El análisis semántico (la prueba anterior): no se continúa.

---

<sub>Para quien mantiene el proyecto: cuando una funcionalidad cambie de estado
en `ROADMAP.md`, cambia también su icono aquí y la fecha de arriba, en el
mismo lenguaje llano.</sub>
