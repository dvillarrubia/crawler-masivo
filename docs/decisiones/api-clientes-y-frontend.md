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
