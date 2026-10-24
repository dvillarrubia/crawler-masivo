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

<a id="d169"></a>
169. **Las preguntas se redactan con el criterio de la revisión humana: «legal»
   incluye lo normativo y el soft 404 se parte en dos** (#8, #9, #11). Una
   persona revisó a ciegas 40 ejemplos dudosos del juego de la 159 y coincidió
   solo en la mitad con las etiquetas de Claude y con Jev. No era ruido, eran
   dos criterios sistemáticos:
   - **Lo normativo es legal.** Las 7 discrepancias de `es_pagina_legal` eran
     políticas ESG, prevención de riesgos penales, alertas de seguridad de
     producto, avisos de subvenciones e información legal para accionistas.
     Para SEO son lo mismo que un aviso legal: existen por obligación, no para
     posicionar. `es_pagina_legal` v2 las cuenta.
   - **Una página de utilidad no es un soft 404.** La persona marcaba como «sí»
     logins, páginas de gracias o confirmación, la baja y el check-in online:
     funcionan como deben, pero no tienen valor de búsqueda. Son dos hallazgos
     distintos (un error disfrazado se arregla; una página de utilidad se
     saca del índice), así que son dos preguntas: `es_soft_404` v2 (error o
     vacío, y dice expresamente que un login o un «gracias» no lo son) y
     `es_pagina_de_utilidad` v1, en su propio módulo sobre los mismos
     sospechosos (`candidatos_soft404`).
   - Subir la versión invalida la caché y obliga a re-etiquetar y medir: un
     umbral calibrado con la redacción vieja no vale para la nueva.
   - **Medido el 24-oct-2026** (1 céntimo en Jev). El juego de la 159 se
     re-etiquetó a ciegas con las definiciones nuevas. En `es_pagina_legal` se
     usaron las etiquetas de la persona donde las había (14, todas coinciden);
     en el soft 404 no, porque respondió con el criterio viejo, que juntaba
     error y utilidad.
     - `es_pagina_legal` v2: 99 % sobre 300. 0,55 cubriría todo con cota de
       Wilson 0,971, pero no se pone: es la pregunta de ejemplo.
     - `es_soft_404` v2: 93,6 % sobre 94, y **sin umbral**. El umbral común
       que salía (0,90, 66 % de cobertura, 62 de 62 aciertos) decidía 60 «no»
       y 2 «sí»: de los 29 soft 404 reales, 27 se quedaban inciertos, entre
       ellos las ocho páginas de error evidentes, que Jev contesta «sí» con
       0,57-0,89. Un umbral así solo serviría para decir «esto no es un soft
       404», que no es el hallazgo. Hace falta un juego con más positivos (y
       quizá otra redacción) antes de poner umbral a la respuesta «sí».
     - `es_pagina_de_utilidad` v1: 91,5 %; 7 de sus 8 fallos eran páginas de
       error contadas como de utilidad. La v2 lo dice y sube al 97,9 %, pero
       **sin umbral**. El común que salía (0,65) solo se sostenía contando dos
       veces los estados repetidos (11 de 94 filas son la misma página con y
       sin www); sin ellos no sale ninguno, y el «no» decidido se quedaba en
       cota 0,88, por debajo del objetivo. Además se midió sobre el mismo juego
       con el que se redactó la v2, con dos clientes, etiquetas de Claude sin
       re-etiquetar para la v2 y 3 de ellas incoherentes con su definición
       (páginas 401 marcadas como de utilidad). Para ponerle umbral hace falta
       un juego más grande, de más clientes, no usado para redactarla y
       etiquetado con el criterio v2.
   - **`evaluar` quita los estados repetidos** (cuenta una vez cada estado
     idéntico y dice cuántos quitó y cuántos se contradecían) y **`avisos`
     salta también cuando una respuesta decidida queda por debajo del
     objetivo** aunque el umbral común llegue. Las dos cosas las vio otra
     revisión independiente, en la PR que ponía 0,65.
   - **La pantalla separa lo decidido de lo incierto** (para cuando haya
     umbrales): por respuesta dice
     cuántas están decididas, la tabla enseña el estado de cada fila y el
     aviso de «orientativas» depende de si la pregunta tiene umbral HOY, no de
     si alguna fila salió decidida (antes, una sola decidida lo escondía para
     todas).
   - **La evaluación separa ahora las respuestas** (`por_respuesta`): cuántas
     de cada una decide el umbral elegido, con qué cota, qué parte de las
     reales recupera y qué umbral tendría esa respuesta sola; y `avisos` dice
     cuándo el umbral común casi no decide una clase con 10 o más reales
     (menos del 25 %). Fue una revisión independiente la que vio que la cifra
     global escondía esto.

<a id="d175"></a>
175. **La canibalización se juzga por pares de páginas del mismo idioma
   (búsqueda + dos URLs), con dos preguntas en llamadas separadas y la
   recomendación leída solo si son sustitutas** (#18, #169 punto 3; revisada en
   el PR #244). Entrada: los avisos `canibalizacion_por_consulta` de la decisión
   157, que dicen QUÉ búsqueda se reparte entre varias URLs, no si compiten ni
   qué hacer. Módulo `canibalizacion` con `paginas_sustitutas` (noul: ¿quien
   hace esta búsqueda quedaría igual de servido con cualquiera de las dos?) y
   `recomendacion_canibalizacion` (choice: `fusionar`, `diferenciar`,
   `canonicalizar`, `dejar`). Dos hallazgos, dos preguntas (169).
   - **La unidad es el par, no la URL ni el grupo.** Una URL está en varios
     avisos (en Lopesan, censo `1e7ea651`, 459 avisos sobre 167 URLs ancla) y
     no puede ser la clave. Y el grupo entero tampoco: 33 de 459 avisos tienen
     3 o 4 páginas y en algunos solo dos son sustitutas («anfi del mar»: la
     ficha del hotel y dos posts que entre sí son el mismo). Juzgando el
     grupo, un `false` perdía esa canibalización y un `fusionar` no decía qué
     fusionar. Un aviso de n páginas da C(n,2) pares (como mucho 10).
     `Modulo.por_grupo`: su `filtrar_urls` (`pares_canibalizacion`) devuelve
     ids de PAR (`aviso × 16 + k`, solo viven dentro de una ejecución) y su
     `construir_estados` los recibe; cada `Estado` va con `ambito="consulta"`
     y `url_id` = la URL menor del par (estable entre censos). En `decisions`
     el par es la `clave` (hash del estado), o `estado_enviado.busqueda` y sus
     rutas, que `GET .../decisions` devuelve en `rutas` y la pantalla pinta
     bajo la búsqueda. Reanudar reconoce lo hecho por la clave; el progreso y
     la estimación cuentan pares (`unidad: "grupos"`).
   - **Un par en idiomas distintos no se pregunta.** Según la revisión del PR, 128 de los 459 avisos
     mezclan secciones de idioma (`/en/` frente a `/de/hotels` en «lopesan
     hotels»). Eso no es una canibalización que se arregle fusionando o
     diferenciando sino segmentación internacional, y preguntarlo daba un
     «no son sustitutas» que, con el efecto encendido, rebajaría el aviso como
     reparto legítimo: lo que no se puede afirmar no se afirma. El idioma es
     `<html lang>`; si falta (censos de antes de la columna, como `391b5ed6`),
     la anotación hreflang de la propia URL, y si no, el tramo de idioma de la
     ruta. Sin saber el de alguna de las dos, se pregunta. En Lopesan quedan
     fuera 132 de 528 pares y 112 búsquedas sin ningún par preguntable
     (6.696 de sus 27.993 clics); el aviso de la 157 sigue como está.
   - **Filtro**: los pares con ALGUNA página en el alcance (por defecto,
     alguna indexable; con carpeta, alguna dentro) y del mismo idioma, de las
     búsquedas con más clics en juego, como mucho **300** búsquedas
     (`MAX_GRUPOS`). En Lopesan son 328 pares con 20.816 clics; sin tope, 394
     pares de 347 búsquedas.
   - **Estado mínimo**: la búsqueda, `reparto_por` (clics, o impresiones en una
     búsqueda sin clics: las instrucciones explican que entonces `parte_pct`
     es de impresiones), `otras_paginas` (cuántas URLs más se la reparten: las
     partes del par no suman 100) y, por página, la ruta (con y sin www es el
     mismo estado), la parte en tramos de 5 %, clics e impresiones a dos cifras
     significativas, la posición entera, `no_indexable` (el motivo:
     `Canonicalised`, `Noindex`...) y `canonica` (la URL que ya declara como
     canónica, si apunta a otra), título, primer H1 que se pinta y 500
     caracteres del contenido sin plantilla. Sin el motivo y la canónica, Jev
     recomendaba canonicalizar lo que ya lo está: en Lopesan 74 páginas de 73
     avisos ya son `Canonicalised` (`/es/` → `/es`, una variante con `gclid`),
     todas hacia una URL de fuera del aviso (si estuviera dentro, la 157 ya las
     habría unido). **Las dos páginas van ordenadas por ruta**, no por parte:
     en un reparto casi al 50 % («kumara», 0,524/0,472) el orden por parte se
     invierte de un recrawl a otro con las mismas cifras redondeadas y la
     caché fallaba.
   - **Las URLs se buscan por `(job_id, url_hash)`**, el índice único: `urls.url`
     no tiene índice y un `url IN (...)` recorría todas las filas del job en
     cada trozo, dentro de la petición síncrona de la estimación. `url_hash` es
     el SHA-256 de `urls.url` (comprobado en producción: 0 distintas en las
     5.578 de Lopesan y en una muestra del 1 % de la tabla); lo que no case por
     hash se busca por URL.
   - **Búsquedas de marca: un solo criterio.** La primera versión decía en
     `paginas_sustitutas` que una página principal frente a una ficha es
     `false` y en la opción `dejar` que una búsqueda de marca «donde vale
     cualquier página» era el caso de dejar: o Jev contradecía sus ejemplos o
     esa rama no se leía nunca (278 de 459 búsquedas de Lopesan contienen
     «lopesan»). Ahora `paginas_sustitutas` dice que una búsqueda de marca
     (aunque esté mal escrita) frente a la home o una home de idioma (`/en/`)
     y una ficha es `false`, y `dejar` es solo «las dos ya están en las
     posiciones 1 a 3». Con las marcas declaradas, la 157 ya quita `/`; las
     homes de idioma no (su `_es_home` solo mira `/`), y queda pendiente ahí.
   - **Llamadas separadas** (`shared.decisiones._partir`): una pregunta con
     `solo_si` va siempre sola. Su instrucción da por hecho que las páginas
     compiten, y en la misma llamada podía empujar a la primera hacia el sí;
     además la evaluación (`responder`) pregunta una a una, y el umbral se
     habría medido con un prompt distinto del de producción, con una
     `clave_cache` que no distingue entre los dos. Ahora el cuerpo de la
     primera pregunta es idéntico en los dos sitios (lo comprueba un test con
     el `FakeLLM`). La estimación cuenta las dos llamadas.
   - **`Pregunta.solo_si`** (`("paginas_sustitutas", "true")`): la pantalla y
     `GET .../decisions?pregunta=` cuentan la recomendación solo donde la otra
     respuesta del mismo estado lo cumple; el resto sale como `no_aplica` (el
     coste es el de todas, que se pagaron). No se envía ni entra en la
     huella; el catálogo comprueba que apunte a una pregunta y una respuesta
     posibles. En el juego (`extraer`), la recomendación se etiqueta solo
     donde las páginas son sustitutas y lo demás se deja vacío.
   - **Coste medido en Lopesan** (24-oct-2026, solo lectura, `caracteres / 4 +
     250` de la 162): 328 pares, 656 llamadas, 777.651 tokens, **0,033 $**
     (1.185 tokens por llamada; en una sola llamada habrían sido 0,023 $). Sin
     tope, 394 pares y 0,039 $. El otro censo con avisos, `391b5ed6` (también
     de Lopesan): 332 pares y 0,033 $. Son los dos únicos de producción.
   - **`extraer` por par**: estratifica por host y COMBINACIÓN de plantillas
     del par, y quita los repetidos (la misma búsqueda con las mismas rutas en
     dos censos o con y sin www, y los estados idénticos).
   - **Sin umbral y marcado como sin evaluar**: todo sale `incierto`.
     `efecto_en_el_aviso` (rebajar el aviso si no son sustitutas, confirmarlo
     con la recomendación si lo son, como pide #169) está escrito y probado,
     pero **apagado**: solo actúa con decisiones `decidido`, que sin umbral no
     hay, y nadie lo llama hasta que un módulo pueda emitir o rebajar avisos
     (pendiente de la 85). Al encenderlo, un aviso se confirma si alguno de
     sus pares sale sustituto y se rebaja solo si TODOS se preguntaron y
     salieron que no: con un par en otro idioma, no se rebaja.
   - Queda: etiquetar el juego y medirlo (#8), el umbral, encender el efecto en
     el aviso, las homes de idioma en la regla de marca de la 157 y los
     clientes sin Search Console (pares por título o MinHash, #18).

<a id="d178"></a>
178. **El tipo de página se pregunta por plantilla, con una taxonomía general de
   once opciones, y la categoría del cliente con su propia taxonomía** (#12;
   lo esperan #22, #169 punto 7 y #198). Dos módulos nuevos, `tipo_pagina` y
   `categoria_cliente`, con su pregunta `choice` en `shared/preguntas/tipo_pagina.py`
   y la muestra, el estado y la propuesta en `shared/tipo_pagina.py`.
   - **Una muestra por plantilla, no cada URL.** La plantilla es la de
     `plantilla_de` (las reglas del cliente en `config.templates` y, si no casa
     ninguna, la forma de la ruta) con el host delante y **sin `www.`**; dentro
     de una plantilla, la misma ruta con y sin `www` es una sola muestra (la 169
     vio que contarla dos veces infla el acuerdo). Se preguntan
     `3 + log10(n)` URLs por plantilla, hasta 8, las primeras por `url_hash`
     (repetir sale de caché). El filtro es el `filtrar_urls` del módulo, así que
     estimación, ejecución y `extraer` preguntan lo mismo. Medido el
     24-oct-2026 en producción, solo lectura:

     | Censo | Indexables | Plantillas | Se preguntan | Coste estimado | URL a URL |
     |---|---|---|---|---|---|
     | Lopesan (1e7ea651) | 3.216 | 84 | 262 | 0,015 $ | ~0,19 $ |
     | Saunier Duval (40ea596f) | 1.080 | 68 | 153 | 0,009 $ | ~0,06 $ |
     | Salle (49ddb8f5) | 13.069 | 247 | 558 | 0,032 $ | ~0,76 $ |
     | Penguin (d3fda8a5) | 78.457 | 100 | 405 | ~0,024 $ | ~4,6 $ |

     El coste sale de `capa.estimar` sobre los estados reales de la muestra
     (caracteres / 4 + 250, decisión 162, a 0,042 $/Mtok): unos 1.380 tokens por
     llamada, de los que ~900 son la pregunta con sus once opciones y ~220 el
     estado (888 caracteres de media en Lopesan, 1.020 como mucho). Lo de Penguin
     es tokens medios × llamadas. URL a URL, Penguin solo se comería casi todo el
     tope global de 5 $ al mes.
   - **El estado**: lo de `pagina_legal` (título, H1 visible, 600 caracteres del
     contenido sin plantilla) más la ruta, las palabras propias, el `og:type`, si
     tiene `rel=next/prev` y los tipos de schema **que no están en todas las
     páginas** (`SCHEMA_DE_TODAS`: WebSite, Organization, BreadcrumbList,
     WebPage... están en 3.215 de las 3.216 indexables de Lopesan y no distinguen nada). En Lopesan es
     lo que separa una habitación (`HotelRoom`, `Offer`), un hotel (`Hotel`), un
     destino (`TouristDestination`), una entrada (`BlogPosting`) y una categoría
     del blog (`CollectionPage`), que comparten forma de ruta.
   - **La taxonomía**: `home`, `listado`, `ficha_producto`, `landing_comercial`,
     `articulo`, `corporativa`, `contacto_ubicacion`, `ayuda`, `legal`,
     `utilidad` y `otro`. Once, como la de jev-seo, y cada opción dice qué es,
     ejemplos y **qué no es** (en jev-seo eso subió el acuerdo de 20 a 27 sobre
     30). `legal` y `utilidad` con el criterio de `es_pagina_legal` v2 y
     `es_pagina_de_utilidad` v2 (169): lo normativo es legal y un login no es un
     error. La frontera producto/documentación, la más débil en jev-seo, va
     escrita en las dos opciones. Lo que el código ya sabe (paginación, facetas,
     búsqueda interna por la URL; un 4xx) no se pregunta: el alcance son
     indexables 200, y lo determinista de #12 queda para el analizador. Las
     claves van en castellano porque son las de los umbrales por tipo de #12 y la
     matriz de #22; las descripciones, en inglés.
   - **Una pregunta, un criterio**: «qué ES la página por su propósito», no de qué
     habla. Una entrada del blog sobre un hotel es `articulo`.
   - **La propuesta por plantilla** (`propuesta_por_plantilla`,
     `GET /api/jobs/{id}/enrichment/{run}/plantillas`): por plantilla, la
     respuesta de sus muestras, cuántas coinciden, la confianza media y a cuántas
     URLs del alcance se propondría. `mixta` si las muestras no coinciden (en
     Lopesan, las plantillas de hoteles de 7 tramos juntan la lista de
     habitaciones, la gastronomía y las ofertas de cada hotel): no se propone nada y hará falta decidirla URL a URL.
     `propagable` solo si todas coinciden y todas están `decidido`, lo que exige
     umbral; **hoy ninguna lo es** y no se escribe nada en ninguna URL. La
     pantalla del enriquecido la enseña («Ver la respuesta por plantilla»), con
     las opciones en castellano y las respuestas de un `choice` de la más
     frecuente a la menos.
   - **La taxonomía del cliente** (punto 5 de #12): `config.taxonomia`, declarada
     en `JobConfig` (hasta 20 categorías de hasta 80 caracteres, sin repetir y
     sin «ninguna», que es la respuesta para lo que no es de ninguna). Es otra
     pregunta y no las opciones de `tipo_pagina`: el tipo es el mismo en todos los
     clientes porque de él cuelgan umbrales y relevancia; la categoría es del
     negocio («Calderas», «Aerotermia»). `categoria_cliente` lleva las del job
     como opciones (`Pregunta.con_opciones`): sus opciones del catálogo son de
     muestra y nunca se envían. Sin taxonomía de dos categorías o más, el módulo
     no pregunta nada ni cuesta nada.
     - **Otras opciones, otra clave de caché.** `con_opciones` fija `variante` (la
       huella de las opciones) y `clave_cache` la añade, solo si existe: las
       claves de las preguntas de siempre no cambian y su caché sigue valiendo.
       La tabla `decisions` no guarda la variante, así que una fila solo vale de
       caché si sus probabilidades son sobre las mismas opciones: cambiar la
       taxonomía vuelve a preguntar, en vez de servir respuestas elegidas entre
       otras categorías.
     - `extraer` guarda las opciones en cada fila del juego y exige una sola
       taxonomía por juego; `responder` y `evaluar` preguntan y miden con ellas.
   - **`extraer` sin estados repetidos.** Recorre el orden estratificado de
     siempre (los `n` primeros son los mismos) y salta un estado idéntico a uno ya
     cogido, rellenando con el siguiente hasta `n`. Vale para todas las preguntas.
   - **Sin evaluar**: las dos preguntas sin umbral y los dos módulos marcados
     (`ejemplo=True`): todo sale `incierto`, nada se propaga y nada va a un
     informe. El juego se saca con
     `evaluar_decisiones.py extraer tipo_pagina --job …` y hay que etiquetarlo
     con varios clientes, con casos de la frontera producto/ayuda y de plantillas
     mixtas.
   - **Preparado y apagado hasta que haya umbral** (lo que #12 pide y depende de
     él): rellenar `urls.page_type` desde la propuesta `propagable`, los umbrales
     por tipo del analizador (`analysis_thresholds.por_tipo`) y sustituir `REGLAS`
     de `check_js_templates.py`. Ninguno existe todavía: un tipo mal puesto
     silenciaría avisos de verdad.
   - **Cómo lo usarán los demás.** #22 lee el tipo por plantilla para la matriz
     de relevancia (`esperado_por_diseño` de `low_word_count` en `legal`,
     `contacto_ubicacion` o `utilidad`, una sola fuente por caso con `por_tipo`) y
     para agrupar acciones («las fichas de producto sin H1»); la prioridad pesa
     más una `ficha_producto` o un `listado` que una `legal`. #198 excluye de la
     poda lo que salga `legal` o `contacto_ubicacion` con umbral. #169 punto 7 es
     #22 con los clics de Search Console. Ninguno debe leerlo antes de que la
     pregunta tenga umbral, o leerá respuestas inciertas como si fueran ciertas.
   - Fuera: el contexto del sitio (`tipo_negocio`, una pregunta por sitio en
     jev-seo y la menos estable entre ejecuciones), el nivel 2 por vertical
     (ficha de hotel, de habitación) y la paginación de #12, que es determinista.
