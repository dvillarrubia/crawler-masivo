import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, Float, Text, DateTime,
    ForeignKey, Index, JSON, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from shared.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Clientes y perfiles de rastreo (#36, F1)
# ---------------------------------------------------------------------------
class Client(Base):
    """El cliente como entidad, en vez de un texto libre en cada job.

    Antes `jobs.client_id` era lo que se escribiera en el modal: una errata
    creaba otro cliente y no habia donde guardar nada que fuera DEL cliente y
    no de un rastreo concreto (sus dominios, su configuracion por defecto, sus
    integraciones con GSC o DataForSEO).

    `config_defecto` es un dict PARCIAL de `JobConfig`: solo lo que el cliente
    fija. Guardar el `JobConfig` entero rellenaria todos los valores por
    defecto y, al heredar, pisaria lo que el perfil o el job quisieran cambiar.

    `integraciones` no guarda NINGUN secreto (decision 83): la API no tiene
    autenticacion, asi que lo que se guarda aqui lo puede leer cualquiera que
    llegue al puerto. Se guarda el tipo, los parametros publicos y el NOMBRE de
    la variable de entorno que tiene la clave.
    """

    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nombre = Column(String(256), nullable=False)
    # Lo que casa dos instalaciones (backup/importacion) y lo que se escribia
    # antes como `client_id`. Unico: dos clientes con el mismo slug son una
    # errata, no dos clientes.
    slug = Column(String(128), nullable=False, unique=True, index=True)
    dominios = Column(JSON, nullable=False, default=list)
    zona_horaria = Column(String(64), nullable=True, default="Europe/Madrid")
    config_defecto = Column(JSON, nullable=False, default=dict)
    integraciones = Column(JSON, nullable=False, default=list)
    # Capa de decisiones (#7): `{"presupuesto_mensual_usd": 5.0}`. NULL o sin
    # la clave = sin tope propio del cliente (sigue mandando el global,
    # `ENRICHMENT_MAX_USD_MES`). Es un presupuesto, no un secreto: la clave de
    # OpenRouter va en el entorno (decision 84).
    decisiones = Column(JSON, nullable=True)
    notas = Column(Text, nullable=True)
    # Vistas guardadas del Explorador (#131): `[{id, nombre, consulta, creada}]`.
    # `consulta` es la parte de despues del «?» de `#/rastreos/<id>/explorador`,
    # asi que una vista vale para cualquier censo del cliente: es como se repite
    # el mismo analisis cada mes.
    vistas_explorador = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    perfiles = relationship(
        "CrawlProfile", back_populates="cliente",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class CrawlProfile(Base):
    """Un rastreo de un cliente: el sitio principal, el blog, un competidor.

    En F1 es «que semillas y que configuracion»: lanzar un job desde un perfil
    hereda cliente -> perfil -> lo que se cambie en el modal, y lo resuelto se
    copia en `jobs.config`. La programacion, los umbrales de alerta y el modo
    incremental son de F2/F4 y no estan aqui todavia: anadir columnas que
    nadie lee invita a creer que hacen algo.
    """

    __tablename__ = "crawl_profiles"
    __table_args__ = (
        UniqueConstraint("cliente_id", "nombre", name="uq_profile_cliente_nombre"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # `cliente_id` y no `client_id`: en este esquema `client_id` es el texto
    # libre de siempre (jobs, programaciones) y `cliente_id` la FK de verdad.
    # Llamar igual a las dos cosas es como se acaba filtrando por la que no es.
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    nombre = Column(String(256), nullable=False)
    # propio | competidor. Un competidor no abrira incidentes en F2: sus
    # cambios son informacion, no problemas.
    rol = Column(String(16), nullable=False, default="propio")
    seeds = Column(JSON, nullable=False, default=list)
    config = Column(JSON, nullable=False, default=dict)
    notas = Column(Text, nullable=True)
    activo = Column(Boolean, nullable=False, default=True)
    # `use_alter`: jobs.profile_id apunta aqui y esto apunta a jobs. Sin el,
    # create_all no sabe en que orden crear las dos tablas.
    last_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL", use_alter=True,
                   name="fk_profiles_last_job"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    cliente = relationship("Client", back_populates="perfiles")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class Programacion(Base):
    """Un rastreo que se repite: cuando toca y que lanzar.

    Lleva sus propias `seeds` y `config` en vez de apuntar a
    `projects/<cliente>/config.json`: esos ficheros viven en la maquina de
    quien lanza los rastreos y la imagen del worker no los tiene. Asi la
    programacion es autosuficiente y se crea con el mismo cuerpo que un
    `POST /api/jobs`.
    """

    __tablename__ = "programaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nombre = Column(String(512), nullable=False)
    client_id = Column(String(128), nullable=True, index=True)
    # El cliente y el perfil, si se conocen. La programacion sigue llevando
    # sus propias `seeds` y `config` (autosuficiente, ver arriba): esto solo
    # dice de quien es, para que los jobs que dispare salgan colgados de el.
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    profile_id = Column(
        UUID(as_uuid=True), ForeignKey("crawl_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    seeds = Column(JSON, nullable=False)
    config = Column(JSON, nullable=True)

    # Expresion de cinco campos (`m h dom mes dsem`) y la zona en la que se
    # lee: un "todos los lunes a las 3:00" sin zona se desplaza una hora en
    # verano y nadie lo nota hasta que falta un censo.
    cron = Column(String(128), nullable=False)
    zona_horaria = Column(String(64), nullable=True)
    activa = Column(Boolean, default=True, nullable=False)

    # En UTC. Se recalcula cada vez que se dispara, y tambien al activar o
    # editar: si se quedara con la de antes, un cambio de horario no tendria
    # efecto hasta el siguiente disparo.
    proxima_ejecucion = Column(DateTime(timezone=True), nullable=True, index=True)
    ultima_ejecucion = Column(DateTime(timezone=True), nullable=True)
    ultimo_job_id = Column(UUID(as_uuid=True), nullable=True)
    # Por que no se disparo la ultima vez que tocaba, si no se disparo. Una
    # programacion que deja de lanzarse sin decir por que es indistinguible de
    # una que nadie miro.
    ultimo_motivo = Column(String(512), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name = Column(String(512), nullable=False)
    # El texto libre de siempre. Se conserva como etiqueta: los scripts lo
    # siguen enviando (`lanzar_job.py`) y los jobs viejos lo tienen como unica
    # pista de su cliente. Lo que manda es `cliente_id`.
    client_id = Column(String(128), nullable=True, index=True)
    # FK de verdad a `clients` (#36). NULL = job sin cliente. Borrar el
    # cliente deja el job sin cliente, no lo borra: es un censo entregado.
    cliente_id = Column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # De que perfil salio, si salio de uno. NULL = rastreo puntual.
    profile_id = Column(
        UUID(as_uuid=True), ForeignKey("crawl_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    owner_id = Column(String(128), nullable=True)
    # Que programacion lo lanzo, si lo lanzo alguna. Es lo que permite
    # compararlo con el anterior de SU serie en vez de con lo ultimo que
    # pasara por ese host (decision 78).
    programacion_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    # censo | canario | prueba. NULL = no se ha dicho: se deduce del nombre
    # (`shared.clientes.tipo_de_rastreo`). Separa en la interfaz los censos
    # que se entregan de las pruebas de 26 URLs (UX-07).
    tipo = Column(String(16), nullable=True)

    # pending | running | analyzing | completed | failed | cancelled | stalled
    # | deleting (un borrado por trozos en curso o a medias, decision 96)
    status = Column(String(20), nullable=False, default="pending", index=True)

    # Por que termino el rastreo: "finished" (frontera agotada, dato completo)
    # o "max_urls_reached" (cortado por el tope, dato PARCIAL). Sin esto, un
    # crawl truncado quedaba como "completed" igual que uno terminado, y no
    # habia forma de saber que el grafo de enlaces —y por tanto el PageRank—
    # se calculo sobre una parte del sitio.
    finish_reason = Column(String(32), nullable=True)
    # Con que version del codigo se hizo este rastreo. Sin esto, comparar dos
    # censos del mismo sitio no puede distinguir "lo cambio el sitio" de "lo
    # cambiamos nosotros" (decision 66).
    crawler_version = Column(String(64), nullable=True)

    # Resultado de la comprobacion automatica de render JS por plantilla. Se
    # rellena al cerrar un rastreo SIN render_js: dice si alguna plantilla
    # esconde enlaces (el grafo y el PageRank no serian fiables) y cuanto
    # contenido carga en cliente. NULL = no se llego a comprobar.
    js_check = Column(JSON, nullable=True)
    # Que cambio respecto al censo anterior del mismo sitio, calculado al
    # cerrar el rastreo. Es la alerta que nadie tiene que ir a buscar.
    comparacion = Column(JSON, nullable=True)
    # Con que version se ANALIZO, que no es lo mismo que con cual se rastreo:
    # un re-analisis cambia las cifras sin que cambie el sitio (decision 66).
    analisis_version = Column(String(64), nullable=True)

    # Resumen del PageRank (ver analysis/pagerank.py): que fraccion acaba en
    # cada tipo de URL (indexable, redireccion, error...) y cuanta se
    # desperdicia en errores. NULL = analisis anterior a este modelo.
    pagerank_resumen = Column(JSON, nullable=True)
    # Salud del rastreo (decision 103): respuestas por clase de
    # `urls.response_class`, cuantas no eran la pagina y la causa si el rastreo
    # se paro o se dio por bloqueado. NULL = rastreo anterior a la columna.
    health = Column(JSON, nullable=True)
    # Observabilidad de la capa de decisiones (#7): llamadas, tokens, coste
    # real, aciertos de cache, 429, latencia y distribucion de respuestas.
    # NULL = nunca se le pregunto nada a un modelo sobre este job, que con
    # este PR es lo normal: nada se dispara solo (decision 84).
    decisiones_resumen = Column(JSON, nullable=True)
    # Enlaces archivados en Parquet (decision 95): filas, apariciones,
    # internos, nofollow internos, fichero, bytes y fecha. NULL = los enlaces
    # estan en `links`. Con valor, `links` no tiene filas de este job.
    enlaces_archivados = Column(JSON, nullable=True)
    # Soft 404 (decision 105): `sondas` las escribe el spider (lo que responde
    # el sitio a una URL que no existe, por host y por plantilla) y `resumen`
    # el analisis. NULL = rastreo anterior a las sondas o con ellas apagadas.
    # Las sondas NO son URLs del censo: no estan en `urls` ni cuentan en nada.
    soft404 = Column(JSON, nullable=True)

    seeds = Column(JSON, nullable=False)  # list of seed URLs
    config = Column(JSON, nullable=False, default=dict)

    total_urls_discovered = Column(Integer, default=0)
    total_urls_crawled = Column(Integer, default=0)
    total_urls_failed = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    urls = relationship("Url", back_populates="job", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
class Url(Base):
    __tablename__ = "urls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    url_hash = Column(String(64), nullable=False)  # sha256 for dedup
    host = Column(String(512))
    path = Column(Text)
    scheme = Column(String(10))

    is_internal = Column(Boolean, default=True)
    crawl_depth = Column(Integer, nullable=True)
    # Clics desde la home (raiz del host y portadas de idioma), calculada por
    # el analisis. NULL = sin camino desde la home. crawl_depth es otra cosa:
    # saltos del rastreador desde las semillas, la que aplica max_depth.
    click_depth = Column(Integer, nullable=True)

    content_type = Column(String(256))
    content_length = Column(BigInteger, nullable=True)
    status_code = Column(Integer, nullable=True)
    status_group = Column(String(10))  # 2xx, 3xx, 4xx, 5xx, timeout, dns_error
    response_time_ms = Column(Float, nullable=True)

    is_html = Column(Boolean, default=False)
    resource_type = Column(String(20))  # html, image, css, js, pdf, other

    redirect_url = Column(Text, nullable=True)

    indexable = Column(Boolean, nullable=True)
    # Directiva `noindex` de la propia pagina (meta robots o X-Robots-Tag),
    # separada de `indexable` —que tambien es False por canonical o por codigo—
    # porque de esto depende si sus enlaces cuentan como entrantes (C3 de #24).
    noindex = Column(Boolean, nullable=True)
    body_hash = Column(String(64), nullable=True)  # for duplicate content detection

    first_seen_at = Column(DateTime(timezone=True), default=_utcnow)
    last_crawled_at = Column(DateTime(timezone=True), default=_utcnow)

    # --- Screaming Frog extended fields ---
    url_length = Column(Integer)                             # character count of URL
    folder_depth = Column(Integer)                           # number of path segments
    word_count = Column(Integer, nullable=True)              # words in body text
    # Palabras del contenido principal (sin menu ni pie). Es la cifra con la
    # que se juzga el thin content: word_count incluye la plantilla.
    content_word_count = Column(Integer, nullable=True)
    # SHA-256 del contenido principal normalizado. `body_hash` es el de los
    # bytes: con un token CSRF o un nonce dentro, dos paginas identicas nunca
    # coincidian y los duplicados exactos no se detectaban en ningun CMS.
    content_hash = Column(String(64), nullable=True, index=True)
    # Bytes del HTML DESCOMPRIMIDO tal como lo sirvio el servidor, antes del
    # render: lo que mide el limite de 2 MB de Googlebot (decision 102). Sin
    # render es `content_length`; con render, `content_length` es el DOM ya
    # montado y esto sale de Navigation Timing (`decodedBodySize`). NULL en
    # los censos anteriores y cuando el navegador no lo da.
    html_bytes_servidos = Column(Integer, nullable=True)
    text_ratio = Column(Float, nullable=True)                # text/HTML ratio percentage
    redirect_type = Column(Integer, nullable=True)           # HTTP redirect code (301, 302, 307, 308)
    status_text = Column(String(64), nullable=True)          # "OK", "Not Found", "Moved Permanently"
    last_modified = Column(String(128), nullable=True)       # Last-Modified header
    http_version = Column(String(16), nullable=True)         # "HTTP/1.1", "HTTP/2"
    # Bytes que viajan por el cable (comprimidos): es el peso que cuenta para el
    # presupuesto de rastreo y para las Core Web Vitals. `content_length` es el
    # recurso ya descomprimido. Estaban cambiados: en un sitio con gzip,
    # content_length daba el tamano comprimido (dividido por 4 o 5).
    transfer_size = Column(BigInteger, nullable=True)
    indexability_status = Column(String(64), nullable=True)  # reason: "Canonicalised", "Noindex", etc.
    blocked_by_robots = Column(Boolean, nullable=True)       # only set in robots_mode="audit"
    # Si la respuesta es la pagina o un muro delante (`shared/respuestas.py`):
    # contenido, challenge_waf, captcha, rate_limited, respuesta_binaria,
    # login_requerido, desconocido. NULL = sin respuesta, salto de una
    # redireccion o rastreo anterior a la columna (decision 103).
    response_class = Column(String(32), nullable=True)
    inlinks_count = Column(Integer, default=0)               # total inlinks to this URL
    outlinks_count = Column(Integer, default=0)              # outlinks INTERNOS (los externos, aparte)
    external_outlinks_count = Column(Integer, default=0)     # external outlinks count
    unique_inlinks_count = Column(Integer, default=0)        # unique source pages linking in
    # Tres columnas porque son tres preguntas distintas, y meterlas en una
    # era el problema (C4 de #24):
    #
    #   pagerank_raw    ¿cuanta autoridad interna tiene del total? Es la
    #                   probabilidad: todas las paginas suman 1. Multiplicada
    #                   por el numero de nodos da "veces la pagina media", que
    #                   SI es comparable entre sitios de tamaño distinto.
    #   pagerank_score  ¿esta arriba o abajo? 0-100 en escala logaritmica, el
    #                   equivalente al Link Score de Screaming Frog. Es la que
    #                   hay que leer y la que se ordena en un informe.
    #   pagerank        la escala 0-10 lineal de siempre, que se mantiene por
    #                   compatibilidad con informes ya entregados. No la uses
    #                   para decidir: la home vale 10 y el resto del sitio se
    #                   apelotona en 0,00xx, asi que no distingue la pagina
    #                   250 de la 25.000.
    pagerank = Column(Float, nullable=True, default=None)    # 0-10 lineal (compatibilidad)
    pagerank_raw = Column(Float, nullable=True, default=None)    # probabilidad, suma 1
    pagerank_score = Column(Integer, nullable=True, default=None)  # 0-100 logaritmico
    # Casi duplicados (MinHash). NULL = no se midio (sin contenido, o texto
    # por debajo del minimo de palabras); 0 = se midio y no tiene ninguna.
    near_duplicate_count = Column(Integer, nullable=True, default=None)
    closest_similarity = Column(Float, nullable=True, default=None)
    # True/False once a sitemap was ingested for the job; NULL = no sitemap data.
    in_sitemap = Column(Boolean, nullable=True, default=None)

    job = relationship("Job", back_populates="urls")
    html_meta = relationship("HtmlMeta", back_populates="url_rel", uselist=False, cascade="all, delete-orphan")
    headings = relationship("Heading", back_populates="url_rel", cascade="all, delete-orphan")
    hreflangs = relationship("Hreflang", back_populates="url_rel", cascade="all, delete-orphan")
    structured_data = relationship("StructuredData", back_populates="url_rel", cascade="all, delete-orphan")
    resources = relationship("Resource", back_populates="url_rel", cascade="all, delete-orphan")
    issues = relationship("Issue", back_populates="url_rel", cascade="all, delete-orphan")
    security = relationship("SecurityHeaders", back_populates="url_rel", uselist=False, cascade="all, delete-orphan")
    page_content = relationship("PageContent", back_populates="url_rel", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("job_id", "url_hash", name="uq_job_url"),
        Index("ix_urls_job_status", "job_id", "status_code"),
        Index("ix_urls_job_host", "job_id", "host"),
    )


# ---------------------------------------------------------------------------
# HTML Metadata
# ---------------------------------------------------------------------------
class HtmlMeta(Base):
    __tablename__ = "html_meta"

    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), primary_key=True)

    title = Column(Text, nullable=True)
    title_len = Column(Integer, nullable=True)
    # Cuantos `<title>` / meta description hay, vacios incluidos: con dos no se
    # sabe cual usa Google (`title_multiple`, `description_multiple`). NULL =
    # rastreo anterior a la columna.
    title_count = Column(Integer, nullable=True)
    meta_description = Column(Text, nullable=True)
    meta_description_len = Column(Integer, nullable=True)
    meta_description_count = Column(Integer, nullable=True)
    meta_keywords = Column(Text, nullable=True)
    meta_robots = Column(String(256), nullable=True)
    # `<meta name="googlebot">`. Sin guardarlo, el analyzer recalculaba la
    # indexabilidad sin el y una pagina noindex solo para Google volvia a
    # "Indexable" al analizar (#26).
    meta_robots_googlebot = Column(String(256), nullable=True)
    # Una cabecera por linea (SEPARADOR_CABECERAS en shared/indexabilidad.py).
    x_robots_tag = Column(String(256), nullable=True)

    canonical_href = Column(Text, nullable=True)
    # Cuantos `rel=canonical` lleva la pagina y si alguno esta en el body.
    # Con varios, Google los ignora TODOS; los del body los ignora siempre.
    # Antes se guardaba el primero en silencio y la pagina salia canonicalizada
    # a una URL que Google no usa.
    canonical_count = Column(Integer, nullable=True)
    canonical_in_body = Column(Boolean, nullable=True)
    canonical_header = Column(Text, nullable=True)

    og_title = Column(Text, nullable=True)
    og_description = Column(Text, nullable=True)
    og_image = Column(Text, nullable=True)
    og_url = Column(Text, nullable=True)
    og_type = Column(String(64), nullable=True)

    twitter_card = Column(String(64), nullable=True)
    twitter_title = Column(Text, nullable=True)
    twitter_description = Column(Text, nullable=True)

    rel_next = Column(Text, nullable=True)
    rel_prev = Column(Text, nullable=True)

    # --- Screaming Frog extended fields ---
    title_pixel_width = Column(Integer, nullable=True)             # SERP pixel width at Arial 20px
    meta_description_pixel_width = Column(Integer, nullable=True)  # SERP pixel width at Arial 14px
    meta_refresh = Column(Text, nullable=True)                     # meta refresh content if present
    # OJO: cuenta cualquier <meta> en el body por lxml, incluidos los
    # `itemprop` de microdatos, que son validos ahi. Salta en el 99,6% de las
    # paginas de penguin: no sirve como incidencia (decision 102). Lo que si
    # dice algo son las dos columnas de abajo.
    has_meta_outside_head = Column(Boolean, nullable=True)
    # Donde corta el navegador el head (`crawler/seo_crawler/head_html.py`,
    # decision 102): la etiqueta que lo cerro antes de su </head> («img»,
    # «div», «#texto»), NULL si acabo donde debia. Solo sin render: el DOM
    # serializado ya no conserva el corte.
    head_cortado_por = Column(String(64), nullable=True)
    # Metadatos que quedan en el body, separados por comas, de entre
    # canonical, description, googlebot, hreflang, robots y title. Cadena
    # vacia = medido y no hay ninguno; NULL = no medido (censo anterior).
    fuera_del_head = Column(String(128), nullable=True)

    # --- Lo que el HTML declara de si mismo (decision 108) ---
    # `content` del primer `<meta name=viewport>` ("" si lo tiene vacio) y
    # cuantos hay. `viewport_count` es la marca de «medido» de todo el bloque:
    # 0 = no hay viewport; NULL = rastreo anterior a la columna, y entonces el
    # analizador no afirma que falte nada (decisiones 67 y 89).
    viewport = Column(Text, nullable=True)
    viewport_count = Column(Integer, nullable=True)
    # `<html lang>` (o `xml:lang`): "" = atributo vacio, NULL = no esta.
    html_lang = Column(String(35), nullable=True)
    # Cabecera `Content-Language`, tal cual.
    content_language = Column(String(64), nullable=True)
    # El href del canonical tal como se escribio: `canonical_href` va resuelto
    # y la forma (relativa, con fragmento) se perdia al extraer.
    canonical_crudo = Column(Text, nullable=True)
    # Forma de la og:image (`shared.metadatos_html.forma_de_href`).
    og_image_forma = Column(String(24), nullable=True)
    twitter_image = Column(Text, nullable=True)
    # Destino de un `location = "..."` a nivel superior de un script (la
    # pagina puente), absoluto y normalizado, sin render. Lo condicional no se
    # guarda.
    redireccion_js = Column(Text, nullable=True)
    # Charset del meta en los 1.024 primeros bytes ("" = no hay) y del BOM,
    # normalizados; si el cuerpo es solo ASCII, y si el titulo o el texto
    # llevan U+FFFD (bytes que no casan con la codificacion). Solo sin
    # render: con render los bytes originales se pierden, todo NULL.
    charset_meta = Column(String(40), nullable=True)
    charset_bom = Column(String(40), nullable=True)
    cuerpo_solo_ascii = Column(Boolean, nullable=True)
    texto_mal_codificado = Column(Boolean, nullable=True)

    url_rel = relationship("Url", back_populates="html_meta")


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------
class Heading(Base):
    __tablename__ = "headings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    tag = Column(String(4), nullable=False)  # h1, h2, h3 …
    position = Column(Integer, nullable=False)
    text = Column(Text, nullable=True)
    # El titular esta en la pagina pero no se pinta (hidden, display:none,
    # clase de utilidad, aria-hidden, dentro de template/noscript/svg). Se
    # guarda igual: lo que no se guarda no se puede auditar. Los checks de h1
    # cuentan solo los que SI se pintan, que es lo que ve el usuario.
    oculto = Column(Boolean, nullable=True)

    url_rel = relationship("Url", back_populates="headings")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
class Link(Base):
    __tablename__ = "links"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    from_url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    to_url = Column(Text, nullable=False)
    to_url_hash = Column(String(64), nullable=False)
    anchor_text = Column(Text, nullable=True)
    rel = Column(String(128), nullable=True)
    is_internal = Column(Boolean, default=True)
    link_position = Column(String(20), nullable=True)  # nav, footer, content

    # --- Screaming Frog extended fields ---
    follow = Column(Boolean, default=True)                # whether link passes equity (derived from rel)
    target = Column(String(20), nullable=True)            # _blank, _self, etc.
    alt_text = Column(Text, nullable=True)                # for image links
    link_type = Column(String(20), default="hyperlink")   # hyperlink, image, redirect, canonical
    # Cuantas apariciones IDENTICAS de este enlace hay en la pagina (decision
    # 94): el megamenu repetido para escritorio y movil es una fila, no tres.
    # Un conteo "por instancia" (inlinks, outlinks) es SUM(repeticiones), no
    # COUNT(*). Los rastreos anteriores tienen 1 en todas: una fila por
    # aparicion, que suma lo mismo.
    repeticiones = Column(Integer, nullable=False, default=1, server_default="1")

    from_url_rel = relationship("Url", foreign_keys=[from_url_id])

    __table_args__ = (
        Index("ix_links_from", "from_url_id"),
        Index("ix_links_to_hash", "job_id", "to_url_hash"),
    )


# ---------------------------------------------------------------------------
# Hreflang
# ---------------------------------------------------------------------------
class Hreflang(Base):
    __tablename__ = "hreflang"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    lang = Column(String(20), nullable=False)
    href = Column(Text, nullable=False)
    return_tag_ok = Column(Boolean, nullable=True)
    lang_valid = Column(Boolean, nullable=True)
    # Anotacion fuera del `<head>`: Google la ignora, asi que no cuenta como
    # retorno de nadie y se avisa con `hreflang_in_body`. NULL = rastreo
    # anterior a la columna (no se sabe; se trata como del head).
    en_body = Column(Boolean, nullable=True)

    url_rel = relationship("Url", back_populates="hreflangs")


# ---------------------------------------------------------------------------
# Structured Data
# ---------------------------------------------------------------------------
class StructuredData(Base):
    __tablename__ = "structured_data"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    raw = Column(JSON, nullable=True)
    format = Column(String(20), nullable=False)  # jsonld, microdata, rdfa
    schema_type = Column(String(128), nullable=True)
    validation_status = Column(String(10), nullable=True)  # ok, info, warning, error
    validation_issues = Column(JSON, nullable=True)
    # Por que el bloque JSON-LD no es JSON estricto (coma final, comentario):
    # Google lo descarta entero, aunque un lector permisivo lo lea y `raw`
    # parezca bueno. NULL = JSON valido, o bloque que no es JSON-LD, o rastreo
    # anterior a la columna (#28, decision 80).
    json_invalido = Column(String(200), nullable=True)

    url_rel = relationship("Url", back_populates="structured_data")


# ---------------------------------------------------------------------------
# Resources (images, CSS, JS, PDFs)
# ---------------------------------------------------------------------------
class Resource(Base):
    __tablename__ = "resources"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_url = Column(Text, nullable=False)
    resource_type = Column(String(20), nullable=False)  # image, css, js, pdf, font, other
    alt_text = Column(Text, nullable=True)
    # Sin rellenar: el rastreo no descarga el cuerpo de los recursos. El dato
    # existiria uniendo resource_url con urls.content_length cuando el recurso
    # se haya rastreado como URL; hasta entonces la columna no se expone.
    size_bytes = Column(BigInteger, nullable=True)

    # --- Screaming Frog extended fields ---
    width = Column(Integer, nullable=True)              # width attribute from HTML
    height = Column(Integer, nullable=True)             # height attribute from HTML
    is_mixed_content = Column(Boolean, nullable=True)   # HTTP resource on HTTPS page

    url_rel = relationship("Url", back_populates="resources")


# ---------------------------------------------------------------------------
# Security Headers (1:1 with Url)
# ---------------------------------------------------------------------------
class PageContent(Base):
    __tablename__ = "page_content"

    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), primary_key=True)
    content_text = Column(Text, nullable=True)
    content_length = Column(Integer, nullable=True)
    content_markdown = Column(Text, nullable=True)

    # HTML tal como lo vio el extractor. Solo con `extraction.store_raw_html`,
    # y por algo viene apagado: medido en un censo real, 170 kB de media por
    # pagina — 29.808 paginas son 4,9 GB antes de que Postgres lo comprima.
    # Sirve para re-extraer contenido sin volver a rastrear y para auditar por
    # que una pagina salio vacia. OJO con render: ahi es el DOM renderizado y
    # YA limpio de banners, no el HTML de origen (es lo que vio el extractor,
    # que es justo lo que hace falta para re-extraer).
    raw_html = Column(Text, nullable=True)

    # Post-crawl cleaning: originals kept for revert; cleaned_at marks state.
    content_text_original = Column(Text, nullable=True)
    content_markdown_original = Column(Text, nullable=True)
    cleaned_at = Column(DateTime(timezone=True), nullable=True)

    url_rel = relationship("Url", back_populates="page_content")


class CleaningRuleset(Base):
    """History of post-crawl cleaning rule applications (per job + URL group)."""

    __tablename__ = "cleaning_rulesets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    group_key = Column(Text, nullable=True)
    url_regex = Column(Text, nullable=True)
    rules = Column(JSON, nullable=False)
    targets = Column(String(20), nullable=False, default="both")  # text | markdown | both
    pages_updated = Column(Integer, default=0)
    pages_skipped = Column(Integer, default=0)
    chars_removed = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class SecurityHeaders(Base):
    __tablename__ = "security_headers"

    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), primary_key=True)

    is_https = Column(Boolean)
    has_mixed_content = Column(Boolean)
    has_hsts = Column(Boolean)                                  # Strict-Transport-Security header present
    has_csp = Column(Boolean)                                   # Content-Security-Policy header present
    has_x_content_type_options = Column(Boolean)                # X-Content-Type-Options present
    has_x_frame_options = Column(Boolean)                       # X-Frame-Options present
    referrer_policy = Column(String(64), nullable=True)         # Referrer-Policy header value
    has_unsafe_crossorigin = Column(Boolean)                    # target=_blank without rel=noopener

    url_rel = relationship("Url", back_populates="security")


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------
class Issue(Base):
    __tablename__ = "issues"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    issue_type = Column(String(64), nullable=False)
    severity = Column(String(10), nullable=False)  # error, warning, info
    details = Column(JSON, nullable=True)
    # Que distingue dos hallazgos del MISMO tipo en la MISMA URL: el codigo de
    # hreflang, el grupo de un duplicado, la imagen sin alt. Sin ella no se
    # puede decir "esto se arreglo" entre dos censos (#32). NULL en las reglas
    # que emiten como mucho una incidencia por URL.
    group_key = Column(String(64), nullable=True)
    detected_at = Column(DateTime(timezone=True), default=_utcnow)

    url_rel = relationship("Url", back_populates="issues")

    __table_args__ = (
        Index("ix_issues_job", "job_id"),
        Index("ix_issues_job_type", "job_id", "issue_type"),
        # url_id es FK con ON DELETE CASCADE y sin indice Postgres tiene que
        # recorrer la tabla entera por CADA fila de `urls` que se borra. Medido
        # en produccion con 1,76 M de incidencias: borrar 16 rastreos (26.597
        # URLs) pasaba de mas de 14 minutos sin terminar a 15 segundos.
        # Tambien lo usa el join de GET /issues/urls.
        Index("ix_issues_url_id", "url_id"),
    )


# ---------------------------------------------------------------------------
# Extraccion y busqueda a medida (#34, decision 104)
# ---------------------------------------------------------------------------
class CustomExtraction(Base):
    """Una fila por (URL, regla) EVALUADA.

    Sin fila = «no evaluada» (no es HTML 2xx, cae fuera de `solo_urls`, o el
    censo es anterior a la regla), que NO es «vacio»: «no contiene» es
    exactamente `estado='sin_coincidencia'`. Las definiciones de las reglas
    viajan en `jobs.config` (`custom_extraction` / `custom_search`).

    `job_id` va en la fila a proposito: filtrar, borrar y pesar por censo sin
    unir con `urls` (decision 37).
    """

    __tablename__ = "custom_extraction"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    nombre = Column(String(40), nullable=False)
    clase = Column(String(10), nullable=False)        # extractor | busqueda
    # ok, sin_coincidencia, incompleta, conversion_fallida, tiempo_agotado,
    # presupuesto_pagina, desactivada, pagina_bloqueada, error
    estado = Column(String(24), nullable=False)
    # Coincidencias reales antes de recortar. NULL salvo en ok,
    # sin_coincidencia y conversion_fallida: en los demas no se llego a contar.
    n = Column(Integer, nullable=True)
    # `primero`: el valor; `todos`: unidos con " | "; busqueda: ~120
    # caracteres de contexto de la primera coincidencia.
    valor = Column(Text, nullable=True)
    # `valor` con los espacios colapsados y recortado a 256: para ordenar,
    # agrupar y comparar sin pasarse del limite de una clave de B-tree.
    valor_clave = Column(String(256), nullable=True)
    valor_num = Column(Float, nullable=True)
    valores = Column(JSON, nullable=True)              # solo con `todos`
    truncado = Column(Boolean, nullable=False, default=False)
    # True = se extrajo del DOM renderizado (y ya sin banners); False = del HTML
    # de la respuesta: job sin render, o pagina que cayo en `_sin_render`.
    render = Column(Boolean, nullable=True)
    detalle = Column(String(200), nullable=True)       # motivo del error, sin datos de la pagina

    __table_args__ = (
        UniqueConstraint("url_id", "nombre", name="uq_cext_url_nombre"),
        Index("ix_cext_job_nombre_estado", "job_id", "nombre", "estado"),
        Index("ix_cext_job_nombre_num", "job_id", "nombre", "valor_num"),
        Index("ix_cext_job_nombre_clave", "job_id", "nombre", "valor_clave"),
    )


# ---------------------------------------------------------------------------
# Enlaces que Google no puede seguir (decision 106)
# ---------------------------------------------------------------------------
class EnlaceNoRastreable(Base):
    """Un enlace que una persona puede pulsar y Google no puede seguir.

    `href="javascript:..."`, `<a onclick>` sin href, `<a routerLink>`,
    `<span href>`, la tarjeta con `onclick="location=..."` y la ruta en el
    fragmento (`#/x`). Tabla APARTE de `links` a proposito: `links` es el grafo
    —entrantes, PageRank, profundidad, la comparacion y el archivo en Parquet
    la leen en mas de veinte consultas— y una fila que no es una arista
    obligaria a filtrarla en todas; la que se olvide cambia el PageRank sin
    avisar (decisiones 23 y 39: dos criterios acaban discrepando).

    `job_id` en la fila para filtrar, borrar y pesar por censo sin unir con
    `urls` (decision 37). Una fila por enlace distinto de la pagina, con
    `repeticiones` (decision 94).
    """

    __tablename__ = "enlaces_no_rastreables"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    from_url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    # javascript_href, onclick_sin_href, a_sin_href, href_fuera_de_a,
    # onclick_en_otra_etiqueta, fragmento_como_ruta (shared/enlaces_no_rastreables.py)
    crawlability = Column(String(32), nullable=False)
    etiqueta = Column(String(20), nullable=True)       # a, span, div, button...
    href = Column(Text, nullable=True)                 # el atributo (o el onclick), recortado
    # A donde iria, si el codigo lo dice; NULL = no se sabe, que no es «a ningun sitio».
    destino = Column(Text, nullable=True)
    destino_interno = Column(Boolean, nullable=True)
    # El de `urls.url_hash`: para saber si el destino esta en el censo.
    destino_hash = Column(String(64), nullable=True)
    anchor_text = Column(Text, nullable=True)
    link_position = Column(String(20), nullable=True)
    repeticiones = Column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        Index("ix_enr_job", "job_id"),
        Index("ix_enr_from", "from_url_id"),
    )


# ---------------------------------------------------------------------------
# Decisiones de un modelo (#7, decision 84)
# ---------------------------------------------------------------------------
class Decision(Base):
    """Una respuesta de Jev a una pregunta sobre un estado.

    Es a la vez el registro de lo decidido y la cache DURADERA: un recrawl
    busca aqui por `(clave, pregunta, version_pregunta, modelo)` antes de
    pagar otra vez (la de Redis es la que comparten las replicas durante una
    ejecucion y caduca).

    `clave` es el hash del estado NORMALIZADO y no depende de `url_id`, que
    cambia de un job a otro: es lo que permite al rastreo incremental (#36 F4)
    arrastrar decisiones de un job al siguiente sin llamar a nadie.

    `run_id` es la ejecucion de enriquecimiento de #40 (`enrichment_runs`), y
    NULL para lo que no venga de una (el juego de evaluacion de #8). SET NULL
    al borrar la ejecucion: la decision sigue valiendo como cache.
    """

    __tablename__ = "decisions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Nullable: el juego de evaluacion de #8 pregunta sin job.
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"),
                    nullable=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=True)
    run_id = Column(UUID(as_uuid=True),
                    ForeignKey("enrichment_runs.id", ondelete="SET NULL"), nullable=True)
    # url | bloque | enlace | par | job
    ambito = Column(String(10), nullable=False, default="url")
    clave = Column(String(64), nullable=False)
    pregunta = Column(String(64), nullable=False)
    version_pregunta = Column(Integer, nullable=False)
    # decidido | incierto | sin_decidir
    estado = Column(String(12), nullable=False)
    # modelo | cache | arrastre | regla | humano
    origen = Column(String(10), nullable=False)
    # Para `noul` la probabilidad de si en texto ("0.98"): una sola columna
    # para los tres tipos, y los numeros siguen en `probabilidades`.
    respuesta = Column(Text, nullable=True)
    probabilidades = Column(JSON, nullable=True)
    confianza = Column(Float, nullable=True)
    # El modelo PEDIDO (fijado en el catalogo), que es el que forma la clave
    # de cache, y el que respondio (`typesafe/jev-1.13-20260917`), que puede
    # no coincidir y es el que hay que mirar al comparar resultados.
    modelo = Column(String(64), nullable=False)
    modelo_respuesta = Column(String(96), nullable=True)
    proveedor = Column(String(64), nullable=True)
    tokens_entrada = Column(Integer, nullable=True)
    # La parte de la llamada que corresponde a esta pregunta (el coste de una
    # llamada con varias preguntas se reparte a partes iguales). 0 en lo
    # servido de cache: es lo que no se ha vuelto a pagar.
    coste_usd = Column(Float, nullable=True)
    coste_estimado = Column(Boolean, nullable=True)
    # Por que quedo sin decidir: presupuesto_agotado, sin_credito, red...
    motivo = Column(String(32), nullable=True)
    # Lo que VIO el modelo: la revision de #8 tiene que ensenar eso y no lo
    # que hay hoy en la pagina. Es contenido que ya esta en nuestra BD.
    estado_enviado = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_decisions_job_pregunta", "job_id", "pregunta"),
        # FK con ON DELETE CASCADE: sin indice, borrar un rastreo recorre la
        # tabla entera por cada fila de `urls` (la leccion de `ix_issues_url_id`).
        Index("ix_decisions_url_id", "url_id"),
        Index("ix_decisions_cache", "clave", "pregunta", "version_pregunta", "modelo"),
        Index("ix_decisions_run_id", "run_id"),
    )


# ---------------------------------------------------------------------------
# Ejecuciones del analisis enriquecido (#40, decision 85)
# ---------------------------------------------------------------------------
class EnrichmentRun(Base):
    """Una pulsacion del boton «Analisis enriquecido»: que se pidio, sobre que
    URLs, con cuanto dinero y en que quedo.

    Es lo UNICO que dispara la capa de decisiones despues de un rastreo: la
    crea la API (con token, decision 85) y la ejecuta el worker desde su
    propia cola. Nunca se crea sola —ni al terminar un rastreo ni desde una
    programacion—.

    `modulos` guarda la lista YA RESUELTA, con las dependencias anadidas, para
    que reanudar tras un reinicio haga exactamente lo que se confirmo aunque
    el registro de modulos cambie entre medias. `estimacion` es lo que se
    enseño en el dialogo al confirmar: comparar con `gastado_usd` es la
    validacion del ±30% de #40.

    Estados: pendiente -> en_curso -> completada | parcial | cancelada |
    fallida. `cancelando` es la peticion de cancelar mientras corre: el worker
    la ve entre lotes y cierra como `cancelada` conservando lo pagado.
    `parcial` = se corto por presupuesto (de la ejecucion o mensual).
    """

    __tablename__ = "enrichment_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    modulos = Column(JSON, nullable=False, default=list)
    # {"carpeta": "/blog/", "max_urls": 500, "muestra": true}; vacio = todas
    # las HTML internas, 200 e indexables.
    alcance = Column(JSON, nullable=False, default=dict)
    estimacion = Column(JSON, nullable=True)
    presupuesto_usd = Column(Float, nullable=True)
    # Suma de `usage.cost` de lo que se ha pagado en esta ejecucion (o de la
    # estimacion por tokens si la respuesta no lo trae, marcado en
    # `decisions.coste_estimado`).
    gastado_usd = Column(Float, nullable=False, default=0.0)
    estado = Column(String(16), nullable=False, default="pendiente", index=True)
    # {"modulo": ..., "procesadas": n, "total": N, "por_modulo": {...}}
    progreso = Column(JSON, nullable=True)
    errores = Column(JSON, nullable=True)
    # Por que se paro (presupuesto_ejecucion, presupuesto_cliente, sin_credito...)
    motivo = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # Latido del worker: lo que distingue una ejecucion viva de una que se
    # quedo en `en_curso` porque un despliegue recreo el contenedor.
    latido_at = Column(DateTime(timezone=True), nullable=True)
