/** Catálogo de incidencias: qué significa cada tipo, en castellano claro.
 *  Se muestra en la vista Incidencias y como tooltip allá donde aparezca
 *  un issue_type. Mantener sincronizado con analysis/ (analyzer, T18/T19). */

export const ISSUE_INFO = {
  // ── Técnico ──────────────────────────────────────────────────────────
  status_4xx: "La página responde con error 4xx (no encontrada, prohibida…). Enlaces que apuntan aquí pierden autoridad y frustran al usuario.",
  status_5xx: "El servidor falla (error 5xx) al pedir esta página. Si Google la ve así repetidamente, la saca del índice.",
  redirect_chain: "Para llegar al destino final hay que atravesar varias redirecciones seguidas. Cada salto pierde autoridad y ralentiza el rastreo.",
  meta_refresh_redirect: "La página redirige con una etiqueta meta refresh en vez de una redirección HTTP. Es más lenta y peor interpretada por los buscadores.",
  js_redirect: "La página redirige mediante JavaScript. Los rastreadores que no ejecutan JS no la siguen.",
  canonical_chain: "El canonical apunta a una página cuyo canonical apunta a otra: cadena de canonicals. Google puede ignorarlos todos.",
  canonical_loop: "Dos o más páginas se declaran canónicas entre sí en círculo. Google no sabe cuál es la buena.",
  slow_page: "La página tarda más de lo aceptable en responder. Afecta al presupuesto de rastreo y a la experiencia.",
  soft_404: "La página devuelve 200 (OK) pero su contenido es una página de error. Google la trata como 404 y desperdicia rastreo.",
  http_url: "La página se sirve por HTTP sin cifrar en lugar de HTTPS.",
  mixed_content: "Página HTTPS que carga recursos (imágenes, scripts…) por HTTP. El navegador puede bloquearlos y marca la página como insegura.",
  missing_hsts: "Falta la cabecera HSTS, que obliga al navegador a usar siempre HTTPS.",
  missing_csp: "Falta la cabecera Content-Security-Policy, que limita qué recursos puede cargar la página (defensa ante inyecciones).",
  in_sitemap_not_crawled: "La URL está declarada en el sitemap pero el rastreo no llegó a ella siguiendo enlaces.",
  crawled_not_in_sitemap: "La URL existe y se rastreó, pero no está en el sitemap. El sitemap está incompleto.",
  orphan_not_in_crawl: "URL que el sitemap o Search Console conocen pero a la que NO se llega navegando por el sitio: huérfana real.",
  watchlist_check_failed: "Una página de negocio vigilada ha dejado de cumplir sus condiciones (responder 200, ser indexable y canonical a sí misma).",
  crawl_trap_detected: "El rastreador detectó un patrón de URLs infinitas (calendarios, filtros combinables…) y dejó de seguirlo. Google puede caer en la misma trampa.",
  stale_lastmod: "El lastmod del sitemap no se corresponde con la realidad: dice que la página cambió y el contenido es idéntico, o al revés. Resta credibilidad al sitemap.",

  // ── On-page ──────────────────────────────────────────────────────────
  missing_title: "La página no tiene etiqueta <title>. Es el texto del resultado en Google.",
  title_too_short: "El title es demasiado corto para describir la página y aprovechar el espacio en resultados.",
  title_too_long: "El title supera el ancho que Google muestra: se corta con puntos suspensivos.",
  duplicate_title: "Varias páginas comparten el mismo title. Compiten entre sí y ninguna destaca.",
  missing_description: "Falta la meta description. Google inventa el texto del resultado y suele hacerlo peor.",
  description_too_short: "La meta description es tan corta que desaprovecha el espacio del resultado.",
  description_too_long: "La meta description se corta en los resultados por exceso de longitud.",
  duplicate_description: "Varias páginas comparten la misma meta description.",
  missing_h1: "La página no tiene encabezado H1, el titular principal que estructura el contenido.",
  multiple_h1: "Hay más de un H1. Diluye la jerarquía del contenido.",
  image_missing_alt: "Imágenes sin texto alternativo (alt): invisibles para lectores de pantalla y para la búsqueda de imágenes.",
  low_word_count: "La página tiene muy poco texto total. Difícil que posicione por nada.",
  low_text_ratio: "Casi todo el peso de la página es código, no texto visible.",
  duplicate_content: "El contenido de esta página es idéntico al de otra (mismo hash de cuerpo).",
  near_duplicate_content: "El contenido es casi idéntico al de otra página (detección por similitud, no requiere que sean 100% iguales). Candidatas a fusionarse.",
  low_unique_content: "Descontando la plantilla que se repite en toda la sección (menús, footers, bloques legales), a esta página le queda muy poco texto propio.",

  // ── Enlazado / arquitectura ──────────────────────────────────────────
  orphan_page: "Ninguna página del rastreo enlaza a esta. Solo se llegó por sitemap o semilla: sin enlaces internos no recibe autoridad.",
  link_orphan: "No existe ningún camino de clics desde la portada hasta esta página (aunque técnicamente esté enlazada desde algún rincón).",
  excessive_click_depth: "Hacen falta demasiados clics desde la portada para llegar. Lo profundo se rastrea menos y posiciona peor.",
  no_contextual_inlinks: "Página de una sección de negocio que solo recibe enlaces de menús o listados, ninguno desde el contenido de otras páginas.",
  authority_sink: "Página con mucha autoridad acumulada que no enlaza a nada desde su contenido: la autoridad muere ahí en vez de repartirse.",
  deep_pagination: "Cadenas de paginación muy largas (página 4, 5, 6…). Lo que solo es alcanzable por paginación profunda apenas se rastrea.",
  hierarchy_imbalance: "La distribución de profundidades del sitio está desequilibrada: hay niveles saturados y saltos bruscos.",
  high_outlink_count: "La página tiene tantos enlaces salientes que cada uno transmite una fracción mínima de autoridad.",
  equity_leak: "Gran parte de la autoridad que sale de esta página se pierde en enlaces nofollow, rotos o redirigidos.",

  // ── URLs ─────────────────────────────────────────────────────────────
  url_too_long: "URL excesivamente larga: fea en resultados, difícil de compartir y de mantener.",
  url_non_ascii: "La URL contiene caracteres no ASCII (acentos, ñ…) que acaban codificados en porcentajes ilegibles.",
  url_uppercase: "La URL mezcla mayúsculas. Riesgo de duplicados (misma página con /Pagina y /pagina).",
  url_underscores: "La URL usa guiones bajos; Google recomienda guiones normales para separar palabras.",
  url_multiple_slashes: "La URL contiene barras dobles (//) en la ruta: suele delatar errores de generación de enlaces.",
  url_has_parameters: "URL con parámetros (?orden=, ?filtro=…). Multiplican variantes de la misma página.",
  url_non_seo_friendly: "URL poco legible (IDs, símbolos, sin palabras). No comunica de qué va la página.",
  url_cms_faceted: "URL de navegación facetada del CMS (combinaciones de filtros). Genera un número explosivo de páginas casi iguales.",

  // ── Semántica y cobertura (firmables: los revisa una persona) ────────
  semantic_cannibalization: "Dos páginas hablan de lo mismo (similitud semántica muy alta) y compiten por las mismas búsquedas. Decidir cuál manda y fusionar o diferenciar.",
  passage_gap: "Hay demanda real (query de Search Console con impresiones) pero ningún pasaje del sitio la responde. Contenido a crear o ampliar.",
  buried_passage: "El pasaje que responde a la query existe, pero está enterrado al fondo de su página. Subirlo o darle su propia sección.",
  orphan_chunk: "Bloques de contenido que no responden a ninguna búsqueda medida: texto que nadie está pidiendo.",
  generic_anchor: "Esta página recibe enlaces con textos genéricos («leer más», «aquí») que no dicen de qué va. Anchor descriptivo = señal semántica.",
  anchor_target_mismatch: "El texto del enlace promete una cosa y la página de destino habla de otra (similitud semántica baja). Confunde a usuarios y buscadores.",
};

/** Devuelve la descripción o un aviso honesto si el tipo no está catalogado. */
export const issueInfo = (type) =>
  ISSUE_INFO[type] || "Tipo de incidencia sin descripción en el catálogo (avisa para añadirla).";

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
