# Search Console en el crawler: qué se puede hacer y qué combinar

Investigación del 17 de octubre de 2026, antes de abrir issues. Sale de
Google Search Console del cajón de «aparcado» (`ROADMAP.md`).

La primera parte está en lenguaje llano, para decidir qué hacemos. La segunda
(«Anexo técnico») es lo que necesita quien lo implemente, con las fuentes.

---

## En una frase

El rastreo dice **cómo está** cada página; Search Console dice **qué consigue**
en Google. Juntos responden a la pregunta que de verdad importa: qué arreglar
primero porque cuesta tráfico, y qué oportunidades hay a mano. Jev, encima,
sirve para juzgar lo que una regla no puede: si la página responde a lo que la
gente busca para llegar a ella.

---

## Lo que ya tenemos

- **Una conexión antigua** con Search Console, de la prueba semántica: trae
  clics, impresiones y posición por URL y por búsqueda. No vale tal cual:
  guarda la llave de Google en la base de datos (cualquiera que llegue a la
  herramienta la podría leer), pide los datos en primer plano y **tira las URLs
  que no están en el rastreo**, que son justo de las más interesantes.
- **La ficha del cliente** ya permite declarar su Search Console (qué propiedad
  y qué llave del servidor usar), pero nada la lee todavía.
- **La versión archivada (v2)** tenía serie diaria por cliente, GA4,
  sincronización nocturna y dos avisos cruzados con el enlazado. Sirve de
  referencia, no se rescata el código.
- **El plan de mejora** ya lo preveía como «Ola 4»: datos en segundo plano,
  tres avisos y ordenar incidencias por tráfico.
- **Jev** ya puede recibir datos de Search Console sin cambios de fondo.

---

## Lo que da Google (y lo que no)

Da:

- **Rendimiento**: clics, impresiones, CTR y posición, por página, búsqueda,
  país, dispositivo y día; 16 meses hacia atrás, con 2-3 días de retraso.
- **Inspección de URL**: si Google la tiene indexada, **qué canonical ha
  elegido Google**, cuándo la rastreó por última vez, si la ve como soft 404,
  si la rastreó como móvil y sus resultados enriquecidos. Solo **2.000 URLs al
  día por propiedad**: es una muestra, no un censo.
- **Sitemaps**: qué sitemaps conoce Google, cuándo los descargó y si tienen
  errores (ya no dice cuántas URLs indexa de cada uno).

No da (o da a medias):

- **Datos de AI Overviews y AI Mode**: salen en la interfaz desde junio de 2026,
  pero ni la API ni la exportación los exponen. AI Mode cuenta dentro de Web,
  sin separar.
- **Todas las búsquedas**: las raras o sensibles van anonimizadas (en torno al
  40-50 % de los clics en muchos sitios), y la API corta en 50.000 filas por
  día. La suma por búsqueda nunca cuadra con el total. En sitios grandes hace
  falta la exportación a BigQuery.
- **El filtro de marca / no marca** de la interfaz: no está en la API.

Cifras que mienten y hay que avisar al compararlas:

- Las **impresiones de mayo 2025 a abril 2026 están infladas** por un error de
  Google que no corrigió el histórico.
- En **septiembre de 2025** Google quitó `num=100`: cayeron impresiones de
  escritorio y subió la posición media sin que el sitio cambiara.

---

## Las combinaciones

Cada una dice qué responde y qué hay que tener en cuenta para no afirmar de
más (la regla de la casa: lo que no se puede afirmar no se afirma).

### A. Rastreo + Search Console (sin coste, sin IA)

1. **Tráfico que se pierde.** Páginas con clics que hoy son noindex, dan error,
   redirigen, están bloqueadas por robots.txt o canonicalizadas a otra. Es lo
   más urgente del informe: es tráfico real en riesgo.
2. **Huérfanas con tráfico.** Reciben clics y ningún enlace interno.
3. **Lo que Google conoce y el rastreo no encontró.** URLs con clics que no
   están en el censo: unas son huérfanas de verdad, otras están fuera del
   alcance del rastreo (otro subdominio, otro idioma). Se separan, y las
   primeras pueden entrar como semillas del siguiente rastreo.
4. **Indexables sin impresiones** en 90 días. Peso muerto o contenido que
   Google no considera. Ojo: Google atribuye el tráfico a *su* canonical, y en
   sitios grandes el corte de filas esconde la cola larga; solo se afirma
   cuando la descarga no llegó al tope.
5. **Oportunidades de posición 4-20 con poco enlazado.** Páginas que casi
   están, con muchas impresiones, y poco PageRank interno o mucha profundidad.
   Con el grafo que ya tenemos se puede proponer **desde qué páginas fuertes
   enlazarlas**.
6. **CTR por debajo de lo esperado para su posición**, con la curva del propio
   cliente, cruzado con lo que ya auditamos del snippet (title genérico,
   cortado por ancho, description igual al title o ausente).
7. **Canibalización por búsqueda.** La misma búsqueda reparte clics entre
   varias URLs del sitio; con su canonical, plantilla e indexabilidad al lado.
8. **El informe ordenado por tráfico.** Una incidencia en una página con 5.000
   clics va antes que la misma en una página sin ninguno (la parte sin IA de
   #22).
9. **Rendimiento por plantilla y por profundidad.** Qué secciones del sitio
   rinden y cuáles no, con las plantillas que ya detectamos.
10. **«Se cayó, y esto es lo que cambió».** La comparación entre rastreos ya
    dice qué cambió en cada página (title, noindex, canonical, contenido); con
    los clics de antes y de después, la alerta pasa de «han caído los clics» a
    «han caído en estas 40 fichas, y en las 40 cambió el canonical». Es la que
    más valor da al seguimiento mensual de un cliente.
11. **Sitemaps según Google.** Qué sitemaps tiene Google y cuáles no
    descarga, junto a lo que ya auditamos de cada sitemap.

### B. Rastreo + inspección de URL (muestra diaria)

12. **El canonical que elige Google no es el declarado.** Lo más útil de la
    inspección: es la prueba de que Google ignora nuestra señal.
13. **Indexable pero no indexada**, y su motivo según Google.
14. **Nuestro veredicto contra el de Google.** Soft 404, indexabilidad,
    móvil: dónde decimos una cosa y Google otra. Sirve para encontrar fallos
    del crawler y, además, como **respuesta correcta para medir a Jev** (el
    juego de pruebas de #8).

Con 2.000 al día por propiedad se reparte: primero las páginas con tráfico en
riesgo y las oportunidades, luego una muestra por plantilla, en rotación.

### C. Rastreo + Search Console + Jev (de pago, bajo demanda con el botón)

15. **¿La página responde a lo que buscan para llegar a ella?** Jev clasifica
    la intención de las búsquedas reales de cada página y la compara con la de
    la página (#39): una ficha de producto que entra por «cómo elegir…» está
    desalineada.
16. **Embudo con datos reales.** Cuánto tráfico entra por contenidos TOFU,
    MOFU y BOFU, por plantilla, y dónde falta (#39).
17. **Canibalización juzgada.** De los pares de la combinación 7, ¿es la misma
    intención (hay que fusionar o diferenciar) o son búsquedas distintas que
    comparten palabras (no pasa nada)? (#18).
18. **Qué le falta a una oportunidad 4-20.** Para las páginas de la
    combinación 5, si el title, el H1 y el contenido cubren lo que se busca o
    falta un tema.
19. **¿El snippet promete lo que se busca?** Para las de la combinación 6.
20. **Búsquedas de marca y no marca, y grupos temáticos**, ya que la API no
    da el filtro de marca.
21. **Prioridad con criterio** (#22): Jev decide cuánto importa cada tipo de
    incidencia en cada tipo de página, y Search Console pone el peso.

### D. Más adelante

- **Exportación a BigQuery** para clientes grandes: sin tope de filas, los
  totales cuadran y se guarda más de 16 meses. Lo tiene que activar el
  propietario de la propiedad; no rellena hacia atrás.
- **GA4**: conversiones e ingresos por página, para priorizar por negocio y no
  solo por clics.
- **DataForSEO** para explicar el CTR con lo que hay en la SERP (AI Overviews,
  paquetes locales), en la línea del waterfall de CTR que ya usamos fuera de la
  herramienta.

---

## Cómo se conecta un cliente

Recomendado: **una cuenta de servicio de la agencia**, con su llave guardada en
el servidor (nunca en la base de datos). El cliente solo tiene que añadir su
email como usuario en su propiedad de Search Console: con permiso «restringido»
basta para el rendimiento; para la inspección de URL puede hacer falta
«completo» (está por confirmar con una llamada real).

La alternativa, conectar con el usuario de Google de una persona de la agencia,
evita pedírselo al cliente, pero depende de esa persona y el acceso caduca.

---

## Propuesta de issues

En este orden, porque cada una se apoya en la anterior:

1. **Base**: conectar Search Console por cliente, descarga nocturna en segundo
   plano, guardado por URL y día y por página × búsqueda (conservando las URLs
   que no están en el rastreo), columnas en el explorador, la ficha y el CSV,
   aviso de rupturas de serie, y retirar la conexión antigua y sus llaves de la
   base de datos.
2. **Cruces que cuestan tráfico**: combinaciones 1, 2, 3, 4 y 8.
3. **Oportunidades**: combinaciones 5, 6, 7, 9 y 11.
4. **Alertas con la causa**: combinación 10, sobre la comparación y las alertas
   que ya existen.
5. **Inspección de URL por muestra**: combinaciones 12, 13 y 14.
6. **Jev con búsquedas reales**: combinaciones 15 a 21, como ampliación de
   #39, #18 y #22, y la 14 como fuente del juego de pruebas de #8.
7. **Más adelante**: BigQuery, GA4 y DataForSEO.

---

## Anexo técnico

### Lo que hay en el código (master 6126fd5)

- Legado del POC: `shared/semantic_models.py` (`GscAccount.credentials_json`
  en la BD, `GscJobData` con `url_id NOT NULL`, `GscQueryData`),
  `api/routers/semantic.py:331-406` (`fetch-gsc` síncrono, descarta las URLs
  sin match, `except: pass` en queries; `_normalize_url_for_match` pasa la ruta
  a minúsculas), `POC_centro_semantico/src/gsc.py` (cuenta de servicio,
  `webmasters.readonly`, 90 días, lag 3, paginación de 25k). Dependencias en
  `api/requirements.txt`, solo en la imagen de la API.
- Integraciones del cliente: `clients.integraciones` (`shared/models.py:54`),
  `api/schemas.Integracion` (tipo `gsc`, `parametros`, `credencial_env`),
  `shared/credenciales.py` (prefijo `GSC_`, `credencial()` sin ningún
  consumidor). El formato del valor de `GSC_*` no está definido.
- Archivo `archivo/v2-experimental`: `gsc_daily`, `ga4_daily`,
  `metric_sync_configs`, `api/routers/metrics.py`, APScheduler en la API con
  candado Redis, `analyze_gsc_signals` (`no_inlinks_with_traffic`,
  `underlinked_high_performer`). Referencia de diseño; #42 lo da por superado.
- Plan vigente: `docs/PLAN_MEJOR_CRAWLER.md` Ola 4 (4.1-4.4): integración `gsc`
  con `credencial('GSC_*')`, cola propia en el worker como la del enriquecido,
  tabla por job/`url_hash` con `clave_url`, retirar `credentials_json`;
  incidencias `huerfana_con_trafico`, `no_indexable_con_clics`,
  `indexable_sin_impresiones`; prioridad = severidad × importancia; URL
  Inspection por muestra de plantilla.
- Decisiones que aplican: 83 (secretos en el entorno), 84 y 85 (Jev: estados
  mínimos, nada se dispara solo, cola propia), 37/94 (enlaces), 67/70/89/91
  (no afirmar), 36/53/64/73 (agrupar avisos).
- Para casar URLs: `shared/indexabilidad.clave_url()` (no pasa la ruta a
  minúsculas). Plantilla: `shared/plantillas.firma_de_ruta()`, al vuelo. Tipo
  de página: no existe (#12).
- Diseño a decidir: Search Console va **por cliente y por día**, no por job (lo
  pide #36); el cruce con un censo se hace por ventana de fechas al analizar.
  Entra en `shared/almacenamiento.py`, en el borrado y en el backup.

### API de Google (con fuente)

- Search Analytics: dimensiones `query`, `page`, `country`, `device`,
  `searchAppearance`, `date` y `hour` (desde abril de 2025, con
  `dataState: hourly_all`, 10 días); tipos `web`, `image`, `video`, `news`,
  `discover`, `googleNews`; `rowLimit` ≤ 25.000 con `startRow`; **tope de
  50.000 filas por día, tipo y propiedad** (pedir día a día); `aggregationType`
  `byPage` frente a `byProperty` (los totales no cuadran); 16 meses;
  `metadata.first_incomplete_date` desde julio de 2025. Cuotas: 1.200 QPM por
  sitio y por usuario (la cuenta de servicio comparte la de usuario entre todas
  las propiedades), y cuotas de «carga» sin cifra para `page`+`query`.
  <https://developers.google.com/webmaster-tools/v1/searchanalytics/query>,
  <https://developers.google.com/webmaster-tools/limits>,
  <https://support.google.com/webmasters/answer/12919192>,
  <https://developers.google.com/search/blog/2022/10/performance-data-deep-dive>.
- URL Inspection: `indexStatusResult` (`verdict`, `coverageState`,
  `indexingState`, `pageFetchState`, `googleCanonical`, `userCanonical`,
  `lastCrawlTime`, `crawledAs`, `sitemap`, `referringUrls`),
  `richResultsResult`; `mobileUsabilityResult` obsoleto desde diciembre de
  2023. **2.000 al día y 600 por minuto por propiedad**; verificar subcarpetas
  como propiedades multiplica la cuota (lo hace Screaming Frog).
  <https://developers.google.com/search/blog/2022/01/url-inspection-api>.
- Sitemaps: `list`/`get`; `contents.indexed` obsoleto desde 2019.
- Exportación a BigQuery: diaria, sin histórico previo, la activa el
  propietario; tablas `searchdata_site_impression` y
  `searchdata_url_impression`; sin tope de filas; posición base 0.
  <https://support.google.com/webmasters/answer/12917991>.
- Atribución: el tráfico va a la canonical **que elige Google**; fragmentos `#`
  aparecen y hay que sumarlos a la URL sin fragmento; en propiedades
  `sc-domain:` llegan todos los subdominios y protocolos (fuera de alcance, no
  huérfanas); no sumar propiedades que se solapan.
  <https://support.google.com/webmasters/answer/7042828>.
- Novedades a tener en cuenta: impresiones infladas del 13-05-2025 al
  27-04-2026 sin corregir; fin de `num=100` en septiembre de 2025; AI Mode
  dentro de Web desde junio de 2025; informes de IA generativa (junio de 2026)
  solo en la interfaz; filtro de marca (noviembre de 2025) solo en la
  interfaz; tipo multimodal (septiembre de 2026) solo en la interfaz.

### Qué hacen los demás

- Screaming Frog: sin datos de GSC, no indexable con datos de GSC, huérfanas
  desde GSC (y rastrearlas), y con inspección: no está en Google, indexable no
  indexada, canonical declarado no elegido.
- Sitebulb: huérfana con tráfico; tráfico en canonicalizadas, bloqueadas,
  noindex, 4xx y 3xx.
- Lumar: huérfanas de GSC, rotas, bloqueadas y redirigidas con tráfico,
  indexables sin impresiones.
- Botify: GSC por segmento, «funnel scope» (no indexables que salen en
  resultados).
- JetOctopus: canibalización por intensidad y dispositivo, grupos de keywords.
- Oncrawl: huérfanas que posicionan, rendimiento por grupo de páginas.

Lo que nadie hace y nosotros podríamos: la alerta con la causa (10), los
enlaces propuestos desde el grafo (5) y el juicio de intención con Jev
(15-19).

### Por confirmar con una llamada real

- Si `hour` se combina con `page` o `query`.
- El permiso mínimo para la inspección de URL (restringido o completo).
- El trato exacto de las búsquedas anonimizadas en BigQuery.
