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

## Componentes

| Component | Path | Technology | Description |
|-----------|------|------------|-------------|
| **API** | `api/` | FastAPI | Job CRUD, results, CSV export, real-time progress via Redis |
| **Crawler** | `crawler/` | Scrapy + Playwright | SEO spider + Redis queue worker. Each crawl runs as **subprocess** |
| **Analysis** | `analysis/` | SQLAlchemy 2.0 | Post-crawl SEO analysis. Triggered automatically by worker |
| **Shared** | `shared/` | SQLAlchemy | Models, DB config, constants. Shared across all components |
| **Frontend** | `frontend/` | Alpine.js | Lightweight static SPA (vanilla JS), served by FastAPI. Ver abajo |
| **MCP** | `mcp_servidor/` | MCP | Servidor de solo lectura sobre la API (decision 148) |
| **Scripts** | `scripts/` | Python | DB init (`init_db.py`), verificacion (`check_content_quality.py`, `check_js_templates.py`) y reparaciones sobre censos ya hechos (`fix_h1_en_contenido.py`, `near_duplicates.py`) |

## Frontend

- Rutas en el hash (`#/rastreos/<id>/problemas?gravedad=error`,
  `#/clientes/<id>`…; `_ruta()` / `_irARuta()` en `app.js`).
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
- **crawler** — Scrapy worker, 1 replica by default (local), resource-limited (2GB RAM, 2 CPUs)
