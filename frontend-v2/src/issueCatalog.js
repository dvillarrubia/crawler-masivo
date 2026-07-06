/** Catálogo de incidencias: nombre legible + explicación en castellano de
 *  cada tipo que emite el análisis (analysis/analyzer.py, link_suggester,
 *  query_coverage, anchor_relevance). Las claves son los issue_type REALES
 *  de la base de datos — mantener sincronizado con el backend. */

export const ISSUE_CATALOG = {
  // ── Respuestas y redirecciones ───────────────────────────────────────
  "4xx_error": ["Error 4xx", "La página responde con error del cliente (404 no encontrada, 403 prohibida…). Los enlaces que apuntan aquí pierden autoridad y frustran al usuario."],
  "5xx_error": ["Error 5xx", "El servidor falla al servir esta página. Si Google la ve así repetidamente, la acaba sacando del índice."],
  connection_error: ["Error de conexión", "No se pudo ni conectar con la página (timeout, DNS, conexión rechazada)."],
  redirect_chain: ["Cadena de redirecciones", "Para llegar al destino final hay que atravesar varias redirecciones seguidas. Cada salto pierde autoridad y ralentiza el rastreo."],
  redirect_loop: ["Bucle de redirecciones", "La redirección acaba volviendo sobre sí misma: nunca se llega a ningún destino."],
  meta_refresh_redirect: ["Redirección por meta refresh", "La página redirige con una etiqueta meta refresh en vez de una redirección HTTP. Más lenta y peor interpretada por los buscadores."],
  js_redirect: ["Redirección por JavaScript", "La página redirige mediante JavaScript. Los rastreadores que no ejecutan JS no la siguen."],
  slow_page: ["Página lenta", "La página tarda más de lo aceptable en responder. Consume presupuesto de rastreo y empeora la experiencia."],
  soft_404: ["Soft-404 (error disfrazado)", "Devuelve 200 (OK) pero su contenido es una página de error. Google la trata como 404 y desperdicia rastreo."],

  // ── Canonical e indexabilidad ────────────────────────────────────────
  canonical_missing: ["Sin canonical", "La página no declara URL canónica. Ante variantes o parámetros, Google elige por su cuenta."],
  canonical_broken: ["Canonical roto", "El canonical apunta a una URL que no responde bien (error o redirección)."],
  canonical_cross_domain: ["Canonical a otro dominio", "El canonical apunta a un dominio distinto: le está regalando la indexación de este contenido."],
  canonical_chain: ["Cadena de canonicals", "El canonical apunta a una página cuyo canonical apunta a otra. Google puede ignorarlos todos."],
  canonical_loop: ["Bucle de canonicals", "Dos o más páginas se declaran canónicas entre sí en círculo. Google no sabe cuál es la buena."],
  noindex_page: ["Página con noindex", "La página pide explícitamente no aparecer en Google. Correcto si es intencional; grave si es una página de negocio."],

  // ── Seguridad ────────────────────────────────────────────────────────
  http_url: ["Página sin HTTPS", "Se sirve por HTTP sin cifrar en lugar de HTTPS."],
  mixed_content: ["Contenido mixto", "Página HTTPS que carga recursos (imágenes, scripts…) por HTTP. El navegador puede bloquearlos y la marca como insegura."],
  missing_hsts: ["Falta cabecera HSTS", "Sin esta cabecera, el navegador no fuerza HTTPS en visitas futuras: deja hueco a ataques de degradado a HTTP."],
  missing_csp: ["Falta Content-Security-Policy", "Esta cabecera limita qué scripts y recursos puede cargar la página; sin ella, una inyección de código tiene vía libre."],
  missing_x_content_type_options: ["Falta X-Content-Type-Options", "Sin esta cabecera el navegador puede «adivinar» tipos de archivo, lo que permite colar scripts camuflados. Se arregla con una línea en el servidor."],
  missing_x_frame_options: ["Falta X-Frame-Options", "Sin esta cabecera cualquier web puede meter tu página en un iframe invisible y robar clics (clickjacking)."],
  unsafe_crossorigin: ["Enlace externo inseguro", "Enlaces con target=_blank sin rel=noopener: la página de destino puede manipular la tuya (tabnabbing)."],

  // ── Sitemaps y cobertura de rastreo ──────────────────────────────────
  in_sitemap_not_crawled: ["En sitemap pero inalcanzable", "La URL está declarada en el sitemap pero el rastreo no llegó a ella siguiendo enlaces."],
  crawled_not_in_sitemap: ["Falta en el sitemap", "La URL existe y se rastreó, pero el sitemap no la declara. El sitemap está incompleto."],
  orphan_not_in_crawl: ["Huérfana real", "El sitemap o Search Console la conocen, pero navegando por el sitio no se llega a ella. Sin enlaces internos no recibe autoridad."],
  stale_lastmod: ["Fecha de sitemap falsa", "El lastmod del sitemap no cuadra con la realidad: dice que la página cambió y el contenido es idéntico, o al revés. Resta credibilidad al sitemap entero."],
  watchlist_check_failed: ["Página vigilada con problemas", "Una página de negocio de la watchlist ha dejado de cumplir sus condiciones (responder 200, ser indexable y canonical a sí misma)."],
  crawl_trap_detected: ["Trampa de rastreo", "El robot detectó un patrón de URLs infinitas (calendarios, filtros combinables…) y dejó de seguirlo. Google puede caer en la misma trampa."],

  // ── Títulos y descripciones ──────────────────────────────────────────
  title_missing: ["Sin title", "La página no tiene etiqueta <title>, el texto azul del resultado en Google."],
  title_too_short: ["Title demasiado corto", "El title no aprovecha el espacio del resultado ni describe bien la página."],
  title_too_long: ["Title demasiado largo", "El title supera el ancho que Google muestra: se corta con puntos suspensivos."],
  title_duplicate: ["Title duplicado", "Varias páginas comparten el mismo title. Compiten entre sí y ninguna destaca."],
  description_missing: ["Sin meta description", "Falta la descripción del resultado; Google inventa el texto y suele hacerlo peor."],
  description_too_short: ["Description demasiado corta", "Desaprovecha el espacio del resultado en Google."],
  description_too_long: ["Description demasiado larga", "Se corta en los resultados por exceso de longitud."],
  description_duplicate: ["Description duplicada", "Varias páginas comparten la misma meta description."],

  // ── Encabezados y contenido ──────────────────────────────────────────
  h1_missing: ["Sin H1", "La página no tiene encabezado principal H1."],
  h1_multiple: ["Varios H1", "Hay más de un H1: diluye la jerarquía del contenido."],
  h1_duplicate: ["H1 duplicado", "Varias páginas comparten el mismo H1."],
  image_missing_alt: ["Imágenes sin texto alternativo", "Imágenes sin atributo alt: invisibles para lectores de pantalla y para la búsqueda de imágenes de Google."],
  low_word_count: ["Poco texto", "La página tiene muy poco texto total. Difícil que posicione por nada."],
  low_text_ratio: ["Poca proporción de texto", "La mayor parte del peso de la página es código, no texto visible."],
  very_low_text_ratio: ["Casi todo es código", "Proporción de texto visible extremadamente baja: la página es prácticamente solo HTML/JS."],
  duplicate_content: ["Contenido duplicado", "El contenido de esta página es idéntico al de otra."],
  near_duplicate_content: ["Contenido casi duplicado", "El contenido es casi idéntico al de otra página (similitud muy alta sin ser copia exacta). Candidatas a fusionarse."],
  low_unique_content: ["Poco contenido propio", "Descontando la plantilla que se repite en toda la sección (menús, footers, bloques legales), queda muy poco texto propio."],

  // ── Hreflang y datos estructurados ───────────────────────────────────
  hreflang_missing_return: ["Hreflang sin retorno", "Esta página declara una versión en otro idioma, pero esa versión no la declara de vuelta. Google exige reciprocidad para respetarlas."],
  hreflang_invalid_lang: ["Hreflang con código inválido", "El código de idioma/región del hreflang no es válido (ej. «en-UK» en vez de «en-GB»)."],
  hreflang_broken_target: ["Hreflang roto", "El hreflang apunta a una URL que da error o redirige."],
  structured_data_error: ["Datos estructurados con errores", "El marcado (schema.org) tiene errores que impiden a Google usarlo para resultados enriquecidos."],
  structured_data_warning: ["Datos estructurados mejorables", "El marcado es válido pero le faltan campos recomendados."],

  // ── Contenido que solo existe tras ejecutar JavaScript (GEO) ─────────
  content_only_after_js: ["Contenido solo tras JavaScript", "Buena parte del contenido de la página solo aparece después de ejecutar JavaScript. Los buscadores y motores de IA que no renderizan JS ven la página casi vacía."],
  schema_only_after_js: ["Datos estructurados solo tras JS", "El marcado schema.org solo se inyecta al ejecutar JavaScript; en el HTML crudo no existe. Google puede no verlo y perderás el resultado enriquecido."],

  // ── Enlazado y arquitectura ──────────────────────────────────────────
  orphan_page: ["Página sin enlaces internos", "Ninguna página del rastreo la enlaza; solo se llegó por sitemap o semilla. Sin enlaces internos no recibe autoridad."],
  link_orphan: ["Sin camino desde la portada", "No existe ninguna ruta de clics desde la home hasta esta página, aunque esté enlazada desde algún rincón."],
  excessive_click_depth: ["Demasiados clics de profundidad", "Hacen falta demasiados clics desde la portada para llegar. Lo profundo se rastrea menos y posiciona peor."],
  no_contextual_inlinks: ["Sin enlaces desde contenido", "Página de una sección de negocio que solo recibe enlaces de menús o listados, ninguno desde el texto de otras páginas."],
  authority_sink: ["Autoridad estancada", "Página con mucha autoridad acumulada que no enlaza a nada desde su contenido: la autoridad muere ahí en vez de repartirse."],
  deep_pagination: ["Paginación profunda", "Cadenas de paginación muy largas (página 4, 5, 6…). Lo que solo es alcanzable así apenas se rastrea."],
  hierarchy_imbalance: ["Arquitectura desequilibrada", "La distribución de profundidades del sitio está desequilibrada: niveles saturados y saltos bruscos."],
  high_outlink_count: ["Demasiados enlaces en el contenido", "El CUERPO de la página (sin contar menú, cabecera ni pie) tiene tantísimos enlaces que cada uno transmite una fracción mínima de autoridad. Los enlaces de plantilla no cuentan aquí porque son iguales en todo el sitio."],
  equity_leak: ["Fuga de autoridad", "Gran parte de la autoridad que sale de esta página se pierde en enlaces nofollow, rotos o redirigidos."],
  no_inlinks_with_traffic: ["Con tráfico pero sin enlaces internos", "La página recibe clics de Google pero ninguna otra página del sitio la enlaza. Enlazándola le darías la autoridad que le falta para rendir aún más."],
  underlinked_high_performer: ["Rinde mucho para lo poco enlazada que está", "Trae clics muy por encima de la media pese a tener poca autoridad interna (PageRank bajo). Reforzar su enlazado suele traducirse en más tráfico."],

  // ── Calidad de las URLs ──────────────────────────────────────────────
  url_too_long: ["URL demasiado larga", "Fea en resultados, difícil de compartir y de mantener."],
  url_non_ascii: ["URL con caracteres raros", "Contiene acentos, eñes u otros caracteres que acaban codificados en porcentajes ilegibles."],
  url_uppercase: ["URL con mayúsculas", "Riesgo de duplicados: /Pagina y /pagina pueden ser la misma página dos veces."],
  url_underscores: ["URL con guiones bajos", "Google recomienda guiones normales para separar palabras."],
  url_multiple_slashes: ["URL con barras dobles", "Barras // en la ruta: suele delatar errores de generación de enlaces."],
  url_has_parameters: ["URL con parámetros", "Parámetros (?orden=, ?filtro=…) que multiplican variantes de la misma página."],
  url_non_seo_friendly: ["URL malformada", "URL rota o ilegible que un robot puede descubrir y rastrear. Si nuestro crawler la encontró, Google también puede."],
  url_cms_faceted: ["URL de filtros del CMS", "Navegación facetada (combinaciones de filtros) que genera un número explosivo de páginas casi iguales y quema presupuesto de rastreo."],

  // ── Entidades (GLiNER2): cruce entidad ↔ demanda real ────────────────
  entity_query_mismatch: ["Entidad ausente donde rankeas", "La página rankea para una búsqueda cuya entidad principal ni siquiera aparece en su contenido. Añadirla (on-page) suele ser la subida más barata."],
  entity_coverage_gap: ["Entidad demandada sin página", "Hay búsquedas reales pidiendo una entidad que ninguna página del sitio cubre. Contenido a crear con demanda demostrada."],
  entity_cannibalization: ["Canibalización por entidad", "Dos o más páginas tienen la misma entidad principal en la misma fase del funnel: compiten entre sí. Decidir cuál manda (consolidar, diferenciar o desoptimizar)."],
  funnel_mismatch: ["Página en la fase equivocada", "Una página transaccional está capturando búsquedas informativas (o al revés). El usuario no encuentra lo que esperaba: crear la pieza que falta o reconducir el enlazado."],

  // ── Semántica y cobertura (propuestas: las firma una persona) ────────
  semantic_cannibalization: ["Canibalización", "Dos páginas hablan de lo mismo y compiten por las mismas búsquedas. Decidir cuál manda y fusionar o diferenciar."],
  passage_gap: ["Búsqueda sin respuesta", "Hay demanda real (búsqueda de Search Console con impresiones) pero ningún pasaje del sitio la responde. Contenido a crear o ampliar."],
  buried_passage: ["Respuesta enterrada", "El pasaje que responde a la búsqueda existe, pero está al fondo de su página. Subirlo o darle su propia sección."],
  orphan_chunk: ["Contenido sin demanda", "Bloques de texto que no responden a ninguna búsqueda medida: nadie está pidiendo eso."],
  generic_anchor: ["Anchors genéricos", "La página recibe enlaces con textos vacíos («leer más», «aquí») que no dicen de qué va. Anchor descriptivo = señal semántica."],
  anchor_target_mismatch: ["Anchor engañoso", "El texto del enlace promete una cosa y la página de destino habla de otra. Confunde a usuarios y buscadores."],
};

/** Nombre legible; si el tipo no está catalogado, se humaniza el slug. */
export const issueLabel = (type) => {
  const e = ISSUE_CATALOG[type];
  return e ? e[0] : (type || "").replace(/_/g, " ");
};

/** Explicación; honesta si falta. */
export const issueInfo = (type) => {
  const e = ISSUE_CATALOG[type];
  return e ? e[1] : "Tipo de incidencia sin descripción en el catálogo (avisa para añadirla).";
};

/** Clases de arista del clasificador de arquitectura, en claro. */
export const EDGE_CLASS_INFO = {
  contextual: "enlace desde el cuerpo del contenido — el que más autoridad y señal semántica transmite",
  listado: "enlace de un listado o parrilla de tarjetas (categorías, archivos)",
  breadcrumb: "enlace de la miga de pan",
  paginacion: "enlace de paginación (siguiente, anterior, números)",
  menu: "enlace del menú de navegación — se repite en todo el sitio",
  footer: "enlace del pie de página — se repite en todo el sitio",
  sidebar: "enlace de una barra lateral",
  desconocido: "no se pudo clasificar por la estructura del HTML",
};

/* ------------------------------------------------------------------ */
/* Formateo de la columna «Detalles»: una FRASE por tipo de incidencia */
/* ------------------------------------------------------------------ */

const _n = (v) => (v == null ? "—" : Number(v).toLocaleString("es"));
const _pct = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1).replace(".", ",")}%`);
const _dec = (v) => (v == null ? "—" : Number(v).toFixed(3).replace(/0+$/, "").replace(/\.$/, "").replace(".", ","));
const _urls = (arr, max = 2) => {
  if (!arr || !arr.length) return "";
  const shown = arr.slice(0, max).join(" · ");
  return arr.length > max ? `${shown} (+${arr.length - max} más)` : shown;
};

/** Razones internas traducidas. */
const REASONS = {
  no_click_path_from_home: "no existe ninguna ruta de clics desde la portada hasta esta página",
  probe_template_hash: "su HTML es idéntico a la página de error del sitio",
  probe_template_similarity: "su contenido es casi idéntico a la página de error del sitio",
  error_title_low_content: "tiene título de error y casi ningún contenido",
  lastmod_changed_content_identical: "el sitemap dice que la página cambió, pero el contenido es exactamente el mismo",
  content_changed_lastmod_stale: "el contenido cambió, pero el sitemap sigue con la fecha antigua",
  not_crawled: "no responde (no se pudo rastrear)",
  not_indexable: "ha dejado de ser indexable",
  canonical_not_self: "su canonical ya no apunta a sí misma",
};
const _reason = (r) => REASONS[r] || (r || "").replace(/^status_(\d+)$/, "responde con error $1").replace(/_/g, " ");

/** Frase específica por tipo. d = details tal cual lo emite el backend. */
const DETAIL_RENDERERS = {
  title_too_short: (d) => `Longitud ${_n(d.length)} caracteres (mínimo recomendado ${_n(d.min)})`,
  title_too_long: (d) => `Longitud ${_n(d.length)} caracteres (máximo recomendado ${_n(d.max)})`,
  description_too_short: (d) => `Longitud ${_n(d.length)} caracteres (mínimo recomendado ${_n(d.min)})`,
  description_too_long: (d) => `Longitud ${_n(d.length)} caracteres (máximo recomendado ${_n(d.max)})`,
  title_duplicate: (d) => `Comparte title con otras ${_n((d.duplicate_urls || []).length)} páginas del sitio`,
  description_duplicate: (d) => `Comparte description con otras ${_n((d.duplicate_urls || []).length)} páginas`,
  h1_duplicate: (d) => `Comparte H1 con otras ${_n((d.duplicate_urls || []).length)} páginas`,
  h1_multiple: (d) => `Tiene ${_n(d.count)} encabezados H1 (debería haber uno)`,
  duplicate_content: (d) => `Contenido idéntico al de otras ${_n((d.duplicate_urls || []).length)} páginas`,
  near_duplicate_content: (d) =>
    `Casi idéntica a otras ${_n((d.cluster_size || 1) - 1)} páginas${d.urls ? `: ${_urls(d.urls)}` : ""}`,
  low_word_count: (d) => `Solo ${_n(d.word_count)} palabras de texto`,
  low_unique_content: (d) =>
    `Solo ${_n(d.unique_word_count)} palabras propias (mínimo ${_n(d.threshold)}); el ${_pct(d.boilerplate_ratio)} de la página es plantilla repetida`,
  url_too_long: (d) => `${_n(d.length)} caracteres de URL`,
  high_outlink_count: (d) =>
    `${_n(d.count)} enlaces en el contenido${d.total_outlinks != null ? ` (${_n(d.total_outlinks)} en total contando menú y pie)` : ""}`,
  slow_page: (d) => `Respondió en ${_n(d.response_time_ms)} ms (umbral: ${_n(d.threshold_ms)} ms)`,
  redirect_chain: (d) => `${_n(d.hops ?? (d.chain || []).length)} saltos: ${_urls(d.chain, 3)}`,
  redirect_loop: (d) => `La cadena vuelve sobre sí misma: ${_urls(d.chain, 3)}`,
  canonical_chain: (d) => `Cadena de canonicals: ${_urls(d.chain, 3)}`,
  canonical_loop: (d) => `Bucle de canonicals: ${_urls(d.chain, 3)}`,
  structured_data_error: (d) =>
    `${d.schema_type || "Schema"}: ${(d.validation_issues || []).filter((v) => v.level === "error").map((v) => v.message).join("; ") || "faltan campos obligatorios"}`,
  structured_data_warning: (d) =>
    `${d.schema_type || "Schema"}: ${(d.validation_issues || []).map((v) => v.message).join("; ") || "faltan campos recomendados"}`,
  hreflang_missing_return: (d) =>
    `Declara «${d.lang}» → ${d.target || d.href}, pero esa página no enlaza de vuelta (falta la etiqueta recíproca)`,
  hreflang_invalid_lang: (d) => `Código de idioma/región no válido: «${d.lang}»`,
  hreflang_broken_target: (d) =>
    `«${d.lang}» apunta a ${d.target || d.href}, que responde ${_n(d.target_status)} en vez de 200`,
  meta_refresh_redirect: (d) => (d.target ? `Redirige a ${d.target}` : ""),
  js_redirect: (d) => (d.target ? `Redirige por JavaScript a ${d.target}` : ""),
  soft_404: (d) => `Parece un error disfrazado: ${_reason(d.reason)}${d.similarity != null ? ` (similitud ${_dec(d.similarity)})` : ""}`,
  stale_lastmod: (d) => `${_reason(d.reason)}${d.lastmod ? ` (lastmod declarado: ${String(d.lastmod).slice(0, 10)})` : ""}`,
  link_orphan: (d) => _reason(d.reason),
  excessive_click_depth: (d) =>
    `A ${_n(d.click_depth)} clics de la portada (límite: ${_n(d.limit)}${d.is_business ? ", más estricto por ser sección de negocio" : ""})`,
  no_contextual_inlinks: () =>
    "Solo recibe enlaces de menús o listados; ninguna otra página la enlaza desde su texto",
  authority_sink: (d) =>
    `Acumula autoridad ${_dec(d.pagerank)} (la mediana del sitio es ${_dec(d.pagerank_p50)}) y no enlaza a nada desde su contenido.${d.template_fix ? ` ${d.template_fix}` : ""}`,
  equity_leak: (d) =>
    `Pierde el ${_pct(d.leak_ratio)} del peso de sus enlaces (${_dec(d.leaked_weight)} de ${_dec(d.total_weight)}): ${_n(d.leaked_edges)} enlaces rotos, redirigidos o nofollow`,
  hierarchy_imbalance: (d) =>
    `Solo el ${_pct(d.business_share)} de las páginas cercanas a la portada son de negocio (mínimo esperado: ${_pct(d.threshold)})`,
  deep_pagination: (d) => (d.chain ? `Cadena de paginación: ${_urls(d.chain, 3)}` : ""),
  crawl_trap_detected: (d) =>
    `Patrón «${d.pattern}» — se cortó tras ver ${_n(d.urls_seen)} URLs del mismo molde`,
  watchlist_check_failed: (d) =>
    `${d.label ? `«${d.label}» — ` : ""}${(d.reasons || []).map(_reason).join("; ")}`,
  in_sitemap_not_crawled: (d) => (d.lastmod ? `Declarada en el sitemap (lastmod ${String(d.lastmod).slice(0, 10)})` : "Declarada en el sitemap"),
  orphan_not_in_crawl: (d) => (d.lastmod ? `El sitemap la declara (lastmod ${String(d.lastmod).slice(0, 10)}) pero navegando no se llega` : "Conocida por sitemap/GSC pero sin camino navegando"),
  content_only_after_js: (d) =>
    `El ${_pct(d.js_content_ratio)} del contenido solo existe tras ejecutar JS (${_n(d.rendered_word_count)} palabras renderizadas vs ${_n(d.raw_word_count)} en el HTML crudo)`,
  no_inlinks_with_traffic: (d) =>
    `${_n(d.clicks)} clics de Google pero 0 enlaces internos que la apunten`,
  underlinked_high_performer: (d) =>
    `${_n(d.clicks)} clics (por encima del P75 del sitio) con PageRank ${_dec(d.pagerank)}, por debajo del P25 (${_dec(d.pagerank_p25)})`,
  image_missing_alt: (d) =>
    d.missing_alt_count != null ? `${_n(d.missing_alt_count)} de ${_n(d.total_images ?? d.missing_alt_count)} imágenes sin alt` : "",
  semantic_cannibalization: (d) =>
    `Compite con ${d.dominant_url || "otra página"} (similitud ${_dec(d.cosine_similarity)}). Esta es la débil: consolidar, redirigir o diferenciar`,
  passage_gap: (d) =>
    `«${d.query}» — ${_n(d.impressions)} impresiones y ${_n(d.clicks)} clics, pero el mejor pasaje del sitio solo llega a ${_dec(d.best_similarity)} de similitud`,
  buried_passage: (d) =>
    `«${d.query}» — el pasaje que la responde (similitud ${_dec(d.similarity)}) está enterrado en la posición ${_n(d.chunk_position)} de la página${d.heading_path ? ` (sección ${d.heading_path})` : ""}`,
  orphan_chunk: (d) =>
    `${_n(d.orphan_chunks)} de sus ${_n(d.total_chunks)} pasajes no responden a ninguna búsqueda medida${d.approximate ? " (estimación)" : ""}`,
  generic_anchor: (d) =>
    `${_n(d.generic_inlinks)} enlaces con anchors vacíos de significado: ${(d.anchors || []).map((a) => `«${a}»`).join(", ")}${d.sources_sample && d.sources_sample.length ? ` — desde ${_urls(d.sources_sample, 1)}` : ""}`,
  anchor_target_mismatch: (d) =>
    `El anchor «${d.anchor}» apenas guarda relación con esta página (similitud ${_dec(d.similarity)}, ${_n(d.n_links)} enlaces)${d.sources_sample && d.sources_sample.length ? ` — desde ${_urls(d.sources_sample, 1)}` : ""}`,
  entity_query_mismatch: (d) =>
    `Rankea para «${d.query}» (${_n(d.impressions)} imprs, pos ${_dec(d.position)}) pero la entidad «${d.entity}» no aparece en la página${d.entidades_presentes && d.entidades_presentes.length ? ` (habla de: ${d.entidades_presentes.join(", ")})` : ""}${d.prioridad != null ? `. Prioridad ${_n(Math.round(d.prioridad))}` : ""}`,
  entity_coverage_gap: (d) =>
    `Nadie cubre «${d.entity}» y hay demanda: ${_n(d.impressions)} impresiones en búsquedas como ${(d.queries || []).slice(0, 3).map((q) => `«${q}»`).join(", ")}`,
  entity_cannibalization: (d) =>
    `Misma entidad principal «${d.entity}» y misma fase ${d.funnel} que ${d.dominant_url} (${_n(d.n_urls)} páginas compiten)${d.converge_embeddings ? " · también salta por similitud de contenido" : ""}. Sugerencia: ${d.accion}`,
  funnel_mismatch: (d) =>
    `Página ${d.page_funnel} capturando ${_n(d.n_queries)} búsquedas ${d.query_funnel} (${_n(d.impressions)} imprs), como ${(d.queries || []).slice(0, 2).map((q) => `«${q}»`).join(", ")}`,
};

// Claves internas que jamás aportan nada al usuario (ids, hashes…).
const HIDDEN_KEYS = new Set([
  "body_hash", "duplicate_urls", "segment_id", "cluster_id", "method",
  "is_business", "sim_threshold", "mismatch_threshold", "orphan_threshold",
  "watch_url", "hint",
]);

const GENERIC_LABELS = {
  length: "longitud", min: "mínimo", max: "máximo", count: "nº",
  chain: "cadena", hops: "saltos", target: "destino",
  similarity: "similitud", threshold: "umbral", reason: "motivo",
  word_count: "palabras", urls: "URLs", lastmod: "lastmod",
  lang: "idioma", href: "href declarado", target_status: "estado del destino",
};

const _fmtVal = (v) => {
  if (v == null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? _n(v) : _dec(v);
  if (typeof v === "boolean") return v ? "sí" : "no";
  if (Array.isArray(v)) return _urls(v.map(String), 3);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
};

/** Frase legible para los details de una incidencia. Si el tipo tiene
 *  renderer propio se usa; si no, clave: valor traducido y sin ids. */
export const detailsToText = (type, details) => {
  if (!details || typeof details !== "object") return "";
  const renderer = DETAIL_RENDERERS[type];
  if (renderer) {
    try {
      const out = renderer(details);
      if (out) return out;
    } catch { /* cae al genérico */ }
  }
  return Object.entries(details)
    .filter(([k]) => !HIDDEN_KEYS.has(k))
    .map(([k, v]) => {
      if (k === "reason" || k === "reasons") {
        return Array.isArray(v) ? v.map(_reason).join("; ") : _reason(v);
      }
      return `${GENERIC_LABELS[k] || k.replace(/_/g, " ")}: ${_fmtVal(v)}`;
    })
    .join(" · ");
};

/** Pares [etiqueta, valor] legibles de un details, para la vista de
 *  detalle (no una frase apretada: campo a campo, sin ids ni hashes). */
export const detailPairs = (details) => {
  if (!details || typeof details !== "object") return [];
  return Object.entries(details)
    .filter(([k]) => !HIDDEN_KEYS.has(k))
    .map(([k, v]) => {
      const label = GENERIC_LABELS[k] || k.replace(/_/g, " ");
      if (k === "reason" || k === "reasons") {
        return [label, Array.isArray(v) ? v.map(_reason).join("; ") : _reason(v)];
      }
      if (Array.isArray(v)) return [label, v.map(String).join(", ")];
      return [label, _fmtVal(v)];
    });
};
