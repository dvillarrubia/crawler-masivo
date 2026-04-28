/* ===== SEO Crawler Frontend — Alpine.js App ===== */

const API = '/api';

/* ===== Traducciones ===== */
const STATUS_LABEL = {
  pending:   'Pendiente',
  running:   'En curso',
  completed: 'Completado',
  failed:    'Fallido',
  cancelled: 'Cancelado',
};

const ISSUE_LABEL = {
  '4xx_error': 'Error 4xx',
  '5xx_error': 'Error 5xx',
  'connection_error': 'Error de conexion',
  'title_missing': 'Sin titulo',
  'title_too_short': 'Titulo muy corto',
  'title_too_long': 'Titulo muy largo',
  'title_duplicate': 'Titulo duplicado',
  'description_missing': 'Sin meta descripcion',
  'description_too_short': 'Descripcion muy corta',
  'description_too_long': 'Descripcion muy larga',
  'description_duplicate': 'Descripcion duplicada',
  'h1_missing': 'Sin H1',
  'h1_multiple': 'Multiples H1',
  'h1_duplicate': 'H1 duplicado',
  'canonical_missing': 'Sin canonical',
  'canonical_cross_domain': 'Canonical externo',
  'canonical_broken': 'Canonical roto',
  'hreflang_missing_return': 'Hreflang sin retorno',
  'hreflang_invalid_lang': 'Hreflang idioma invalido',
  'hreflang_broken_target': 'Hreflang destino roto',
  'structured_data_error': 'Error datos estructurados',
  'structured_data_warning': 'Aviso datos estructurados',
  'noindex_page': 'Pagina noindex',
  'duplicate_content': 'Contenido duplicado',
  'redirect_loop': 'Bucle de redireccion',
  'redirect_chain': 'Cadena de redirecciones',
  'image_missing_alt': 'Imagen sin alt',
  'http_url': 'URL sin HTTPS',
  'mixed_content': 'Contenido mixto',
  'missing_hsts': 'Sin cabecera HSTS',
  'missing_csp': 'Sin politica CSP',
  'missing_x_content_type_options': 'Sin X-Content-Type-Options',
  'missing_x_frame_options': 'Sin X-Frame-Options',
  'unsafe_crossorigin': 'Crossorigin inseguro',
  'low_word_count': 'Pocas palabras',
  'very_low_text_ratio': 'Ratio texto muy bajo',
  'low_text_ratio': 'Ratio texto bajo',
  'url_too_long': 'URL muy larga',
  'url_non_ascii': 'URL con caracteres no ASCII',
  'url_uppercase': 'URL con mayusculas',
  'url_underscores': 'URL con guiones bajos',
  'url_multiple_slashes': 'URL con barras multiples',
  'url_has_parameters': 'URL con parametros',
  'orphan_page': 'Pagina huerfana',
  'high_outlink_count': 'Demasiados enlaces salientes',
};

const SEVERITY_LABEL = { error: 'Error', warning: 'Aviso', info: 'Info' };

const POSITION_LABEL = {
  nav: 'Navegacion', footer: 'Pie de pagina', content: 'Contenido',
  header: 'Cabecera', sidebar: 'Lateral',
};

// Helpers
const fmt = {
  num: n => n == null ? '—' : Number(n).toLocaleString('es-ES'),
  pct: n => n == null ? '—' : n.toFixed(1) + '%',
  ms:  n => n == null ? '—' : n < 1000 ? Math.round(n) + 'ms' : (n/1000).toFixed(1) + 's',
  date: d => d ? new Date(d).toLocaleString('es-ES') : '—',
  ago: d => {
    if (!d) return '—';
    const s = Math.floor((Date.now() - new Date(d).getTime()) / 1000);
    if (s < 60)    return 'hace ' + s + 's';
    if (s < 3600)  return 'hace ' + Math.floor(s/60) + 'min';
    if (s < 86400) return 'hace ' + Math.floor(s/3600) + 'h';
    return 'hace ' + Math.floor(s/86400) + 'd';
  },
  bytes: b => {
    if (b == null) return '—';
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
    return (b/1048576).toFixed(1) + ' MB';
  },
  status: s => STATUS_LABEL[s] || s,
  issue: t => ISSUE_LABEL[t] || t.replaceAll('_', ' '),
  severity: s => SEVERITY_LABEL[s] || s,
  position: p => POSITION_LABEL[p] || p || '—',
};

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Error en la peticion');
  }
  if (res.status === 204) return null;
  return res.json();
}


/* ===== Main App ===== */
function app() {
  return {
    // Navegacion
    view: 'jobs',
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

    // Modal de creacion
    showCreate: false,
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

    // Pestana URLs
    urls: [], urlsPage: 1, urlsPages: 1, urlsTotal: 0,
    urlFilters: { status_group: '', is_internal: '', resource_type: '' },

    // Pestana Problemas
    issues: [], issuesPage: 1, issuesPages: 1, issuesTotal: 0,
    issueFilters: { severity: '', issue_type: '' },

    // Pestana Enlaces
    links: [], linksPage: 1, linksPages: 1, linksTotal: 0,

    // Vista detalle de URL
    urlDetail: null,
    urlDetailTab: 'general',
    urlDetailLoading: false,
    contentViewMode: 'markdown',

    // ------- Init -------
    init() {
      this.loadJobs();

      // Keyboard shortcut: 'n' to open create modal
      document.addEventListener('keydown', (e) => {
        if (e.key === 'n' && !this.showCreate && !['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)) {
          this.openCreate();
        }
      });

      this.$nextTick(() => lucide.createIcons());
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
        const f = this.jobsFilter ? `&status=${this.jobsFilter}` : '';
        const data = await api(`/jobs?page=${this.jobsPage}&page_size=20${f}`);
        this.jobs = data.items;
        this.jobsPages = data.pages;
        this.jobsTotal = data.total;
      } catch (e) { this.error = e.message; }
      this.loading = false;
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
    openCreate() {
      this.createForm = _freshForm();
      this.createError = null;
      this.showCreate = true;
      this.$nextTick(() => lucide.createIcons());
    },

    async submitCreate() {
      this.creating = true;
      this.createError = null;
      try {
        const f = this.createForm;
        const seeds = f.seeds.split('\n').map(s => s.trim()).filter(Boolean);

        const payload = {
          name: f.name,
          seeds,
          config: {
            max_depth:     parseInt(f.max_depth)     || 3,
            max_urls:      parseInt(f.max_urls)      || 50000,
            concurrent_requests:            parseInt(f.concurrent_requests)            || 32,
            concurrent_requests_per_domain: parseInt(f.concurrent_requests_per_domain) || 8,
            follow_external: f.follow_external,
            respect_robots:  f.respect_robots,
            render_js:       f.render_js,
            user_agent:      f.user_agent || 'SEOCrawler/1.0',
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
              basic_auth_user: f.basic_auth_user,
              basic_auth_password: f.basic_auth_password,
            },
            analysis_thresholds: {
              title_min_length: parseInt(f.title_min_length) || 10,
              title_max_length: parseInt(f.title_max_length) || 60,
              description_min_length: parseInt(f.description_min_length) || 50,
              description_max_length: parseInt(f.description_max_length) || 160,
              min_word_count: parseInt(f.min_word_count) || 200,
              max_redirect_chain_length: parseInt(f.max_redirect_chain_length) || 2,
              max_outlinks: parseInt(f.max_outlinks) || 100,
            },
          }
        };
        if (f.client_id) payload.client_id = f.client_id;

        await api('/jobs', { method: 'POST', body: JSON.stringify(payload) });
        this.showCreate = false;
        this.jobsPage = 1;
        await this.loadJobs();
        this.showToast('Rastreo iniciado correctamente');
      } catch (e) { this.createError = e.message; }
      this.creating = false;
    },

    // ------- Detalle del trabajo -------
    async openJob(id) {
      this.view = 'detail';
      this.detailTab = 'overview';
      this.stats = null;
      this.progress = null;
      this.insights = null;
      this.expandedCategories = {};
      this.urls = []; this.issues = []; this.links = [];
      this.urlsTotal = 0; this.issuesTotal = 0; this.linksTotal = 0;
      this.loading = true;
      try {
        this.job = await api(`/jobs/${id}`);
        await this.loadStats();
        this._startProgress();
      } catch (e) { this.error = e.message; }
      this.loading = false;
      window.scrollTo({ top: 0, behavior: 'smooth' });
      this.$nextTick(() => lucide.createIcons());
    },

    backToJobs() {
      this._stopProgress();
      this.view = 'jobs';
      this.job = null;
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
      if (!this.job || !['pending','running'].includes(this.job.status)) return;
      const poll = async () => {
        try {
          this.progress = await api(`/jobs/${this.job.id}/progress`);
          if (this.progress.status !== this.job.status) {
            this.job.status = this.progress.status;
            if (['completed','failed','cancelled'].includes(this.progress.status)) {
              this._stopProgress();
              this.job = await api(`/jobs/${this.job.id}`);
              await this.loadStats();
            }
          }
        } catch {}
      };
      poll();
      this.progressTimer = setInterval(poll, 2000);
    },

    _stopProgress() {
      if (this.progressTimer) { clearInterval(this.progressTimer); this.progressTimer = null; }
    },

    // ------- Cancelar / Eliminar -------
    async cancelJob() {
      if (!this.job || !confirm('¿Cancelar este rastreo? El crawler se detendrá en cuanto procese la señal.')) return;
      try {
        this.job = await api(`/jobs/${this.job.id}/cancel`, { method: 'PATCH' });
        this._stopProgress();
      } catch (e) { alert(e.message); }
    },

    async deleteJob() {
      if (!this.job || !confirm('¿Eliminar este trabajo y todos sus datos? Esta acción no se puede deshacer.')) return;
      try {
        await api(`/jobs/${this.job.id}`, { method: 'DELETE' });
        this.showToast('Trabajo eliminado', 'info');
        this.backToJobs();
      } catch (e) { alert(e.message); }
    },

    // ------- Pestanas -------
    async switchTab(tab) {
      this.detailTab = tab;
      if (tab === 'urls' && this.urls.length === 0) this.loadUrls();
      if (tab === 'issues' && this.issues.length === 0) this.loadIssues();
      if (tab === 'links' && this.links.length === 0) this.loadLinks();
      if (tab === 'insights' && !this.insights) this.loadInsights();
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

    // SVG ring dasharray for score circle
    ringDash(score, radius) {
      const circ = 2 * Math.PI * radius;
      return `${(score / 100) * circ} ${circ}`;
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
      this.urlDetailTab = 'general';
      this.urlDetail = null;
      this.view = 'url-detail';
      try {
        this.urlDetail = await api(`/jobs/${this.job.id}/urls/${urlId}`);
      } catch (e) { this.error = e.message; }
      this.urlDetailLoading = false;
      window.scrollTo({ top: 0, behavior: 'smooth' });
      this.$nextTick(() => lucide.createIcons());
    },

    backToDetail() {
      this.view = 'detail';
      this.urlDetail = null;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },

    // ------- Problemas -------
    async loadIssues() {
      this.loading = true;
      try {
        let f = '';
        if (this.issueFilters.severity)   f += `&severity=${this.issueFilters.severity}`;
        if (this.issueFilters.issue_type) f += `&issue_type=${this.issueFilters.issue_type}`;
        const data = await api(`/jobs/${this.job.id}/issues?page=${this.issuesPage}&page_size=50${f}`);
        this.issues = data.items;
        this.issuesPages = data.pages;
        this.issuesTotal = data.total;
      } catch (e) { this.error = e.message; }
      this.loading = false;
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

    // ------- Exportar -------
    exportCSV() {
      if (!this.job) return;
      window.open(`${API}/jobs/${this.job.id}/export`, '_blank');
    },

    // ------- Helpers -------
    statusColor(s) {
      return { pending:'badge-pending', running:'badge-running', completed:'badge-completed', failed:'badge-failed', cancelled:'badge-cancelled' }[s] || 'badge-pending';
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

    get issueTypes() {
      if (!this.stats) return [];
      return [...new Set(this.stats.issues_by_type.map(i => i.issue_type))];
    },

    get issuesSorted() {
      if (!this.stats) return [];
      return [...this.stats.issues_by_type].sort((a, b) => b.count - a.count);
    },

    crawledPercent() {
      if (!this.progress || !this.job?.config?.max_urls) return 0;
      return Math.min(100, (this.progress.crawled_count / this.job.config.max_urls) * 100);
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
      let score = 0, total = 7;
      if (s.is_https) score++;
      if (!s.has_mixed_content) score++;
      if (s.has_hsts) score++;
      if (s.has_csp) score++;
      if (s.has_x_content_type_options) score++;
      if (s.has_x_frame_options) score++;
      if (!s.has_unsafe_crossorigin) score++;
      return { score, total, pct: Math.round(score / total * 100) };
    },

    fmt,

    // ------- Reinit Lucide icons -------
    reinitIcons() {
      this.$nextTick(() => { if (window.lucide) lucide.createIcons(); });
    },
  };
}

function _freshForm() {
  return {
    name: '',
    seeds: '',
    client_id: '',
    configTab: 'general',
    // General
    max_depth: 3,
    max_urls: 50000,
    concurrent_requests: 32,
    concurrent_requests_per_domain: 8,
    follow_external: false,
    respect_robots: true,
    render_js: false,
    user_agent: 'SEOCrawler/1.0',
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
    // HTTP
    custom_headers: '',
    accept_language: '',
    cookies: '',
    basic_auth_user: '',
    basic_auth_password: '',
    // Analisis
    title_min_length: 10,
    title_max_length: 60,
    description_min_length: 50,
    description_max_length: 160,
    min_word_count: 200,
    max_redirect_chain_length: 2,
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
