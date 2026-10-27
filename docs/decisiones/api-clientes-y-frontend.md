# Decisiones: api, clientes, csv, backup y frontend

> Movidas tal cual desde `CLAUDE.md` (2026-10-14). Indice de todas las
> decisiones en [`README.md`](README.md). La numeracion es historica y se
> conserva: los commits, issues y el resto de decisiones citan por numero.

<a id="d5"></a>
5. **Streaming CSV** — New DB session per 1000-row window to avoid long transactions.

<a id="d33"></a>
33. **Pydantic v2 tira en silencio lo que no esta declarado** — el formulario
   enviaba `use_sitemap`, `JobConfig` no lo declaraba y el spider leia
   `job_config.get("use_sitemap", True)`: desmarcar la casilla no tenia NINGUN
   efecto y nadie se enteraba. Le pasa a cualquier clave nueva. Hay un test
   que se mantiene solo (`test_jobconfig_claves.py`): saca por regex las
   claves que lee el spider —hoy 14— y falla si alguna no esta en el schema.
   Ese cubre lo que lee el CODIGO; lo que escribe una PERSONA lo cubren
   `tests/test_lanzador.py` (las claves de los `projects/*/config.json` contra
   las que `JobConfig` declara, leidas del AST) y `scripts/lanzar_job.py`, que
   tras crear el job compara lo enviado con lo guardado y sale con **3** si la
   API ha tirado algo, nombrando la clave. Con los cinco configs de hoy no
   salta: es un cable trampa, no un lobo que grita.

<a id="d65"></a>
65. **El backup decía «stream» y montaba el censo entero en memoria, y los
   duplicados se guardaban al cuadrado** — las dos salieron de lo mismo:
   intentar traerme un censo de producción para compararlo y que no cupiera.
   (a) `stream_backup_zip` acumulaba **cada tabla entera** en una lista de
   cadenas, la unía en otra cadena y lo metía todo en un `BytesIO` que solo se
   soltaba al final, con un único `yield`. Ahora cada fila se escribe
   directamente en la entrada del ZIP (`zf.open(name, "w")`) sobre un destino
   **sin `seek`** —así `zipfile` escribe descriptores de datos y el ZIP se
   puede servir mientras se genera— y se suelta un trozo cada MB.
   (b) Lo que de verdad pesaba no era el ZIP: cada incidencia de duplicado
   guardaba `duplicate_urls` con **la lista de TODAS las demás del grupo**, o
   sea N×N identificadores. Medido en el censo de CST, donde **7.511 páginas
   comparten la misma description**: esa sola clase ocupaba **431 MB** de la
   columna `details` (9 kB por fila, la mayor de 59 kB), frente a 8, 7 y 0,8 MB
   en censos sin un grupo gigante. Y **no lo leía nadie**: cuatro sitios lo
   escribían y ninguno lo consumía. Ahora va la cuenta real
   (`duplicate_count`), una muestra de 20 y una marca si está recortada; el
   grupo entero se saca con una consulta por el valor compartido.
   Medido, pico de memoria del proceso que exporta:

   | censo | antes | ahora |
   |---|---|---|
   | Saunier Duval (2.876 URLs) | 264 MB | **97 MB** |
   | CST (12.112 URLs) | 1.450 MB | **92 MB** |

   Y la columna `details` de CST: **434 MB → 4,3 MB** con las mismas 49.207
   incidencias. Lo importante del segundo par de cifras es que **ya no crece
   con el censo**: 97 MB con 2.876 URLs y 92 MB con 12.112. El ZIP sale **byte
   a byte idéntico** en los dos censos (comprobado entrada por entrada), así
   que es la misma copia hecha de otra manera.

<a id="d83"></a>
83. **El cliente es una entidad, y sus credenciales viven en el entorno, no
   en la base de datos** (#36, F1). Tres criterios:
   - **Secretos.** La API no tiene autenticación y el CORS está abierto: todo lo
     que se guarda en `clients` lo lee y lo cambia cualquiera que llegue al
     puerto, y viaja en cada backup. Una integración (GSC, DataForSEO, Ahrefs,
     Semrush, Majestic, PageSpeed) guarda solo el tipo, parámetros públicos
     (propiedad de GSC, ubicación e idioma de DataForSEO) y el **nombre** de la
     variable de entorno con la clave (`DATAFORSEO_LIN3S`, en el `.env` del
     servidor). La API devuelve `configurada: true/false`, jamás el valor; quien
     llame al proveedor pide el secreto con `shared/credenciales.credencial()`.
     El nombre tiene que ser un identificador de entorno **y empezar por el
     prefijo de su tipo** (`DATAFORSEO_*`, `GSC_*`...): como cualquiera puede
     escribir en `clients`, sin el prefijo bastaría con declarar una
     integración cuya variable fuera `POSTGRES_PASSWORD` para que el código que
     llama a DataForSEO mandara la contraseña de la base de datos como
     credencial. Se valida al guardar y otra vez al leer (la BD se puede editar
     a mano). También se rechazan parámetros con nombre de secreto (`api_key`,
     `password`, `token`...) y `http.basic_auth_password` en la configuración
     de un cliente o un perfil; en un job puntual sigue pudiendo ir.
   - **Herencia.** `clients.config_defecto` y `crawl_profiles.config` son dicts
     PARCIALES de `JobConfig` (solo lo que fijan; un `JobConfig` entero pisaría
     con sus defaults todo lo que viniera detrás), y una clave desconocida es un
     422 que la nombra en vez de perderse (decisión 33). Al crear el job se
     fusiona en profundidad defaults ← cliente ← perfil ← lo que mande el job
     (`shared/clientes.fusionar_config`: listas y escalares, gana la última
     capa; `None` explícito quita lo heredado), se valida DESPUÉS de fusionar y
     el resultado se copia en `jobs.config`. El crawler y el analizador no saben
     que existe el cliente, y editar un cliente no cambia ningún job ya creado.
   - **Migración sin romper lo viejo.** `jobs.client_id` (texto) NO se convierte
     en FK: se añade `jobs.cliente_id` al lado (y `profile_id`; lo mismo en
     `programaciones`). Los jobs viejos se abren igual, `lanzar_job.py` sigue
     mandando texto (la API lo resuelve por slug y crea el cliente si falta) y
     si la conversión saliera mal el dato original sigue ahí. `init_db.py`
     crea un cliente por slug distinto (sin acentos ni caja) y **imprime los
     textos que colapsan en el mismo slug**: fusionar erratas en silencio sería
     cambiar el histórico de un cliente sin que nadie lo sepa. `acme` y
     `acme-canary` son clientes distintos porque eso no se puede decidir mirando
     el texto. Borrar un job deja `last_job_id` del perfil a NULL; borrar un
     cliente borra sus perfiles y deja sus jobs sin cliente (con su texto como
     pista), salvo `con_jobs=true`, y exige `confirmar=<slug>`. El backup lleva
     `client.json`/`profile.json` (formato 2, acepta el 1) y al importar el
     cliente se casa por slug, nunca por id; el perfil se casa por nombre y no
     se crea, para que importar un censo no deje nada programado.

<a id="d87"></a>
87. **Un estático sin `Cache-Control` mezcla dos versiones de la aplicación**
   — tras un despliegue, la consola de un navegador salía llena de
   `Uncaught ReferenceError: verClientes is not defined` para funciones que
   **sí estaban** en el `app.js` que servía el servidor. El `index.html` venía
   nuevo y el `app.js` de la caché, y no hay forma de distinguir eso de un
   fallo de código sin ir a mirar lo que se sirve de verdad: la primera
   reacción siempre es sospechar del despliegue. A mí me pasó seis veces en un
   día y lo fui tapando con recargas forzadas en vez de arreglarlo.
   El servidor mandaba `etag` y `last-modified` pero **no `Cache-Control`**, y
   sin esa cabecera el navegador decide por su cuenta cuánto se fía de su
   copia. Ahora los estáticos y el HTML van con `no-cache`, que no es «no
   guardes» sino «guárdalo, pero pregunta antes de usarlo»: con el `etag` que
   ya se mandaba, la pregunta se contesta con un **304** de unos cientos de
   bytes frente a los 100 kB del fichero. Se paga una petición condicional por
   carga y se gana no servir nunca una mezcla de dos versiones.

<a id="d96"></a>
96. **Borrar censos viejos: en masa, con ids, y sin llevarse por delante la
   referencia de una serie** — un censo de produccion ocupa gigas y la
   instalacion acumula pruebas de 40 URLs, canarios y censos de hace meses.
   Borrarlos de uno en uno —entrar en la ficha, teclear «eliminar», volver— es
   lo que hace que no se borren nunca y que el aviso lo acabe dando el disco.
   Medido en la base local el 2026-10-15, **antes de agrupar los enlaces
   (decision 94)**: **57 GB para 50 rastreos**, de los cuales `links` eran
   **49 GB y 187,5 millones de filas, el 86%**.
   `POST /api/jobs/eliminar`, con cinco criterios:
   - **Ids, nunca un filtro.** Un filtro se puede desplazar entre lo que se vio
     en la lista y lo que llega al servidor —otro rastreo termina y entra en la
     pagina— y esto no se recupera.
   - **Dos guardias, más el candado del censo.** Un rastreo en marcha (`pending`, `running`,
     `analyzing`) y **el censo de referencia de una programacion activa**: sin
     el, el siguiente rastreo de esa serie se queda sin con que compararse, y
     un silencio se lee igual que «no ha cambiado nada» (decisiones 78 y 86).
     Lo demas es aviso y no guardia: que un host se quede sin ningun censo
     completado solo muerde si ese sitio se vuelve a rastrear, lo que puede no
     pasar nunca; la serie programada SI va a volver a correr. `forzar` salta
     **solo el de la serie**, y marcarlo en la interfaz **rehace la vista
     previa**: la lista que se lee y lo que se borra tienen que ser lo mismo.
     Un rastreo en marcha no se borra ni forzando: con `running` el pipeline
     sigue metiendo enlaces mientras se borran, y con `analyzing` el `DELETE`
     de la fila de `jobs` espera al `FOR UPDATE` que el analisis mantiene toda
     la pasada —43 min en Druni—, con la peticion colgada mucho mas alla de su
     presupuesto. Se cancela, se espera a que pare y se borra.
     Lo mismo, y tampoco con `forzar`, si alguien tiene **el candado del
     censo** (`shared/candados.py`): el re-analisis de la referencia (decision
     93) y el archivado (95) lo toman sobre censos `completed`, justo los que
     se borran, sin que el `status` lo diga. Se prueba y se suelta en el acto;
     en cuanto el borrado marca `deleting`, ninguno de los dos lo elige.
     El archivado de la decision 95 no anade ningun guardia: solo archiva
     censos que ya NO son referencia de nada, y la comparacion no lee `links`
     (`paginas_de_censo` va por `urls`), asi que un censo archivado que vuelva
     a ser referencia porque se borre el mas nuevo sigue sirviendo para
     comparar paginas y alertas.
   - **La vista previa la calcula el MISMO endpoint, con `simular`.** Dos
     caminos distintos para «que se va a borrar» y «que se borra» son dos
     criterios que acaban discrepando (decisiones 23 y 39).
   - **Presupuesto de tiempo, no limite de trabajo.** Cada censo se borra en su
     propia transaccion y por lotes de 200.000 filas; lo que no cabe en los
     60 s vuelve como `pendientes` y la interfaz repite. Las hijas se borran
     antes que la fila de `jobs`, asi que lo que queda a medias es «un censo
     con menos enlaces» —consistente, y la siguiente pasada lo termina— y no
     filas huerfanas. Hace falta porque un `DELETE` de decenas de millones de
     enlaces son minutos con la tabla tomada, y porque **mergear una PR recrea
     el contenedor de la API y se lleva la peticion en vuelo**
     ([`operacion.md`](../referencia/operacion.md)).
     Lo primero que se confirma es **`status='deleting'`**, porque a medias
     tambien puede quedarse para siempre (la pestana se cierra, la API se
     recrea) y lo que queda —sin enlaces ni incidencias, con media tabla de
     URLs— no puede seguir pareciendo un `completed`: la comparacion lo
     elegiria como referencia y sus alertas callarian sobre las pocas paginas
     comunes (decisiones 78 y 86), y el archivado lo guardaria en un Parquet
     vacio (decision 95). Ninguno de los dos mira un job sin terminar, y la
     lista lo ensena como «Borrandose» para terminarlo repitiendo el borrado.
     La interfaz da una pasada por atascada solo si no termina ningun censo
     **ni borra ninguna fila**: un censo grande pasa muchas vueltas sin
     terminar, borrando en cada una. El presupuesto empieza antes de medir
     (medir tambien es tiempo de la peticion) y el primer censo de cada pasada
     entra siempre, asi que toda pasada avanza al menos un lote.
   - **Lo que se libera se dice bien.** Postgres marca el espacio como
     reutilizable y lo reusa en el siguiente rastreo, pero el fichero **no se
     encoge** sin un `VACUUM FULL`, que toma un candado exclusivo sobre una
     tabla de decenas de GB y por eso no se dispara desde aqui. Lo que se gana
     es que el disco deje de crecer, y esa frase viaja en la respuesta.
     Lo liberado se suma con lo que midio la **vista previa**: un censo que
     necesita varias pasadas llega a la ultima casi vacio, y medirlo entonces
     daria el resto como si fuera lo que ocupaba. Al borrar, cada censo se
     mide **una sola vez, justo antes de empezarlo** (contar sus enlaces son
     segundos, y repetirlo en cada pasada era gastar el presupuesto en contar
     filas que se iban a borrar a continuacion): uno que no se llega a empezar
     en una pasada no se mide en ella, y uno que ya estaba en `deleting` no se
     vuelve a medir (sale «sin medir», nunca un resto con cara de total). Un
     Parquet que no se puede medir marca `archivo_incompleto` en vez de sumar
     un cero. `deleting` no necesita migracion: `jobs.status` es un
     `String(20)` sin restriccion de valores.

   **El Parquet de un censo archivado se borra con el, y por la misma
   funcion.** `borrar_job` —la del `DELETE /api/jobs/{id}`, la del borrado de
   un cliente con `con_jobs` y la de este endpoint— llama a
   `archivo_enlaces.borrar` despues del commit; si el presupuesto corta a
   medias, la fila de `jobs` sigue y su fichero tambien, porque quitarlo antes
   dejaria un censo «archivado» sin archivo. La API ya monta el volumen
   `archivo` (lo lee para servir los enlaces archivados), asi que no hace falta
   un barrido del worker.

   El tamano por censo (`shared/almacenamiento.py`, tambien `GET
   /api/almacenamiento` con el desglose por tabla) es una **estimacion**:
   filas por `job_id` x bytes por fila medidos con `pg_total_relation_size` y
   `reltuples`, y las tablas que cuelgan de `url_id` repartidas por URL,
   porque contarlas exigiria unir con `urls` y eso es lo que prohibe la
   decision 37. Tres cosas que costaron una medicion o un criterio:
   - **Los enlaces se cuentan con una consulta POR CENSO, no con un `IN`.** El
     recorrido del indice de `links` iba a unos **1,6 us por fila** (691.939
     enlaces de un censo de progym en 1,1 s, el 2026-10-15 y antes de
     agrupar), asi que un `IN` con cinco censos de progym se pasaba del tope de
     20 s y, al reventar la consulta agrupada, se perdian los tamanos de los
     CINCO —medido: ocho con «?»—. Una por censo degrada de uno en uno: los que
     caben en el presupuesto traen su cifra y los demas salen **sin medir**,
     que es «no se sabe» y no una cifra corta.
   - **Sin los enlaces no hay cifra.** Siendo la tabla mas grande con
     diferencia, una estimacion sin ellos no se queda corta: miente. Y un 0 se
     leeria como «este censo no ocupa nada», que es justo lo contrario de lo
     que se quiere saber antes de borrarlo. Lo que se cuentan son **filas**,
     no enlaces: desde la decision 94 una fila agrupa sus `repeticiones`, y lo
     que ocupa disco es la fila.
   - **Un censo archivado (decision 95) se mide por su Parquet.** No tiene
     filas en `links`, y se sabe: van a 0 sin consultar, asi que nunca sale
     «sin medir» por falta de presupuesto ni con el tamano que tenia antes de
     archivarse. Lo que ocupa es su fichero, medido con `stat` (o lo anotado
     en `jobs.enlaces_archivados` si la API no lo ve), y va **aparte**
     (`archivo_bytes`), no sumado al de la base: ese espacio SI vuelve al disco
     al borrar, y mezclarlo con la estimacion de Postgres haria mentir a la
     nota. El panel de espacio suma tambien los Parquet del directorio, que
     estan fuera de `pg_database_size`.
   Las tablas del semantico y de Search Console se pesan igual —`semantic_pages`
   guarda un vector de 1.024 dimensiones por pagina— y se borran por el
   `ON DELETE CASCADE`: importar `shared.semantic_models` en el borrado
   registraria sus tablas de pgvector en el `MetaData` compartido y tumbaria el
   `create_all` de `test_pagerank_db.py` contra el `postgres:16-alpine` de CI.
   Por eso el test que comprueba que ninguna tabla del censo se queda fuera
   pregunta a los modelos **en otro proceso**.

<a id="d134"></a>
134. **La API pide un token, y sin él está cerrada, no abierta** (6.1, criterio
   del usuario, 2026-10-18). Hasta hoy, quien llegara a la dirección pública
   podía leer los censos de todos los clientes, lanzar rastreos y borrarlos. Y
   el CORS era peor de lo que parecía: `allow_origins=["*"]` con
   `allow_credentials=True` hace que Starlette devuelva el origen de quien
   pregunta, así que cualquier web podía leer la API desde el navegador de
   quien la visitara.
   - **Quién la usaba**, comprobado en producción antes de cerrarla: la
     interfaz (por traefik), las comprobaciones de `ocupado-prod.sh` desde el
     propio servidor y `lanzar_job.py`. n8n no la llama (de sus 21 flujos,
     ninguno apunta al crawler) y motor-de-contenido lee la base de datos con
     su usuario de solo lectura, no la API. El puerto 8000 solo escucha en
     `127.0.0.1`.
   - **Un token, `API_TOKEN`, en el entorno** (decisión 83), de 24 caracteres
     como mínimo. Los scripts lo mandan como `Authorization: Bearer`; el
     navegador lo escribe una vez en `/entrar` y recibe una cookie
     `HttpOnly`, `SameSite=Strict` y `Secure` por https, que **no lleva el
     token**: lleva una caducidad (30 días) firmada con una clave derivada de
     él. Cambiar el token cierra todas las sesiones.
   - **Middleware y no dependencia por router**: un router nuevo nace
     protegido. Es ASGI puro porque por ahí pasan el CSV en streaming y el
     backup. Públicos: `/health`, los estáticos, el esqueleto de la página y
     `/api/sesion`. `/docs` y `/openapi.json` no, porque enumeran cada
     endpoint.
   - **Sin `API_TOKEN` es 503**, con el motivo: el fallo seguro de un
     despliegue que se olvida de configurarlo es no poder entrar.
     `API_AUTH_DESACTIVADA=1` la abre a propósito (local y tests), nunca por
     omisión. El CORS queda cerrado salvo los orígenes de `CORS_ORIGINS`.
   - El token del enriquecido (decisión 85) se mantiene aparte: lanzar algo de
     pago sigue pidiendo el suyo aunque se haya entrado.
   - En la interfaz, cualquier 401 de `/api` lleva a `/entrar` y vuelve después
     a la misma ruta (solo rutas `#/...`, para que el parámetro no sea una
     redirección abierta). «Cerrar sesión» está en el menú de la barra.
   Queda para después: tokens por rol y enlaces de solo lectura por cliente
   (el resto de 6.1), que necesitan acotar cada router por `cliente_id`.

<a id="d138"></a>
138. **El informe para el cliente junta lo que ya se calcula, y la
   fiabilidad va dentro, no en una nota aparte** (Ola 6.2 de
   `docs/PLAN_MEJOR_CRAWLER.md`, primera parte). Hasta ahora el informe
   mensual se montaba a mano a partir de la interfaz (`docs/PARA_CLIENTES.md`).
   `GET /api/jobs/{id}/informe` devuelve, de un censo: cliente y sitio (la raíz
   del host, no la primera semilla, que en Lopesan es `/blog/`), la nota con su
   tope y las categorías (decisión 92), las cifras de la ficha, lo que ha
   cambiado frente al anterior (`_resumen_de_rastreo`, lo mismo que el resumen
   semanal de la decisión 135: alertas, reglas del cliente, incidencias nuevas,
   resueltas o el motivo de no compararlas), las **diez primeras por
   prioridad** (decisión 136) con tres ejemplos cada una, y `fiabilidad`.
   No mide nada nuevo: llama a las funciones de `/insights`, `/stats` y
   `/issues/resumen`, para que el informe no pueda contradecir a la interfaz.
   - **La fiabilidad, en el informe.** Un censo cortado (por máximo de URLs,
     tiempo, estancado o cancelado, salvo en modo lista, donde el tope es lo
     esperado), un muro de WAF (`bloqueado_por_waf`) o un grafo sin los
     enlaces que monta el JavaScript (`_pagerank_fiable`, decisión 35) salen
     en un recuadro «Antes de leer las cifras», arriba. Un informe que parece
     completo y no lo es hace más daño que no entregarlo (decisiones 67 y 103).
   - **La página**: `#/rastreos/<id>/informe-cliente`, desde el botón
     «Informe para el cliente» de la cabecera de la ficha (estaba en
     Exportar hasta la revisión de UX del 25-oct). Las fichas (qué es, qué decide Google, cómo se
     arregla) salen de `incidencias.js`, que vive en el frontend; por eso el
     endpoint devuelve datos y la interfaz pinta. Se imprime o se guarda como
     PDF desde el navegador (`@media print` quita la barra y los botones), y
     el título de la pestaña es el nombre del PDF. Un hallazgo de plantilla
     (una fila con `paginas_afectadas`) dice cuántos hallazgos son y que se
     arregla una vez, no «1 página».
   Probado contra el censo semanal de Lopesan (14-oct) con la función ejecutada
   contra producción en una conexión de solo lectura: nota 79 (la media sería
   90, y 115 URLs con error la recortan), 44 tipos de incidencia, la
   comparación dice por qué las incidencias no se comparan (dos versiones del
   análisis, decisión 91). PDF A4 de 3 páginas sin retoques.
   **Pendiente** de la 6.2: generarlo en el worker al terminar un programado y
   mandarlo (6.4), la evolución del perfil en gráfica (decisión 130), y que el
   equipo lo revise como entregable.
   **Ampliado el 19-oct-2026** con dos secciones que salen solo si hay algo
   medido (no se afirma «todo bien» sin mirar): **«Tráfico de Google»**
   (decisión 142) y **«Buscadores de IA»**: qué bots de búsqueda y de
   entrenamiento de IA bloquea el robots.txt del sitio (decisión 129), las
   indexables con `nosnippet` o `max-snippet` corto (137) y las plantillas con
   texto o enlaces que solo existen con JavaScript (140). Y el informe tardaba
   **17 s** en Lopesan, casi todo `/insights`: `_calc_links` recorria dos veces
   los 1,43 M enlaces del censo (total y `nofollow`). Con una suma condicional
   en un solo recorrido, medido a traves del tunel, 12,5 s -> 5-6 s; en
   produccion `/insights` quedo en 10,7 s y el informe en 11,7. Como la nota
   solo cambia al re-analizar, **se cachea en Redis** por (job, version del
   analisis, numero de incidencias), siete dias: la primera apertura tras un
   analisis la calcula y las demas —tambien el enlace del cliente, decision
   143— salen de la cache. Sin Redis, se calcula como siempre.

<a id="d141"></a>
141. **Search Console por cliente: la llave se sube, va a un fichero del
   servidor y la descarga es diaria y sola** (Ola 4.1 de
   `docs/PLAN_MEJOR_CRAWLER.md`, primera parte). Criterio del usuario
   (19-oct-2026): acceso por cliente, con una cuenta de servicio por cliente o
   una para todos, subida desde la interfaz, y que una vez configurado «haga lo
   que tenga que hacer».
   - **La llave se sube y no va a la base de datos.** La decisión 83 dejó los
     secretos en el `.env` del servidor; obligar a editarlo por ssh para cada
     cliente es lo que hace que no se configure nunca. `POST /api/credenciales`
     guarda el JSON en `SECRETOS_DIR` (`/secretos`, volumen `secretos` de
     Docker: lectura y escritura en la API, solo lectura en el worker) como
     `<NOMBRE>.json` con permisos 600 y escritura atómica
     (`shared/secretos.py`). Lo que protegía la 83 se mantiene: no está en la
     base de datos ni en los backups, la API **no devuelve nunca** su contenido
     (solo `client_email` y `project_id`, lo que hay que dar de alta en Search
     Console), y el nombre lleva el prefijo de su tipo (`GSC_*`) con la misma
     validación que las variables. `credencial()` lee primero el entorno y si
     no el fichero: manda lo que el operador puso a mano. Una llave sirve a
     varios clientes; no se borra si alguno la usa (409 con sus nombres).
     `GET /api/credenciales/{nombre}/propiedades` lista lo que ve la cuenta,
     para elegir la propiedad sin escribirla y saber si el cliente ya dio acceso.
     Y la ruta del prototipo semántico que guardaba la llave **en la base de
     datos** (`POST /api/semantic/gsc-accounts`, `gsc_accounts.credentials_json`)
     responde ahora 410 diciendo dónde se sube; en producción no había ninguna
     guardada (19-oct-2026).
   - **Por cliente y día, no por job** (`docs/INVESTIGACION_SEARCH_CONSOLE.md`):
     tabla `gsc_paginas` (cliente, propiedad, fecha, URL, `url_clave`, clics,
     impresiones, posición), con **todas** las URLs que da Google, también las
     que el rastreo no encontró. `url_clave` es `clave_url` (decisión 39) en
     SHA-256, y dos URLs que esa regla iguala (orden de parámetros) se suman.
   - **La descarga** (`shared/gsc.py`, en el worker, un hilo propio para no
     quitar huecos a los rastreos y con candado en Redis entre réplicas): la
     primera vez, `SEARCH_CONSOLE_DIAS_INICIALES` (90) días; después, una vez
     al día a partir de las 4:00 UTC, desde tres días antes del último guardado
     hasta hoy menos dos (Google tarda 2-3 días y los últimos cambian hasta ser
     finales). Por día, de 25.000 en 25.000 filas, y el día se reescribe entero
     en su transacción. Google corta en 50.000 filas por día: esos días quedan
     en `gsc_estado.dias_truncados`, porque ahí «sin impresiones» no se puede
     afirmar. Un error se reintenta a las 6 horas; «Sincronizar ahora» lo pide
     para la siguiente vuelta del worker.
   - **Los errores dicen qué hacer**: un 403 es «la cuenta X no tiene acceso a
     la propiedad Y: añádela como usuario en Search Console → Configuración →
     Usuarios y permisos»; un 400/404, cómo se escribe la propiedad; un 401,
     que la llave está revocada.
   - **Interfaz**: tarjeta Search Console en Configuración del cliente (estado,
     cuenta, días guardados, clics e impresiones de los últimos 28 días,
     «Sincronizar ahora», y conectar en tres pasos: llave subida o nueva,
     propiedad de entre las que ve la cuenta, guardar).
   Comprobado contra Google con la llave real de Lopesan (sin guardar nada): la
   cuenta ve `https://www.lopesan.com/` con permiso completo, y un día (15-oct)
   son **3.609 URLs, 5.714 clics y 252.555 impresiones**, en 0,6 s. Ningún
   test llama a la red. **Segunda parte**, aparte: los cruces con el rastreo
   (tráfico en páginas rotas o fuera del índice, huérfanas con tráfico, lo que
   Google conoce y el rastreo no encontró, prioridad por clics) y la columna en
   el Explorador. Ojo para ella: las URLs con más clics de Lopesan llevan
   `?partner=` y no casarán con la del censo sin tratar ese parámetro.

<a id="d143"></a>
143. **El cliente tiene un enlace propio de solo lectura, y solo ve sus
   informes** (Ola 6.1 de `docs/PLAN_MEJOR_CRAWLER.md`, primera parte). La API
   tiene un único token (decisión 134), que lo abre todo: no se le puede dar a
   un cliente. Acotar TODOS los routers por cliente es la 6.1 entera y mucha
   superficie; lo que un cliente pide de verdad es su informe, así que eso es
   lo primero:
   - `/c/<token>` es la misma aplicación en **modo compartido**: sin barra ni
     datos de la agencia, el informe de la decisión 138 (y con él la prioridad,
     la comparación y el tráfico de Google) con un selector entre sus censos.
     No pide nada protegido: se comprobó que no hace ni una petición fuera de
     `/api/publico`.
   - **El token autoriza, no la sesión.** `api/auth.py` deja pasar
     `/api/publico` (y nada que solo empiece por esas letras: `/api/publicos`
     sigue protegido). 32 caracteres aleatorios (`secrets.token_urlsafe(24)`);
     en `enlaces_cliente` va su SHA-256 y seis caracteres para reconocerlo, y el
     token se enseña **una vez**, al crearlo: ni la base de datos ni un backup
     permiten entrar.
   - **Cada endpoint público comprueba que lo pedido es de ese cliente**: solo
     censos completados (no canarios ni pruebas, `tipo_de_rastreo`) de su
     `cliente_id`. Un id de otro cliente, un enlace revocado, uno caducado y
     uno inventado dan el mismo 404: a quien prueba tokens no se le dice cuál
     existió.
   - Se gestiona en la ficha del cliente, Configuración → «Enlace para el
     cliente»: crear (con nombre y caducidad, 365 días por defecto), copiar,
     revocar, y cuándo y cuántas veces se abrió.
   - En modo compartido el informe no enseña la jerga interna: el motivo de
     no comparar incidencias («dos versiones del análisis…») se cambia por una
     frase neutra.
   **Pendiente** de la 6.1: tokens por rol para la API (lectura frente a
   escritura) y acotar el resto de routers por cliente, que es lo que haría
   falta para darle al cliente el Explorador.

<a id="d145"></a>
145. **El censo en Excel con varias hojas, legible sin la herramienta** (Ola
   6.3 de `docs/PLAN_MEJOR_CRAWLER.md`, sin Google Sheets). El CSV sirve para
   cruzar datos; lo que se manda a un cliente o a desarrollo tiene que abrirse
   y entenderse. `GET /api/jobs/{id}/export.xlsx` (Exportar → «Excel (varias
   hojas)»), en `api/excel.py`:
   - **Resumen** (cliente, sitio, cifras, nota y categorías, Search Console y
     los avisos de fiabilidad del informe, decisión 138), **Qué arreglar**
     (todas las incidencias por prioridad, decisión 136, con su ficha: qué es,
     qué decide Google y cómo se arregla, y un ejemplo), **URLs** (las columnas
     útiles del Explorador, las de Search Console incluidas), **Incidencias**
     (hasta 500.000; el formato admite 1.048.576 filas y penguin tiene 959.633)
     y **Google sin rastrear** (las URLs con clics que el rastreo no encontró).
   - **Los nombres y las fichas se leen del frontend** (`incidencias.js` y
     `app.js`), no se copian: dos catálogos acabarían diciendo cosas distintas.
     El formato no lleva comillas escapadas y se lee con una expresión regular;
     si cambiara, la hoja sale con el nombre técnico en vez de romperse (hay un
     test que exige más de 100 fichas leídas).
   - **Memoria plana**: `openpyxl` en solo escritura y las URLs por tramos de
     1.000 con su sesión (decisiones 5 y 65); se escribe en un temporal que se
     borra al servirlo. Medido con el censo de Lopesan contra producción en
     solo lectura: 5.281 URLs, **26 s** (10 son `/insights`), **1,2 MB** y un
     pico de **121 MB**.
   Queda de la 6.3: Google Sheets para Looker Studio (la cuenta de servicio de
   Search Console necesitaría además el alcance de Sheets y Drive).

<a id="d148"></a>
148. **Un servidor MCP de solo lectura para preguntarle al crawler desde
   Claude Code o Cowork** (Ola 6.5 de `docs/PLAN_MEJOR_CRAWLER.md`).
   `mcp_servidor/` corre en el ordenador de quien lo usa (stdio), con el token
   de la API (decisión 134), y expone ocho herramientas marcadas como de solo
   lectura (`read_only_hint`): clientes, rastreos, informe, problemas por
   prioridad, URLs con los filtros del Explorador, comparar dos censos, alertas
   con estado y Search Console. Nada que lance, borre o cambie: la escritura,
   si llega, irá con un `simular` o `estimate` previo como dice el plan.
   - **La lógica son funciones normales** (`herramientas.py`) con el cliente
     HTTP inyectado, que se prueban con `httpx.MockTransport` sin red ni SDK;
     `servidor.py` solo las registra. Un cliente se nombra por id, slug o
     nombre, y si no existe la respuesta dice cuáles hay.
   - **SDK fijado** (`mcp==2.3.0`): en la 2.x `FastMCP` pasó a `MCPServer` y las
     anotaciones van en snake_case; con la versión abierta, el próximo cambio de
     API rompería el servidor sin avisar. No trae `httpx`, que se declara aparte.
   Probado de punta a punta: el servidor arrancado por stdio con el cliente del
   SDK, las ocho herramientas listadas y `clientes` y `problemas` respondiendo
   con los datos de producción.

<a id="d151"></a>
151. **El servidor MCP en remoto, en `/mcp` de producción, con OAuth; y con
   escritura: lanzar, cancelar y programar rastreos** (amplía la 148).
   claude.ai, Cowork, Claude Desktop y la app del móvil solo conectan con un
   servidor MCP remoto (un «conector personalizado»: URL https + OAuth); el de
   stdio de la 148 solo valía para Claude Code en un ordenador con el repo.
   Ahora la API sirve el mismo servidor en `https://<produccion>/mcp`, y basta
   esa URL en cualquier cliente y en cualquier dispositivo.
   - **Dentro de la API y no como servicio aparte**: mismo contenedor, mismo
     dominio y mismo Traefik, sin tocar `docker-compose.vps.yml`. Un ASGI por
     fuera de todo (`api/mcp_despacho.py`) manda `/mcp`, `/authorize`,
     `/token`, `/register`, `/revoke`, `/oauth/entrar` y
     `/.well-known/oauth-*` al servidor MCP; el resto sigue a FastAPI. Sin
     sesiones (`stateless_http`) ni SSE: cada llamada es una petición, y un
     reinicio de la API no deja a nadie con una sesión colgada.
   - **Las herramientas llaman a la API por dentro** (`127.0.0.1:8000`) con
     `API_TOKEN`: lo que ve el asistente es lo que ve la interfaz, con las
     mismas validaciones, sin una segunda copia de las consultas.
   - **OAuth 2.1 con registro dinámico y PKCE** (el servidor de autorización
     del SDK, con `mcp_servidor/remoto.py` de proveedor). Autorizar es escribir
     la clave de acceso de la interfaz (decisión 134), o un clic si el navegador
     ya tiene la sesión de `/entrar`. No hay usuarios ni roles nuevos.
   - **Solo se registran redirecciones de Claude**: `claude.ai`/`claude.com`
     (`/api/mcp/auth_callback`) y `localhost` (Claude Code). Otra aplicación no
     puede conseguir un token aunque alguien pique en un enlace suyo;
     `MCP_REDIRECCIONES_EXTRA` añade URIs exactas.
   - **Tokens en Redis, guardados por su hash**, con la huella de `API_TOKEN`:
     cambiar la clave cierra también todos los conectores. Acceso 8 h, refresco
     90 días y rotado en cada uso. Cada dispositivo o aplicación autoriza el
     suyo y conviven sin límite; un conector de claude.ai vale ya para la web,
     el escritorio, el móvil y Cowork de esa cuenta.
   - **Escritura en dos pasos** (`mcp_servidor/acciones.py`), como pedía la
     148: `lanzar_rastreo` (también canario, el de `lanzar_job.py`: lo comprueba
     un test), `cancelar_rastreo`, `programar_rastreo` y `editar_programacion`
     (pausar, reanudar, cambiar el cuándo). Sin `confirmar` no escriben nada y
     devuelven lo que harían: cliente, perfil, semillas, configuración efectiva
     y **lo que ya está rastreando producción**; con `confirmar=true` lo hacen
     por los mismos endpoints que la interfaz. Las de escritura no llevan
     `read_only_hint` (cancelar va como destructiva), así que Claude pide
     permiso además. Avisa de las claves de config que la API tira (33).
     **Borrar no está**: ni rastreos ni programaciones (se pausan).
   - **Toda la data**: `endpoints` lista los GET de la API (de su OpenAPI) y
     `consultar` hace cualquiera de ellos, menos las descargas (CSV, Excel,
     backup, HTML) y con un tope de 60.000 caracteres que dice cómo pedir menos.
   - Se apaga con `MCP_REMOTO=0`, y sin `API_TOKEN` no arranca.
   Probado de punta a punta con el cliente oficial del SDK contra uvicorn: 401,
   descubrimiento, registro, autorizar con la clave, token, las 17
   herramientas, y un lanzamiento simulado que no escribe nada.

<a id="d189"></a>
189. **La ficha de una URL trae la última inspección de Google de esa URL
   (`google_inspeccion`), con su fecha, y no solo cuando algo no cuadra**
   (revisión de UX del 25-oct, 2.7; sobre la 144). La canonical que elige
   Google solo llegaba a la interfaz por los avisos
   `google_elige_otro_canonical` y `google_tiene_canonical_antiguo`, que salen
   cuando no coincide: en las 3.727 URLs de Lopesan inspeccionadas, 155. En las
   demás no se podía decir «Google acepta tu canonical» aunque estuviera
   guardado. Se lee de `gsc_inspecciones` por cliente y `url_clave`
   (`shared.gsc.clave_de_url`), la última sin error:
   - **Con su fecha y la de la última visita de Google**: es una muestra
     rotatoria del cliente, no del censo, y `gsc_inspecciones` guarda una fila
     por URL que se reescribe en cada inspección; la ficha dice «según la
     inspección de Google del 19 oct (su última visita: 11 oct)».
   - **Solo se compara con el censo si la inspección cae en su ventana**
     (`en_ventana_del_censo`): la de la 144, 30 días antes y 14 después,
     `shared.gsc.ventana_de_inspeccion`, la misma que usan los avisos (antes
     las dos constantes vivían en `analysis/`). Fuera de ella, en un censo
     viejo o con la URL reinspeccionada después, comparar dos fechas no dice
     nada y la tarjeta contradiría a los avisos de la misma ficha: las
     comparaciones con el censo salen None y la ficha lo dice.
   - **`google_acepta`, `visto_igual_que_censo` y `google_igual_que_censo` se
     calculan en la API con `shared.indexabilidad.misma_url`**, la regla del
     análisis: una copia en JavaScript acabaría discrepando del aviso. Si
     Google vio declarada otra canonical que la del censo, su elección es de
     una versión anterior y la ficha no dice que Google ignore la del censo; y
     si la que elige ya es la del censo (Lopesan, 
     `oferta-om-spa-costa-meloneras-cumpleanos`: vio la de
     `webserver-lopesan-prd.lfr.cloud` y elige la de www), dice que ya la
     sigue, no que «cambiará con la próxima visita». Sin eso, «puede
     cambiar», no «cambiará». Es «la del censo» y no «la de hoy»: el censo
     puede ser viejo (revisión de la 2.7).
   - **None no es «Google no la tiene»**: sin cliente, sin inspección o solo
     con inspecciones fallidas. La ficha dice que no está inspeccionada solo
     si el censo tiene inspecciones (`gsc_resumen.inspeccion`); contra una API
     sin el campo, usa los detalles de los avisos y, sin ellos, «no consta».
   - Best-effort: el `try` cubre la función entera; un fallo deja la ficha
     igual, sin el campo. Test: `tests/test_ficha_url_google.py`.

<a id="d194"></a>
194. **El alta de un cliente pide nombre y dominio, crea sola un perfil «Sitio principal» y acaba en el siguiente paso**

   **Problema.** Dar de alta un cliente abría el formulario entero de
   Configuración (JSON incluido) y, al guardar, se quedaba ahí, sin perfil y
   sin decir qué hacer después (revisión de UX del 25-oct, V37). Para el
   primer censo hacía falta además crear un perfil a mano.

   **Decisión.** `POST /api/clients` acepta `perfil_principal: true`: en la
   misma transacción crea el perfil «Sitio principal» con la home del primer
   dominio como semilla (`https://<dominio>/`; los dominios ya se guardan sin
   esquema ni ruta). Sin dominio no crea nada. La interfaz pide solo nombre y
   dominio y abre la ficha en Estado con «Siguiente paso: el primer censo»:
   «Lanzar canario» y «Lanzar censo» abren el modal de lanzar ya con el
   cliente y el perfil (el del canario destaca su botón y explica que son 250
   páginas que no cuentan en la evolución; nada se lanza sin confirmarlo en el
   modal) y «Programar cada semana» abre la programación del perfil (decisión
   193), lunes a las 03:00 de Madrid. El bloque sale mientras el cliente no
   tenga ningún censo ni rastreo en marcha y tenga un perfil propio activo.
   Test: `tests/test_programaciones_perfil.py` (alta con y sin perfil).

<a id="d196"></a>
196. **La configuración se edita con el mismo formulario en el cliente, el perfil y el rastreo; lo de la agencia va en Ajustes**

   **Problema.** La configuración del cliente y la del perfil eran JSON a
   mano y el modal de Nuevo rastreo un formulario de siete pestañas que abría
   «Sin cliente», con lo avanzado desplegado y 32 concurrentes (revisión de UX
   del 25-oct, V35 y V36). Nadie veía con qué saldría el próximo rastreo ni de
   dónde venía cada valor, y el modal mantenía a mano una lista de claves que
   «solo se mandan si se tocan» (sondas, WordPress, topes) para no pisar lo
   heredado. El bot de Slack de toda la agencia se configuraba dentro de cada
   cliente, igual que las llaves de Search Console.

   **Decisión.**

   - Un solo formulario (`CAMPOS_CONFIG` en `frontend/app.js`, una plantilla
     `tpl-config-niveles`) para los tres niveles. Cada nivel guarda solo lo que
     fija (`propia`); lo de arriba llega resuelto por la API con su procedencia
     y cada campo dice «del cliente», «del perfil», «de serie» o «cambiado
     aquí» con «volver al heredado». **Escribir lo mismo que lo heredado no lo
     fija**: así un cambio del cliente sigue llegando al perfil. Al lanzar se
     manda solo lo de ese rastreo, que `create_job` pone encima de lo heredado
     (ya lo hacía con `exclude_unset`). El JSON queda en «Avanzado» para lo que
     el formulario no tiene (plantillas, segmentos, marcas…).
   - Los límites y las opciones del formulario son los de `JobConfig`:
     `tests/test_config_niveles_frontend.py` los compara campo a campo con el
     modelo. Una cabecera, cookie o tope heredado no se puede quitar desde un
     nivel de abajo (la API no admite ahí un `null`): el formulario lo dice en
     vez de perderlo en silencio. La contraseña del Basic, solo en un rastreo
     (decisión 83).
   - `config-resuelta` devuelve además `origen` (ruta → capa; un `null` cuenta
     como decisión de esa capa), `serie`, `avisos` y las programaciones del
     perfil con `config_propia` (decisión 193). `POST /api/clients/config-resuelta`
     resuelve un **borrador** de un nivel sin guardarlo y lo valida como al
     guardar: es el bloque «Así rastreará» mientras se edita.
   - **Avisos solo con lo que se sabe** (`api/avisos_config.py`): el del WAF
     sale si un rastreo anterior **del mismo sitio** se paró por un bloqueo
     (`finish_reason`) o tuvo respuestas de muro (`health.por_clase`: WAF,
     captcha o 429; no las binarias), con sus
     cifras y la concurrencia de entonces frente a la de ahora; parado y con la
     misma o más concurrencia es «peligro», muros sueltos un aviso. Sin ese
     rastro no se dice nada del WAF. El resto sale de la propia configuración
     (robots ignorado, se presenta como un buscador, sin tope de URLs…).
   - Nuevo rastreo trae el cliente y su perfil principal y la configuración
     plegada; sin cliente, un aviso y el botón «Iniciar sin cliente».
   - Configuración del cliente en secciones plegables con índice, cada una con
     su resumen y su «Guardar» (guardar los datos no se lleva un borrador de la
     configuración).
   - **Ajustes de la agencia** (`#/ajustes`, menú «…»): el bot de Slack, las
     llaves de Search Console (se suben aquí; en el cliente se elige cuál y la
     propiedad), si están las claves de los modelos y el tope mensual
     (`GET /api/ajustes`: solo booleanos y la cifra del tope), importar backup
     y el tema. Ningún secreto se enseña ni se guarda en el navegador; los de
     la API siguen en el entorno o en `SECRETOS_DIR`.

   **Lo que no cambia.** El esquema de la base de datos y lo que se guarda en
   `clients.config_defecto`, `crawl_profiles.config` y `jobs.config`. Las
   claves de Gemini del análisis semántico siguen donde estaban (en la base de
   datos, como antes de esta decisión); Ajustes solo dice cuántas hay.

   **Revisión del 26-oct** (revisor independiente, antes de subir):

   - «Del mismo sitio» = alguna semilla del rastreo anterior en los hosts del
     próximo (las semillas del perfil o, sin perfil, los dominios del cliente y
     las semillas de sus perfiles propios; `www.` no cuenta). Antes miraba todo
     el cliente, y el bloqueo del sitio de un competidor salía como «peligro»
     en el sitio principal (reglas 67 y 70). Los rastreos limpios del mismo
     sitio que vinieron después se dicen y rebajan el aviso (a «info» si uno
     salió con la misma o más concurrencia que la de ahora), y «bajar la
     concurrencia» ya no se propone si ahora sale más despacio que entonces.
   - `config-resuelta` trae `origen_por_clave`: cabeceras, cookies, topes y
     reglas con nombre se fusionan clave a clave, y atribuirlas enteras a la
     última capa mandaba a quitar al perfil una cabecera que era del cliente.
   - El JSON de «Avanzado» se aplica al dejar de escribir y antes de guardar o
     lanzar; con un botón aparte, lo escrito sin pulsarlo se perdía en silencio
     y la interfaz daba el guardado por bueno. Un JSON a medias bloquea el
     guardado con su error.
   - En los enteros el punto solo es de miles si lo parece (`5.000`): «1.5» en
     el máximo de URLs era 15 sin aviso.
   - El «Nuevo rastreo» visible de la ficha del cliente trae también el perfil
     principal (solo lo hacía el atajo «n»).

   Tests: `tests/test_config_niveles.py` (API: origen y origen por clave,
   borrador, avisos del mismo sitio, ajustes sin secretos) y
   `tests/test_config_niveles_frontend.py`.

<a id="d199"></a>
199. **Cada entrada de la barra del Explorador cuenta lo mismo que abre, con «0» cuando es cero, y dos cifras distintas llevan nombres distintos**

   **Problema.** La barra lateral del Explorador decía «HTML 3.772» y la
   tarjeta del Resumen «3.731 páginas HTML», y la tarjeta abría la entrada de
   3.772 (revisión de UX del 25-oct, V30). No era un error de cuenta: la
   tarjeta cuenta las HTML internas que responden 2xx (`/stats.paginas_html`) y
   la barra todo lo que responde HTML, también los 4xx (en Lopesan, justo los
   41 de diferencia). Además, JavaScript, CSS, 5xx, Indexables, No indexables,
   Bloqueadas y todos los ámbitos SEO salían sin cifra: no se distinguía «no
   hay ninguna» de «no se ha contado».

   **Decisión.**

   - Dos entradas con nombre propio: **«Páginas HTML»** (internas, HTML, 2xx:
     la cifra de la tarjeta, que ahora abre esta) y **«Todo el HTML»** (la de
     antes, con un `title` que dice que incluye las que dan error).
     Indexables y No indexables llevan un `title` que dice que son de
     cualquier tipo (PDF, redirecciones), porque no cuadran con la tarjeta
     «Indexables», que es solo de páginas HTML.
   - Lo que `/stats` agrupa del censo entero (tipo de recurso, grupo de
     estado) da «0» cuando no sale: un `GROUP BY` completo no omite nada que
     exista.
   - Lo demás es el `total` de `GET /urls` con el mismo filtro que aplica la
     entrada (`page_size=1`): una consulta cada vez, en orden y en segundo
     plano, guardada por censo y por estado y versión del análisis: con el
     rastreo sin terminar se recuenta cada vez que se entra, y al acabar el
     análisis se tira lo contado a medias (y el resumen de problemas del que
     salen los ámbitos) antes de recontar. En un ámbito SEO la cifra son las
     **páginas** de esa vista con algún problema del ámbito (`issue_type` con
     todos sus tipos), no la suma de incidencias: una página con dos problemas
     del ámbito cuenta una vez. Y como la entrada tiene que abrir lo que cuenta,
     **pulsar un ámbito abre «Con algún problema de este ámbito»** («Títulos
     307» abre 307, no las 3.772 páginas HTML); «Todas las páginas» queda a un
     clic en los chips. Una URL guardada con la pestaña y sin `problema` sigue
     abriendo todas. Un ámbito sin ningún tipo en el resumen de problemas es 0
     sin preguntar, pero solo si el resumen trae algo (vacío puede ser que
     falló). Si una consulta falla, la entrada se queda sin cifra: nunca un 0
     que no se ha contado.
   - **Un censo sin analizar** (cancelado o fallido antes del análisis, o aún
     en curso: sin `analisis_version` o con el estado sin cerrar) no tiene
     problemas ni indexabilidad calculados, que no es «0»: los ámbitos,
     Indexables y No indexables van sin cifra ni punto de color, el ámbito abre
     todas las páginas y en lugar de «Ningún problema de este ámbito» dice que
     no se analizó. El mismo criterio que la tarjeta «sin analizar todavía» del
     Resumen. «Externas» sigue sin cifra con una API sin `/links/resumen`
     (revisión 2.8).
   - Con Search Console en el censo, las columnas de serie llevan los clics de
     Google detrás de indexable y enlaces entrantes (`columnasPorDefecto`); sin
     él no se añade una columna vacía.

   Tests: `tests/test_movil_frontend.py` (la tarjeta y su entrada con la misma
   cifra, un ámbito abre lo que cuenta, un censo sin analizar sin cifra en los
   ámbitos, contadores en todas las entradas, columnas por defecto).
<a id="d200"></a>
200. **Lo editado en la ficha del cliente se guarda con una barra fija y salir con cambios pregunta; borrar va dentro de «⋯»**

   **Problema.** La pestaña Configuración tenía un «Guardar» por sección (la
   configuración, los datos, las integraciones, las reglas), cada uno al final
   de su bloque, y nada avisaba de lo que quedaba sin guardar: cambiar un
   umbral, irse a Producción y volver era perderlo sin saberlo. Guardar una
   sección recargaba la ficha y se llevaba por delante lo cambiado en otra. Las
   «×» de borrar perfiles y programaciones estaban junto a «Lanzar» y
   «Pausar». Y el botón de aceptar de todas las preguntas sin texto que
   escribir («Eliminar», «Restablecer», «Cancelar el rastreo») salía
   desactivado: su `:disabled` daba `''` y Alpine pone el atributo con
   cualquier valor que no sea `false`, `null` o `undefined` (revisión de UX
   del 25-oct, 3.5).

   **Decisión.**

   - Una barra fija abajo, «Cambios sin guardar en … · Descartar · Guardar»,
     aparece en cuanto algo de la ficha del cliente (cómo se rastrea, datos,
     integraciones, reglas de alertas) o de Ajustes (un token de Slack pegado)
     difiere de lo guardado. Su «Guardar» guarda todo lo pendiente: primero las
     reglas y luego lo del cliente en un solo PATCH. Los botones por sección se
     quitan. Escribir en un campo el mismo número que había («20» frente a 20)
     no cuenta como cambio.
   - Salir de la ficha (otro cliente u otra vista, por enlace o con atrás)
     con cambios pregunta antes y, si no se acepta, se queda donde estaba;
     recargar o cerrar la pestaña usa el aviso del navegador. Cambiar de
     pestaña dentro del mismo cliente no pregunta: los formularios siguen ahí y
     la barra también. El modal del perfil pregunta al cerrarse con cambios y
     su pie dice «Cambios sin guardar».
   - Eliminar un perfil, una programación o una regla propia va dentro de
     «⋯», con lo que no se borra dicho al lado.
   - El pie de los modales queda fijo (Lanzar y Guardar a la vista) y el
     «Nuevo rastreo» de arriba solo es el botón verde donde la vista no tiene
     uno propio (Nuevo cliente, Nueva programación, Informe para el cliente).
   - El `:disabled` de las preguntas se fuerza a booleano.
   - Recargar la ficha del mismo cliente (guardar o eliminar un perfil, lanzar
     un rastreo desde la ficha, guardar o quitar Slack, guardar una sección)
     conserva lo editado y sin guardar en los datos y en las integraciones; solo
     se toma del servidor lo que se acaba de guardar (`abrirCliente(..., {
     guardado })`). Las reglas y «cómo se rastrea» ya no se recargaban.
   - El error de validación de las reglas nombra la regla en llano («Páginas
     que ahora responden con error»: falta el número), no por su clave.

   Tests: `tests/test_botones_jerga_frontend.py` (cambios por sección, salir
   con y sin aceptar, el perfil, la recarga de la ficha con cambios a medias,
   el error de las reglas, la expresión del `:disabled` tal cual está en
   `index.html`, y los formatos de número).

<a id="d201"></a>
201. **Una escala de letra de cuatro tamaños (ninguno por debajo de 12 px) y los estilos en línea repetidos pasan a clases que dicen lo mismo**

   **Problema.** Con `html` a 14px, el frontend usaba 39 tamaños de letra
   distintos (de `.6rem` a `2.5rem`): `.8rem` eran 11,2 px, las insignias y
   las cabeceras de tabla bajaban a 9 px, el texto de los ejes de la evolución
   a 7 px pintado y `code`/`pre` a 11,4 px por una rareza de Chromium (una
   familia `monospace` sola encoge el texto a 13/16). Leer «qué decide Google
   / cómo se arregla» costaba (V43). Y `index.html` tenía 922 atributos
   `style="..."` fijos, la mayoría repetidos (iconos de 14 px, filas flex,
   márgenes), que nadie podía cambiar de una vez (UX-21).

   **Decisión.**
   - Cuatro tamaños de texto en `:root`: `--fs-sm` 12 px (notas, insignias,
     cabeceras), `--fs-base` 14 px, `--fs-md` 16 px y `--fs-lg` 20 px; más dos
     excepciones declaradas que no son texto corrido: `--fs-cifra` 24 px (la
     cifra grande de una tarjeta) y `--fs-icono` 32 px (el icono de un estado
     vacío y la nota del informe). Todo `font-size` del frontend usa una de
     ellas, salvo la raíz; cada valor viejo va al más cercano (≤13 px → 12,
     <15 → 14, <18 → 16, <22 → 20), y `.stat-value` a la cifra.
   - `code`, `pre`, `kbd` y `samp` llevan otra familia delante de `monospace`;
     el texto de un SVG cuenta por cómo se pinta (las columnas de la evolución
     pasan a 320 px como poco para no encoger la gráfica, el anillo de la
     puntuación a 14 unidades), y la barra lateral del Explorador pasa de 170 a
     190 px para que sus entradas no partan.
   - Los estilos en línea repetidos pasan a clases con el mismo contenido:
     utilidades nombradas por su valor (`mt-35` = margin-top .35rem, `px-75`,
     `w-5rem`, `ico-14`, `fs-sm`, `c-red`, `filete-orange`) y componentes con
     nombre propio cuando se repetía la combinación entera (`btn-rojo`,
     `campo-simple`, `caja-codigo`, `fila-extremos`). Llevan `!important`
     porque sustituyen a un estilo en línea y deben ganar igual que él; las de
     `display` van sin él, para que `x-show` pueda ocultar. Los `:style` que
     dependen de datos y los fijos de una sola vez se quedan: 922 → 108.
   - Se comprobó que el aspecto no cambia salvo por la escala comparando el
     estilo calculado de cada elemento, antes y después, en las 21 rutas de la
     revisión sobre producción: solo cambian lo que deriva del tamaño de letra
     (`letter-spacing` y huecos en `em`, márgenes `auto`) y un
     `justify-content: flex-end` que la clase `justify-end` ya pedía en
     Configuración sin que existiera. Esa comparación miraba solo lo visible
     y se le escapó una: `.mono` (de antes, sin `!important`) perdía contra
     `input[type="text"] {font-family: inherit}`, y la cron de los dos
     modales de programar, la propiedad de GSC, el canal de Slack y la
     credencial dejaron de ser monoespaciados. Ahora `.mono` lleva
     `!important` como el resto, y el test comprueba ejecutando que cada
     utilidad con `!important` gana en el elemento que la lleva, oculto o no
     (salvo en las tablas que en móvil pasan a tarjetas, que mandan a
     propósito).
   - Lo que la escala cortaba o dejaba fuera: las etiquetas de «Qué arreglar
     primero» pasan de 130 px en una línea a 150 px en hasta dos («Error datos
     estructurados» salía con puntos suspensivos), y los `h5`/`h6` de un
     informe en markdown, que heredaban `.83em`/`.67em` del navegador (10 y 8
     px), van a 12 px como el `h4`.
   - Un solo ayudante de porcentaje: `fmt.pct` (se quita `pctEs`; con un
     decimal fijo, «2,2 %», y «—» si no hay dato).

   Tests: `tests/test_escala_letra_frontend.py` (21 vistas a 1440 y 390 px en
   los dos temas: ningún texto visible calcula menos de 12 px, el de los SVG
   con su escala; cada utilidad con `!important` gana donde se usa; titulares
   de markdown y etiquetas largas de las barras; todo `font-size` usa la escala; los estilos en línea fijos
   por debajo de la mitad y sus clases definidas; un solo ayudante de
   porcentaje).


<a id="d202"></a>
202. **Las cifras de errores cuentan páginas en todas partes, y Problemas pinta una fila por tipo con el desglose de gravedad**

   **Problema.** La auditoría independiente del 27-oct encontró cifras que no
   cuadraban entre vistas del mismo censo. La portada, la tabla y la gráfica
   del Historial decían «Errores 85» (incidencias de gravedad error) y la
   tarjeta del censo y el Estado del cliente «72 páginas con error» (V10). El
   contador de la pestaña Problemas era 11.681, todas las incidencias con las
   info (V16). Problemas sacaba dos filas por tipo («Canibalización por
   búsqueda» 39 aviso y 157 info, V14). La barra lateral de URLs decía
   «Indexables 3.407» (contaba PDF e imágenes) y la tarjeta «3.216» (3.4). Y
   en «Qué arreglar primero» la barra medía la prioridad con las páginas al
   lado: 10 páginas llenaban la barra y 358 casi no se veían.

   **Decisión.**
   - `CensoDeSerie` (portada y `/clients/{id}/serie`) lleva
     `paginas_con_error`: páginas distintas con algún error, la misma consulta
     que `/stats`. `errores` sigue siendo incidencias. La tendencia del panel
     (`tendencias_de_clientes`) pasa a `urls_con_error` (y lo dice en
     `metrica`); cada serie de la evolución lleva también
     `tendencia_paginas_error`. La gráfica del Historial pinta
     `urls_con_error` como «Páginas con error». El MCP devuelve
     `paginas_con_error` y `tendencia_paginas_con_error`.
   - Con una API anterior, la ficha del cliente saca las páginas de la
     evolución (`urls_con_error` del mismo rastreo, la misma cuenta) y la
     portada dice «errores (incidencias)» en la cabecera: no llama páginas a
     lo que son incidencias.
   - `/issues/resumen` lleva `paginas_tipo`: páginas distintas del tipo en
     cualquier gravedad. La interfaz junta las filas de un tipo en una
     (`filasPorTipo`) con la gravedad más alta y «39 aviso · 157 info»; las
     páginas del tipo solo si están todas sus gravedades y la API las trae.
     Sumar las de cada gravedad contaría dos veces la página que tenga las dos,
     y eso no se afirma: sin el dato, la fila dice solo el desglose. Lo mismo
     en el desplegable de problemas de URLs (cada tipo en el grupo de su
     gravedad más alta) y en «Qué arreglar primero».
   - El contador de la pestaña Problemas son los tipos de error o aviso (los
     que hay que mirar), sin los info.
   - «Páginas indexables» y «Páginas no indexables» de la barra de URLs
     cuentan y abren las páginas HTML internas con 2xx, las de la tarjeta (que
     abre «no indexables» y antes llevaba a una lista con las redirecciones y
     los errores dentro).
   - «Qué arreglar primero» es lo primero del Resumen, seguido de lo que ha
     cambiado y de Google (el orden de la propuesta), con cinco filas como el
     Estado del cliente y en la misma lista numerada: sin barra.

   Tests: `tests/test_pulido_frontend.py` (filas por tipo, páginas que no se
   suman, contador, cifras de páginas con su respaldo), `tests/test_stats_api.py`
   (`paginas_tipo`), `tests/test_evolucion_api.py` (portada y serie con dos
   errores en la misma página, tendencia de páginas).

<a id="d203"></a>
203. **El tipo del rastreo se elige al lanzarlo (prueba de serie sin perfil), y la portada dice «topado en N URLs»**

   **Problema.** Un rastreo sin marcar contaba como censo salvo que el nombre
   llevara una pista (`tipo_de_rastreo`), y el modal de lanzar no pedía tipo:
   la portada podía juzgar a un cliente por una prueba (V12, UX-07). Un censo
   que se paraba en su tope salía en la portada como si fuera el sitio entero.

   **Decisión.** `JobCreate` acepta `tipo` (`censo`, `prueba`, `canario`) y
   lo guarda; sin él, como antes (los scripts y las programaciones no lo
   mandan). El modal Nuevo rastreo siempre lleva uno marcado y a la vista:
   censo si hay perfil, prueba si no; «Lanzar canario» manda canario. Con una
   API anterior, que ignora el campo, se marca después con el `PATCH` que ya
   existía. `CensoDeSerie.topado` es el `max_urls` de la config si el rastreo
   acabó por tope (`MOTIVOS_TRUNCADO`); sin tope en la config, `null` (no se
   inventa la cifra), y la portada lo dice en naranja bajo la cifra de errores.

   Lo que queda en datos y no en código: `lopesan-remedicion` tiene su prueba
   sin marcar y sigue contando como censo hasta que alguien la marque.

   Tests: `tests/test_clientes.py` (el tipo al crear),
   `tests/test_evolucion_api.py` (topado con y sin tope),
   `tests/test_pulido_frontend.py` (el tipo de serie del modal).

<a id="d204"></a>
204. **En la pestaña Alertas, las alertas iguales del cliente se agrupan en pantalla y las informativas van a «Cambios a favor»**

   **Problema.** En Cerradas salían por separado «Páginas que ya no se
   encuentran» y «se han alejado de la home» del 19 y del 21 oct (dos series,
   la misma regla), y la de tipo info iba mezclada con el resto (V08, 1.12).

   **Decisión.** Solo presentación: cada incidente sigue siendo suyo
   (decisión 139). La interfaz agrupa por `clave` (`agruparIncidentes`): un
   grupo con su gravedad más alta, el titular de la más reciente, «Vista en
   los censos del 19 oct y del 21 oct», el detalle de cierre de cada una y un
   botón «Ver las N páginas» (Comparar del último censo que la vio, con las
   páginas que guarda la alerta). Marcar como vista, volver a abierta y
   silenciar se aplican a todas las vivas del grupo. Las de gravedad info van
   en una sección «Cambios a favor».

   Tests: `tests/test_pulido_frontend.py` (agrupado, gravedad, info aparte).

<a id="d205"></a>
205. **«No incluida en el sitemap» sale de los totales de «incidencias nuevas y resueltas» del Estado, del informe, de Slack y de Problemas si cualquiera de los dos censos leyó sus sitemaps a medias**

   **Problema.** El V13 (revisión de UX del 25-oct, 3.6) solo estaba cerrado
   en Problemas, y solo mirando el censo actual. El Estado de Lopesan decía
   «240 incidencias nuevas y 775 resueltas» sin descontar ni avisar, con 370
   de 396 ficheros de sitemap vacíos; el informe y el aviso de Slack, lo
   mismo. Y si el parcial era el censo de referencia, sus «resueltas» de
   `not_in_sitemap` tampoco se pueden afirmar y se contaban igual.

   **Decisión.** Una regla en `shared/sitemaps_lectura.py`
   (`lectura_dudosa`: lectura parcial, un fichero que no responde 200 o uno
   que responde 200 sin un sitemap reconocible), la misma que
   `resumenSitemaps().dudoso` del frontend; un test ejecuta las dos sobre los
   mismos casos. Si cualquiera de los dos censos es dudoso, los totales
   (`nuevas`, `resueltas`, `siguen`) no cuentan los tipos de sitemap
   (`sin_tipos_de_sitemap`) y se dice: «Sin contar «No incluida en el
   sitemap»: no se leyeron todos los sitemaps y su diferencia no se puede
   afirmar». El informe (`comparacion.incidencias.sin_sitemap`) y el aviso de
   «rastreo terminado» y el resumen semanal salen de
   `_resumen_de_rastreo`; un censo de referencia que ya no está no se puede
   comprobar y tampoco se afirma. En la interfaz, el Estado pide el censo de
   referencia (`refDudaSitemaps`) y no pinta la línea hasta saberlo;
   Problemas lo guarda en `versionAnterior.dudaSitemaps`; el informe usa el
   campo de la API y, con una API anterior, el desglose por tipo del censo
   cargado. No cambia nada guardado ni el análisis: los avisos siguen ahí,
   como «no concluyente».

   Tests: `tests/test_sitemaps_lectura.py` (la regla en Python y en el
   frontend, el informe y el aviso con SQLite) y
   `tests/test_revision_final_frontend.py` (Estado con Alpine en Chromium,
   Problemas con el anterior dudoso, informe con una API anterior).
