# SEO Crawler de Lin3s

Crawler y auditor SEO del estilo de Screaming Frog, pensado para una agencia. Rastrea cada sitio de una cartera de clientes, lo
compara con su rastreo anterior, lo cruza con los datos de Google y avisa
cuando algo se rompe.

**FastAPI + Scrapy + PostgreSQL + Redis**, con Playwright para lo que necesita
JavaScript. Interfaz web estática (Alpine.js) servida por la propia API.

> Si eres una persona o un agente que va a tocar el código, empieza por
> [`CLAUDE.md`](CLAUDE.md): es el índice de las reglas que no se pueden romper y
> de dónde está cada cosa.

---

## Qué hace

**Rastreo**
- Sitios de decenas de miles de URLs: el mayor rastreo en producción tiene
  90.000 (36 horas). Render JS por muestra de plantilla y sin render donde no
  hace falta. Huella TLS y user-agent de un Chrome real. Topes por carpeta o
  subdominio.
- Detecta los muros (WAF, captcha, límite de peticiones) y lo dice, en vez de
  dar el sitio por limpio. Respeta el robots.txt de cada bot, también el de los
  bots de IA, y dice qué línea bloquea cada URL.
- Lee los sitemaps (de qué fichero viene cada URL, su `lastmod`) y la REST API
  de WordPress.
- Extracción y búsqueda a medida (precio, SKU, stock, una etiqueta concreta),
  probada contra una URL en vivo.

**Auditoría**
- Indexabilidad, canonicals, hreflang (HTML, cabecera y sitemap), datos
  estructurados y sus valores, titles, descriptions, encabezados, paginación,
  duplicados y casi duplicados (MinHash), soft 404, contenido que solo aparece
  con JavaScript, móvil frente a escritorio y accesibilidad (axe-core).
- Enlazado interno: profundidad en clics, huérfanas, enlaces que Google no
  puede seguir, `nofollow` interno, anclas genéricas.
- **Trampas de rastreo**: calendarios infinitos, sesión en la URL, facetas
  combinatorias, búsqueda interna enlazada, `&amp;` copiado a los enlaces.
- Cada incidencia lleva su ficha (qué es, qué hace Google, cómo se arregla) y
  una prioridad: gravedad × importancia de las páginas afectadas.

**PageRank**
- El interno, con el peso de cada enlace medido por cuánto se repite en el
  sitio (un menú no vale lo que un enlace de contenido) y cuánta autoridad se
  pierde en errores y redirecciones.
- **Desde fuera**: con un CSV de backlinks de Ahrefs, Semrush o Majestic, por
  dónde entra la autoridad real y dónde se pierde (backlinks a errores, a
  noindex, a páginas bloqueadas).

**Lo que dice Google**
- **Search Console** por cliente: clics, impresiones y posición por URL,
  oportunidades (posiciones 4-20 con poco enlazado, CTR bajo, canibalización
  por búsqueda) y lo que cuesta tráfico (páginas con clics que no se pueden
  indexar o que el rastreo no encontró). Dice lo que la API de Google no cuenta,
  con la parte de clics anonimizada medida en el sitio.
- Inspección de URLs por muestra diaria: la canonical que elige Google.
- **Logs del servidor**: qué visita Googlebot de verdad (verificado por sus
  rangos de IP), qué páginas importantes no visita y en qué se le va el
  presupuesto de rastreo.
- El tráfico entre dos rastreos, agrupado por lo que cambió en esas páginas.

**Clientes y seguimiento**
- Cliente → perfiles de rastreo → programaciones, con la configuración heredada
  en cada disparo. «Lanzar ahora». Un rastreo por perfil a la vez.
- Cada rastreo se compara con el anterior de su serie: qué incidencias son
  nuevas, cuáles se arreglaron y cuáles siguen, sin afirmar lo que un rastreo
  truncado no puede afirmar.
- Alertas con estado (se abren, se ven, se cierran solas) y reglas por cliente,
  en la interfaz y en el canal de Slack del cliente, más un resumen semanal.
- Evolución de cada perfil y panel de la cartera.

**Entregables**
- Informe para el cliente imprimible a PDF, Excel de varias hojas y CSV desde
  cualquier vista del Explorador.
- Enlace de solo lectura para cada cliente con sus informes.
- Servidor MCP (local y remoto con OAuth) para preguntarle al crawler desde
  Claude Code, Cowork o claude.ai, y lanzar o programar rastreos.

**IA (opcional y nunca sola)**
- Una capa de decisiones tipadas con Jev (TypeSafe, vía OpenRouter) para lo que
  una regla no puede juzgar: soft 404, tipo de página, intención, canibalización
  juzgada, title genérico, marcado frente a lo visible.
- Solo se lanza a mano, con token y presupuesto. Nada llega a un informe sin un
  umbral calibrado contra etiquetas humanas, que se ponen en la pantalla
  «Revisar a Jev» a ciegas.

## Arquitectura

```
Navegador ──▶ API (FastAPI, sirve también la interfaz y /mcp)
                │        │
                │        └──▶ Redis: jobs:pending · jobs:programados · enrichment:pending · logs:pending
                ▼                     ▲
           PostgreSQL ◀────────── Worker (Scrapy en un subproceso por rastreo;
                                          al acabar, el análisis)
```

- **`api/`**: la API y sus routers. **`crawler/`**: el spider, los middlewares
  y el worker. **`analysis/`**: el análisis que corre al acabar cada rastreo.
  **`shared/`**: modelos y las reglas que comparten spider y analizador.
  **`frontend/`**: la interfaz. **`mcp_servidor/`**: el servidor MCP.
  **`scripts/`**: utilidades (migraciones, re-análisis, evaluación de Jev).
- La API y el worker comparten un volumen de archivo: los enlaces de los
  censos superados en Parquet, las capturas y los logs subidos mientras se
  leen.
- Detalle: [`docs/referencia/arquitectura.md`](docs/referencia/arquitectura.md).

## Escala: lo que aguanta y lo que no

- **El análisis está hecho para sitios grandes**:
  - el PageRank agrega las aristas en SQL (con 139 millones de enlaces, unos
    240 MB en memoria);
  - los enlaces de los censos superados salen a Parquet;
  - las URLs de los sitemaps entran en la cola por cupos;
  - el Excel se escribe en streaming.
- **El rastreo, no tanto**:
  - cada rastreo es un solo proceso de Scrapy, sin repartirse entre máquinas;
  - el VPS de hoy tiene 2 CPU, un worker y dos rastreos a la vez;
  - por cortesía con el sitio (concurrencia de 3-4 por dominio, con pausas)
    va a unas 0,7 URLs por segundo, unas 60.000 al día;
  - un sitio de un millón de URLs tardaría semanas, y rastrearlo más deprisa
    o repartido no está probado.

## Puesta en marcha (local)

```bash
cp .env.example .env              # pon un API_TOKEN de 24 caracteres o más
docker compose up -d --build
docker compose exec api python scripts/init_db.py
```

La interfaz queda en <http://127.0.0.1:8000>; se entra con el `API_TOKEN`.
Sin token, la API responde 503: está cerrada, no abierta.

Los secretos (llaves de Search Console, token de Slack, claves de modelos) van
en el entorno o en ficheros de `SECRETOS_DIR`, nunca en la base de datos.

## Producción

Un VPS con Docker. **Mergear a `master` despliega**: la CI
(`.github/workflows/deploy.yml`) pasa los tests, reconstruye las imágenes y
recrea la API y el worker, lo que corta el rastreo que esté en marcha. En el
VPS se usa `docker-compose.yml` con `docker-compose.vps.yml` (que vive solo en
el servidor: Traefik delante de la API y un worker). Operación, backups y
reconstruir sin cortar nada:
[`docs/referencia/operacion.md`](docs/referencia/operacion.md) y
[`docs/referencia/despliegue-y-entorno.md`](docs/referencia/despliegue-y-entorno.md).

## Tests

```bash
pip install -r tests/requirements.txt && pytest
```

Varios tests corren contra Postgres y Redis de verdad, y los del frontend en
Chromium. Cómo destaparlos:
[`docs/referencia/testing.md`](docs/referencia/testing.md).

## Documentación

| Para… | Lee |
|---|---|
| Reglas que no se pueden romper e índice de todo | [`CLAUDE.md`](CLAUDE.md) |
| Por qué cada cosa es como es (más de 200 decisiones, por tema) | [`docs/decisiones/README.md`](docs/decisiones/README.md) |
| API, módulos y checks del analizador | [`docs/referencia/api-y-modulos.md`](docs/referencia/api-y-modulos.md) |
| Base de datos | [`docs/referencia/base-de-datos.md`](docs/referencia/base-de-datos.md) y [`SEO_CRAWLER_DB.md`](SEO_CRAWLER_DB.md) |
| Qué hay hecho y qué viene | [`ROADMAP.md`](ROADMAP.md), y en lenguaje llano [`ROADMAP_SENCILLO.md`](ROADMAP_SENCILLO.md) |
| El plan para ser el mejor crawler del mercado | [`docs/PLAN_MEJOR_CRAWLER.md`](docs/PLAN_MEJOR_CRAWLER.md) |
| La interfaz | [`docs/AUDITORIA_UX.md`](docs/AUDITORIA_UX.md) |

Proyecto interno de [Lin3s](https://lin3s.com).
