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

<a id="d176"></a>
176. **Intención de la página y de sus búsquedas de Search Console: una pregunta
   por criterio, la búsqueda sin nada de la página y el desajuste solo
   orientativo** (#39, #169 puntos 1 y 2). Dos módulos nuevos del enriquecido,
   los dos marcados como sin evaluar (163): todo sale `incierto` y nada va a un
   informe.
   - **`intencion_pagina`**: dos preguntas `choice` en la misma llamada (el
     estado se paga una vez), cada una con UN criterio (169):
     `intencion_pagina` (informational, commercial, transactional, navigational,
     local, unclear) y `etapa_embudo` (tofu, mofu, bofu, post_purchase,
     out_of_funnel, unclear), aparte y no mezcladas: una comparativa es comercial
     y MOFU, una página de precios es comercial y BOFU. Estado: ruta, título, H1
     visible, 600 caracteres de contenido y (revisión, abajo) `tipos_schema`; solo las páginas con algo que juzgar
     (título, H1 o contenido). Las opciones dicen «quién llegaría aquí», lo que
     NO son y el desempate donde se tocan (reservar un hotel es transaccional
     aunque el hotel esté en un sitio; una página de marca no lo es si se puede
     reservar en ella). `unclear` es la salida obligatoria de #9. `local` va
     dentro del choice, aunque el comentario de #39 lo dejaba aparte como
     `noul`: lleva su desempate escrito y, si al etiquetar se mezcla con la
     transaccional, saldrá a un `noul` propio.
   - **`intencion_busquedas`**: `intencion_busqueda`, con las MISMAS opciones
     que la de la página, sobre las búsquedas de `gsc_consultas` (157) que traen
     clics a cada página en la ventana del censo (90 días hasta el día en que
     empezó, ahora en `shared/gsc.py` para que la lean igual el analizador y la
     API) y de las propiedades activas del cliente (con integraciones declaradas
     y todas apagadas, ninguna): las **10 con más clics por página**. El estado
     es `{sitio, marcas, busqueda}` (el sitio sin `www.` y las `marcas` del job)
     y **nada de la página ni cifras**: la misma búsqueda en otra página u otro
     censo del cliente es el mismo estado, una llamada y después caché (84);
     otro sitio es otro estado (la caché es por cliente). Los clics se vuelven a
     leer de `gsc_consultas` al comparar. Un estado por (página, búsqueda) con
     `ambito="busqueda"` y la fila en `decisions` con su `url_id`. Depende de
     `intencion_pagina` (se añade sola). Sin cliente, sin Search Console o sin
     clics en la ventana no tiene trabajo y **lo dice**: `Modulo.por_que_vacio`
     (el mismo gancho que la categoría del cliente sin taxonomía, 178) pone el
     motivo en la estimación (`por_modulo[...].aviso` y `avisos`, en el
     diálogo) y en los errores de la ejecución.
   - **Desajuste ponderado por clics, orientativo** (`shared/intencion.py`,
     `GET /api/jobs/{id}/enrichment/{run}/intencion`): reparto de los clics de
     cada página por la intención de sus búsquedas; **no cuentan** las
     `navigational` (una búsqueda de marca puede caer en cualquier página de la
     marca) ni las `unclear`. Desalineada = la intención de la página se conoce,
     la que más clics concentra es otra y la de la página se queda con menos de
     la mitad (la definición de #39). Con el embudo con tráfico real (#169 punto
     2): clics de la página por etapa y por plantilla.
   - **`intencion_desalineada` preparada y apagada**:
     `incidencias_intencion_desalineada` devuelve una por página con las
     búsquedas que la causan, pero nada mientras `intencion_pagina` o
     `intencion_busqueda` no tengan umbral, y aun con umbral solo con respuestas
     `decidido`. Nadie la llama: el enriquecido todavía no emite incidencias (85).
   - **La pantalla**: los resultados de un choice salen con sus opciones en el
     orden del catálogo y en castellano, la unidad («búsquedas», no «URLs») y la
     búsqueda en la tabla de respuestas (solo la búsqueda, que es de Search
     Console; del estado de una página no sale nada); y una tarjeta con el
     desajuste y el embudo.
   - **`extraer` saca el juego por estado, no por URL**: un módulo puede dar
     varios estados por URL, y se quitan los repetidos (la misma página con y
     sin `www.`, la misma búsqueda en dos páginas), que inflaban la muestra
     (169). El id de un ejemplo de búsqueda es su estado.
   - **Medido** (24-oct-2026, Lopesan `1e7ea651`, en solo lectura): de 3.216
     indexables, todas con algo que juzgar y 1.921 con búsquedas con clics;
     10.121 pares página × búsqueda y **7.096 búsquedas distintas**, que cubren
     el 77,6 % de los clics con búsqueda conocida (todas serían 14.943 por el
     22 % restante, casi todo cola larga). Estimado con la fórmula de la 162:
     `intencion_pagina` 3.216 llamadas, 4,6 M tokens, **0,19 $**;
     `intencion_busquedas` 7.096 llamadas, 5,3 M tokens, **0,22 $** (el texto
     de la pregunta pesa más que la búsqueda). Un 8 % del tope mensual de 5 $;
     el censo siguiente solo paga las búsquedas y páginas nuevas. Ningún otro
     cliente tiene Search Console: la de la página costaría ~0,06 $ por cada
     1.000 páginas (Saunier Duval, 1.080; Penguin, 78.457, se pasaría del tope:
     con muestra o carpeta).
   - **Revisión antes de mergear** (la revisión independiente del PR #243):
     - *Una página de marca no se da por desalineada.* Las búsquedas
       navegacionales no cuentan para la parte alineada, así que una página
       `navigational` salía con un 0 % alineado con un solo clic de otra
       intención, y 1.000 clics de marca con 5 informacionales ya daban una
       página desalineada (en Lopesan, el 73 % de los clics son de marca).
       Ahora `desajuste` no afirma nada (`sin_afirmar`) si la página es
       `navigational` o `unclear`, ni si la intención que más clics concentra
       **contando la marca** es la marca; `intencion_dominante` se calcula así.
       La misma función alimenta la incidencia apagada. No se pone un mínimo de
       clics: sería otro umbral sin calibrar, la lista se ordena por clics con
       otra intención y cada fila lleva sus clics.
     - *Reglas en orden en vez de definiciones sueltas.* `intencion_busqueda`
       se contradecía en lo que más clics sin marca concentra: «day pass gran
       canaria» o «hoteles gran canaria» (tipo de producto + lugar, sin palabra
       de acción) no encajaba en ninguna opción, y «day pass lopesan» o un
       lugar suelto quedaban entre dos o tres. Ahora gana la primera que
       encaja: palabra de acción o precio (lista cerrada; «day pass», «spa» o
       «tickets» son productos, no acciones) → transactional; acceso a un
       sitio → local; nombra una marca u otro negocio concreto → navigational;
       tipo de producto con o sin lugar, o comparación → commercial; aprender o
       un lugar suelto → informational. Si no se sabe si un nombre es un
       negocio o un lugar, se trata como lugar.
     - *La página: la señal que el texto no da y el desempate de los
       listados.* El widget de reserva y el precio no salen en los 600
       caracteres, así que las fichas de hotel quedaban entre tres opciones.
       El estado lleva ahora **`tipos_schema`**: los tipos de schema.org que
       declara la página sin los de sitio (Organization, WebSite, WebPage,
       BreadcrumbList, Person...); en Lopesan las fichas declaran `Hotel` y
       `Offer`, los listados `TouristDestination`, el blog `BlogPosting`. Y
       reglas en orden: la home es navigational; una página de UN producto,
       servicio, hotel u oferta que el sitio vende es transactional aunque el
       texto solo la describa; un listado de varios (también los del propio
       sitio, «nuestros hoteles en X», «hoteles con day pass») es commercial;
       dirección o cómo llegar, local; lo demás de la organización (quiénes
       somos, empleo, área de empleados, legal), navigational; guías,
       noticias y destinos, informational. `etapa_embudo` recibe la misma
       advertencia sobre el texto y el BOFU deja de pedir la reserva visible.
       Las tres siguen en la versión 1: no han salido de esta rama ni se ha
       pagado ninguna respuesta (huellas nuevas en `HUELLAS`).
     - *No se afirma una ausencia sin datos.* `motivo_sin_busquedas` decía «no
       hay búsquedas entre X e Y» también cuando esos días no están
       descargados (un censo de antes de conectar Search Console, o de hace
       más de `SEARCH_CONSOLE_DETALLE_DIAS`). Ahora mira la serie `consultas`
       de `gsc_estado.detalle` (`cobertura_de_la_ventana`): sin días de la
       ventana dice que no hay datos; con parte, dice de qué días habla; y la
       tarjeta avisa (`aviso_cobertura`) cuando la ventana está descargada a
       medias (el censo `02d9278f`, ventana desde el 22-jul, con la serie
       desde el 24-jul). Los últimos `RETRASO_DIAS` no cuentan como hueco.
     - *La tarjeta enseña lo preguntado.* `intencion_frente_a_busquedas`
       recalculaba el top 10 con lo que hay hoy en `gsc_consultas`; ahora lee
       las búsquedas de `decisions.estado_enviado` de la ejecución y solo los
       clics de `gsc_consultas` (cruzados por la clave del estado con el sitio
       y las marcas con que se preguntó). Una búsqueda preguntada que ya no
       tiene clics sale con `clics` None, no pesa y se cuenta en
       `busquedas_sin_clics_hoy`.
     - *Coste re-medido* (fórmula de la 162): las reglas alargan el texto de
       las preguntas, +356 tokens por llamada en la de la página y +317 en la
       de la búsqueda; `tipos_schema` suma ~21 caracteres por página (2.836
       páginas de Lopesan declaran alguno). Lopesan: `intencion_pagina` ~5,8 M
       tokens, **0,24 $**; `intencion_busquedas` ~7,6 M tokens, **0,32 $**; en
       total 0,56 $, un 11 % del tope mensual. La de la página, ~0,075 $ por
       cada 1.000 páginas.
   - Fuera: el contraste con DataForSEO (#39 punto 3), la intención de grupo
     de búsquedas y `mejor_pagina` (comentarios de #39 y #169), los informes de
     cobertura y flujo del embudo por enlaces (#39 punto 4), las columnas en el
     explorador y el CSV (85) y, sobre todo, el juego etiquetado y los umbrales
     (#8): hasta entonces todo es orientativo.

<a id="d177"></a>
177. **Title, title genérico y marcado frente a lo visible: una pregunta por
   hallazgo, sin preguntar lo que ya dice una regla y por muestra de plantilla**
   (#16, #17). Tres módulos del enriquecido, cada uno con una pregunta y sin
   umbral (marcados como sin evaluar: todo sale `incierto` y nada va a un
   informe). Filtros y estados en `shared/coherencia.py`, preguntas en
   `shared/preguntas/coherencia.py`.
   - **`title_contenido` → `title_describe_contenido`** (score de 4 niveles que
     describen situaciones, como propuso el comentario de jev-seo en #16:
     otro asunto · solo el tema general · parcial · describe el contenido). Solo
     el asunto: ni estilo, ni longitud, ni palabras clave, ni marca. Estado: el
     de `pagina_legal` (title, primer h1 visible y 600 caracteres de
     `content_text`). **No se pregunta** lo que resuelve una regla: sin title
     (`title_missing`), el placeholder (`title_generico`, decisión 121) y los
     duplicados exactos (`title_duplicate`: ese title hay que cambiarlo igual);
     ni lo que no tiene contenido con el que comparar (menos de 50 palabras,
     el mismo corte que el soft 404), ni el title que es **solo la marca**
     («Saunier Duval» en /buscador/): «solo el tema general» por
     construcción. Del resto, 100 por plantilla.
   - **`title_generico` → `title_es_generico`** (noul). Se juzga con el
     **title, el dominio y la ruta**, como en un resultado; sin el contenido:
     si lo describe es la pregunta anterior, y juntarlas era mezclar dos
     criterios (169). La ruta entró en la revisión del PR: solo con el title la
     definición no separaba los casos reales. «SAT» es correcto en la página
     del servicio (/sat/) y genérico en las 67 fichas de ciudad de Saunier
     Duval (/sats/a-coruna-100), y «SafiraAqua», «LineAqua» (productos de
     Saunier Duval) o «Culpa», «Lar», «Lobos» (libros de Penguin) parecían un
     código hasta que la ruta dice qué son. Las instrucciones dan esos ejemplos
     de los dos lados; con una ruta opaca (ids o parámetros) se juzga el title
     solo, y un nombre propio o una sigla que podría nombrar la página es
     `false` (lo que no se puede afirmar no se afirma). Solo titles de **4
     palabras o menos sin la marca** (la marca, como la regla: el segmento que
     está en más de la mitad de los titles del host), sin la portada (su title
     con la marca sola es «qué dice la portada», #199), sin los placeholders de
     la regla y **sin los que son solo la marca** («Saunier Duval», «| Penguin
     Libros ES»): genéricos por definición, no se preguntan; el día que haya
     incidencia, esos salen por regla. Con dos segmentos distintos que pasan
     los dos por marca («Detalle | Acme» cuando casi todo el sitio se titula
     así) no se sabe cuál es la marca y sí se pregunta. Los duplicados exactos
     entran (que 40 páginas digan «SAT» lo dice `title_duplicate`, no si «SAT»
     dice de qué va), pero como el estado lleva la ruta cada URL es una
     llamada: por plantilla, 50 titles distintos y **3 URLs de cada uno**. En
     producción salen «Detalle noticia» (Lopesan) o «SAT» (Saunier Duval).
   - **`schema_visible` → `schema_contradice_contenido`** (noul). Solo la
     **contradicción**: el texto guardado es el contenido principal, no la
     página (el HTML no se guarda; el precio del lateral no está), así que que un
     valor no aparezca no se puede afirmar y la pregunta lo dice. Estado: title,
     h1, los valores verificables del marcado como «Tipo.campo: valor» (precio,
     moneda, rango de precios, valoración y número de opiniones, disponibilidad,
     fechas de un evento y validez de una oferta; JSON-LD y microdatos, nunca el
     bloque entero; una lista de más de 3 valores es un listado y no entra) y
     hasta 1.000 caracteres de `content_text` alrededor de sus números y de
     cualquier precio, valoración, fecha o disponibilidad escrita, repartidos
     por prioridad: primero las ventanas de un valor declarado, después las
     genéricas, cada una entera o recortada alrededor de su coincidencia, nunca
     un corte al final (la revisión del PR vio que ese corte se comía el
     precio declarado cuando salía detrás de otros, y Jev habría visto
     `Offer.price: 178` frente a «99 / 120 / 135 / 150»: una contradicción
     fabricada por el estado; en las 2.315 páginas reales con un valor
     declarado escrito, ninguna se queda sin ninguno y 3 pierden alguno por
     sitio, frente a 5 antes). **Sin nada
     visible con lo que comparar, no se pregunta.** Ni autor ni fecha de
     publicación: el extractor quita la firma, y lo único que se encontraría es
     el mismo nombre, nunca uno distinto. Un `@graph` son varios bloques (el
     JSON-LD de La Salle va así); el RDFa de producción es FOAF/SIOC de Drupal,
     sin estos campos. 10 por plantilla: el marcado lo pinta la
     plantilla. Ya en los datos: una habitación de Lopesan con
     `Offer.price: 354USD$` y `priceCurrency: EUR`.
   - **Medido el 24-oct-2026** en producción, solo lectura, con el estimador
     (`caracteres / 4 + 250`, decisión 162; 0,042 $/Mtok):

     | Censo (indexables) | title_contenido | title_generico | schema_visible |
     |---|---|---|---|
     | Lopesan 1e7ea651 (3.216) | 1.661 URLs, 0,050 $ | 101 URLs y llamadas, 0,0035 $ | 151, 0,005 $ |
     | Saunier Duval 40ea596f (1.080) | 240, 0,007 $ | 366 URLs, 205 llamadas, 0,007 $ | 0 (sin valores verificables) |
     | Penguin d3fda8a5 (78.457) | 3.695, 0,11 $ | 4.694 URLs, 2.661 llamadas, 0,093 $ | ~900, ~0,03 $ (465 y 0,014 $ en 1/32 de las URLs) |
     | La Salle 49ddb8f5 (13.069) | 818, 0,024 $ | 894 URLs, 845 llamadas, 0,029 $ | 10 de 26 comparables (una plantilla), 0,0003 $ |

     El genérico es tras la revisión (ruta en el estado, 3 URLs por title):
     antes, sin ruta y con todas las URLs de cada title, costaba 0,002 $,
     0,004 $, 0,067 $ y 0,016 $. Llamadas por debajo de URLs: la misma página
     con y sin www es el mismo estado.

     Sin la muestra por plantilla, la alineación costaba 0,093 $ en Lopesan y
     0,83 $ en Penguin, y el genérico de Penguin 0,55 $ (todas sus fichas de
     libro tienen 2-4 palabras). Cada llamada de alineación son ~720 tokens:
     las instrucciones y los cuatro niveles pesan más que el estado.
   - **`extraer` dice cuántos estados repetidos saltó**: el juego ya sale sin
     estados repetidos (la misma página con y sin www cuenta una vez, 169 y
     176); ahora la salida lo cuenta (`informe["estados_repetidos"]`).
   - **La pantalla pinta un score por nivel**: Jev devuelve un número continuo
     (2,62) y `por_respuesta` sacaba una entrada por decimal. Ahora se agrupa por
     el nivel más cercano (`etiqueta_de_respuesta`, como `evaluar`); en
     `_resultados`, las `opciones` de un score son sus niveles («0»...«3»), así
     que se pintan en orden con el mismo mecanismo que un choice (176), y
     `etiquetas` trae el nombre corto de cada nivel (`Pregunta.etiquetas`, que
     no se envía ni entra en la huella; lo demás sale del diccionario único de
     la pantalla). Pinchar un nivel filtra sus filas por rango y la tabla
     enseña nivel y número.
   - **Las incidencias de #16/#17 quedan apagadas** (`title_desalineado`,
     `title_generico` con modelo, `schema_valor_no_visible`): dependen de un
     umbral que estas preguntas no tienen. Para ponérselo, `extraer`, etiquetar
     a ciegas con el criterio de cada pregunta y `evaluar` (159), en censos que
     no se hayan usado para redactarlas.

<a id="d178"></a>
178. **El tipo de página se pregunta por plantilla, con una taxonomía general de
   doce opciones, y la categoría del cliente con su propia taxonomía** (#12;
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
     | Lopesan (1e7ea651) | 3.216 | 84 | 262 | ~0,019 $ | ~0,24 $ |
     | Saunier Duval (40ea596f) | 1.080 | 68 | 153 | ~0,011 $ | ~0,07 $ |
     | Salle (49ddb8f5) | 13.069 | 247 | 558 | ~0,039 $ | ~0,93 $ |
     | Penguin (d3fda8a5) | 78.457 | 100 | 405 | ~0,029 $ | ~5,6 $ |

     El coste salió de `capa.estimar` sobre los estados reales de la muestra
     (caracteres / 4 + 250, decisión 162, a 0,042 $/Mtok) con la primera
     redacción, unos 1.380 tokens por llamada; la de doce opciones con sus
     casos frontera (abajo) añade 323 tokens de pregunta por llamada (~1.200 en
     total) y el estado sin claves vacías quita unos 16 en Salle, así que ahora
     son ~1.690-1.730. El estado son ~220 tokens (888 caracteres de media en
     Lopesan, 1.020 como mucho). URL a URL, Penguin solo se pasaría del tope
     global de 5 $ al mes.
   - **El estado**: lo de `pagina_legal` (título, H1 visible, 600 caracteres del
     contenido sin plantilla) más la ruta y, **solo si los hay**, las palabras
     propias, el `og:type`, si tiene `rel=next/prev` y los tipos de schema **que
     no están en todas las páginas** (`SCHEMA_DE_TODAS`: WebSite, Organization, BreadcrumbList,
     WebPage... están en 3.215 de las 3.216 indexables de Lopesan y no distinguen nada). En Lopesan es
     lo que separa una habitación (`HotelRoom`, `Offer`), un hotel (`Hotel`), un
     destino (`TouristDestination`), una entrada (`BlogPosting`) y una categoría
     del blog (`CollectionPage`), que comparten forma de ruta. Lo vacío no se
     manda: `content_word_count` es NULL en las 13.069 indexables de Salle y en
     las 78.457 de Penguin, Salle y Saunier no tienen schema y Salle no tiene
     `og:type`; allí el estado se queda en ruta, título, H1 y contenido, y la
     instrucción dice «when present». En Salle el contenido aún empieza por el
     menú («Pasar al contenido principal...»): es su `custom_boilerplate_selectors`
     lo que falta, no este módulo, y la instrucción pide ignorar el menú.
   - **La taxonomía**: `home`, `listado`, `ficha_producto`, `landing_comercial`,
     `articulo`, `perfil`, `corporativa`, `contacto_ubicacion`, `ayuda`, `legal`,
     `utilidad` y `otro`. Las once de jev-seo más `perfil`, y cada opción dice
     qué es, ejemplos y **qué no es** (en jev-seo eso subió el acuerdo de 20 a 27 sobre
     30). `legal` y `utilidad` con el criterio de `es_pagina_legal` v2 y
     `es_pagina_de_utilidad` v2 (169): lo normativo es legal y un login no es un
     error. La frontera producto/documentación, la más débil en jev-seo, va
     escrita en las dos opciones. Lo que el código ya sabe (paginación, facetas,
     búsqueda interna por la URL; un 4xx) no se pregunta: el alcance son
     indexables 200, y lo determinista de #12 queda para el analizador. Las
     claves van en castellano porque son las de los umbrales por tipo de #12 y la
     matriz de #22; las descripciones, en inglés.
   - **Los casos frontera de producción, cada uno en una sola opción**
     (`CASOS_FRONTERA`). Una revisión independiente etiquetó a ciegas páginas
     reales con la primera redacción y cada una admitía dos o tres opciones, la
     lección de la 169. Ahora cada caso lo reclama la parte afirmativa (antes
     del primer «Not») de exactamente una opción, y `test_tipo_pagina.py` lo
     comprueba:
     - **Autores de Penguin** (`/es/<id>-<slug>`, bio y libros, `og:type`
       website): 18.955 de las 78.457 indexables, y 18.955 de las 18.958 de la
       plantilla `/:idioma/:slug`, así que sus 8 muestras deciden la plantilla
       entera. No tenían opción (`listado` excluía «una página de un solo
       elemento que enlaza a otros»): van a **`perfil`**, también el autor con
       «0 Títulos». Lo mismo las 1.141 fichas del directorio de Salle
       (`/es/la-salle/directorio/*`: cargo, email, edificio), que caían entre
       `contacto_ubicacion`, `corporativa` y `otro`.
     - **Páginas de marca de Lopesan** (`/es/abora-hotels`, «Cuatro hoteles
       Abora...», schema Hotel+Offer): `corporativa` decía «the brands of the
       group». Una página de marca que presenta y lista sus hoteles o productos
       es **`listado`**; `corporativa` y `landing_comercial` la excluyen.
     - **Asignaturas de Salle** (`/es/bases-de-datos-9`, «El objetivo de la
       asignatura...»): **`ficha_producto`**, como parte de la titulación (igual
       que el spa de un hotel); `articulo` la excluye.
     - **El buscador de subvenciones de Saunier** y su página «Subvenciones y
       ayudas» para el usuario: **`ayuda`** (herramientas que ayudan a elegir o
       calcular, e información de ayudas que el cliente puede pedir); `legal`
       se queda con las subvenciones que **recibió la empresa** y excluye las
       otras, y `utilidad` excluye calculadoras y buscadores.

     No es una evaluación: el juego etiquetado (#8) dirá si Jev sigue estas
     fronteras. Pero sin ellas, etiquetar el juego repetía el desacuerdo
     sistemático de la 169.
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
   - **La taxonomía del cliente** (punto 5 de #12): `taxonomia`, declarada en
     `JobConfig` (hasta 20 categorías de hasta 80 caracteres, sin repetir y sin
     «ninguna», que es la respuesta para lo que no es de ninguna). Cada categoría
     es un nombre o `{"nombre", "descripcion"}` (hasta 400 caracteres, qué entra
     y qué no): la descripción es lo que el modelo lee para decidir, y sin ella
     solo tiene el nombre.
     - **Se lee del cliente HOY, no del job** (`taxonomia_de`): la de
       `clients.config_defecto` y, si el cliente no tiene, la de `jobs.config`.
       La taxonomía es del negocio, no del rastreo; `jobs.config` se congela al
       lanzar (el PATCH del job no la toca) y leer solo de ahí obligaba a
       volver a rastrear el sitio entero para preguntar por un censo ya hecho.
       Ningún cliente la tiene todavía: se pone en su ficha y vale para sus
       censos de antes.
     - **Sin taxonomía lo dice.** `Modulo.por_que_vacio` explica por qué un
       módulo no preguntará nada: la estimación lo devuelve en `avisos` (y en
       su `por_modulo`), el diálogo lo pinta, y la ejecución lo deja en
       `errores`. Antes salían 0 URLs y 0 llamadas sin explicación.
     - **Un criterio: de qué trata.** La instrucción decide solo por el tema,
       sea cual sea el tipo de página: una página de contacto o de servicio
       técnico de una sola categoría es de esa categoría. La primera redacción
       mandaba a «ninguna» la portada, lo legal, el contacto y lo corporativo,
       un juicio de tipo que ya hace `tipo_pagina`, y una misma página tenía
       dos respuestas defendibles. «ninguna» queda para lo que no trata de
       ninguna categoría o trata de varias por igual. Es otra
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

<a id="d179"></a>
179. **Una respuesta rara se mide con un juego de precisión: todas las veces que
   Jev la da en un conjunto amplio, más un control aleatorio; su precisión sí,
   su recall no** (#8). En la medida del 24-oct-2026 (juegos aleatorios de
   200, etiqueta de Claude a ciegas) la respuesta que importa es rara:
   `paginas_sustitutas` 9 «sí» reales de 200, `schema_contradice_contenido` 6 y
   `title_es_generico` 17, y Jev contesta «sí» 7, 3 y 8 veces. Aunque acierte
   todas, la cota de Wilson del «sí» se queda en 0,65 (7 de 7), 0,44 (3 de 3) y
   0,41 (6 de 8), y `avisos` lo marca: con un juego aleatorio no hay positivos
   bastantes para calibrar el «sí» (hacen falta ~35 aciertos seguidos para una
   cota de 0,9). Etiquetar 2.000 al azar para tener 70 «sí» no tiene sentido.
   - **`evaluar_decisiones.py precision <conjunto> <respuestas> --respuesta X`**
     saca, de un conjunto amplio ya respondido, **todas** las filas donde Jev
     contestó X (o, con `--positivos N`, las N de más confianza, con los empates
     del corte dentro) y un **control** uniforme de todo el conjunto
     (`--control`, 100 por defecto; puede caer en un positivo, que queda
     `en_control`). Cada fila lleva su `origen` (`positivo_jev` | `control`),
     `en_control` y el resumen (`juego_precision`: respuesta, tamaño del
     conjunto, cuántas X había, cuántas entran, corte de confianza, semilla).
     Barajadas y sin llamar a nadie. No se saca de otro juego de precisión, ni
     de un conjunto donde Jev no contestó X ninguna vez (solo traería el control).
   - **`evaluar` lo reconoce por el `origen`**: la precisión de X y su cota se
     calculan **sobre los positivos**, y como todas las X de Jev del conjunto
     están en el juego (o todas las de confianza ≥ el corte), la precisión es la
     de verdad para ese conjunto; la curva y el **umbral propio** de X salen de
     ellos con el mismo criterio de la 159 (Wilson ≥ objetivo con 30 o más por
     encima). **El recall no se afirma** (`recall: null` y por qué): los X reales
     que Jev contestó con otra cosa no están entre los positivos. Tampoco el
     acierto, la ECE ni el umbral común: los positivos están
     sobrerrepresentados y saldrían sesgados (`umbral: null`; el común se pone
     con un juego aleatorio). Lo único que estima el recall es el **control**,
     y se da aparte con su tamaño (`control.recall_x`, su cota y cuántos X
     reales trae; con menos de 10, `avisos` dice que no dice nada). Avisa
     también si faltan positivos por etiquetar (la precisión ya no es la de
     todas) y si el juego es de las N de más confianza. **Con otras respuestas
     que las de las que salió el juego no evalúa**: lo nota si a alguna fila le
     falta respuesta (otro fichero, otra versión), si un positivo ya no dice X
     o si una fila de control dice X y el juego la habría metido como positivo
     (con todas las X, cualquiera; con `--positivos N`, con confianza ≥ el
     corte). Con otra pasada la precisión saldría de un subconjunto de las X.
   - **Etiquetar a ciegas.** El `origen` delata la respuesta de Jev: quien
     etiqueta ve id y estado, no el fichero entero (el procedimiento, en
     `docs/referencia/operacion.md`).
   - **Tamaño y coste** con lo medido (frecuencia del «sí» de Jev en el juego
     de 200 y coste medio por fila de `r_<pregunta>_v1.jsonl`): un conjunto de
     2.000 daría ~70 «sí» en `paginas_sustitutas` (0,10 $), ~80 en
     `title_es_generico` (0,07 $) y ~30 en `schema_contradice_contenido`
     (0,06 $). El marcado no llega con los censos de hoy: entre los cuatro de
     la 177 hay unas 1.060 llamadas posibles, ~16 «sí» esperados; hace falta
     más clientes con valores verificables. El título genérico tiene además
     precisión observada 0,75 (6 de 8; los dos fallos eran `[duda: true]` en la
     etiqueta): con ella no llega aunque haya positivos de sobra, y el juego lo
     dirá.
   - **Lo que no resuelve**: el recall del «sí» (`title_es_generico` ve 6 de 17)
     sigue necesitando juegos aleatorios grandes o un juego de recall aparte
     (sacar lo que una regla barata marca como candidato y Jev contesta «no»).
     Y no pone umbral a nada: las tres preguntas siguen sin umbral hasta medir
     con estos juegos.
<a id="d180"></a>
180. **`intencion_pagina` v2 y `title_describe_contenido` v2: navegacional solo
   para la portada, dos opciones para lo que no es de búsqueda, y el title que
   nombra la página es el nivel alto aunque sea corto** (#8, #16, #39; mide lo
   que dejaron la 176 y la 177). Las dos preguntas suben de versión (huellas en
   `HUELLAS`) y siguen **sin umbral**: todo sale `incierto`.
   - **Medido el 24-oct-2026** (producción, solo lectura; juegos de 200 de la
     159 en `/data/archivo/jev/`, etiqueta de Claude a ciegas con nota y
     `[duda: x]`, respuestas de Jev de ese día; `evaluar` en un contenedor
     efímero). Ningún estado repetido.
     - `intencion_pagina` v1: **76,5 %**. Casi todo el fallo es
       `navigational`: 88 reales y Jev acierta 50 (recall 0,57), con 29
       `informational` y 7 `transactional`; su precisión es 0,98, así que
       cuando lo dice acierta: el problema es la definición, no el modelo. La
       otra cara es `informational`: 65 respuestas para 33 reales (precisión
       0,49). Del resto, `transactional` 58 de 61 y `commercial` 13 de 16.
       Saldría un umbral común de 0,84, que **no se pone**: la etiqueta con la
       que se calcula es la de una definición que esta decisión cambia.
     - `title_describe_contenido` v1: **78,5 %**. 192 de las 200 etiquetas son
       «describe el contenido» y Jev las baja a 2 (29) o a 1 (11), varias con
       confianza 0,00-0,1. El juego casi no tiene negativos (7 de nivel 2, 1 de
       nivel 0): mide bien si Jev reconoce un buen title, no si detecta uno malo.
   - **Por qué `navigational` fallaba.** La regla 5 de la v1 («cualquier otra
     página sobre la organización») metía en la misma opción la portada, lo
     corporativo (ESG, privacidad, sala de prensa, directorio, proyectos de
     investigación, documentación de calidad) y las páginas para quien **ya**
     es cliente, alumno o miembro (normativa académica, horarios, biblioteca,
     tutorías, clubes, login, confirmación de un formulario). La intención de
     una *búsqueda* navegacional es clara (nombra la marca); la de una
     *página* no, y Jev, con razón, veía «informational» en una normativa o en
     un horario. Revisadas a mano las **47 discrepancias**: 38 eran
     `navigational` de la etiqueta; con la v2 serían 16 `existing_users`, 11
     `corporate`, 5 `commercial` («por qué estudiar en La Salle», un portfolio),
     4 `transactional` (un programa o una actividad concretos a los que
     apuntarse), 1 `informational` y 1 `unclear` (el contenido es el menú).
     Las otras 9 conservan su opción y 6 de ellas tienen ahora la frontera
     escrita: las selecciones de libros en venta (comercial, Jev decía
     informacional), el localizador de servicios técnicos (local), las sesiones
     informativas a las que apuntarse (transaccional) y la financiación de los
     estudios (informacional); la oficina con ubicación y horario (local) Jev
     la llamó navegacional, que la v2 ya no admite fuera de la portada. Quedan
     2 que la redacción no cambia: «UOC Corporate» (varias soluciones,
     comercial; Jev transaccional) y las villas de Lopesan sin schema (una,
     transaccional; Jev comercial con 0,96).
   - **`intencion_pagina` v2**: ocho opciones con reglas en orden («la primera
     que encaje», como la 176), cada una con lo que NO es:
     1. `navigational`: **solo** la portada o una raíz de idioma o país, «and
        nothing else is».
     2. `existing_users` (nueva): para quien ya es cliente, alumno, huésped,
        miembro o empleado, o acaba de enviar un formulario: login, área
        privada, gestionar una reserva, garantías y soporte, manuales y
        documentación para quien tiene o instala el producto, normativa
        académica, horarios, biblioteca, tutorías, mentoría y bolsa de trabajo
        de alumnos, clubes, alumni, movilidad de alumnos matriculados, gracias y
        confirmación. Va antes que la transaccional y la comercial: la lista de
        servicios al estudiante es de esta opción, no un listado comercial.
     3. `transactional`: UN producto, servicio, hotel, villa, habitación,
        programa, curso, entrada, evento, sesión informativa, visita u oferta
        que el sitio vende, reserva o deja solicitar o apuntarse, también «por
        qué elegir nuestro <programa>» y el **primer** alta en un programa de
        fidelización, área privada o suscripción (entrar en la cuenta que ya se
        tiene es la 2). Excluye por escrito los documentos oficiales, de
        calidad o de acreditación de UN programa (la 6) y la financiación y
        las becas (la 7): con «la primera que encaje» y el title igual al
        nombre del máster, la regla 3 se los quedaba antes de llegar a ellas
        (en la v1 Jev ya les daba 0,30-0,48 de transaccional). «Por qué elegir
        nuestro <programa>» es de aquí solo si el contenido presenta ese
        programa.
     4. `commercial`: varios para elegir, también la selección de varios
        productos en venta escrita como artículo (los «libros sobre el 23F» de
        Penguin, `BlogPosting` + `Offer`), «por qué elegirnos» para un área
        amplia y no para un programa (`'why study at <university>'`, `'why
        choose <firm> for <a whole field>'`) y los casos de éxito.
     5. `local`: dirección, cómo llegar, horario de un sitio físico o un
        localizador de tiendas o técnicos (la página de contacto general, no).
     6. `corporate` (nueva): la organización en sí y no lo que vende: quiénes
        somos, valores, gobierno, sostenibilidad, cumplimiento y alertas de
        seguridad de producto, documentos de calidad o acreditación (también
        los de un solo programa o producto),
        subvenciones recibidas, textos legales, empleo, prensa, redes sociales,
        contacto general, directorio y fichas de personal, sus escuelas,
        institutos, grupos y proyectos de investigación, y convenios.
     7. `informational`: guías, noticias, artículos, FAQ, destinos y cómo
        funciona la admisión, las becas o la financiación para quien llega.
   - **La búsqueda no cambia** (`intencion_busqueda` sigue en v1 con
     `INTENCIONES`): una búsqueda no es «de usuarios existentes» ni
     «corporativa» (se reparten entre marca e informacional). La página tiene
     `INTENCIONES_PAGINA` = las de la búsqueda + `SOLO_PAGINA`, y el test
     comprueba que la diferencia son exactamente esas dos. El **desajuste no
     se afirma** de una página con una de ellas (`sin_afirmar =
     "pagina_sin_equivalente"`, con su texto en la pantalla): ninguna búsqueda
     puede casar con ella y saldría siempre un 0 % alineado, el mismo
     razonamiento que la página de marca de la 176. La pantalla traduce las dos
     opciones (`RESPUESTA_ENRIQ`) y un test lo comprueba; `navigational`, que
     comparten página y búsqueda, se rotula «navegacional (portada o marca)».
   - **`title_describe_contenido` v2**: primero «qué es la página» (qué ítem
     presenta, qué sección o listado es, de qué tema trata), avisando de que
     el H1 puede ser un eslogan y el fragmento un menú, los datos de un
     producto (editorial, precio, «pagos 100 % seguros»), la biografía del
     autor, una cita o el primer elemento de un listado, que son partes de la
     página y no otro asunto; después, solo si el title lo nombra. Con **dos
     excepciones** escritas, porque sin ellas esa indulgencia se comía los
     únicos negativos del juego (el estado del title no lleva la ruta, así que
     el modelo no ve `/gestion-de-la-calidad/`): una lista de documentos o
     informes SOBRE el ítem que nombra el title, cada uno con «View» o
     «Download» y su fecha, es la página de sus documentos (nivel 2), y un
     title que nombra un evento, una oferta o una promoción que ni el H1 ni el
     fragmento mencionan mientras describen otra cosa promete lo que la
     página no enseña (nivel 0). Por eso «fecha» ya no está entre los
     metadatos que se perdonan. La escala:
     0 otro asunto o engaña · 1 genérico (solo la marca o una etiqueta que
     valdría para muchas páginas) · 2 algo relacionado pero no exactamente esta
     página (el ítem padre cuando la página es una parte de él: sus
     documentos, sus habitaciones, sus opiniones) · 3 nombra el ítem, la
     sección, el listado o el tema, **aunque sea corto, un nombre propio o una
     pregunta**. Revisadas a mano las **43 discrepancias**: 22 fichas de libro
     de Penguin (title = el libro; el fragmento empieza por la editorial y la
     biografía del autor), 6 hubs de destino de Lopesan (title «Hoteles en X»,
     H1 eslogan), 4 «¿Por qué estudiar…?», un listado de noticias cuyo H1 es la
     primera noticia, un aviso legal con H1 «Responsabilidad de los usuarios»,
     un login, portadas y secciones de La Salle: con la v2, todas al 3 por
     regla escrita. Las 3 de la otra dirección (etiqueta 2, Jev 3: el title
     nombra el máster y la página es su documentación de calidad) son el
     ejemplo del nivel 2. Queda una dudosa: «International» en el listado de
     proyectos internacionales, que tanto es la sección como una etiqueta
     genérica.
   - **Los ejemplos son genéricos**, con marcadores (`'<book title> |
     <publisher>'`, `'Hotels in <island>'`, `'/mx/'`) y no los del juego: con
     «El oro de los tigres» en la instrucción, medir sobre el juego que lo
     contiene inflaría el acierto (la lección de la 169). Un test lo vigila en
     la del title y en la de la intención («Management», «La Salle», «UOC»…:
     la primera redacción de la regla 4 copiaba casi una fila del juego).
   - **Las etiquetas del juego v1 no valen para la v2.** En la intención
     cambian las opciones (dos nuevas y `navigational` reducida a la
     portada); en el title cambia lo que significa cada nivel. `evaluar` ya
     rechaza un juego de otra versión; para re-etiquetar está
     `evaluar_decisiones.py reetiquetar <juego> --salida <juego_v2>`
     (`para_reetiquetar`): los mismos estados e ids con la versión del catálogo
     y **sin etiqueta, autor ni nota**, para etiquetar a ciegas sin ver la de
     la v1. Probado sobre los dos juegos del 24-oct (200 filas cada uno, en
     local). Siguiente paso: re-etiquetar a ciegas esos dos juegos, `responder
     --confirmar` (~0,01 $ cada uno) y `evaluar`. Y como las dos v2 se
     redactaron mirando estos juegos, para el **umbral** hace falta además un
     juego nuevo de censos no usados aquí (169), y en el title, con titles
     malos de verdad (los `title_es_generico` positivos y titles de plantilla
     sin rellenar), que este juego no tiene.
   - **Coste** (fórmula de la 162, caracteres / 4): la v2 de la intención añade
     2.965 caracteres de pregunta (6.076 frente a 3.111, con las exclusiones
     de la regla 3 de la revisión), unos 740 tokens por llamada: en Lopesan
     (3.216 llamadas) ~2,4 M tokens y **~0,10 $** más por censo, de 0,24 a
     ~0,34 $. La del title, 1.319 caracteres (~330 tokens, con sus dos
     excepciones): en Penguin (3.695 llamadas) ~0,05 $ más. Las opciones se recortaron para no
     repetir las listas de las reglas: la primera redacción pesaba 6.869.
   - Fuera: `etapa_embudo` y `intencion_busqueda` (sin medir con este juego;
     siguen en v1), añadir la ruta al estado del title (habría resuelto las
     fichas de libro sin tocar la escala, pero cambia el estado y la caché de
     todos los títulos), y que la pantalla nombre los niveles de un score con
     los de la versión de hoy aunque las decisiones guardadas sean de la v1.

<a id="d181"></a>
181. **`tipo_pagina` v2: «qué hace la página», no «de qué ítem habla», con las
   fronteras que fallaron en la v1 medida** (#8, #12; mide lo que dejó la
   178). La pregunta sube a la versión 2 (huella en `HUELLAS`) y sigue **sin
   umbral**: todo sale `incierto` y ninguna plantilla es `propagable`.
   - **Medido el 24-oct-2026**: 300 páginas de Lopesan, Penguin, Saunier
     Duval, La Salle y Workoholics, etiquetadas a ciegas, con las respuestas
     de Jev a la v1: 222/300 en la primera pasada.
   - **Cómo se eligió la segunda pasada, y hacia dónde empuja.** Se volvieron
     a etiquetar 40 páginas: 30 de los 78 desacuerdos con Jev y 10 de los 222
     acuerdos, a ciegas (sin ver la respuesta de Jev ni la etiqueta anterior).
     Cambiaron 9 etiquetas, **7 hacia lo que respondía Jev** y 2 a una tercera
     opción: 229/300. Ser a ciegas no quita el sesgo: re-etiquetar sobre todo
     desacuerdos solo puede corregir errores de etiqueta donde Jev discrepa,
     así que la muestra empuja hacia Jev.
   - **Etiquetas que se contradecían, unificadas antes de redactar.** La
     revisión de la PR encontró plantillas idénticas con etiquetas distintas
     dentro del mismo juego, y la primera redacción de la v2 tomaba a veces el
     lado minoritario: si Jev la obedecía, rompía páginas que la v1 acertaba.
     Se unifica la etiqueta de cada plantilla (juego `juego_etiquetado_v3`, que
     no está en el repo) y se vuelve a contar: 232/300. Lo unificado, y por
     qué:
     - **Tablas de convenios** (5: 3 `listado`, 2 `corporativa`) → `listado`:
       una tabla de universidades socias es una lista; la v1 las acertaba las 5
       como `listado`. Antes la v2 las mandaba a `corporativa`: ganaba 2 y
       perdía 3.
     - **Hub de normativa académica** (3: 1 `listado`, 2 `legal`) → `listado`:
       enlaza a una página por normativa, que sí es `legal`; `legal` se queda
       la lista de documentos para descargar (la documentación oficial de un
       programa). Aquí se elige la etiqueta minoritaria, por coherencia con el
       resto de hubs; la v1 acertaba 2 de 3 con ella.
     - **Grupos de investigación propios** (5: 4 `corporativa`, 1 `perfil`) →
       `corporativa`. La página en inglés que la v1 «acertaba» como `perfil`
       pasa a fallo: no es una pérdida de la v2, era ruido de etiqueta.
     - **Prácticas en empresas** (2: 1 `ayuda`, 1 `corporativa`) →
       `corporativa`, que ahora las reclama en su parte afirmativa; `ayuda`
       deja de reclamarlas.
     - **«Por qué estudiar»** (10: 6 `landing_comercial`, 2 `corporativa`, 2
       `ficha_producto`): el de un área entera o de la organización →
       `landing_comercial` (las 2 `corporativa` eran la misma plantilla que
       otras `landing`); el de UN programa (dirección de proyectos, ca y en,
       que la v1 acertaba) sigue siendo `ficha_producto` y ahora lo dice su
       parte afirmativa.
     - **Un proyecto de I+D propio** (2: 1 `articulo`, 1 `corporativa`) →
       `articulo`, como la v1 («proyecto de portfolio»); `corporativa` se queda
       los grupos y las líneas de investigación.
     **Cuatro de las seis unificaciones van hacia lo que respondía Jev**
     (convenios, normativa, prácticas y el proyecto de I+D, donde Jev dijo
     `articulo` en los dos y el grupo estaba 1-1); solo los grupos de
     investigación y los «por qué estudiar» no. Medido página a página, de
     `v2` a `v3`: 9 cambios, **5 hacia Jev, 2 en contra** (el grupo en inglés,
     `perfil` → `corporativa`, y la normativa en catalán, `legal` → `listado`)
     y 2 neutros (los dos «por qué estudiar», que Jev no daba como
     `landing_comercial`); saldo +3 (229 → 232). Es una elección con riesgo
     de complacencia, y por eso se dice.
   - **Segunda revisión de la PR: tres etiquetas más** (juego final
     `juego_etiquetado_v4`, tampoco en el repo). El laboratorio IASlab y el
     instituto Ioticat abren con el aviso de cofinanciación FEDER y estaban
     como `legal`; la v1 decía que una página que abre con ese aviso es
     `legal`, y eso choca con «clasifica por lo que ES»: un laboratorio o
     instituto propio es `corporativa` aunque empiece por el aviso, y `legal`
     se queda la página cuyo propósito es el aviso de la ayuda recibida. Los
     dos pasan a `corporativa` (Jev dijo `articulo`: siguen siendo fallo,
     neutros). El hub de información académica de máster (reparte a
     normativa y calendarios) estaba como `ayuda` contra la regla de listado
     y pasa a `listado` (hacia Jev, +1). **Acierto de la v1 con el juego
     final: 233/300 (77,7 %).** En total, de la primera pasada al juego final
     cambiaron 21 etiquetas: 13 hacia Jev, 2 en contra y 6 neutras. La cifra
     de la v1 en este juego es, por tanto, optimista.
   - **Lo que falla la v1 con las etiquetas unificadas.** `ficha_producto`
     tiene recall 1,0 y `articulo` 0,96 (23/24); el fallo está en lo que se
     les cuela y en las opciones que casi nunca salen:
     1. **`ficha_producto` de más** (115 predichas para 88 reales):
        «por qué estudiar <área>», un paquete, una experiencia en varios
        hoteles o una charla de captación (`landing_comercial`, 8); financiación, becas, intercambio y
        clases de apoyo para el alumno (`ayuda`, 7); instalaciones, servicios
        de emprendimiento y la incubadora propios (`corporativa`, 5); memorias
        e informes de acreditación de una titulación (`legal`, 4). La página
        **nombra** un programa y Jev la tomaba por su ficha.
     2. **`landing_comercial` casi nunca** (5 de 24): «por qué estudiar» de un
        área (4 más como `articulo`), captación (sesiones informativas,
        jornadas de puertas abiertas, concursos: 4 como `listado`), campañas de
        marca y la presentación comercial a un tipo de cliente (3 como
        `corporativa`).
     3. **`legal` se escapa** (18 de 25): documentación de calidad y
        acreditación, el sistema de calidad publicado por obligación legal y
        las memorias de sostenibilidad.
     4. **`ayuda` frente a `listado`** (5): hubs de horarios, catálogos de
        biblioteca y portales de búsqueda, la página que reúne las herramientas
        para profesionales; y al revés (3): hubs casi vacíos de servicios para
        doctorandos y el del proceso de admisión, que son `listado` y Jev dio
        como `ayuda`. **`corporativa` frente a `listado`** (2): los
        departamentos.
     5. Menores: la portada `/de` (Jev `listado`), una página de una sola
        palabra (Jev `articulo`), los grupos de investigación propios (3, Jev
        `perfil`) y una línea de investigación, el laboratorio y el instituto
        propios (3, Jev `articulo`).
   - **El criterio general**, en las instrucciones: nombrar un producto, curso
     o programa no hace a la página su ficha; se pregunta qué hace por el
     visitante. Presentar UN ítem para venderlo o reservarlo es
     `ficha_producto`; persuadir o invitar a apuntarse a un evento,
     `landing_comercial`; explicar cómo pagar, inscribirse o recibir apoyo,
     `ayuda`; presentar instalaciones, centros o investigación propios que el
     visitante no compra, reserva ni contrata, `corporativa` (sin
     «servicios»: los de apoyo al alumno son `ayuda`); publicar lo que exige
     una norma, `legal`.
   - **Cada frontera en la parte afirmativa de UNA opción** (antes del primer
     «Not», como exige `test_tipo_pagina.py`) y excluida tras el «Not» de las
     otras; 38 casos nuevos en `CASOS_FRONTERA` (46 en total), agrupados
     por patrón:
     - `ficha_producto` dice ahora «presentado como ese ítem: qué es, qué
       incluye o cuesta, sus características o su temario, también la página
       de ese programa que argumenta por qué elegirlo» y excluye por escrito
       las cuatro cosas que se quedaba (landing, ayuda, corporativa, legal). La
       parte afirmativa no pierde nada de la v1, para no tocar su recall.
     - `landing_comercial`: paquete con lo que incluye, experiencia
       promocionada en varios establecimientos, **financiación promocional
       para comprar** (la de Saunier, que la v1 acertaba), campaña de marca,
       presentación comercial a un tipo de cliente, «por qué estudiar aquí»
       de un área o de la organización y la página que **invita a apuntarse**
       a una jornada, sesión, charla o concurso. Excluye cómo se paga lo ya
       elegido (`ayuda`) y el texto informativo sobre un evento o concurso que
       no invita a apuntarse (`articulo`, que lo dice en su parte afirmativa).
     - `ayuda`: cómo pagar o financiar la matrícula o las tasas, becas, los
       pasos y documentos para inscribirse; intercambio y movilidad; clases de
       apoyo y tutorías para quien ya está matriculado; horarios, calendarios,
       catálogos y portales de biblioteca, también la página cuyos enlaces
       llevan **directamente** a ellos, y la que reúne las herramientas para
       profesionales. Excluye por escrito el hub que solo reparte a otras
       secciones (admisiones, becas, servicios), que es `listado`, y la
       financiación promocional (`landing_comercial`). `utilidad` excluye las
       instrucciones de inscripción o pago.
     - `listado` lleva a páginas del mismo tipo (productos, artículos, hoteles,
       ofertas, eventos, personas), se queda las tablas de instituciones
       socias y el hub de sección que es poco más que enlaces, **también
       cuando sus subsecciones son de admisiones, becas, servicios, normativa
       o información académica, aunque enlace además a uno o dos
       documentos** (proceso de admisión, servicios para doctorandos, precios
       y becas, normativa, información académica de máster); excluye la
       página cuyos enlaces llevan **todos** directamente a recursos
       prácticos (`ayuda`) y la estructura y los departamentos propios
       (`corporativa`).
     - `corporativa`: estructura y departamentos, **grupos y líneas de
       investigación propios** (no `perfil`, que se queda con personas y
       entidades de fuera), instalaciones, laboratorios, campus, incubadoras,
       institutos y centros propios presentados como parte de la organización
       y no como algo que el visitante compra, reserva o contrata (**también
       cuando la página abre con el aviso de la financiación pública
       recibida**), y **el programa de prácticas en empresas** de sus
       alumnos: ninguna otra opción lo reclamaba, y la página presenta el
       programa y los convenios propios, no cómo pagar ni inscribirse. Excluye
       por escrito la parte de UN ítem que se vende (el restaurante, el spa o
       la gastronomía de un hotel) y el servicio del campus al que uno se
       apunta (el gimnasio), que son `ficha_producto`; `ficha_producto` lo dice
       en su parte afirmativa («a campus gym or sports programme people sign
       up for», y la parte de un ítem «even if that part is not sold
       separately»), porque «no se vende por separado» valía para las dos.
     - `legal`: normativa académica o interna, sistema de calidad publicado por
       obligación legal, informes de verificación, seguimiento o acreditación y
       documentos oficiales de un programa o producto (también la página que
       los lista para descargar, no el hub que enlaza a páginas de normativa),
       memorias de sostenibilidad y la página **cuyo propósito es** el aviso
       de una ayuda pública recibida. Excluye por escrito la página de un
       proyecto, laboratorio o instituto que solo abre con ese aviso: es lo
       que sea el proyecto. La primera redacción decía lo contrario («la
       página que abre con un aviso de cofinanciación antes de describir un
       proyecto»), que contradecía el criterio general.
     - `home` admite `/de` con o sin barra; `otro`, la página cuyo contenido
       propio son una o dos palabras (y `articulo` la excluye).
   - **Los solapes de significado que encontró la revisión**, quitados: el
     test de `CASOS_FRONTERA` compara la frase literal, y la primera redacción
     cumplía la letra pero no el sentido (el hub de admisión también cabía en
     «how to register or apply» de `ayuda`; la instrucción mandaba a
     `corporativa` los «servicios» propios que `ayuda` reclamaba como clases
     de apoyo; «contest» en `landing_comercial` y en `articulo`; «financing»
     sin matiz en `ayuda` frente a la financiación de Saunier, que es
     `landing_comercial`; «also as a hub» en `ayuda`, que reforzaba el fallo
     de los hubs de doctorandos y ponía en riesgo los de becas y admisiones).
     `test_v2_sin_los_solapes_de_significado_de_la_revision` vigila cada uno,
     pero el sentido solo lo garantiza medir.
   - **Sin sobreajustar, hasta donde se puede decir**: lo que se envía no
     lleva nombres del juego (un test vigila «La Salle», «Salle», «Lopesan»,
     «Saunier», «Penguin», «Workoholics», «FEDER», «822/2021», «VSMA» y
     «Technova»; las URLs están solo en los comentarios de `CASOS_FRONTERA`,
     que no se envían), **pero sí sus patrones**: varias frases salen casi
     tal cual de las notas de etiquetado de una sola universidad (la tabla de
     instituciones, el gimnasio del campus, el programa de prácticas). Ese
     test no lo detecta; solo lo detectará medir en otro juego.
   - **Páginas que la v1 acertaba y la v2 podría hacer caer** (con las
     etiquetas unificadas, mirado una a una): los «por qué estudiar» de UN
     programa (2, protegidos por la parte afirmativa de `ficha_producto`); la
     financiación de Saunier (protegida en `landing_comercial`); los hubs de
     becas, admisiones y servicios que se acertaban como `listado` (6,
     protegidos en `listado` y excluidos en `ayuda`); las tablas de convenios
     (5, ahora reclamadas por `listado`); las prácticas (2, `ayuda` ya no las
     reclama); y, sin protección por escrito, el hub de sesiones informativas
     (`listado`, que la captación de `landing_comercial` podría comerse), la
     oficina de calidad (`corporativa`, frente a la documentación de calidad
     de `legal`) y las noticias sobre proyectos propios (`articulo`).
   - **Coste** (fórmula de la 162, caracteres / 4): la pregunta enviada pasa de
     4.868 a 9.681 caracteres (instrucciones de 626 a 1.121, opciones de 3.987
     a 8.305), **+4.813 caracteres, ~1.203 tokens por llamada**. Como se
     pregunta por muestra de plantilla (178), por censo es poco: Salle (558
     llamadas) ~0,67 M tokens, **~0,028 $** más; Penguin (405) ~0,020 $;
     Lopesan (262) ~0,013 $; Saunier (153) ~0,008 $, a 0,042 $/Mtok.
   - **La v2 se mide tras el merge en un juego nuevo, con otra semilla** y,
     si es posible, otros censos (si no, páginas que no estén en el de 300):
     está redactada mirando sus errores, y sus etiquetas se corrigieron con
     una segunda pasada y unas unificaciones que empujaron hacia Jev (13 de
     los 15 cambios con dirección); medirla sobre él inflaría el acierto
     (169, 180). Esa medida es la única que vale para la v2. Las
     etiquetas de la v1 sirven de referencia, pero `evaluar` rechaza un juego
     de otra versión; el orquestador llama a Jev (`responder --confirmar`) y
     evalúa. Solo con esa medida se podrá hablar de umbral. La v2 no se llegó a
     enviar antes de esta revisión, así que su huella cambia sin subir a la 3.
<a id="d182"></a>
182. **`title_es_generico` v2: un nombre propio o un tema concreto no es
   genérico aunque el title sea corto, y la etiqueta que tienen todos los
   ítems de una sección sin decir cuál sí** (#8, #16; mide lo que dejó la
   177). La pregunta sube a la v2 (huella en `HUELLAS`) y sigue **sin
   umbral**: todo sale `incierto`. El estado no cambia.
   - **Medido el 24-oct-2026** (producción, solo lectura;
     `/data/archivo/jev/`, etiqueta de Claude a ciegas con nota y `[duda: x]`,
     respuestas de Jev de ese día, cruzadas por id):
     - Juego de 200 de la 159 (`title_es_generico_v1_etiquetado.jsonl`):
       **93,5 %**, pero casi todo son negativos fáciles (181 de 183 bien). De
       los **17 genéricos** Jev solo ve 6: los otros 11, con confianza
       0,52-0,87, son la etiqueta que se repite en cada estudio, instituto o
       año sin decir de cuál («Normativa» en /estudis/enginyeria/normativa,
       «Formació» en /innova-institute/formacio-…, «Documentación» en
       /documentacion/accesorios-bc/, «Notas de prensa» en /noticias/2016/) y
       «Gracias». Sus 2 falsos positivos: el libro «Poesía» y «Grupos de
       investigación» con ruta opaca.
     - Juego de precisión de la 179 (`prec_title_es_generico_etiquetado.jsonl`
       sobre 2.000 titles): Jev dijo «sí, genérico» **51** veces y **34**
       lo eran (0,67, cota de Wilson 0,53). Los **17 falsos positivos**, todos
       con confianza 0,50-0,73, son titles cortos que nombran algo con nombre
       propio: el cliente de un caso de agencia («Iberdrola | Work», «CAF |
       Work», «ICOBEN | Work», dos idiomas), la versión de un país («Penguin
       Libros Chile», «… Perú», «… Uruguay» en /cl/, /pe/, /uy/), un proyecto
       («Planta Fotovoltaica | Grupo Lopesan», tres idiomas), las categorías de
       FAQ de La Salle con ruta opaca («Direcciones académicas», «Servicios del
       Campus», «Grupos de investigación»), la sección en su propia página
       («Work» en /work/), «Registration sent», el término «International» y un
       artículo con title de otro tema. En el control aleatorio (100), 5
       genéricos y Jev ve 4; el que se le escapa es «Presentació» en
       /revista-comprendre/presentacio, el mismo fallo que el juego de 200.
     - Los dos fallos son **de definición**: la v1 no decía que una marca más
       una palabra no es la marca, ni que un nombre propio sigue nombrando
       aunque detrás vaya la sección, ni separaba «la sección en su página»
       de «la etiqueta que tienen todos los ítems de esa sección».
   - **La v2**, en inglés como la v1, en tres partes: quitar **solo** la
     marca (la del dominio; «<Brand> <country>» o «<Brand> Kids» se quedan con
     lo añadido); **false** si lo que queda nombra algo concreto (un cliente,
     empresa, proyecto, producto, libro, curso, lugar, país, persona, evento o
     app, aunque sea una palabra, desconocida, o la siga la sección; el ítem
     de la ruta descrito con palabras comunes; un tema que solo tiene esa
     página, también con ruta opaca; la sección en su propia página; en una
     confirmación, el title que nombra la acción hecha, «Subscription
     confirmed»);
     **true** solo si no nombra nada: (1) solo la marca, con
     cualquier grafía («ACME», «AcmeGroup»: «WORKOHOLICS» y «PenguinLibros»
     llegan a Jev porque `solo_marca` no las reconoce; en Workoholics muchos
     titles no llevan la marca y no sale de `marcas_del_host`, y en Penguin
     la marca es «Penguin Libros ES», con espacio y sufijo), (2)
     texto por defecto o de error (Untitled, Access denied, Search results
     for), también un agradecimiento que no nombra ninguna
     acción, aunque sea en una confirmación («Muchas gracias» es `true`,
     «Registration sent» `false`), (3) un nombre interno de plantilla, bloque o
     fichero o un código, (4) una etiqueta vaga sin asunto, y (5) en la página
     de UN ítem o subsección (producto, gama, curso, área de estudios,
     instituto, centro, revista, hotel, ciudad, año: una carpeta padre nombra
     ese ítem) y sin nombrarlo, la sección o el servicio que comparten sus
     hermanas o una etiqueta que tiene cada uno sin decir cuál.
   - **La frontera de (5), con una prueba explícita** (revisión de la PR: en
     el primer borrador «un tema concreto» y «una etiqueta que tiene cada
     ítem» la reclamaban las dos opciones, y el desempate «en la duda,
     nombre» empujaba hacia `false`, el lado de los 11 falsos negativos):
     *¿valdría el mismo title, palabra por palabra, en la misma página de otro
     curso, instituto, revista o año?* Si sí, `true`; si solo ese ítem puede
     tenerlo (un proyecto o una línea de investigación del instituto, un libro
     de la serie), `false`. Y «la sección en su propia página» es `false` solo
     si la ruta no la pone bajo un ítem concreto: «Work» en /work/ sí,
     «Redacció» en /revista-comprendre/redaccio no. El desempate «nombre»
     (lo que no se puede afirmar no se afirma, 67) queda para la duda entre
     nombre propio y etiqueta; bajo un ítem concreto decide la prueba.
   - **Ejemplos inventados o con marcadores** (`'<speaker name> | Speakers'`,
     `'<Brand> <country>'`, `'Harbour extension'`, `'Fishing licences'`,
     `'Team' on /labs/<lab name>/team`): salen «SAT», «SafiraAqua» y «Culpa»,
     que la v1 llevaba y están en los dos juegos. El primer borrador tenía
     uno copiado tal cual del juego de precisión y del conjunto de 2.000
     («Scholarships and grants») y otro de un cliente («Accessible rooms»), y
     el test de entonces, con una lista a mano, no lo vio. Ahora
     `test_ningun_ejemplo_del_title_generico_sale_de_los_juegos_con_que_se_mide`
     compara cada ejemplo citado (entre comillas o en una lista entre
     paréntesis) con las **huellas** de los titles de los tres juegos
     (`tests/fixtures/titles_juegos_title_es_generico.txt`, sha256 de cada
     fragmento entre separadores), generadas con la orden nueva
     `scripts/evaluar_decisiones.py huellas`. Las huellas **no ocultan** los
     titles: son sha256 sin sal de textos cortos y se recuperarían probando un
     diccionario; solo evitan guardar la lista legible en el repo. Y el test
     solo ve la copia literal (normalizada a minúsculas, espacios y NFC, entre
     comillas simples, dobles o tipográficas, o en una lista entre
     paréntesis): una paráfrasis, una traducción o un sinónimo no casan con
     ninguna huella. **No hay lista blanca**: un primer intento admitía
     «Home», «Contact» y «Projects» como vocabulario de la definición, pero
     los tres son titles de los juegos («Contact» en /contact está tal cual
     en el de precisión, «Projects | …» en el conjunto de 2.000) y «Projects»
     en /projects/ es sinónimo directo de un falso positivo medido («Work» en
     /work/). Una segunda revisión lo vio: la sección en su página se ilustra
     con una inventada sin huella («'Lost property' on /lost-property/») y
     «Home» sale de la lista de textos por defecto (quedan «Untitled», «New
     page», «Welcome»…). También se cambiaron los ejemplos que eran
     paráfrasis de un fallo medido («Solar plant» por «Planta Fotovoltaica»,
     «<client name> | Projects» por «Iberdrola | Work», «News» en
     /news/<year>/, «Downloads» en /products/<range>/downloads,
     «promo-apr-23», y «'Technical service' on /technical-services/<city>»,
     traducción literal de «SAT» en /sats/<ciudad>-NN, que está en los tres
     juegos, por «'Clinics' on /clinics/<town>»; con el mismo criterio,
     buscando a mano la traducción al castellano de cada ejemplo en las
     huellas, «'Timetable' on /courses/<course>/timetable» por «'Exam
     dates'» («Horarios» está en los juegos), y salen de las listas «Page not
     found» y «Thank you, Thanks a lot», traducciones literales de «Página no
     encontrada», «Gracias» y «Muchas gracias»: el agradecimiento se queda
     como regla, sin ejemplo); «<Brand> <country>» se queda porque es la regla misma.
   - Los casos reales van en **`CASOS_FRONTERA`** de
     `shared/preguntas/coherencia.py`, como en `tipo_pagina.py` (178): caso,
     respuesta y la frase que lo decide, incluidos los pares que las
     etiquetas separaban sin criterio («Formació» del Innova y «Training and
     Capacity Building» del CFI; «Presentació» y «Redacció»/«Subscription» de
     la misma revista; «Nirvana» bajo el Innova, que sí es suyo; «Muchas
     gracias» frente a «Registration sent»). El test solo vigila que **la
     frase que lo decide sigue en su parte** («Answer false» o «Answer true»)
     y no en la otra; no prueba que el significado de la otra parte no lo
     cubra también. No se envían. Llevan nombres de clientes, como los de la
     178: no cambian ningún comportamiento ni salen hacia el modelo.
   - **El estado basta** (title, dominio y ruta): todos los fallos se
     deciden con lo que ya va; la ruta dice el padre («enginyeria»,
     «accesorios-bc», «2016») y el dominio la marca. Mandar la marca calculada
     (`marcas_del_host`) ayudaría con «<marca> <país>», pero cambia el estado:
     las filas de los juegos ya guardados no la llevan, `responder` los
     reutiliza tal cual y lo medido tras el merge no sería lo de producción.
   - **Cambian etiquetas**, así que los juegos se re-etiquetan a ciegas
     (`evaluar_decisiones.py reetiquetar <juego> --salida <juego_v2>`, 180)
     en vez de reutilizarlos. Las candidatas según la prueba de (5), al menos
     nueve: en el de 200 pasan a `true` «Serveis per a l'estudiant» en
     /estudis/masters-i-postgraus/ y /cursos-despecialitzacio/ (eran `false`
     con `[duda: true]`), «Publicaciones y conferencias» en /cfi-…/, «Academic
     regulation» en /masters-postgraduates/academic-information/ y
     «Evaluation Regulations» en /postgraduate-master-degrees/… (la misma
     etiqueta que «Normativa» en /estudis/enginyeria/); en el de precisión
     pasan a `true` «Training and Capacity Building» del CFI y «Redacció» y
     «Subscription» de la revista Comprendre (eran `false`, las dos últimas
     con `[duda: true]`), y «Ciudad Sostenible 2017» en
     /2017-08-21/la-conca-de-barbera (era `true` con `[duda: false]`) pasa a
     `false`: nada en la ruta dice que sea la serie. Quedan dudosas
     «Próximos cursos»/«Upcoming Courses» en /cursos-de-especializacion/
     (¿nombra el ítem o es la etiqueta de cada nivel?), «Portal clientes»
     (`false` en /login, `true` en /register) y «Climatización correcta» en
     un proyecto de viviendas. El re-etiquetado a ciegas manda sobre esta
     lista.
   - **Se medirá tras el merge** (lo hace el orquestador, que es quien llama a
     Jev): re-etiquetar los dos juegos, `responder --confirmar` y `evaluar`,
     y el de precisión otra vez sobre el mismo conjunto de 2.000. **Esa
     medida es optimista por construcción**: la v2 se redactó mirando estos
     fallos, y aunque ningún ejemplo citado casa con la huella de un title de
     los juegos (el test lo comprueba sin lista blanca; no ve paráfrasis ni
     traducciones, que se revisan a mano), las reglas y varios ejemplos con
     marcadores siguen su forma («<Brand> <country>», el
     nombre propio seguido de la sección, el proyecto descrito con palabras
     comunes, la etiqueta de subsección). Sirve para ver si la v2 arregla lo
     que quería y no rompe los verdaderos positivos, **no para darla por
     buena**: la v2 no se valida, ni se le pone umbral, hasta medirla en un
     juego de censos no usados aquí (169).
   - **Coste** (fórmula de la 162): 3.899 caracteres de pregunta frente a
     1.999, ~475 tokens más por llamada: en Penguin (2.661 llamadas) ~0,05 $
     más por censo, en La Salle (845) ~0,02 $.
   - Fuera: que `solo_marca` reconozca la marca sin espacios ni sufijo o la
     del dominio cuando `marcas_del_host` no da ninguna (sacaría
     «WORKOHOLICS» y «PenguinLibros» sin preguntar; cambia el filtro de la 177
     y la incidencia por regla aún no existe), y mandar la marca en el estado
     (arriba).
