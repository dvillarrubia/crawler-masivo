# Roadmap

Qué hace hoy el crawler y qué viene después, contado por funcionalidades. El
detalle técnico de cada punto está en la issue de GitHub enlazada.

Estado a 5 de octubre de 2026.

---

## Lo que ya funciona

### Rastreo

- Rastrea sitios grandes, con o sin JavaScript, y aguanta muchos cortafuegos
  (WAF) haciéndose pasar por un navegador real.
- Se puede seguir en directo, cancelar, pausar y reanudar. Al reanudar repite
  las páginas que se perdieron o fallaron, y para solo si se atasca.
- Respeta robots.txt y usa los sitemaps.
- Sigue bien las redirecciones, incluidas las meta refresh, y guarda cada salto
  de la cadena.
- Configuración por cliente: reglas de plantillas de cada sitio.

### Auditoría SEO automática

- Códigos de respuesta, titles, descriptions, encabezados, canonicals,
  hreflang, datos estructurados e indexabilidad.
- Indexabilidad calculada sobre la URL final tras redirecciones; las páginas
  bloqueadas por robots.txt no cuentan como indexables.
- Duplicados exactos y casi duplicados (páginas que comparten el 90 % del texto
  o más).
- Cadenas de redirección, imágenes sin alt, seguridad, contenido escaso, URLs
  problemáticas, páginas huérfanas y cobertura del sitemap.
- PageRank interno:
  - las redirecciones y los canonicals pasan la autoridad a la página correcta;
  - los menús y enlaces repetidos no cuentan como contenido;
  - mide cuánto PageRank se desperdicia en errores y redirecciones.
- Aviso automático cuando el JavaScript esconde enlaces y el PageRank puede no
  ser fiable.

### Contenido y entregables

- Extracción del texto principal de cada página, con recuperación del titular
  cuando se perdía.
- Informe con puntuaciones y recomendaciones.
- Exportaciones: listado de URLs con 75 columnas, enlaces, contenido y copia de
  seguridad.

---

## En cola

El orden de los bloques es el orden recomendado: primero lo que corrige datos,
después lo nuevo.

### 1. Arreglos urgentes

- [#37](https://github.com/dvillarrubia/crawler-masivo/issues/37) La cola de
  trabajos va al revés, se ignora la opción «usar sitemap» y un sitio bloqueado
  por robots.txt da un trabajo «completado» con 0 URLs.

### 2. Arreglos pendientes de la auditoría

Índice: [#23](https://github.com/dvillarrubia/crawler-masivo/issues/23).

- [#24](https://github.com/dvillarrubia/crawler-masivo/issues/24) Rematar el
  PageRank: mostrarlo en una escala más fácil de leer y avisar en el propio dato
  cuando no es fiable por el JavaScript.
- [#25](https://github.com/dvillarrubia/crawler-masivo/issues/25) Rastreo:
  patrones de exclusión que rompen el rastreo, subdominios que se escapan y URLs
  bloqueadas por robots.txt que no aparecen en el listado.
- [#26](https://github.com/dvillarrubia/crawler-masivo/issues/26)
  Indexabilidad: directivas específicas de Googlebot, varias cabeceras robots y
  canonical enviado en cabecera.
- [#27](https://github.com/dvillarrubia/crawler-masivo/issues/27) Contenido:
  páginas que se quedan a 0 palabras, tablas desordenadas, recuento de palabras
  en japonés o chino y texto oculto.
- [#28](https://github.com/dvillarrubia/crawler-masivo/issues/28) Imágenes,
  datos estructurados y seguridad.
- [#29](https://github.com/dvillarrubia/crawler-masivo/issues/29) Restos del
  análisis automático.
- [#30](https://github.com/dvillarrubia/crawler-masivo/issues/30) Scripts de
  control.

### 3. Funcionalidades nuevas

- [#36](https://github.com/dvillarrubia/crawler-masivo/issues/36) **Clientes
  como centro**: rastreos programados, alertas cuando algo se rompe, informe por
  cliente con su evolución, y rastreos incrementales que solo vuelven a pedir lo
  que cambia.
- [#32](https://github.com/dvillarrubia/crawler-masivo/issues/32) **Comparar
  dos rastreos** del mismo sitio: qué se arregló, qué apareció y qué cambió.
- [#31](https://github.com/dvillarrubia/crawler-masivo/issues/31) **Backlinks**
  de Ahrefs o DataForSEO: un PageRank con la autoridad que entra desde fuera, y
  mapas de redirección para recuperar enlaces perdidos.
- [#34](https://github.com/dvillarrubia/crawler-masivo/issues/34) **Extracción
  personalizada**: sacar campos concretos de cada página (precio, SKU,
  autor…).
- [#35](https://github.com/dvillarrubia/crawler-masivo/issues/35) **Logs del
  servidor**: qué rastrea Googlebot de verdad, cruzado con el rastreo.
- [#33](https://github.com/dvillarrubia/crawler-masivo/issues/33)
  **WordPress**: cruzar el rastreo con lo que el propio WordPress dice que tiene
  publicado.

### 4. Análisis enriquecido con Jev

Índice: [#6](https://github.com/dvillarrubia/crawler-masivo/issues/6).

Siempre bajo demanda: un botón en el trabajo, con el coste estimado antes de
lanzarlo y un presupuesto máximo
([#40](https://github.com/dvillarrubia/crawler-masivo/issues/40)). Nada de pago
se lanza solo.

- Detectar bloqueos ([#10](https://github.com/dvillarrubia/crawler-masivo/issues/10))
  y soft 404 ([#11](https://github.com/dvillarrubia/crawler-masivo/issues/11)).
- Tipo de página ([#12](https://github.com/dvillarrubia/crawler-masivo/issues/12)).
- Intención de búsqueda y embudo TOFU/MOFU/BOFU, contrastados con las keywords
  ([#39](https://github.com/dvillarrubia/crawler-masivo/issues/39)).
- Checks on-page nuevos
  ([#16](https://github.com/dvillarrubia/crawler-masivo/issues/16)), coherencia
  del schema con lo visible
  ([#17](https://github.com/dvillarrubia/crawler-masivo/issues/17)),
  canibalización
  ([#18](https://github.com/dvillarrubia/crawler-masivo/issues/18)) y contenido
  hackeado ([#19](https://github.com/dvillarrubia/crawler-masivo/issues/19)).
- Ordenar el informe por lo que importa en cada página
  ([#22](https://github.com/dvillarrubia/crawler-masivo/issues/22)).
- Validar los datos extraídos y parar el rastreo si dejan de tener sentido
  ([#20](https://github.com/dvillarrubia/crawler-masivo/issues/20)).
- Resto: [#13](https://github.com/dvillarrubia/crawler-masivo/issues/13),
  [#14](https://github.com/dvillarrubia/crawler-masivo/issues/14),
  [#15](https://github.com/dvillarrubia/crawler-masivo/issues/15),
  [#21](https://github.com/dvillarrubia/crawler-masivo/issues/21). La base
  técnica está en [#7](https://github.com/dvillarrubia/crawler-masivo/issues/7),
  [#8](https://github.com/dvillarrubia/crawler-masivo/issues/8) y
  [#9](https://github.com/dvillarrubia/crawler-masivo/issues/9).

---

## Aparcado

- Google Search Console y Google Analytics 4.
- Análisis semántico (`POC_centro_semantico/`): era una prueba y no se va a
  continuar.
