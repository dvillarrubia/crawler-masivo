# Decisiones: capa de decisiones (llm) y análisis enriquecido

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d84"></a>
84. **Jev por OpenRouter: una capa que no se dispara sola, no tumba un censo y
   no vuelve a pagar lo mismo** (#7). Base de la capa de decisiones (#6);
   el disparo es el boton de #40.
   - **Un transporte, un formato verificado.** `shared/llm.py` es el UNICO
     sitio que habla con un modelo, siempre a traves de OpenRouter
     (`POST https://openrouter.ai/api/v1/systemone`, la ruta estable y
     compatible con TypeSafe; no la alfa `/api/alpha/decisions`). Cliente
     propio sobre `httpx` y no los SDK: el de OpenRouter fija `pydantic<2.13`
     y no reintenta los 429; el de TypeSafe descarta `usage.cost`, que es lo
     que necesita el presupuesto. El formato lo fija `test_llm_contrato.py`
     contra los modelos Pydantic de `openrouter==1.3.22` y
     `typesafe-sdk==0.7.2` en su propio trabajo de CI. Verificado el
     2026-10-13 en el codigo de los dos SDK, en la guia
     `openrouter.ai/docs/guides/community/typesafe-sdk`, en
     `docs.typesafe.ai/{api,models}` y en `GET /api/v1/models/typesafe/
     jev-1.13/endpoints`: un solo endpoint (proveedor TypeSafe,
     `typesafe/jev-1.13-20260917`), **0,042 $/M tokens de entrada, salida
     gratis**, 32k de prompt y 64k de contexto, y aparece en
     `GET /api/v1/endpoints/zdr` (o sea que `provider.zdr=true` deberia
     encontrarlo; sin probar con una llamada). `instructions` es obligatorio
     en los tres tipos (el esquema de TypeSafe lo deja opcional, el de
     OpenRouter y la referencia publica no), y un `noul` lleva sus dos
     criterios o ninguno. No esta publicado el limite de tasa de Jev A TRAVES
     de OpenRouter (TypeSafe directo: 80 peticiones/s y 100.000 tokens/s), de
     ahi `LLM_RPM=600` y AIMD: tras un 429 el ritmo baja a la mitad 60 s.
   - **Resiliencia.** Reintento con backoff y jitter (o `retry-after-ms` /
     `retry-after`) ante 408, 429, 5xx, 524, 529, timeouts y errores de red:
     3 reintentos y 30 s por llamada como mucho. Sin reintento ante 400, 413
     y 422 (esa peticion esta mal; las demas siguen). 402 (sin credito), 401,
     403 y 404 **cortan la capa** para el resto de la ejecucion, tambien las
     llamadas que esperaban turno; cinco fallos seguidos abren el circuito.
     Lo que no se decide queda `sin_decidir` con su `motivo` y **nada lanza**:
     una caida de Jev no puede tumbar un censo.
   - **Cache por el estado normalizado, no por bytes ni por URL.** Clave =
     SHA-256 de (estado sin caja, sin espacios repetidos, sin nonces/CSRF de
     24+ caracteres, sin marcas de tiempo con hora y sin `?v=`) | pregunta |
     version | **modelo pedido**. Los numeros de negocio NO se tocan: un precio
     o la fecha de una oferta cambian la respuesta. Tres niveles: memoria de
     la ejecucion, Redis `llm:dec:{clave}` (30 dias, lo que comparten las
     replicas) y la tabla `decisions` (duradera: un recrawl no vuelve a
     pagar). Dos estados identicos —bloques de plantilla— son UNA llamada, y
     las preguntas que faltan de un estado van todas en la misma. Lo
     `sin_decidir` no se cachea. La tabla tolera no existir aun (primer minuto
     de un despliegue): es una cache que falla, no una decision.
   - **El modelo va fijado y la version es parte de la pregunta.** El
     catalogo (`shared/preguntas/`) rechaza `*-latest`: el alias se mueve
     cuando sale una version y la cache y los umbrales de #8 dejarian de
     significar lo que significaban. Cambiar el texto de una pregunta es subir
     su `version`; un test guarda la huella de cada (nombre, version) y falla
     si cambia el texto sin la version. Sin umbral calibrado por #8 **nada
     sale `decidido`**: todo lo respondido es `incierto`.
   - **Presupuesto en dolares, con reserva.** Antes de cada llamada se
     reserva su coste estimado contra tres topes —el de la ejecucion (#40),
     el mensual del cliente (`clients.decisiones.presupuesto_mensual_usd`) y
     el global (`ENRICHMENT_MAX_USD_MES`)— y despues se ajusta con
     `usage.cost` (si falta, tokens x precio, y se marca `coste_estimado`).
     Los mensuales van en Redis con `INCRBYFLOAT` (atomico entre replicas) y
     se siembran desde `decisions` si Redis se vacio. La reserva aprende del
     coste real (nunca baja de la estimacion por precio) y se hace dentro del
     cupo de llamadas en vuelo: con `gather` sin cupo todas reservaban a la
     vez con la estimacion inicial y el tope se pasaba por tantas llamadas
     como estados. El exceso maximo es el error de estimacion de
     `LLM_MAX_CONCURRENCIA` llamadas. `estimar()` da llamadas, tokens y coste
     tras la cache sin llamar (lo que enseña el dialogo de #40).
   - **Que sale de la instalacion.** Hacia OpenRouter, y de ahi a TypeSafe:
     el `state` que construya cada familia de preguntas (#9 en adelante) —
     estados MINIMOS: titulo, h1, el principio del contenido principal,
     codigo, cabeceras relevantes, metricas; **nunca el HTML entero**—, las
     instrucciones y opciones de las preguntas, el modelo, `session_id` = id
     del job (segun el SDK de OpenRouter, «never sent to the provider») y
     `user` = **slug** del cliente (nunca su nombre). `provider` lleva
     `data_collection: deny` siempre y `zdr: true` con `LLM_ZDR=1`; la
     retencion cero depende del proveedor que hay detras, no la garantiza
     OpenRouter. Nada mas: ni URLs completas si el estado no las incluye, ni
     cookies, ni credenciales. La clave va en el entorno, nunca en la BD; un
     cliente que no autorice el envio no debe tener ejecuciones de #40.
   - **Nada se dispara solo.** Ni el spider, ni el analizador, ni el worker,
     ni la API importan la capa, y `test_decisiones_no_se_disparan.py` lo
     comprueba **ejecutando** un analisis completo y un rastreo real con
     `DECISIONS_ENABLED=1` y una clave puesta, con una trampa que anota
     cualquier cliente construido o intento de red. En toda la suite, un
     transporte real de httpx hacia `openrouter.ai`/`typesafe.ai` falla
     (`conftest.py`) y la clave se borra del entorno; los tests usan el
     `FakeLLM` de `tests/fake_llm.py` (probabilidades fijas, 429, 503, 402...).
   - **Observabilidad por job** en `jobs.decisiones_resumen`, expuesta en
     `GET /api/jobs/{id}`: llamadas, tokens, coste real, aciertos de cache
     (y cuantos de la BD), 429, reintentos, errores, latencia (histograma por
     cubos, que se fusiona EXACTO entre ejecuciones; p50 y p95 son el limite
     del cubo), distribucion de respuestas por pregunta, avisos y `estado`
     (ok, degradado, presupuesto_agotado, desactivado, clave_invalida,
     sin_credito; al fusionar gana el peor). Se acumula con bloqueo de fila.
   - **`decisions`**: clave estable (no depende de `url_id`, para que el
     rastreo incremental de #36 F4 arrastre decisiones sin pagar), `run_id`
     nullable y sin FK hasta que exista la tabla de ejecuciones de #40, el
     modelo pedido y el que respondio, y `estado_enviado` (lo que VIO el
     modelo, para la revision de #8). El coste de una llamada con varias
     preguntas se reparte a partes iguales entre sus filas.
   - Fuera de este PR: la migracion del modulo semantico a OpenRouter (paso 9
     de la guia de #7, aparcado en el ROADMAP), `job.config.decisions` para los
     modulos de rastreo (#10/#14/#20 lo declararan en `JobConfig` cuando
     existan) y la prueba real con 1.000 URLs, que necesita una clave.

<a id="d85"></a>
85. **El análisis enriquecido se lanza a mano, con token, y lo ejecuta el
   worker desde su propia cola** (#40). Es el disparo de la capa de la
   decisión 84 y lo único de la instalación que gasta dinero después de un
   rastreo.
   - **Seguridad, porque la API no tiene autenticación.** Con el CORS abierto,
     cualquiera que llegue al puerto podía lanzar análisis de pago. Crear una
     ejecución (`POST /api/jobs/{id}/enrichment`) exige la cabecera
     `X-Enrichment-Token` igual a `ENRICHMENT_TOKEN`, comparada con
     `hmac.compare_digest` (un `==` corta en el primer carácter distinto y el
     tiempo de respuesta va diciendo cuántos se han acertado). **Sin la
     variable, o con menos de 16 caracteres, el lanzamiento está DESACTIVADO**
     (403 que dice qué falta), nunca abierto: el fallo seguro de un despliegue
     que se olvida de configurarla es que no se pueda gastar. Ninguna respuesta
     ni ninguna fila repite el token. Estimar, leer y **cancelar** no lo piden:
     no gastan, y cancelar es justo lo que tiene que poder hacerse deprisa
     cuando algo se va de las manos. El frontend lo pide en el diálogo, lo manda
     solo en esa petición y lo guarda en `sessionStorage` (muere con la
     pestaña), nunca en `localStorage` ni en la URL; uno rechazado se borra.
   - **Cola aparte, no un tipo de mensaje.** `enrichment:pending`
     (`shared/cola.py`) y no ids mezclados en `jobs:pending`: todo lo que lee la
     de rastreos (el bucle, el vigilante, las reanudaciones) da por hecho que
     cada elemento es un job, y un id de ejecución colado ahí acabaría lanzado
     como rastreo. El worker la atiende con `lpop` sin bloquear ANTES de mirar
     el cupo de rastreos (si no, con los huecos llenos no se leería nunca) y en
     su PROPIO pool (`MAX_CONCURRENT_ENRIQUECIDOS=1`): 60.000 URLs a 600 por
     minuto es hora y media que no puede quitarle el hueco a un rastreo, ni un
     rastreo de nueve horas tener esperando a quien pulsó el botón. Nunca dentro
     del request, que es el error de `fetch-gsc`.
   - **Estados**: `pendiente → en_curso → completada | parcial | cancelada |
     fallida`, más `cancelando` mientras corre. El worker **reclama** con un
     UPDATE condicional sobre `pendiente`, así que un id duplicado en la cola
     (un reencolado tras un reinicio) lo ejecuta uno solo. Entre lote y lote
     (`ENRICHMENT_LOTE=200` URLs) relee el estado, anota progreso, gasto y
     latido. `parcial` = cortó un presupuesto (el de la ejecución, el mensual
     del cliente o el global): lo procesado queda guardado. `fallida` = sin
     crédito, clave mala, capa desactivada en el worker o una excepción.
     `cancelada` conserva todo lo pagado. Sin latido en
     `ENRICHMENT_STALE_MINUTES` (15) el vigilante devuelve a `pendiente` y
     reencola.
   - **Idempotente sin repagar.** Lo que no se decidió NI se pagó (cortes,
     red) no se escribe en `decisions`, para que reanudar lo vuelva a intentar;
     lo pagado se escribe siempre, aunque la respuesta viniera incompleta, o
     ese dinero desaparecería de la cuenta. Reanudar salta las URLs que ya
     tienen fila con ese `run_id`, y relanzar desde cero sale de la caché de la
     decisión 84: medido en la prueba de la interfaz contra Postgres, 40 URLs
     → 40 llamadas la primera vez y **0 llamadas y 0 $** la segunda.
   - **Coste real**: `gastado_usd` es la suma de `usage.cost` (respaldo por
     tokens marcado en `decisions.coste_estimado`). `estimacion` guarda lo que
     enseñó el diálogo al confirmar, para la comparación ±30% de #40, que queda
     **pendiente**: sin una clave no hay coste real con el que comparar.
   - **Módulos**: `shared/enriquecido.REGISTRO`, cada uno con sus preguntas del
     catálogo, cómo construir el estado y de qué otros depende
     (`resolver_modulos` los añade solos y los pone antes; un ciclo es un
     error, no un cuelgue). Hoy solo `pagina_legal`, la pregunta de ejemplo de
     #7, marcado como ejemplo: los de verdad son #9 con su evaluación de #8, y
     un test falla si aparece otro. Su estado es título, primer h1 que se pinta
     y 600 caracteres de `page_content` (sin plantilla). Alcance por defecto:
     internas, HTML, 200 e indexables; la carpeta va con `startswith` escapado
     (un `%` es literal); el modo muestra ordena por `url_hash` (un SHA-256:
     reparto estable y la misma muestra si se repite, que entonces sale de
     caché). Un `job` solo admite una ejecución activa a la vez y solo si está
     `completed` (un análisis que falla deja `failed`, decisión 28).
   - **Antes de encolar** se rehace la estimación y, si el cliente o la
     instancia ya agotaron su mes, es un 409 que da las cifras: mejor que una
     ejecución que se queda `parcial` sin haber hecho nada.
   - **Qué cambia de la decisión 84**: el worker y la API YA llegan a la capa,
     pero solo a través de `shared.enriquecido` y solo desde dos sitios, que
     `test_decisiones_no_se_disparan.py` comprueba por AST con la función que
     importa: el router de enriquecido y, en el worker, `_ejecutar_enriquecido`
     y `_recuperar_enriquecidos`. Otro test **ejecuta** `import worker` y
     comprueba que no carga la capa, y otro dispara una programación y
     comprueba que solo se encola el rastreo. `decisions.run_id` tiene ya su FK
     (`SET NULL`, `scripts/init_db.py`).
   - **Pendiente**: la opción por perfil «repetir el análisis enriquecido en
     cada ejecución» (desactivada por defecto cuando exista; hoy un perfil o
     una programación **no lanzan nada de pago**), las fuentes externas
     (DataForSEO, Ahrefs), los resultados como columnas del explorador, de
     issues y del CSV, y sustituir las issues de un módulo al relanzarlo (no
     hay módulos que emitan issues todavía). Al apagar el worker una ejecución
     en curso no se interrumpe: se queda sin latido y el vigilante la reencola.

<a id="d159"></a>
159. **Una pregunta a Jev se mide antes de decidir nada, y el juego se prepara
   sin gastar** (#8, parte que no necesita la clave). En producción no hay
   `OPENROUTER_API_KEY` (comprobado el 23-oct-2026), así que nada de la capa
   puede funcionar todavía; lo que se puede dejar listo es cómo se medirá cada
   pregunta el día que la haya:
   - `scripts/evaluar_decisiones.py extraer <pregunta> --job …` saca un juego
     de censos reales **estratificado por plantilla** (por turnos: una de cada
     plantilla, después la segunda…, para que 20.000 fichas no se coman el
     juego y una plantilla de 12 páginas legales también salga), con la
     etiqueta vacía, a `tests/decisiones/<pregunta>/v<version>.jsonl`.
     Determinista con la semilla (consulta ordenada; el orden de plantillas se
     baraja en cada vuelta para que un cliente o las rutas «/z» no se queden
     fuera); la misma URL en dos censos es un ejemplo. En Lopesan: 150
     ejemplos de 87 plantillas.
   - `evaluar` mide contra la **etiqueta humana** (nunca la de Jev):
     acierto, precisión y recall por clase, matriz, **ECE** con su curva de
     fiabilidad y la **curva de cobertura frente a precisión**, de la que sale
     el umbral: el más bajo con el que lo decidido llega a la precisión
     objetivo **con margen** —la cota inferior de Wilson al 95 % ≥ 0,9, no la
     precisión observada: 29 de 30 es «0,97» y su cota, 0,83— cubriendo al
     menos 30 ejemplos, y sigue llegando con cualquier umbral más alto (un pico
     suelto no es un umbral). Sin eso, la pregunta se queda sin umbral y nada
     sale `decidido` (84). La etiqueta humana se normaliza («sí», `True`) y la
     que no está en el vocabulario de la pregunta se cuenta como inválida.
   - `repetir` da la **repetibilidad** (la misma pregunta dos veces, casi
     gratis: descarta preguntas inestables antes de etiquetar) y, con
     `--acuerdo`, el acuerdo y la kappa entre dos jueces o dos redacciones, que
     no es acierto (comentario de #8 tras revisar jev-seo). En el vocabulario
     de la etiqueta (en un score, 2,6 y 2,61 son el mismo nivel); la kappa de
     dos jueces que dicen siempre lo mismo no se sabe (0/0), no es 1.
   - `responder` es la **única vía directa a la capa fuera del botón de #40**:
     pide clave, `DECISIONS_ENABLED=1`, que exista `ENRICHMENT_TOKEN` y
     `--confirmar`; sin confirmar solo enseña la estimación. Dentro del
     contenedor el token está en el entorno, así que la barrera real es
     `--confirmar`. Gasta con los mismos presupuestos que el botón (mensual del
     cliente y global, desde `decisions` y en Redis; sin Redis no gasta), un
     tope de la estimación × 1,5, y deja las decisiones en `decisions`. El test de
     «nada se dispara solo» la permite por fichero y función, y cualquier otro
     import del script sigue fallando. Estimado para el juego de Lopesan: 138
     llamadas, 0,002 $.
   - Las métricas son funciones puras (`shared/evaluacion_decisiones.py`)
     probadas sin red; el script se prueba de punta a punta con `FakeLLM`.
   Queda de #8 lo que necesita la clave y personas: etiquetar, la vista de
   revisión en la interfaz y `decision_labels`.

<a id="d162"></a>
162. **Jev en producción: la primera llamada real calibra la estimación y el gasto
   se cuenta una vez** (#40). El 24-oct-2026 se configuró la clave de
   OpenRouter en el servidor (`DECISIONS_ENABLED=1`, `ENRICHMENT_TOKEN` y un
   tope global de **5 $ al mes**, `ENRICHMENT_MAX_USD_MES`). Primera llamada:
   el juego de 150 páginas de Lopesan (159) con `es_pagina_legal`, a mano y con
   `--confirmar`. 150 respuestas de `typesafe/jev-1.13-20260917`, todas
   `incierto` (sin umbral no hay `decidido`, 84); 5 «legales»: cuatro
   políticas de privacidad con 0,98-0,99 y «Criminal Risk Prevention» con 0,65.
   - **La estimación se quedaba corta**: 0,0022 $ estimados y un 52 % más
     facturado. Ajustando los tokens reales contra los caracteres enviados:
     **tokens = 0,247 × caracteres + 246** (error medio del 2,8 %, frente al
     27,6 % de «caracteres / 3»). Hay un coste fijo por llamada que el
     proveedor añade. La estimación pasa a `caracteres / 4 + 250`, un pelo por
     encima, que es el lado bueno para una reserva. El precio facturado
     cuadra con el publicado (0,042 $/Mtok).
   - **El gasto de los estados repetidos se contaba varias veces**: dos URLs con
     el mismo estado hacen UNA llamada, pero cada copia de la decisión llevaba
     el coste, y la suma de `decisions` —de la que se siembra el gasto del mes
     al arrancar— lo contaba tantas veces como URLs (14 de 150 en esta prueba).
     Ahora el coste va solo en la primera copia. El gasto de la ejecución
     (`gastado_usd`) ya salía del presupuesto y estaba bien.

<a id="d163"></a>
163. **La primera pregunta de verdad: soft 404, solo sobre lo sospechoso y marcada
   como sin evaluar** (#11). `es_soft_404` (noul) pregunta si una página que
   responde 200 comunica en realidad un error, un producto que ya no está, una
   búsqueda vacía o un «próximamente», con la ruta, las palabras, el título, el
   H1 y el principio del contenido.
   - **Solo sobre lo sospechoso**: los candidatos que dejó la versión sin
     modelo (decisión 105) y las indexables con menos de 50 palabras propias.
     Las que la determinista ya afirmó no se preguntan. Sin las de poco
     contenido el módulo no tenía trabajo: en producción la determinista solo
     dejaba un candidato; con ellas, 17 de 3.216 indexables en Lopesan y 47 de
     1.080 en Saunier Duval.
   - Un módulo puede ahora filtrar sus URLs (`Modulo.filtrar_urls`), y el
     filtro se aplica **igual** en la estimación del diálogo y al ejecutar, con
     el total del progreso fijo desde el principio. `extraer` (159) saca el
     juego de lo mismo que pregunta el módulo.
   - **Sin umbral, y el módulo marcado como sin evaluar**: todo sale
     `incierto` y nada va a un informe. El test que solo dejaba existir el
     módulo de ejemplo pasa a exigir esto de cualquier módulo: si alguna de sus
     preguntas no tiene umbral, va marcado.
