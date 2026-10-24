# API, mapa de ficheros y extraccion

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## File Map

### API (`api/`)
| File | Description |
|------|-------------|
| `main.py` | FastAPI app, lifespan, CORS, static files, SPA fallback. Arranca el servidor MCP remoto (decision 151) |
| `mcp_despacho.py` | ASGI por fuera de todo: `/mcp`, `/authorize`, `/token`, `/register`, `/revoke`, `/oauth/entrar` y `/.well-known/oauth-*` van al servidor MCP (`mcp_servidor/remoto.py`), con su propio OAuth; 503 si no esta arrancado |
| `schemas.py` | Pydantic v2 request/response models |
| `dependencies.py` | Shared Redis client + DB session dependency |
| `routers/jobs.py` | Job CRUD endpoints (POST, GET, PATCH cancel, DELETE) |
| `routers/results.py` | Results endpoints (urls, issues, links, stats, export CSV) |
| `routers/clientes.py` | Clientes, perfiles e integraciones (#36, decision 83). Resuelve la herencia de configuracion al crear un job (`resolver_config`) |
| `routers/almacenamiento.py` | Lo que ocupa la base, cada tabla y los Parquet de enlaces archivados (decision 96). Router propio: en el de jobs la ruta caeria debajo de `GET /{job_id}` |
| `routers/segmentos.py` | Segmentos de negocio (decision 125): listar y re-segmentar un censo |
| `routers/extraccion.py` | Extraccion y busqueda a medida (#34, decision 104): probar reglas (`POST /api/extraccion/probar`; con `url`, la descarga el worker, decision 166), resumen y distribucion de valores de un censo |
| `routers/enriquecido.py` | Analisis enriquecido (#40, decision 85): estimar, crear (con `X-Enrichment-Token`), leer y cancelar ejecuciones. No llama a ningun modelo: encola en `enrichment:pending` |

### Crawler (`crawler/`)
| File | Description |
|------|-------------|
| `worker.py` | Redis queue consumer. Runs Scrapy as **subprocess** per job |
| `seo_crawler/spiders/seo_spider.py` | Main SEO spider — response handling, link following, all extraction |
| `seo_crawler/extractors.py` | **Pure functions** (no Scrapy imports) — all HTML extraction logic |
| `seo_crawler/pipelines.py` | PostgreSQL pipeline — upsert pages, batch-insert child items |
| `seo_crawler/items.py` | Scrapy Item definitions (PageItem, HtmlMetaItem, LinkItem, etc.) |
| `seo_crawler/middlewares.py` | UA rotation, proxy, job config, HTTP config middlewares |
| `seo_crawler/settings.py` | Scrapy settings (BFS, autothrottle, Playwright, pipeline) |
| `seo_crawler/handlers.py` | `CompositeDownloadHandler` (Playwright o curl_cffi) e `ImpersonateConMedidas`: el `_download_request` de scrapy-impersonate 1.10.0 (fijado) con `curl_infos`, que deja en `meta` el TTFB, el `Content-Encoding` servido y la version HTTP (decision 109) |
| `seo_crawler/sonda_tls.py` | Clasificacion del error TLS de una descarga (`tls_error` con motivo) y sonda del certificado, una vez por host https interno, en un hilo y fuera de `max_urls`; va a `jobs.tls` (decision 110) |
| `seo_crawler/sonda_respuestas.py` | Sondas de respuesta (decisiones 123, 124 y 154): enlaces externos (`comprobar_enlaces_externos`) y recursos embebidos (`comprobar_recursos`, `check_external_resources`) con HEAD y GET+Range de repuesto, y la og:image (`_sonda_og_image`, encendida de serie: GET de 64 KB, `og_image_muestra` y `og_image_por_plantilla`, `ancho`/`alto`), robots.txt del tercero, origen caido tras DNS confirmado o dos fallos, tope global y por host, latido por tiempo. No cuentan en `max_urls`, no son `urls` ni cuentan para la salud; van a `respuestas_sondeadas` |
| `seo_crawler/sonda_soft404.py` | Sondas de soft 404 (decision 105): una URL inexistente por host de las semillas, por host interno nuevo y por plantilla nueva (y repetida en el destino si solo cambia de esquema o host), con su propio callback; no cuentan en `max_urls` ni se guardan en `urls`, van a `jobs.soft404["sondas"]` |
| `seo_crawler/sonda_wordpress.py` | Adaptador WordPress (decision 161): con la respuesta de cada semilla pide `/wp-json/` (y la raiz que anuncie) y, si es un indice con `wp/v2`, lee tipos, taxonomias, entradas, paginas, tipos propios, medios y terminos paginando por `X-WP-TotalPages`, dos a la vez como mucho por una cola propia en el slot del host, con latido y tope `max_minutos`, solo hacia hosts del job (sin seguir saltos fuera); sondas de pagina N+1 y de paginas de adjunto sin seguir redirecciones. `EntidadesAdaptadorItem` a `adapter_entities` y el resumen a `jobs.adapters`; fuera de `max_urls` |
| `seo_crawler/sonda_axe.py` | Accesibilidad (decision 174): una muestra por plantilla (`axe_muestra`, `axe_por_plantilla`) se vuelve a renderizar y se le pasa axe-core vendorizado (`vendor/axe/`, MPL-2.0) con las reglas WCAG 2.x A/AA; `CapturaAlternativaItem` con `perfil='axe'`, fuera de `max_urls`. El resumen y la agregacion por regla y plantilla viven en `shared/accesibilidad.py` |
| `seo_crawler/sonda_paridad.py` | Paridad movil/escritorio (decision 119): una muestra por plantilla (`config.templates` o `firma_de_ruta`) pedida con el otro perfil (UA, huella TLS y contexto de Playwright `perfil_<perfil>`), sin seguir redirecciones (el destino se pide una vez para mirar la anotacion del m-dot) y con control de volatilidad; `CapturaAlternativaItem`, fuera de `max_urls`. `contextos_del_perfil` pone el UA del job en el contexto `custom` |

### Analysis (`analysis/`)
| File | Description |
|------|-------------|
| `analyzer.py` | `SEOAnalyzer` class with 17 check methods + `run_analysis()` entry point |
| `near_duplicates.py` | Contenido casi duplicado — MinHash + LSH. Funciones puras sobre texto; las escrituras sin espacios (CJK, tailandés…) se parten en caracteres (decisión 118) |
| `sd_validation.py` | Validación conservadora de datos estructurados |
| `avisos_de_wordpress.py` | WordPress (decision 161): cruza `adapter_entities` con el censo; ver la tabla de checks |
| `soft404.py` | Soft 404 determinista (decision 105): cruza las sondas de `jobs.soft404` y las senales de `shared/soft404.py` con el censo |

### Shared (`shared/`)
| File | Description |
|------|-------------|
| `models.py` | SQLAlchemy models: Job, Url (40+ fields), HtmlMeta, Heading, Link, Hreflang, StructuredData, Resource, PageContent, SecurityHeaders, Issue |
| `database.py` | Engine + SessionLocal factory |
| `config.py` | Env vars + SEO thresholds (title/description min/max lengths) |
| `clientes.py` | Slug, fusion profunda de configuracion (`fusionar_config`), cliente a partir del `client_id` de texto y la migracion de esos textos (decision 83) |
| `credenciales.py` | `credencial(integracion)`: el secreto de una integracion, leido del ENTORNO (nunca de la BD) y solo dentro del prefijo de su tipo (decision 83) |
| `llm.py` | Transporte UNICO hacia OpenRouter (hoy Jev por `/systemone`): asincrono (httpx), ritmo por minuto con AIMD ante 429, reintentos, corte ante 402/401/404, circuito abierto y `usage.cost` (decision 84) |
| `decisiones.py` | `CapaDecisiones.preguntar(estados, preguntas)`: cache (memoria, Redis, tabla `decisions`), agrupacion por estado, presupuesto (ejecucion, mensual del cliente, global), degradacion a `sin_decidir` y observabilidad por job (decision 84). Solo la dispara `enriquecido.py` |
| `enriquecido.py` | Analisis enriquecido bajo demanda (#40, decision 85): registro de modulos con dependencias, alcance, `estimar()` sin llamar a nadie y `ejecutar()` por lotes en el worker (corte por presupuesto, cancelar, reanudar) |
| `preguntas/` | Catalogo versionado de preguntas (`Pregunta`: nombre, tipo, instrucciones, opciones, version, modelo fijado). Solo una de ejemplo: el catalogo es #9 y la evaluacion #8 |
| `almacenamiento.py` | Cuanto ocupa un censo y cuanto ocupa la base (decision 96). Estimacion por filas x bytes/fila; los enlaces se cuentan por censo y con presupuesto, lo que no se puede medir viene a `None`, y un censo archivado se mide por su Parquet (`archivo_bytes`, aparte) |
| `topes_carpeta.py` | Topes de URLs por subdominio o carpeta (decision 172): `compilar` (valida `topes_por_carpeta`), `grupos_de(url, reglas)` (el cupo de ruta y el de host), `clave_normal` (la misma regla con otra grafia, para la herencia), `Presupuesto` (lo que lleva el spider, con `ClavesCompactas`, y su `resumen` para `jobs.topes_carpeta`), `corto` (si algun tope dejo URLs sin pedir: el worker y el analizador) y `describir` (la frase del informe). La usan `JobConfig`, `fusionar_config`, el spider, el worker y el analizador |
| `segmentos.py` | Segmentos de negocio (decision 125): `validar`, `Segmentador` (primera regla verdadera, logica de tres valores, repuesto `firma_de_ruta`) y `segmentar(session, job_id, reglas)`, que materializa `urls.segmento` y `jobs.segmentacion`. La usan el analizador y la API |
| `extraccion_a_medida.py` | Extraccion y busqueda a medida (decision 104): `validar`, `compilar`, `aplicar`, `firma`, `a_numero` y el `Presupuesto` con cortacircuitos. La usan el spider y la API |
| `texto_visible.py` | Texto que el navegador pinta, linea a linea (`_lineas_visibles`, `_nodo_oculto`...). Mudado desde `extractors.py`, que lo reexporta, para que la API lo comparta (decision 104) |
| `respuestas.py` | `clasificar_respuesta(status, cabeceras, cuerpo)`: la pagina o un muro (desafio de WAF, captcha, 429, cuerpo binario, login), y `VentanaDeSalud` / `causa_de_bloqueo` para parar un rastreo bloqueado. La usan el spider y `descarga_cruda.py` (decision 103) |
| `soft404.py` | Reglas puras del soft 404 (decision 105): `mangle_path`/`token_sonda` (la URL que no existe), `huella`/`misma_huella` (contenido sin el eco de la URL), `senal_titulo` (titulares de error en es/en/ca/eu/fr/de/pt/it), `es_busqueda`/`frase_sin_resultados`, `disponibilidades` |
| `paginacion.py` | `pagina_de(url, debiles=False)` -> (serie, n, forma): pagina N de una serie por la forma de la URL (`?page=`, `/page/N`...; `?p=N` y `/N` solo con evidencia; el `?e-page-<id>=N` de Elementor), `clave_comparable` y `es_primera_de` (decision 122) |
| `robots.py` | Lectura de directivas robots — tokenizado y detección de separadores inválidos. En `shared/` porque el analyzer y el extractor deben leerlas igual |
| `sitemaps.py` | Lo que se juzga de un sitemap (decision 126): `leer_lastmod` (W3C Datetime a UTC, None si no se puede leer), `es_futuro` (con un dia de margen) y `motivos_de_limite` con los limites de Google (50.000 URLs, 50 MB sin comprimir), que no son los topes del worker |
| `indexabilidad.py` | `estado_indexabilidad`, `es_noindex`/`es_nofollow` y `misma_url`/`clave_url`: la indexabilidad de spider y analyzer (decision 39). `X-Robots-Tag` trozo a trozo con `x_robots_por_bot`/`directivas_x_robots` (una directiva con valor no es un bot; el bot dura hasta el siguiente) y `x_robots_para_bot` para la sintaxis (decision 107) |
| `perfiles.py` | Perfiles de dispositivo (decision 119): UA de Googlebot smartphone (el del preset del formulario) y de Chrome Android, `CHROME_EVERGREEN`, huella TLS movil, viewport de Playwright, `es_ua_movil`/`perfil_de_ua`, `contexto_playwright` |
| `modo_lista.py` | Modo lista y veredicto (decision 128): `es_lista`, `umbral_absoluto` (lo que recibe `comparar_censos(umbral_absoluto=)`), `acotar_a_la_lista` (la referencia recortada a las URLs de la lista) y `veredicto(db, job)` |
| `dimensiones_imagen.py` | `dimensiones(bytes)` -> (ancho, alto) o None, de la cabecera de PNG, GIF, JPEG, WebP, AVIF/HEIF y BMP, sin Pillow; `BYTES_NECESARIOS` (64 KB) es el `Range` de la sonda de la og:image (decision 154) |
| `wordpress.py` | Lo que se sabe de un WordPress por su REST API (decision 161): `raiz_de_cabecera`, `raiz_por_defecto`, `url_rest` (con y sin enlaces permanentes), `es_indice`, `plugin_seo`, `tipos_de_contenido` y `taxonomias` (fuera los internos del nucleo), `url_de_pagina` (pagina N de un archivo) |
| `paridad.py` | `diferencias(base, alternativa)` campo a campo con sus umbrales (palabras del contenido principal, enlaces internos que faltan), `canonical_efectivo`, `vary_por_user_agent`, `resumen` (decision 119) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/jobs` | Create crawl job |
| GET | `/api/jobs` | List jobs (`?status=`, `?client_id=` —UUID, slug o el texto viejo—, `?profile_id=`, `?page=`) |
| GET/POST | `/api/clients` | Listar / crear clientes |
| GET/PATCH/DELETE | `/api/clients/{id}` | Ficha, edicion y borrado (`?confirmar=<slug>`, `?con_jobs=`) |
| GET/POST | `/api/clients/{id}/profiles` | Perfiles de rastreo del cliente |
| GET/PATCH/DELETE | `/api/clients/{id}/profiles/{pid}` | Un perfil |
| GET | `/api/clients/{id}/config-resuelta` | Lo que heredaria un job (`?profile_id=`), para precargar el modal |
| GET | `/api/clients/{id}/evolucion` | Evolucion del cliente (decision 130): una serie por perfil (o programacion, o host) con los puntos de `profile_runs`, `comparable`/`motivo`, `cambio_version` y `tendencia_errores`; `?profile_id=`, `?limite=` (26). `sin_agregado` = terminados sin fila |
| GET | `/api/clients/{id}/profiles/{pid}/evolucion` | Lo mismo, solo el perfil |
| GET | `/api/clients/{id}/trafico` | Trafico de Google entre censos para la ficha del cliente (decision 165): el ultimo de cada perfil entre los `?limite=` (30) rastreos mas recientes con comparacion, con `textos` del mismo redactor que la ficha del rastreo y Slack (`trafico_entre_censos.textos`), `pendiente`, `general` y `aviso`. Sin los perfiles de la competencia |
| GET | `/api/enrichment/modulos` | Modulos del analisis enriquecido y si lanzar esta activado (decision 85) |
| POST | `/api/jobs/{id}/enrichment/estimate` | Llamadas tras la cache, tokens, coste, tiempo y gasto del mes del cliente. No gasta nada |
| POST | `/api/jobs/{id}/enrichment` | Crea y encola una ejecucion. **Exige `X-Enrichment-Token`**; sin `ENRICHMENT_TOKEN` en el entorno, 403 |
| GET | `/api/jobs/{id}/enrichment[/{run_id}]` | Ejecuciones del job / una con su progreso, coste y resultados por pregunta |
| GET | `/api/jobs/{id}/enrichment/{run_id}/decisions` | Respuestas URL a URL (`?pregunta=`, `?respuesta=`, `?page=`) |
| PATCH | `/api/jobs/{id}/enrichment/{run_id}/cancel` | Cancela (pendiente: en el acto; en curso: entre lotes, conservando lo pagado) |
| GET | `/api/jobs/{id}` | Get job details |
| PATCH | `/api/jobs/{id}/cancel` | Cancel job |
| DELETE | `/api/jobs/{id}` | Delete job + cascade, y el Parquet de sus enlaces si estaban archivados (decision 95) |
| POST | `/api/jobs/eliminar` | Borrado en masa (decision 96). `{ids, simular, forzar, segundos}` (`forzar` salta solo el guardia de la serie programada, nunca el de un rastreo en marcha): `simular` es la vista previa con tamanos, guardias y `archivo_bytes`; sin el, borra (Parquet incluido) y devuelve `pendientes` con lo que no cupo en su presupuesto de tiempo; lo que queda a medias pasa a `status='deleting'` |
| GET | `/api/almacenamiento` | Lo que ocupa la base y cada tabla, mas los Parquet archivados (`archivo_bytes`, `archivo_ficheros`), para decidir que censos borrar |
| GET | `/api/jobs/{id}/progress` | Real-time progress from Redis |
| GET | `/api/jobs/{id}/urls` | Crawled URLs (`?status_group=`, `?is_internal=`, `?resource_type=`, `?issue_type=` —uno o varios, por comas—, `?severity=`, `?in_sitemap=`, `?vacio=`/`?con_valor=` campos, `<col>_gte/_lte`, `<campo>_contains`, `?ligero=true` sin `page_content`; columnas calculadas con `?origenes=`, `?h1=` y `?cambios=` —frente al censo de la comparación automática o `?con=`—). Los «contiene» admiten `a|b` y `!a`; `status_code` admite `301,302`. El filtrado vive en `_urls_filtradas`, compartido con el CSV de la vista. Reglas a medida (decision 104): `x.<n>_gte/_lte`, `x.<n>_contains` (`!a` solo sobre lo evaluado), `x.<n>=si\|no`, `x.<n>_estado=`, `vacio=x.<n>`, `sort_by=x.<n>` y `?a_medida=n1,n2\|todas` para añadir los valores; una regla que el censo no tiene es 422. Segmentos (decision 125): `?segmento=` (repetible: O) y `sort_by=segmento` |
| GET | `/api/jobs/{id}/urls/plantillas` | La vista del Explorador agrupada por forma de ruta (`shared.plantillas.firma_de_ruta`): URLs, indexables, estados, con errores, medianas y los últimos tramos que se repiten (`null` × 6 = un fallo de plantilla). `/urls?plantilla=` entra en una. `?agrupar=segmento` agrupa por `urls.segmento` (decision 125; `(sin segmentar)` = censo anterior a la columna) |
| GET | `/api/jobs/{id}/urls/enlaces-entrantes` | CSV con todos los enlaces que apuntan a las URLs de la vista (destino, origen, ancla, posición, follow). `/urls?origenes=true` añade a cada fila `enlazada_desde` y `origenes` |
| GET/POST/DELETE | `/api/clients/{id}/vistas[/{vista_id}]` | Vistas guardadas del Explorador (`clients.vistas_explorador`): la consulta de la URL con nombre, válida en cualquier censo del cliente |
| GET | `/api/uso/explorador` | Qué pestañas, filtros, órdenes, problemas y columnas se piden en el Explorador (`?dias=`). Solo nombres, nunca valores (`api/uso.py`) |
| GET | `/api/jobs/{id}/urls/export` | CSV de la vista del Explorador: mismos filtros y orden que `/urls`, solo las columnas de `?columnas=` (claves de `COLUMNAS_VISTA` y `x.<nombre>`: una columna por posicion con `cuantos=todos`, formulas escapadas) |
| POST | `/api/extraccion/probar` | Prueba reglas a medida (decision 104) contra `html` pegado (5 MB como mucho, 413) o el `raw_html` de `job_id` + `url_id` (409 si no se guardo). Hereda con `cliente_id`/`profile_id`. O `url` en vivo (decision 166): la API valida (422), encola en `extraccion:pruebas` y espera al worker (504 si no contesta en `PRUEBA_EN_VIVO_ESPERA_S`; 422 si un salto apunta a la red interna; 502 si falla la descarga), con `user_agent`/`impersonate` opcionales; sin render. La API nunca descarga. Dos a la vez como mucho (429; `CUSTOM_EXTRACTION_PRUEBAS_A_LA_VEZ`); las de `url`, con su propio tope (`CUSTOM_EXTRACTION_PRUEBAS_EN_VIVO_A_LA_VEZ`, 4). Devuelve por regla estado, n, valor(es), valor_num, ms y avisos de coste |
| GET | `/api/jobs/{id}/extraccion` | Por regla: definicion, firma, evaluadas, por estado, renderizadas y los 5 valores mas frecuentes; avisa si un extractor no encontro nada |
| GET | `/api/jobs/{id}/extraccion/{nombre}/valores` | Distribucion paginada de `valor_clave` |
| GET | `/api/jobs/{id}/issues` | SEO issues (`?severity=`, `?issue_type=`, `?segmento=` —solo las de su página: fuera las de `paginas_afectadas` > 1, decision 125—), por gravedad y tipo |
| GET | `/api/jobs/{id}/accesibilidad` | axe por muestra de plantilla (decision 174): `pedida`, paginas evaluadas y no evaluadas con su motivo, `reglas` (violaciones por regla, de mas a menos grave, con plantillas afectadas sobre muestreadas; lo mismo que los avisos `accesibilidad_*`), `incompletas` (sin afirmar) y `coste` (ms de axe y de la descarga). Sin capturas, `reglas` vacio y 0 evaluadas |
| GET | `/api/jobs/{id}/robots` | robots.txt de cada host del censo y que bloquea cada bot (`bots`: catalogo con tipo buscador/busqueda_ia/usuario_ia/entrenamiento; `hosts[].bots`: home y muestra por plantilla, None si se analizo antes de la decision 129). La ficha de una URL bloqueada trae `robots_regla`: la linea que la bloquea, para Googlebot y para nuestro rastreo |
| GET | `/api/jobs/{id}/robots/cuerpo?origen=` | El robots.txt guardado de un origen del censo (`cuerpo`, `recortado`, `fecha`); 404 si no hay o no se guardo cuerpo. Punto de partida del robots.txt de prueba (decision 171) |
| POST | `/api/robots/probar` | robots.txt de prueba (decision 171): `{robots_txt, urls (<=500), agentes (<=20, por defecto Googlebot), modo: google\|rastreador}` -> `resultados[]` con `permitida`, `linea`, `texto`, `directiva`, `grupo`, `linea_grupo` por (agente, URL), y `diagnostico` (lineas que no se leen, `Sitemap`, grupos, reglas, `trozos`, `reglas_con_comodin`, `recortado`). Misma logica que el crawler (`shared/robots_regla.evaluar_lote`), sin descargar nada. 422 con un UA entero en modo google o si el coste (`robots_prueba.coste`: trozos entre comodines x URLs, largo de las URLs por regla con comodin y pares) pasa de 15 M unidades (~1 s) |
| GET | `/api/jobs/{id}/issues/resumen` | Una fila por tipo: incidencias, páginas distintas y ejemplos (vista agrupada de Problemas). Decision 125: `?segmento=` (con `pct_paginas` y `urls_denominador`) y `?por_segmento=true` (`por_segmento: [{segmento, paginas, urls, pct}]` hasta `segmentos_max`, y `agregadas`: las que se emiten una vez por hallazgo de varias páginas y no se reparten; con `?segmento=` un tipo que solo tiene de esas sale con 0 páginas, no desaparece) |
| GET | `/api/jobs/{id}/segmentos` | Segmentos materializados con URLs, indexables, 2xx y páginas con error/aviso; las reglas, `segmentacion` (resumen de la última) y `al_dia` (si `urls.segmento` corresponde a las reglas del censo) (decision 125) |
| POST | `/api/jobs/{id}/segmentos` | Re-segmenta SIN el análisis entero: `{segmentos: [...]}` (reglas nuevas), `{desde_cliente: true}` (las del cliente/perfil de hoy) o vacío (las del censo). Guarda las reglas en `jobs.config.segmentos`. 422 si una condición usa una regla a medida que el censo no tiene; 409 si el censo está en marcha o se está analizando. Vale con los enlaces archivados |
| GET | `/api/jobs/{id}/links` | Link graph |
| GET | `/api/jobs/{id}/wordpress` | Adaptador WordPress (decision 161): `resumen` (`jobs.adapters.wordpress`: detectado, estado de la REST API, plugin SEO, totales de `X-WP-Total`, si se corto, y `analisis` con lo afirmado y lo que no) y `por_tipo` (leidas y en el censo) |
| GET | `/api/jobs/{id}/wordpress/entidades` | Entidades de la REST API y sus sondas, paginadas (`?tipo=`, `?solo_no_rastreadas=true` = sin fila en `urls` y sin las sondas: «Solo en WordPress» del Explorador, decision 168; `?buscar=` texto en la URL; `?page=`, `?page_size=` hasta 1000) |
| GET | `/api/jobs/{id}/wordpress/assets` | Plugins y tema por la ruta de los CSS y JS (`/wp-content/plugins/<slug>/`) en las primeras `?muestra=` paginas HTML (2.000 de serie, hasta 20.000), con las paginas de cada uno y las versiones del `?ver=` que tienen forma de version y no son copias de cache (decision 168) |
| POST | `/mcp` | Servidor MCP remoto (decision 151), Streamable HTTP sin sesiones. Pide `Bearer` de OAuth (no `API_TOKEN`); sin el, 401 con `resource_metadata`. Descubrimiento en `/.well-known/oauth-protected-resource/mcp` y `/.well-known/oauth-authorization-server`; autorizar en `/oauth/entrar` con la clave de la interfaz |
| GET | `/api/jobs/{id}/stats` | Aggregated stats |
| GET | `/api/jobs/{id}/veredicto` | QA de despliegues (decision 128): `{veredicto: pasa\|falla\|no_concluyente, motivos, no_concluyente_por, es_lista, comparado_con, urls_de_la_lista}`. **200 siempre** (404 solo si el job no existe). `falla`: alerta critica de `jobs.comparacion` o una URL de las semillas que no da 200, que robots.txt prohibe, que deja de ser indexable o cambia de canonical (y `robots_bloquea_todo`). Sin terminar, fallido, sin referencia, truncado o bloqueado es `no_concluyente`, nunca `pasa`. El modo lista es `max_depth=0` (solo semillas, sin sitemap salvo que se pida) y su umbral de alerta `analysis_thresholds.alerta_lista_min_paginas` (def. 1) |
| GET | `/api/jobs/{id}/urls/{url_id}` (campo `wordpress`) | La entidad de la REST API de WordPress que es la URL: tipo, ID, estado, `modified_gmt` y entradas de un termino; por `url_hash` o el otro esquema, sin sondas (decision 168). Sin tabla, lista vacia |
| GET | `/api/jobs/{id}/urls/{url_id}` (campo `capturas_alternativas`) | La ficha de la URL lleva sus capturas con otro perfil: estado, diferencias, volatiles, Vary y el destino de un m-dot (decision 119). Sin tabla, lista vacia |
| GET | `/api/jobs/{id}/urls/{url_id}/raw-html` | HTML guardado de una URL (solo con `extraction.store_raw_html`). Endpoint aparte y no un campo del detalle: 170 kB de media por pagina |
| GET | `/api/jobs/{id}/export` | CSV export (streaming, 1000-row windows) — **85 columnas**: URL + metadatos + `click_depth` (clics desde la home, decision 90) + **h1/h2** (solo los que se pintan), og/twitter, hreflang, tipos de datos estructurados, imagenes sin alt y cabeceras de seguridad. Las de M1: `content_word_count`, `content_hash`, `pagerank_score` y `pagerank_raw` (la 0-100 es la que hay que leer y ordenar, decision 34), `pagerank_fiable` (decision 35), `blocked_by_robots` (decision 48, con `status_code` VACIO porque la URL no se pidio), `response_class` (decision 103), `bloqueada_googlebot` (decision 113, vacio = no evaluado) y los dos `*_pixel_width` (decision 54), agregados por lote (4 consultas por ventana, no 4 por URL). `content_text_first_500` dice en el nombre que va recortado; el texto entero es `/content/export`. Con reglas a medida, columnas `x_<nombre>` AL FINAL (decision 104) |


## Extraction Functions (`extractors.py`)

All pure functions — no Scrapy imports. First candidates for unit tests.

| Function | Returns |
|----------|---------|
| `extract_meta(selector)` | dict — title, description, canonical, OG, Twitter, robots |
| `extract_headings(selector)` | list[dict] — tag, position, text (excludes template/noscript/svg) |
| `extract_links(selector, base_url, hosts)` | list[dict] — to_url, anchor, rel, position, follow, type |
| `extract_hreflang(selector)` | list[dict] — lang, href |
| `extract_structured_data(html, url)` | list[dict] — raw, format (jsonld/microdata/rdfa), schema_type |
| `extract_resources(selector, base_url)` | list[dict] — url, type, alt, width, height, mixed_content |
| `contar_palabras(texto)` | int — tokens, y caracteres en las escrituras sin espacios (CJK, tailandes) |
| `extract_word_count(selector)` | int — palabras del texto visible del body |
| `extract_visible_text(selector)` | str |
| `extract_main_content(selector)` | str or None — boilerplate-free text |
| `extract_main_content_markdown(selector)` | str or None — content as Markdown |
| `extract_meta_refresh(selector)` | str or None |
| `extract_videos(selector, base_url)` | list[dict] — `{tipo, id, src, decorativo}` de `<video>` y reproductores de YouTube/Vimeo, como mucho 20 (decision 155). `extract_meta` trae ademas `amphtml` y `alternate_movil` (decision 156) |
| `head_html.leer_head(html)` | dict — `cortado_por` (etiqueta que cierra el head antes de tiempo) y `fuera_del_head` (metadatos en el body), como Chromium con scripting (decision 102) |
| `extract_security_headers(headers)` | dict — HTTPS, HSTS, CSP, X-Frame, etc. |
| `extract_custom(selector, html, comp, **kw)` | list[dict] — filas de las reglas a medida (envuelve `shared.extraccion_a_medida.aplicar`, decision 104) |

## SEO Analysis Checks (`analyzer.py`)

The `SEOAnalyzer` class runs 17 check methods and populates the `issues` table:

| Method | Issue Types Detected |
|--------|---------------------|
| `analyze_status_codes()` | status_4xx, status_5xx (sin los muros), **respuesta_no_valida** por host y clase (decision 103) |
| `analyze_titles()` | missing_title, title_too_short, title_too_long, duplicate_title, **title_generico** (placeholder del CMS por valor, `shared/titulos.py`; tapa su `title_too_short` y su `title_duplicate`: decision 121) |
| `analyze_descriptions()` | missing_description, description_too_short, description_too_long, duplicate_description, **description_igual_title** (por host y plantilla, decision 121) |
| `analyze_headings()` | missing_h1, multiple_h1 (solo los h1 con texto), **h1_vacio** (no en censos anteriores a la decision 46; decision 121) |
| `analyze_saltos_de_encabezado()` | **encabezado_salta_nivel** (info, por host y plantilla; titulares visibles y con texto; no en censos anteriores a la decision 46). Decision 153 |
| `analyze_canonicals()` | canonical_missing, canonical_multiple, canonical_in_body, canonical_cross_domain, canonical_a_http y lo que hay en el destino: **canonical_a_error, canonical_a_redireccion, canonical_a_no_indexable, canonical_encadenado** (decision 101). El canonical es el del HTML o, si no hay, el de la cabecera `Link` (`fuente: cabecera`); **canonical_html_y_cabecera_distintos** y **noindex_y_canonical_a_otra** (por plantilla, sustituye al `noindex_page`), sin evaluar las meta refresh (decision 120) |
| `analyze_hreflang()` | hreflang_missing_return, hreflang_invalid_lang, hreflang_broken_target, hreflang_in_body, y los de la decision 99: hreflang_sin_autorreferencia, hreflang_sin_x_default (por host), hreflang_idioma_repetido, hreflang_destino_no_indexable, hreflang_retorno_incoherente; y hreflang_fuentes_discrepan (decision 167, uno por host y combinacion de fuentes). Las anotaciones son la UNION del `<head>`, la cabecera `Link` y el sitemap (`sitemap_entries.hreflang`); lo que solo declara el sitemap lleva `origen: sitemap` en el detalle, y con el sitemap leido a medias no se afirman ausencias. Solo sobre paginas HTML indexables; las anotaciones repetidas cuentan una vez (decision 116) |
| `analyze_structured_data()` | structured data validation |
| `analyze_html_ignorado()` | html_supera_2mb (por pagina, indexables), head_cortado y meta_fuera_del_head (agrupados) — decision 102 |
| `analyze_robots_txt()` | **robots_txt_inaccesible** por host: robots.txt con 5xx, 429 o sin respuesta (`error`; `warning` si a la segunda respondio o si es un 429). Carga `robots_txt` para la indexabilidad (decision 112, logica en `analysis/robots_txt.py` y `shared/robots_txt.py`) |
| `analyze_indexability()` | indexability status; con `robots_txt` guardado, `urls.bloqueada_googlebot` (Protego con el token `Googlebot`) y **robots_difiere_para_googlebot** por host (decision 113); por host, `robots_txt.evaluacion.bots` con 12 bots de buscador e IA y **bot_busqueda_ia_bloqueado** (info) si uno de busqueda de IA tiene cerrado el sitio entero (decision 129) |
| `avisos_de_nosnippet.avisar()` | **data_nosnippet_contenido_principal** (warning) y **data_nosnippet_ignorado** (info), por (host, plantilla), solo indexables 2xx sin `nosnippet` de pagina; lee `html_meta.data_nosnippet` (decision 170) |
| `analyze_robots_syntax()` | robots_invalid_syntax (directivas pegadas con `/`, `\|` o `;`; de `X-Robots-Tag`, solo lo que va a Google, decision 107) |
| `analyze_duplicates()` | content duplicates (byte-idénticos) |
| `analyze_near_duplicates()` | near_duplicate_content (MinHash, umbral 0.9 configurable), solo internas e indexables como `analyze_duplicates` (decisión 118) |
| `analyze_redirect_chains()` | redirect_chain (uno por cadena, `error` pasados 10 saltos: decision 117), redirect_loop, **redireccion_a_error** (decision 101), **redireccion_a_noindex** y **meta_refresh_redireccion** (retardo con `shared/meta_refresh.py`; decision 120) |
| `analyze_images()` | image_missing_alt |
| `analyze_security()` | http_url, mixed_content, missing_hsts, missing_csp |
| `analyze_content()` | low_word_count (sobre el contenido propio, solo en indexables) |
| `analyze_soft_404()` | **soft_404** y **soft_404_candidato** (decision 105, logica en `analysis/soft404.py`): huella de la sonda, titular de error, busqueda sin resultados; descatalogados y huellas de plantilla como candidatos. Tapa en la misma pagina lo que describe su contenido (`TAPADAS`: contenido escaso, titulo, description, h1, duplicados) |
| `avisos_de_accesibilidad.avisar()` | **accesibilidad_grave** (impacto critico o grave de axe, warning) y **accesibilidad_menor** (moderado o leve, info), nunca error: una fila por (regla, plantilla) con `paginas_afectadas`/`paginas_muestreadas`, elementos, criterios WCAG y ejemplos; lo `incomplete` de axe no avisa (decision 174) |
| `analyze_paridad_movil()` | **paridad_movil_indexacion** (noindex/canonical error, nofollow aviso), **paridad_movil_contenido** (title, H1, palabras, enlaces aviso; description info) y **paridad_movil_respuesta** (otro codigo o redireccion aviso; m-dot bien anotado info), sobre `capturas_alternativas`: una fila por (perfil, plantilla, campo) con `paginas_afectadas`/`paginas_muestreadas` (decision 119) |
| `analyze_url_issues()` | url_too_long, url_non_ascii, url_uppercase, url_underscores, url_multiple_slashes, url_has_parameters, url_non_seo_friendly, url_cms_faceted |
| `analyze_links()` | orphan_page (cede ante `solo_enlazada_desde_paginacion_excluida`, decision 122), **solo_enlazada_por_redireccion** (decision 63), **canonical_sin_enlazar** (decision 101), **not_reachable_from_home** y `click_depth` (decision 90, en `compute_link_counts`), high_outlink_count, y las metricas del grafo (inlinks, outlinks, pagerank). `not_reachable_from_home` tambien cede ante la paginacion |
| `analyze_sitemap()` | sitemap_orphan (solo indexables; cede ante la paginacion, decision 122), not_in_sitemap, **sitemap_no_indexable** (decision 100, con el motivo en `details`) |
| `avisos_de_servidor.py` | **respuesta_lenta** por (host, plantilla) con la mediana del TTFB (`respuesta_lenta_ms` 1.000 / `respuesta_lenta_grave_ms` 3.000, `respuesta_lenta_min_paginas` 20), **html_sin_comprimir** por host, y **tls_certificado_invalido**, **tls_caduca_pronto** (`tls_dias_aviso`, 7) y **tls_version_antigua** por host desde `jobs.tls`. Decisiones 109 y 110 |
| `avisos_de_enlaces.py` | **enlace_interno_a_error**, **enlace_interno_a_redireccion** y **utm_en_enlace_interno** y **enlace_sin_ancla** (decision 121) (sobre la temporal de aristas de `compute_link_counts`) y **enlace_no_rastreable** (sobre `enlaces_no_rastreables`). Por pagina de origen salvo lo de plantilla (`enlaces_plantilla_min_paginas`, 10), que se avisa una vez; `utm` por destino. Decision 106 |
| `avisos_de_html.py` | Lo que el HTML declara de si mismo (decision 108, reglas en `shared/metadatos_html.py`): **viewport_ausente** y **viewport_incorrecto** (warning), **texto_mal_codificado** (warning), **canonical_con_fragmento** (warning si apunta a otra pagina, info si a la propia), y en info **html_lang_ausente**, **html_lang_no_coincide_hreflang**, **charset_incoherente** y **charset_ausente** (por host), **canonical_relativo**, **og_ausente**, **og_image_relativa** y **redireccion_por_javascript** (por pagina, sin render). Agrupado por (host, `firma_de_ruta`) y anclado en la URL menor; NULL (censo anterior o con render) no se afirma |
| `avisos_de_medios.py` | Decisiones 154-156, por (host, plantilla[, motivo]) y solo indexables: **og_image_pequena** y **og_image_rota** (info, de `respuestas_sondeadas` con `uso='og_image'`; sin medida no se afirma), **twitter_card_invalida** (info), **video_sin_sitemap_de_video** (info, solo si el sitio tiene sitemap de video: `sitemap_entries.videos`), **amphtml_incorrecto** y **alternate_movil_incorrecto** (destino en el censo con error, redireccion o sin canonical de vuelta; con `_Fichas`). Resumen en `jobs.sondas_resumen.og_image` |
| `avisos_de_paginacion.py` | Paginacion (decision 122). Paso `analizar_paginacion` (antes de `compute_link_counts`, sobre `urls` + `html_meta` + los botones de `enlaces_no_rastreables`): reconoce las paginas 2+, las excluidas y lo que cada pagina anuncia como siguiente, sin emitir. Paso `desde_aristas` (sobre `aristas_tmp`, antes de la profundidad de clic, con `origen_pag_excluida`, `origen_pag_profunda` y `rel_destino_url` de la misma lectura de `links`): descarta las cadenas de `rel` entre entradas y emite **paginacion_canonical_a_la_primera**, **paginacion_noindex** (warning si deja indexables colgando), **solo_enlazada_desde_paginacion_profunda**, **paginacion_sin_enlace** (rel o boton sin ningun `<a>` a la siguiente), **paginacion_siguiente_rota** y **paginacion_rel_incorrecto**, por plantilla, y deja **solo_enlazada_desde_paginacion_excluida**, por pagina, que sustituye a orphan_page/sitemap_orphan/not_reachable_from_home |
| `avisos_de_enlaces.py` | **enlace_interno_a_error**, **enlace_interno_a_redireccion** y **utm_en_enlace_interno** (sobre la temporal de aristas de `compute_link_counts`) y **enlace_no_rastreable** (sobre `enlaces_no_rastreables`). Por pagina de origen salvo lo de plantilla (`enlaces_plantilla_min_paginas`, 10), que se avisa una vez; `utm` por destino. Decision 106 |
| `avisos_de_sondas.py` | **enlace_externo_roto** (uno por destino, con `paginas_afectadas`) y **dominio_externo_muerto** (uno por host; sobre `externo_hash` de la temporal de aristas), **broken_resource** (uno por recurso) y `resources.size_bytes`, de `respuestas_sondeadas`; resumen en `jobs.sondas_resumen`. Decisiones 123 y 124 |
| `avisos_de_wordpress.py` | Decision 161, de `adapter_entities` y `jobs.adapters`, todo agrupado: **wp_publicado_no_rastreado** (warning, por host y tipo; solo en un censo entero: ni truncado, ni con `max_depth` alcanzado, ni con filtros de URL, y sin lo que ya es `orphan_page`), **wp_adjunto_indexable** (warning, por host: del censo o de la muestra), **wp_taxonomia_anemica** (info, por host y taxonomia: rastreado, indexable y con `wp_taxonomia_anemica_max` entradas o menos) y **wp_paginacion_sin_fin** (pagina N+1 con 200; warning si indexable, info si no; nada si el host tiene soft 404 de serie). Resumen en `jobs.adapters.wordpress.analisis` |
| `avisos_de_sitemaps.py` | **sitemap_supera_limite** (uno por fichero: >50.000 entradas o >50 MB sin comprimir), **lastmod_futuro** (uno por fichero, contra `leido_at` + 1 dia) y **en_varios_sitemaps** (info, uno por combinacion de ficheros, con `paginas_afectadas` y `lastmod_distinto`), de `jobs.sitemaps` y `sitemap_entries`. Criterios en `shared/sitemaps.py`. Decision 126 |

Configurable thresholds via `job.config.analysis_thresholds` JSON or module-level constants.

## Scrapy Item Types

| Item Class | DB Table | Relation |
|------------|----------|----------|
| `PageItem` | `urls` | One per response |
| `HtmlMetaItem` | `html_meta` | 1:1 with urls |
| `HeadingItem` | `headings` | N per url |
| `LinkItem` | `links` | N per url |
| `HreflangItem` | `hreflang` | N per url |
| `StructuredDataItem` | `structured_data` | N per url |
| `ResourceItem` | `resources` | N per url |
| `ContentItem` | `page_content` | 1:1 with urls |
| `SecurityItem` | `security_headers` | 1:1 with urls |
| `EnlacesNoRastreablesItem` | `enlaces_no_rastreables` | uno por pagina con todas sus filas; la reemplaza entera (decision 106) |
| `CapturaAlternativaItem` | `capturas_alternativas` | una por URL muestreada y perfil, de la sonda de paridad, la de render (140) o la de axe (174); reemplaza la anterior (decision 119) |
| `RespuestaSondeadaItem` | `respuestas_sondeadas` | uno por URL sondeada; upsert por (job_id, url_hash) (decisiones 123 y 124) |
| `EntidadesAdaptadorItem` | `adapter_entities` | uno por respuesta de la REST API de WordPress o por sonda, con sus entidades; sin borrado por URL: el spider borra las del job al empezar a leer (decision 161) |
| `SitemapEntradasItem` | `sitemap_entries` | uno por fichero de sitemap con sus entradas internas; la primera vez en cada proceso borra las del job, porque cada proceso vuelve a leer todos los sitemaps (decision 126) |

## Middleware Stack

| Middleware | Priority | Purpose |
|-----------|----------|---------|
| `JobConfigMiddleware` | 100 | Injects per-job settings from Redis/DB |
| `UserAgentMiddleware` | 400 | UA rotation (replaces Scrapy default) |
| `ProxyMiddleware` | 410 | Optional proxy rotation from `PROXY_LIST` |
| `HttpConfigMiddleware` | 420 | Bloque `http` del job. Cabeceras propias, cookies y Basic **solo** a los hosts de las semillas (con y sin www, subdominios con `crawl_subdomains`; no el host al que salta una semilla) y se quitan de las demas; `Accept-Language` a todas. Sin jar, las cookies van como cabecera `Cookie`; con render, en `meta["_http_render"]` para `render.preparar_pagina` (decision 114) |
| `CookiesTrasElJarMiddleware` | 705 | Solo con `COOKIES_ENABLED` (`http.conservar_cookies`): tras el `CookiesMiddleware` de Scrapy vacia `request.cookies` para que curl_cffi mande la cabecera del jar (decision 114) |
| `RobotsCrawlDelayMiddleware` | 110 | `Crawl-delay` de robots.txt como retraso del slot, solo en `respect` (decision 81) |
| `RobotsTxtRegistroMiddleware` | 90 | Guarda en `robots_txt` la respuesta FINAL del robots.txt de cada host interno (por debajo de Retry y Redirect), y si fallo lo pide otra vez al quedarse el spider sin trabajo. Solo anota: lo que se pide no cambia (decision 112). Ajustes: `ROBOTS_TXT_MAX_HOSTS` (200), `ROBOTS_TXT_COMPROBAR` (True) |
| `RetryAfterMiddleware` | 560 | `Retry-After` de un 429/503 como pausa del slot con tope, en todos los `robots_mode`; vuelve a `piso_de_slot` al caducar (decision 111) |

## Scrapy Settings

| Setting | Value | Notes |
|---------|-------|-------|
| `CONCURRENT_REQUESTS` | 32 | Env-overridable |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | 8 | Env-overridable |
| `DOWNLOAD_TIMEOUT` | 30s | |
| `RETRY_TIMES` | 2 | Only for 502, 503, 504, 408, 429 |
| `ROBOTSTXT_OBEY` | True | Overridable per-job |
| `COOKIES_ENABLED` | False | Sin jar, como Googlebot; `http.conservar_cookies` lo enciende (worker, `-s`). Las de `http.cookies` se mandan igual (decision 114) |
| `AUTOTHROTTLE_ENABLED` | True | Target concurrency: 8.0 |
| `HTTPERROR_ALLOW_ALL` | True | All status codes reach spider (Screaming Frog parity) |
| `DEPTH_PRIORITY` | 1 | BFS scheduling |
| `PIPELINE_BATCH_SIZE` | 200 | Child items buffered then bulk-inserted |
| Playwright | chromium, headless | JS rendering via `scrapy-playwright` |
| `PLAYWRIGHT_MAX_PAGES_PER_CONTEXT` | 8 | Env-overridable (`PLAYWRIGHT_MAX_PAGES`) |
| `PLAYWRIGHT_MAX_CONTEXTS` | 3 | Safety cap on browser contexts (env-overridable) |
| `playwright_page_init_callback` | `seo_crawler.render.preparar_pagina` | Es **meta** por peticion (`_playwright_meta`), no setting: el `PLAYWRIGHT_PAGE_INIT_CALLBACK` de settings.py no lo leia scrapy-playwright. Instala el contador de peticiones en vuelo (12b) y las cookies y credenciales del job (114) |
| JS auto-cap | 8 / 4 | When `render_js=true`, worker auto-caps concurrency (env-overridable) |
