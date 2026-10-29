# SEO Crawler - CLAUDE.md

Crawler SEO distribuido (tipo Screaming Frog): **FastAPI + Scrapy + PostgreSQL +
Redis**, con Playwright para el render. Auditorias de sitios grandes con progreso
en vivo, analisis SEO tras el rastreo, comparacion entre censos y export CSV.

Este fichero es el **indice**: lo minimo para no romper nada y donde esta el
resto. El detalle vive en `docs/` y se lee cuando se toca su area.

## Varias terminales a la vez: protocolo obligatorio

Suele haber varias sesiones de Claude sobre este repo **y sobre la misma
produccion**. Lo coordinan los hooks de `.claude/hooks/sesiones.py`:

- **Al arrancar** recibes el tablero (sesiones, worktrees, ramas, tareas, candado
  de despliegue). Declara tu tarea: `python3 .claude/hooks/sesiones.py tarea <tu-id> "<tarea>"`.
- **Un worktree por sesion**, nunca el directorio principal:
  `git -C <principal> worktree add -b <rama> ../crawler-masivo-<tema> origin/master`.
  El hook bloquea `checkout`/`switch` en el principal. Push `git push origin <rama>:<rama>`, nunca `HEAD`.
- **Mergear despliega** y recrea api y worker. `gh pr merge` se bloquea si otra
  sesion tiene el candado, hay un `deploy.yml` en vuelo o produccion esta ocupada.
  Si pasa, toma el candado 20 min y sueltalo al acabar
  (`python3 .claude/hooks/sesiones.py candado soltar deploy <tu-id>`).
- **Lanzar un rastreo** se bloquea mientras otra sesion despliega.
- **Antes de mergear o lanzar algo largo**, avisa a las otras sesiones
  (`ListAgents` / `SendMessage`). No toques worktrees que no son tuyos.
- `SESIONES_FORZAR=1 <comando>` solo si el usuario lo pide expresamente.

A mano: `python3 .claude/hooks/sesiones.py tablero` y `... prod`.

## Arquitectura

Frontend estatico (Alpine.js) -> API (FastAPI) -> cola en Redis -> worker
(Scrapy, un subproceso por job) -> PostgreSQL; el worker lanza el analisis al
acabar. Codigo en `api/`, `crawler/`, `analysis/`, `shared/` (lo comun a los
tres), `frontend/`, `mcp_servidor/` y `scripts/`. Detalle y servicios de Docker:
[`docs/referencia/arquitectura.md`](docs/referencia/arquitectura.md).

## Comandos

```bash
docker compose up -d                                                      # local
docker compose -f docker-compose.yml -f docker-compose.vps.yml up -d      # VPS (vps.yml solo esta en el servidor; un worker)
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

Solo las que se rompen sin estar tocando el area. Las de rastreo, enlaces,
analisis y borrado estan en [`docs/referencia/reglas.md`](docs/referencia/reglas.md):
**lee su seccion antes de tocar esa parte.**

- **Reconstruir el contenedor mata el rastreo en marcha**, y mergear despliega.
  Nunca `docker cp` al contenedor que rastrea: prueba con un contenedor efimero.
  [`operacion.md`](docs/referencia/operacion.md).
- **La tabla `links`** (181 M filas): filtra el job en `links`, nunca uniendo con
  `urls` (37, 41); un conteo por aparicion es `SUM(links.repeticiones)`, no
  `COUNT(*)` (94); un censo con `jobs.enlaces_archivados` no tiene filas ahi, se
  lee del Parquet y `run_analysis` lo restaura, analiza y re-archiva (95, 150).
- **Reglas que comparten spider y analizador viven en `shared/`** (robots,
  indexabilidad, dominios, plantillas): dos copias acaban discrepando. 23, 39, 64.
- **Una clave nueva de config se declara en `JobConfig`**: Pydantic v2 la tira en
  silencio. Decision 33.
- **Nada de un cliente en codigo**: va en `projects/<cliente>/config.json`.
- **Un aviso por hallazgo**, y lo que es de plantilla o de servidor se agrupa
  (una fila con `paginas_afectadas`, no una por pagina). 36, 53, 64, 73.
- **Lo que no se puede afirmar no se afirma**: ausencias en censos truncados o
  de otro alcance, incidencias entre censos analizados con codigo distinto. 67, 70, 89, 91.
  Y un veredicto no concluyente nunca es `pasa` (128).
- **Best-effort significa el `try` cubriendo la funcion ENTERA**, y un test que lee
  el fuente no sustituye a ejecutar. 40, 74.
- **Nada llama a un modelo solo**: la capa de decisiones solo la dispara el
  analisis enriquecido, con token. 84, 85.
- **Antes de atribuir una diferencia al crawler, repitela con el codigo y la
  config viejos**: los sitios varian solos. 13, 66, 72.

## Documentacion

| Si vas a... | Lee |
|---|---|
| Instalarlo de cero en un ordenador (equipo) | [`docs/INSTALACION.md`](docs/INSTALACION.md) |
| Cambiar cualquier comportamiento | [`docs/decisiones/README.md`](docs/decisiones/README.md) (decisiones por tema y numero) y la seccion de [`reglas.md`](docs/referencia/reglas.md) del area |
| Entender la arquitectura, el frontend o los servicios | [`docs/referencia/arquitectura.md`](docs/referencia/arquitectura.md) |
| Tocar API, extractores, checks del analizador, middlewares | [`docs/referencia/api-y-modulos.md`](docs/referencia/api-y-modulos.md) |
| Consultar o migrar la BD | [`docs/referencia/base-de-datos.md`](docs/referencia/base-de-datos.md) y `SEO_CRAWLER_DB.md` (esquema completo) |
| Desplegar, variables de entorno, CI, umbrales | [`docs/referencia/despliegue-y-entorno.md`](docs/referencia/despliegue-y-entorno.md) |
| Operar produccion: rastreos, reconstruir, backups, `projects/`, check de render JS | [`docs/referencia/operacion.md`](docs/referencia/operacion.md) |
| Escribir o correr tests | [`docs/referencia/testing.md`](docs/referencia/testing.md) |
| Elegir trabajo nuevo | [`docs/PLAN_MEJOR_CRAWLER.md`](docs/PLAN_MEJOR_CRAWLER.md) (orden), `ROADMAP.md` y [`docs/referencia/pendiente.md`](docs/referencia/pendiente.md) |
| Tocar la interfaz | `docs/AUDITORIA_UX.md` |

## Como se documenta (para que esto no vuelva a crecer)

- **Una decision nueva va en `docs/decisiones/<tema>.md`**, con numero reservado
  por `python3 .claude/hooks/sesiones.py numero-decision <tu-id>` (dos ramas
  cogiendo «el siguiente hueco» chocan) y su fila en el indice. **No** aqui.
- **Una regla nueva va en `docs/referencia/reglas.md`**, en su area. Aqui solo
  si se rompe sin tocar esa area; y entonces sale otra o se funde con una
  existente: este fichero no pasa de ~120 lineas.
- Un endpoint, tabla, variable o test nuevo, en su fichero de `docs/referencia/`.
  Una funcionalidad cerrada o abierta, en `ROADMAP.md`.

## Convenciones de codigo

- Todos los ficheros con `from __future__ import annotations`.
- Pydantic v2 con `model_config = ConfigDict(from_attributes=True)`.
- SQLAlchemy 2.0 estilo `select()` en el analizador; estilo ORM en la API.
- `extractors.py` son funciones puras, sin imports de Scrapy.
- Los Items de Scrapy mapean 1:1 a los modelos de SQLAlchemy.
- La API pide `API_TOKEN` (Bearer para scripts, cookie de `/entrar` para el
  navegador; decision 134), CORS cerrado, token unico sin roles; los secretos
  van en el entorno (83). Los scripts que llaman a la API leen `API_TOKEN` del entorno.
