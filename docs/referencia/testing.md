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
(`test_extractors.py`), que los atajos de CPU de `parse` dan la misma salida
que el camino de antes, item por item (`test_parse_atajos.py`, decision 215;
`PARSE_ATAJOS_PAGINAS=<carpeta>` le anade paginas que no pueden ir en el repo), main-content extraction / boilerplate stripping
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
`test_hreflang_sitemap.py` (decision 167: compactar/expandir y completitud en
`shared/sitemaps.py`, el spider real guardando el hreflang de cada entrada y el
tope, la tuberia real contra Postgres con el analisis encima, y la union de
fuentes, la discrepancia agrupada, las ausencias con sitemap a medias, la
ruta con doble barra y un fallo a medias de la lectura contra SQLite; la nota
de /insights con hreflang solo del sitemap va en `test_insights.py`),
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
La medida de una pregunta (decisiones 159 y 179): `test_evaluacion_decisiones.py`
(metricas, umbral, muestra y el script de punta a punta) y
`test_evaluacion_precision.py` (juego de precision con datos sinteticos: la
precision sale la de verdad, el recall no se afirma y el control lo estima).
Analisis enriquecido (decision 85): `test_enriquecido.py` (modulos y
dependencias, alcance, estimacion sin llamar, corte por presupuesto y por tope
mensual, relanzar a coste 0, cancelar, reanudar, la cola del worker) y
`test_enriquecido_api.py` (token: desactivado, incorrecto y nunca en una
respuesta; validacion, 409, cancelar), los dos tambien en el trabajo de la API.
`test_enriquecido_canibalizacion.py` (decision 175): el modulo por grupo de
punta a punta con el `FakeLLM` (pares del mismo idioma, la pregunta con
`solo_si` en su propia llamada y con el mismo cuerpo que en la evaluacion,
reanudar por la clave del estado), filtro, estado (ordenado por ruta, con la
canonica), busqueda por `url_hash`, estimacion, `solo_si` en la pantalla, el
efecto apagado y el `extraer` sin repetidos.
Tipo de pagina (decision 178): `test_tipo_pagina.py` (muestra por plantilla sin
la misma ruta con y sin www, estado, tokens de la estimacion, punta a punta con
una plantilla mixta, `propagable` solo con umbral, la taxonomia del cliente como
opciones (la del cliente hoy sobre la del job, con descripciones) y como clave de
cache, el aviso sin taxonomia, cada caso frontera en una sola opcion, `extraer` sin estados repetidos y el endpoint),
tambien en el trabajo de la API.
Coherencia de title y marcado (decision 177): `test_coherencia.py` (valores
del marcado y fragmentos visibles, que el corte no se coma el valor declarado,
que el title solo marca no se pregunte y la ruta en el estado del generico,
filtros contra SQLite, los tres modulos de
punta a punta con el `FakeLLM`, el score agrupado por nivel en la API y
`extraer` sin estados repetidos).
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
Prevision de disco (decision 225): `test_prevision_disco.py` (veredicto y
reserva, estimacion por el censo del perfil contra Postgres con los enlaces
archivados, y el 409 de `POST /api/jobs` que `forzar_disco` salta).
Retencion por perfil (decision 223): `test_retencion_db.py` contra Postgres
(que se vacia y que se queda, la evolucion conservada y sin recontar, un
vaciado no es base del incremental, apagada no toca nada, a medias se termina
en la pasada siguiente y el worker no falla si revienta).
Intencion (decision 176): `test_intencion.py` (preguntas y modulos, las
busquedas de la ventana y de la propiedad activa, sin Search Console lo dice, la
estimacion por busqueda distinta, de punta a punta con el `FakeLLM` y relanzar a
0 llamadas, la cache por cliente, el desajuste y la incidencia apagada, una
pagina de marca o con mas clics de marca que no se da por desalineada, las
reglas en orden de las preguntas, `tipos_schema` en el estado, la ventana sin
descargar o descargada a medias que no se da por «sin busquedas», y `extraer`
sin estados repetidos) y `test_intencion_api.py` (resultados de un choice, la
busqueda en la tabla, el desajuste por API y la tarjeta que ensena lo
preguntado aunque cambie `gsc_consultas`); los dos tambien en el trabajo de la
API.
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
Edicion y prueba en vivo (decision 166): `test_prueba_en_vivo.py` (curl_cffi de
verdad contra un servidor local: forma de la URL, todas las IP globales, IP fijada
con `CURLOPT_RESOLVE`, host con `%XX` rechazado y host internacionalizado fijado
en punycode, redireccion a IP privada o a un nombre que resuelve a una
cortada sin pedirla, tope de saltos y de bytes, `atender` con estado por regla,
y el hilo real del worker contra Redis con `REDIS_TEST_URL`; trabajo del
crawler), `test_prueba_en_vivo_api.py` (la API encola y espera, 422 sin encolar,
504 sin worker, codigos de lo que no pudo el worker, semaforo propio frente a
las pruebas locales; trabajo de la API),
`test_reextraer_a_medida.py` (el script contra SQLite, que muerto a medias no
deja nada cambiado, y, con Postgres, el candado del job),
`test_extraccion_modal_frontend.py` (`app.js` en Chromium: al volver a «Sin
cliente» las heredadas se lanzan, y cambiar de perfil conserva las propias),
el hilo de pruebas en vivo arrancado una vez en `main()` en `test_worker.py` y `test_cambios_extraidos_api.py` (la columna `cambios` con
los guardias de la 127),
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
contra Chromium), `test_handler_conexiones.py` (decisión 221: el handler real
reutiliza conexiones con keep-alive y no cuela cookies entre peticiones ni
hosts), `test_sonda_tls.py` (la sonda contra certificados hechos en
el test: caducado, a 3 dias, otro nombre, autofirmado, solo TLS 1.0; y la
excepcion real de curl_cffi), `test_retry_after.py` (el parser y el
middleware con un slot simulado: sube, no lo baja AUTOTHROTTLE, vuelve al
Crawl-delay, tope, latido) y `test_avisos_de_servidor_db.py` (los cinco
avisos contra SQLite, con su ficha y su ambito en el frontend).
`data-nosnippet` (decision 170): `test_data_nosnippet.py` (`medir` con HTML a
mano: solo `span`/`div`/`section`, booleano y sin caja, anidados, lo oculto, el
H1 y el contenido por lineas con un pie largo que no cubre el articulo;
`SeoSpider.parse` con el contenido principal real y un fallo que deja NULL sin
perder la fila; el aviso contra SQLite por plantilla; ficha, nombre y ambito).
robots.txt de prueba (decision 171): `test_robots_prueba.py` (casos limite de
comodines, `$`, grupos y longitud, el lote igual que una a una y el veredicto
contra Protego en los dos modos; `diagnostico`, con un test al azar de que
cuenta las mismas reglas que la evaluacion y el `User-agent:` vacio) y
`test_robots_prueba_api.py` (el endpoint con TestClient, 422, tope de coste
con comodines y URLs largas, el peor caso aceptado en segundos y el cuerpo
guardado; trabajo de la API).
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
Accesibilidad con axe (decision 174): `test_accesibilidad.py` (el resumen de
`axe.run` —todos los nodos, ejemplos recortados, shadow DOM, experimental sin
afirmar, un resultado roto es no evaluado—, la agregacion por regla y
plantilla, el axe vendorizado con su licencia y su README, y el analisis
contra SQLite: una fila por regla y plantilla anclada en la URL menor, nunca
`error`, incompletas, muros, 404 y no evaluadas sin aviso, render y paridad
sin mezclarse, sin tabla y re-analizar), `test_sonda_axe_spider.py` (el
`SeoSpider` real con Chromium contra un sitio con fallos conocidos: imagen sin
alt, boton sin nombre, contraste, campo sin etiqueta; CSP estricta, cargador
AMD, fallo solo tras el JavaScript, pagina limpia, cupo por plantilla y tope
total, con y sin render, sin gastar `max_urls`; apagado de serie),
`test_accesibilidad_api.py` (el endpoint con TestClient; trabajo de la API) y
`test_accesibilidad_frontend.py` (Chromium: las claves del modal solo si se
cambian, impacto, ambito y detalles, y la tarjeta que no afirma sin paginas).
Perfil y capturas de pantalla (decision 173): `test_capturas_pantalla.py`
(el almacen del volumen de archivo, la ficha y la imagen con TestClient y
SQLite, que la captura de un censo no se sirve desde otro, la referencia
automatica del mismo perfil, el hash del contenido entre perfiles, el preset
Chrome Android y los topes; en el trabajo de la API del CI) y
`test_capturas_pantalla_spider.py` (el `SeoSpider` real con Chromium: una por
plantilla y perfil con el tamano del dispositivo, la imagen la pide la
captura con el UA de cada perfil y no el render del censo, y con la paridad a
la vez; se salta sin Chromium). El borrado, en `test_borrado_en_masa.py`.
Huella TLS y UA (decision 132): `test_huella_en_la_peticion.py` (el
`SeoSpider` real con curl_cffi contra un servidor que anota lo que llega: UA,
`sec-ch-ua` y `sec-ch-ua-platform` de serie, de un config viejo con el UA de
124 y de una mezcla fijada a mano, que se avisa; y `descargar_crudo`) y
`test_huella_y_ua.py` (la lista de perfiles contra el curl_cffi instalado, la
validacion de `JobConfig.impersonate`, la deduccion de uno a partir del otro y
la herencia; tambien en el trabajo de la API).
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
Restos de #150 (decisiones 153-156): `test_dimensiones_imagen.py` (cabeceras
hechas a mano de PNG, GIF, BMP, JPEG base y progresivo con 40 KB de EXIF, WebP
VP8/VP8L/VP8X y AVIF; un JPEG cortado antes del SOF y un SVG dan None),
`test_extractors_medios.py` (videos propios, incrustados, diferidos, en
`noscript`, el fondo animado, el tope; `amphtml` y la alternativa movil solo
del head y sin hreflang; las reglas de la og:image y la twitter:card; los
`<video:video>` de un sitemap), `test_sonda_og_image_spider.py` (el `SeoSpider`
real: la og:image del tercero llega como GET con `Range: bytes=0-65535` sin
HEAD, se mide, la rota queda rota, el tope por plantilla y la muestra a 0),
`test_saltos_encabezado_db.py` y `test_avisos_de_medios_db.py` (SQLite: por
plantilla y URL menor, lo oculto y lo vacio, lo no medido sin aviso, sin
sitemap de video no falta nada, AMP y movil solo con el destino en el censo),
el de `test_sondas_db.py` con Postgres (`ancho`/`alto` por la tuberia y el
`run_all`) y `test_incidencias_medios_frontend.py` (ficha, nombre y ambito).
Sitemaps por fichero (decision 126): `test_sitemaps.py` (`leer_sitemap` cuenta
lo que pasa del tope, mide el XML descomprimido y sabe que un gzip cortado lo
pasaba), `test_sitemaps_criterio.py` (lastmod W3C, el dia de margen y los
limites de Google), `test_spider_sitemap_entradas.py` (el spider real con un
indice de varios ficheros: fichero y lastmod por entrada, `jobs.sitemaps`, el
tope de filas), `test_avisos_de_sitemaps_db.py` (los tres avisos contra SQLite,
su unidad y su ancla, y que reanudar no duplica en la tuberia) y
`test_sitemaps_api.py` (la ficha y el backup; trabajo de la API).
Evolucion por perfil (decision 130): `test_evolucion.py` (el agregado contra
SQLite y Postgres, que no lee `links` capturando el SQL, que lo escriben
`run_analysis` y el worker sin lanzar nunca, la serie con sus motivos y cortes
de version, y el relleno simulado que no deja nada), `test_evolucion_api.py`
(los endpoints, el panel y el backup; trabajo de la API) y
`test_evolucion_frontend.py` (graficas y tendencia en Chromium, con el
color de cada tramo y el rango minimo del eje, UX 3.1),
`test_contraste_frontend.py` (la escala de color pasa 4,5:1 en el tema claro
y en el terminal, midiendo en Chromium el color que resulta; UX 3.1),
`test_ficha_cliente_frontend.py` (rutas de las pestanas del cliente y la
referencia de «frente al anterior», en Chromium) y `test_ia_frontend.py`
(pestana IA, UX 2.9: el aviso de consulta repetida, la linea «Revisado con IA»
de Problemas que cuenta solo las paginas del problema y calla sin listas
completas, los textos de la clave; en Chromium con la API simulada, y las
parejas pregunta-problema contra el catalogo y el analizador).
`test_comparar_frontend.py` (Comparar sin mapeo, lo guardado al instante y el
desplegable por tipo, UX 2.13/V21/V22) y `test_migracion_frontend.py`
(«Validar migración», decision 192: la vista previa de cada caso comun contra
`shared.comparacion._aplicar_rutas`, en Chromium).
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
Trafico entre censos (decisiones 158 y 165): `test_trafico_entre_censos.py`
contra SQLite (ventanas, caidas y subidas por cambio, trasvase, general,
estacional, rupturas; y de la 165: contenido por `content_hash` con la regla de
palabras si falta en una o si `hash_comparable` dice que no —otra version del
crawler o de la config de extraccion—, la caida general con los grupos detras
de la frase de detalle y sin frase si no hay grupo que escribir, y el aviso al
completarse un pendiente: sale una vez por Slack con `publicar` simulado, no
sale sin Slack, lanzado a mano ni de un perfil competidor, y un fallo al avisar
no deshace la comparacion) y `test_trafico_cliente_api.py` (el endpoint de la
ficha del cliente con TestClient, sin los perfiles de la competencia; trabajo
de la API).
WordPress, interfaz (decision 168): `test_wordpress_ui_api.py` ejecuta contra
SQLite y Postgres (`PAGERANK_TEST_DATABASE_URL`) la entidad de WordPress en la
ficha de la URL (por `url_hash` o el otro esquema, nunca una sonda), «Solo en
WordPress» sin sondas y con busqueda, y el inventario de plugins y tema por los
CSS y JS con URLs reales del blog de Lopesan (copias de WP Rocket, `ver=1`,
sellos de tiempo, version igual a la de WordPress, muestra por profundidad),
y en Postgres los planes: la muestra no entra por `urls_pkey` cuando el censo
va detras de otro job, y la ficha va por `ix_adent_job_hash_alt` y no por
`ix_adent_job_tipo`;
`test_wordpress_frontend.py` (Chromium) la tarjeta del Resumen
(`resumenWordPress`: sin resumen o sin rastro no sale, publicadas frente a
leidas y censo por tipo, por que no se afirma, sin analizar) y que la casilla
del modal solo se manda si se cambia frente a lo heredado, y que con «Solo en
WordPress» abierto las flechas no abren fichas de la tabla oculta.
Topes por carpeta (decision 172): `test_topes_carpeta.py` (las reglas de
`shared/topes_carpeta.py`, la validacion y la herencia en `JobConfig`, el
herencia con otra grafia, `ClavesCompactas`, el `Presupuesto` y `corto`, `_con_topes` del worker y el `SeoSpider` real
con el arnes: lo que no cabe no se pide —tampoco desde el sitemap ni tras un
salto—, las semillas si, y el recuento a `jobs.topes_carpeta`),
`test_analyzer_topes_carpeta_db.py` (SQLite: con un tope cortado no hay
`orphan_page` ni `sitemap_orphan` en ninguna parte del censo, tampoco en otra
carpeta enlazada solo desde lo no pedido; sin corte, como siempre) y
`test_topes_carpeta_api.py` (informe y ficha; tambien en el trabajo de la API).
Nofollow interno (decision 183): `test_avisos_nofollow_db.py` (SQLite, se
ejecuta el analizador: `solo_enlazada_con_nofollow` sustituye a
`not_reachable_from_home` y no sale con un entrante seguible, desde una
`noindex`, con un tope que corto ni en el destino de una redireccion;
`enlace_interno_nofollow` por destino, de plantilla desde el umbral, sin aviso
a no indexables y sin contar la directiva de pagina; `pagina_nofollow`
agrupada; ninguna lectura mas de `links`; y que la regla del `rel` en SQL de
`shared/rel_de_enlace.py` da lo mismo que la de Python, en SQLite y en Postgres
con `PAGERANK_TEST_DATABASE_URL`, y que el extractor la usa) y, en
`test_analyzer_paginacion_db.py`, que la paginacion va antes que
`solo_enlazada_con_nofollow`.

Enlaces con filtros (decision 195): `tests/test_enlaces_filtros.py` ejecuta
`GET /links`, `/links/destinos` y `/links/resumen` con SQLite y, con
`PAGERANK_TEST_DATABASE_URL`, con Postgres; cada filtro, el estado del
destino y el orden, y que el censo vivo y archivado en Parquet devuelvan lo
mismo con lotes y grupos de filas pequenos. Necesita `pyarrow` (la imagen de la
API de desarrollo puede no traerlo: `pip install pyarrow`).
Configuracion en tres niveles y Ajustes (decision 196): `test_config_niveles.py`
(API con SQLite: `config-resuelta` dice de que capa sale cada clave y trae lo de
serie; el borrador de `POST /api/clients/config-resuelta` se valida como al
guardar y no guarda nada; el aviso del WAF solo con un rastreo anterior parado
o con muros, con sus cifras, y sin ese rastro nada; `GET /api/ajustes` sin el
valor de ninguna clave) y `test_config_niveles_frontend.py` (Chromium: los
limites y las opciones del formulario son los de `JobConfig` —necesita tambien
pydantic—, heredado/cambiado/«volver al heredado», una cabecera heredada no se
quita en silencio, al lanzar solo va lo de ese rastreo y el tope del canario, y
cambiar de cliente o quitarlo conserva lo cambiado).
Movil y barra del Explorador (revision de UX del 25-oct, 3.2 y 3.4; decision
199): `test_movil_frontend.py` carga `frontend/index.html` entero con Alpine
(de su CDN: sin red, se salta) en Chromium y una API simulada con nombres y URLs
largos. A 390 px, `document.documentElement.scrollWidth` <= 390 en clientes,
rastreos, historial del cliente, Resumen y URLs; las listas sin cabecera de
tabla y con sus etiquetas; el desplegable de vistas con sus cifras. A 1440 px,
barra lateral, busqueda primero, URL fija y la tarjeta «Páginas HTML» con la
misma cifra que su entrada.
Cierre de la fase 3 (decisiones 202, 203 y 204): `test_pulido_frontend.py`
(Chromium, `app.js` suelto: filas por tipo sin sumar paginas que no se saben,
contador de Problemas, la diferencia sin «No incluida en el sitemap» dudosa,
paginas con error con su respaldo de la evolucion, alertas agrupadas, frases de
Google con sus paginas solo si el grupo es unico, «200 de 4.720», errores de una
API anterior en castellano, el tipo de serie del modal y los textos de V42).
Revision final de la fase 3 (decision 205): `test_revision_final_frontend.py`
(con el arnes de `test_movil_frontend.py`: la portada no cambia de unidad tras
abrir un cliente, la flecha del Historial en la misma unidad, «Revisado con IA»
en lineas, el Estado sin «No incluida en el sitemap» dudosa; y con `app.js`
suelto, Problemas con el anterior dudoso, el informe con una API anterior y la
ficha de Google con el censo ya cargado) y `test_sitemaps_lectura.py` (la regla
de `shared/sitemaps_lectura.py` frente a la del frontend, y el informe y el
aviso de Slack con SQLite).
`test_programaciones_perfil.py::test_el_worker_resuelve_la_config_igual_que_la_api`
se salta, diciendolo, en una imagen de la API sin parsel (anterior a la decision
104): no es un fallo del codigo, hay que reconstruir la imagen.
Revision de UX del 25-oct, 3.3 y 3.6 (decisiones 197 y 198):
`test_trafico_entre_censos.py` tambien junta en una frase la misma pagina de dos
grupos (y no lo hace sin saber que son las mismas) y comprueba que `GET
/api/jobs/{id}` redacta las frases al servirlas; `test_limpieza_repetido.py`
(lineas repetidas en la mitad de la muestra y el orden de `/cleaning/groups` con
TestClient y SQLite; necesita fastapi: correr en la imagen de la API) y
`test_google_limpieza_frontend.py` (Chromium: cifras y ventana de los datos del
bloque «Google», frases que solo dicen lo que se sabe, paginas una vez,
sitemaps vacios y su matiz, host dominante, base de cada alerta, y la celda,
el orden y el editor de la Limpieza).
La frontera en disco (#276, decision 216): `test_frontera_memoria.py` (el
`DupefilterCompacto` decide lo mismo que el de Scrapy, el conjunto con tope, y
el `SeoSpider` real con `JOBDIR`, con y sin meta de render, rastrea lo mismo que
con la cola en memoria y con TODA la cola en disco: `scheduler/unserializable`
a 0) y `test_frontera_reanudar_db.py` (Postgres de verdad: la frontera al
reanudar con la menor profundidad de sus origenes, sin lo que no se habria
seguido y sin perder destinos al paginar). La memoria con un millon de URLs
pendientes se mide con `scripts/bench/frontera_memoria.py` (`--modo antes|ahora`).
Los enlaces con `COPY` (#284, decision 217): `test_pipeline_copy_db.py` contra
Postgres (escapes del formato de texto, NUL, nulos, el camino sin `COPY` con
las mismas filas, lotes de una misma pagina y re-rastreo, y un fallo que tumba
el lote entero; desde la decision 222, tambien encabezados, hreflang, datos
estructurados y recursos, con el `raw` None como JSON `null`) y
`test_cancel_espaciado.py` (el cancel de Redis, una lectura por segundo y
recordado). El coste del pipeline por pagina se mide con
`scripts/bench/pipeline_por_pagina.py` (Postgres de pruebas, nunca `crawler_db`).
Lo que tarda cada fase del analisis en un censo sintetico grande (decision 226):
`scripts/bench/analisis_por_fases.py` (`--urls`, `--enlaces`; Postgres de pruebas).
Los hijos en un item por tipo (decision 219): el arnes (`Recolector`) y
`test_parse_atajos.py` despliegan los `HijosItem` con `items.desplegar()`, asi
que los tests que buscan `LinkItem` siguen igual.
La instalacion de cero (docs/INSTALACION.md): `test_init_db_vacia.py` pasa
`init_db()` sobre una base de datos desechable recien creada en el servidor de
`PAGERANK_TEST_DATABASE_URL` y comprueba que crea la extension `vector` y las
tablas, y que repetirlo no falla. Necesita un Postgres con pgvector y el paquete
`pgvector`, asi que corre en la imagen de la API, no en la del crawler, y en la
CI de hoy (`postgres:16-alpine`) se salta.
