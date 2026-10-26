# Auditoría de UX de la interfaz

> Hecha el 2026-10-13 recorriendo la app en local con el censo de Lopesan
> (`e77ac4f6`, 5.751 URLs, 38.922 incidencias), más lectura de
> `frontend/index.html` y `frontend/app.js`. La secuencia viva es el milestone
> **M6 · Interfaz** de GitHub; este documento explica **qué se vio y por qué
> ese orden**. Cada hallazgo lleva un id (`UX-nn`) que usan las issues.

## El diagnóstico en una frase

La app está organizada alrededor del **rastreo** —un objeto técnico— y no de lo
que se hace con ella: entrar en un cliente, ver qué está roto, qué ha cambiado
desde el último censo y sacar el informe. Las funciones se han ido añadiendo
como pestañas y botones (9 pestañas y 8 botones en la ficha de un rastreo), y
la misma pregunta se contesta en tres sitios con tres cifras distintas.

## Regla para cada cambio de esta lista

Igual que la Regla 0 de `docs/PRIORIDADES.md`: un cambio de interfaz se cierra
respondiendo **qué decisión toma quien mira la pantalla** con él. «Queda más
bonito» no cierra nada; «el error grave sale arriba en vez de en la página 412»
sí.

Y se cierra **con captura de antes y después**: `docs/ux/capturas.py` recorre
todas las vistas y guarda captura, texto visible y alto de página de cada una.

```bash
docker run --rm -v "$PWD/docs/ux":/s -v /tmp/ux-antes:/out crawler-masivo-crawler \
    python /s/capturas.py http://host.docker.internal:8000 e77ac4f6-60a5-4c8b-834c-6e2af4f98d56 lopesan
```

`frontend/**` **despliega** al mergear (filtro de `deploy.yml`): antes de
mergear, la comprobación de siempre (nada en `running`/`analyzing`, ver
CLAUDE.md «Al operar»).

---

## Hallazgos

### Rotos o engañosos

| id | qué | medido |
|---|---|---|
| UX-01 | El Resumen pinta **todas** las semillas como texto (`index.html`, `job.seeds?.join(', ')`) | con las 3.040 de Lopesan, la página mide **114.465 px** de alto |
| UX-02 | La pestaña Enlaces se queda en «0 enlaces · Cargando...»: el endpoint hace `q.count()` sobre `links` (`api/routers/results.py`, `list_links`) | **2 min 02 s** en local para la primera página. Mismo mal que #52 / decisión 37 |
| UX-03 | No hay rutas: recargar vuelve a la lista, «atrás» no funciona, no se puede enlazar un rastreo, una URL ni un filtro | `view` es solo estado de Alpine; ni `location.hash` ni `pushState` en `app.js` |
| UX-04 | Problemas es una lista plana, sin agrupar por tipo, ordenada error → **info** → aviso, con detalles crudos (`status_code: 404`) | 38.922 filas en **779 páginas** |
| UX-05 | El Informe SEO da **88 «Bueno»** a un censo con 2.367 canonicals a otro dominio marcados como `error` (el fallo de Lopesan, decisión 64) | contradice a Problemas y al Resumen |

### Navegación y estructura

| id | qué |
|---|---|
| UX-06 | La barra superior cambia según la vista: en la ficha de un rastreo no están Clientes ni Programados; en la de un cliente no está Programados ni la vuelta a Trabajos |
| UX-07 | La portada es una lista de 50 rastreos con nombre libre, sin buscador ni filtro por cliente; canarios, pruebas y censos mezclados |
| UX-08 | El cliente no es la entrada: la ficha de Lopesan enseña **1 rastreo** cuando hay ~10 (los de julio no están ligados); Druni no tiene cliente |
| UX-09 | Ficha del rastreo sobrecargada: 4 botones de exportar sueltos, «Eliminar» junto a «Explorador», 9 pestañas |
| UX-10 | La pestaña URLs duplica el Explorador, que es mejor (filtros por columna, columnas elegibles, teclado) |
| UX-11 | «Principales problemas» (Resumen), Problemas e Informe SEO responden lo mismo de tres formas |
| UX-12 | Comparar se abre vacío con un desplegable que mezcla censos con pruebas de 26 URLs, aunque `jobs.comparacion` ya trae la comparación con el anterior |
| UX-13 | Tarjetas de cifras redundantes: «URLs totales» = «Procesadas»; «Fallidas 0» en rojo |

### Detalle y consistencia

| id | qué |
|---|---|
| UX-14 | Formatos: duración `14787.1s`; números `4319` junto a `15.026` (`Intl` es-ES no agrupa 4 cifras: hace falta `useGrouping: 'always'`); fechas «hace 6d» junto a `6/10/2026, 9:00:27`; cron crudo `0 4 * * 3` |
| UX-15 | Iconos que no salen (botón Clientes, pestañas y «Nuevo rastreo» de la ficha de cliente) y `lucide@latest` sin versión fijada |
| UX-16 | Explorador: la lupa sale enorme y descolocada encima del buscador; miga de pan truncada |
| UX-17 | Ficha de URL: «General» es un volcado de 30 campos sin jerarquía; indexabilidad, problemas y enlazado no están arriba |
| UX-18 | Semántico pide API key de Gemini y tres deslizadores (alpha, beta, umbral) antes de decir qué da; términos en inglés (Ring Map, Drift, Scatter UMAP) |
| UX-19 | Nuevo rastreo: 6 pestañas técnicas y 32 concurrentes por defecto; cliente/perfil (que heredan todo) es un campo más |
| UX-20 | Textos sin tilde («Codigos», «Semantico», «Configuracion», «Analisis») junto a otros con ella («Análisis enriquecido») |
| UX-21 | Accesibilidad: 31 pestañas son `div` (sin teclado), 107 ayudas solo al pasar el ratón (`data-tip`), 11 `confirm`/`alert`/`prompt` nativos, 688 `style=""` en línea |
| UX-22 | Móvil: la barra superior desborda a 390 px (hay scroll horizontal) |

---

## Plan por fases

Cada fase es una issue del milestone M6 con su checklist. **Un PR por casilla
o por un grupo pequeño de casillas**, no uno por fase: así cada despliegue es
pequeño y una regresión se aísla sola.

### Fase 0 · Arreglos rápidos — #119

Lo que hoy rompe o miente, sin tocar la estructura.

- [x] UX-01 Semillas plegadas en el Resumen («3.040 semillas · ver»). Criterio: la ficha de Lopesan mide menos de 3.000 px. (#125)
- [x] UX-02 Enlaces responde en segundos: total estimado o `count` filtrando por `links.job_id` con índice; y el estado «cargando» no puede decir «0 enlaces». Medir antes y después con el mismo job. (#125)
- [x] UX-14 Un único `fmt` para números (`useGrouping: 'always'`), duraciones («4 h 06 min»), fechas (relativa + absoluta en `title`) y cron legible («miércoles 4:00»). (#125)
- [x] UX-15 Fijar versión de lucide y arreglar los iconos que faltan. (#125)
- [x] UX-16 Lupa y miga de pan del Explorador. (#125)
- [x] UX-04 (parcial) Problemas ordenado por gravedad real: error → aviso → info. (#125)

### Fase 1 · Navegación — #120

- [x] UX-03 Rutas en el hash (`#/rastreos/:id/problemas?tipo=...`, `#/clientes/:slug`, `#/rastreos/:id/url/:url_id`) con atrás, recarga y enlaces que se pueden pegar. (#126)
- [x] UX-06 Navegación fija e igual en todas las vistas: Rastreos · Clientes · Programados. Importar backup y el tema, a un menú. (#126)
- [x] UX-09 «Exportar ▾» con los cuatro exports; Backup y Eliminar en «⋯». (#126)
- [x] UX-10 Fusionar la pestaña URLs con el Explorador (el Explorador se queda). (#126)
- [x] UX-09/11 Cinco pestañas: Resumen, Explorador, Problemas, Enlaces, Comparar. Semántico, Enriquecido y Limpieza bajo «Avanzado». (#126)
- [x] UX-13 Tarjetas que digan algo: indexables, no indexables, errores, cambio frente al censo anterior. (#126)

### Fase 2 · Problemas como centro — #121

La pantalla que se enseña en una reunión.

- [x] UX-04 Problemas agrupado por tipo: páginas afectadas, gravedad, qué es, qué decide Google, cómo se arregla, ejemplos; clic → Explorador filtrado. La lista plana queda como detalle. (#127)
- [x] UX-05 La nota del Informe SEO no puede decir «Bueno» con errores graves abiertos (tope por error grave, o quitar la nota). Criterio: Lopesan no sale «Bueno». (#127)
- [x] UX-11 Resumen, Problemas e Informe sacan las cifras de la misma consulta (decisiones 23 y 39: dos cifras que dicen medir lo mismo acaban sin coincidir). (#127)
- [x] UX-12 Comparar abre con la comparación automática contra el censo anterior (`jobs.comparacion`): qué se arregló, qué es nuevo, qué sigue; el desplegable, para elegir otro, sin canarios ni pruebas por defecto. (#127)
- [x] UX-17 Ficha de URL con lo importante arriba: indexabilidad y por qué, problemas, enlazado (in/out, PageRank 0-100), y «todos los campos» plegado. (#127)

### Fase 3 · El cliente como eje — #122

Depende de #36 (clientes) y comparte terreno con él.

- [x] UX-07 Portada = panel de clientes: último censo, tendencia de errores, alertas críticas. La lista de rastreos pasa a ser una vista con buscador y filtro por cliente y estado. (#128)
- [x] UX-07 Marcar cada rastreo como censo, canario o prueba (y filtrar por ello). (#128)
- [x] UX-08 Asignar a un cliente los rastreos que no tienen (Druni, los Lopesan de julio). (#128)
- [x] UX-08 Ficha de cliente con la serie de censos y sus comparaciones encadenadas. (#128)
- [x] UX-19 Nuevo rastreo empieza por cliente/perfil y hereda; nombre + semillas visibles, «Avanzado» plegado, botón «lanzar canario». (#128)

Revisión del 25-oct (fase 2 del informe), hecho en la rama `ux2-cliente`:

- [x] 2.1 Ficha del cliente con cuatro pestañas: Estado · Historial · Alertas · Configuración. Rutas `#/clientes/<id>[/historial|alertas|configuracion]`; `rastreos` sigue abriendo Historial. Migas «Clientes › cliente › pestaña». Un solo «Nuevo rastreo» en la ficha (V47).
- [x] 2.3 «Frente al anterior» = `comparacion.comparado_con` en la tabla (antes, la fila de debajo) y con fecha en tabla y gráficas (V10). «Alertas» = alertas abiertas (incidentes vivos) en portada, Estado y pestaña; las de una comparación, «saltaron N» con su fecha; sin abiertas, la portada dice las que saltó el último censo.
- [x] 2.2 El perfil como única unidad (V06, V07, V44; decisión 193; rama `ux2c-perfil`, en local): cada programación nueva cuelga de un perfil y hereda en cada disparo la configuración del momento. Programados con columnas Cliente y Perfil, «Editar», «Nueva programación» (cliente y perfil; día y hora en desplegables, hora de Madrid; el cron solo en «Otra») y aviso de repetidas (mismo perfil o mismas semillas, activas). En Configuración › Perfiles cada perfil dice cuándo se repite («Cada lunes a las 11:00», con Editar y Pausar) o «Programar cada semana…», y las de copia fija salen aparte como «Programaciones sin perfil». «Programar» desde un rastreo propone su perfil (o crear uno con sus semillas); la copia fija queda como segunda opción. Migración de las viejas: `scripts/migrar_programaciones_a_perfiles.py` (sin ejecutar en producción).
- [x] 2.14 Alta de cliente guiada (V37; decisión 194, en local): nombre y dominio → perfil «Sitio principal» con la home → Estado con «Siguiente paso»: «Lanzar canario», «Lanzar censo» y «Programar cada semana».

### Fase 4 · Pulido — #123

Sin fecha; se va haciendo cuando se toca cada zona.

- [x] UX-21 Pestañas y botones como `button` con `role="tab"`; ayudas también con foco/clic. (#129)
- [x] UX-21 Diálogos propios en lugar de `confirm`/`alert`/`prompt`. (#129)
- [ ] UX-21 Estilos en línea a clases (al tocar cada bloque, no en un PR gigante).
- [x] UX-20 Tildes y términos en castellano en toda la interfaz. (#129)
- [x] UX-18 Semántico: explicar qué da antes de pedir clave; parámetros en «Avanzado». (#129)
- [x] UX-22 Mínimo usable en móvil: barra superior sin desbordar. (#129)

---

## Estado

| fase | issue | estado |
|---|---|---|
| 0 · Arreglos rápidos | #119 | hecha (#125) |
| 1 · Navegación | #120 | hecha (#126) |
| 2 · Problemas como centro | #121 | hecha (#127) |
| 3 · Cliente como eje | #122 | hecha (#128) |
| 4 · Pulido | #123 | casi: queda pasar los estilos en línea a clases (#129) |

Revisión del 25-oct, 2.6 (26-oct): el Explorador pasa a ser la pestaña
«URLs» de la ficha del censo, activa también en la ficha de una URL (era V29:
nunca salía activa), con la cabecera compacta encima y sin botón «Volver».
Pendiente: el panel lateral del Explorador como versión recortada de la ficha
de URL; hoy repite los datos con otros bloques y otro orden.

Revisión del 25-oct, 2.7 (26-oct; V27, V28): la ficha de URL agrupa sus
problemas por tipo (en `/blog/` de Lopesan, 23 avisos pasan a 5 bloques), con
el texto de `incidencias.js`, el detalle legible, si es de plantilla o del
servidor (`paginas_afectadas` o `alcance`, decisiones 53, 64 y 73; «en parte»
si solo lo son algunas) y el enlace a URLs filtrada por el tipo. En un
hallazgo de plantilla el enlace dice «las N páginas donde se anota»: URLs
lista las páginas de anclaje, no las miles afectadas. La tarjeta «Google (90
días)» lee `gsc_clics`, `gsc_impresiones` y `gsc_posicion` de la URL (NULL no
es 0: sin datos dice por qué) y la canonical de Google de `google_inspeccion`
(decisión 189); contra una API anterior, de los avisos de la inspección.

Corregido tras la revisión independiente de la 2.7: `fmt.detalles` traduce
las claves que salían en crudo (`content_word_count`, `pct`, `reparto_por`,
`image_url`, `tipo texto_vacio`; las listas dicen qué hay dentro: «ancla
«saber más» en 3 páginas», «compite con /blog/… (22,2 %, posición 4,2)»); «Ver
las N páginas» cuenta con `/urls?issue_type=` como la pestaña URLs (antes
sumaba gravedades: 196 frente a 167); la tarjeta solo compara la canonical de
Google con la del censo si la inspección cae en la ventana de la decisión 144,
dice «ya es la que declara la página en este censo» cuando Google ya la
sigue y «puede cambiar», no «cambiará», cuando no; y si Search Console no
cubre los 90 días, la ventana empieza en `datos_desde` y lo avisa.
### Revisión del 25-oct · 2.9 Pestaña IA (V32, V33, V34)

- [x] Pestaña **IA** propia (`#/rastreos/<id>/ia`; el tramo viejo `enriquecido`
  sigue abriéndola), entre Puntuación y «Más». Propia y no dentro del menú: es
  lo único que se paga y nadie sabía si ya se había preguntado; con un nombre
  de dos letras cabe en la fila de escritorio, y en móvil la fila queda en dos
  líneas quitando los iconos; ahí «Más» cae a media fila, y su menú se abre a
  lo ancho de la fila de pestañas para no salirse de la pantalla. Sin botón en
  la cabecera del censo.
- [x] Dentro: «Nueva consulta…», **Lo ya preguntado** (fecha, módulos con nombre
  en castellano, sobre qué y a cuántas URLs, estado y gasto) y la respuesta de
  la ejecución elegida. El progreso solo mientras corre.
- [x] Lanzador: módulos con nombre llano y, solo de lo marcado, una explicación
  propia en castellano llano de qué pregunta y a qué páginas (el catálogo la
  da sin tildes y con jerga: «score de 4 niveles», «TOFU, MOFU, BOFU»); no se
  dice «solo a las sospechosas» por el título del catálogo, porque 10 de los
  11 módulos eligen sus páginas; por defecto **las candidatas de cada módulo**,
  con cuántas páginas pregunta cada uno («17 de 3.216»). **Clave de
  autorización** en lugar de ENRICHMENT_TOKEN (también en los errores de la
  API). Llamadas, memoria, tokens, presupuesto y «gasto del mes en todos los
  clientes, con tope de N $» en **Opciones avanzadas**; si lo que queda del tope
  no alcanza, el aviso sale a la vista. El botón dice «Lanzar y pagar hasta
  N $» y sigue pidiendo la clave.
- [x] **Antes de pagar otra vez**: si una ejecución de este censo (no fallida ni
  cancelada) ya hizo todas las preguntas marcadas, «Ya se preguntó lo mismo el
  24 oct (…): ver resultado», diciendo si fue sobre las mismas páginas.
- [x] **Problemas**: «Revisado con IA el 24 oct, N pares: compiten en a, no en b ·
  orientativo · ver resultado» en la canibalización (los pares salen de esos
  avisos) y, en los candidatos a soft 404, contando solo las páginas que
  tienen el problema (el módulo pregunta también a páginas con poco texto que
  no lo tienen). Si alguna lista no se lee entera, o hay un segmento elegido,
  no se dice nada. Los demás tipos no se relacionan con ninguna pregunta: sería
  inventarlo.
- [x] **Ficha de URL**: tarjeta «Revisado con IA» con la respuesta, si es
  orientativa, la confianza y la fecha. Sin respuestas no sale.
- [x] «Avanzado» pasa a **«Más»** con nombre fijo y la opción activa marcada
  dentro (V34; también 3.7).
- Pendiente: en Lopesan (el único censo de producción con ejecuciones) no hay
  avisos `soft_404_candidato` ni ejecución de canibalización, así que la línea
  de Problemas solo se ha visto con datos simulados. Las demás preguntas
  (title genérico, marcado frente a lo visible…) no se cruzan con Problemas.

### Lo que salió por el camino (2026-10-14)

Cosas que no estaban en la auditoría y aparecieron al hacerla:

- **`init()` se ejecutaba dos veces** desde siempre: `x-init="init()"` y el
  `init()` automático de Alpine 3. La lista de rastreos se pedía dos veces al
  cargar y, con las rutas, dos navegaciones iniciales se pisaban (#126).
- **Los avisos breves no existían**: `showToast()` llenaba una lista que nadie
  pintaba. «Rastreo iniciado», «Cliente guardado»… no se habían visto nunca (#129).
- **Seis tipos de incidencia sin nombre** en la interfaz (`canonical_multiple`,
  `canonical_in_body`, `canonical_a_http`, `title_multiple`,
  `description_multiple`, `hreflang_in_body`) (#127).
- **`click_depth` NULL no siempre es «sin camino desde la home»**: en un censo
  analizado antes de la decisión 90 es «sin calcular» (#127).
- **El orden de `/links` por `id`** recorría la tabla entera de todos los
  rastreos: 2 min la primera página en local, 3,1 s en producción tras el
  arreglo (#125).

### Lo que la API ya devolvía y la interfaz no enseñaba (2026-10-18)

- **Sondas de enlaces externos y recursos** (decisiones 123 y 124) en el
  formulario, en una sección «Sondas fuera del censo» junto a la paridad móvil,
  con sus topes (`enlaces_externos_max`, `recursos_max`, `sondas_por_host`).
  Antes las dos casillas se mandaban SIEMPRE (`false` explícito); ahora solo se
  mandan si se tocan respecto a lo que cargó el modal, así que un job lanzado
  desde un perfil sigue heredando del perfil.
- **Tarjetas en el Resumen de la ficha**: certificado TLS por host (110), soft
  404 por sonda (105), enlaces externos y recursos comprobados (123/124, con el
  aviso de «comprobados < distintos») y sitemaps leídos (126). Cada una solo si
  el job trae el dato. `jobs.sitemaps` va en la ficha (`JobDetailResponse`) y no
  en la lista de rastreos.
- **Veredicto del modo lista** (128) arriba de la ficha, antes de las alertas.
- Lo abierto: la lista de rastreos (`GET /api/jobs`) sigue llevando `soft404`
  (con `detalle_sondas` y el texto de cada sonda), `tls` y `sondas_resumen` en
  cada fila sin pintarlos; pasarlos a la ficha como `sitemaps` aligeraría la
  lista.

### Revisión del 25-oct · 2.12 Producción

- [x] La barra pasa a **Clientes · Producción**. Producción tiene tres apartados:
  **Ahora** (`#/produccion`: en curso, en cola, próximos programados y rastreos
  sin cliente), **Programados** (`#/programados`) y **Todos los rastreos**
  (`#/rastreos`, el histórico con sus filtros). Las rutas viejas siguen
  funcionando y abren su apartado.
- En curso con barra y «1.374 / ~4.000 · termina hacia las 14:20». El total es lo
  rastreado + la cola del planificador (`pending_count`), sin pasar de
  `max_urls`; sin cola, solo «de N como mucho» y sin hora de fin. El ritmo se
  mide solo entre refrescos de la propia vista (1 min como mínimo): no se usa
  `started_at`, porque al reanudar un rastreo el worker lo reinicia y la cuenta
  de páginas no, y la hora de fin saldría demasiado pronto. Sin base,
  «calculando la hora de fin…». Con la cola del planificador en 0 o el tope
  alcanzado, «terminando»; con 2 min sin páginas nuevas, «sin páginas nuevas en
  los últimos N min». La frase de arriba separa los que rastrean de los que ya
  solo se analizan.
- En cola con su puesto según el orden real de Redis (`GET /api/jobs/cola`, FIFO).
  Sin ese dato (API anterior o Redis caído) se ordena por creación y no se
  dice el puesto.
- La columna Fallidas solo si alguna fila tiene alguna (V38). En Programados,
  sin cron crudo a la vista, la zona dicha («hora de Madrid»), las fechas en la
  zona de la programación y «toca» → «lanzado a su hora» (V44). En móvil la
  tabla se apila.

### Revisión del 25-oct · 2.10 y 2.11 Problemas

- [x] **Diferencia frente al anterior por tipo** («+12», «−5», «+3 · −1»,
  «nuevo», «igual»), con la referencia de todo lo demás
  (`job.comparacion.comparado_con`) y las cifras que ya guarda
  (`incidencias.nuevas/resueltas/siguen` por tipo). Arriba, el total («+643
  nuevas · −437 resueltas frente al 14 oct»), las que estaban en páginas no
  visitadas y los avisos de la comparación; abajo, los tipos que ya no
  aparecen. Si no se puede afirmar —otro alcance, truncado, bloqueado o
  versiones del análisis distintas, también si uno de los dos se re-analizó
  después (decisión 191)— lo dice y no hay etiquetas. Con una sección elegida
  no se enseña (es del sitio entero).
- [x] **Secciones del negocio** (decisión 125) como primer nivel: tarjetas con
  URLs, páginas con errores y con avisos, que filtran; el reparto de cada tipo
  por sección y las páginas fuera de toda sección. Sin segmentos de regla (es
  el caso de Lopesan), nada de esto se pinta y el desplegable de formas de ruta
  queda como estaba.
- [x] **Soft 404 de plantilla** (V17, decisión 190): un aviso agrupado
  `soft_404_plantilla` que cuenta plantillas y despliega cada una con la URL
  inventada que se pidió; el Resumen dice «N plantillas responden 200 a una URL
  que no existe» en naranja y no «0 páginas» en verde.
- [x] Tras la revisión independiente: el Resumen y la lista desplegada pintan
  solo las sondas que el análisis cuenta (sin muros de WAF servidos con 200;
  `sondas_responden_200`, o el mismo filtro en los censos anteriores); con una
  sección elegida la fila del soft 404 de plantilla no dice «7 plantillas»; la
  etiqueta de cambio va en la fila visible con el filtro de gravedad; la
  diferencia exige que las incidencias de hoy cuadren con las comparadas; los
  nombres de plantilla sin «(4n)» ni «:slug»; «Ver la incidencia» en singular.
- [x] El Resumen ya no desborda a 390 px (V41): las columnas de
  `.overview-grid` pasan a `minmax(0,1fr)` y las tablas de Search Console van
  en `.table-wrap` con su propio scroll.
- Lo abierto: la fila de un hallazgo agrupado de otros tipos sigue contando «1
  página» (la de anclaje).

### Cómo se probó

Cada fase, con una API local montada sobre el worktree (`docker run … -v
$PWD:/app crawler-masivo-api uvicorn … --reload`, puerto 8001) contra la BD de
desarrollo, y con Playwright dentro de la imagen del crawler: capturas de cada
vista (`docs/ux/capturas.py`), excepciones de página (`pageerror`, no solo la
consola: el fallo del `x-for` con claves repetidas solo salía ahí), historial
del navegador (atrás cuatro veces), teclado en las pestañas y desbordamiento a
390 px.

Al cerrar una casilla: marcarla aquí y en la issue, con el PR al lado.
