# Plan de prioridades

> Estado a 2026-10-07. La fuente de verdad del *orden* son los milestones de
> GitHub; esto explica **por qué** ese orden y qué cifra lo sostiene.
> `docs/AUDITORIA_Y_VERIFICACION.md` sigue siendo el detalle técnico.

## Regla 0: todo lleva criterio SEO

Antes que el orden está el criterio. Ninguna issue de este plan se cierra
—ni se abre— sin responder **qué decide Google con ese dato**. No es retórica:
es lo que separa un arreglo de un cambio de números.

Tres ejemplos de este mismo plan, para que se vea la diferencia:

- **`high_outlink_count`** no se arregla "deduplicando", se arregla preguntando
  qué le preocupa a Google: la dilución del presupuesto de rastreo y del reparto
  de autoridad por enlaces **editoriales**. Un megamenú de 500 enlaces repetido
  en todo el sitio es un hecho de la plantilla, no un problema de cada página.
  Contar solo `link_position='content'` no es una optimización del contador: es
  la única lectura que significa algo. (7.011 páginas → 51.)
- **`inlinks_count`** no es un número, son tres preguntas distintas metidas en
  una columna: *¿está enlazada?* (huérfana: cuenta cualquier enlace interno,
  incluso `nofollow`, porque la página SÍ está enlazada, solo que sin respaldo),
  *¿recibe autoridad?* (PageRank: ahí `nofollow` no cuenta) y *¿desde dónde?*
  (si todos sus enlaces vienen de páginas noindex o 404, el hallazgo es ese y no
  "tiene 12 inlinks"). Colapsarlas en un entero es lo que hoy tapa 6.925
  páginas.
- **El contenido recortado a 500 caracteres** del export no era un bug de
  formato: un informe de contenido que parece completo y no lo es hace que se
  descarte una página por "thin content" que no lo es.

Corolario operativo: cada issue de `M1` dice en su cuerpo qué decisión SEO
depende de ese dato, y cada arreglo se mide **antes y después sobre un censo
real**, no sobre un caso inventado (lección 13 del `CLAUDE.md`).

## Cómo está organizado GitHub

**Milestones** (secuencia, no prioridad): `M1 · Dato fiable` → `M2 · Decisiones:
base` → `M3 · Decisiones: rastreo y estructura` → `M4 · Decisiones: contenido y
SEO` → `M5 · Producto`.

**Etiquetas**: `tipo:` (bug / deuda / feature / decision), `area:` (rastreo /
analisis / datos / ui / infra / decisiones), `P0`-`P2`, `bloqueado` (depende de
otra issue para poder empezar) y `medido` (tiene una cifra real que justifica
el arreglo).

Regla: **nada entra en M2 hasta que M1 esté cerrado.** No es burocracia, lo
exige la propia #6: *«sin eso, las decisiones se toman sobre datos corruptos»*.
Una decisión del modelo sobre un PageRank mal calculado es un error más caro
que el PageRank mal calculado, porque llega al informe con aire de conclusión.

## M1 · Dato fiable (prerrequisito)

Lo que corrompe o tapa dato **hoy**, en los censos que ya se entregan.

| # | Qué | Cifra que lo justifica |
|---|---|---|
| #41 | CI que corra los tests y bloquee el despliegue | El deploy a producción no ejecuta ni un test; la suite tarda 4 s |
| #42 | Decidir el destino de `v2-experimental` | 72 commits, 3 meses, motor ya superado por master; 6 arreglos que sí valen |
| #24 | PageRank: enlaces, inlinks y conteos | `high_outlink` avisa en 7.011 páginas; contando solo contenido, 51 |
| #37 | Cola LIFO y job con 0 URLs → `completed` | 1 de sus 3 partes ya está arreglada; las otras dos siguen vivas |
| #23 | Paraguas de la auditoría 2026-09 | 90 casillas abiertas en sus siete sub-issues |
| #26 | Indexabilidad: una única función con tabla de tests | El analizador normaliza peor que el extractor (`_norm_url` ignora puerto, barra final y www) |
| #25 #27 #29 | Rastreo, contenido y analyzer | Ver cada issue |
| #28 #30 | Recursos y scripts de control | P2 dentro de M1 |

**Definición de hecho de M1**: la CI corre en verde en cada PR, los conteos de
enlaces se pueden recalcular dos veces y dan lo mismo, y un rastreo bloqueado
por robots.txt no se entrega como sitio limpio.

### Estado a 2026-10-07: la parte determinista de M1 está cerrada

Los tres puntos de la definición de hecho se cumplen: la CI bloquea el
despliegue (`deploy.yml` depende de `tests.yml`), `compute_link_counts` pone los
conteos a cero antes de agregar —así que un re-análisis no deja cifras rancias—
y un `Disallow: /` deja el job en `failed` con motivo `robots_bloquea_todo`.

Lo entregado, con la medición de cada cosa, está en las decisiones 44-57 del
`CLAUDE.md` y en los comentarios de #24 a #30. Resumen de lo que más movía:

| Qué estaba mal | Medido |
|---|---|
| Páginas guardadas con **0 palabras** por una clase en el `<body>`, o porque el limpiador del navegador borraba la página entera (texto, enlaces y titulares) | reproducido en Chromium: el HTML capturado era `<html><head></head></html>` |
| El aviso de contenido escaso se medía sobre el body, con el menú dentro | 997 → **10.433** páginas escasas en un censo; 4.736 de ellas indexables |
| El ratio texto/HTML generaba issue en el 97% de un sitio | `pct_thin` 97,3% → **2,1%** en blogs.uoc.edu |
| El `@graph` de Yoast dejaba sin tipo los datos estructurados | **30.701 bloques en 29.803 páginas**: el 100% de un censo |
| Los duplicados incluían variantes `?utm` y noindex, y el exacto se medía por bytes | title_duplicate 14.580 → 5.299; duplicado exacto **0 → 745 páginas** |
| Cabeceras de seguridad avisadas por página | **266.504** avisos que eran uno por sitio |
| Títulos «demasiado largos» medidos por caracteres | el **29%** de esos avisos era falso (Google corta por píxeles) |
| Enlaces desde páginas rotas y noindex contaban como respaldo | −240.543 entrantes en www.uoc.edu; **6 puntos** de PageRank pasan de noindex a indexables en blogs.uoc.edu |

**Lo que queda abierto en esas issues es juicio**, y por eso va a M3/M4: si un
carrusel o un bloque de suscripción es contenido (#15), si un parámetro de URL
es legítimo (#12, #22), qué tipo de página es cada plantilla (#12). Y dos
decisiones de modelo que son tuyas: **C2** (si el peso de un `nofollow` se
evapora o se reparte) y la revisión a mano del top 50 por PageRank de los tres
censos, que ya está sacado.

## M2 · Decisiones: base (F0)

#40 (botón bajo demanda con coste estimado), #7 (cliente de Jev vía OpenRouter,
tabla `decisions`, caché, presupuesto), #8 (juego de evaluación y calibración),
#9 (diseño de preguntas).

Orden interno: **#40 antes que #7**. El botón define el contrato de coste y
presupuesto; construir el cliente sin saber quién lo dispara y con qué tope
lleva a un cliente que hay que rehacer. Y #8 antes de usar nada en producción:
sin juego etiquetado no se puede saber si una pregunta acierta.

## M3 · Decisiones: rastreo y estructura (F1-F2)

#10, #11 (salud del rastreo) y #12, #13, #14 (estructura). **#12 es la clave**:
el tipo de página por URL lo necesitan casi todas las de M4. #13 depende del
peso por repetición medida que ya entró en #24.

## M4 · Decisiones: contenido y SEO (F3-F4)

#39, #15-#22. Es la capa que da valor al cliente, y la que más se beneficia de
que M1 esté limpio: todas sus señales se calculan sobre `page_content`,
`indexability_status` y el grafo de enlaces.

## M5 · Producto

#36 (clientes como entidad central: rastreos programados, alertas, informe
central, rastreo incremental) es la que más cambia el producto y la única P1 de
este bloque. #31, #32, #33, #34, #35 son P2.

## Decisiones tomadas

- **2026-10-06: `v2-experimental` no se mergea.** Su motor está en master y es
  tres meses más nuevo; su superficie de producto la describen mejor #36 y #6.
  Pendiente de ejecutar el rescate de seis arreglos (#42).
- **2026-10-06: nada de M2-M4 empieza antes de cerrar M1.**
- **2026-10-07: C3 también en el PageRank.** Una página `noindex` recibe pero no
  reparte: sus enlaces no pasan autoridad (Google acaba tratándolos como
  nofollow) y su masa acumulada va al teletransporte, no a sus destinos. Que sí
  reciba es deliberado: un enlace a una noindex es autoridad que se tira, y eso
  es lo que mide `reparto["no_indexable"]`. En blogs.uoc.edu mueve **6 puntos**
  de PageRank; el orden del top 50 no cambia en ninguno de los tres censos.
- **2026-10-07: el ratio texto/HTML deja de generar issue.** No es una señal de
  Google y, bien medido, salta en el 97% de las páginas de un sitio moderno. La
  columna se sigue calculando y exportando.
- **2026-10-07: las cabeceras de seguridad se avisan por host, no por página.**
  Son una propiedad del servidor y no afectan al posicionamiento; `unsafe_crossorigin`
  desaparece (los navegadores aplican `noopener` por defecto desde 2021).

## Lo que no está en ninguna issue y conviene recordar

- `resources.size_bytes` nunca se rellena; el dato saldría de unir
  `resource_url` con `urls.content_length` de los recursos ya rastreados.
- `urls.http_version` solo se rellena con `render_js`; por el camino de
  curl_cffi hay que reimplementar el handler de `scrapy-impersonate`.
- Un `?excel_es=1` en el export para que Excel en español no lea los decimales
  como texto.
- `docs/**` y los `.md` de la raíz **no** disparan el despliegue (filtro de
  `paths` en `deploy.yml`). Cualquier cambio en código sí.
