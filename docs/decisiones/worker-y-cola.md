# Decisiones: worker, cola, subprocesos y programaciones

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d1"></a>
1. **Subprocess per crawl** — Worker runs `python -m scrapy crawl seo` as subprocess. Do NOT try to run Scrapy in-process; Twisted reactor cannot restart.

<a id="d2"></a>
2. **Settings via CLI flags** — Per-job Scrapy settings passed via `-s` flags. `custom_settings` on spider class is NOT used (too late for Scrapy to read).

<a id="d3"></a>
3. **Cancel via Redis** — `job:{id}:cancel` key checked every response. Progress via `job:{id}:crawled_count`.

<a id="d4"></a>
4. **Batched pipeline** — Parent items (page, html_meta) upserted individually. Child items (links, headings) buffered and bulk-inserted every 200 items. DELETE-before-INSERT prevents duplicates on re-crawl.

<a id="d15"></a>
15. **Un estancamiento reencola el job, no lo cierra** — el vigilante del worker
   mata el Scrapy que lleva `stall_timeout_minutes` sin latido y lo vuelve a
   encolar hasta `STALL_AUTO_RESUME` veces (def. 3); el spider retoma desde la
   frontera guardada en BD. Solo al agotar los intentos se cierra como `stalled`.

<a id="d16"></a>
16. **El log de Scrapy va a fichero, en vivo** — `-s LOG_FILE=$SCRAPY_LOG_DIR/<job>.log`
   (def. `/tmp/scrapy-logs`, dentro del contenedor, modo append). Antes solo
   existía en memoria hasta que el proceso acababa. Al matar un rastreo se
   registra un resumen de sus líneas de error en WARNING.

<a id="d21"></a>
21. **El contador del job contaba solo el ultimo tramo** — cada reanudacion
   abre un proceso de Scrapy nuevo, con su tuberia y su cuenta desde cero, y
   al cerrar la escribia en `jobs.total_urls_crawled`: un rastreo con 34.704
   filas se reportaba como 802 y `/stats` devolvia las dos cifras a la vez. La
   tuberia arranca ahora leyendo lo que ya hay guardado del job y solo suma
   URLs nuevas.

<a id="d28"></a>
28. **Un job solo es `completed` si su dato esta completo** — antes el worker
   daba por bueno cualquier rastreo con codigo de salida 0 y un analisis que
   reventaba se tragaba en el log: un censo de 289 URLs rastreadas y 0
   guardadas (faltaba una columna) y todos los analisis fallando en
   `analyze_near_duplicates` terminaron `completed`, sin issues ni PageRank,
   que se lee como "sitio limpio". Ahora: si se rastrea y no se guarda nada, el
   job es `failed` con `finish_reason=persistence_failed`; si falla el
   analisis, `failed` conservando el `finish_reason` del rastreo (lo rastreado
   vale; se relanza con `python -m analysis.analyzer <job_id>`). Los ERROR de
   `seo_crawler.*` suben al log del worker agrupados, con la ultima excepcion
   del traceback (`resumir_stderr`); antes solo subian los WARNING.

<a id="d31"></a>
31. **La cola de rastreos es una cola, no una pila** — `rpush` + `brpop`
   trabajan sobre el MISMO extremo de la lista, asi que el ultimo job creado
   adelantaba a todos y lo recuperado tras un reinicio se ponia delante de lo
   que llevaba horas esperando. No se habia notado porque en el uso normal hay
   un job a la vez. El nombre de la cola estaba escrito a mano en cinco
   sitios; ahora el orden y el nombre viven en `shared/cola.py` (`encolar` /
   `siguiente`, FIFO con `rpush` + `blpop`) y el FIFO vale para TODO, tambien
   para reanudaciones y recuperaciones: adelantarlas significaria que un job
   que se atasca y se reencola hasta `STALL_AUTO_RESUME` veces pisa
   indefinidamente a los que esperan.

<a id="d32"></a>
32. **Un rastreo con cero URLs no es un sitio limpio** — con `Disallow: /` y
   `robots_mode=respect`, Scrapy descarta la semilla con `IgnoreRequest`,
   `handle_error` no registraba nada y Scrapy terminaba con codigo 0: el job
   quedaba `completed` con 0 URLs, que se lee igual que un sitio vacio y en
   orden (y con la comparacion entre censos de #32 declararia desaparecido el
   sitio entero). Ahora es `failed`, y el motivo distingue
   `robots_bloquea_todo` —un hallazgo que se cuenta en una frase— de
   `sin_urls`, que hay que mirar. El spider deja la marca en Redis cuando lo
   prohibido es una SEMILLA. Un tercer motivo, `config_invalida`: las reglas
   de extraccion a medida no compilan y no se rastrea nada; ese manda incluso
   en una reanudacion con filas ya guardadas, y el motivo concreto va al log
   del worker ([104](extraccion-a-medida.md#d104)).

<a id="d40"></a>
40. **Un candado no puede tragarse los errores de lo que protege** — la primera
   version de `candado_de_job` tenia el `yield` dentro de un `try/except`
   amplio: cuando el analisis de dentro reventaba, la excepcion entraba por el
   yield, la cazaba ese except y se cedia por segunda vez, asi que Python
   lanzaba `generator didn't stop after throw()` y el error ORIGINAL
   desaparecia. Medido: el analisis de un censo de 60.399 URLs fallo y el log
   solo decia eso. Un solo `yield`, y el `except` del montaje no lo envuelve.

<a id="d75"></a>
75. **Los rastreos se programan, y el planificador vive DENTRO del worker**
   (#36) — un `crontab` en el servidor se salta todo lo que el worker ya
   resuelve: la cola FIFO (decisión 31), el candado del análisis, el vigilante
   de estancamientos y el problema de que un despliegue recree el contenedor a
   media faena. Y no se ve desde ningún sitio. `shared/programaciones.py` +
   `_quizas_programar` en el bucle del worker, al lado del vigilante; lo que se
   dispara es un `encolar()`, no un rastreo, así que todo lo anterior sigue
   mandando. De APScheduler se usa **solo el motor de tiempos**: un hilo suyo
   en un proceso que lanza Scrapy como subproceso y maneja señales es una
   carrera esperando.
   Cuatro criterios: un disparo perdido se lanza tarde dentro de una ventana de
   12 h (cubre un despliegue —2-3 min medidos— y un reinicio, no un fin de
   semana caído; es un juicio, no una medida); **no se encola si el rastreo
   anterior de esa programación sigue vivo**, porque uno semanal que tarda
   nueve días encolaría el siguiente encima y en 2 vCPU se caen los dos; la
   próxima se recalcula SIEMPRE, se haya lanzado o no, porque si no se queda
   con la hora pasada y reintenta cada minuto para siempre; y el motivo de no
   lanzar se guarda en `ultimo_motivo`, porque una programación que deja de
   dispararse sin decir por qué es indistinguible de una que nadie miró.

<a id="d76"></a>
76. **`CronTrigger.from_crontab` de APScheduler NO es cron** — su día de la
   semana es 0=lunes, así que `0 3 * * 1` —«lunes a las 3:00» en cualquier
   crontab— le sale **martes**, y el 7 (domingo, válido en cron) lo rechaza con
   un error. Contrastado contra `croniter` el 2026-10-09:

   | campo | `from_crontab` | cron real |
   |---|---|---|
   | 0 | lunes | **domingo** |
   | 1 | **martes** | **lunes** |
   | 6 | domingo | **sábado** |
   | 7 | error | domingo |

   Es el fallo perfecto para no enterarse: la programación se dispara, solo que
   un día tarde, todas las semanas, para siempre. `_dia_de_semana` traduce los
   números a nombres (`mon`, `tue`…), que sí significan lo mismo en los dos, y
   despliega `*/N` antes de traducir porque un paso sobre otra numeración no da
   los mismos días. Hay un test parametrizado que contrasta 15 expresiones
   contra `croniter`.
   Y la próxima vez es **estrictamente posterior**: preguntado con
   `(None, ahora)`, APScheduler incluye el instante exacto —a las 12:00:00 en
   punto, `*/15` devuelve las 12:00:00—, y como al disparar se recalcula desde
   la hora actual, un disparo que cayera justo en el minuto se quedaría con su
   propia hora y se repetiría en cada vuelta del bucle.
