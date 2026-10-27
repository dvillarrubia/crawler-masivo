# Arquitectura y servicios

> Movido desde `CLAUDE.md` (2026-10-19). El mapa fichero a fichero esta en
> [`api-y-modulos.md`](api-y-modulos.md).

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

## Colas de Redis (`shared/cola.py`)

| Cola | Qué lleva | Quién la llena | Decisión |
|---|---|---|---|
| `jobs:pending` | ids de rastreos lanzados a mano, reanudados o recuperados | API, worker | 31 |
| `jobs:programados` | ids de rastreos que dispara el planificador; solo se atienden con `jobs:pending` vacía | worker (`_lanzar_programados`) | 209 |
| `enrichment:pending` | ejecuciones del análisis con IA | API (con token) | 85 |
| `logs:pending` | logs del servidor subidos, para leer en un hilo del worker | API | 213 |

Las pruebas en vivo de la extracción a medida van por su propia cola
(`shared/prueba_en_vivo.py`, decisión 166).

## Volúmenes

- **`archivo`** (`ARCHIVO_DIR=/data/archivo`), compartido por la API y el
  worker: los enlaces de los censos superados en Parquet (95, 150), las
  capturas de pantalla (173) y los logs subidos mientras se leen (213; se
  borran al acabar).
- **`secretos`** (`SECRETOS_DIR=/secretos`): llaves de Search Console, token
  de Slack y claves por cliente (141, 149). Nunca en la base de datos (83).

## Componentes

| Component | Path | Technology | Description |
|-----------|------|------------|-------------|
| **API** | `api/` | FastAPI | Job CRUD, results, CSV export, real-time progress via Redis |
| **Crawler** | `crawler/` | Scrapy + Playwright | SEO spider + Redis queue worker. Each crawl runs as **subprocess** |
| **Analysis** | `analysis/` | SQLAlchemy 2.0 | Post-crawl SEO analysis. Triggered automatically by worker |
| **Shared** | `shared/` | SQLAlchemy | Models, DB config, constants. Shared across all components |
| **Frontend** | `frontend/` | Alpine.js | Lightweight static SPA (vanilla JS), served by FastAPI. Ver abajo |
| **MCP** | `mcp_servidor/` | MCP | Servidor MCP sobre la API: por stdio (148) y en remoto en `/mcp` de la API, con OAuth y escritura en dos pasos (151) |
| **Scripts** | `scripts/` | Python | DB init (`init_db.py`), verificacion (`check_content_quality.py`, `check_js_templates.py`) y reparaciones sobre censos ya hechos (`fix_h1_en_contenido.py`, `near_duplicates.py`) |

## Frontend

- Rutas en el hash (`#/rastreos/<id>/problemas?gravedad=error`,
  `#/clientes/<id>`…; `_ruta()` / `_irARuta()` en `app.js`). La ficha del
  cliente: `#/clientes/<id>` es Estado, y `/historial`, `/alertas` y
  `/configuracion` sus otras pestanas (`rastreos` abre Historial).
- `incidencias.js` es el catalogo de cada tipo de incidencia (que es, que decide
  Google, como se arregla): un tipo nuevo del analizador lleva su entrada ahi.
- Plan y estado de la interfaz en `docs/AUDITORIA_UX.md`.
- Tema claro (`style.css`) por defecto; `theme-terminal.css` (consola de fosforo
  verde) opcional, con el boton de la barra superior, guardado en
  `localStorage['seo-crawler-theme']`.

## Servicios de Docker (`docker-compose.yml`)

- **postgres** — PostgreSQL 16 Alpine, port 5432, healthcheck via `pg_isready`
- **redis** — Redis 7 Alpine, port 6379, healthcheck via `redis-cli ping`
- **api** — FastAPI app, port 8000, depends on postgres + redis
- **crawler** — Scrapy worker, 1 replica by default (local), resource-limited (2GB RAM, 2 CPUs). En el VPS también es uno solo (`crawler-masivo-worker`, por `docker-compose.vps.yml`; ver `despliegue-y-entorno.md`)
