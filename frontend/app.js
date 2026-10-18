/* ===== SEO Crawler Frontend — Alpine.js App ===== */

const API = '/api';

/* ===== User-Agent presets ===== */
// Las cadenas de los bots son las que publica cada proveedor (comprobadas el
// 2026-10-16; decision 98). Google y Bing escriben `Chrome/W.X.Y.Z` y lo
// sustituyen por la version de Chromium/Edge que usan en cada momento: aqui va
// la estable de Chrome de esa fecha. Para robots.txt solo cuenta el TOKEN
// (`UA_TOKEN_ROBOTS`), que no cambia con la version.
const CHROME_EVERGREEN = '155.0.8059.84';
const UA_PRESETS = {
  chrome_win:  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  chrome_mac:  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  safari_mac:  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
  firefox_win: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
  edge_win:    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
  // developers.google.com/search/docs/crawling-indexing/google-common-crawlers
  googlebot_mobile: `Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${CHROME_EVERGREEN} Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)`,
  // La cadena antigua de escritorio, sin Chrome: se conserva para que los
  // configs guardados con ella sigan casando con un preset.
  googlebot:   'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
  // blogs.bing.com/webmaster/april-2022/Announcing-user-agent-change-for-Bing-crawler-bingbot
  bingbot:     `Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) Chrome/${CHROME_EVERGREEN} Safari/537.36`,
  // developers.openai.com/api/docs/bots
  gptbot:      'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.4; +https://openai.com/gptbot',
  oai_searchbot: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36; compatible; OAI-SearchBot/1.4; +https://openai.com/searchbot',
  // Anthropic publica el token (support.claude.com, articulo 8896518), no la
  // cadena entera: esta es la que se ve en los logs.
  claudebot:   'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)',
  // docs.perplexity.ai/guides/bots
  perplexitybot: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)',
};
// Los presets que llevan `CHROME_EVERGREEN` dentro. Al subir la constante, un
// cliente o un perfil guardado con la version anterior tiene que seguir
// reconociendose como ese preset —y relanzarse con la version de hoy, que es
// lo que hace el bot de verdad—, no caer a «Personalizado» con una cadena
// congelada. Es el mismo motivo por el que se conserva el `googlebot` antiguo.
const UA_PRESETS_EVERGREEN = ['googlebot_mobile', 'bingbot'];
const RE_VERSION_CHROME = /Chrome\/[0-9.]+/;
function presetDeUserAgent(ua) {
  if (!ua) return null;
  const exacto = Object.keys(UA_PRESETS).find(k => UA_PRESETS[k] === ua);
  if (exacto) return exacto;
  const sinVersion = s => s.replace(RE_VERSION_CHROME, 'Chrome/*');
  return UA_PRESETS_EVERGREEN.find(k => sinVersion(UA_PRESETS[k]) === sinVersion(ua)) || null;
}
const UA_LABELS = {
  chrome_win:  'Chrome - Windows',
  chrome_mac:  'Chrome - macOS',
  safari_mac:  'Safari - macOS',
  firefox_win: 'Firefox - Windows',
  edge_win:    'Edge - Windows',
  googlebot_mobile: 'Googlebot smartphone',
  googlebot:   'Googlebot escritorio (cadena antigua)',
  bingbot:     'Bingbot',
  gptbot:      'GPTBot (OpenAI, entrenamiento)',
  oai_searchbot: 'OAI-SearchBot (búsqueda de ChatGPT)',
  claudebot:   'ClaudeBot (Anthropic)',
  perplexitybot: 'PerplexityBot',
  custom:      'Personalizado',
};
// El token de producto con que robots.txt agrupa las reglas de cada bot. Es lo
// que casa el parser (Protego) dentro de la cadena entera; se usa para avisar
// en el formulario y en `test_ua_presets.py`, que lo comprueba con el
// middleware de robots de verdad.
const UA_TOKEN_ROBOTS = {
  googlebot_mobile: 'Googlebot',
  googlebot:   'Googlebot',
  bingbot:     'bingbot',
  gptbot:      'GPTBot',
  oai_searchbot: 'OAI-SearchBot',
  claudebot:   'ClaudeBot',
  perplexitybot: 'PerplexityBot',
};

// El token de bot que lleva un User-Agent (de preset o escrito a mano), o ''.
function tokenDeBot(ua) {
  const texto = (ua || '').toLowerCase();
  const tokens = [...new Set(Object.values(UA_TOKEN_ROBOTS))];
  return tokens.find(t => texto.includes(t.toLowerCase())) || '';
}

/* ===== Traducciones ===== */
const STATUS_LABEL = {
  pending:   'Pendiente',
  running:   'En curso',
  analyzing: 'Analizando',
  completed: 'Completado',
  failed:    'Fallido',
  cancelled: 'Cancelado',
  deleting:  'Borrándose',
};

const ISSUE_LABEL = {
  '4xx_error': 'Error 4xx',
  '5xx_error': 'Error 5xx',
  'connection_error': 'Error de conexión',
  'title_missing': 'Sin título',
  'title_too_short': 'Título muy corto',
  'title_too_long': 'Título muy largo',
  'title_duplicate': 'Título duplicado',
  'description_missing': 'Sin meta descripción',
  'description_too_short': 'Descripción muy corta',
  'description_too_long': 'Descripción muy larga',
  'description_duplicate': 'Descripción duplicada',
  'h1_missing': 'Sin H1',
  'h1_multiple': 'Múltiples H1',
  'h1_duplicate': 'H1 duplicado',
  'canonical_missing': 'Sin canonical',
  'canonical_cross_domain': 'Canonical externo',
  'canonical_broken': 'Canonical roto',
  'hreflang_missing_return': 'Hreflang sin retorno',
  'hreflang_invalid_lang': 'Hreflang idioma inválido',
  'hreflang_broken_target': 'Hreflang destino roto',
  'structured_data_error': 'Error datos estructurados',
  'structured_data_warning': 'Aviso datos estructurados',
  'structured_data_info': 'Resultado enriquecido retirado',
  'noindex_page': 'Página noindex',
  'robots_invalid_syntax': 'Meta robots mal escrito',
  'duplicate_content': 'Contenido duplicado',
  'near_duplicate_content': 'Contenido casi duplicado',
  'redirect_loop': 'Bucle de redirección',
  'redirect_chain': 'Cadena de redirecciones',
  'image_missing_alt': 'Imagen sin alt',
  'http_url': 'URL sin HTTPS',
  'mixed_content': 'Contenido mixto',
  'missing_hsts': 'Sin cabecera HSTS',
  'missing_csp': 'Sin política CSP',
  'missing_x_content_type_options': 'Sin X-Content-Type-Options',
  'missing_x_frame_options': 'Sin X-Frame-Options',
  'unsafe_crossorigin': 'Crossorigin inseguro',
  'low_word_count': 'Pocas palabras',
  'soft_404': 'Soft 404',
  'soft_404_candidato': 'Posible soft 404',
  'very_low_text_ratio': 'Ratio texto muy bajo',
  'low_text_ratio': 'Ratio texto bajo',
  'url_too_long': 'URL muy larga',
  'url_non_ascii': 'URL con caracteres no ASCII',
  'url_uppercase': 'URL con mayúsculas',
  'url_underscores': 'URL con guiones bajos',
  'url_multiple_slashes': 'URL con barras múltiples',
  'url_has_parameters': 'URL con parámetros',
  'url_non_seo_friendly': 'URL no SEO-friendly',
  'url_cms_faceted': 'URL de filtro/CMS (crawl budget)',
  'orphan_page': 'Página huérfana',
  'high_outlink_count': 'Demasiados enlaces salientes',
  'sitemap_orphan': 'Huérfana (solo en sitemap)',
  'solo_enlazada_por_redireccion': 'Solo se llega por una redirección',
  'not_reachable_from_home': 'Sin camino de enlaces desde la home',
  'not_in_sitemap': 'No incluida en el sitemap',
  'sitemap_no_indexable': 'En el sitemap sin ser indexable',
};

const SEVERITY_LABEL = { error: 'Error', warning: 'Aviso', info: 'Info' };

const POSITION_LABEL = {
  nav: 'Navegación', footer: 'Pie de página', content: 'Contenido',
  header: 'Cabecera', sidebar: 'Lateral',
};

// Pestanas de la ficha <-> tramo de la URL (#/rastreos/<id>/<tramo>)
const TAB_SLUG = {
  overview: 'resumen', issues: 'problemas', links: 'enlaces', insights: 'informe',
  semantic: 'semantico', diff: 'comparar', enriquecido: 'enriquecido', cleaning: 'limpieza',
};
const SLUG_TAB = Object.fromEntries(Object.entries(TAB_SLUG).map(([k, v]) => [v, k]));

// Helpers
const fmt = {
  // `useGrouping: 'always'`: en es-ES Intl no agrupa los numeros de cuatro
  // cifras, asi que la misma columna mezclaba `4319` con `15.026` (UX-14).
  num: n => n == null ? '—' : Number(n).toLocaleString('es-ES', { useGrouping: 'always' }),
  pct: n => n == null ? '—' : n.toFixed(1) + '%',
  // Tiempos de respuesta (milisegundos de una peticion).
  ms:  n => n == null ? '—' : n < 1000 ? Math.round(n) + ' ms' : (n/1000).toFixed(1).replace('.', ',') + ' s',
  // Lo que dura un rastreo. Antes salia por `ms` y un censo se leia
  // `14787.1s`: hay que hacer la division de cabeza para saber que son 4 h.
  lapso: ms => {
    if (ms == null || isNaN(ms) || ms < 0) return '—';
    const s = Math.round(ms / 1000);
    if (s < 60) return s + ' s';
    const m = Math.floor(s / 60);
    if (m < 60) return m + ' min' + (s % 60 ? ' ' + String(s % 60).padStart(2, '0') + ' s' : '');
    const h = Math.floor(m / 60);
    if (h < 24) return h + ' h' + (m % 60 ? ' ' + String(m % 60).padStart(2, '0') + ' min' : '');
    return Math.floor(h / 24) + ' d ' + (h % 24) + ' h';
  },
  date: d => d ? new Date(d).toLocaleString('es-ES', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—',
  dia: d => d ? new Date(d).toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric' }) : '—',
  ago: d => {
    if (!d) return '—';
    const s = Math.floor((Date.now() - new Date(d).getTime()) / 1000);
    if (s < 0)     return 'ahora';
    if (s < 60)    return 'hace ' + s + ' s';
    if (s < 3600)  return 'hace ' + Math.floor(s/60) + ' min';
    if (s < 86400) return 'hace ' + Math.floor(s/3600) + ' h';
    const dias = Math.floor(s/86400);
    if (dias === 1) return 'ayer';
    if (dias < 30) return 'hace ' + dias + ' días';
    return fmt.dia(d);
  },
  // Una expresion cron en castellano. Solo los patrones que se usan de
  // verdad (diario, semanal, quincenal, mensual, cada N horas); lo demas se
  // devuelve tal cual, que es mejor que una traduccion inventada.
  cron: expr => {
    if (!expr) return '—';
    const p = expr.trim().split(/\s+/);
    if (p.length !== 5) return expr;
    const [mi, h, dm, mes, ds] = p;
    const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
    const num = x => /^\d+$/.test(x);
    if (!num(mi)) return expr;
    if (/^\*\/\d+$/.test(h) && dm === '*' && mes === '*' && ds === '*')
      return 'cada ' + h.slice(2) + ' h, en el minuto ' + mi;
    if (!num(h)) return expr;
    const hora = h.padStart(2, '0') + ':' + mi.padStart(2, '0');
    if (mes !== '*') return expr;
    if (dm === '*' && ds === '*') return 'cada día a las ' + hora;
    if (dm === '*' && /^[0-7](,[0-7])*$/.test(ds)) {
      const ds_ = ds.split(',').map(d => DIAS[+d]);
      return (ds_.length === 1 ? 'cada ' : '') + ds_.join(', ') + ' a las ' + hora;
    }
    if (dm === '*' && ds === '1-5') return 'de lunes a viernes a las ' + hora;
    if (ds === '*' && /^\d+(,\d+)*$/.test(dm))
      return 'el día ' + dm.split(',').join(' y ') + ' de cada mes a las ' + hora;
    return expr;
  },
  // Se corta en MB para el tamano de una pagina, pero un censo son cientos
  // de MB y la base entera 57 GB: con el tope en MB se leia «57344.0 MB», y
  // esa division la tenia que hacer de cabeza quien mira el panel.
  bytes: b => {
    if (b == null) return '—';
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
    if (b < 1073741824) return (b/1048576).toFixed(1) + ' MB';
    if (b < 1099511627776) return (b/1073741824).toFixed(1).replace('.', ',') + ' GB';
    return (b/1099511627776).toFixed(1).replace('.', ',') + ' TB';
  },
  status: s => STATUS_LABEL[s] || s,
  issue: t => ISSUE_LABEL[t] || ISSUE_LABEL_EXTRA[t] || t.replaceAll('_', ' '),
  // `issues.details` en castellano, para la tabla de Problemas. Antes se
  // pintaba el JSON tal cual («status_code: 404», «duplicate_urls: 276074»).
  detalles: d => {
    if (!d || typeof d !== 'object') return '';
    const partes = [];
    const corta = u => String(u).replace(/^https?:\/\/[^/]+/, '') || '/';
    for (const [k, v] of Object.entries(d)) {
      if (v == null || v === '' || k === 'otro_dominio_registrable' || k === 'paginas_del_host') continue;
      if (k === 'validation_issues' && Array.isArray(v)) {
        partes.push(v[0] + (v.length > 1 ? ` (y ${v.length - 1} más)` : ''));
      } else if (k === 'duplicate_urls' && Array.isArray(v)) {
        if (d.duplicate_count == null) partes.push(`${fmt.num(v.length + 1)} páginas en el grupo`);
      } else if (k === 'matches' && Array.isArray(v)) {
        if (v.length) partes.push('se parece a ' + corta(v[0].url || v[0]) + (v.length > 1 ? ` y ${v.length - 1} más` : ''));
      } else if (k === 'chain' && Array.isArray(v)) {
        partes.push(`${v.length - 1} saltos`);
      } else if (k === 'closest_similarity' || k === 'similarity') {
        partes.push(`similitud ${Math.round(v * 100)} %`);
      } else if (k === 'threshold') {
        continue;
      } else if (k === 'alcance') {
        partes.push(v === 'plantilla' ? 'de plantilla' : 'en esta página');
      } else if (k === 'tipo' && TIPO_NO_RASTREABLE[v]) {
        partes.push(TIPO_NO_RASTREABLE[v]);
      } else if (k === 'por_tipo' && typeof v === 'object') {
        partes.push(Object.entries(v).map(([t, n]) => `${TIPO_NO_RASTREABLE[t] || t} ${fmt.num(n)}`).join(', '));
      } else if (k === 'destino_rastreado' || k === 'destino_indexable') {
        if (v === false) partes.push(k === 'destino_rastreado' ? 'destino sin rastrear' : 'destino no indexable');
        else if (v === true && k === 'destino_indexable') partes.push('destino indexable');
      } else if (['ejemplos', 'paginas_ejemplo', 'ejemplos_origen'].includes(k) && Array.isArray(v)) {
        const e = v[0];
        if (e == null) continue;
        const txt = typeof e === 'string' ? corta(e)
          : e.url ? corta(e.url) + (e.status_code ? ` (${e.status_code})` : '')
          : (e.destino ? corta(e.destino) : (e.href || ''));
        if (txt) partes.push('p. ej. ' + (txt.length > 70 ? txt.slice(0, 70) + '…' : txt));
      } else if (['canonical', 'href', 'image_url', 'target', 'redirect_url', 'destino', 'href_ejemplo', 'destino_ejemplo'].includes(k)) {
        partes.push((DETALLE_LABEL[k] || k) + ' ' + (String(v).length > 70 ? String(v).slice(0, 70) + '…' : v));
      } else if (k === 'canonical_host') {
        if (!d.canonical) partes.push('a ' + v);
      } else if (k === 'target_status') {
        partes.push(`el destino responde ${v}`);
      } else if (k === 'clase') {
        partes.push(fmt.claseRespuesta(v));
      } else if (k === 'codigos' && typeof v === 'object' && !Array.isArray(v)) {
        // respuesta_no_valida: lo que distingue un 429 de un 403 (decision 103)
        const cods = Object.entries(v).map(([c, n]) => `${c === 'null' ? 'sin código' : c} × ${fmt.num(n)}`);
        if (cods.length) partes.push('códigos ' + cods.join(', '));
      } else if (Array.isArray(v)) {
        partes.push(`${DETALLE_LABEL[k] || k}: ${v.length}`);
      } else if (typeof v === 'object') {
        continue;
      } else if (typeof v === 'boolean') {
        if (v) partes.push(DETALLE_LABEL[k] || k);
      } else {
        partes.push(`${DETALLE_LABEL[k] || k} ${typeof v === 'number' ? fmt.num(v) : v}`);
      }
    }
    return partes.join(' · ');
  },
  // Analisis enriquecido (#40)
  usd: n => n == null ? '—' : (n === 0 ? '0 $' : (n < 0.01 ? Number(n).toFixed(5) : Number(n).toFixed(2)) + ' $'),
  duracion: s => s == null ? '—' : s < 60 ? s + ' s' : s < 3600 ? Math.ceil(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h',
  estadoEnriq: e => ({ pendiente: 'En cola', en_curso: 'En curso', cancelando: 'Cancelando…',
    completada: 'Completada', parcial: 'Parcial (presupuesto)', cancelada: 'Cancelada', fallida: 'Fallida' })[e] || e,
  colorEnriq: e => ({ completada: 'var(--green)', parcial: 'var(--orange)', fallida: 'var(--red)',
    cancelada: 'var(--text-dim)', cancelando: 'var(--orange)' })[e] || 'var(--accent)',
  motivoEnriq: m => ({ presupuesto_ejecucion: 'se agotó el presupuesto de la ejecución',
    presupuesto_cliente: 'se agotó el tope mensual del cliente', presupuesto_global: 'se agotó el tope mensual de la instancia',
    presupuesto_sin_contador: 'sin contador de gasto no se puede garantizar el tope', max_llamadas: 'tope de llamadas',
    sin_credito: 'sin crédito en OpenRouter', clave_invalida: 'clave de OpenRouter inválida',
    desactivado: 'capa desactivada en el worker', cancelada: 'cancelada a mano' })[m] || m,
  alcanceEnriq: a => {
    if (!a) return 'todas las indexables';
    const partes = [];
    if (a.carpeta) partes.push('carpeta ' + a.carpeta);
    if (a.muestra) partes.push('muestra de ' + a.max_urls);
    else if (a.max_urls) partes.push('máx. ' + a.max_urls + ' URLs');
    return partes.join(', ') || 'todas las indexables';
  },
  respuestaEnriq: r => r === 'true' ? 'sí' : r === 'false' ? 'no' : r,
  campoDiff: c => ({
    status_code: 'Código de respuesta',
    indexability_status: 'Indexabilidad',
    canonical_href: 'Canonical',
    title: 'Título',
    h1: 'H1',
    word_count: 'Palabras (cambio de mas del 20%)',
  }[c] || c),
  // Si lo que llego era la pagina o un muro delante (decision 103).
  claseRespuesta: c => ({
    contenido: 'La página',
    challenge_waf: 'Desafío de un WAF',
    captcha: 'Captcha',
    rate_limited: 'Límite de peticiones (429)',
    respuesta_binaria: 'Cuerpo binario ilegible',
    login_requerido: 'Pide iniciar sesión',
    desconocido: 'Cuerpo vacío',
  }[c] || (c ? c : '—')),
  // El titular de cada alerta, en castellano llano: quien abre esto quiere
  // saber que ha pasado, no el nombre de la regla.
  reglaDiff: r => ({
    canonical_a_otro_host: 'El canonical se ha ido a otro host',
    canonical_a_otra_url: 'Páginas canonicalizadas a otra URL',
    rotas: 'Páginas que ahora responden con error',
    salen_del_indice: 'Páginas que han salido del índice',
    redirigidas: 'Páginas que ahora redirigen',
    pierden_contenido: 'Páginas que han perdido texto',
    cambia_el_titulo: 'Páginas que han cambiado de título',
    desaparecen: 'Páginas que ya no se encuentran en el sitio',
    entran_en_el_indice: 'Páginas que han entrado en el índice',
  }[r] || r),
  // El color de una alerta sale de SU severidad. La tarjeta de la ficha
  // llevaba el rojo fijo, asi que un aviso se pintaba como una critica: si
  // todo es rojo, el rojo no significa nada.
  colorSeveridad: s => ({
    critical: 'var(--red,#d32f2f)',
    warning: 'var(--orange,#e67e22)',
    info: 'var(--blue,#2979b5)',
  }[s] || 'var(--muted,#6b7280)'),
  severity: s => SEVERITY_LABEL[s] || s,
  position: p => POSITION_LABEL[p] || p || '—',
};

// El `detail` de un error de validacion de FastAPI (422) es una LISTA de
// objetos, no una cadena: `new Error(lista)` daba literalmente
// "[object Object]". Le pasaba a cualquier error de validacion de toda la
// aplicacion, asi que el mensaje que el servidor se molesta en redactar
// —«'todos los lunes' no es una expresion cron valida…»— no llegaba nunca a
// quien lo necesitaba.
function mensajeDeError(err) {
  const d = err && err.detail;
  if (!d) return 'Error en la peticion';
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map(e => {
      // `loc` es ['body', 'cron']: interesa el campo, no que venga del cuerpo.
      const campo = (e.loc || []).filter(x => x !== 'body').join('.');
      const texto = (e.msg || '').replace(/^Value error,\s*/, '');
      return campo ? `${campo}: ${texto}` : texto;
    }).join(' · ');
  }
  return typeof d === 'object' ? JSON.stringify(d) : String(d);
}


async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(mensajeDeError(err));
  }
  if (res.status === 204) return null;
  return res.json();
}


/* ===== Main App ===== */
function app() {
  return {
    // Navegacion
    view: 'jobs',
    programaciones: [],
    programarAbierto: false,
    programarNombre: '',
    programarCron: '0 3 * * 1',
    programarZona: 'Europe/Madrid',
    programarError: '',
    cadencias: [
      { etiqueta: 'Cada dia',      cron: '0 3 * * *' },
      { etiqueta: 'Cada lunes',    cron: '0 3 * * 1' },
      { etiqueta: 'Cada 15 dias',  cron: '0 3 1,15 * *' },
      { etiqueta: 'Cada mes',      cron: '0 3 1 * *' },
    ],
    theme: document.documentElement.dataset.theme || 'light',
    menuAbierto: '',
    dialogo: null,            // { titulo, mensaje, aceptar, peligro, escribir, entrada, solo_aviso }
    editarJob: null,          // { name, tipo, cliente_id } mientras el dialogo esta abierto
    editarError: '',          // '' | 'app' | 'exportar' | 'mas'
    urlDetailId: null,
    urlDetailDesde: 'detail', // a donde vuelve la ficha de URL: 'detail' | 'explorer'
    _aplicandoRuta: false,
    loading: false,
    error: null,

    // Toast notifications
    toasts: [],

    // Lista de trabajos
    jobs: [],
    jobsPage: 1,
    jobsPages: 1,
    jobsTotal: 0,
    jobsFilter: '',
    jobsBuscar: '',
    jobsCliente: '',          // id de cliente | 'ninguno' | ''
    jobsTipo: '',

    // Borrado en masa (decision 96). La seleccion se guarda por id y NO se
    // limpia al cambiar de pagina o de filtro: la limpieza tipica es «todas
    // las pruebas viejas», que no caben en una pagina de 20. Lo que evita
    // borrar a ciegas no es olvidar la seleccion, es que el dialogo liste
    // nombre a nombre lo que se va.
    jobsSel: {},
    borrado: null,            // {paso, previa, resultado, escribir, forzar, vueltas}
    almacenamiento: null,
    verAlmacenamiento: false,

    // Clientes (#36)
    clientes: [],
    panelClientes: null,
    clienteSerie: [],
    serieSoloCensos: true,
    cliente: null,
    clienteTab: 'rastreos',
    clienteForm: _formCliente(null),
    clienteError: '',
    clientePerfiles: [],
    clienteJobs: [],
    perfilForm: null,
    perfilError: '',

    // Modal de creacion
    showCreate: false,
    createPerfiles: [],
    createForm: _freshForm(),
    creating: false,
    createError: null,

    // Detalle del trabajo
    job: null,
    detailTab: 'overview',
    stats: null,
    progressTimer: null,
    progress: null,

    // Pestana Informe SEO
    insights: null,
    insightsLoading: false,
    expandedCategories: {},
    recUrls: {},      // { "rec-title": { loading, urls, loaded } }
    recUrlsOpen: {},  // { "rec-title": bool }

    // Pestana URLs
    urls: [], urlsPage: 1, urlsPages: 1, urlsTotal: 0,
    urlFilters: { status_group: '', is_internal: '', resource_type: '' },

    // Pestana Problemas
    issues: [], issuesPage: 1, issuesPages: 1, issuesTotal: 0,
    issuesResumen: null,      // una fila por tipo (/issues/resumen)
    issuesSegmento: '',       // segmento elegido en Problemas (decision 125)
    issuesVista: 'tipos',     // 'tipos' | 'lista'
    grupoAbierto: {},
    ISSUE_INFO,
    issueFilters: { severity: '', issue_type: '' },

    // Pestana Enlaces
    links: [], linksPage: 1, linksPages: 1, linksTotal: 0,

    // Pestana Semantico
    semanticResults: null,
    semanticLoading: false,
    semanticError: null,
    semanticSubTab: 'rings',
    semanticProgress: null,
    semanticPollTimer: null,
    semanticAlpha: 0.6,
    semanticBeta: 0.4,
    semanticThreshold: 0.92,
    gscAccounts: [],
    geminiAccounts: [],
    semanticGeminiAccountId: '',
    showGeminiModal: false,
    geminiFormName: '',
    geminiFormKey: '',
    geminiFormError: null,
    semanticGscAccountId: '',
    semanticGscProps: [],
    semanticGscProp: '',
    semanticGscDays: '90',
    gscPropsLoading: false,
    gscFetchLoading: false,
    gscFetchResult: null,
    showGscUploadModal: false,
    gscUploadName: '',
    gscUploadFile: null,
    cannibalPairs: [],
    cannibalLoading: false,
    cannibalBrand: '',
    cannibalHasQueryData: false,
    cannibalFilter: 'all',
    driftData: [],
    driftLoading: false,
    gapTopic: '',
    gapExclude: '',
    gapResults: null,
    gapLoading: false,
    targetTheme: '',
    targetResults: null,
    targetLoading: false,

    // Pestana Limpieza
    cleaningGroups: [],
    cleaningGroupsLoading: false,
    cleaningSelectedGroup: null,
    cleaningRules: [{ type: 'line_exact', value: '' }],
    cleaningTargets: 'both',
    cleaningUrlRegex: '',
    cleaningPreview: null,
    cleaningPreviewLoading: false,
    cleaningApplying: false,
    cleaningReverting: false,
    cleaningRulesets: [],
    cleaningError: null,
    cleaningMessage: null,

    // Vista detalle de URL
    urlDetail: null,
    urlDetailTab: 'general',
    urlDetailLoading: false,
    contentViewMode: 'markdown',

    // ------- Init -------
    init() {
      // La ruta manda (UX-03): un enlace pegado, una recarga o el boton atras
      // llevan a la misma vista. Sin hash, la lista de rastreos.
      // Sin hash, el panel de clientes (UX-07): la pregunta del dia a dia es
      // «como estan mis clientes», no «cual fue el ultimo rastreo».
      const inicial = location.hash && location.hash !== '#/' ? location.hash : '#/clientes';
      history.replaceState(null, '', inicial);
      this._irARuta(inicial);
      window.addEventListener('popstate', () => this._irARuta(location.hash));
      // Cualquier cambio de vista, rastreo, pestana o filtro se refleja en la
      // URL. Un solo vigilante en vez de acordarse en cada metodo.
      // Si solo cambia lo que va despues del «?» (un filtro, el orden, la
      // pagina) se sustituye la entrada: cada tecla en un filtro no puede ser
      // un paso del boton atras.
      this.$watch('_ruta()', h => {
        if (!h || location.hash === h) return;
        const mismaVista = location.hash.split('?')[0] === h.split('?')[0];
        if (this._aplicandoRuta || mismaVista) history.replaceState(null, '', h);
        else history.pushState(null, '', h);
      });

      // Keyboard shortcut: 'n' to open create modal
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.menuAbierto) { this.menuAbierto = ''; return; }
        if (e.key === 'n' && !this.showCreate && !['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) {
          this.openCreate();
        }
      });

      this.$nextTick(() => lucide.createIcons());
      this._vigilarIconos();
    },

    // Lucide convierte los <i data-lucide> que hay en el DOM CUANDO se le
    // llama. Todo lo que Alpine monta despues (un x-if, un x-for, una vista
    // nueva) se quedaba con el hueco del icono salvo que ese metodo concreto
    // se acordase de llamarle: asi salian vacios los botones de la ficha de
    // cliente (UX-15). Un observador lo hace para todos, una vez por frame.
    _vigilarIconos() {
      if (!window.MutationObserver || !window.lucide) return;
      let pendiente = false;
      const pintar = () => { pendiente = false; lucide.createIcons(); };
      new MutationObserver(muts => {
        if (pendiente) return;
        for (const m of muts) {
          for (const n of m.addedNodes) {
            if (n.nodeType === 1 && (n.matches('i[data-lucide]') || n.querySelector('i[data-lucide]'))) {
              pendiente = true;
              requestAnimationFrame(pintar);
              return;
            }
          }
        }
      }).observe(document.body, { childList: true, subtree: true });
    },

    // ------- Rutas (UX-03) -------
    // #/rastreos                       lista de rastreos (?estado=)
    // #/rastreos/<id>[/<pestana>]      ficha; pestanas en castellano (TAB_SLUG)
    // #/rastreos/<id>/problemas?tipo=&gravedad=
    // #/rastreos/<id>/explorador
    // #/rastreos/<id>/url/<url_id>
    // #/clientes · #/clientes/<id> · #/clientes/nuevo · #/programados
    _ruta() {
      const v = this.view;
      if (v === 'jobs') {
        const q = new URLSearchParams();
        if (this.jobsBuscar.trim()) q.set('buscar', this.jobsBuscar.trim());
        if (this.jobsCliente) q.set('cliente', this.jobsCliente);
        if (this.jobsTipo) q.set('tipo', this.jobsTipo);
        if (this.jobsFilter) q.set('estado', this.jobsFilter);
        if (this.jobsPage > 1) q.set('pagina', this.jobsPage);
        return '#/rastreos' + (q.toString() ? '?' + q : '');
      }
      if (v === 'clients') return '#/clientes';
      if (v === 'client') return this.cliente ? '#/clientes/' + this.cliente.id : '#/clientes/nuevo';
      if (v === 'programados') return '#/programados';
      if (!this.job) return '';
      const base = '#/rastreos/' + this.job.id;
      if (v === 'explorer') {
        const q = this._estadoExplorador();
        return base + '/explorador' + (q ? '?' + q : '');
      }
      if (v === 'url-detail') return this.urlDetailId ? base + '/url/' + this.urlDetailId : base;
      if (v !== 'detail') return '';
      const t = this.detailTab;
      if (t === 'overview') return base;
      let h = base + '/' + (TAB_SLUG[t] || t);
      if (t === 'issues') {
        const q = new URLSearchParams();
        if (this.issueFilters.issue_type) q.set('tipo', this.issueFilters.issue_type);
        if (this.issueFilters.severity) q.set('gravedad', this.issueFilters.severity);
        if (this.issuesVista === 'lista' && !this.issueFilters.issue_type) q.set('vista', 'lista');
        if (q.toString()) h += '?' + q.toString();
      }
      if (t === 'diff' && this.diffOtro && this.diffOtro !== (this.job.comparacion || {}).comparado_con) {
        h += '?con=' + this.diffOtro;
      }
      return h;
    },

    async _irARuta(hash) {
      const [ruta, qs] = (hash || '').replace(/^#/, '').split('?');
      const q = new URLSearchParams(qs || '');
      const p = ruta.split('/').filter(Boolean);
      this._aplicandoRuta = true;
      try {
        if (!p.length) {
          p.push('clientes');
          history.replaceState(null, '', '#/clientes');
        }
        if (p[0] === 'rastreos' && p.length === 1) {
          const f = { estado: q.get('estado') || '', buscar: q.get('buscar') || '',
                      cliente: q.get('cliente') || '', tipo: q.get('tipo') || '', pagina: +(q.get('pagina') || 1) };
          const igual = this.jobsFilter === f.estado && this.jobsBuscar === f.buscar
            && this.jobsCliente === f.cliente && this.jobsTipo === f.tipo && this.jobsPage === f.pagina;
          if (this.view !== 'jobs' || !igual || !this.jobs.length) {
            this._salirDeJob();
            this.view = 'jobs';
            this.jobsFilter = f.estado; this.jobsBuscar = f.buscar;
            this.jobsCliente = f.cliente; this.jobsTipo = f.tipo; this.jobsPage = f.pagina;
            await this.loadJobs();
          }
        } else if (p[0] === 'clientes') {
          if (!p[1]) await this.verClientes();
          else if (p[1] === 'nuevo') this.nuevoCliente();
          else if (!this.cliente || this.cliente.id !== p[1] || this.view !== 'client') await this.abrirCliente(p[1]);
        } else if (p[0] === 'programados') {
          await this.verProgramados();
        } else if (p[0] === 'rastreos') {
          if (!this.job || this.job.id !== p[1]) await this.openJob(p[1]);
          if (!this.job) return;
          const sub = p[2];
          if (sub === 'explorador') {
            const pre = this._preajusteExplorador(q);
            if (this.view !== 'explorer' || this._estadoExplorador() !== new URLSearchParams(qs || '').toString()) {
              this.openExplorer(pre);
            }
          } else if (sub === 'url' && p[3]) {
            if (this.view !== 'url-detail' || String(this.urlDetailId) !== p[3]) await this.openUrlDetail(+p[3]);
          } else {
            if (this.view === 'explorer') this._unbindExplorerKeyNav();
            this.view = 'detail';
            const tab = SLUG_TAB[sub] || 'overview';
            if (tab === 'issues') {
              const tipo = q.get('tipo') || '', grav = q.get('gravedad') || '';
              const vista = tipo || q.get('vista') === 'lista' ? 'lista' : 'tipos';
              if (tipo !== this.issueFilters.issue_type || grav !== this.issueFilters.severity || vista !== this.issuesVista) {
                this.issueFilters.issue_type = tipo;
                this.issueFilters.severity = grav;
                this.issuesVista = vista;
                this.issues = [];
                this.issuesPage = 1;
              }
            }
            if (tab === 'diff' && q.get('con') && q.get('con') !== this.diffOtro) {
              this.diffOtro = q.get('con');
              this.cargarDiff();
            }
            await this.switchTab(tab);
          }
        } else {
          history.replaceState(null, '', '#/rastreos');
          await this._irARuta('#/rastreos');
        }
      } finally {
        // El vigilante de la ruta corre en un microtask: se suelta el
        // indicador despues, para que lo que dispare esta navegacion
        // reemplace la entrada del historial en vez de apilar otra.
        await this.$nextTick();
        this._aplicandoRuta = false;
      }
    },

    // La vista del Explorador en la URL (pestaña, busqueda, problema, orden,
    // pagina y filtros): «mira esto» con un enlace.
    _estadoExplorador() {
      const q = new URLSearchParams();
      if (this.expTab && this.expTab !== 'all') q.set('pestana', this.expTab);
      if (this.expSearch) q.set('buscar', this.expSearch);
      if (this.expProblema) q.set('problema', this.expProblema);
      if (this.expGravedad) q.set('gravedad', this.expGravedad);
      if (this.expSort) { q.set('orden', this.expSort); q.set('dir', this.expSortDir); }
      if (this.expPage > 1) q.set('pagina', this.expPage);
      if (this.expAgrupar) q.set('agrupar', this.expAgruparPor);
      if (this.expPlantilla) q.set('plantilla', this.expPlantilla);
      if (this.expSegmento) q.set('segmento', this.expSegmento);
      const f = Object.fromEntries(Object.entries(this.expColumnFilters).filter(([, v]) =>
        v && (v.op === 'vacio' || v.op === 'con_valor' || (v.value ?? '').toString().trim() !== '')));
      if (Object.keys(f).length) q.set('filtros', JSON.stringify(f));
      return q.toString();
    },

    _preajusteExplorador(q) {
      let filtros = {};
      try { filtros = JSON.parse(q.get('filtros') || '{}') || {}; } catch (_) { filtros = {}; }
      return {
        tab: q.get('pestana') || 'all', buscar: q.get('buscar') || '',
        problema: q.get('problema') || '', gravedad: q.get('gravedad') || '',
        orden: q.get('orden') || null, dir: q.get('dir') === 'desc' ? 'desc' : 'asc',
        pagina: +(q.get('pagina') || 1), filtros,
        agrupar: ['plantilla', 'segmento'].includes(q.get('agrupar')),
        agruparPor: q.get('agrupar') === 'segmento' ? 'segmento' : 'plantilla',
        plantilla: q.get('plantilla') || '', segmento: q.get('segmento') || '',
      };
    },

    // Seccion de la barra superior que se marca como activa.
    seccion() {
      if (['clients', 'client'].includes(this.view)) return 'clientes';
      if (this.view === 'programados') return 'programados';
      return 'rastreos';
    },

    // Miga de pan: donde estoy y por donde vuelvo.
    migas() {
      const m = [];
      const v = this.view;
      if (v === 'client') {
        m.push({ texto: 'Clientes', href: '#/clientes' });
        m.push({ texto: this.cliente ? this.cliente.nombre : 'Nuevo cliente' });
      }
      if (['detail', 'explorer', 'url-detail'].includes(v) && this.job) {
        m.push({ texto: 'Rastreos', href: '#/rastreos' });
        if (this.job.cliente_id) m.push({ texto: this.job.cliente_nombre || this.job.client_id, href: '#/clientes/' + this.job.cliente_id });
        m.push({ texto: this.job.name, href: '#/rastreos/' + this.job.id });
        if (v === 'explorer') m.push({ texto: 'Explorador' });
        if (v === 'url-detail') {
          if (this.urlDetailDesde === 'explorer') m.push({ texto: 'Explorador', href: '#/rastreos/' + this.job.id + '/explorador' });
          const u = this.urlDetail && this.urlDetail.url;
          m.push({ texto: u ? u.replace(/^https?:\/\/[^/]+/, '') || '/' : 'URL' });
        }
      }
      return m;
    },

    // Lo que hay que parar al dejar la ficha de un rastreo.
    _salirDeJob() {
      this._stopProgress();
      this._stopSemanticPoll();
      this._pararEnriq();
      if (this.view === 'explorer') this._unbindExplorerKeyNav();
      this.job = null;
    },

    // ------- Tema (terminal / claro) -------
    toggleTheme() {
      this.theme = this.theme === 'terminal' ? 'light' : 'terminal';
      document.documentElement.dataset.theme = this.theme;
      try { localStorage.setItem('seo-crawler-theme', this.theme); } catch (_) { /* sin storage */ }
    },

    // Los layouts de Plotly vienen del servidor con fondo blanco; en el tema
    // terminal se vuelven transparentes y con texto verde.
    plotLayout(layout) {
      if (this.theme !== 'terminal') return layout;
      const css = getComputedStyle(document.documentElement);
      const text = css.getPropertyValue('--text').trim();
      const grid = css.getPropertyValue('--border').trim();
      const axis = (a) => ({ ...(a || {}), gridcolor: grid, zerolinecolor: grid, linecolor: grid });
      return {
        ...layout,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { ...(layout && layout.font), color: text, family: 'IBM Plex Mono, monospace' },
        xaxis: axis(layout && layout.xaxis),
        yaxis: axis(layout && layout.yaxis),
      };
    },

    // El bot que declara el User-Agent del formulario, para el aviso de WAF y
    // de robots.txt (decision 98).
    botDelFormulario() {
      const f = this.createForm || {};
      return tokenDeBot(f.user_agent_preset === 'custom' ? f.user_agent_custom : UA_PRESETS[f.user_agent_preset]);
    },

    // ------- Toast notifications -------
    showToast(msg, type = 'success') {
      const id = Date.now();
      this.toasts.push({ id, msg, type });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, 3500);
    },

    // ------- Lista de trabajos -------
    async loadJobs() {
      this.loading = true;
      this.error = null;
      try {
        const q = new URLSearchParams({ page: this.jobsPage, page_size: 20 });
        if (this.jobsFilter) q.set('status', this.jobsFilter);
        if (this.jobsBuscar.trim()) q.set('buscar', this.jobsBuscar.trim());
        if (this.jobsTipo) q.set('tipo', this.jobsTipo);
        if (this.jobsCliente === 'ninguno') q.set('sin_cliente', 'true');
        else if (this.jobsCliente) q.set('client_id', this.jobsCliente);
        if (!this.clientes.length) api('/clients').then(c => { this.clientes = c; }).catch(() => {});
        const data = await api(`/jobs?${q}`);
        this.jobs = data.items;
        this.jobsPages = data.pages;
        this.jobsTotal = data.total;
        if (!this.almacenamiento) this.cargarAlmacenamiento();
      } catch (e) { this.error = e.message; }
      this.loading = false;
    },

    // ------- Borrado en masa (decision 96) -------
    // Lo que libera espacio son los censos viejos, y de uno en uno —entrar en
    // la ficha, teclear «eliminar», volver— no se borran nunca.
    get seleccionados() {
      return Object.keys(this.jobsSel).filter(id => this.jobsSel[id]);
    },

    alternarSeleccion(id) {
      this.jobsSel = { ...this.jobsSel, [id]: !this.jobsSel[id] };
    },

    get paginaSeleccionada() {
      return this.jobs.length > 0 && this.jobs.every(j => this.jobsSel[j.id]);
    },

    alternarPagina() {
      const todos = this.paginaSeleccionada;
      const sel = { ...this.jobsSel };
      this.jobs.forEach(j => { sel[j.id] = !todos; });
      this.jobsSel = sel;
    },

    limpiarSeleccion() {
      this.jobsSel = {};
    },

    // Paso 1: la vista previa. La pide al MISMO endpoint con `simular`, así
    // que los guardias que se ven aquí son exactamente los que se aplicarán.
    async abrirBorrado() {
      const ids = this.seleccionados;
      if (!ids.length) return;
      this.borrado = { paso: 'cargando', ids, previa: null, resultado: null,
                       escribir: '', forzar: false };
      await this.recargarPrevia();
    },

    // Marcar «borrarlos igualmente» REHACE la vista previa. Si no, la tabla
    // seguiria enseñando los que sí se iban y el borrado se llevaria además
    // los protegidos: la lista que se lee y lo que pasa tienen que ser lo
    // mismo.
    async recargarPrevia() {
      const b = this.borrado;
      if (!b) return;
      b.paso = 'cargando';
      try {
        b.previa = await api('/jobs/eliminar', {
          method: 'POST', body: JSON.stringify({ ids: b.ids, simular: true, forzar: b.forzar }) });
        b.paso = 'previa';
      } catch (e) {
        this.borrado = null;
        this.avisar('No se pudo preparar el borrado', e.message);
      }
    },

    get borradoBytes() {
      const p = this.borrado && (this.borrado.resultado || this.borrado.previa);
      return p ? p.bytes_aprox : null;
    },

    // Paso 2: borrar de verdad, en tantas peticiones como haga falta. El
    // servidor devuelve `pendientes` cuando se le agota su presupuesto de
    // tiempo (un censo de millones de enlaces no cabe en una petición), así
    // que esto es un bucle con barra de avance y no una espera a ciegas.
    async confirmarBorrado() {
      const b = this.borrado;
      if (!b || b.escribir.trim() !== 'eliminar') return;
      const total = b.previa.eliminados.length;
      let pendientes = b.previa.eliminados.map(e => e.id);
      // Lo liberado se suma con lo que midió la VISTA PREVIA, de antes de
      // empezar: un censo que necesita varias pasadas llega a la última con
      // casi todo borrado, y lo que el servidor mide entonces es el resto, no
      // lo que ocupaba.
      const medidaPrevia = Object.fromEntries(b.previa.eliminados.map(e => [e.id, e]));
      const acumulado = { eliminados: [], omitidos: [], filas: {}, bytes_aprox: 0,
                          bytes_incompletos: false, archivo_bytes: 0, archivo_incompleto: false,
                          sin_terminar: 0, nota: b.previa.nota };
      b.paso = 'borrando';
      b.hechos = 0;
      b.total = total;

      // Tope de vueltas: si el servidor devolviera siempre los mismos
      // pendientes sin borrar nada, esto sería un bucle infinito contra la
      // API. Mejor pararse y decirlo.
      for (let vuelta = 0; pendientes.length && vuelta < 200; vuelta++) {
        let r;
        try {
          r = await api('/jobs/eliminar', {
            method: 'POST', body: JSON.stringify({ ids: pendientes, forzar: b.forzar }) });
        } catch (e) {
          b.paso = 'previa';
          this.avisar('El borrado se ha interrumpido',
            `${acumulado.eliminados.length} de ${total} rastreos se habían borrado ya. ` +
            `Lo que quedó a medias se puede terminar repitiendo la operación.\n\n${e.message}`);
          await this.loadJobs();
          return;
        }
        acumulado.eliminados.push(...r.eliminados);
        acumulado.omitidos.push(...r.omitidos);
        let filasBorradas = 0;
        for (const [tabla, n] of Object.entries(r.filas || {})) {
          acumulado.filas[tabla] = (acumulado.filas[tabla] || 0) + n;
          filasBorradas += n;
        }
        for (const e of r.eliminados) {
          const m = medidaPrevia[e.id] || e;
          if (m.bytes != null) acumulado.bytes_aprox += m.bytes;
          else acumulado.bytes_incompletos = true;
          if (m.archivado && m.archivo_bytes == null) acumulado.archivo_incompleto = true;
          else acumulado.archivo_bytes += m.archivo_bytes || 0;
        }
        b.hechos = acumulado.eliminados.length;
        pendientes = r.pendientes;
        // Avanzar es terminar algún censo O borrar filas: un censo de decenas
        // de millones de enlaces pasa muchas vueltas sin terminar, borrando
        // en cada una. Atascado es una vuelta que no hace ninguna de las dos.
        if (pendientes.length && !r.eliminados.length && !filasBorradas) {
          this.showToast('El borrado no avanza; quedan ' + pendientes.length, 'error');
          break;
        }
      }
      if (pendientes.length) {
        // Se acabaron las vueltas o se atascó: lo que queda se ve en la lista
        // como «Borrándose» y se termina repitiendo la operación.
        acumulado.sin_terminar = pendientes.length;
      }

      this.borrado.resultado = acumulado;
      this.borrado.paso = 'hecho';
      this.jobsSel = {};
      await this.loadJobs();
      if (this.verAlmacenamiento || this.almacenamiento) this.cargarAlmacenamiento();
    },

    cerrarBorrado() {
      this.borrado = null;
    },

    // ------- Espacio en disco -------
    async cargarAlmacenamiento() {
      try { this.almacenamiento = await api('/almacenamiento'); } catch (_) { this.almacenamiento = null; }
    },

    setJobsPage(p) {
      if (p < 1 || p > this.jobsPages) return;
      this.jobsPage = p;
      this.loadJobs();
    },

    filterJobs(status) {
      this.jobsFilter = this.jobsFilter === status ? '' : status;
      this.jobsPage = 1;
      this.loadJobs();
    },

    // ------- Crear trabajo -------
    // `preset` = { cliente_id, profile_id } cuando se lanza desde la ficha de
    // un cliente: el modal se abre ya con lo heredado.
    async openCreate(preset = null) {
      this.createForm = _freshForm();
      this.createError = null;
      this.createPerfiles = [];
      this.showCreate = true;
      this.$nextTick(() => lucide.createIcons());
      try {
        if (!this.clientes.length) this.clientes = await api('/clients');
      } catch (_) { /* sin clientes: el desplegable queda con «Sin cliente» */ }
      if (preset && preset.cliente_id) {
        this.createForm.cliente_id = preset.cliente_id;
        await this.cambiarClienteModal(preset.profile_id || '');
      }
    },

    // Al elegir cliente (o perfil) en el modal, se precarga lo que heredaria
    // el rastreo. Lo que se ve es lo que se lanza: el modal manda lo que
    // muestra, y lo que el modal no tiene (plantillas, umbrales extra...) se
    // hereda en el servidor al crear el job.
    async cambiarClienteModal(profileId = '') {
      const f = this.createForm;
      if (f.cliente_id === '__nuevo__') {
        this.showCreate = false;
        this.nuevoCliente();
        return;
      }
      f.profile_id = profileId;
      this.createPerfiles = [];
      if (!f.cliente_id) return;
      try {
        this.createPerfiles = await api(`/clients/${f.cliente_id}/profiles`);
      } catch (e) { this.createError = e.message; return; }
      await this.aplicarHerencia();
    },

    async aplicarHerencia() {
      const f = this.createForm;
      if (!f.cliente_id) return;
      try {
        const q = f.profile_id ? `?profile_id=${f.profile_id}` : '';
        const r = await api(`/clients/${f.cliente_id}/config-resuelta${q}`);
        const nuevo = _freshForm();
        _formDesdeConfig(nuevo, r.config_heredada || {});
        // Se conserva lo que no es configuracion: nombre, cliente y perfil.
        nuevo.name = f.name;
        nuevo.cliente_id = f.cliente_id;
        nuevo.profile_id = f.profile_id;
        nuevo.configTab = f.configTab;
        nuevo.seeds = (r.seeds && r.seeds.length) ? r.seeds.join('\n') : f.seeds;
        if (!nuevo.name) {
          const perfil = this.createPerfiles.find(p => p.id === f.profile_id);
          const cliente = this.clientes.find(c => c.id === f.cliente_id);
          const base = perfil ? `${cliente ? cliente.nombre + ' · ' : ''}${perfil.nombre}` : (cliente ? cliente.nombre : '');
          nuevo.name = base ? `${base} · ${new Date().toISOString().slice(0, 10)}` : '';
        }
        this.createForm = nuevo;
      } catch (e) { this.createError = e.message; }
    },

    // Una linea con lo que se va a usar, para no tener que abrir las seis
    // pestanas para saberlo (UX-19).
    resumenConfig() {
      const f = this.createForm;
      const partes = [
        'profundidad ' + f.max_depth,
        f.max_urls ? fmt.num(+f.max_urls) + ' URLs' : 'sin tope de URLs',
        f.render_js ? 'con render JS' : 'sin render JS',
        'robots: ' + ({ respect: 'respetar', ignore: 'ignorar', audit: 'auditar' }[f.robots_mode] || f.robots_mode),
      ];
      return partes.join(' · ');
    },

    // El mismo rastreo con 250 URLs y «CANARY» en el nombre: se reconoce
    // como canario (tipo deducido) y no entra en la evolucion del cliente.
    async lanzarCanario() {
      const original = { name: this.createForm.name, max_urls: this.createForm.max_urls };
      const host = (this.createForm.seeds.split('\n')[0] || '').replace(/^https?:\/\//, '').split('/')[0];
      this.createForm.name = 'CANARY - ' + (original.name || host);
      this.createForm.max_urls = 250;
      await this.submitCreate();
      if (this.showCreate) Object.assign(this.createForm, original);  // fallo: se deja como estaba
    },

    async submitCreate() {
      this.creating = true;
      this.createError = null;
      try {
        const f = this.createForm;
        const seeds = f.seeds.split('\n').map(s => s.trim()).filter(Boolean);

        const payload = { name: f.name, seeds, config: _configDesdeForm(f) };
        if (f.cliente_id) payload.cliente_id = f.cliente_id;
        if (f.profile_id) payload.profile_id = f.profile_id;

        await api('/jobs', { method: 'POST', body: JSON.stringify(payload) });
        this.showCreate = false;
        this.jobsPage = 1;
        await this.loadJobs();
        if (this.view === 'client' && this.cliente) await this.abrirCliente(this.cliente.id, this.clienteTab);
        this.showToast('Rastreo iniciado correctamente');
      } catch (e) { this.createError = e.message; }
      this.creating = false;
    },

    // ------- Clientes (#36) -------
    async verClientes() {
      this.view = 'clients';
      await this.cargarClientes();
    },

    async cargarClientes() {
      try {
        const [clientes, panel] = await Promise.all([api('/clients'), api('/clients/panel')]);
        this.clientes = clientes;
        this.panelClientes = panel;
      } catch (e) {
        this.clientes = [];
        this.error = 'No se pudieron cargar los clientes: ' + e.message;
      }
    },

    async abrirCliente(id, tab = 'rastreos') {
      this.view = 'client';
      this.clienteTab = tab;
      this.clienteError = '';
      try {
        const [c, perfiles, jobs, serie] = await Promise.all([
          api(`/clients/${id}`),
          api(`/clients/${id}/profiles`),
          api(`/jobs?client_id=${id}&page_size=100`),
          api(`/clients/${id}/serie`),
        ]);
        this.cliente = c;
        this.clientePerfiles = perfiles;
        this.clienteJobs = jobs.items || [];
        this.clienteSerie = serie || [];
        this.clienteForm = _formCliente(c);
      } catch (e) { this.error = e.message; }
    },

    nuevoCliente() {
      this.view = 'client';
      this.cliente = null;
      this.clientePerfiles = [];
      this.clienteJobs = [];
      this.clienteForm = _formCliente(null);
      this.clienteTab = 'config';
      this.clienteError = '';
    },

    agregarIntegracion() {
      this.clienteForm.integraciones.push({
        tipo: 'gsc', etiqueta: '', parametros: '', credencial_env: '', activa: true, configurada: null,
      });
    },

    quitarIntegracion(i) {
      this.clienteForm.integraciones.splice(i, 1);
    },

    // El prefijo que exige el servidor para la variable de cada tipo: se
    // muestra como pista, la validacion de verdad es la de la API.
    prefijoIntegracion(tipo) {
      return ({ gsc: 'GSC_', dataforseo: 'DATAFORSEO_', ahrefs: 'AHREFS_', semrush: 'SEMRUSH_',
                majestic: 'MAJESTIC_', pagespeed: 'PAGESPEED_' })[tipo] || '';
    },

    async guardarCliente() {
      this.clienteError = '';
      const f = this.clienteForm;
      let config = {};
      try {
        config = f.config_defecto.trim() ? JSON.parse(f.config_defecto) : {};
      } catch (e) {
        this.clienteError = 'La configuracion por defecto no es JSON valido: ' + e.message;
        return;
      }
      const cuerpo = {
        nombre: f.nombre,
        dominios: f.dominios.split('\n').map(s => s.trim()).filter(Boolean),
        zona_horaria: f.zona_horaria || null,
        notas: f.notas || null,
        config_defecto: config,
        integraciones: f.integraciones.map(i => ({
          tipo: i.tipo,
          etiqueta: i.etiqueta || null,
          parametros: _parseKV(i.parametros, '='),
          credencial_env: (i.credencial_env || '').trim() || null,
          activa: !!i.activa,
        })),
      };
      if (f.slug) cuerpo.slug = f.slug;
      try {
        const c = this.cliente
          ? await api(`/clients/${this.cliente.id}`, { method: 'PATCH', body: JSON.stringify(cuerpo) })
          : await api('/clients', { method: 'POST', body: JSON.stringify(cuerpo) });
        this.clientes = await api('/clients');
        await this.abrirCliente(c.id, 'config');
        this.showToast('Cliente guardado');
      } catch (e) { this.clienteError = e.message; }
    },

    async borrarCliente() {
      if (!this.cliente) return;
      // Se pide el slug escrito: un borrado que se lleva perfiles e
      // integraciones no puede salir de un clic suelto. Los rastreos NO se
      // borran desde aqui: son censos entregados y quedan sin cliente.
      const ok = await this.preguntar({
        titulo: `Borrar el cliente «${this.cliente.nombre}»`,
        mensaje: `Se borran el cliente y sus perfiles. Sus ${this.cliente.n_jobs} rastreos NO se borran: quedan sin cliente.`,
        aceptar: 'Borrar cliente', peligro: true, escribir: this.cliente.slug,
      });
      if (!ok) return;
      const slug = this.cliente.slug;
      try {
        await api(`/clients/${this.cliente.id}?confirmar=${encodeURIComponent(slug)}`, { method: 'DELETE' });
        this.cliente = null;
        await this.verClientes();
        this.showToast('Cliente borrado');
      } catch (e) { this.clienteError = e.message; }
    },

    nuevoPerfil() {
      this.perfilError = '';
      this.perfilForm = { id: null, nombre: '', rol: 'propio', seeds: '', config: '{}', notas: '', activo: true };
    },

    editarPerfil(p) {
      this.perfilError = '';
      this.perfilForm = {
        id: p.id, nombre: p.nombre, rol: p.rol, seeds: (p.seeds || []).join('\n'),
        config: JSON.stringify(p.config || {}, null, 2), notas: p.notas || '', activo: p.activo,
      };
    },

    async guardarPerfil() {
      this.perfilError = '';
      const f = this.perfilForm;
      let config = {};
      try {
        config = f.config.trim() ? JSON.parse(f.config) : {};
      } catch (e) {
        this.perfilError = 'La configuracion no es JSON valido: ' + e.message;
        return;
      }
      const cuerpo = {
        nombre: f.nombre, rol: f.rol,
        seeds: f.seeds.split('\n').map(s => s.trim()).filter(Boolean),
        config, notas: f.notas || null, activo: !!f.activo,
      };
      const base = `/clients/${this.cliente.id}/profiles`;
      try {
        if (f.id) await api(`${base}/${f.id}`, { method: 'PATCH', body: JSON.stringify(cuerpo) });
        else await api(base, { method: 'POST', body: JSON.stringify(cuerpo) });
        this.perfilForm = null;
        await this.abrirCliente(this.cliente.id, 'rastreos');
      } catch (e) { this.perfilError = e.message; }
    },

    async borrarPerfil(p) {
      if (!await this.preguntar({ titulo: `Borrar el perfil «${p.nombre}»`,
        mensaje: 'Sus rastreos NO se borran: quedan colgados del cliente.', aceptar: 'Borrar perfil', peligro: true })) return;
      try {
        await api(`/clients/${this.cliente.id}/profiles/${p.id}`, { method: 'DELETE' });
        await this.abrirCliente(this.cliente.id, 'rastreos');
      } catch (e) { this.clienteError = e.message; }
    },

    serieVisible() {
      return this.serieSoloCensos ? this.clienteSerie.filter(j => j.tipo === 'censo') : this.clienteSerie;
    },

    // El censo completado anterior a este en la serie: es contra el que se
    // mide la diferencia de errores, saltandose canarios y pruebas.
    censoAnterior(j) {
      const i = this.clienteSerie.indexOf(j);
      return this.clienteSerie.slice(i + 1).find(x => x.tipo === 'censo' && x.status === 'completed') || null;
    },

    nombreDePerfil(id) {
      const p = this.clientePerfiles.find(x => x.id === id);
      return p ? p.nombre : '';
    },

    // ------- Detalle del trabajo -------
    async openJob(id) {
      this._stopSemanticPoll();
      this.view = 'detail';
      this.detailTab = 'overview';
      this.stats = null;
      this.progress = null;
      this.insights = null;
      this.expandedCategories = {};
      this.semanticResults = null;
      this.semanticError = null;
      this.semanticProgress = null;
      this.cannibalPairs = [];
      this.cannibalHasQueryData = false;
      this.cannibalFilter = 'all';
      this.driftData = [];
      this.gapResults = null;
      this.gapTopic = '';
      this.targetResults = null;
      this.targetTheme = '';
      this.gscFetchResult = null;
      this.cleaningGroups = [];
      this.cleaningSelectedGroup = null;
      this.cleaningPreview = null;
      this.cleaningRulesets = [];
      this.cleaningError = null;
      this.cleaningMessage = null;
      this._pararEnriq();
      this.enriqRuns = []; this.enriqRun = null; this.enriqDecisiones = null;
      this.urls = []; this.issues = []; this.links = [];
      this.issueFilters = { severity: '', issue_type: '' };
      this.issuesResumen = null; this.issuesVista = 'tipos'; this.grupoAbierto = {};
      this.segmentosJob = null; this.issuesSegmento = '';
      this.linksPage = 1; this.issuesPage = 1;
      this.diff = null; this.diffOtro = ''; this.diffCandidatos = [];
      this.urlsTotal = 0; this.issuesTotal = 0; this.linksTotal = 0;
      this.loading = true;
      try {
        this.job = await api(`/jobs/${id}`);
        this._registrarAMedida(this.job);
        await this.loadStats();
        this._startProgress();
        this.cargarEjecuciones();
        this.cargarResumenAMedida();
      } catch (e) { this.error = e.message; }
      this.loading = false;
      window.scrollTo({ top: 0, behavior: 'smooth' });
      this.$nextTick(() => lucide.createIcons());
    },

    backToJobs() {
      this._salirDeJob();
      this.view = 'jobs';
      this.loadJobs();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },

    async loadStats() {
      if (!this.job) return;
      try {
        this.stats = await api(`/jobs/${this.job.id}/stats`);
      } catch { this.stats = null; }
    },

    // ------- Polling de progreso -------
    _startProgress() {
      this._stopProgress();
      // 'analyzing' is a non-terminal phase after the crawl while the analyzer
      // populates issues/indexability/pagerank — keep polling through it.
      if (!this.job || !['pending','running','analyzing'].includes(this.job.status)) return;
      const poll = async () => {
        try {
          this.progress = await api(`/jobs/${this.job.id}/progress`);
          if (this.progress.status !== this.job.status) {
            this.job.status = this.progress.status;
            if (['completed','failed','cancelled'].includes(this.progress.status)) {
              this._stopProgress();
              this.job = await api(`/jobs/${this.job.id}`);
              await this.loadStats();
              // Analysis just finished: drop cached tab data so the active
              // tab refetches fresh issues/insights instead of the empty/
              // partial snapshot loaded mid-analysis.
              this._invalidateResultCaches();
              this._reloadActiveTab();
            }
          }
        } catch {}
      };
      poll();
      this.progressTimer = setInterval(poll, 2000);
    },

    _invalidateResultCaches() {
      this.insights = null;
      this.issues = []; this.issuesPage = 1;
      this.links = []; this.linksPage = 1;
      this.urls = []; this.urlsPage = 1;
    },

    _reloadActiveTab() {
      const t = this.detailTab;
      if (t === 'urls') this.loadUrls();
      else if (t === 'issues') this.loadIssues();
      else if (t === 'links') this.loadLinks();
      else if (t === 'insights') this.loadInsights();
    },

    _stopProgress() {
      if (this.progressTimer) { clearInterval(this.progressTimer); this.progressTimer = null; }
    },

    _stopSemanticPoll() {
      if (this.semanticPollTimer) { clearInterval(this.semanticPollTimer); this.semanticPollTimer = null; }
    },

    // ------- Dialogos propios (UX-21) -------
    // Sustituyen a confirm(), alert() y prompt(): los nativos bloquean la
    // pagina entera (y las pruebas automaticas), no se pueden leer con calma
    // y no dicen que es irreversible con el peso que merece.
    // `escribir`: texto que hay que teclear para habilitar el boton (borrados
    // que se llevan mucho por delante). Devuelve true/false.
    // `pedir`: etiqueta de un campo de texto libre; entonces devuelve el texto
    // escrito (o false si se cancela).
    preguntar({ titulo, mensaje, aceptar = 'Aceptar', peligro = false, escribir = '', solo_aviso = false, pedir = '', valor = '' }) {
      return new Promise(resolver => {
        this.dialogo = { titulo, mensaje, aceptar, peligro, escribir, entrada: valor, solo_aviso, pedir, resolver };
        this.$nextTick(() => {
          const el = document.querySelector('.dialogo [data-foco]');
          if (el) el.focus();
        });
      });
    },
    avisar(titulo, mensaje) {
      return this.preguntar({ titulo, mensaje, aceptar: 'Entendido', solo_aviso: true });
    },
    cerrarDialogo(respuesta) {
      const d = this.dialogo;
      if (!d) return;
      if (respuesta && d.escribir && d.entrada.trim() !== d.escribir) return;
      if (respuesta && d.pedir && !d.entrada.trim()) return;
      this.dialogo = null;
      d.resolver(d.pedir ? (respuesta ? d.entrada.trim() : false) : !!respuesta);
    },

    // ------- Nombre, tipo y cliente (UX-07/08) -------
    async abrirEditarJob() {
      if (!this.job) return;
      this.editarError = '';
      try { if (!this.clientes.length) this.clientes = await api('/clients'); } catch (_) {}
      this.editarJob = {
        name: this.job.name,
        tipo: this.job.tipo_efectivo || 'censo',
        cliente_id: this.job.cliente_id || '',
      };
    },

    async guardarJob() {
      const e = this.editarJob;
      const cuerpo = { name: e.name, cliente_id: e.cliente_id || null };
      // Solo se marca a mano si cambia lo deducido: asi un rastreo que se
      // llama «CANARY» y se deja como canario sigue deduciendose del nombre.
      if (e.tipo !== this.job.tipo_efectivo || this.job.tipo) cuerpo.tipo = e.tipo;
      try {
        this.job = await api(`/jobs/${this.job.id}`, { method: 'PATCH', body: JSON.stringify(cuerpo) });
        this.editarJob = null;
        this.panelClientes = null;
        this.showToast('Rastreo actualizado');
      } catch (err) { this.editarError = err.message; }
    },

    // ------- Cancelar / Eliminar -------
    async cancelJob() {
      if (!this.job || !await this.preguntar({ titulo: 'Cancelar este rastreo',
        mensaje: 'El crawler se detendrá en cuanto procese la señal. Lo rastreado hasta ahora se conserva y se puede reanudar.',
        aceptar: 'Cancelar el rastreo', peligro: true })) return;
      try {
        this.job = await api(`/jobs/${this.job.id}/cancel`, { method: 'PATCH' });
        this._stopProgress();
      } catch (e) { this.avisar('No se pudo cancelar', e.message); }
    },

    async deleteJob() {
      if (!this.job || !await this.preguntar({ titulo: 'Eliminar este rastreo',
        mensaje: `Se borran «${this.job.name}» y todos sus datos: URLs, enlaces, incidencias y comparaciones. No se puede deshacer. Si es un censo entregado, descarga antes el backup (Exportar → Backup completo).`,
        aceptar: 'Eliminar para siempre', peligro: true, escribir: 'eliminar' })) return;
      try {
        await api(`/jobs/${this.job.id}`, { method: 'DELETE' });
        this.showToast('Trabajo eliminado', 'info');
        this.backToJobs();
      } catch (e) { this.avisar('No se pudo eliminar', e.message); }
    },

    // ------- Pestanas -------
    // ------- Comparar con otro censo (#32) -------
    diff: null,
    diffOtro: '',
    diffCandidatos: [],
    diffCargando: false,

    // Las tres columnas (resueltas / nuevas / siguen) en una sola fila por
    // tipo: tres tablas separadas obligan a cruzarlas a ojo, que es lo que
    // nadie hace.
    tiposDeIncidencia() {
      const inc = this.diff && this.diff.incidencias;
      if (!inc) return [];
      const filas = {};
      const meter = (lista, campo) => (lista || []).forEach(f => {
        const fila = filas[f.tipo] || (filas[f.tipo] = {
          tipo: f.tipo, severidad: f.severidad,
          resueltas: 0, nuevas: 0, siguen: 0,
        });
        fila[campo] = f.total;
      });
      meter(inc.resueltas && inc.resueltas.por_tipo, 'resueltas');
      meter(inc.nuevas && inc.nuevas.por_tipo, 'nuevas');
      meter(inc.siguen && inc.siguen.por_tipo, 'siguen');
      const orden = { error: 0, warning: 1, info: 2 };
      return Object.values(filas).sort((a, b) =>
        (orden[a.severidad] ?? 3) - (orden[b.severidad] ?? 3)
        || (b.nuevas + b.resueltas) - (a.nuevas + a.resueltas));
    },
    // --- Rastreos programados ---
    abrirProgramar() {
      // El nombre por defecto quita la fecha del rastreo de origen: la
      // programacion le pone la suya a cada ejecucion.
      this.programarNombre = (this.job.name || '').replace(/\s*[·-]\s*\d{4}-\d{2}-\d{2}.*$/, '');
      this.programarError = '';
      this.programarAbierto = true;
    },

    async guardarProgramacion() {
      this.programarError = '';
      try {
        await api('/programaciones', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            nombre: this.programarNombre,
            client_id: this.job.client_id,
            cliente_id: this.job.cliente_id || null,
            profile_id: this.job.profile_id || null,
            seeds: this.job.seeds,
            config: this.job.config || {},
            cron: this.programarCron,
            zona_horaria: this.programarZona,
          }),
        });
        this.programarAbierto = false;
        await this.verProgramados();
      } catch (e) {
        // El mensaje se queda EN el modal: si se cierra, quien escribio una
        // expresion mal no ve por que no se guardo.
        this.programarError = e.message;
      }
    },
    async verProgramados() {
      this.view = 'programados';
      await this.cargarProgramaciones();
    },

    async cargarProgramaciones() {
      try {
        this.programaciones = await api('/programaciones');
      } catch (e) {
        this.programaciones = [];
        this.error = 'No se pudieron cargar las programaciones: ' + e.message;
      }
    },

    async alternarProgramacion(p) {
      try {
        await api('/programaciones/' + p.id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ activa: !p.activa }),
        });
        await this.cargarProgramaciones();
      } catch (e) {
        this.error = 'No se pudo cambiar: ' + e.message;
      }
    },

    async borrarProgramacion(p) {
      // Se avisa de lo que NO se borra: los rastreos que ya lanzo son
      // censos entregados o comparables, y tirarlos por cambiar de idea
      // sobre la hora seria perder el historico del cliente.
      if (!await this.preguntar({ titulo: `Quitar la programación «${p.nombre}»`,
        mensaje: 'Deja de lanzarse sola. Los rastreos que ya ha lanzado NO se borran.', aceptar: 'Quitar', peligro: true })) return;
      try {
        await api('/programaciones/' + p.id, { method: 'DELETE' });
        await this.cargarProgramaciones();
      } catch (e) {
        this.error = 'No se pudo borrar: ' + e.message;
      }
    },

    async cargarCandidatosDiff() {
      // Solo rastreos COMPLETADOS del mismo host: comparar con uno truncado
      // se puede, pero el resultado no afirma ausencias, y comparar con otro
      // sitio lo rechaza el servidor.
      if (!this.job) return;
      const miHost = (this.job.seeds && this.job.seeds[0] || '').replace(/^https?:\/\//, '').split('/')[0];
      const d = await api('/jobs?page=1&page_size=100');
      this.diffCandidatos = (d.items || []).filter(j => {
        if (j.id === this.job.id) return false;
        if (j.status !== 'completed') return false;
        const h = (j.seeds && j.seeds[0] || '').replace(/^https?:\/\//, '').split('/')[0];
        return h && h === miHost;
      });
    },

    // Tamano parecido = al menos la mitad de URLs que este censo.
    candidatosDiff(parecidos) {
      const mio = (this.stats && this.stats.total_urls) || this.job?.total_urls_crawled || 0;
      return this.diffCandidatos.filter(j => ((j.total_urls_crawled || 0) >= mio * 0.5) === parecidos);
    },

    async cargarDiff() {
      if (!this.job || !this.diffOtro) { this.diff = null; return; }
      this.diffCargando = true;
      try {
        this.diff = await api(`/jobs/${this.job.id}/diff/${this.diffOtro}`);
      } catch (e) {
        this.diff = { comparable: false, motivo: 'No se pudo comparar: ' + e };
      } finally {
        this.diffCargando = false;
        this.$nextTick(() => lucide.createIcons());
      }
    },

    async switchTab(tab) {
      this.detailTab = tab;
      if (tab === 'urls' && this.urls.length === 0) this.loadUrls();
      if (tab === 'issues') {
        if (!this.issuesResumen) this.loadIssuesResumen();
        if ((this.issueFilters.issue_type || this.issuesVista === 'lista') && this.issues.length === 0) this.loadIssues();
      }
      if (tab === 'links' && this.links.length === 0) this.loadLinks();
      if (tab === 'insights' && !this.insights) this.loadInsights();
      if (tab === 'semantic') this.loadSemantic();
      if (tab === 'cleaning') this.loadCleaning();
      if (tab === 'diff') {
        if (!this.diffCandidatos.length) this.cargarCandidatosDiff();
        // Se abre con la comparacion que el worker hizo al cerrar el rastreo
        // (UX-12): un desplegable vacio obligaba a saber con cual comparar.
        const auto = this.job && this.job.comparacion && this.job.comparacion.comparado_con;
        if (auto && !this.diffOtro) { this.diffOtro = auto; this.cargarDiff(); }
      }
      if (tab === 'enriquecido') this.cargarEjecuciones();
      this.$nextTick(() => lucide.createIcons());
    },

    // ------- Informe SEO -------
    async loadInsights() {
      if (!this.job) return;
      this.insightsLoading = true;
      try {
        this.insights = await api(`/jobs/${this.job.id}/insights`);
      } catch (e) { this.error = e.message; }
      this.insightsLoading = false;
    },

    toggleCategory(key) {
      this.expandedCategories[key] = !this.expandedCategories[key];
    },

    isCategoryExpanded(key) {
      return !!this.expandedCategories[key];
    },

    scoreColor(score) {
      if (score >= 80) return 'var(--green)';
      if (score >= 50) return 'var(--orange)';
      return 'var(--red)';
    },

    scoreClass(score) {
      if (score >= 80) return 'score-good';
      if (score >= 50) return 'score-ok';
      return 'score-bad';
    },

    scoreLabel(score) {
      if (score >= 80) return 'Bueno';
      if (score >= 50) return 'Mejorable';
      return 'Critico';
    },

    priorityClass(p) {
      return 'priority-' + p;
    },

    priorityLabel(p) {
      return { alta: 'Alta', media: 'Media', baja: 'Baja' }[p] || p;
    },

    // ------- Recommendation URL drill-down -------
    async toggleRecUrls(rec) {
      const key = rec.title;
      this.recUrlsOpen[key] = !this.recUrlsOpen[key];
      // Already loaded? Just toggle visibility
      if (this.recUrls[key]?.loaded) return;
      // Load URLs
      this.recUrls[key] = { loading: true, urls: [], loaded: false };
      try {
        if (rec.issue_types && rec.issue_types.length > 0) {
          // Fetch from issue_types (merge results from each type)
          let all = [];
          for (const it of rec.issue_types) {
            const data = await api(`/jobs/${this.job.id}/issues/urls?issue_type=${encodeURIComponent(it)}&limit=50`);
            all = all.concat(data);
          }
          // Dedup by url id
          const seen = new Set();
          this.recUrls[key].urls = all.filter(u => { if (seen.has(u.id)) return false; seen.add(u.id); return true; });
        } else if (rec.url_filter && Object.keys(rec.url_filter).length > 0) {
          // Fetch from urls endpoint with filters
          let params = 'page=1&page_size=50';
          for (const [k, v] of Object.entries(rec.url_filter)) params += `&${k}=${v}`;
          const data = await api(`/jobs/${this.job.id}/urls?${params}`);
          this.recUrls[key].urls = data.items.map(u => ({ id: u.id, url: u.url, status_code: u.status_code }));
        }
        this.recUrls[key].loaded = true;
      } catch (e) { this.error = e.message; }
      this.recUrls[key].loading = false;
      this.$nextTick(() => lucide.createIcons());
    },

    recHasUrls(rec) {
      return (rec.issue_types && rec.issue_types.length > 0) || (rec.url_filter && Object.keys(rec.url_filter).length > 0);
    },

    openRecInExplorer(rec) {
      if (rec.url_filter && Object.keys(rec.url_filter).length > 0) {
        // Find matching explorer tab or use 'all' with manual filter
        this.openExplorer();
        // Apply the filter by finding the right tab
        const f = rec.url_filter;
        let matched = EXP_TABS.find(t => t.filter && JSON.stringify(t.filter) === JSON.stringify(f));
        if (matched) {
          this.setExplorerTab(matched.key);
        }
      } else if (rec.issue_types && rec.issue_types.length > 0) {
        // Navigate to issues tab filtered by issue type
        this.detailTab = 'issues';
        this.issueFilters.issue_type = rec.issue_types[0];
        this.issuesPage = 1;
        this.loadIssues();
      }
    },

    // SVG ring dasharray for score circle
    ringDash(score, radius) {
      const circ = 2 * Math.PI * radius;
      return `${(score / 100) * circ} ${circ}`;
    },

    // ------- Semantico -------
    async loadSemantic() {
      if (!this.job) return;
      // Load GSC + Gemini accounts in parallel
      try {
        const [gsc, gemini] = await Promise.all([
          api('/semantic/gsc-accounts'),
          api('/semantic/gemini-accounts'),
        ]);
        this.gscAccounts = gsc;
        this.geminiAccounts = gemini;
        if (gemini.length === 1 && !this.semanticGeminiAccountId) {
          this.semanticGeminiAccountId = gemini[0].id;
        }
      } catch (_) {}
      // Check if analysis exists
      try {
        const status = await api(`/jobs/${this.job.id}/semantic/status`);
        if (status.status === 'completed') {
          const results = await api(`/jobs/${this.job.id}/semantic/results`);
          this.semanticResults = results;
          this.semanticError = null;
          this.$nextTick(() => this.renderSemanticChart('rings'));
        } else if (status.status === 'running') {
          this.semanticLoading = true;
          this.semanticProgress = { stage: status.stage, progress: status.progress };
          this._pollSemanticStatus();
        } else if (status.status === 'failed') {
          this.semanticError = status.error_message || 'El analisis fallo.';
        }
      } catch (_) {}
    },

    _pollSemanticStatus() {
      if (this.semanticPollTimer) clearInterval(this.semanticPollTimer);
      this.semanticPollTimer = setInterval(async () => {
        try {
          const status = await api(`/jobs/${this.job.id}/semantic/status`);
          this.semanticProgress = { stage: status.stage, progress: status.progress };
          if (status.status === 'completed') {
            clearInterval(this.semanticPollTimer);
            this.semanticPollTimer = null;
            this.semanticLoading = false;
            const results = await api(`/jobs/${this.job.id}/semantic/results`);
            this.semanticResults = results;
            this.semanticError = null;
            this.$nextTick(() => this.renderSemanticChart('rings'));
          } else if (status.status === 'failed') {
            clearInterval(this.semanticPollTimer);
            this.semanticPollTimer = null;
            this.semanticLoading = false;
            this.semanticError = status.error_message || 'El analisis fallo.';
          }
        } catch (_) {}
      }, 2000);
    },

    async runSemanticAnalysis() {
      if (!this.job) return;
      if (!this.semanticGeminiAccountId) {
        this.semanticError = 'Selecciona una API key de Gemini antes de lanzar el analisis.';
        return;
      }
      this.semanticLoading = true;
      this.semanticError = null;
      this.semanticResults = null;
      this.semanticProgress = { stage: 'starting', progress: 0 };
      try {
        await api(`/jobs/${this.job.id}/semantic/analyze`, {
          method: 'POST',
          body: JSON.stringify({
            gemini_account_id: this.semanticGeminiAccountId,
            alpha: this.semanticAlpha,
            beta: this.semanticBeta,
            cannibal_threshold: this.semanticThreshold,
          }),
        });
        this._pollSemanticStatus();
      } catch (e) {
        this.semanticLoading = false;
        this.semanticError = e.message;
      }
    },

    async saveGeminiAccount() {
      this.geminiFormError = null;
      const name = (this.geminiFormName || '').trim();
      const key = (this.geminiFormKey || '').trim();
      if (!key) {
        this.geminiFormError = 'La API key no puede estar vacia.';
        return;
      }
      try {
        const created = await api('/semantic/gemini-accounts', {
          method: 'POST',
          body: JSON.stringify({ name: name || 'Gemini account', api_key: key }),
        });
        this.geminiAccounts = await api('/semantic/gemini-accounts');
        this.semanticGeminiAccountId = created.id;
        this.geminiFormName = '';
        this.geminiFormKey = '';
        this.showGeminiModal = false;
      } catch (e) {
        this.geminiFormError = e.message;
      }
    },

    async deleteGeminiAccount(id) {
      if (!await this.preguntar({ titulo: 'Eliminar esta API key',
        mensaje: 'Los análisis pasados conservan su resultado, pero no podrán volver a calcular embeddings de consultas.', aceptar: 'Eliminar', peligro: true })) return;
      try {
        await api(`/semantic/gemini-accounts/${id}`, { method: 'DELETE' });
        this.geminiAccounts = await api('/semantic/gemini-accounts');
        if (this.semanticGeminiAccountId === id) this.semanticGeminiAccountId = '';
      } catch (e) {
        this.semanticError = e.message;
      }
    },

    semanticStageLabel(stage) {
      const labels = {
        starting: 'Iniciando...',
        loading_data: 'Cargando datos de la BD...',
        filtering: 'Filtrando páginas...',
        embedding: 'Generando embeddings (esto puede tardar)...',
        weighting: 'Calculando pesos...',
        centroid: 'Calculando centroide...',
        dimensionality_reduction: 'Reduccion dimensional (PCA + UMAP)...',
        clustering: 'Clustering HDBSCAN...',
        classification: 'Clasificando anillos...',
        cannibalization: 'Detectando canibalización...',
        done: 'Completado',
      };
      return labels[stage] || stage || 'Procesando...';
    },

    async renderSemanticChart(type) {
      if (!this.job) return;
      await this.$nextTick();
      try {
        if (type === 'rings') {
          const data = await api(`/jobs/${this.job.id}/semantic/ring-data`);
          await this.$nextTick();
          const el = document.getElementById('semantic-ring-chart');
          if (el && window.Plotly) Plotly.newPlot(el, data.data, this.plotLayout(data.layout), { responsive: true });
        } else if (type === 'scatter') {
          const data = await api(`/jobs/${this.job.id}/semantic/scatter-data`);
          await this.$nextTick();
          const el = document.getElementById('semantic-scatter-chart');
          if (el && window.Plotly) Plotly.newPlot(el, data.data, this.plotLayout(data.layout), { responsive: true });
        }
      } catch (e) { this.error = e.message; }
    },

    async loadCannibalization() {
      if (!this.job) return;
      this.cannibalLoading = true;
      try {
        const brand = encodeURIComponent(this.cannibalBrand.trim());
        const data = await api(`/jobs/${this.job.id}/semantic/cannibalization?brand=${brand}`);
        this.cannibalPairs = data.pairs || [];
        this.cannibalHasQueryData = data.has_query_data || false;
      } catch (e) { this.error = e.message; }
      this.cannibalLoading = false;
    },

    async loadDrift() {
      if (!this.job || this.driftData.length > 0) return;
      this.driftLoading = true;
      try {
        const data = await api(`/jobs/${this.job.id}/semantic/drift`);
        this.driftData = data.drift || [];
      } catch (e) { this.error = e.message; }
      this.driftLoading = false;
    },

    async runGapAnalysis() {
      if (!this.job || !this.gapTopic.trim()) return;
      this.gapLoading = true;
      this.gapResults = null;
      try {
        this.gapResults = await api(`/jobs/${this.job.id}/semantic/gap-analysis`, {
          method: 'POST',
          body: JSON.stringify({ topic: this.gapTopic.trim() }),
        });
      } catch (e) { this.error = e.message; }
      this.gapLoading = false;
    },

    async analyzeTarget() {
      if (!this.job || !this.targetTheme.trim()) return;
      this.targetLoading = true;
      this.targetResults = null;
      try {
        this.targetResults = await api(`/jobs/${this.job.id}/semantic/target-rings`, {
          method: 'POST',
          body: JSON.stringify({ target_theme: this.targetTheme.trim() }),
        });
        this.$nextTick(() => {
          const el = document.getElementById('semantic-target-ring-chart');
          if (el && window.Plotly && this.targetResults.ring_map) {
            Plotly.newPlot(el, this.targetResults.ring_map.data, this.plotLayout(this.targetResults.ring_map.layout), { responsive: true });
          }
        });
      } catch (e) { this.error = e.message; }
      this.targetLoading = false;
    },

    async loadGscProperties() {
      if (!this.semanticGscAccountId) return;
      this.gscPropsLoading = true;
      try {
        const data = await api(`/semantic/gsc-accounts/${this.semanticGscAccountId}/properties`);
        this.semanticGscProps = data.properties || [];
      } catch (e) { this.error = e.message; }
      this.gscPropsLoading = false;
    },

    async fetchGscData() {
      if (!this.job || !this.semanticGscAccountId || !this.semanticGscProp) return;
      this.gscFetchLoading = true;
      this.gscFetchResult = null;
      try {
        this.gscFetchResult = await api(`/jobs/${this.job.id}/semantic/fetch-gsc`, {
          method: 'POST',
          body: JSON.stringify({
            gsc_account_id: this.semanticGscAccountId,
            property_url: this.semanticGscProp,
            days: parseInt(this.semanticGscDays) || 90,
          }),
        });
      } catch (e) { this.error = e.message; }
      this.gscFetchLoading = false;
    },

    async uploadGscAccount() {
      if (!this.gscUploadFile || !this.gscUploadName.trim()) return;
      try {
        const text = await this.gscUploadFile.text();
        const json = JSON.parse(text);
        await api('/semantic/gsc-accounts', {
          method: 'POST',
          body: JSON.stringify({ name: this.gscUploadName.trim(), credentials_json: json }),
        });
        this.gscAccounts = await api('/semantic/gsc-accounts');
        this.showGscUploadModal = false;
        this.gscUploadName = '';
        this.gscUploadFile = null;
        this.showToast('Cuenta GSC guardada');
      } catch (e) { this.error = e.message; }
    },

    exportSemanticCsv() {
      if (!this.job) return;
      window.open(`${API}/jobs/${this.job.id}/semantic/export`, '_blank');
    },

    // ------- URLs -------
    async loadUrls() {
      this.loading = true;
      try {
        let f = '';
        if (this.urlFilters.status_group) f += `&status_group=${this.urlFilters.status_group}`;
        if (this.urlFilters.is_internal)  f += `&is_internal=${this.urlFilters.is_internal}`;
        if (this.urlFilters.resource_type) f += `&resource_type=${this.urlFilters.resource_type}`;
        const data = await api(`/jobs/${this.job.id}/urls?page=${this.urlsPage}&page_size=50${f}`);
        this.urls = data.items;
        this.urlsPages = data.pages;
        this.urlsTotal = data.total;
      } catch (e) { this.error = e.message; }
      this.loading = false;
    },

    applyUrlFilters() { this.urlsPage = 1; this.loadUrls(); },
    setUrlsPage(p) { if (p >= 1 && p <= this.urlsPages) { this.urlsPage = p; this.loadUrls(); } },

    // ------- Detalle de URL individual -------
    async openUrlDetail(urlId) {
      this.urlDetailLoading = true;
      this.urlDetailTab = 'seo';
      this.urlDetail = null;
      if (this.view === 'explorer') { this.urlDetailDesde = 'explorer'; this._unbindExplorerKeyNav(); }
      else if (this.view === 'detail') this.urlDetailDesde = 'detail';
      this.urlDetailId = urlId;
      this.view = 'url-detail';
      try {
        this.urlDetail = await api(`/jobs/${this.job.id}/urls/${urlId}`);
      } catch (e) { this.error = e.message; }
      this.urlDetailLoading = false;
      window.scrollTo({ top: 0, behavior: 'smooth' });
      this.$nextTick(() => lucide.createIcons());
    },

    backToDetail() {
      this.urlDetail = null;
      this.urlDetailId = null;
      if (this.urlDetailDesde === 'explorer') {
        this.view = 'explorer';
        this._bindExplorerKeyNav();
        return;
      }
      this.view = 'detail';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },

    // ------- Problemas -------
    async loadIssues() {
      this.loading = true;
      try {
        let f = '';
        if (this.issueFilters.severity)   f += `&severity=${this.issueFilters.severity}`;
        if (this.issueFilters.issue_type) f += `&issue_type=${this.issueFilters.issue_type}`;
        // El segmento elegido en la vista por tipos sigue mandando en la lista:
        // si no, el recuento de la tarjeta y la lista hablarian de cosas distintas.
        if (this.issuesSegmento) f += `&segmento=${encodeURIComponent(this.issuesSegmento)}`;
        const data = await api(`/jobs/${this.job.id}/issues?page=${this.issuesPage}&page_size=50${f}`);
        this.issues = data.items;
        this.issuesPages = data.pages;
        this.issuesTotal = data.total;
      } catch (e) { this.error = e.message; }
      this.loading = false;
    },

    async loadIssuesResumen() {
      if (!this.job) return;
      try {
        // El % por segmento (decision 125) va siempre: son tres consultas
        // agrupadas. Con un segmento elegido, solo las paginas de ese segmento.
        const q = new URLSearchParams({ por_segmento: 'true', segmentos_max: '8' });
        if (this.issuesSegmento) q.set('segmento', this.issuesSegmento);
        if (!this.segmentosJob) this.cargarSegmentos();
        this.issuesResumen = await api(`/jobs/${this.job.id}/issues/resumen?${q}`);
      } catch (e) { this.error = e.message; this.issuesResumen = []; }
    },

    resumenIssues(severidad) {
      const r = this.issuesResumen || [];
      return severidad ? r.filter(x => x.severity === severidad) : r;
    },

    verTipoDeIncidencia(tipo, severidad) {
      this.issueFilters.issue_type = tipo;
      this.issueFilters.severity = severidad || '';
      this.issuesVista = 'lista';
      this.applyIssueFilters();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },

    volverAGrupos() {
      this.issueFilters.issue_type = '';
      this.issuesVista = 'tipos';
      this.issues = [];
    },

    applyIssueFilters() { this.issuesPage = 1; this.loadIssues(); },
    setIssuesPage(p) { if (p >= 1 && p <= this.issuesPages) { this.issuesPage = p; this.loadIssues(); } },

    // ------- Enlaces -------
    async loadLinks() {
      this.loading = true;
      try {
        const data = await api(`/jobs/${this.job.id}/links?page=${this.linksPage}&page_size=50`);
        this.links = data.items;
        this.linksPages = data.pages;
        this.linksTotal = data.total;
      } catch (e) { this.error = e.message; }
      this.loading = false;
    },

    setLinksPage(p) { if (p >= 1 && p <= this.linksPages) { this.linksPage = p; this.loadLinks(); } },

    // ------- Limpieza -------
    // Wrapper propio (en lugar de api()) para poder mostrar el detalle de los
    // errores 422 de FastAPI, que llegan como array de objetos y no como string.
    async _cleaningApi(path, opts = {}) {
      const res = await fetch(`${API}/jobs/${this.job.id}/cleaning${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        let msg = err.detail;
        if (Array.isArray(msg)) msg = msg.map(d => d.msg || JSON.stringify(d)).join(' | ');
        throw new Error(msg || 'Error en la peticion');
      }
      return res.json();
    },

    loadCleaning() {
      if (!this.job) return;
      this.loadCleaningGroups();
      this.loadCleaningRulesets();
    },

    async loadCleaningGroups() {
      this.cleaningGroupsLoading = true;
      this.cleaningError = null;
      try {
        this.cleaningGroups = await this._cleaningApi('/groups');
      } catch (e) { this.cleaningError = e.message; }
      this.cleaningGroupsLoading = false;
      this.$nextTick(() => lucide.createIcons());
    },

    async loadCleaningRulesets() {
      try {
        this.cleaningRulesets = await this._cleaningApi('/rulesets');
      } catch (e) { this.cleaningError = e.message; }
    },

    selectCleaningGroup(g) {
      this.cleaningSelectedGroup = g.group_key;
      this.cleaningPreview = null;
      this.cleaningMessage = null;
      this.$nextTick(() => lucide.createIcons());
    },

    addCleaningRule() { this.cleaningRules.push({ type: 'line_contains', value: '' }); },
    removeCleaningRule(i) { this.cleaningRules.splice(i, 1); },

    _cleaningValidRules() {
      return this.cleaningRules.some(r => r.value.trim() !== '');
    },

    _cleaningPayload(withRules = true) {
      const payload = { group_key: this.cleaningSelectedGroup };
      if (this.cleaningUrlRegex.trim()) payload.url_regex = this.cleaningUrlRegex.trim();
      if (withRules) {
        payload.rules = this.cleaningRules
          .filter(r => r.value.trim() !== '')
          .map(r => ({ type: r.type, value: r.value }));
        payload.targets = this.cleaningTargets;
      }
      return payload;
    },

    async previewCleaning() {
      if (!this._cleaningValidRules()) { this.cleaningError = 'Añade al menos una regla con valor'; return; }
      this.cleaningPreviewLoading = true;
      this.cleaningError = null;
      this.cleaningMessage = null;
      try {
        this.cleaningPreview = await this._cleaningApi('/preview', {
          method: 'POST',
          body: JSON.stringify({ ...this._cleaningPayload(), sample_size: 5 }),
        });
      } catch (e) { this.cleaningError = e.message; }
      this.cleaningPreviewLoading = false;
      this.$nextTick(() => lucide.createIcons());
    },

    async applyCleaning() {
      if (!this._cleaningValidRules()) { this.cleaningError = 'Añade al menos una regla con valor'; return; }
      const group = this.cleaningGroups.find(g => g.group_key === this.cleaningSelectedGroup);
      const pages = group ? fmt.num(group.pages) : '?';
      if (!await this.preguntar({ titulo: 'Aplicar la limpieza',
        mensaje: `Se aplican las reglas al grupo «${this.cleaningSelectedGroup}» (${pages} páginas). Los originales se conservan y se puede revertir.`, aceptar: 'Aplicar' })) return;
      this.cleaningApplying = true;
      this.cleaningError = null;
      this.cleaningMessage = null;
      try {
        const res = await this._cleaningApi('/apply', {
          method: 'POST',
          body: JSON.stringify(this._cleaningPayload()),
        });
        this.cleaningMessage = `Limpieza aplicada: ${fmt.num(res.pages_updated)} páginas actualizadas, ${fmt.num(res.total_chars_removed)} caracteres eliminados`
          + (res.pages_skipped_safety ? ` (${fmt.num(res.pages_skipped_safety)} saltadas por la válvula de seguridad)` : '');
        this.showToast('Limpieza aplicada');
        this.cleaningPreview = null;
        await Promise.all([this.loadCleaningGroups(), this.loadCleaningRulesets()]);
      } catch (e) { this.cleaningError = e.message; }
      this.cleaningApplying = false;
    },

    async revertCleaning() {
      if (!await this.preguntar({ titulo: 'Revertir la limpieza',
        mensaje: `Se restaura el contenido original del grupo «${this.cleaningSelectedGroup}».`, aceptar: 'Revertir' })) return;
      this.cleaningReverting = true;
      this.cleaningError = null;
      this.cleaningMessage = null;
      try {
        const res = await this._cleaningApi('/revert', {
          method: 'POST',
          body: JSON.stringify(this._cleaningPayload(false)),
        });
        this.cleaningMessage = `Limpieza revertida: ${fmt.num(res.pages_updated)} páginas restauradas`;
        this.showToast('Limpieza revertida', 'info');
        this.cleaningPreview = null;
        await Promise.all([this.loadCleaningGroups(), this.loadCleaningRulesets()]);
      } catch (e) { this.cleaningError = e.message; }
      this.cleaningReverting = false;
    },

    // ------- Exportar -------
    exportCSV() {
      if (!this.job) return;
      window.open(`${API}/jobs/${this.job.id}/export`, '_blank');
    },

    exportLinksCSV() {
      if (!this.job) return;
      window.open(`${API}/jobs/${this.job.id}/links/export`, '_blank');
    },

    exportContentCSV() {
      if (!this.job) return;
      window.open(`${API}/jobs/${this.job.id}/content/export`, '_blank');
    },

    exportBackup() {
      if (!this.job) return;
      window.open(`${API}/jobs/${this.job.id}/backup`, '_blank');
    },

    // ------- Reanudar job fallido / cancelado -------
    showResumeModal: false,
    resuming: false,
    resumeForm: {
      max_runtime_hours: 6,
      autothrottle_enabled: true,
      concurrent_requests_per_domain: 8,
      concurrent_requests: 32,
    },

    openResumeDialog() {
      if (!this.job) return;
      const cfg = this.job.config || {};
      const cb = cfg.crawl_behavior || {};
      this.resumeForm = {
        max_runtime_hours: cb.max_runtime_hours || 6,
        autothrottle_enabled: cb.autothrottle_enabled !== false,
        concurrent_requests_per_domain: cfg.concurrent_requests_per_domain || 8,
        concurrent_requests: cfg.concurrent_requests || 32,
      };
      this.showResumeModal = true;
      this.$nextTick(() => lucide.createIcons());
    },

    async confirmResume() {
      if (!this.job || this.resuming) return;
      this.resuming = true;
      try {
        const overrides = {
          concurrent_requests: parseInt(this.resumeForm.concurrent_requests) || 32,
          concurrent_requests_per_domain: parseInt(this.resumeForm.concurrent_requests_per_domain) || 8,
          crawl_behavior: {
            max_runtime_hours: parseInt(this.resumeForm.max_runtime_hours) || 6,
            autothrottle_enabled: !!this.resumeForm.autothrottle_enabled,
          },
        };
        this.job = await api(`/jobs/${this.job.id}/resume`, {
          method: 'POST',
          body: JSON.stringify(overrides),
        });
        this.showResumeModal = false;
        this.showToast('Rastreo reanudado', 'info');
        // Restart progress polling
        this._startProgress();
      } catch (e) {
        this.avisar('No se pudo reanudar', e.message);
      }
      this.resuming = false;
    },

    // ------- Analisis enriquecido (#40, decision 85) -------
    // Lo unico de la interfaz que gasta dinero. El dialogo estima SIEMPRE
    // antes de dejar lanzar, y el token va en la cabecera de la peticion de
    // lanzar y en ningun otro sitio: sessionStorage (muere con la pestaña),
    // nunca localStorage ni la URL.
    enriqAbierto: false,
    enriqCatalogo: [],
    enriqLanzable: false,
    enriqForm: { modulos: [], tipo: 'todas', carpeta: '', max_urls: 500, presupuesto: 0.01 },
    enriqEstimacion: null,
    enriqEstimando: false,
    enriqLanzando: false,
    enriqError: null,
    enriqToken: '',
    enriqRuns: [],
    enriqRun: null,
    enriqDecisiones: null,
    enriqFiltro: { pregunta: null, respuesta: null, page: 1 },
    enriqTimer: null,

    _alcanceEnriq() {
      const f = this.enriqForm;
      if (f.tipo === 'carpeta') return { carpeta: (f.carpeta || '').trim() || null };
      if (f.tipo === 'max') return { max_urls: parseInt(f.max_urls) || null };
      if (f.tipo === 'muestra') return { muestra: true, max_urls: parseInt(f.max_urls) || 500 };
      return {};
    },

    async abrirEnriquecido() {
      if (!this.job || this.job.status !== 'completed') return;
      this.enriqError = null;
      this.enriqEstimacion = null;
      try { this.enriqToken = sessionStorage.getItem('enrichment-token') || ''; } catch { this.enriqToken = ''; }
      try {
        const cat = await api('/enrichment/modulos');
        this.enriqCatalogo = cat.modulos;
        this.enriqLanzable = cat.lanzamiento_activado;
      } catch (e) { this.enriqError = e.message; return; }
      // Sin nada marcado: que el coste aparezca solo cuando alguien elige.
      // Con un unico modulo se marca para que la estimacion salga de entrada.
      if (!this.enriqForm.modulos.length && this.enriqCatalogo.length === 1) {
        this.enriqForm.modulos = [this.enriqCatalogo[0].nombre];
      }
      this.enriqAbierto = true;
      this.$nextTick(() => lucide.createIcons());
      this.estimarEnriquecido();
    },

    alternarModuloEnriq(nombre, marcado) {
      const m = this.enriqForm.modulos.filter(n => n !== nombre);
      if (marcado) m.push(nombre);
      this.enriqForm.modulos = m;
      this.estimarEnriquecido();
    },

    async estimarEnriquecido() {
      if (!this.job) return;
      this.enriqError = null;
      if (!this.enriqForm.modulos.length) { this.enriqEstimacion = null; return; }
      this.enriqEstimando = true;
      try {
        const e = await api(`/jobs/${this.job.id}/enrichment/estimate`, {
          method: 'POST',
          body: JSON.stringify({ modulos: this.enriqForm.modulos, alcance: this._alcanceEnriq() }),
        });
        this.enriqEstimacion = e;
        this.enriqForm.presupuesto = e.presupuesto_sugerido_usd;
      } catch (e) {
        this.enriqEstimacion = null;
        this.enriqError = e.message;
      }
      this.enriqEstimando = false;
    },

    async lanzarEnriquecido() {
      if (!this.job || this.enriqLanzando || !this.enriqEstimacion) return;
      this.enriqLanzando = true;
      this.enriqError = null;
      const token = (this.enriqToken || '').trim();
      try {
        const run = await api(`/jobs/${this.job.id}/enrichment`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Enrichment-Token': token },
          body: JSON.stringify({
            modulos: this.enriqForm.modulos,
            alcance: this._alcanceEnriq(),
            presupuesto_usd: parseFloat(this.enriqForm.presupuesto) || 0,
          }),
        });
        try { sessionStorage.setItem('enrichment-token', token); } catch {}
        this.enriqAbierto = false;
        this.showToast('Análisis enriquecido en cola', 'info');
        this.detailTab = 'enriquecido';
        await this.cargarEjecuciones(run.id);
      } catch (e) {
        // Un token rechazado no se guarda: que la proxima vez se pida otra vez.
        if (/token/i.test(e.message)) { try { sessionStorage.removeItem('enrichment-token'); } catch {} }
        this.enriqError = e.message;
      }
      this.enriqLanzando = false;
    },

    async cargarEjecuciones(elegir = null) {
      if (!this.job) return;
      try {
        this.enriqRuns = await api(`/jobs/${this.job.id}/enrichment`);
      } catch { this.enriqRuns = []; return; }
      const id = elegir || (this.enriqRun && this.enriqRun.id) || (this.enriqRuns[0] && this.enriqRuns[0].id);
      if (id && this.detailTab === 'enriquecido') await this.verEjecucion(id);
    },

    async verEjecucion(id) {
      if (!this.job || !id) return;
      if (!this.enriqRun || this.enriqRun.id !== id) {
        this.enriqDecisiones = null;
        this.enriqFiltro = { pregunta: null, respuesta: null, page: 1 };
      }
      try {
        this.enriqRun = await api(`/jobs/${this.job.id}/enrichment/${id}`);
      } catch (e) { this.error = e.message; return; }
      this._pararEnriq();
      if (['pendiente', 'en_curso', 'cancelando'].includes(this.enriqRun.estado)) {
        this.enriqTimer = setInterval(async () => {
          if (!this.job || !this.enriqRun) return this._pararEnriq();
          try {
            const r = await api(`/jobs/${this.job.id}/enrichment/${this.enriqRun.id}`);
            this.enriqRun = r;
            const i = this.enriqRuns.findIndex(x => x.id === r.id);
            if (i >= 0) this.enriqRuns[i] = { ...this.enriqRuns[i], estado: r.estado, gastado_usd: r.gastado_usd };
            if (!['pendiente', 'en_curso', 'cancelando'].includes(r.estado)) this._pararEnriq();
          } catch {}
        }, 3000);
      }
      this.$nextTick(() => lucide.createIcons());
    },

    _pararEnriq() {
      if (this.enriqTimer) { clearInterval(this.enriqTimer); this.enriqTimer = null; }
    },

    async cancelarEnriquecido() {
      if (!this.job || !this.enriqRun) return;
      try {
        this.enriqRun = await api(`/jobs/${this.job.id}/enrichment/${this.enriqRun.id}/cancel`, { method: 'PATCH' });
        this.showToast('Cancelación pedida: lo ya pagado se conserva', 'info');
        await this.cargarEjecuciones(this.enriqRun.id);
      } catch (e) { this.showToast(e.message, 'error'); }
    },

    async cargarDecisionesEnriq(pregunta = undefined, respuesta = undefined) {
      if (!this.job || !this.enriqRun) return;
      if (pregunta !== undefined) this.enriqFiltro = { pregunta, respuesta, page: 1 };
      const f = this.enriqFiltro;
      const q = new URLSearchParams({ page: f.page, page_size: 50 });
      if (f.pregunta) q.set('pregunta', f.pregunta);
      if (f.respuesta) q.set('respuesta', f.respuesta);
      try {
        this.enriqDecisiones = await api(`/jobs/${this.job.id}/enrichment/${this.enriqRun.id}/decisions?${q}`);
      } catch (e) { this.error = e.message; }
    },

    // ------- Importar backup -------
    showImportModal: false,
    importFile: null,
    importing: false,
    importError: null,
    importResult: null,
    importPreserveId: false,

    closeImportModal() {
      this.showImportModal = false;
      this.importFile = null;
      this.importError = null;
      this.importResult = null;
      this.importPreserveId = false;
      // Refresh jobs list if we imported something
      if (this.view === 'jobs') this.loadJobs();
    },

    async submitImport() {
      if (!this.importFile) return;
      this.importing = true;
      this.importError = null;
      this.importResult = null;
      try {
        const formData = new FormData();
        formData.append('file', this.importFile);
        const params = this.importPreserveId ? '?preserve_job_id=true' : '';
        const res = await fetch(`${API}/jobs/import${params}`, {
          method: 'POST',
          body: formData,
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(err.detail || 'Error al importar');
        }
        this.importResult = await res.json();
        this.showToast('Backup importado correctamente');
        // Refresh jobs list
        if (this.view === 'jobs') this.loadJobs();
      } catch (e) {
        this.importError = e.message;
      }
      this.importing = false;
    },

    // ------- Helpers -------
    statusColor(s) {
      return { pending:'badge-pending', running:'badge-running', analyzing:'badge-running', completed:'badge-completed', failed:'badge-failed', cancelled:'badge-cancelled', deleting:'badge-cancelled' }[s] || 'badge-pending';
    },

    barColor(group) {
      return { '2xx':'var(--green)', '3xx':'var(--blue)', '4xx':'var(--orange)', '5xx':'var(--red)' }[group] || 'var(--text-dim)';
    },

    maxStatusCount() {
      if (!this.stats) return 1;
      return Math.max(1, ...this.stats.urls_by_status_group.map(s => s.count));
    },

    maxIssueCount() {
      if (!this.stats) return 1;
      return Math.max(1, ...this.stats.issues_by_type.map(i => i.count));
    },

    issueBarColor(sev) {
      return { error:'var(--red)', warning:'var(--orange)', info:'var(--blue)' }[sev] || 'var(--text-dim)';
    },

    erroresDelJob() {
      if (!this.stats) return 0;
      return this.stats.issues_by_type.filter(i => i.severity === 'error').reduce((a, i) => a + i.count, 0);
    },

    get issueTypes() {
      if (!this.stats) return [];
      return [...new Set(this.stats.issues_by_type.map(i => i.issue_type))];
    },

    get issuesSorted() {
      if (!this.stats) return [];
      return [...this.stats.issues_by_type].sort((a, b) => b.count - a.count);
    },

    crawledPercent() {
      if (!this.progress) return 0;
      const hechas = this.progress.crawled_count || 0;
      const cola = this.progress.pending_count;
      // Sin tope de URLs el porcentaje se mide contra el trabajo real que
      // queda (rastreadas / (rastreadas + en cola)), no contra un numero
      // inventado de antemano. Con tope se sigue midiendo contra el, porque
      // ahi si marca el final del rastreo.
      const tope = this.job?.config?.max_urls;
      if (tope) return Math.min(100, (hechas / tope) * 100);
      if (cola === null || cola === undefined) return 0;
      const total = hechas + cola;
      return total > 0 ? Math.min(100, (hechas / total) * 100) : 0;
    },

    // URL detail helpers
    headingIndent(tag) {
      const level = parseInt(tag.replace('h', '')) || 1;
      return (level - 1) * 1.25;
    },

    headingColor(tag) {
      const colors = { h1: 'var(--accent)', h2: 'var(--green)', h3: 'var(--blue)', h4: 'var(--orange)', h5: 'var(--text-dim)', h6: 'var(--text-dim)' };
      return colors[tag] || 'var(--text-dim)';
    },

    renderMarkdown(md) {
      if (!md) return '';
      try {
        return marked.parse(md);
      } catch {
        return '<pre>' + md.replace(/</g, '&lt;') + '</pre>';
      }
    },

    securityScore() {
      if (!this.urlDetail?.security) return null;
      const s = this.urlDetail.security;
      // `has_unsafe_crossorigin` ya no cuenta: desde 2021 los navegadores
      // aplican `noopener` por defecto a target="_blank". El dato se sigue
      // guardando, pero no es un fallo que arreglar.
      let score = 0, total = 6;
      if (s.is_https) score++;
      if (!s.has_mixed_content) score++;
      if (s.has_hsts) score++;
      if (s.has_csp) score++;
      if (s.has_x_content_type_options) score++;
      if (s.has_x_frame_options) score++;
      return { score, total, pct: Math.round(score / total * 100) };
    },

    fmt,

    // ------- Reinit Lucide icons -------
    reinitIcons() {
      this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
    },

    // ===================================================================
    //                         EXPLORER VIEW
    // ===================================================================
    expTab: 'all',
    expSearch: '',
    expSort: null,
    expSortDir: 'asc',
    expUrls: [],
    expPage: 1,
    expPages: 1,
    expTotal: 0,
    expPageSize: 100,
    expLoading: false,
    expSelectedUrl: null,
    expSelectedIndex: -1,
    expUrlDetail: null,
    expDetailTab: 'resumen',
    expDetailOpen: false,
    expBadgeCounts: {},
    expColumnsByTab: {},        // { tabKey: [colKey, ...] } — persisted column selection per tab
    expColumnPickerOpen: false,
    expColumnFilters: {},       // { columnKey: { op: '>=', value: '0.5' } | { value: 'foo' } | { op: 'vacio' } }
    expProblema: '',            // issue_type (o varios separados por comas) del filtro «Con el problema…»
    expGravedad: '',            // severity del mismo filtro
    expBuscarColumna: '',       // buscador del selector de columnas
    expAgrupar: false,          // vista agrupada por plantilla (forma de ruta)
    expVistas: [],              // vistas guardadas del cliente del rastreo
    expPlantillas: null,        // respuesta de /urls/plantillas
    expPlantilla: '',           // plantilla elegida: filtra la tabla
    expAgruparPor: 'plantilla', // 'plantilla' (forma de ruta) o 'segmento' (decision 125)
    expSegmento: '',            // segmento elegido: filtra la tabla (?segmento=)
    segmentosJob: null,         // GET /jobs/{id}/segmentos
    expAMedida: [],             // reglas a medida del censo (decision 104): [{key, nombre, clase, etiqueta}]
    expAvisoRegla: '',          // 422 de una regla que este censo no tiene (vista guardada de otro)
    resumenAMedida: null,       // GET /jobs/{id}/extraccion
    EXP_GRUPOS_COLUMNAS,
    _expSearchTimer: null,
    _expFilterTimer: null,
    _expKeyHandler: null,

    // ---- Extraccion y busqueda a medida (#34, decision 104) ----
    // Las columnas `x.<nombre>` se inyectan en EXP_COLUMN_DEFS al abrir el
    // censo: asi todo lo que ya lee EXP_COLUMN_DEFS[col] (cabecera, filtro,
    // orden, celda, CSV) vale sin tocarlo. Las del censo anterior se quitan.
    _registrarAMedida(job) {
      for (const k of Object.keys(EXP_COLUMN_DEFS)) if (k.startsWith('x.')) delete EXP_COLUMN_DEFS[k];
      const defs = _defsAMedida(job);
      Object.assign(EXP_COLUMN_DEFS, defs);
      this.expAMedida = Object.entries(defs).map(([key, d]) => ({ key, nombre: d.nombre, clase: d.clase, etiqueta: d.label }));
      const grupos = this.EXP_GRUPOS_COLUMNAS || EXP_GRUPOS_COLUMNAS;
      const grupo = grupos.find(g => g[0] === "A medida");
      if (grupo) grupo[1] = Object.keys(defs);
      else grupos.push(["A medida", Object.keys(defs)]);
      const tab = EXP_TABS.find(t => t.key === 'a_medida');
      if (tab) tab.columns = ['url', 'status_code', ...Object.keys(defs)];
    },

    async cargarResumenAMedida() {
      this.resumenAMedida = null;
      if (!this.job || !this.expAMedida.length) return;
      try { this.resumenAMedida = await api(`/jobs/${this.job.id}/extraccion`); } catch (_) { /* sin resumen */ }
    },

    // Desde la ficha del job o la franja del Explorador: la pestaña a medida
    // con un filtro ya puesto. `op`: 'vacio' (sin coincidencia), 'no' / 'si'
    // (busqueda) o 'estado:<estado>'.
    verAMedida(nombre, op) {
      const key = 'x.' + nombre;
      let f = null;
      if (op === 'vacio') f = { op: 'vacio' };
      else if (op === 'si' || op === 'no') f = { value: op === 'si' ? 'true' : 'false' };
      else if (op && op.startsWith('estado:')) f = { op: 'estado', value: op.slice(7) };
      // El chip SUSTITUYE los filtros, desde la ficha y desde el Explorador:
      // su cifra es la del censo entero, y sumado a otro filtro (AND) la
      // tabla no la daba. Pulsar el chip activo lo quita.
      const activo = f && this.chipAMedidaActivo(nombre, op);
      if (this.view !== 'explorer') this.openExplorer({ tab: 'a_medida', filtros: f ? { [key]: f } : {} });
      else {
        this.expTab = 'a_medida';
        this.expColumnFilters = (f && !activo) ? { [key]: f } : {};
        this.expPage = 1;
        this.loadExplorerUrls();
      }
    },

    chipAMedidaActivo(nombre, op) {
      const filtros = Object.entries(this.expColumnFilters || {}).filter(([, v]) => v);
      if (this.view !== 'explorer' || filtros.length !== 1) return false;
      const [k, f] = filtros[0];
      if (k !== 'x.' + nombre) return false;
      if (op === 'vacio') return f.op === 'vacio';
      if (op === 'si' || op === 'no') return !f.op && f.value === (op === 'si' ? 'true' : 'false');
      if (op && op.startsWith('estado:')) return f.op === 'estado' && f.value === op.slice(7);
      return false;
    },

    // Un 422 por una regla que el censo no tiene: se quitan los filtros y el
    // orden `x.*` en vez de enseñar el censo entero como si nada.
    quitarFiltrosAMedida() {
      const f = { ...this.expColumnFilters };
      for (const k of Object.keys(f)) if (k.startsWith('x.') && !EXP_COLUMN_DEFS[k]) delete f[k];
      this.expColumnFilters = f;
      if (this.expSort && this.expSort.startsWith('x.') && !EXP_COLUMN_DEFS[this.expSort]) this.expSort = null;
      this.expAvisoRegla = '';
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    getExpCellTitle(row, col) {
      const def = EXP_COLUMN_DEFS[col];
      if (def && def.tip) { try { return def.tip(row, this.job); } catch { return ''; } }
      return this.getExpCellValue(row, col);
    },

    // `preajuste`: { tab, problema, gravedad, buscar, filtros, orden, dir, pagina }
    // Lo usan la ruta (#/rastreos/<id>/explorador?...) y «Ver en el Explorador»
    // de Problemas.
    openExplorer(preajuste = {}) {
      this.view = 'explorer';
      this.expTab = preajuste.tab || 'all';
      this.expSearch = preajuste.buscar || '';
      this.expSort = preajuste.orden || null;
      this.expSortDir = preajuste.dir || 'asc';
      this.expPage = preajuste.pagina || 1;
      this.expProblema = preajuste.problema || '';
      this.expGravedad = preajuste.gravedad || '';
      this.expAgrupar = !!preajuste.agrupar;
      this.expAgruparPor = preajuste.agruparPor || 'plantilla';
      this.expPlantilla = preajuste.plantilla || '';
      this.expSegmento = preajuste.segmento || '';
      if (!this.segmentosJob) this.cargarSegmentos();
      this.expPlantillas = null;
      this.expDetailOpen = false;
      this.expSelectedUrl = null;
      this.expSelectedIndex = -1;
      this.expUrlDetail = null;
      this.expColumnFilters = preajuste.filtros || {};
      this.expColumnPickerOpen = false;
      this.expColumnsByTab = this._loadExpColumnPrefs();
      if ((this.expProblema || EXP_TABS.find(t => t.key === this.expTab)?.ambito) && !this.issuesResumen) this.loadIssuesResumen();
      this.cargarVistas();
      this._bindExplorerKeyNav();
      this.loadExplorerBadges();
      this.loadExplorerUrls();
      this.$nextTick(() => lucide.createIcons());
    },

    backFromExplorer() {
      this._unbindExplorerKeyNav();
      this.view = 'detail';
      this.$nextTick(() => lucide.createIcons());
    },

    loadExplorerBadges() {
      if (!this.stats) return;
      const b = {};
      b.all = this.stats.total_urls;
      // Resource types
      for (const rt of this.stats.urls_by_resource_type || []) {
        b[rt.resource_type] = rt.count;
      }
      // Internal / external
      b.internal = this.stats.internal_count || 0;
      b.external = this.stats.external_count || 0;
      // Status groups
      for (const sg of this.stats.urls_by_status_group || []) {
        b[sg.status_group] = sg.count;
      }
      this.expBadgeCounts = b;
    },

    // Cambiar de pestaña ya no borra la busqueda ni los filtros: pasar de
    // «4xx» a «3xx» con el mismo «Inlinks ≥ 1» obligaba a reescribirlo. Los
    // filtros de columnas que la pestaña nueva no muestra siguen aplicandose
    // y se ven en el contador «N filtros», que los quita de golpe.
    setExplorerTab(tab) {
      const antes = EXP_TABS.find(t => t.key === this.expTab);
      this.expTab = tab;
      // Entre vistas de ambito, el problema elegido es de la anterior: fuera.
      if (antes && antes.ambito && this.expProblema && this.expProblema.split(',').every(t => antes.ambito.includes(t))) {
        this.expProblema = '';
      }
      if (EXP_TABS.find(t => t.key === tab)?.ambito && !this.issuesResumen) this.loadIssuesResumen();
      this.expPage = 1;
      this.expDetailOpen = false;
      this.expSelectedUrl = null;
      this.expSelectedIndex = -1;
      this.expColumnPickerOpen = false;
      this.loadExplorerUrls();
    },

    // ---- Vistas guardadas por cliente (#131) ----
    // Una vista es la consulta de la URL (#/rastreos/<id>/explorador?...),
    // guardada en el cliente: vale para cualquier censo suyo, y asi se repite
    // el mismo analisis cada mes.
    async cargarVistas() {
      this.expVistas = [];
      if (!this.job || !this.job.cliente_id) return;
      try { this.expVistas = await api(`/clients/${this.job.cliente_id}/vistas`); } catch (_) { /* sin vistas */ }
    },

    async guardarVista() {
      if (!this.job || !this.job.cliente_id) return;
      const nombre = await this.preguntar({
        titulo: 'Guardar esta vista',
        mensaje: `Se guarda en el cliente «${this.job.cliente_nombre || ''}» y se podrá abrir en cualquier censo suyo: pestaña, problema, filtros, orden y agrupación.`,
        aceptar: 'Guardar', pedir: 'Nombre de la vista',
        valor: (EXP_TABS.find(t => t.key === this.expTab) || {}).label || '',
      });
      if (!nombre) return;
      try {
        await api(`/clients/${this.job.cliente_id}/vistas`, {
          method: 'POST', body: JSON.stringify({ nombre, consulta: this._estadoExplorador() }),
        });
        await this.cargarVistas();
        this.showToast('Vista guardada');
      } catch (e) { this.avisar('No se pudo guardar', e.message); }
    },

    abrirVista(v) {
      this.openExplorer(this._preajusteExplorador(new URLSearchParams(v.consulta)));
    },

    async borrarVista(v) {
      if (!await this.preguntar({ titulo: `Borrar la vista «${v.nombre}»`, mensaje: 'Se borra para todos los censos del cliente.',
                                 aceptar: 'Borrar', peligro: true })) return;
      try {
        await api(`/clients/${this.job.cliente_id}/vistas/${v.id}`, { method: 'DELETE' });
        await this.cargarVistas();
      } catch (e) { this.avisar('No se pudo borrar', e.message); }
    },

    // ---- Agrupar por plantilla (#131) ----
    // En SEO tecnico se arregla una plantilla, no una URL: los 32 errores 4xx
    // de Lopesan son, en buena parte, dos fallos repetidos por idioma.
    async cargarPlantillas() {
      this.expLoading = true;
      try {
        const q = new URLSearchParams(this._buildExplorerParams());
        q.delete('page'); q.delete('page_size'); q.delete('ligero'); q.delete('plantilla');
        // Siempre con `agrupar`: el uso (api/uso.py) cuenta las dos agrupaciones,
        // y sin el valor por defecto en la URL no se podrian comparar.
        q.set('agrupar', this.expAgruparPor === 'segmento' ? 'segmento' : 'plantilla');
        if (this.expAgruparPor === 'segmento') q.delete('segmento');
        this.expPlantillas = await api(`/jobs/${this.job.id}/urls/plantillas?${q}`);
        this.expTotal = this.expPlantillas.urls;
      } catch (e) { this.error = e.message; }
      this.expLoading = false;
    },

    alternarAgrupar(por = 'plantilla') {
      // El mismo boton apaga; el otro cambia de agrupacion sin apagar.
      this.expAgrupar = !(this.expAgrupar && this.expAgruparPor === por);
      this.expAgruparPor = por;
      this.expPlantillas = null;
      this.expDetailOpen = false;
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    entrarEnPlantilla(forma) {
      if (this.expAgruparPor === 'segmento') {
        this.expSegmento = forma === '(sin segmentar)' ? '' : forma;
      } else this.expPlantilla = forma;
      this.expAgrupar = false;
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    // ---- Segmentos de negocio (decision 125) ----
    // `urls.segmento` lo materializa el analisis con `config.segmentos`; sin
    // reglas, cada URL lleva la forma de su ruta (empieza por «/»).
    async cargarSegmentos() {
      if (!this.job) return;
      try { this.segmentosJob = await api(`/jobs/${this.job.id}/segmentos`); }
      catch (_) { this.segmentosJob = { segmentos: [], reglas: [] }; }
    },

    // Los de regla primero (en su orden) y luego las formas de ruta con mas URLs.
    segmentosParaElegir() {
      const lista = (this.segmentosJob && this.segmentosJob.segmentos) || [];
      return { regla: lista.filter(x => x.de_regla), forma: lista.filter(x => !x.de_regla).slice(0, 60) };
    },

    elegirSegmento(valor) {
      this.expSegmento = valor;
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    async elegirSegmentoProblemas(valor) {
      this.issuesSegmento = valor;
      this.issuesResumen = null;
      if (this.issuesVista === 'lista' || this.issueFilters.issue_type) this.applyIssueFilters();
      await this.loadIssuesResumen();
      this.$nextTick(() => lucide.createIcons());
    },

    quitarPlantilla() {
      this.expPlantilla = '';
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    // Un tramo repetido (`null`) como filtro de URL.
    filtrarPorTramo(tramo) {
      this.expColumnFilters = { ...this.expColumnFilters, url: { value: '/' + tramo } };
      this.expAgrupar = false;
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    // Los tipos de incidencia del ambito de la pestaña, con sus paginas.
    tiposDelAmbito() {
      const tab = EXP_TABS.find(t => t.key === this.expTab);
      if (!tab || !tab.ambito) return [];
      const por = Object.fromEntries((this.issuesResumen || []).map(r => [r.issue_type, r]));
      return tab.ambito.filter(t => por[t]).map(t => por[t]);
    },

    // Gravedad mas alta del ambito: el punto de color de la barra lateral.
    gravedadDelAmbito(tab) {
      if (!tab.ambito || !this.issuesResumen) return '';
      const sev = this.issuesResumen.filter(r => tab.ambito.includes(r.issue_type)).map(r => r.severity);
      return sev.includes('error') ? 'error' : sev.includes('warning') ? 'warning' : sev.includes('info') ? 'info' : 'ok';
    },

    elegirTipoDelAmbito(tipo) {
      const tab = EXP_TABS.find(t => t.key === this.expTab);
      // '' = todas las paginas; '*' = cualquier problema del ambito
      this.expProblema = tipo === '*' ? tab.ambito.join(',') : tipo;
      this.expGravedad = '';
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    // «Con el problema…»: '' | tipo | 'sev:error'
    setExpProblema(valor) {
      if (valor.startsWith('sev:')) { this.expGravedad = valor.slice(4); this.expProblema = ''; }
      else { this.expProblema = valor; this.expGravedad = ''; }
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    expValorProblema() {
      return this.expGravedad ? 'sev:' + this.expGravedad : this.expProblema;
    },

    // Desde un tipo de Problemas: esas URLs, con las columnas que lo explican.
    verProblemaEnExplorador(tipo) {
      this.openExplorer({ problema: tipo, segmento: this.issuesSegmento || '' });
    },

    // El CSV de LO QUE SE VE: mismos filtros, mismo orden, columnas visibles
    // (antes exportaba el censo entero con 83 columnas).
    exportarVista() {
      if (!this.job) return;
      const q = new URLSearchParams(this._buildExplorerParams(false));
      q.delete('page'); q.delete('page_size'); q.delete('ligero');
      q.set('columnas', this.getExpColumns().join(','));
      window.open(`${API}/jobs/${this.job.id}/urls/export?${q}`, '_blank');
    },

    // Todos los enlaces que apuntan a las URLs de esta vista (origen, ancla,
    // posicion, follow): la lista para corregir enlaces a 404 o redirecciones.
    exportarEnlacesEntrantes() {
      if (!this.job) return;
      const q = new URLSearchParams(this._buildExplorerParams(false));
      ['page', 'page_size', 'ligero', 'origenes', 'sort_by', 'sort_dir'].forEach(k => q.delete(k));
      window.open(`${API}/jobs/${this.job.id}/urls/enlaces-entrantes?${q}`, '_blank');
    },

    // Columnas del selector, por grupos y filtradas por el buscador.
    expGruposVisibles() {
      const t = this.expBuscarColumna.trim().toLowerCase();
      return EXP_GRUPOS_COLUMNAS
        .map(([nombre, claves]) => [nombre, claves.filter(k => !t || (EXP_COLUMN_DEFS[k]?.label || k).toLowerCase().includes(t))])
        .filter(([, claves]) => claves.length);
    },

    explorerSort(col) {
      if (this.expSort === col) {
        this.expSortDir = this.expSortDir === 'asc' ? 'desc' : 'asc';
      } else {
        this.expSort = col;
        this.expSortDir = 'asc';
      }
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    explorerSearchInput() {
      clearTimeout(this._expSearchTimer);
      this._expSearchTimer = setTimeout(() => {
        this.expPage = 1;
        this.loadExplorerUrls();
      }, 400);
    },

    /**
     * Build the query string parameters for the /urls endpoint based on tab,
     * sort, search and the inline per-column filters.
     */
    _buildExplorerParams(ligero = true) {
      const tabCfg = EXP_TABS.find(t => t.key === this.expTab) || EXP_TABS[0];
      const params = new URLSearchParams();
      params.set('page', String(this.expPage));
      params.set('page_size', String(this.expPageSize));
      // Sin el texto de cada pagina: era el 89% de 1,6 MB por pagina de la
      // tabla y no lo pinta nadie (el panel lo pide aparte).
      if (ligero) params.set('ligero', 'true');
      // Para medir el uso (api/uso.py): que pestaña se mira. Solo nombres.
      params.set('vista', 'explorador');
      params.set('pestana', this.expTab);
      if (this.expProblema) params.set('issue_type', this.expProblema);
      if (this.expPlantilla) params.set('plantilla', this.expPlantilla);
      if (this.expSegmento) params.set('segmento', this.expSegmento);
      if (this.getExpColumns().some(c => EXP_COLUMN_DEFS[c]?.calculada === 'origenes')) params.set('origenes', 'true');
      if (this.getExpColumns().some(c => EXP_COLUMN_DEFS[c]?.calculada === 'h1')) params.set('h1', 'true');
      if (this.getExpColumns().includes('cambio')) params.set('cambios', 'true');
      const xs = this.getExpColumns().filter(c => c.startsWith('x.') && EXP_COLUMN_DEFS[c]);
      if (xs.length) params.set('a_medida', xs.map(c => c.slice(2)).join(','));
      if (this.expGravedad) params.set('severity', this.expGravedad);
      const vacio = [], conValor = [];
      if (tabCfg.filter) {
        for (const [k, v] of Object.entries(tabCfg.filter)) params.set(k, String(v));
      }
      if (this.expSearch) params.set('search', this.expSearch);
      if (this.expSort) { params.set('sort_by', this.expSort); params.set('sort_dir', this.expSortDir); }

      // Inline column filters
      for (const [colKey, f] of Object.entries(this.expColumnFilters)) {
        if (!f) continue;
        const def = EXP_COLUMN_DEFS[colKey];
        if (!def && colKey.startsWith('x.')) {
          // Una regla que este censo no tiene (vista guardada de otro censo):
          // se manda igual para que la API dé el 422 y salga el aviso con
          // «Quitar el filtro». Descartarla aquí enseñaba el censo ENTERO
          // como si el filtro se hubiera aplicado (decision 104).
          const raw = (f.value ?? '').toString().trim();
          if (f.op === 'vacio') vacio.push(colKey);
          else if (f.op === 'con_valor') conValor.push(colKey);
          else if (f.op === 'estado') params.set(`${colKey}_estado`, raw);
          else if (['>=', '>', '='].includes(f.op)) params.set(`${colKey}_gte`, raw);
          else if (['<=', '<'].includes(f.op)) params.set(`${colKey}_lte`, raw);
          else params.set(colKey, raw);
          continue;
        }
        if (!def || !def.filterKey) continue;
        if (def.aMedida && f.op === 'estado') { if (f.value) params.set(`x.${def.nombre}_estado`, f.value); continue; }
        if (def.vacio && f.op === 'vacio') { vacio.push(def.vacio); continue; }
        if (def.vacio && f.op === 'con_valor') { conValor.push(def.vacio); continue; }
        const raw = (f.value ?? '').toString().trim();
        if (raw === '') continue;
        if (def.type === 'numeric') {
          if (def.exact && (f.op === '=' || !f.op)) {
            // status_code exact match — overrides any tab filter on status_code
            params.set(def.filterKey, raw);
          } else {
            const op = f.op || '>=';
            if (op === '=')        { params.set(`${def.filterKey}_gte`, raw); params.set(`${def.filterKey}_lte`, raw); }
            else if (op === '>=' ) { params.set(`${def.filterKey}_gte`, raw); }
            else if (op === '>')   { const n = parseFloat(raw); if (!isNaN(n)) params.set(`${def.filterKey}_gte`, String(n + 0.00001)); }
            else if (op === '<=')  { params.set(`${def.filterKey}_lte`, raw); }
            else if (op === '<')   { const n = parseFloat(raw); if (!isNaN(n)) params.set(`${def.filterKey}_lte`, String(n - 0.00001)); }
          }
        } else if (def.type === 'boolean') {
          params.set(def.filterKey, raw);
        } else {
          params.set(def.filterKey, raw);
        }
      }
      if (vacio.length) params.set('vacio', vacio.join(','));
      if (conValor.length) params.set('con_valor', conValor.join(','));
      return params.toString();
    },

    async loadExplorerUrls() {
      if (!this.job) return;
      if (this.expAgrupar) return this.cargarPlantillas();
      this.expLoading = true;
      try {
        const params = this._buildExplorerParams();
        this.expAvisoRegla = '';
        const data = await api(`/jobs/${this.job.id}/urls?${params}`);
        this.expUrls = data.items;
        this.expPages = data.pages;
        this.expTotal = data.total;
        // Keep current selection in sync with the new page
        if (this.expSelectedUrl) {
          const idx = this.expUrls.findIndex(u => u.id === this.expSelectedUrl.id);
          this.expSelectedIndex = idx;
          if (idx === -1) {
            this.expDetailOpen = false;
            this.expSelectedUrl = null;
            this.expUrlDetail = null;
          }
        } else {
          this.expSelectedIndex = -1;
        }
      } catch (e) {
        if (/no tiene la regla/.test(e.message || '')) { this.expAvisoRegla = e.message; this.expUrls = []; this.expTotal = 0; }
        else this.error = e.message;
      }
      this.expLoading = false;
    },

    async selectExplorerUrl(u, idx = null) {
      this.expSelectedUrl = u;
      this.expSelectedIndex = idx != null ? idx : this.expUrls.findIndex(x => x.id === u.id);
      this.expDetailOpen = true;
      this.expUrlDetail = null;
      try {
        this.expUrlDetail = await api(`/jobs/${this.job.id}/urls/${u.id}`);
      } catch (e) { this.error = e.message; }
      this.$nextTick(() => lucide.createIcons());
    },

    closeExplorerDetail() {
      this.expDetailOpen = false;
      this.expSelectedUrl = null;
      this.expSelectedIndex = -1;
      this.expUrlDetail = null;
    },

    setExpPage(p) {
      if (p < 1 || p > this.expPages) return;
      this.expPage = p;
      this.loadExplorerUrls();
    },

    /* ---------- column picker ---------- */
    getExpColumns() {
      const stored = this.expColumnsByTab[this.expTab];
      if (stored && stored.length) return stored;
      const tabCfg = EXP_TABS.find(t => t.key === this.expTab);
      return (tabCfg && tabCfg.columns) || EXP_DEFAULT_COLUMNS;
    },

    getExpAllColumnKeys() {
      return Object.keys(EXP_COLUMN_DEFS);
    },

    isExpColumnActive(key) {
      return this.getExpColumns().includes(key);
    },

    toggleExpColumn(key) {
      const current = this.getExpColumns().slice();
      const idx = current.indexOf(key);
      if (idx >= 0) {
        if (current.length <= 1) return; // never leave the table without columns
        current.splice(idx, 1);
      } else {
        current.push(key);
      }
      this.expColumnsByTab = { ...this.expColumnsByTab, [this.expTab]: current };
      this._saveExpColumnPrefs();
    },

    resetExpColumns() {
      const next = { ...this.expColumnsByTab };
      delete next[this.expTab];
      this.expColumnsByTab = next;
      this._saveExpColumnPrefs();
    },

    _expColumnPrefsKey() {
      return `expCols:${this.job?.id || 'global'}`;
    },

    _loadExpColumnPrefs() {
      try {
        const raw = localStorage.getItem(this._expColumnPrefsKey());
        return raw ? JSON.parse(raw) : {};
      } catch { return {}; }
    },

    _saveExpColumnPrefs() {
      try { localStorage.setItem(this._expColumnPrefsKey(), JSON.stringify(this.expColumnsByTab)); }
      catch (_) { /* localStorage full / disabled */ }
    },

    /* ---------- inline column filters ---------- */
    expColumnFilterInput(colKey, patch) {
      const cur = this.expColumnFilters[colKey] || {};
      const next = { ...cur, ...patch };
      this.expColumnFilters = { ...this.expColumnFilters, [colKey]: next };
      clearTimeout(this._expFilterTimer);
      this._expFilterTimer = setTimeout(() => {
        this.expPage = 1;
        this.loadExplorerUrls();
      }, 400);
    },

    expClearColumnFilter(colKey) {
      const next = { ...this.expColumnFilters };
      delete next[colKey];
      this.expColumnFilters = next;
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    expClearAllColumnFilters() {
      this.expColumnFilters = {};
      this.expProblema = ''; this.expGravedad = ''; this.expPlantilla = '';
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    expActiveFilterCount() {
      return Object.values(this.expColumnFilters).filter(f =>
        f && (f.op === 'vacio' || f.op === 'con_valor' || (f.value != null && f.value.toString().trim() !== ''))
      ).length + (this.expProblema || this.expGravedad ? 1 : 0) + (this.expPlantilla ? 1 : 0);
    },

    /**
     * Default operator shown when a numeric column has no filter yet.
     * status_code is "exact" so it defaults to '='.
     */
    expDefaultOp(col) {
      const def = EXP_COLUMN_DEFS[col];
      if (def?.type === 'string') return 'contiene';
      return def?.exact ? '=' : '>=';
    },

    expCurrentOp(col) {
      return this.expColumnFilters[col]?.op || this.expDefaultOp(col);
    },

    /** Dynamic placeholder so the user knows what the input expects right now. */
    expFilterPlaceholder(col) {
      const def = EXP_COLUMN_DEFS[col];
      if (!def) return '';
      if (def.type === 'numeric') {
        const op = this.expCurrentOp(col);
        return ({
          '>=': 'al menos…',
          '>':  'mayor que…',
          '=':  def.exact ? 'igual a… (301,302)' : 'igual a…',
          '<=': 'máximo…',
          '<':  'menor que…',
        })[op] || 'núm';
      }
      if (def.type === 'string') {
        const op = this.expCurrentOp(col);
        return op === 'vacio' ? '(vacío)' : op === 'con_valor' ? '(con valor)' : 'contiene… (a|b, !a)';
      }
      return '';
    },

    /** Tooltip for the operator selector explaining each option. */
    expOpTooltip(col) {
      const op = this.expCurrentOp(col);
      const colLabel = EXP_COLUMN_DEFS[col]?.label || col;
      const human = ({
        '>=': 'al menos',
        '>':  'mayor que',
        '=':  'igual a',
        '<=': 'como máximo',
        '<':  'menor que',
      })[op] || op;
      return `${colLabel} ${human}…  (clic para cambiar operador)`;
    },

    /* ---------- keyboard navigation ---------- */
    _bindExplorerKeyNav() {
      if (this._expKeyHandler) return;
      this._expKeyHandler = (e) => {
        if (this.view !== 'explorer') return;
        // Don't hijack typing inside inputs/textareas/contenteditable
        const t = e.target;
        if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
        if (!this.expUrls.length) return;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          const idx = Math.min(this.expSelectedIndex + 1, this.expUrls.length - 1);
          if (idx !== this.expSelectedIndex && idx >= 0) {
            this.selectExplorerUrl(this.expUrls[idx], idx);
            this._scrollSelectedRowIntoView();
          }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const idx = Math.max((this.expSelectedIndex < 0 ? 0 : this.expSelectedIndex) - 1, 0);
          if (idx !== this.expSelectedIndex) {
            this.selectExplorerUrl(this.expUrls[idx], idx);
            this._scrollSelectedRowIntoView();
          }
        } else if (e.key === 'Escape' && this.expDetailOpen) {
          e.preventDefault();
          this.closeExplorerDetail();
        }
      };
      window.addEventListener('keydown', this._expKeyHandler);
    },

    _unbindExplorerKeyNav() {
      if (this._expKeyHandler) {
        window.removeEventListener('keydown', this._expKeyHandler);
        this._expKeyHandler = null;
      }
    },

    _scrollSelectedRowIntoView() {
      this.$nextTick(() => {
        const row = document.querySelector('.exp-table tr.exp-row-selected');
        if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
      });
    },

    getExpCellValue(row, col) {
      const def = EXP_COLUMN_DEFS[col];
      if (!def) return '';
      try { return def.fmt(row); } catch { return ''; }
    },

    expStatusClass(code) {
      if (!code) return '';
      if (code < 300) return 'exp-status-2xx';
      if (code < 400) return 'exp-status-3xx';
      if (code < 500) return 'exp-status-4xx';
      return 'exp-status-5xx';
    },

    openUrlFromExplorer(urlId) {
      this.openUrlDetail(urlId);
    },
  };
}


/* ===== Explorer Constants =====
 *
 * EXP_COLUMN_DEFS describes every column the explorer table can render.
 *
 *   type:        'string' | 'numeric' | 'boolean'
 *   filterKey:   query-param prefix used for inline filters
 *                  - numeric → emits `${filterKey}_gte` / `${filterKey}_lte`
 *                  - string  → emits `${filterKey}` directly (contains)
 *                  - boolean → emits `${filterKey}=true|false`
 *                Omit to make the column non-filterable.
 *   width:       suggested min width in px for the column.
 */
const EXP_COLUMN_DEFS = {
  url:                  { label: 'URL',              type: 'string',  sortable: true,  filterKey: 'url_contains',          width: 320, fmt: r => { try { return new URL(r.url).pathname || '/'; } catch { return r.url; } } },
  status_code:          { label: 'Estado',           type: 'numeric', sortable: true,  filterKey: 'status_code', exact: true, width: 130, fmt: r => r.status_code ?? '' },
  status_text:          { label: 'Texto estado',     type: 'string',  sortable: false, filterKey: 'status_text_contains',    width: 150, fmt: r => r.status_text ?? '' },
  content_type:         { label: 'Tipo',             type: 'string',  sortable: false, filterKey: 'content_type_contains',   width: 160, fmt: r => r.content_type ? r.content_type.split(';')[0] : '' },
  content_length:       { label: 'Tamaño',           type: 'numeric', sortable: true,  filterKey: 'content_length',          width: 140, fmt: r => r.content_length != null ? fmt.bytes(r.content_length) : '' },
  transfer_size:        { label: 'Transferido',      type: 'numeric', sortable: true,  filterKey: 'transfer_size',           width: 140, fmt: r => r.transfer_size != null ? fmt.bytes(r.transfer_size) : '' },
  response_time_ms:     { label: 'Tiempo (ms)',      type: 'numeric', sortable: true,  filterKey: 'response_time_ms',        width: 140, fmt: r => r.response_time_ms != null ? Math.round(r.response_time_ms) + 'ms' : '' },
  click_depth:          { label: 'Clics desde home', type: 'numeric', sortable: true,  filterKey: 'click_depth',             width: 160, fmt: r => r.click_depth ?? '' },
  crawl_depth:          { label: 'Prof. rastreo',    type: 'numeric', sortable: true,  filterKey: 'crawl_depth',             width: 140, fmt: r => r.crawl_depth ?? '' },
  folder_depth:         { label: 'Niveles',          type: 'numeric', sortable: true,  filterKey: 'folder_depth',            width: 130, fmt: r => r.folder_depth ?? '' },
  word_count:           { label: 'Palabras',         type: 'numeric', sortable: true,  filterKey: 'word_count',              width: 140, fmt: r => r.word_count != null ? r.word_count.toLocaleString('es-ES') : '' },
  content_word_count:   { label: 'Palabras contenido', type: 'numeric', sortable: true, filterKey: 'content_word_count',   width: 170, fmt: r => r.content_word_count != null ? r.content_word_count.toLocaleString('es-ES') : '' },
  text_ratio:           { label: '% texto', grupo: 'otros', type: 'numeric', sortable: true,  filterKey: 'text_ratio',              width: 130, fmt: r => r.text_ratio != null ? r.text_ratio.toFixed(1) + '%' : '' },
  url_length:           { label: 'Long. URL',        type: 'numeric', sortable: true,  filterKey: 'url_length',              width: 140, fmt: r => r.url_length ?? '' },
  inlinks_count:        { label: 'Inlinks',          type: 'numeric', sortable: true,  filterKey: 'inlinks_count',           width: 130, fmt: r => r.inlinks_count != null ? r.inlinks_count.toLocaleString('es-ES') : '' },
  unique_inlinks_count: { label: 'Inlinks únicos',   type: 'numeric', sortable: true,  filterKey: 'unique_inlinks_count',    width: 140, fmt: r => r.unique_inlinks_count != null ? r.unique_inlinks_count.toLocaleString('es-ES') : '' },
  outlinks_count:       { label: 'Outlinks',         type: 'numeric', sortable: true,  filterKey: 'outlinks_count',          width: 130, fmt: r => r.outlinks_count != null ? r.outlinks_count.toLocaleString('es-ES') : '' },
  external_outlinks_count: { label: 'Outlinks ext.', type: 'numeric', sortable: true,  filterKey: 'external_outlinks_count', width: 140, fmt: r => r.external_outlinks_count != null ? r.external_outlinks_count.toLocaleString('es-ES') : '' },
  // La que hay que leer y ordenar es `pagerank_score` (0-100 logaritmica,
  // decision 34): con la escala 0-10 lineal el 85% de las paginas de un censo
  // queda en 0,00xx y no se distingue la 250 de la 25.000. La 0-10 se queda por
  // compatibilidad con informes ya entregados.
  pagerank_score:       { label: 'PageRank (0-100)',  type: 'numeric', sortable: true,  filterKey: 'pagerank_score',          width: 160, fmt: r => r.pagerank_score != null ? String(r.pagerank_score) : '' },
  pagerank_raw:         { label: 'PageRank (x media)', grupo: 'otros', type: 'numeric', sortable: true, filterKey: 'pagerank_raw',            width: 170, fmt: r => r.pagerank_raw != null ? (r.pagerank_raw * 1e4).toFixed(2) : '' },
  pagerank:             { label: 'PageRank (0-10, antiguo)', grupo: 'otros', type: 'numeric', sortable: true,  filterKey: 'pagerank',                width: 140, fmt: r => r.pagerank != null ? r.pagerank.toFixed(4) : '' },
  near_duplicate_count: { label: 'Casi duplicadas', type: 'numeric', sortable: true,  filterKey: 'near_duplicate_count',    width: 150, fmt: r => r.near_duplicate_count ?? '' },
  closest_similarity:   { label: 'Similitud máx.', type: 'numeric', sortable: true,  filterKey: 'closest_similarity',      width: 150, fmt: r => r.closest_similarity != null ? (r.closest_similarity * 100).toFixed(1) + '%' : '' },
  redirect_url:         { label: 'Redirige a',      type: 'string',  sortable: false, filterKey: 'redirect_url_contains', vacio: 'redirect_url',   width: 260, fmt: r => r.redirect_url ?? '' },
  redirect_type:        { label: 'Cód. redir.',     type: 'numeric', sortable: false, filterKey: 'redirect_type',            width: 130, fmt: r => r.redirect_type ?? '' },
  indexable:            { label: 'Indexable',        type: 'boolean', sortable: false, filterKey: 'indexable',               width: 130, fmt: r => r.indexable === true ? 'Sí' : r.indexable === false ? 'No' : '' },
  indexability_status:  { label: 'Razón noindex',   type: 'string',  sortable: false, filterKey: 'indexability_status_contains', width: 200, fmt: r => r.indexability_status ?? '' },
  is_internal:          { label: 'Interna',          type: 'boolean', sortable: false, filterKey: 'is_internal',             width: 130, fmt: r => r.is_internal === true ? 'Sí' : r.is_internal === false ? 'No' : '' },
  segmento:             { label: 'Segmento',         type: 'string',  sortable: true,  width: 200, fmt: r => r.segmento ?? '',
                          tip: r => r.segmento == null ? 'Sin segmentar: el censo se analizó antes de los segmentos (decisión 125)'
                            : r.segmento.startsWith('/') ? 'Ninguna regla casa: es la forma de la ruta' : 'Segmento de negocio: ' + r.segmento },
  host:                 { label: 'Host',             type: 'string',  sortable: true,  filterKey: 'host_contains',           width: 200, fmt: r => r.host ?? '' },
  // html_meta fields
  title:                { label: 'Título',          type: 'string',  sortable: true,  filterKey: 'title_contains', vacio: 'title',          width: 260, fmt: r => r.html_meta?.title ?? '' },
  title_len:            { label: 'Long. título',    type: 'numeric', sortable: true,  filterKey: 'title_len',               width: 140, fmt: r => r.html_meta?.title_len ?? '' },
  // Google corta por pixeles (~580 el titulo, ~985 la descripcion), no por
  // caracteres: decision 54. El dato llegaba en cada fila y no era columna.
  title_pixel_width:    { label: 'Título (px)',     type: 'numeric', sortable: true,  filterKey: 'title_pixel_width',       width: 130, fmt: r => r.html_meta?.title_pixel_width ?? '' },
  meta_description:     { label: 'Descripción',     type: 'string',  sortable: false, filterKey: 'description_contains', vacio: 'meta_description',    width: 300, fmt: r => r.html_meta?.meta_description ?? '' },
  meta_description_len: { label: 'Long. desc.',     type: 'numeric', sortable: true,  filterKey: 'meta_description_len',    width: 140, fmt: r => r.html_meta?.meta_description_len ?? '' },
  meta_description_pixel_width: { label: 'Descripción (px)', type: 'numeric', sortable: true, filterKey: 'meta_description_pixel_width', width: 150, fmt: r => r.html_meta?.meta_description_pixel_width ?? '' },
  canonical_href:       { label: 'Canonical',       type: 'string',  sortable: false, filterKey: 'canonical_contains', vacio: 'canonical_href',      width: 260, fmt: r => r.html_meta?.canonical_href ?? '' },
  meta_robots:          { label: 'Meta robots',     type: 'string',  sortable: false, filterKey: 'meta_robots_contains', vacio: 'meta_robots',    width: 180, fmt: r => r.html_meta?.meta_robots ?? '' },
  x_robots_tag:         { label: 'X-Robots-Tag',    type: 'string',  sortable: false, filterKey: 'x_robots_tag_contains', vacio: 'x_robots_tag',   width: 180, fmt: r => (r.html_meta?.x_robots_tag ?? '').replace(/\n/g, ' | ') },
  canonical_header:     { label: 'Canonical (cabecera)', type: 'string', sortable: false, filterKey: 'canonical_header_contains', width: 240, fmt: r => r.html_meta?.canonical_header ?? '' },
  meta_refresh:         { label: 'Meta refresh',    type: 'string',  sortable: false, filterKey: 'meta_refresh_contains',   width: 200, fmt: r => r.html_meta?.meta_refresh ?? '' },
  in_sitemap:           { label: 'En el sitemap',   type: 'boolean', sortable: false, filterKey: 'in_sitemap',              width: 140, fmt: r => r.in_sitemap === true ? 'Sí' : r.in_sitemap === false ? 'No' : '' },
  og_title:             { label: 'OG title',        type: 'string',  sortable: false, filterKey: 'og_title_contains', vacio: 'og_title',       width: 220, fmt: r => r.html_meta?.og_title ?? '' },
  og_image:             { label: 'OG image',        type: 'string',  sortable: false, filterKey: 'og_image_contains', vacio: 'og_image',       width: 240, fmt: r => r.html_meta?.og_image ?? '' },
  og_type:              { label: 'OG type',         type: 'string',  sortable: false, filterKey: 'og_type_contains',        width: 140, fmt: r => r.html_meta?.og_type ?? '' },
  og_url:               { label: 'OG url',          type: 'string',  sortable: false, filterKey: 'og_url_contains',         width: 240, fmt: r => r.html_meta?.og_url ?? '' },
  twitter_card:         { label: 'Twitter card',    type: 'string',  sortable: false, filterKey: 'twitter_card_contains',   width: 150, fmt: r => r.html_meta?.twitter_card ?? '' },
  http_version:         { label: 'HTTP',            type: 'string',  sortable: false, filterKey: 'http_version_contains',   width: 130, fmt: r => r.html_meta?.http_version ?? r.http_version ?? '' },
  last_modified:        { label: 'Last-Modified',   type: 'string',  sortable: false, filterKey: 'last_modified_contains',  width: 180, fmt: r => r.last_modified ?? '' },
  // Calculadas en el servidor solo si estan visibles (?origenes=true): el
  // origen mas frecuente y cuantas paginas la enlazan. Para un 404 o una
  // redireccion es lo que dice DONDE hay que corregir.
  h1:                   { label: 'H1',               type: 'string',  sortable: false, calculada: 'h1', width: 260, fmt: r => r.h1 ?? '' },
  h1_count:             { label: 'Nº de H1',         type: 'numeric', sortable: false, calculada: 'h1', width: 110, fmt: r => r.h1_count ?? '' },
  // Frente al censo de la comparacion automatica (decision 71): «nueva»,
  // «200→404», «sale del índice», «título», «canonical», «palabras a→b».
  cambio:               { label: 'Cambio frente al anterior', type: 'string', sortable: false, calculada: 'cambios', width: 240, fmt: r => r.cambio ?? '' },
  enlazada_desde:       { label: 'Enlazada desde',  type: 'string',  sortable: false, calculada: 'origenes', width: 280, fmt: r => r.enlazada_desde ? (() => { try { return new URL(r.enlazada_desde).pathname; } catch { return r.enlazada_desde; } })() : '' },
  origenes:             { label: 'Páginas que la enlazan', type: 'numeric', sortable: false, calculada: 'origenes', width: 170, fmt: r => r.origenes ?? '' },
  last_crawled_at:      { label: 'Último rastreo',  type: 'string',  sortable: true,                                        width: 170, fmt: r => fmt.date(r.last_crawled_at) },
};

// El selector de columnas, agrupado: 50 casillas seguidas no se leen. «Otros»
// lleva lo que no conviene usar para decidir: la escala 0-10 del PageRank
// (decision 34) y el ratio de texto, que no es una señal de Google (decision 44).
const EXP_GRUPOS_COLUMNAS = [
  ['Respuesta',   ['url','segmento','status_code','status_text','content_type','redirect_url','redirect_type','response_time_ms','content_length','transfer_size','http_version','last_modified','host','is_internal']],
  ['Indexación',  ['indexable','indexability_status','canonical_href','canonical_header','meta_robots','x_robots_tag','meta_refresh','in_sitemap']],
  ['Títulos y snippet', ['title','title_len','title_pixel_width','meta_description','meta_description_len','meta_description_pixel_width','h1','h1_count']],
  ['Contenido',   ['word_count','content_word_count','near_duplicate_count','closest_similarity']],
  ['Enlazado',    ['enlazada_desde','origenes','inlinks_count','unique_inlinks_count','outlinks_count','external_outlinks_count','pagerank_score','click_depth','crawl_depth','folder_depth','url_length']],
  ['Redes sociales', ['og_title','og_image','og_type','og_url','twitter_card']],
  ['Frente al censo anterior', ['cambio']],
  ['Otros',       ['pagerank_raw','pagerank','text_ratio','last_crawled_at']],
];

const EXP_DEFAULT_COLUMNS = [
  'url','status_code','title','title_len','meta_description','meta_description_len',
  'content_type','content_length','response_time_ms','click_depth',
  'word_count','inlinks_count','outlinks_count','pagerank_score','indexable','canonical_href'
];

const HTML_INTERNO = { resource_type: 'html', is_internal: true };

// Columnas de las reglas a medida del censo (decision 104). Una celda que no
// es un resultado se pinta «—» y el motivo va en el tooltip: «no evaluada»
// (sin fila) no es «vacío» (sin coincidencia).
const ESTADOS_A_MEDIDA = {
  sin_coincidencia: 'sin coincidencia', incompleta: 'incompleta: la fuente pasaba de 2 MB',
  conversion_fallida: 'no se pudo leer como número', tiempo_agotado: 'la regex agotó su tiempo',
  presupuesto_pagina: 'sin tiempo en esta página', desactivada: 'regla desactivada (agotó su tiempo 3 veces)',
  pagina_bloqueada: 'página bloqueada por un WAF', error: 'error',
};

function _defsAMedida(job) {
  const cfg = (job && job.config) || {};
  const out = {};
  const render = !!cfg.render_js;
  const marca = (f) => (render && f && f.render === false) ? ' *' : '';
  const tip = (nombre) => (row) => {
    const f = (row.a_medida || {})[nombre];
    if (!f) return 'No evaluada: no es HTML 2xx o queda fuera de solo_urls';
    const base = f.estado === 'ok' ? (f.valor ?? '') : (ESTADOS_A_MEDIDA[f.estado] || f.estado);
    return base + (f.truncado ? ' (recortado)' : '') + (render && f.render === false ? ' · extraído SIN render' : '');
  };
  for (const [nombre, r] of Object.entries(cfg.custom_extraction || {})) {
    if (!r) continue;
    const numerica = r.valor === 'numero' || r.cuantos === 'recuento';
    const key = 'x.' + nombre;
    out[key] = {
      label: r.etiqueta || nombre, nombre, clase: 'extractor', aMedida: true, sortable: true, width: 160,
      type: numerica ? 'numeric' : 'string',
      filterKey: numerica ? key : key + '_contains', vacio: key, tip: tip(nombre),
      fmt: row => {
        const f = (row.a_medida || {})[nombre];
        if (!f || (f.estado !== 'ok' && f.estado !== 'conversion_fallida')) return '—';
        const v = numerica && f.valor_num != null ? fmt.num(f.valor_num) : (f.valor ?? '');
        return v + marca(f);
      },
    };
  }
  for (const [nombre, r] of Object.entries(cfg.custom_search || {})) {
    if (!r) continue;
    const key = 'x.' + nombre;
    out[key] = {
      label: r.etiqueta || nombre, nombre, clase: 'busqueda', aMedida: true, sortable: true, width: 140,
      type: 'boolean', filterKey: key, tip: tip(nombre),
      fmt: row => {
        const f = (row.a_medida || {})[nombre];
        if (!f) return '—';
        if (f.estado === 'ok') return 'sí (' + f.n + ')' + marca(f);
        if (f.estado === 'sin_coincidencia') return 'no' + marca(f);
        return '—';
      },
    };
  }
  return out;
}

const EXP_TABS = [
  { key: 'all',        label: 'Todas',       icon: 'layers',      filter: null,                                   columns: EXP_DEFAULT_COLUMNS },
  { key: 'html',       label: 'HTML',        icon: 'file-text',   filter: { resource_type: 'html' },              columns: ['url','status_code','title','title_len','meta_description','meta_description_len','word_count','response_time_ms','content_length','inlinks_count','outlinks_count','external_outlinks_count','pagerank_score','indexable','canonical_href','meta_robots'] },
  { key: 'js',         label: 'JavaScript',  icon: 'file-code',   filter: { resource_type: 'js' },                columns: ['url','status_code','content_length','transfer_size','response_time_ms','last_modified'] },
  { key: 'css',        label: 'CSS',         icon: 'palette',     filter: { resource_type: 'css' },               columns: ['url','status_code','content_length','transfer_size','response_time_ms','last_modified'] },
  { key: 'image',      label: 'Imágenes',    icon: 'image',       filter: { resource_type: 'image' },             columns: ['url','status_code','content_length','transfer_size','response_time_ms'] },
  { key: 'pdf',        label: 'PDFs',        icon: 'file',        filter: { resource_type: 'pdf' },               columns: ['url','status_code','content_length','transfer_size','response_time_ms'] },
  { key: '_div1', divider: true },
  { key: 'internal',   label: 'Internas',    icon: 'home',        filter: { is_internal: true },                  columns: ['url','status_code','title','title_len','meta_description','word_count','inlinks_count','outlinks_count','external_outlinks_count','pagerank_score','click_depth','indexable','canonical_href'] },
  { key: 'external',   label: 'Externas',    icon: 'external-link', filter: { is_internal: false },               columns: ['url','status_code','host','content_type','content_length','response_time_ms'] },
  { key: '_div2', divider: true },
  { key: '2xx',        label: '2xx',         icon: 'check-circle', filter: { status_group: '2xx' },               columns: ['url','status_code','title','title_len','meta_description','meta_description_len','word_count','response_time_ms','inlinks_count','outlinks_count','pagerank_score','indexable','canonical_href'] },
  { key: '3xx',        label: '3xx',         icon: 'arrow-right', filter: { status_group: '3xx' },                columns: ['url','status_code','redirect_url','redirect_type','inlinks_count','origenes','enlazada_desde','click_depth'] },
  { key: '4xx',        label: '4xx',         icon: 'alert-triangle', filter: { status_group: '4xx' },             columns: ['url','status_code','inlinks_count','origenes','enlazada_desde','click_depth','content_type'] },
  { key: '5xx',        label: '5xx',         icon: 'alert-octagon', filter: { status_group: '5xx' },              columns: ['url','status_code','status_text','response_time_ms','click_depth','inlinks_count'] },
  { key: '_div3', divider: true },
  { key: 'indexable',  label: 'Indexables',  icon: 'eye',         filter: { indexable: true, is_internal: true }, columns: ['url','status_code','title','title_len','meta_description','word_count','inlinks_count','pagerank_score','canonical_href'] },
  { key: 'noindex',    label: 'No indexables', icon: 'eye-off',   filter: { indexable: false, is_internal: true },columns: ['url','status_code','indexability_status','title','meta_robots','canonical_href'] },
  // URLs que el sitio enlaza y robots.txt prohibe: no se piden, pero existen y
  // hay que decidir si el bloqueo es correcto o se esta tapando contenido.
  { key: 'bloqueadas', label: 'Bloqueadas robots', icon: 'slash',  filter: { status_group: 'blocked' },           columns: ['url','indexability_status','inlinks_count','click_depth'] },
  // Extraccion y busqueda a medida (decision 104): solo si el censo tiene
  // reglas. Sus columnas las rellena _registrarAMedida al abrir el censo.
  { key: 'a_medida',   label: 'Extracción a medida', icon: 'scan-search', filter: HTML_INTERNO, requiere: 'a_medida', columns: ['url','status_code'] },
  // Vistas por ambito SEO (#131), como las pestañas de Screaming Frog pero
  // construidas con lo que ya calcula el analizador: cada una son las paginas
  // HTML internas, sus columnas y, encima de la tabla, un chip por tipo de
  // incidencia de su ambito con cuantas paginas lo tienen.
  { key: '_h1', header: 'Por ámbito SEO' },
  { key: 'a_titulos',  label: 'Títulos',       icon: 'heading',      filter: HTML_INTERNO, ambito: ['title_missing','title_too_short','title_too_long','title_duplicate','title_multiple','title_generico'],
    columns: ['url','status_code','title','title_len','title_pixel_width','indexable','inlinks_count'] },
  { key: 'a_desc',     label: 'Descripciones', icon: 'align-left',   filter: HTML_INTERNO, ambito: ['description_missing','description_too_short','description_too_long','description_duplicate','description_multiple','description_igual_title'],
    columns: ['url','status_code','meta_description','meta_description_len','meta_description_pixel_width','indexable'] },
  { key: 'a_h1',       label: 'Encabezados',   icon: 'type',         filter: HTML_INTERNO, ambito: ['h1_missing','h1_vacio','h1_multiple','h1_duplicate'],
    columns: ['url','status_code','h1','h1_count','title','indexable'] },
  { key: 'a_canon',    label: 'Canonicals',    icon: 'link-2',       filter: HTML_INTERNO, ambito: ['canonical_missing','canonical_cross_domain','canonical_a_error','canonical_a_redireccion','canonical_a_no_indexable','canonical_encadenado','canonical_sin_enlazar','canonical_broken','canonical_multiple','canonical_in_body','canonical_a_http','canonical_html_y_cabecera_distintos','noindex_y_canonical_a_otra'],
    columns: ['url','status_code','canonical_href','canonical_header','indexable','indexability_status'] },
  // Las redirecciones no son HTML: el ambito va sobre las internas.
  { key: 'a_redir',    label: 'Redirecciones', icon: 'corner-down-right', filter: { is_internal: true }, ambito: ['redireccion_a_error','redireccion_a_noindex','meta_refresh_redireccion','redirect_chain','redirect_loop'],
    columns: ['url','status_code','redirect_url','redirect_type','inlinks_count','origenes','click_depth'] },
  { key: 'a_robots',   label: 'Directivas',    icon: 'shield',       filter: HTML_INTERNO, ambito: ['noindex_page','noindex_y_canonical_a_otra','robots_invalid_syntax'],
    columns: ['url','status_code','indexable','indexability_status','meta_robots','x_robots_tag','in_sitemap'] },
  { key: 'a_html',     label: 'HTML ignorado', icon: 'scissors',     filter: HTML_INTERNO, ambito: ['html_supera_2mb','head_cortado','meta_fuera_del_head'],
    columns: ['url','status_code','content_length','title','meta_robots','indexable'] },
  { key: 'a_hreflang', label: 'Hreflang',      icon: 'languages',    filter: HTML_INTERNO, ambito: ['hreflang_missing_return','hreflang_invalid_lang','hreflang_broken_target','hreflang_in_body','hreflang_sin_autorreferencia','hreflang_sin_x_default','hreflang_idioma_repetido','hreflang_destino_no_indexable','hreflang_retorno_incoherente'],
    columns: ['url','status_code','title','indexable','inlinks_count'] },
  { key: 'a_sd',       label: 'Datos estructurados', icon: 'braces', filter: HTML_INTERNO, ambito: ['structured_data_error','structured_data_warning','structured_data_info'],
    columns: ['url','status_code','title','indexable','pagerank_score'] },
  { key: 'a_enlazado', label: 'Enlazado',      icon: 'share-2',      filter: HTML_INTERNO, ambito: ['orphan_page','sitemap_orphan','solo_enlazada_por_redireccion','not_reachable_from_home','high_outlink_count','not_in_sitemap','enlace_no_rastreable','enlace_interno_a_error','enlace_interno_a_redireccion','utm_en_enlace_interno','enlace_sin_ancla'],
    columns: ['url','status_code','inlinks_count','origenes','click_depth','pagerank_score','in_sitemap','indexable'] },
  // Sin HTML_INTERNO: lo que el sitemap declara y no se puede indexar son
  // sobre todo saltos (resource_type 'redirect') y errores (decision 100). Y
  // sin in_sitemap en el filtro: dejaria vacio el chip de not_in_sitemap.
  { key: 'a_sitemap',  label: 'Sitemap',       icon: 'map',          filter: { is_internal: true }, ambito: ['sitemap_no_indexable','sitemap_orphan','not_in_sitemap'],
    columns: ['url','status_code','indexable','indexability_status','redirect_url','canonical_href','inlinks_count','in_sitemap'] },
  { key: 'a_contenido', label: 'Contenido',    icon: 'file-text',    filter: HTML_INTERNO, ambito: ['low_word_count','duplicate_content','near_duplicate_content','soft_404','soft_404_candidato'],
    columns: ['url','status_code','title','content_word_count','near_duplicate_count','closest_similarity','indexable'] },
  { key: 'a_urls',     label: 'Forma de la URL', icon: 'link',       filter: { is_internal: true }, ambito: ['url_too_long','url_non_ascii','url_uppercase','url_underscores','url_multiple_slashes','url_has_parameters','url_non_seo_friendly','url_cms_faceted'],
    columns: ['url','status_code','url_length','indexable','inlinks_count'] },
];

function _freshForm() {
  return {
    name: '',
    seeds: '',
    cliente_id: '',
    profile_id: '',
    configTab: 'general',
    // General
    max_depth: 3,
    max_urls: 50000,
    concurrent_requests: 32,
    concurrent_requests_per_domain: 8,
    follow_external: false,
    robots_mode: 'respect',
    render_js: false,
    use_sitemap: true,
    user_agent_preset: 'chrome_win',
    user_agent_custom: '',
    impersonate: 'chrome124',
    // Spider — resource types
    crawl_images: true,
    crawl_css: true,
    crawl_js: true,
    crawl_pdfs: true,
    crawl_fonts: false,
    crawl_svg: true,
    crawl_other: true,
    check_external_resources: false,
    // Spider — behavior
    crawl_subdomains: false,
    follow_nofollow: false,
    exclude_patterns: '',
    include_patterns: '',
    max_url_length: 0,
    max_folder_depth: 0,
    // Extraction (Contenido)
    extract_structured_data: true,
    extract_hreflang: true,
    extract_security_headers: true,
    extract_page_content: true,
    store_raw_html: false,
    // Velocidad
    download_timeout: 30,
    retry_count: 2,
    request_delay: 0,
    autothrottle_enabled: true,
    autothrottle_target_concurrency: 8,
    max_runtime_hours: 6,
    // HTTP
    custom_headers: '',
    accept_language: '',
    cookies: '',
    conservar_cookies: false,
    basic_auth_user: '',
    basic_auth_password: '',
    // Analisis
    title_min_length: 10,
    title_max_length: 60,
    description_min_length: 50,
    description_max_length: 160,
    min_word_count: 200,
    // 1, no 2: dos saltos ya son una cadena (decision 43). El formulario
    // mandaba 2 y pisaba el default del servidor en cada rastreo lanzado aqui.
    max_redirect_chain_length: 1,
    max_outlinks: 100,
  };
}

function _parseKV(text, separator) {
  const obj = {};
  if (!text) return obj;
  text.split('\n').forEach(line => {
    line = line.trim();
    if (!line) return;
    const idx = line.indexOf(separator);
    if (idx < 1) return;
    const key = line.substring(0, idx).trim();
    const val = line.substring(idx + separator.length).trim();
    if (key) obj[key] = val;
  });
  return obj;
}

// La configuracion de un job a partir del formulario del modal. Es lo que se
// enviaba siempre; con cliente, el formulario llega precargado con lo heredado
// (`_formDesdeConfig`), asi que lo que se ve es lo que se lanza.
function _configDesdeForm(f) {
  return {
    max_depth:     parseInt(f.max_depth)     || 3,
    max_urls:      parseInt(f.max_urls)      || 50000,
    concurrent_requests:            parseInt(f.concurrent_requests)            || 32,
    concurrent_requests_per_domain: parseInt(f.concurrent_requests_per_domain) || 8,
    follow_external: f.follow_external,
    robots_mode:     f.robots_mode,
    render_js:       f.render_js,
    use_sitemap:     f.use_sitemap,
    user_agent:      f.user_agent_preset === 'custom' ? f.user_agent_custom : UA_PRESETS[f.user_agent_preset],
    impersonate:     f.impersonate || 'chrome124',
    exclude_patterns: f.exclude_patterns.split('\n').map(s => s.trim()).filter(Boolean),
    include_patterns: f.include_patterns.split('\n').map(s => s.trim()).filter(Boolean),
    resource_types: {
      crawl_images: f.crawl_images,
      crawl_css: f.crawl_css,
      crawl_js: f.crawl_js,
      crawl_pdfs: f.crawl_pdfs,
      crawl_fonts: f.crawl_fonts,
      crawl_svg: f.crawl_svg,
      crawl_other: f.crawl_other,
      check_external_resources: f.check_external_resources,
    },
    crawl_behavior: {
      download_timeout: parseInt(f.download_timeout) || 30,
      retry_count: parseInt(f.retry_count) || 2,
      request_delay: parseFloat(f.request_delay) || 0,
      autothrottle_enabled: f.autothrottle_enabled,
      autothrottle_target_concurrency: parseFloat(f.autothrottle_target_concurrency) || 8,
      follow_nofollow: f.follow_nofollow,
      crawl_subdomains: f.crawl_subdomains,
      max_runtime_hours: parseInt(f.max_runtime_hours) || 6,
    },
    url_filters: {
      max_url_length: parseInt(f.max_url_length) || 0,
      max_folder_depth: parseInt(f.max_folder_depth) || 0,
    },
    extraction: {
      extract_structured_data: f.extract_structured_data,
      extract_hreflang: f.extract_hreflang,
      extract_security_headers: f.extract_security_headers,
      extract_page_content: f.extract_page_content,
      store_raw_html: f.store_raw_html,
    },
    http: {
      custom_headers: _parseKV(f.custom_headers, ':'),
      accept_language: f.accept_language,
      cookies: _parseKV(f.cookies, '='),
      conservar_cookies: !!f.conservar_cookies,
      basic_auth_user: f.basic_auth_user,
      basic_auth_password: f.basic_auth_password,
    },
    analysis_thresholds: {
      title_min_length: parseInt(f.title_min_length) || 10,
      title_max_length: parseInt(f.title_max_length) || 60,
      description_min_length: parseInt(f.description_min_length) || 50,
      description_max_length: parseInt(f.description_max_length) || 160,
      min_word_count: parseInt(f.min_word_count) || 200,
      max_redirect_chain_length: parseInt(f.max_redirect_chain_length) || 1,
      max_outlinks: parseInt(f.max_outlinks) || 100,
    },
  };
}

// Lo contrario: vuelca en el formulario una configuracion PARCIAL (la que
// fijan cliente y perfil). Solo toca los campos que la configuracion trae; el
// resto se queda con los valores de siempre del modal.
function _formDesdeConfig(f, cfg) {
  const poner = (campo, valor) => { if (valor !== undefined && valor !== null) f[campo] = valor; };
  ['max_depth', 'max_urls', 'concurrent_requests', 'concurrent_requests_per_domain',
   'follow_external', 'robots_mode', 'render_js', 'use_sitemap', 'impersonate']
    .forEach(k => poner(k, cfg[k]));
  if (cfg.user_agent) {
    const preset = presetDeUserAgent(cfg.user_agent);
    f.user_agent_preset = preset || 'custom';
    if (!preset) f.user_agent_custom = cfg.user_agent;
  }
  if (Array.isArray(cfg.exclude_patterns)) f.exclude_patterns = cfg.exclude_patterns.join('\n');
  if (Array.isArray(cfg.include_patterns)) f.include_patterns = cfg.include_patterns.join('\n');
  const sub = (obj, campos) => campos.forEach(k => poner(k, (obj || {})[k]));
  sub(cfg.resource_types, ['crawl_images', 'crawl_css', 'crawl_js', 'crawl_pdfs', 'crawl_fonts',
    'crawl_svg', 'crawl_other', 'check_external_resources']);
  sub(cfg.crawl_behavior, ['download_timeout', 'retry_count', 'request_delay', 'autothrottle_enabled',
    'autothrottle_target_concurrency', 'follow_nofollow', 'crawl_subdomains', 'max_runtime_hours']);
  sub(cfg.url_filters, ['max_url_length', 'max_folder_depth']);
  sub(cfg.extraction, ['extract_structured_data', 'extract_hreflang', 'extract_security_headers',
    'extract_page_content', 'store_raw_html']);
  const http = cfg.http || {};
  if (http.custom_headers) f.custom_headers = _kvTexto(http.custom_headers, ': ');
  if (http.cookies) f.cookies = _kvTexto(http.cookies, '=');
  poner('accept_language', http.accept_language);
  poner('conservar_cookies', http.conservar_cookies);
  poner('basic_auth_user', http.basic_auth_user);
  sub(cfg.analysis_thresholds, ['title_min_length', 'title_max_length', 'description_min_length',
    'description_max_length', 'min_word_count', 'max_redirect_chain_length', 'max_outlinks']);
  return f;
}

function _kvTexto(obj, sep) {
  return Object.entries(obj || {}).map(([k, v]) => `${k}${sep}${v}`).join('\n');
}

// El formulario de la ficha de un cliente. `c` = la respuesta de la API, o
// null para uno nuevo. Las integraciones traen `configurada` del servidor:
// si la variable existe en su entorno, nunca su valor.
function _formCliente(c) {
  return {
    nombre: c ? c.nombre : '',
    slug: c ? c.slug : '',
    dominios: c ? (c.dominios || []).join('\n') : '',
    zona_horaria: c ? (c.zona_horaria || '') : 'Europe/Madrid',
    notas: c ? (c.notas || '') : '',
    config_defecto: c ? JSON.stringify(c.config_defecto || {}, null, 2) : '{}',
    integraciones: c ? (c.integraciones || []).map(i => ({
      tipo: i.tipo,
      etiqueta: i.etiqueta || '',
      parametros: _kvTexto(i.parametros, '='),
      credencial_env: i.credencial_env || '',
      activa: i.activa !== false,
      configurada: i.configurada,
    })) : [],
  };
}
