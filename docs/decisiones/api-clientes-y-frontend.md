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
   - **Dos guardias, y solo dos.** Un rastreo en marcha (`pending`, `running`,
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
