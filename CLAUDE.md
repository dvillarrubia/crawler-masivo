# SEO Crawler - CLAUDE.md

Crawler SEO distribuido (tipo Screaming Frog): **FastAPI + Scrapy + PostgreSQL +
Redis**, con Playwright para el render. Auditorias de sitios grandes con progreso
en vivo, analisis SEO tras el rastreo, comparacion entre censos y export CSV.

Este fichero es el **indice**: lo minimo para no romper nada y donde esta el
resto. El detalle vive en `docs/` y se lee cuando se toca su area.

## Varias terminales a la vez: protocolo obligatorio

Suele haber varias sesiones de Claude trabajando sobre este repo **y sobre la
misma produccion**. Lo coordinan los hooks de `.claude/settings.json`
(`.claude/hooks/sesiones.py`):

- **Al arrancar** cada sesion recibe el tablero: quien mas esta abierto, en que
  worktree, en que rama y con que tarea, y si alguien tiene el candado de
  despliegue. Declara tu tarea en cuanto la sepas
  (`python3 .claude/hooks/sesiones.py tarea <tu-id> "<tarea>"`).
- **Un worktree por sesion.** Nunca trabajes en el directorio principal:
  `git -C <principal> worktree add -b <rama> ../crawler-masivo-<tema> origin/master`.
  El hook **bloquea** `git checkout`/`git switch` en el principal (le cambia la
  rama a las demas). Push como `git push origin <rama>:<rama>`, nunca `HEAD`.
- **Mergear despliega** y recrea api y worker. El hook de `gh pr merge` **bloquea**
  si otra sesion tiene el candado de despliegue, si hay un `deploy.yml` en vuelo
  o si produccion esta ocupada (jobs `pending/queued/running/analyzing`,
  candados advisory, analisis por `docker exec`). Si pasa, toma el candado 20
  min; sueltalo al acabar el despliegue
  (`python3 .claude/hooks/sesiones.py candado soltar deploy <tu-id>`).
- **Lanzar un rastreo** (`lanzar_job.py` o `POST /api/jobs`) se bloquea mientras
  otra sesion despliega.
- **Antes de mergear o lanzar algo largo**, avisa a las otras sesiones activas
  (`ListAgents` / `SendMessage`). No toques worktrees que no son tuyos.
- Saltarse un guardia (`SESIONES_FORZAR=1 <comando>`) solo si el usuario lo pide
  expresamente.

Comprobar a mano: `python3 .claude/hooks/sesiones.py tablero` y `... prod`.

## Arquitectura

```
┌─────────┐    ┌───────┐    ┌──────────┐    ┌──────────┐
│ Frontend │───▶│  API  │───▶│  Redis   │◀───│  Worker  │
│ (static) │    │FastAPI│    │ (queue)  │    │ (Scrapy) │
└─────────┘    └───┬───┘    └──────────┘    └────┬─────┘
                   │                              │
                   └──────────┐  ┌────────────────┘
                              ▼  ▼
                         ┌──────────┐
                         │PostgreSQL│
                         └──────────┘
```

### Components

| Component | Path | Technology | Description |
|-----------|------|------------|-------------|
| **API** | `api/` | FastAPI | Job CRUD, results, CSV export, real-time progress via Redis |
| **Crawler** | `crawler/` | Scrapy + Playwright | SEO spider + Redis queue worker. Each crawl runs as **subprocess** |
| **Analysis** | `analysis/` | SQLAlchemy 2.0 | Post-crawl SEO analysis (17 check types). Triggered automatically by worker |
| **Shared** | `shared/` | SQLAlchemy | Models, DB config, constants. Shared across all components |
| **Frontend** | `frontend/` | Alpine.js | Lightweight static SPA (vanilla JS). Served by FastAPI. Rutas en el hash (`#/rastreos/<id>/problemas?gravedad=error`, `#/clientes/<id>`…; `_ruta()` / `_irARuta()` en `app.js`). `incidencias.js` es el catálogo de cada tipo de incidencia (qué es, qué decide Google, cómo se arregla): un tipo nuevo del analizador lleva su entrada ahí. Plan y estado de la interfaz en `docs/AUDITORIA_UX.md`. Light theme (`style.css`) is the default; `theme-terminal.css` (green-phosphor console) is opt-in; toggle in the top bar, stored in `localStorage['seo-crawler-theme']` |
| **Scripts** | `scripts/` | Python | DB init (`init_db.py`), verificación (`check_content_quality.py`, `check_js_templates.py`) y reparaciones sobre censos ya hechos (`fix_h1_en_contenido.py`, `near_duplicates.py`) |

### Docker Services (docker-compose.yml)
- **postgres** — PostgreSQL 16 Alpine, port 5432, healthcheck via `pg_isready`
- **redis** — Redis 7 Alpine, port 6379, healthcheck via `redis-cli ping`
- **api** — FastAPI app, port 8000, depends on postgres + redis
- **crawler** — Scrapy worker, 1 replica by default (local), resource-limited (2GB RAM, 2 CPUs)

## Comandos

```bash
docker compose up -d                                                      # local
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d     # VPS
docker compose up -d --build                                              # tras cambiar codigo
docker compose exec api python scripts/init_db.py                         # tablas
docker compose logs -f crawler
docker exec -it crawlermasivo-postgres-1 psql -U crawler -d crawler_db
python scripts/lanzar_job.py <cliente> [--canary] [--set k=v]            # rastreo de un cliente
python -m analysis.analyzer <job_id>                                      # re-analizar un censo
```

Tests: `pip install -r tests/requirements.txt && pytest` (con Postgres y Redis
de verdad: ver [`docs/referencia/testing.md`](docs/referencia/testing.md)).

## Lo que no se puede romper

Una linea por regla; el porque y la medida, en la decision citada.

- **Scrapy corre como subproceso** por job, con los settings por `-s`; nunca en
  el proceso del worker (el reactor de Twisted no reinicia). Decisiones 1-2.
- **Reconstruir el contenedor mata el rastreo en marcha**, y mergear despliega.
  Nunca `docker cp` al contenedor que rastrea: prueba con un contenedor efimero.
  [`operacion.md`](docs/referencia/operacion.md).
- **Filtra el job en `links`, nunca uniendo con `urls`** (181 M filas). Decision 37;
  los agregados de enlaces leen `links` una vez (41).
- **Un conteo de enlaces por aparicion es `SUM(links.repeticiones)`, no `COUNT(*)`**:
  las apariciones identicas de una pagina van en una fila. Decision 94.
- **Un censo con `jobs.enlaces_archivados` no tiene filas en `links`**: se lee del
  Parquet y no se re-analiza sin `archivar_enlaces.py --restaurar`. Decision 95.
- **Lo que analiza o archiva un censo entero toma el candado del job**
  (`shared/candados.py`): el re-analisis de la referencia (93) cae justo sobre
  censos superados, que es lo que se archiva (95).
- **Un censo se borra con `borrar_job`** (ficha, cliente y borrado en masa): es la que
  quita tambien el Parquet de sus enlaces archivados. Decision 96.
- **Reglas que comparten spider y analizador viven en `shared/`** (robots,
  indexabilidad, dominios, plantillas): dos copias acaban discrepando. 23, 39, 64.
- **Una clave nueva de config se declara en `JobConfig`**: Pydantic v2 la tira en
  silencio. Decision 33.
- **Un valor extraido a medida sin fila es «no evaluado», no «vacio»**: «no
  contiene» es `estado='sin_coincidencia'` y nunca incluye bloqueadas ni errores. 104.
- **Nada de un cliente en codigo**: va en `projects/<cliente>/config.json`.
- **Un aviso por hallazgo**, y lo que es de plantilla o de servidor se agrupa
  (una fila con `paginas_afectadas`, no una por pagina). 36, 53, 64, 73.
- **Un job solo es `completed` si su dato esta completo**; cero URLs no es un
  sitio limpio. 28, 32.
- **Lo que no se puede afirmar no se afirma**: ausencias en censos truncados o
  de otro alcance, incidencias entre censos analizados con codigo distinto. 67, 70, 89, 91.
- **Un `noindex` o una pagina que no da 200 no reparten enlazado.** 38, 57.
- **Best-effort significa el `try` cubriendo la funcion ENTERA**, y un test que lee
  el fuente no sustituye a ejecutar. 40, 74.
- **Nada llama a un modelo solo**: la capa de decisiones solo la dispara el
  analisis enriquecido, con token. 84, 85.
- **Antes de atribuir una diferencia al crawler, repitela con el codigo y la
  config viejos**: los sitios varian solos. 13, 66, 72.

## Documentacion

| Si vas a... | Lee |
|---|---|
| Cambiar cualquier comportamiento | [`docs/decisiones/README.md`](docs/decisiones/README.md): las decisiones por tema y por numero. Lee las del area antes de tocarla |
| Tocar API, extractores, checks del analizador, middlewares | [`docs/referencia/api-y-modulos.md`](docs/referencia/api-y-modulos.md) |
| Consultar o migrar la BD | [`docs/referencia/base-de-datos.md`](docs/referencia/base-de-datos.md) y `SEO_CRAWLER_DB.md` (esquema completo) |
| Desplegar, variables de entorno, CI, umbrales | [`docs/referencia/despliegue-y-entorno.md`](docs/referencia/despliegue-y-entorno.md) |
| Operar produccion: rastreos, reconstruir, backups, `projects/`, check de render JS | [`docs/referencia/operacion.md`](docs/referencia/operacion.md) |
| Escribir o correr tests | [`docs/referencia/testing.md`](docs/referencia/testing.md) |
| Elegir trabajo nuevo | [`docs/referencia/pendiente.md`](docs/referencia/pendiente.md), `ROADMAP.md` y `docs/AUDITORIA_Y_VERIFICACION.md` (TODO canonico) |
| Tocar la interfaz | `docs/AUDITORIA_UX.md` |

## Como se documenta (para que esto no vuelva a crecer)

- **Una decision nueva va en `docs/decisiones/<tema>.md`**, con un numero
  reservado con `python3 .claude/hooks/sesiones.py numero-decision <tu-id>`
  (mira master, todos los worktrees y las reservas de las demas sesiones: dos
  ramas cogiendo «el siguiente hueco» a la vez chocan) y su fila en el indice (instrucciones al final de
  `docs/decisiones/README.md`). **No** en este fichero.
- Aqui solo entra una linea en «Lo que no se puede romper» si cualquier sesion
  tiene que saberla sin abrir nada.
- Un endpoint, tabla, variable o test nuevo se anota en su fichero de
  `docs/referencia/`. Una funcionalidad cerrada o abierta, en `ROADMAP.md`.

## Convenciones de codigo

- Todos los ficheros con `from __future__ import annotations`.
- Pydantic v2 con `model_config = ConfigDict(from_attributes=True)`.
- SQLAlchemy 2.0 estilo `select()` en el analizador; estilo ORM en la API.
- `extractors.py` son funciones puras, sin imports de Scrapy.
- Los Items de Scrapy mapean 1:1 a los modelos de SQLAlchemy.
- Sin autenticacion y con CORS abierto: lo que se guarda lo lee cualquiera
  (por eso los secretos van en el entorno, decision 83).
