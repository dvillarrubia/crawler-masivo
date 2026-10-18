# Tests

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## Testing

Unit test suite at `tests/` (contado el 2026-10-16: **977 casos** en la imagen
del crawler, 939 pasan y 38 se saltan con Postgres y Redis de pruebas —los que
necesitan FastAPI o Pydantic—; y **392** en el trabajo de la API, que repite
parte de los anteriores y corre los que necesitan FastAPI —`test_insights.py`,
`test_export_csv.py`, `test_raw_html.py`, los de extraccion a medida...—).
Entre los del crawler hay varios que SOLO corren con servicios de verdad: `test_pagerank_db.py` (27, Postgres) y el de la
cola contra Redis. Para destaparlos en local:

```bash
docker compose exec -T postgres createdb -U crawler crawler_test
docker run --rm --network crawler-masivo_default -v "$PWD":/repo -w /repo \
  -e PYTHONPATH=/repo:/repo/crawler:/repo/tests \
  -e PAGERANK_TEST_DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_test \
  -e REDIS_TEST_URL=redis://redis:6379/9 \
  crawler-masivo-crawler bash -lc "pip install -q pytest; python -m pytest /repo/tests -q"
```

Sin la red de compose y los nombres de servicio no valen: apuntar
`REDIS_TEST_URL` a `127.0.0.1` desde otro contenedor hace fallar el test de la
cola, y parece un fallo del codigo. Lo que cubren: pure extractors
(`test_extractors.py`), main-content extraction / boilerplate stripping
(`test_content_extraction.py`), structured-data validation
(`test_sd_validation.py`, con los resultados retirados de la decision 97),
presets de User-Agent contra el middleware de robots real (`test_ua_presets.py`,
decision 98), el enlace del aviso contra las rutas de `_irARuta`
(`test_avisos.py`), sitemap parsing (`test_sitemaps.py`) and
casi-duplicados (`test_near_duplicates.py`, que contrasta la similitud
estimada contra la Jaccard exacta, y fija la firma de un texto latino y la
tokenizacion de CJK, hindi y tailandes, decision 118) y un test de integracion del spider
(`test_spider_rastreo.py`: lanza el `SeoSpider` real contra un sitio servido en
local por `spider_harness.py`, con BD y Redis simulados — redirecciones, meta
refresh, sitemaps, patrones y hrefs malformados, tambien en el modo en que la
redireccion la sigue el navegador). `test_spider_muro.py` usa el mismo arnes
con el desafio real de Cloudflare de progym, el cuerpo binario y un 429 masivo
(decision 103), y `test_spider_muro_unidad.py` llama a `parse` y a
`spider_opened` de verdad para el render, los hosts externos y la reanudacion
que repite los muros; las clases van en
`test_respuestas.py`, el analizador en `test_analyzer_muros_db.py` y el worker
en `test_worker_bloqueo.py`. `test_credenciales_y_cookies.py` (decision
114) usa el arnes con `http_real` (los middlewares del bloque `http` y el jar
como lo deja el worker), `descarga_real` (curl_cffi y Playwright con los
ajustes de settings.py; `chromium` para un ejecutable concreto) y
`ver_cabeceras` (vuelca las cabeceras que llegan al servidor): las
credenciales solo llegan a `localhost` y nunca a `127.0.0.1` (un tercero: su
robots.txt, el destino de una redireccion y un enlace externo), las sondas las
llevan, las cookies del servidor no se guardan salvo con `conservar_cookies`, y
con render las configuradas llegan a la pagina. Los de render buscan un
Chromium que lance (el de Playwright o uno en `PLAYWRIGHT_BROWSERS_PATH`) y se
saltan sin el. Y `test_analyzer_near_duplicates_db.py`, que
pasa `analyze_near_duplicates` contra SQLite en memoria (alcance de la
decision 118, limpieza al re-analizar y el `--dry-run` del script), y `test_worker.py`
(que errores del spider suben al log del worker). PageRank: `test_pagerank.py`
(modelo sobre grafos pequenos) y `test_pagerank_db.py`, que necesita Postgres
de verdad y se salta sin `PAGERANK_TEST_DATABASE_URL` (instrucciones en el
fichero; nunca apuntarlo a `crawler_db`). Del contrafactual por columna salen
tres mas: `test_export_csv.py` (cada columna declarada del CSV tiene su valor
en la fila; sin esto, anadir una y olvidar el dato desplaza en silencio todas
las de la derecha), y `test_render_espera.py` (contrato de la espera de render:
no resolver con peticiones en vuelo).
Hreflang (decision 99): `test_hreflang_completo_db.py` ejecuta
`analyze_hreflang` contra SQLite (autorreferencia, x-default por host, idioma
repetido sin N², destino no indexable, retorno incoherente y que una fuente no
indexable no da avisos; la autorreferencia por una variante canonicalizada a
la propia pagina, el host y el ancla del x-default sin depender del orden de
las filas, e `indexable` a NULL), y `test_catalogo_incidencias.py` falla si el
analizador emite un tipo sin ficha en `frontend/incidencias.js` (los tipos
salen con `ast`; una llamada con tipo no literal tiene que declararse).
Extraccion de #149: `test_esquemas_y_credenciales.py` (lista blanca de
esquemas en `extract_links` e `is_internal_url`, credenciales fuera al
normalizar y semillas con credenciales rechazadas; decisiones 106 y 39),
`test_hreflang_repetido_y_cabecera.py` (deduplicacion y hreflang de la cabecera
`Link`, extractor y `analyze_hreflang` contra SQLite; decision 116),
`test_canonical_distintos.py` (destinos distintos del canonical; decision 115),
`test_cadenas_largas_db.py` (un aviso por cadena y error pasados 10 saltos;
decision 117) y `test_spider_cabeceras_y_saltos.py` (spider real: `ftp://` y
`ws://` internos no se rastrean, hreflang de un PDF por cabecera, tope de 20
saltos).
Re-analisis de #150: `test_senales_contradictorias_db.py` (canonical solo en
la cabecera, HTML frente a cabecera, `noindex` con canonical a otra,
redireccion a una noindex, meta refresh con y sin retardo, `_Fichas` siguiendo
la meta refresh, y `run_all` dos veces con el mismo resultado en SQLite y
Postgres; decision 120) y `test_titulares_titulos_anclas_db.py` (H1 vacio con
la guardia de la decision 46, `title_generico`, `description_igual_title` y
`enlace_sin_ancla` sobre la temporal de aristas con una sola lectura de
`links`; decision 121).
Capa de decisiones (decision 84): `test_llm.py` (transporte: reintentos,
429/AIMD, cortes), `test_decisiones.py` (agrupacion, cache, presupuesto,
confianza), `test_decisiones_api.py`, `test_decisiones_no_se_disparan.py` y
`test_llm_contrato.py` (contra los SDK oficiales; necesita
`tests/requirements-contrato.txt` y corre en su propio trabajo de CI). Todos
con el `FakeLLM` de `tests/fake_llm.py`: **ningun test llama a la red**, y
`conftest.py` hace fallar cualquier transporte real hacia OpenRouter.
Analisis enriquecido (decision 85): `test_enriquecido.py` (modulos y
dependencias, alcance, estimacion sin llamar, corte por presupuesto y por tope
mensual, relanzar a coste 0, cancelar, reanudar, la cola del worker) y
`test_enriquecido_api.py` (token: desactivado, incorrecto y nunca en una
respuesta; validacion, 409, cancelar), los dos tambien en el trabajo de la API.
Borrado en masa y espacio (decision 96): `test_borrado_en_masa.py` (que se
borra TODO lo del censo y nada de lo demas, los dos guardias, que `forzar`
salta el de la serie y nunca el de en marcha, que `simular` no toca nada, el
presupuesto de tiempo y que repetir termina, que un censo a medio borrar queda
en `deleting` y no es referencia ni se archiva, y que el Parquet de un censo archivado se mide, se borra con el y se
queda con su job si el borrado queda a medias) y `test_almacenamiento.py` (la
cuenta de bytes como funcion pura, que un «no se sabe» no se convierta en un
cero, que un censo archivado tiene tamano sin gastar presupuesto, y la
medicion contra Postgres con `ALMACENAMIENTO_TEST_DATABASE_URL`). Los dos
corren tambien en el trabajo de la API.
Soft 404 (decision 105): `test_soft404.py` (las reglas puras —sonda, eco, titulares
seguros y dudosos en ocho idiomas, busquedas, disponibilidad— y el analisis contra
SQLite: huella exacta y MinHash, sonda 404, redireccion y muro de WAF sin huella,
huella de plantilla y portada como candidato agrupado —tambien la de la raiz
mirada por plantilla—, pagina de error larga con la sonda recortada, senales
sin sonda y `run_all` EJECUTADO en SQLite y en Postgres para ver que la
`soft_404` no se queda con `low_word_count`, `title_duplicate`...) y
`test_sonda_soft404_spider.py` (el `SeoSpider` real: una sonda por host —tambien
el host al que redirige la semilla— y por plantilla, que no sale como URL y que
no gasta `max_urls`; la repeticion de una sonda que solo cambia de esquema; el
plan del script). `test_insights.py` comprueba que una soft 404 no sube la nota
de contenido.
Segmentos de negocio (decision 125): `test_segmentos.py` (precedencia, Y/O/NO,
logica de tres valores, muros, externas y recursos fuera de las reglas y
validacion; el esquema de `JobConfig` en el trabajo de la API),
`test_segmentos_db.py` (`run_all` materializa, re-segmentar es retroactivo,
funciona con los enlaces archivados; SQLite y Postgres; y, solo en Postgres, que
los tramos van por `uq_job_url` mirando el plan de las consultas que manda),
`test_segmentos_api.py` (filtro en `/urls`, CSV, `/issues`, `%` por segmento con
su denominador, agregadas que no desaparecen, la herencia cliente -> perfil ->
job con condiciones `x`, agrupar, `POST /segmentos`, 422/409 y `api/uso.py`) y
`test_segmentos_frontend.py` (ejecuta `app.js` en Chromium y mira lo que pide:
la lista de Problemas conserva el segmento y la vista agrupada manda siempre
`agrupar`).
Extraccion y busqueda a medida (decision 104): `test_extraccion_a_medida.py`
(la funcion pura: CSS, XPath, regex, `a_numero`, muro, tiempo y
cortacircuitos, validacion, firma; y el esquema y la herencia, que necesitan
Pydantic y corren en el trabajo de la API), `test_a_medida_api.py` (filtros y
CSV con las mismas filas, «no evaluada» frente a «sin coincidencia», 422 por
regla inexistente, orden, una columna por posicion y formulas, `/export`,
prueba de reglas y su tope de pruebas a la vez, uso, y fusion al guardar un
perfil o al cambiar el cliente; trabajo de la API),
`test_spider_a_medida.py` (el `SeoSpider` real con el arnes: lo emitido, la
marca `config_invalida` en Redis y, con Postgres, lo guardado tras reanudar,
404, 301 y repetir), `test_job_vacio.py` (`_run_job` ejecutado: una
reanudacion con reglas que no compilan sale `failed`/`config_invalida`),
`test_backup_a_medida.py` (ida y vuelta del backup con los `url_id`
reasignados; trabajo de la API) y `test_a_medida_db.py` (600.000 filas en
Postgres con `_urls_filtradas` y `_ordenar` REALES: cada lectura de la tabla va
acotada por `job_id` o `url_id`; corre en el trabajo de la API con
`A_MEDIDA_TEST_DATABASE_URL`).

HTML que Google trunca o ignora (decision 102): `test_head_html.py` (17 casos
del head roto con lo que hace Chromium, y el contraste contra el navegador si
esta instalado; en CI lo esta) y `test_html_ignorado_db.py` (el check contra
SQLite, el spider guardando las columnas y `decodedBodySize` contra Chromium).

robots.txt por job (decisiones 112 y 113): `test_robots_txt.py` (funciones
puras: el estado con el criterio de Google, la tabla Chrome frente a Googlebot,
los ejemplos de precedencia de Google con Protego y el recorte a 500 KiB),
`test_spider_robots_txt.py` (el `SeoSpider` real con robots.txt 503, 500, 429,
timeout, 404, un desafio de Cloudflare y 200: la fila, los intentos, la
comprobacion al cerrar y que el rastreo sigue; con Postgres, que se guarda y que
reanudar la sustituye), `test_analyzer_robots_db.py` (el analizador contra
SQLite: un aviso por host, severidades, ancla estable, robots.txt que bloquea a
Googlebot y no a Chrome y al reves, `audit` e `ignore`, censo sin filas sin
cambios, re-analizar da lo mismo) y `test_backup_robots_txt.py` (ida y vuelta
del backup y `borrar_job`). El arnes admite `robots_obey`, `lentas` (ruta ->
segundos) y `settings`, y devuelve en `@@CIERRE@@` lo que anoto el middleware.

Enlaces que Google no puede seguir (decision 106): `test_enlaces_no_rastreables.py`
(los ejemplos de Google uno por uno, lo que NO se guarda, y que nada de ello
entra en `extract_links`), `test_avisos_de_enlaces_db.py` (agregacion por
origen y de plantilla, 4xx de plantilla sin aviso nuevo, `utm` por destino y
sustituyendo a `url_has_parameters`, censo truncado, tabla ausente; SQLite),
`test_spider_no_rastreables.py` (el `SeoSpider` real: el destino no se rastrea
ni es arista y, con Postgres, re-rastrear no duplica),
`test_pipeline_no_rastreables_db.py` (la tuberia real contra Postgres: sin la
tabla, o con un fallo al guardarlas, no se pierde ninguna pagina; una pagina que
ahora falla pierde las suyas), `test_incidencias_enlaces_frontend.py` (nombre,
ficha y ambito de los cuatro tipos en el frontend), el de `test_worker.py` que
ejecuta `_run_job` y mira que el analisis ya ve `finish_reason`, y
`test_backup_no_rastreables.py` (ida y vuelta del backup; trabajo de la API).

Lo que el HTML declara de si mismo (decision 108):
`test_extractors_declaraciones.py` (viewport, lang, charset con BOM, meta mas
alla del byte 1.024 y cabecera incoherente, forma del canonical y de la
og:image, la redireccion JS con los ocho casos de la guia de #150 y los de
minificado, regex, `ld+json` y `noscript`, y la meta refresh a `javascript:`),
`test_spider_declaraciones.py` (`SeoSpider.parse`: bytes latin1 servidos como
utf-8, con render todo lo del HTML servido a NULL; y el arnes con Postgres: las
columnas llegan a la BD y el destino JS no se sigue),
`test_avisos_de_html_db.py` (los doce tipos contra SQLite: agrupados por
plantilla y anclados en la URL menor, solo indexables, NULL de un censo viejo
sin aviso, muros fuera) y `test_incidencias_html_frontend.py` (ficha, nombre y
ambito de cada tipo; el catalogo general solo lee `analyzer.py`).
Tiempo, compresion, TLS y Retry-After (decisiones 109-111):
`test_handler_medidas.py` (el `SeoSpider` real con el handler de produccion
contra servidores locales: TTFB con 300 ms de espera, gzip e `identity`,
`http_version` sin render, un 429 con `Retry-After` en segundos y un 503 con
fecha HTTP con los huecos medidos en el servidor, y un HTTPS caducado que da
`tls_error` con su motivo y la ficha de la sonda; ademas Navigation Timing
contra Chromium), `test_sonda_tls.py` (la sonda contra certificados hechos en
el test: caducado, a 3 dias, otro nombre, autofirmado, solo TLS 1.0; y la
excepcion real de curl_cffi), `test_retry_after.py` (el parser y el
middleware con un slot simulado: sube, no lo baja AUTOTHROTTLE, vuelve al
Crawl-delay, tope, latido) y `test_avisos_de_servidor_db.py` (los cinco
avisos contra SQLite, con su ficha y su ambito en el frontend).
Paginacion (decision 122): `test_paginacion.py` (las formas de `shared/paginacion.py`,
`?p=123` sin evidencia, otros parametros, barra final, mayusculas),
`test_analyzer_paginacion_db.py` (SQLite: canonical a la 1 agrupado por plantilla,
autocanonica y «ver todo» sin aviso, serie por `rel=prev`, noindex agrupado, lo que
solo cuelga de una paginacion noindex o canonicalizada sin `orphan_page` ni
`sitemap_orphan`, `rel=next` sin `<a>` rastreada o no, `rel` a un 404 y a un 429; y,
con SQLite y con Postgres —`run_all` entero—, que el paso no anade ninguna sentencia
que lea `links`; y los `test_r*`/`test_e*` de la revision: muro en el destino del
`rel`, cadena de entradas de WordPress, boton «cargar mas» con y sin pagina 2, noindex
de fuera y `nofollow` que no son camino, el `warning` en la serie, la pagina 3 contada
en su serie, comparacion por `group_key` aunque se mueva el ancla, profundas, `rel`
que no coincide con el `<a>`, Elementor, variantes canonicalizadas a otra serie y que
cada clave de los detalles tenga nombre) y `test_incidencias_paginacion_frontend.py`
(ficha, nombre, ambito y columnas). `test_insights.py` comprueba que la nota de
Enlazado cuenta tambien `solo_enlazada_desde_paginacion_excluida`.
Paridad movil/escritorio (decision 119): `test_paridad_movil.py` (el
`SeoSpider` real con la descarga de produccion contra un sitio que sirve otro
HTML a un UA de movil —`rutas_movil` y `{aleatorio}` del arnes—: noindex,
canonical, menu, texto, m-dot con su anotacion, campo volatil, cupo por
plantilla, sin gastar `max_urls`, nada con la muestra a 0; con Chromium, el
contexto movil de verdad; y con Postgres, la tuberia, el analisis y que
reanudar no repite) y `test_paridad_movil_analisis.py` (la comparacion, los
perfiles, el preset del formulario, el contexto `custom` con el UA del job,
los avisos contra SQLite y el aviso de perfil distinto entre censos).
Sondas de respuesta (decisiones 123 y 124): `test_sondas.py` (veredictos, cuando
un HEAD no basta, tamano, SSRF, el mixin sin red: un robots por origen, tope por
host, recursos internos y externos por separado, latido y cancelacion),
`test_sonda_respuestas_spider.py` (el `SeoSpider` real con curl_cffi contra
servidores de «tercero» en 127.0.0.2 y 127.0.0.3 y un `x.invalid`: 404/410,
403 de WAF, 429, 5xx, HEAD 405 → GET, HEAD 404 confirmado con GET, redireccion a
un 404, robots.txt del tercero, host que rechaza conexiones, DNS inexistente,
ninguna credencial del job en el tercero, no gastan `max_urls`, topes; y una
pasada con el handler de Twisted), `test_avisos_de_sondas_db.py` (los avisos
contra SQLite: uno por destino, dominio muerto sin duplicar, lo dudoso sin aviso,
resolver caido, origen 404, recursos y `size_bytes`), `test_sondas_db.py`
(Postgres: upsert de la tuberia, reanudacion con el presupuesto gastado y el
`run_all` entero) y `test_backup_sondas.py` (trabajo de la API). El arnes
(`tests/spider_harness.py`) sirve ahora `HEAD` y, con `externos`, un servidor por
IP de tercero en el mismo puerto (`@@EXTERNAS@@`: metodo, ruta y cabeceras).
Run with
`pip install -r tests/requirements.txt && pytest`.
`scripts/prueba_espera_render.py` mide si la espera de render pierde el
contenido que llega por XHR (laboratorio con retardo, o URLs reales).
`scripts/check_content_quality.py <job_id>` compares, per URL template,
what the crawl stored against what the extractor, `extract_main_content` and a
Chromium render see right now — it is how content loss is caught after a crawl.
De la capa de analisis contra BD ya hay tres tests
(`test_analyzer_headings_db.py`, `test_analyzer_near_duplicates_db.py` y
`test_pagerank_db.py`); el resto se sigue verificando con las consultas SQL de
`docs/AUDITORIA_Y_VERIFICACION.md`.
Sitemap (decision 100): `test_analyzer_sitemap_db.py` ejecuta `analyze_sitemap`
contra SQLite: cada motivo de `sitemap_no_indexable` con su detalle, que un 429 o un 503
o una URL sin respuesta no se afirman, que una no indexable del sitemap no sale
ademas como `sitemap_orphan`, y que vale con los enlaces archivados.
Modo lista y veredicto (decision 128): `test_modo_lista.py` (JobConfig con
`max_depth=0`, el umbral absoluto en `comparar_censos` y, contra SQLite, la
comparacion automatica y el veredicto: 10 de 50 URLs clave a noindex es `falla`,
el control `pasa`, y sin referencia, truncado, bloqueado o sin terminar es
`no_concluyente`), `test_veredicto_api.py` (el endpoint con TestClient: 200
siempre) —los dos tambien en el trabajo de la API— y `test_modo_lista_spider.py`
(spider real: solo semillas y sus saltos, sitemap solo si se pide, y al
reanudar una lista no se rastrea la frontera). `test_modo_lista.py` cubre
tambien que relanzar la lista no pone en verde lo que sigue roto, que un
hallazgo es un motivo aunque lo diga tambien la alerta y que un rastreo
completo no toma una lista como referencia; `test_modo_lista_frontend.py`
(Chromium) que un perfil en modo lista se lanza sin sitemap desde el modal.
