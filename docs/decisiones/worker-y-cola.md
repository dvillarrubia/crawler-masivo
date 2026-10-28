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

<a id="d193"></a>
193. **Una programación cuelga de un perfil y en cada disparo hereda la configuración del momento, no una copia congelada; la serie y los incidentes son del perfil**

   **Problema.** Perfil, programación y serie de evolución eran tres cosas
   desconectadas (revisión de UX del 25-oct, V06, V07 y V44). Una programación
   guardaba una COPIA de las semillas y la configuración del rastreo del que se
   creó: un cambio en el cliente o en el perfil (otro UA contra un WAF, una
   carpeta excluida) no llegaba nunca al censo del lunes, y nada lo decía. Cada
   programación hacía su serie («programacion:<id>»), aparte de la del perfil.
   En Lopesan, el 26-oct: un perfil «lista» nunca lanzado, tres programaciones
   «lopesan.com (semanal)» con las mismas 3.040 semillas (dos estuvieron
   activas a la vez la semana anterior, gastando producción dos veces) y dos
   series iguales en la gráfica. Programados no dejaba crear ni editar, y la
   única forma de programar era desde la ficha de un rastreo viejo.

   **Decisión.**
   - `programaciones.hereda_perfil` (bool, por defecto falso). Verdadero = la
     programación cuelga de su perfil: `seeds` queda vacía y `config` guarda
     solo lo propio de la programación (p. ej. un tope de URLs). El worker
     (`shared.programaciones.resolver_disparo`) lee en cada disparo el perfil
     y el cliente de ESE momento: semillas del perfil y config = defaults ←
     cliente ← perfil ← lo propio, en el mismo orden que la API
     (`resolver_config`). Si el perfil ya no existe, está desactivado o no tiene
     semillas, no se lanza y `ultimo_motivo` lo dice.
   - La imagen del worker no lleva la API ni Pydantic, así que los defaults de
     `JobConfig` van copiados en `shared/config_por_defecto.json` y un test los
     compara con `JobConfig()`: un campo nuevo sin regenerar la copia hace
     fallar la suite, no el censo. La validación de la mezcla se hace al
     ESCRIBIR: crear o editar la programación (lo propio sobre cliente y
     perfil), editar el perfil o el cliente (sus programaciones colgadas tienen
     que seguir siendo válidas: 422 al guardar, no un disparo que no sale).
   - No se encola si el perfil ya tiene un rastreo vivo, aunque lo lanzara otra
     programación o una persona: es el mismo sitio dos veces a la vez.
   - Las de copia fija (las que había) siguen funcionando igual: compatibilidad
     hasta migrarlas. `POST /api/programaciones` con `seeds` y `config`
     completas sigue creando una copia fija (scripts, MCP con semillas propias);
     con `profile_id` y sin semillas crea una colgada. El MCP (`programar_rastreo`)
     cuelga del perfil cuando se programa un perfil sin semillas propias.
   - Repetidas: `GET /api/programaciones` dice en `repetida_con` qué otras
     activas repiten el trabajo (mismo perfil o mismas semillas efectivas). No
     se bloquea —un canario diario y un censo semanal del mismo perfil pueden ser
     a propósito—: se avisa en Programados, en el perfil y antes de crear otra.
   - Borrar un perfil borra sus programaciones colgadas (sin perfil no tienen
     qué lanzar); las de copia fija solo pierden el enlace, como antes. Sus
     rastreos se quedan.
   - **Series históricas.** Lo que identifica la serie sigue siendo
     `serie_de_job`/`clave_de_serie`: el perfil si el job lo tiene. Migrar una
     programación sin tocar sus rastreos partiría la gráfica en dos (los puntos
     viejos en «programacion:<id>», los nuevos en «perfil:<id>»). Por eso
     `unir_serie_al_perfil` da `profile_id` a los rastreos que lanzó (solo a
     los que no tenían perfil) y pasa sus incidentes a «perfil:<id>»; si el
     perfil ya tenía vivo uno con la misma clave, el de la programación se
     cierra con `motivo_cierre = 'serie_unida'` (sin afirmar que el fallo se
     fue, decisiones 67 y 89), para que no haya dos vivos. Así las tres
     programaciones de Lopesan acaban en UNA serie «Sitio completo» con sus
     cinco censos. La comparación automática (`censo_anterior`) no cambia:
     sigue buscando primero el anterior de la misma programación, que es lo
     que evita comparar un censo con un canario lanzado a mano (decisión 78).
   - `scripts/migrar_programaciones_a_perfiles.py`, en simulación por defecto
     (`--confirmar` para aplicar): para cada programación de copia fija con
     cliente, reutiliza el perfil del cliente con exactamente las mismas
     semillas o crea «Sitio completo» con, como config, solo lo que la copia
     tiene de distinto respecto al cliente (`shared.clientes.diferencia_de_config`);
     la cuelga (lo propio = lo que difiere del perfil) y une su serie. Antes de
     tocar nada comprueba que lo que lanzaría el siguiente disparo es lo mismo
     que antes; si no cuadra, o si la copia lleva contraseña de autenticación
     básica (un perfil no guarda contraseñas, decisión 83), la deja como está y
     dice por qué. Las activas van primero, para que el perfil salga de la que
     se lanza. Idempotente. NO se ha ejecutado en producción.

   **Revisión del 26-oct (antes de mergear).** Una revisión independiente
   encontró que el worker no resolvía igual que la API, y se corrigió:
   - `config_del_momento` fundía las capas sobre los defaults y se saltaba los
     `model_validator` de `JobConfig`: el perfil «lista» de Lopesan
     (`max_depth: 0`) programado habría sembrado el sitemap entero (lo que la
     decisión 128 evita) y un UA de iPhone salía con huella de Chrome. Las
     claves que `JobConfig` deduce de otras viven ahora en una sola función,
     `shared.perfiles.claves_deducidas` (modo lista sin sitemap; UA y huella,
     decisión 132), que llaman los validadores de `JobConfig` y el worker.
     `config_por_defecto.json` lleva también los defaults que valen `None`
     (`render_wait_ms`…) y los de cada regla (`custom_extraction`,
     `custom_search`, `segmentos`), que Pydantic rellena y una capa guardada no
     lleva. Un test compara `config_del_momento` con `resolver_config` caso a
     caso (clientes con la forma de los de producción × perfiles que deducen
     claves × lo propio); con las configuraciones reales de producción del
     26-oct (17 clientes, su perfil y las 4 programaciones) dan lo mismo en los
     1.785 casos.
   - La migración fija explícitas las claves que la resolución deduciría
     distinto (`capa_que_reproduce`) y va con un savepoint por programación:
     si la comprobación falla, el perfil recién creado se deshace con ella (con
     `flush` ya insertado, quedaba huérfano).
   - El bloqueo por «rastreo vivo del perfil» solo vale para las colgadas
     (`hereda_perfil`): una de copia fija con `profile_id` sigue mirando solo su
     rastreo anterior, como antes. Y `ultimo_motivo` distingue «el rastreo
     anterior de esta programación sigue sin terminar» de «su perfil ya tiene
     un rastreo sin terminar (lanzado a mano o por otra programación)»: un
     canario diario colgado del mismo perfil que un censo semanal se salta los
     días que corre el censo, a propósito, y ahora lo dice con verdad.
   - «Programar» desde un rastreo con «Crear un perfil con este rastreo»
     (`POST /api/clients/{id}/profiles` con `desde_job_id`) guarda en el perfil
     lo que la config del rastreo tiene de distinto respecto al cliente. Antes
     guardaba solo las semillas, y el censo de Lopesan 1e7ea651 se habría
     programado sin sus `exclude_patterns` (entre ellos `%25`, el que evita los
     400 del F5). Lo que no cabe en un perfil (la contraseña de autenticación
     básica, claves que `JobConfig` ya no tiene) no se copia y se dice en
     `avisos`.

   Tests: `tests/test_programaciones_perfil.py` (disparo con lo del momento,
   copia fija igual que antes, perfil desactivado o sin semillas, rastreo vivo
   del perfil y su motivo, copia fija con perfil que no lo mira, borrado,
   validaciones, repetidas, worker igual que la API, perfil desde un rastreo,
   migración en simulación y de verdad sobre el caso Lopesan, con claves
   deducidas y sin perfil huérfano, serie única en la evolución, defaults al
   día).

<a id="d209"></a>
209. **Un disparo programado se toma con candado y se encola después de guardarlo; los programados esperan en su propia cola, detrás de lo lanzado a mano; y una programación se puede lanzar ahora**

   **Problema.** Con más de un worker, cada uno pasa por `_lanzar_programados`
   cada minuto. Los dos leían las pendientes, decidían sin candado y creaban
   el job: si coincidían en el mismo instante, un disparo salía dos veces, con
   dos rastreos del mismo sitio a la vez. Contra Postgres, forzando el cruce,
   salen 2 jobs de 1 disparo. **Corrección tras desplegar:** se escribió que
   producción corre dos réplicas, por el `replicas: 2` de
   `docker-compose.prod.yml`. No es así: el despliegue usa
   `docker-compose.vps.yml` y en el VPS hay un solo worker
   (`crawler-masivo-worker`). El fallo no podía darse hoy. El candado es para
   cuando se escale, y el resto de la decisión vale igual. Había otros dos
   fallos en el mismo código: el id se encolaba ANTES del commit, así que la
   otra réplica podía sacarlo de Redis y no encontrarlo en la base de datos; y
   una programación que reventaba hacía perder la pasada entera, y como va
   primero la que tiene la hora más antigua, se perdía en cada vuelta. #36 F2
   pedía además una cola de menor prioridad para lo programado y un «Ejecutar
   ahora».

   **Decisión.**
   - Las programaciones se toman de **una en una** con `SELECT … FOR UPDATE SKIP
     LOCKED`, por hora. La réplica que llega segunda salta la fila que tiene
     cogida la otra, y cuando la otra la suelta ya lleva la próxima hora, así
     que no vuelve a salir. En SQLite (los tests) SQLAlchemy no emite el `FOR
     UPDATE`: allí hay un solo proceso.
   - Cada disparo hace su propio commit y **encola después**. Si Redis no
     responde, el job queda `failed` y `ultimo_motivo` lo dice. Sin eso se
     quedaba `pending` para siempre, y además bloqueaba el disparo siguiente
     por «el rastreo anterior sigue sin terminar».
   - Un disparo que revienta se deshace él solo y la pasada sigue con las
     demás. Él se reintenta en la pasada siguiente, porque su hora no avanzó.
   - **Dos colas**: lo programado va a `jobs:programados` (`encolar(…,
     programado=True)`) y `siguiente()` hace `BLPOP` sobre
     `[jobs:pending, jobs:programados]`. Redis mira las claves en orden, así
     que lo programado solo sale con la cola normal vacía. Quien lanza a mano
     está mirando la pantalla; el semanal de las 3:00 puede esperar a que acabe
     ese rastreo. Solo el PRIMER encolado de un disparo va a la cola de
     programados. Una reanudación o una recuperación tras un reinicio va a la
     normal: es trabajo ya empezado, y detrás de cada rastreo a mano podría
     quedarse parado días. `en_orden()` y `pendientes()` cuentan las dos colas,
     con los programados detrás, que es como salen.
   - **«Lanzar ahora»**: `POST /api/programaciones/{id}/ejecutar`, en el menú
     «⋯» de la fila. Crea el mismo job que el planificador
     (`crear_job_de_disparo`, compartido) y lo pone en la cola normal. Da 409
     con el motivo si hay un rastreo vivo, o si el perfil no existe, está
     desactivado o no tiene semillas. No toca la próxima hora: si el rastreo
     lanzado a mano sigue vivo cuando llega, ese disparo se salta y lo dice,
     como siempre. Funciona con la programación en pausa: pausar es «que no se
     lance sola», no «que no se pueda lanzar». `ultimo_motivo` queda en
     «lanzada a mano».

   - **Un rastreo vivo por perfil, también entre réplicas.** El candado de la
     programación no basta: dos programaciones colgadas del mismo perfil son
     dos filas, y las dos réplicas veían «nada vivo» a la vez.
     `bloquear_perfil` coge la fila del perfil antes de `motivo_rastreo_vivo`,
     con el mismo orden (programación, luego perfil) en el worker y en «Lanzar
     ahora».
   - **Un job solo arranca si está `pending`**: `_run_job` lo marca `running`
     con un `UPDATE … WHERE status = 'pending'`. Un id repetido en las colas
     ya no rastrea otra vez un censo terminado, ni uno que corre en la otra
     réplica. Con dos colas, una copia vieja puede esperar días en la de
     programados.
   - **Huérfanos.** Encolar después del commit tiene su propio hueco: un
     proceso que muere entre los dos deja un job `pending` fuera de las colas.
     La recuperación periódica (`_reencolar_huerfanos`) lo vuelve a encolar si
     se creó hace más de `STALE_JOB_MINUTES` y no tiene latido reciente. Un
     job reanudado mientras su proceso anterior se cierra está fuera de la cola
     a propósito, y su latido lo delata. «Lanzar ahora» sin Redis da 503 y deja
     el job `failed`, como el worker.

   **Fuera.** Lo demás de #36 F2: `ignore_patterns`, confirmar una alerta en
   dos ejecuciones y el criterio de dos semanas sobre un cliente real.

   Tests: `tests/test_programaciones_candado.py`.
   - Cola de programados y encolado después del commit, comprobado desde otra
     sesión.
   - Redis caído, y que el siguiente disparo no se lo salte.
   - Una programación que revienta no frena a las demás.
   - «Lanzar ahora»: crea el job, no toca la próxima hora, funciona en pausa,
     da 409 con un rastreo vivo y 404 si no existe, y el planificador respeta
     lo lanzado a mano.
   - **Dos réplicas contra Postgres de verdad**: sin el candado salen 2 jobs
     y con él sale 1. Lo mismo con dos programaciones del mismo perfil.
   - Un job que no está `pending` no arranca, los huérfanos se reencolan y
     «Lanzar ahora» sin Redis no deja la programación bloqueada.

   `tests/test_cola.py` añade la prioridad entre las dos colas, también contra
   Redis de verdad.

<a id="d220"></a>
220. **Rastreo incremental en los programados de un perfil: se pide lo que toca y el resto se copia del censo anterior antes del análisis, y cada job sigue siendo una foto completa**

   **Problema.** Cada disparo semanal rastreaba el sitio entero. En un sitio
   con cortesía, el tiempo lo pone el número de peticiones, no la CPU: Lopesan
   va a 1,7 URL/s después de #282. Casi todo es igual que la semana anterior,
   así que es pagar cada semana el censo entero para ver lo poco que cambia.
   #36 F4 pedía pedir solo lo que toca, sin que el analizador, el PageRank, la
   comparación ni las alertas tuvieran que saber que no se pidió todo.

   **Decisión.**
   - **Se activa en el perfil** (`config.incremental.activo`, con
     `completo_cada` = 4, `muestra_control` = 0,1, `umbral_control` = 0,2 e
     `intervalo_max` = 4 ciclos). Solo lo leen los disparos de una
     programación (`crear_job_de_disparo`, también «Lanzar ahora»). Un rastreo
     puntual lo ignora y rastrea todo. Apagado, nada cambia: `jobs.incremental`
     queda NULL.
   - **Un ciclo es un job del perfil, no un día**: no hace falta leer el cron
     para saber qué toca, y «Lanzar ahora» cuenta igual.
   - **Selección al crear el job** (`shared/incremental.preparar_job`). El
     ciclo sale **completo** si no hay censo anterior del perfil con estado, o
     si el anterior acabó truncado: lo que se copia tiene que ser una foto
     completa. También si la huella de la configuración o de las semillas ha
     cambiado (sin el bloque `incremental`), si el anterior lo pidió
     (`forzar_completo`), o si le toca el completo de cada N ciclos, que es el
     único que confirma que una URL ha desaparecido. Si no, es incremental:
     `job_frontera` recibe lo que toca de `url_state` (`proximo_ciclo <=
     ciclo`), lo fijo (semillas, portadas, hubs del percentil 95 de enlaces
     salientes) y una muestra al azar del resto. Si algo falla al preparar, el
     job sale completo con `error_al_preparar`: rastrear de más nunca deja una
     foto incompleta.
   - **Spider**: lo del censo anterior que no está en la frontera va al mismo
     conjunto de «ya rastreado» que usa la reanudación, así que ni los
     enlaces, ni el sitemap, ni las redirecciones lo piden, sin tocar `parse`.
     La frontera se pide con la profundidad del censo anterior (no como
     semilla) y, sin render, condicional (`If-None-Match` /
     `If-Modified-Since`). Con render no: Chromium que recibe un 304 sin copia
     en caché pinta una página vacía. Un 304 a una petición condicional es una
     fila sin hijos con `fetch_mode='not_modified'`: guardada como página
     normal sería no indexable, y sin guardar nada el job acabaría con
     «rastreadas > 0, guardadas = 0» (`persistence_failed`). Lo nuevo se
     descubre como siempre: por los enlaces de lo pedido y por el sitemap,
     que se lee entero en cada ciclo.
   - **Arrastre** (`analysis/arrastre.py`), en el worker, después del rastreo
     y antes del análisis, en una transacción:
     1. completa los 304 con la fila del anterior;
     2. copia a `urls` lo del anterior que no está (`fetch_mode='carried'`),
        con su `last_crawled_at` de entonces. Las columnas que calcula el
        análisis vuelven a su valor por defecto. Un test obliga a clasificar
        cada columna nueva de `urls` (copiada, calculada o de la fila) y a
        copiar todo lo que escribe el pipeline;
     3. copia los hijos (metadatos, encabezados, hreflang sin retorno ni
        validez, datos estructurados sin validar, recursos, contenido con el
        original sin limpiar, cabeceras, extracción a medida, enlaces no
        rastreables) y los enlaces, de `links` o del Parquet si el anterior
        está archivado (decisión 150), con el candado del anterior. No copia
        incidencias, Search Console ni nada del análisis;
     4. aplica la membresía del sitemap de ESTE rastreo, con la regla de
        siempre (`True`/`False`/`NULL`). El spider la escribe al cerrar,
        cuando lo copiado aún no existe, así que deja sus hashes en
        `sitemap_arrastre`;
     5. **poda** lo copiado (HTML 2xx) que ya no se alcanza desde las
        semillas, el sitemap o el destino de una redirección (el spider no
        guarda la redirección como enlace). Si una página pedida quita el
        enlace a una que no tocaba, esa saldría huérfana nueva y en la
        comparación nunca «desaparecería». Un completo no la habría
        encontrado.

     Con el arrastre hecho (`jobs.incremental.arrastre.hecho`) no se repite.
     Si el censo anterior ya no existe, el job acaba `failed` con
     `origen_arrastre_borrado`; si el arrastre falla, con `arrastre_fallido`.
     Una foto incompleta no es `completed` (decisiones 20 y 28).
   - **Estado** (`shared/incremental.actualizar_estado`), después del análisis
     de todo job con `jobs.incremental`, completo o incremental. Por URL:
     firma del contenido (`urls.content_hash`) y firma SEO (estado, destino,
     title, description, canonical, robots, H1 y enlaces salientes). El
     intervalo es adaptativo (StormCrawler): sin cambio crece ×1,4 hasta el
     máximo; con cambio se acerca al mínimo un 20 % por ciclo. Un error o un
     muro vuelve a tocar en el siguiente ciclo y sin cabeceras condicionales.
     Lo copiado conserva su turno. Lo que no está en el job sale de
     `url_state`. Si en la muestra de control cambia más del umbral, o cambia
     el robots.txt, el siguiente ciclo es completo. Best-effort: si falla, el
     siguiente sale completo (`sin_estado`).
   - En un incremental no corre la comprobación de render por plantilla:
     miraría páginas que no se han pedido.
   - **Que no parezca más fresco de lo que es** (decisión 20): la ficha del
     rastreo dice qué parte es copia y de cuándo es lo más antiguo, y cada URL
     lleva su `fetch_mode` y su `last_crawled_at` real.

   **Lo que no hace (todavía).** El `lastmod` del sitemap no adelanta el turno
   de una URL. Los topes por carpeta no cuentan lo copiado. Las sondas de
   respuesta, las capturas y la comprobación de render solo miran lo pedido.
   Una página que se ha movido de nivel conserva la profundidad vieja hasta el
   siguiente completo. Una página que no tocaba y ya no enlaza nadie, pero
   que se pidió por la frontera, no se poda: se pidió y respondió.

   `tests/test_incremental.py` cubre las reglas puras (completo o
   incremental, intervalo, estado de una URL) y la clasificación de columnas.
   `tests/test_incremental_db.py` (Postgres) prueba un ciclo completo y uno
   incremental de punta a punta: selección, 304 completado, arrastre desde
   `links` y desde Parquet, sitemap, poda, idempotencia, estado, la muestra de
   control que fuerza un completo, la base truncada y el origen borrado.
   `tests/test_spider_incremental.py` prueba el spider: lo que no toca no se
   pide, la frontera condicional con su profundidad, nada de condicionales
   con render, el 304 y la reanudación de un incremental ya arrastrado.
