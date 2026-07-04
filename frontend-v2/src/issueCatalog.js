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

  // ── Enlazado y arquitectura ──────────────────────────────────────────
  orphan_page: ["Página sin enlaces internos", "Ninguna página del rastreo la enlaza; solo se llegó por sitemap o semilla. Sin enlaces internos no recibe autoridad."],
  link_orphan: ["Sin camino desde la portada", "No existe ninguna ruta de clics desde la home hasta esta página, aunque esté enlazada desde algún rincón."],
  excessive_click_depth: ["Demasiados clics de profundidad", "Hacen falta demasiados clics desde la portada para llegar. Lo profundo se rastrea menos y posiciona peor."],
  no_contextual_inlinks: ["Sin enlaces desde contenido", "Página de una sección de negocio que solo recibe enlaces de menús o listados, ninguno desde el texto de otras páginas."],
  authority_sink: ["Autoridad estancada", "Página con mucha autoridad acumulada que no enlaza a nada desde su contenido: la autoridad muere ahí en vez de repartirse."],
  deep_pagination: ["Paginación profunda", "Cadenas de paginación muy largas (página 4, 5, 6…). Lo que solo es alcanzable así apenas se rastrea."],
  hierarchy_imbalance: ["Arquitectura desequilibrada", "La distribución de profundidades del sitio está desequilibrada: niveles saturados y saltos bruscos."],
  high_outlink_count: ["Demasiados enlaces salientes", "La página tiene tantos enlaces que cada uno transmite una fracción mínima de autoridad."],
  equity_leak: ["Fuga de autoridad", "Gran parte de la autoridad que sale de esta página se pierde en enlaces nofollow, rotos o redirigidos."],

  // ── Calidad de las URLs ──────────────────────────────────────────────
  url_too_long: ["URL demasiado larga", "Fea en resultados, difícil de compartir y de mantener."],
  url_non_ascii: ["URL con caracteres raros", "Contiene acentos, eñes u otros caracteres que acaban codificados en porcentajes ilegibles."],
  url_uppercase: ["URL con mayúsculas", "Riesgo de duplicados: /Pagina y /pagina pueden ser la misma página dos veces."],
  url_underscores: ["URL con guiones bajos", "Google recomienda guiones normales para separar palabras."],
  url_multiple_slashes: ["URL con barras dobles", "Barras // en la ruta: suele delatar errores de generación de enlaces."],
  url_has_parameters: ["URL con parámetros", "Parámetros (?orden=, ?filtro=…) que multiplican variantes de la misma página."],
  url_non_seo_friendly: ["URL malformada", "URL rota o ilegible que un robot puede descubrir y rastrear. Si nuestro crawler la encontró, Google también puede."],
  url_cms_faceted: ["URL de filtros del CMS", "Navegación facetada (combinaciones de filtros) que genera un número explosivo de páginas casi iguales y quema presupuesto de rastreo."],

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
/* Formateo de la columna «Detalles» de las incidencias               */
/* ------------------------------------------------------------------ */

const DETAIL_LABELS = {
  length: "longitud", min: "mínimo", max: "máximo", value: "valor",
  count: "nº", pages: "páginas", urls: "URLs", url: "URL",
  duplicate_count: "duplicados", duplicates: "duplicados",
  chain: "cadena", hops: "saltos", target: "destino", source: "origen",
  similarity: "similitud", cosine_similarity: "similitud",
  best_similarity: "mejor similitud", sim_threshold: "umbral",
  mismatch_threshold: "umbral", orphan_threshold: "umbral",
  threshold: "umbral", query: "búsqueda", impressions: "impresiones",
  clicks: "clics", position: "posición", chunk_position: "posición del pasaje",
  heading_path: "sección", best_passage_url: "mejor pasaje en",
  word_count: "palabras", unique_word_count: "palabras propias",
  text_ratio: "% texto", ratio: "ratio", response_time_ms: "ms de respuesta",
  anchor: "anchor", n_links: "enlaces", sources_sample: "desde",
  generic_inlinks: "inlinks genéricos", anchors: "anchors",
  orphan_chunks: "pasajes sin demanda", total_chunks: "pasajes totales",
  positions: "posiciones", approximate: "aproximado",
  click_depth: "profundidad de clic", depth: "profundidad",
  pagerank: "autoridad", lost_ratio: "proporción perdida",
  lost_weight: "peso perdido", out_total: "peso saliente",
  outlinks: "enlaces salientes", inlinks: "enlaces entrantes",
  reasons: "motivos", label: "etiqueta", hint: "pista",
  pattern: "patrón", urls_seen: "URLs vistas", lastmod: "lastmod",
  body_changed: "contenido cambiado", images: "imágenes",
  missing_alt_count: "sin alt", total_images: "imágenes totales",
  dominant_url: "página dominante", weak_url: "página débil",
  langs: "idiomas", lang: "idioma", href: "URL", errors: "errores",
  schema_type: "tipo de schema", format: "formato",
};

const _fmtVal = (v) => {
  if (v == null) return "—";
  if (typeof v === "number") {
    return Number.isInteger(v) ? v.toLocaleString("es") : v.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (typeof v === "boolean") return v ? "sí" : "no";
  if (Array.isArray(v)) {
    const shown = v.slice(0, 3).map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x)));
    return shown.join(", ") + (v.length > 3 ? ` (+${v.length - 3})` : "");
  }
  if (typeof v === "object") return JSON.stringify(v);
  const s = String(v);
  return s.length > 90 ? s.slice(0, 90) + "…" : s;
};

/** Convierte el JSON de details en texto legible «clave: valor · …». */
export const detailsToText = (details) => {
  if (!details || typeof details !== "object") return "";
  return Object.entries(details)
    .map(([k, v]) => `${DETAIL_LABELS[k] || k.replace(/_/g, " ")}: ${_fmtVal(v)}`)
    .join(" · ");
};
