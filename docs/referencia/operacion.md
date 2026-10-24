# Operar en produccion

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## Al operar: reconstruir el contenedor mata el rastreo en marcha

`docker compose up -d --build crawler` recrea el contenedor, y con el se va el
subproceso de Scrapy del rastreo que estuviera corriendo **y el directorio de
logs** (`/tmp/scrapy-logs`, que vive dentro). El job se queda en `running` sin
nadie detras hasta que el vigilante lo recupera por latido viejo
(`STALE_JOB_MINUTES`, 30 por defecto).

Y ojo, que eso **no era verdad hasta el 2026-10-08**: `_recover_stale_jobs`
corria UNA sola vez, al arrancar el worker, asi que no cubria el caso que mas
lo necesita. Si el contenedor se reinicia DENTRO de los primeros 30 minutos de
un rastreo —justo cuando lo pilla un despliegue—, el job recien empezado no
llega al umbral, no es candidato, y como nadie vuelve a mirar se queda
huerfano **para siempre**. Medido en carne propia: un rastreo lanzado a las
22:22:01 y un despliegue que recreo el contenedor a las 22:22:39 dejaron el
job colgado **7 h 40 min con 39 URLs**, sin proceso de Scrapy y sin que el
vigilante lo tocara. Ahora pasa cada `RECOVERY_INTERVAL_SECONDS` (300) dentro
del bucle.

**Y antes de lanzar un rastreo, mirar que no haya un despliegue en vuelo**, no
solo antes de bajar un censo: `gh run list --workflow=deploy.yml --limit 1`.
Mergear una PR y lanzar un rastreo en el mismo minuto es matarlo.

Pasa con cualquier cambio de codigo que obligue a reconstruir —una migracion de
esquema, por ejemplo— y es facil confundirlo con un bloqueo del sitio: un
canario que se para en seco a las 199 URLs parece un WAF y era esto. Antes de
reconstruir, mirar si hay algo rastreando:

```bash
for s in pending queued running analyzing; do
  curl -s "$API/api/jobs?status=$s" | python -c "import json,sys;print('$s', [j['name'] for j in json.load(sys.stdin)['items']])"
done
```

**`analyzing` cuenta como en marcha.** El 2026-10-13 un merge se hizo con
«0 running» y el rastreo semanal de Lopesan en pleno analisis: el analisis
tiene `jobs` con `FOR UPDATE` toda la pasada, el `ALTER TABLE jobs` de la
migracion se quedo esperando su candado y, mientras esperaba, **la API dejo de
responder a `/api/jobs`** (Postgres pone en cola detras del `ALTER` todas las
lecturas de la tabla). Desde entonces las migraciones del despliegue llevan
`lock_timeout` de 15 s (`limitar_espera_de_bloqueos`) y fallan en vez de
bloquear, y el despliegue solo reintenta cuando falla la conexion SSH: antes
repetia el script entero, y un despliegue cancelado siguio vivo dos intentos
mas camino del `up -d`.

Y lo mismo vale para cualquier cosa larga contra la API: **mergear una PR
dispara un despliegue, y el despliegue recrea el contenedor de la API**, asi
que se lleva por delante la descarga que estuviera en curso. Medido el
2026-10-07: el backup del censo de CST murio con `curl 92` (error de framing
HTTP/2) **once segundos antes** de que terminara el despliegue de un merge
hecho mientras tanto. No fue memoria ni un fallo del endpoint; fue el
despliegue. Antes de bajar un censo grande, mirar que no haya un merge en
vuelo (`gh run list --workflow=deploy.yml --limit 1`).

Y **nunca** copiar ficheros al contenedor que esta rastreando (`docker cp`): si
el spider y los extractores quedan descasados, la siguiente reanudacion del job
muere con un ImportError. Para probar codigo nuevo contra el sitio de pruebas o
contra un script, levantar un contenedor efimero con el repo montado, que no
toca al que trabaja:

```bash
docker run --rm --network host -v "$PWD":/repo -w /repo \
  -e PYTHONPATH=/repo:/repo/crawler:/repo/tests \
  crawler-masivo-crawler bash -lc "pip install -q pytest; python -m pytest /repo/tests -q"
```

Si el efimero es un Postgres o un Redis levantado con `-d --rm` para quitarlo
luego a mano, se quita con `docker rm -fv`: sin `-v` su volumen anonimo se queda
(el hook de sesiones lo bloquea). Para revisar lo que ya haya: `docker volume ls
-f dangling=true`, y `docker volume prune -f` borra solo los anonimos sin
contenedor, nunca `crawler-masivo_pgdata` ni otro con nombre.

## Configuración por cliente (`projects/`)

Todo lo específico de un cliente (filtros, selectores de plantilla, `templates`
para el muestreo por plantilla, idioma, ritmo, semillas) vive en
`projects/<cliente>/config.json`, que es el campo `config` de `POST /api/jobs`.
Se lanza con `python scripts/lanzar_job.py <cliente> [--canary] [--set k=v]`.
Solo `projects/_ejemplo/` se versiona; ver `projects/README.md`. **Nunca**
reglas de un cliente en código.

## Re-extraer las reglas a medida de un censo (decision 166)

Sin re-rastrear, sobre `page_content.raw_html` (solo si el censo se lanzó con
`extraction.store_raw_html`), dentro del contenedor del crawler:

```bash
python scripts/reextraer_a_medida.py <job_id> --reglas r.json --dry-run   # cuenta
python scripts/reextraer_a_medida.py <job_id> --reglas r.json            # escribe
```

`r.json` lleva `custom_extraction` y/o `custom_search`; se fusiona con las del
censo (por nombre; `null` quita) y se guarda en `jobs.config`. Toma el candado
del job y se niega con el job en marcha. Las páginas sin `raw_html` pierden las
filas de las reglas que cambian (quedan «no evaluadas»).

## Automatic JS-render check

Every crawl that finishes **without** `render_js` triggers
`scripts/check_js_templates.py` automatically (worker → `_comprobar_render_js`).
It groups the crawled URLs by template shape, samples them, and compares raw HTML
against Chromium-rendered HTML.

It answers the question that otherwise stays invisible: **if a template builds its
links with JavaScript, the link graph is incomplete and the PageRank computed from
it is wrong, with nothing to flag it.** A WARNING is logged when that happens.

Result is stored in `jobs.js_check` and exposed on the job endpoint:

| Field | Meaning |
|---|---|
| `grafo_fiable` | false → some template hides links; re-crawl with `render_js=true` before trusting link metrics |
| `enlaces_ocultos` | whether any JS-only internal links were found |
| `plantillas[]` | per template: samples, JS-only links, words raw vs rendered |

Costs ~1 min on top of a multi-hour crawl (5 templates × 1 sample by default).
Env: `JS_CHECK_ENABLED=0` disables it, `JS_CHECK_TEMPLATES`, `JS_CHECK_SAMPLES`.
Classification rules live at the top of the script and are per-project — CMS URLs
(Liferay portlets) are isolated first so they don't pollute the sample.

Run it by hand against any finished job:

```bash
docker compose exec -T crawler python /app/scripts/check_js_templates.py <job_id> --muestras 2
```
