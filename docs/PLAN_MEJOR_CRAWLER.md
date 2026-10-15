# Plan para que el crawler de Lin3s sea el mejor del mercado para una agencia SEO

> Base: `origin/master` a 2026-10-16 (último commit `d06280e`, borrado en masa = decisión 96), las 27 issues abiertas, ROADMAP, la sección 10b de AUDITORIA_Y_VERIFICACION e INVESTIGACION_EXPLORADOR. Todos los huecos se han comprobado contra el código, y donde la primera estimación de valor o esfuerzo no se sostenía se usa la corregida.

**La tesis.** Screaming Frog y Sitebulb ganan en **cuántas cosas comprueban**. Nosotros ya ganamos en **cuánto nos podemos fiar de lo que decimos**: sin falsos positivos, sin rastreos «completados» que no lo están, con comparación entre censos que no inventa cambios y alertas con denominador. Ser el mejor para una agencia es cerrar los checks que faltan sobre datos que ya guardamos, añadir los datos de negocio (GSC, extracción a medida, segmentos) y convertir la monitorización y el informe en entregables. Nada de esto depende de la capa LLM.

---

## 1. Dónde ya somos mejores, y cómo explotarlo

| Ventaja | Por qué nadie la iguala | Cómo explotarla |
|---|---|---|
| **El dato es honesto.** `completed` solo si está completo (dec. 28 y 32); el truncado se marca; `in_sitemap` NULL si la lectura fue parcial (50) | SF da por bueno un rastreo de 1 URL detrás de un WAF | Mostrarlo en el informe al cliente como «fiabilidad del censo», y en la propuesta comercial |
| **Un aviso por hallazgo, y la severidad según la consecuencia** (36, 53, 64, 73, 92) | Penguin: 226.651 incidencias de SD se quedan en 4. Lopesan: 315.119 incidencias totales se quedan en 38.922 | Usar el «antes y después» de Lopesan como caso de venta: el error grave quedaba enterrado bajo el ruido de las herramientas comerciales |
| **La comparación entre censos no afirma lo que no sabe** (67-71, 78, 86, 88, 89, 91) y alerta sola, con webhook (77) | Ninguna distingue «cambió el sitio» de «cambiamos nosotros» (sellos 66 y 72) | Es la base de la monitorización de cartera (ola 5). Cobrarla como servicio recurrente |
| **PageRank interno legible** (0-100 log), con techo por repetición, noindex que recibe pero no reparte, y fiabilidad del grafo que viaja con el dato (29, 34, 35, 38, 57) | SF da un «Link Score» sin decir si el grafo está incompleto por JS | Usarlo como eje de la priorización (ola 4) y de las sugerencias de enlazado |
| **Profundidad en clics desde la home y las portadas hreflang** (90), y huérfanas sin falsos positivos (36, 63) | Penguin: 26.116 huérfanas falsas se quedan en 639 | Entregable de arquitectura de información para migraciones |
| **Acceso a sitios difíciles**: huella TLS (11), espera de render por XHR en vuelo (12b), reanudación desde una frontera en BD (15, 21) | SF usa un AJAX timeout fijo, no imita TLS y no reanuda tras caerse el proceso | Mantenerlo vivo. **Riesgo:** el perfil por defecto `chrome124` envejece, así que hay que revisarlo cada trimestre (ola 0) |
| **Contenido sin plantilla** (9, 10, 44, 45, 79), duplicado por hash de contenido (47) y casi duplicados con MinHash como Jaccard (24-26) | blogs.uoc.edu: 0 duplicados midiendo por bytes, 745 midiendo por contenido | Es la materia prima de soft 404, segmentos y canibalización sin LLM |
| **Explorador por plantilla**, cambios frente al anterior y catálogo «qué es / qué decide Google / cómo se arregla» (#131, `incidencias.js`) | Sitebulb y Botify solo agrupan si alguien configura la segmentación a mano | Copiar las fichas tal cual en el informe exportable (ola 6) |
| **Operación a escala de agencia**: programaciones con cron real (75, 76), borrado en masa con guardias (96), archivo de enlaces en Parquet (95) | Herramienta de escritorio frente a servicio | Es la base de las olas 5 y 6 |

**Regla para todo el plan:** cada check nuevo hereda estas propiedades. Lleva su ficha en `incidencias.js`, una incidencia por hallazgo con `group_key` (88), un «no se sabe» explícito, se mide en al menos 3 censos reales (Lopesan, UOC, penguin o progym) y queda reflejado en `docs/decisiones/`.

---

## 2. Huecos críticos en olas de trabajo

Están ordenadas por valor/esfuerzo. Las olas 1 y 2 se pueden llevar en paralelo; las 3 a 6 llevan el orden indicado por dependencias.

### Ola 0. Higiene rápida (1 semana)

| PR | Contenido | Hecho cuando |
|---|---|---|
| 0.1 | Bug: `shared/avisos.py:68` enlaza a `#/jobs/{id}` y debe ser `#/rastreos/{id}` | Un test comprueba que la ruta del aviso la resuelve `_irARuta` |
| 0.2 | `sd_validation`: FAQ y HowTo marcados como «resultado retirado o restringido» (aviso informativo, no error) | `REQUISITOS_ESPERADOS` actualizado, con la fuente de Google citada |
| 0.3 | Presets de UA: Googlebot smartphone, Bingbot, GPTBot, ClaudeBot y OAI-SearchBot, con un aviso de que un UA de bot sobre huella Chrome puede delatarse | Están en `UA_PRESETS` y en `test_jobconfig_claves` |
| 0.4 | Revisar el perfil de impersonación por defecto (`chrome124`) frente a una versión actual | Canario de 3 sitios con WAF (UOC, progym, Lopesan) sin peor tasa de 200 |

### Ola 1. Diagnóstico técnico completo sobre datos que ya guardamos (re-analizar, sin re-rastrear)

Es el mejor valor por esfuerzo del plan. Cierra la paridad con SF en lo que más se audita y casi todo se aplica a los censos ya hechos.

| PR | Issue | Contenido | Hecho cuando |
|---|---|---|---|
| 1.1 | **Nueva:** «Canonicals y cadenas: destino noindex/3xx/4xx, cadenas canonical, redirecciones que acaban en error» | `canonical_broken` se separa en `canonical_a_redireccion` / `canonical_a_error`; nuevos `canonical_a_no_indexable`, `canonical_encadenado`, `redireccion_a_error` y `canonical_sin_enlazar`; informe de cadenas que mezcla redirecciones y canonicals (migraciones). El canonical relativo o con fragmento queda para una segunda parte (exige guardar el href crudo y re-rastrear) | Re-analizados Lopesan, UOC y penguin con las cifras escritas en la decisión; 0 regresiones en `test_indexabilidad` |
| 1.2 | **Nueva:** «Hreflang completo: autorreferencia, x-default, destino no canónico, idioma repetido, retorno incoherente» | 5 tipos nuevos con `group_key` por clúster; precargar `indexable`, `noindex` y `canonical_href`. No se incluye «hreflang no enlazado» | Medido en Lopesan (es/en/de) y UOC (ca/es/en); el tiempo del check en penguin (438.957 anotaciones) no pasa de 2× los 20,3 s actuales |
| 1.3 | **Nueva:** «Sitemaps sucios: no indexables, origen por fichero, lastmod y límites» | Parte A (S, re-analizar): `sitemap_no_indexable` (3xx/4xx/5xx, noindex, canonicalizada). Parte B (M, re-rastrear): conectar `parse_sitemap_entradas` y crear la tabla `sitemap_entries` (url_hash, fichero, lastmod) para `en_varios_sitemaps`, `sitemap_supera_limite` y `lastmod_futuro` | A medido en 3 censos; B en un rastreo nuevo de Lopesan, con el fichero de origen visible en la ficha de cada URL |
| 1.4 | 10b ⭐⭐. **Nueva:** «Paginación: páginas 2+ en noindex o canonicalizadas a la 1, paginación sin `<a>`» | Detección por `rel` y por forma de URL (`/page/N`, `?page=`); el cruce con enlaces respeta la decisión 37 y el Parquet (95) | blogs.uoc.edu reproduce las 168 categorías de la decisión 38 como hallazgo propio |
| 1.5 | 10b ⭐. **Nueva:** «HTML que Google trunca o ignora: >2 MB, head roto, meta fuera del head» | `html_supera_2mb` (re-analizar, sobre `content_length`); `has_meta_outside_head` pasa a incidencia; `title`/`robots` fuera del head; head o body múltiples. Evaluar html5lib solo para el head roto | Test con 6 casos del head roto contra el comportamiento de Chromium |

**Criterio de la ola:** con los mismos censos, la tabla de paridad de la sección 10b pasa de «gap» a «hecho» en canonical, hreflang, sitemaps, paginación y validación, y ningún tipo emitido se queda sin ficha (`grep` del analyzer contra `incidencias.js`).

### Ola 2. Confianza del dato en el rastreo (versiones deterministas de issues bloqueadas)

Se desbloquea la parte que no necesita LLM. Las issues originales siguen vivas para su fase con modelo.

| PR | Issue | Contenido | Hecho cuando |
|---|---|---|---|
| 2.1 | Separar de #10. **Nueva:** «Respuestas no válidas, versión determinista: `response_class`, salud del job y pausa» | Reutilizar `TITULOS_DE_BLOQUEO`/`NoEsLaPagina` (62, 82) en `seo_spider.parse`; señales de cf-ray/server, cuerpo binario y caída de tamaño; proporción por ventana, y marca en Redis que pasa a `finish_reason=bloqueado_por_waf`. Un muro no se guarda como indexable. La comparación (#36 F2) suprime las alertas SEO de un censo bloqueado | Reproducido con progym («Just a moment…»): el job acaba `failed`/pausado con su causa, 0 páginas de muro en `indexable`, y un censo bloqueado no dispara alertas |
| 2.2 | Separar de #11. **Nueva:** «Soft 404 determinista: sonda por host y plantilla, títulos de error, búsquedas vacías, OutOfStock» | Sonda con `mangle_path` al empezar; igualdad por `content_hash` (por debajo de 30 palabras `firma()` devuelve None) o por MinHash ≥0,9; umbrales en `AnalysisThresholdsConfig` (33) | Medido en 3 censos y con 20 casos revisados a mano; los dudosos se marcan como candidatos para la fase LLM |
| 2.3 | **Nueva:** «Enlaces que Google no puede seguir: javascript:, onclick sin href, span/div con href, utm internos» | `extract_links` guarda los no rastreables con `crawlability` en vez de descartarlos; incidencia agregada por página de origen (enlaces a 3xx/4xx/5xx); `utm_en_enlace_interno` | 58 páginas de control con la cifra antes y después; la incidencia por origen sale del agregado y no de unir con `urls` (37) |

### Ola 3. Datos a medida: extracción personalizada y segmentos

Es lo que más usa una agencia en consultoría y lo que convierte el crawler en herramienta de negocio, no solo técnica.

| PR | Issue | Contenido | Hecho cuando |
|---|---|---|---|
| 3.1 | #34 | Extractores CSS, XPath o regex por job: función pura en `extractors.py`, tabla `custom_extraction` por url_id, validación con 422, herencia cliente → perfil → job (83) | Precio, SKU y disponibilidad extraídos de un e-commerce real; columnas dinámicas en el Explorador y en el CSV de la vista |
| 3.2 | #34 (ampliar) | Búsqueda «contiene / no contiene» (texto o regex) sobre el HTML crudo o renderizado: presencia del tag de analítica, «sin stock» | Encuentra en un censo real las páginas sin el tag de GTM |
| 3.3 | **Nueva:** «Segmentos de negocio definidos por el usuario, retroactivos y aplicados a todo» | Reglas por cliente (patrón Y/O/NO, valor extraído, campo) con precedencia; se materializa un `segmento` por URL en el re-análisis (filtrar en Python no escala); `firma_de_ruta` como segmento por defecto; reutiliza el formato de `config.templates` | % de incidencias por segmento en `/issues/resumen` y filtro en el Explorador. Las alertas por plantilla de la decisión 68 pasan a ser alertas por segmento |
| 3.4 | #34 + #32 | Campo extraído como campo comparable en `shared/comparacion.py` | La comparación de dos censos detecta un cambio masivo de precio o disponibilidad |

**Dependencia:** 3.3 desbloquea #36 F3 («las fichas perdieron 40 indexables») sin esperar a #12.

### Ola 4. Datos de negocio: Search Console y priorización

| PR | Issue | Contenido | Hecho cuando |
|---|---|---|---|
| 4.1 | **Nueva:** «Search Console en el worker: clics, impresiones y posición por URL, cruzados con el censo» | Integración `gsc` con `credencial('GSC_*')` (83), cola propia del worker como el enriquecido (85), tabla por job/url_hash normalizada con `clave_url`; columnas en `_urls_filtradas`, el CSV y la ficha. **Retirar** el `credentials_json` del POC semántico de la BD | Lopesan y UOC con datos de GSC; 0 secretos en la BD (test) |
| 4.2 | Igual | Incidencias cruzadas: `huerfana_con_trafico`, `no_indexable_con_clics`, `indexable_sin_impresiones` (90 días) | Medidas en 2 clientes y con un aviso por hallazgo frente a `orphan_page` y `sitemap_orphan` (36) |
| 4.3 | #22, versión determinista | Prioridad = severidad × importancia (pagerank_score, click_depth, in_sitemap y clics si los hay); `?orden=prioridad` en `/issues/resumen`; campo `tipo` (problema, oportunidad o aviso) en `incidencias.js` | El top 10 de Lopesan por prioridad lo valida a mano el equipo SEO |
| 4.4 | Opcional | URL Inspection con muestra por plantilla (cuota de 2.000/día por propiedad) y GA4 Data API | Solo si 4.1 a 4.3 se usan en informes reales |

### Ola 5. Monitorización de cartera (#36 F2 y F3, #32)

| PR | Issue | Contenido | Hecho cuando |
|---|---|---|---|
| 5.1 | #36 F2 | Tabla `incidents` (abierto, visto, cerrado solo), agrupada por cliente, perfil y regla; solo el job más reciente abre o cierra; silenciar hasta una fecha; canales por cliente con Apprise y destino en variable de entorno (83) | El criterio de #36: dos semanas de perfil semanal real con 0 alertas falsas por WAF, truncado o contenido dinámico; reanalizar un job viejo no avisa |
| 5.2 | #36 F2 | Reglas configurables (incidencia o métrica + operador + umbral o delta) y resumen semanal | Una regla de cliente («4xx > 20 en fichas») dispara en un caso de prueba y no en el control |
| 5.3 | #36 F3 | `profile_runs` con agregados por ejecución (4xx, 5xx, indexables, incidencias por severidad, PageRank desperdiciado de `pagerank_resumen`), gráficas SVG en la ficha del cliente y panel de cartera con tendencia | Ficha de cliente con 4 o más ejecuciones graficadas sin leer `links` (37) |
| 5.4 | #32 | Comparación ampliada: inlinks, click_depth, pagerank_score, description, tipos de SD y hreflang; incidencias nuevas separadas entre URL existente y URL nueva; mapeo de rutas por regex (migraciones) | Migración simulada (regex vieja → nueva) empareja más del 95% de URLs; mismos guardias (67, 70, 91) |
| 5.5 | **Nueva:** «Modo lista y veredicto pasa/falla para QA de despliegues» | `max_depth=0` (solo semillas); umbral de alerta absoluto para listas pequeñas (hoy el piso de 20 de la decisión 68 lo impide); `GET /api/jobs/{id}/veredicto` | Una lista de 50 URLs clave con 10 que pasan a noindex alerta y el veredicto devuelve `falla` |

### Ola 6. Entregables y acceso del cliente

| PR | Issue | Contenido | Hecho cuando |
|---|---|---|---|
| 6.1 | **Nueva:** «Autenticación mínima: tokens por rol y enlaces firmados de solo lectura por cliente» | Generalizar el patrón de `X-Enrichment-Token` (`hmac.compare_digest`) a una dependencia de FastAPI; acotar por `cliente_id` todos los routers; proteger borrado y lanzamiento; cerrar el CORS | Test de que un token de cliente A no lee nada de B en ningún router (jobs, results, export, backup, diff) |
| 6.2 | #36 F3 (export). **Nueva:** «Informe exportable HTML/PDF del censo» | Nota con tope (92), problemas priorizados (4.3) con su ficha de `incidencias.js`, comparación y evolución; se genera en el worker | Un informe mensual de Lopesan se entrega sin retoque manual (sustituye a lo que hoy se monta a mano según `PARA_CLIENTES.md`) |
| 6.3 | **Nueva:** «XLSX multihoja y Google Sheets para Looker Studio» | XLSX en streaming (memoria plana, 65); Sheets con cuenta de servicio en el entorno | Un dashboard de Looker se alimenta solo tras un programado |
| 6.4 | Igual | Envío de las exportaciones elegidas al terminar un programado, saltándose las vacías | Correo o Slack con adjunto tras el semanal de Lopesan |
| 6.5 | **Nueva:** «Servidor MCP de solo lectura sobre la API» | Herramientas tipadas: problemas, diff, export y «qué cambió desde el mes pasado»; las de escritura más adelante, siempre con `simular` o `estimate` previo | Encaja con Claude Code y Cowork de Lin3s; requiere 6.1 |

### Ola 7. JavaScript, IA y móvil

| PR | Issue | Contenido | Hecho cuando |
|---|---|---|---|
| 7.1 | 10b ⭐⭐; la parte determinista de #14. **Nueva:** «Respuesta frente a render por URL en plantillas marcadas» | Doble captura (crudo vía `_sin_render` y render) solo en las plantillas que `js_check` marca o que el perfil pide; `html_meta_raw` o un diff por url_id: canonical, robots, title, H1, description, enlaces y palabras; incidencias `noindex_solo_en_crudo` y `canonical_cambia_al_renderizar` | En la UOC (megamenú JS) la pestaña muestra los enlaces solo-JS; el coste de render queda medido y acotado |
| 7.2 | **Nueva:** «Preparación para buscadores de IA según el criterio de Google» | `nosnippet` y `max-snippet` restrictivo en indexables (re-analizar, S); `data-nosnippet` (re-rastrear); «contenido visible sin JS», que sale de 7.1; llms.txt solo como dato informativo | Vista por ámbito «IA» en el Explorador (#135) |
| 7.3 | **Nueva:** «robots.txt por job: guardado, evaluado por bot (Googlebot y bots de IA) y regla concreta» | Guardar robots.txt con hash por job (prepara #36); evaluar con Protego para N UA; la regla que bloquea en la fila bloqueada (48); robots.txt de prueba. El historial y la alerta los cubre #36 | La ficha de una URL bloqueada dice la línea de robots.txt; tabla de qué bloquea cada bot de IA por host |
| 7.4 | **Nueva:** «Perfil de render móvil y capturas por muestra de plantilla» | Contexto de Playwright con nombre por perfil; UA móvil coherente también sin render; la comparación distingue el perfil, no solo `render_js` | Comparación móvil frente a escritorio de un sitio con servido dinámico |

### Ola 8. Paridad secundaria (solo con hueco y tras las anteriores)

- **#101 Recursos embebidos**: rotos y peso, por recurso y no por aparición (64b), con un presupuesto separado de `max_urls`. Activar el `check_external_resources` que hoy nadie lee.
- **#35 Logs**: verificación de Googlebot y bots de IA por DNS inverso, en Parquet (95) y asociados al cliente. Valor alto, pero es L, así que va cuando haya un cliente que dé los logs.
- **Nueva:** «Core Web Vitals por plantilla (PSI/CrUX)», con `credencial('PAGESPEED_*')` y muestreo por `firma_de_ruta`.
- **Nueva:** «Anclas: agregado por destino y lista determinista de anclas genéricas por idioma» (separar de #13). Los n-gramas con sugerencias de enlazado, después.
- **Nueva:** «Datos estructurados: formato de valores (ISO 8601, URL absoluta, moneda, availability)».
- **Nueva:** «Accesibilidad con axe en modo muestra». Se vende a la UOC y al sector público (EAA); solo con render.
- **Nueva:** «Tope de URLs por subdominio o carpeta y proxy por cliente» (multisites como blogs.uoc.edu; el proxy en variable de entorno).

---

## 3. Qué NO hacer

1. **Repartir un censo entre réplicas** (scrapy-redis). La cartera llega a unas 87k URLs y el cuello de botella medido es el análisis (`compute_pagerank`: unos 35 de 43 min en Druni, #52). Invertir ahí, no en velocidad de rastreo.
2. **Rastreo incremental (#36 F4) antes de las olas 5 y 6.** Es XL, choca con los guardias de alcance (70) y con el Parquet (95), y no da ningún hallazgo nuevo. Va al final.
3. **Esperar a la capa LLM para lo determinista.** #10, #11, #13, #14 y #22 tienen una mitad sin modelo. Se separa y se entrega. La capa (#6-#9, #40) sigue con su propio calendario.
4. **Ortografía y gramática, huella de carbono, generación de contenido con IA, «AI score» opaco.** El mercado lo tiene y no cambia ninguna decisión SEO; además rompería la tesis de «cada cifra se puede defender».
5. **Lighthouse de laboratorio en todas las URLs y axe en todo el censo.** Coste de render enorme y señal débil: siempre con muestra por plantilla.
6. **Backlinks propios.** No competir con Ahrefs; conectar con DataForSEO o un CSV (#31) solo como entrada del PageRank.
7. **Login por formulario genérico con perfiles cifrados.** Las cookies cubren los staging de la práctica; solo bajo demanda de un cliente.
8. **Visualización de grafo force-directed.** Queda vistosa en una demo y no sirve en 30k nodos. Las tablas por plantilla y segmento responden mejor.
9. **Rotar UA o reglas por cliente en el código.** Se mantiene el principio: todo lo del cliente va en `projects/<cliente>/config.json` (memoria del usuario y CLAUDE.md).
10. **Copiar los 100 extractores de SF o la sintaxis XPath 3.1 entera.** XPath 1.0, CSS y regex vía parsel cubren el 95% de los casos.

---

## 4. Dependencias y riesgos

**Grafo de dependencias**
- 6.1 (autenticación) va antes que 6.2-6.5 y que cualquier enlace compartido con un cliente.
- 2.1 (muros) va antes que 5.1 (incidentes): sin él, la regla provisional de #36 F2 (>50% de errores o 403) es la única protección contra alertas falsas.
- 3.3 (segmentos) va antes que 5.3 (evolución por segmento) y que #36 F3 «agrupación por taxonomía».
- 4.1 (GSC) va antes que 4.3 (priorización con tráfico) y que 6.2 (informe con impacto de negocio).
- 7.1 (respuesta frente a render) va antes que 7.2 (contenido visible sin JS).
- #52 (rendimiento de enlaces) va antes de añadir más agregados sobre `links` (1.4, 2.3, anclas).

**Producción compartida**
- Mergear despliega y recrea los contenedores de la API y del crawler: mata rastreos **y análisis** (`analyzing` cuenta), además de descargas en vuelo. Antes de cada merge de estas olas: `gh run list --workflow=deploy.yml --limit 1` y el bucle de estados de CLAUDE.md.
- Toda migración con `lock_timeout` (ya existe `limitar_espera_de_bloqueos`). Las olas 1, 3, 4 y 5 añaden tablas: crearlas con `IF NOT EXISTS` en `init_db.py` y nunca con un `ALTER` largo sobre `jobs` en hora de programados.
- Las olas 1 y 4.3 cambian cifras de censos ya entregados al re-analizar. `analisis_version` (72) lo deja registrado, y la comparación de incidencias se niega a comparar (91). Hay que decirlo en el informe al cliente.
- Varias sesiones en paralelo usan worktrees: la numeración de decisiones va por el hook `numero-decision` para no chocar (la del borrado ya pasó de 94 a 96).

**Disco**
- `links` era el 86% de la base (49 GB de 57). El Parquet (95) archiva los censos superados, pero la fase 2 (que los enlaces no entren nunca en Postgres) está sin hacer y el `VACUUM FULL` sigue pendiente. Las olas 1.4, 2.3 y 5.4 (enlaces ganados y perdidos) leen enlaces: diseñarlas para leer del Parquet desde el principio.
- Datos nuevos que pesan: `custom_extraction` (3.1), `sitemap_entries` (1.3B), la doble captura (7.1), las capturas de pantalla (7.4) y GSC por URL y día (4.1). Cada uno entra en `shared/almacenamiento.py` y en el borrado en masa (96) en el mismo PR. Así lo exige el test que comprueba que ninguna tabla del censo se queda fuera.
- Una issue de la fase 2 del Parquet debe ir antes de la ola 5, porque la monitorización semanal multiplica los censos.

**Capa LLM bloqueada por la clave**
- La infraestructura (84, 85) está completa y probada con `FakeLLM`, pero nunca contra una clave real. Sin la calibración de #8 nada sale `decidido`, y el único módulo es `pagina_legal` (de ejemplo).
- **Nada de este plan depende de ella.** Cuando haya clave, el orden es: prueba con 1.000 URLs y comparación ±30% (#40), luego #8 y #9, y después los módulos que reciben los candidatos de las versiones deterministas (2.2 → #11, 2.1 → #10, 4.3 → #22, anclas → #13, 3.1 → #20). Las olas 2 a 4 se convierten así en el **prefiltro barato** que abarata cada llamada.

**Otros riesgos**
- **Cuotas de Google**: URL Inspection admite 2.000 consultas al día por propiedad, y Search Analytics tiene límites por consulta. Hacerlo por muestra y por cola, nunca dentro del request (el error de `fetch-gsc`).
- **Los sitios varían solos** (13): cada check nuevo se valida repitiendo contra el estado actual del sitio antes de atribuir una diferencia al código.
- **Huella TLS**: un WAF que aprenda `chrome124` deja ciegos todos los rastreos sin render. Lo vigilan 0.4 y la tasa de muros de 2.1.
- **Superficie de ataque**: hasta que exista 6.1, la API depende de estar en `127.0.0.1` detrás del túnel. No exponer nada nuevo (MCP, enlaces compartidos) antes.

---

### Issues nuevas propuestas (resumen)

1. Canonicals y cadenas: destino noindex/3xx/4xx, cadenas canonical, redirecciones que acaban en error
2. Hreflang completo: autorreferencia, x-default, destino no canónico, idioma repetido, retorno incoherente
3. Sitemaps sucios: no indexables, origen por fichero, lastmod y límites
4. Paginación: páginas 2+ en noindex o canonicalizadas a la 1, paginación sin `<a>`
5. HTML que Google trunca o ignora: >2 MB, head roto, meta fuera del head
6. Respuestas no válidas, versión determinista: `response_class`, salud del job y pausa (separada de #10)
7. Soft 404 determinista: sonda por host y plantilla, títulos de error, búsquedas vacías (separada de #11)
8. Enlaces que Google no puede seguir: javascript:, onclick, span/div con href, utm internos
9. Segmentos de negocio definidos por el usuario, retroactivos y aplicados a todo
10. Search Console en el worker: clics, impresiones y posición por URL, cruzados con el censo
11. Modo lista y veredicto pasa/falla para QA de despliegues
12. Autenticación mínima: tokens por rol y enlaces firmados de solo lectura por cliente
13. Informe exportable HTML/PDF del censo
14. XLSX multihoja y Google Sheets para Looker Studio
15. Servidor MCP de solo lectura sobre la API
16. Respuesta frente a render por URL en plantillas marcadas
17. Preparación para buscadores de IA según el criterio de Google
18. robots.txt por job: guardado, evaluado por bot y regla concreta
19. Perfil de render móvil y capturas por muestra de plantilla
20. Core Web Vitals por plantilla (PSI/CrUX)
21. Anclas: agregado por destino y lista determinista de anclas genéricas (separada de #13)
22. Datos estructurados: formato de valores (ISO 8601, URL absoluta, moneda, availability)
23. Accesibilidad con axe en modo muestra
24. Tope de URLs por subdominio o carpeta y proxy por cliente
25. Enlaces fuera de Postgres, fase 2 del Parquet (antes de la ola 5)

Issues existentes que cubre el plan: #32, #34, #35, #36 (F2 y F3; F4 al final), #52, #101, #22/#10/#11/#13/#14 (su mitad determinista) y #31 (solo como entrada).