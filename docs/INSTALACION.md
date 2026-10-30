# Instalar el crawler en tu ordenador (Docker)

Guía para que cualquiera del equipo tenga el crawler funcionando en local:
la interfaz, la API, el worker que rastrea, PostgreSQL y Redis, todo en
contenedores. No hace falta instalar Python ni bases de datos.

> **Tu instalación es independiente de producción.** Empieza vacía: no ve los
> clientes ni los censos del VPS, y lo que rastrees en local no sube a ningún
> sitio. Para consultar producción no hace falta instalar nada: usa la
> interfaz web o el conector MCP ([`mcp_servidor/README.md`](../mcp_servidor/README.md)).

Comprobado de cero el 2026-10-29 en macOS (Apple Silicon) con Docker Desktop
y Compose v2.32: `docker compose up -d --build` sin más, entrada a la
interfaz y un rastreo de prueba.

---

## 1. Requisitos

| | |
|---|---|
| **Docker** | Docker Desktop (Mac o Windows) o Docker Engine con el plugin Compose v2 (Linux). Comprueba con `docker compose version`: tiene que ser v2.20 o más. |
| **Memoria para Docker** | 6 GB como mínimo y 8 GB mejor. En Docker Desktop: *Settings → Resources → Memory*. El worker se reserva 2 GB y Chromium los usa cuando un rastreo renderiza JavaScript. |
| **Disco** | Unos 10 GB libres: las imágenes (con Chromium) pesan varios GB y la base de datos crece con cada rastreo. |
| **Git** y **acceso al repo** | El repo es privado (`github.com/dvillarrubia/crawler-masivo`). Pide a David que te añada como colaborador. |
| **Python 3** (opcional) | Solo para lanzar rastreos desde la terminal con `scripts/lanzar_job.py`, que no tiene dependencias. Desde la interfaz no hace falta. |
| **Windows** | Usa Docker Desktop con WSL 2 y clona el repo dentro de WSL (no en `C:\`): los comandos de esta guía son de bash. |

## 2. Clonar el repo

```bash
git clone https://github.com/dvillarrubia/crawler-masivo.git
cd crawler-masivo
```

## 3. Crear el `.env`

```bash
cp .env.example .env
```

Abre `.env` y pon una **clave de acceso** en `API_TOKEN` (mínimo 24
caracteres). Genera una con:

```bash
openssl rand -hex 24
```

```dotenv
API_TOKEN=pega-aqui-lo-que-ha-salido
```

Es la contraseña con la que entrarás en la interfaz. Sin ella la API responde
503 a todo: está cerrada, no abierta.

El resto de valores de `.env.example` valen tal cual para local. Si cambias
`POSTGRES_PASSWORD`, cambia también la contraseña dentro de `DATABASE_URL`, y
hazlo **antes** del primer arranque: Postgres solo la lee al crear su volumen.

El `.env` no se sube a git (está en `.gitignore`). No lo compartas por Slack.

## 4. Construir y arrancar

```bash
docker compose up -d --build
```

- La **primera** construcción tarda: compila algunas dependencias de Python y
  descarga Chromium. Cuenta con 10-20 minutos según la máquina y la conexión.
  Las siguientes aprovechan la caché y van en un par de minutos.
- La API crea la base de datos al arrancar (la extensión `vector` de pgvector
  y las tablas). En el log del worker puede salir, solo en los primeros
  segundos, un error de Search Console con `relation "clients" does not exist`:
  el worker ha mirado antes de que la API terminara de crear las tablas, y a
  la siguiente vuelta ya las encuentra. No hay que hacer nada.

## 5. Comprobar que funciona

```bash
docker compose ps
```

Los cuatro servicios (`postgres`, `redis`, `api`, `crawler`) tienen que estar
`Up`, y Postgres y Redis además `(healthy)`. Si `api` sale como `Restarting`,
ve a [Problemas frecuentes](#problemas-frecuentes).

```bash
curl http://127.0.0.1:8000/health        # {"status":"ok"}
```

Abre <http://127.0.0.1:8000> en el navegador: te lleva a `/entrar`, donde se
pega el `API_TOKEN` del `.env`.

## 6. Primer rastreo

**Desde la interfaz:** crea un cliente, un perfil de rastreo con la URL de
inicio y dale a «Lanzar ahora». Empieza con un sitio pequeño o con pocas URLs.

**Desde la terminal**, con la configuración de un cliente en
`projects/<cliente>/config.json` ([`projects/README.md`](../projects/README.md)):

```bash
cp -r projects/_ejemplo projects/prueba   # y cambia _seeds por la URL a rastrear
export API_TOKEN=$(grep '^API_TOKEN=' .env | cut -d= -f2)
python3 scripts/lanzar_job.py prueba --canary   # 300 URLs como mucho; siempre primero
```

Las carpetas de `projects/` de los clientes no están en git (solo
`_ejemplo/`): pídeselas a quien lleve el cliente o cógelas del Drive.

Qué hace el worker mientras rastrea:

```bash
docker compose logs -f crawler
```

Al acabar el rastreo, el mismo worker lanza el análisis; el censo aparece en
la interfaz con sus incidencias.

## 7. El día a día

| Para… | Comando |
|---|---|
| Arrancar (tras reiniciar el ordenador, Docker lo hace solo si está abierto) | `docker compose up -d` |
| Parar sin perder datos | `docker compose stop` |
| Ver logs | `docker compose logs -f api` o `docker compose logs -f crawler` |
| Actualizar a la última versión | Abajo: son cinco pasos y el orden importa |
| Abrir la base de datos | `docker compose exec postgres psql -U crawler -d crawler_db` |
| Re-analizar un censo | `docker compose exec crawler python -m analysis.analyzer <job_id>` |
| Borrarlo **todo** (también los datos) | `docker compose down -v` |

- `docker compose down` (sin `-v`) quita los contenedores y conserva los datos.
  Con `-v` se borran la base de datos, los enlaces archivados y las llaves
  subidas, y no hay vuelta atrás.

### Actualizar a la última versión

```bash
git pull
docker compose build
docker compose run --rm --no-deps --entrypoint python crawler scripts/migrate_add_missing_columns.py
docker compose up -d
docker compose exec api python scripts/init_db.py
```

Es lo mismo que hace el despliegue de producción, en el mismo orden:

- **Las columnas nuevas, antes de arrancar el código nuevo.** La API crea al
  arrancar las tablas que falten, pero no añade columnas a las que ya existen.
  Si el código nuevo espera una columna que tu base de datos no tiene, la API
  da error 500. `migrate_add_missing_columns.py` añade todas las que falten
  (nunca borra ni cambia nada) y `init_db.py` aplica el resto de migraciones.
  Repetir cualquiera de los dos no hace nada.
- **Actualizar mata el rastreo que esté en marcha**: `up -d` recrea el
  contenedor del worker. Espera a que acabe o cancélalo.

## 8. Integraciones (opcionales)

Nada de esto hace falta para rastrear y auditar. Todas las claves van en el
`.env` o se suben desde la ficha del cliente; nunca en la base de datos ni en
el código. Tras tocar el `.env`, `docker compose up -d` para que lo lean los
contenedores.

| Integración | Qué poner | Más detalle |
|---|---|---|
| Search Console | Se sube la llave de la cuenta de servicio desde la ficha del cliente | [`despliegue-y-entorno.md`](referencia/despliegue-y-entorno.md) |
| Slack | Token del bot (`xoxb-…`) en la ficha del cliente, o `SLACK_<NOMBRE>` en `.env` | `.env.example` |
| Capa de IA (Jev) | `DECISIONS_ENABLED=1`, `OPENROUTER_API_KEY` y `ENRICHMENT_TOKEN`. **Cuesta dinero**: déjala apagada salvo que vayas a probarla | `.env.example` y [`decisiones/`](decisiones/README.md) |
| MCP local (Claude Code contra tu instalación) | `CRAWLER_API_URL=http://127.0.0.1:8000` y tu token en `CRAWLER_API_TOKEN` | [`mcp_servidor/README.md`](../mcp_servidor/README.md) |

## Problemas frecuentes

**`api` en `Restarting` y en los logs `type "vector" does not exist`.** Tu
copia del repo es anterior al 2026-10-29, cuando la API aún no creaba la
extensión sola: haz `git pull` y `docker compose up -d --build`, o pasa a mano
`docker compose run --rm api python scripts/init_db.py` y
`docker compose restart api crawler`.

**`port is already allocated` al arrancar.** Algo de tu ordenador ya usa el
8000, el 5432 (otro Postgres) o el 6379 (otro Redis). Para otro proyecto o
cambia los puertos sin tocar `docker-compose.yml`: crea un
`docker-compose.override.yml` (Compose lo lee solo y git lo ignora):

```yaml
services:
  postgres:
    ports: !override ["127.0.0.1:15432:5432"]
  redis:
    ports: !override ["127.0.0.1:16379:6379"]
  api:
    ports: !override ["127.0.0.1:18000:8000"]
```

y entra por <http://127.0.0.1:18000>. Con `lanzar_job.py`, añade
`--api http://localhost:18000`.

**La interfaz dice que la clave no es válida, o la API da 503.** El
`API_TOKEN` del `.env` está vacío o tiene menos de 24 caracteres, o lo
cambiaste sin recrear la API (`docker compose up -d api`).

**`password authentication failed for user "crawler"`.** Cambiaste
`POSTGRES_PASSWORD` después del primer arranque. O la devuelves a la de antes,
o borras los datos y empiezas de cero con `docker compose down -v`.

**El worker se muere (`Killed`, código 137) en rastreos con JavaScript.** A
Docker le falta memoria: súbela en Docker Desktop, o baja
`JS_CONCURRENT_REQUESTS` y `PLAYWRIGHT_MAX_PAGES` en `.env`.

**Un sitio devuelve 403 o captcha.** Es el WAF del sitio, no tu instalación.
Baja el ritmo en el `config.json` del cliente (`concurrent_requests` 4,
`concurrent_requests_per_domain` 2, `crawl_behavior.request_delay` 0.5) y mira
si hay notas del cliente en su `projects/<cliente>/README.md`.

## Si vas a tocar el código

Lee antes [`CLAUDE.md`](../CLAUDE.md): las reglas que no se pueden romper y
dónde está cada cosa. **Mergear a `master` despliega a producción** y corta el
rastreo que esté en marcha allí. Los tests:
[`referencia/testing.md`](referencia/testing.md).
