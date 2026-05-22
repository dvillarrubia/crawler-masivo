/* ===== SEO Crawler Frontend — Alpine.js App ===== */

const API = '/api';

/* ===== User-Agent presets ===== */
const UA_PRESETS = {
  chrome_win:  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  chrome_mac:  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  safari_mac:  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
  firefox_win: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
  edge_win:    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0',
  googlebot:   'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
};
const UA_LABELS = {
  chrome_win:  'Chrome - Windows',
  chrome_mac:  'Chrome - macOS',
  safari_mac:  'Safari - macOS',
  firefox_win: 'Firefox - Windows',
  edge_win:    'Edge - Windows',
  googlebot:   'Googlebot',
  custom:      'Personalizado',
};

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
  'url_non_seo_friendly': 'URL no SEO-friendly',
  'url_cms_faceted': 'URL de filtro/CMS (crawl budget)',
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
    recUrls: {},      // { "rec-title": { loading, urls, loaded } }
    recUrlsOpen: {},  // { "rec-title": bool }

    // Pestana URLs
    urls: [], urlsPage: 1, urlsPages: 1, urlsTotal: 0,
    urlFilters: { status_group: '', is_internal: '', resource_type: '' },

    // Pestana Problemas
    issues: [], issuesPage: 1, issuesPages: 1, issuesTotal: 0,
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
            robots_mode:     f.robots_mode,
            render_js:       f.render_js,
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
      if (tab === 'semantic') this.loadSemantic();
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
      if (!confirm('Eliminar esta API key? Los analisis pasados conservaran el id pero no podran re-embebir queries.')) return;
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
        filtering: 'Filtrando paginas...',
        embedding: 'Generando embeddings (esto puede tardar)...',
        weighting: 'Calculando pesos...',
        centroid: 'Calculando centroide...',
        dimensionality_reduction: 'Reduccion dimensional (PCA + UMAP)...',
        clustering: 'Clustering HDBSCAN...',
        classification: 'Clasificando anillos...',
        cannibalization: 'Detectando canibalizacion...',
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
          if (el && window.Plotly) Plotly.newPlot(el, data.data, data.layout, { responsive: true });
        } else if (type === 'scatter') {
          const data = await api(`/jobs/${this.job.id}/semantic/scatter-data`);
          await this.$nextTick();
          const el = document.getElementById('semantic-scatter-chart');
          if (el && window.Plotly) Plotly.newPlot(el, data.data, data.layout, { responsive: true });
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
            Plotly.newPlot(el, this.targetResults.ring_map.data, this.targetResults.ring_map.layout, { responsive: true });
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
        alert(e.message);
      }
      this.resuming = false;
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
    expDetailTab: 'content',
    expDetailOpen: false,
    expBadgeCounts: {},
    expColumnsByTab: {},        // { tabKey: [colKey, ...] } — persisted column selection per tab
    expColumnPickerOpen: false,
    expColumnFilters: {},       // { columnKey: { op: '>=', value: '0.5' } | { value: 'foo' } }
    _expSearchTimer: null,
    _expFilterTimer: null,
    _expKeyHandler: null,

    openExplorer() {
      this.view = 'explorer';
      this.expTab = 'all';
      this.expSearch = '';
      this.expSort = null;
      this.expSortDir = 'asc';
      this.expPage = 1;
      this.expDetailOpen = false;
      this.expSelectedUrl = null;
      this.expSelectedIndex = -1;
      this.expUrlDetail = null;
      this.expColumnFilters = {};
      this.expColumnPickerOpen = false;
      this.expColumnsByTab = this._loadExpColumnPrefs();
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

    setExplorerTab(tab) {
      this.expTab = tab;
      this.expPage = 1;
      this.expSearch = '';
      this.expDetailOpen = false;
      this.expSelectedUrl = null;
      this.expSelectedIndex = -1;
      this.expColumnFilters = {};
      this.expColumnPickerOpen = false;
      this.loadExplorerUrls();
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
    _buildExplorerParams() {
      const tabCfg = EXP_TABS.find(t => t.key === this.expTab) || EXP_TABS[0];
      const params = new URLSearchParams();
      params.set('page', String(this.expPage));
      params.set('page_size', String(this.expPageSize));
      if (tabCfg.filter) {
        for (const [k, v] of Object.entries(tabCfg.filter)) params.set(k, String(v));
      }
      if (this.expSearch) params.set('search', this.expSearch);
      if (this.expSort) { params.set('sort_by', this.expSort); params.set('sort_dir', this.expSortDir); }

      // Inline column filters
      for (const [colKey, f] of Object.entries(this.expColumnFilters)) {
        if (!f) continue;
        const def = EXP_COLUMN_DEFS[colKey];
        if (!def || !def.filterKey) continue;
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
          // string contains — for url use the 'search' param
          if (def.filterKey === 'search') {
            params.set('search', raw);
          } else {
            params.set(def.filterKey, raw);
          }
        }
      }
      return params.toString();
    },

    async loadExplorerUrls() {
      if (!this.job) return;
      this.expLoading = true;
      try {
        const params = this._buildExplorerParams();
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
      } catch (e) { this.error = e.message; }
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
      this.expPage = 1;
      this.loadExplorerUrls();
    },

    expActiveFilterCount() {
      return Object.values(this.expColumnFilters).filter(f =>
        f && f.value != null && f.value.toString().trim() !== ''
      ).length;
    },

    /**
     * Default operator shown when a numeric column has no filter yet.
     * status_code is "exact" so it defaults to '='.
     */
    expDefaultOp(col) {
      return EXP_COLUMN_DEFS[col]?.exact ? '=' : '>=';
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
          '=':  'igual a…',
          '<=': 'máximo…',
          '<':  'menor que…',
        })[op] || 'núm';
      }
      if (def.type === 'string') return 'contiene…';
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
  url:                  { label: 'URL',              type: 'string',  sortable: true,  filterKey: 'search',                width: 320, fmt: r => { try { return new URL(r.url).pathname || '/'; } catch { return r.url; } } },
  status_code:          { label: 'Estado',           type: 'numeric', sortable: true,  filterKey: 'status_code', exact: true, width: 130, fmt: r => r.status_code ?? '' },
  status_text:          { label: 'Texto estado',     type: 'string',  sortable: false, filterKey: 'status_text_contains',    width: 150, fmt: r => r.status_text ?? '' },
  content_type:         { label: 'Tipo',             type: 'string',  sortable: false, filterKey: 'content_type_contains',   width: 160, fmt: r => r.content_type ? r.content_type.split(';')[0] : '' },
  content_length:       { label: 'Tamaño',           type: 'numeric', sortable: true,  filterKey: 'content_length',          width: 140, fmt: r => r.content_length != null ? fmt.bytes(r.content_length) : '' },
  transfer_size:        { label: 'Transferido',      type: 'numeric', sortable: true,  filterKey: 'transfer_size',           width: 140, fmt: r => r.transfer_size != null ? fmt.bytes(r.transfer_size) : '' },
  response_time_ms:     { label: 'Tiempo (ms)',      type: 'numeric', sortable: true,  filterKey: 'response_time_ms',        width: 140, fmt: r => r.response_time_ms != null ? Math.round(r.response_time_ms) + 'ms' : '' },
  crawl_depth:          { label: 'Prof.',            type: 'numeric', sortable: true,  filterKey: 'crawl_depth',             width: 130, fmt: r => r.crawl_depth ?? '' },
  folder_depth:         { label: 'Niveles',          type: 'numeric', sortable: true,  filterKey: 'folder_depth',            width: 130, fmt: r => r.folder_depth ?? '' },
  word_count:           { label: 'Palabras',         type: 'numeric', sortable: true,  filterKey: 'word_count',              width: 140, fmt: r => r.word_count != null ? r.word_count.toLocaleString('es-ES') : '' },
  text_ratio:           { label: '% texto',          type: 'numeric', sortable: true,  filterKey: 'text_ratio',              width: 130, fmt: r => r.text_ratio != null ? r.text_ratio.toFixed(1) + '%' : '' },
  url_length:           { label: 'Long. URL',        type: 'numeric', sortable: true,  filterKey: 'url_length',              width: 140, fmt: r => r.url_length ?? '' },
  inlinks_count:        { label: 'Inlinks',          type: 'numeric', sortable: true,  filterKey: 'inlinks_count',           width: 130, fmt: r => r.inlinks_count != null ? r.inlinks_count.toLocaleString('es-ES') : '' },
  unique_inlinks_count: { label: 'Inlinks únicos',   type: 'numeric', sortable: true,  filterKey: 'unique_inlinks_count',    width: 140, fmt: r => r.unique_inlinks_count != null ? r.unique_inlinks_count.toLocaleString('es-ES') : '' },
  outlinks_count:       { label: 'Outlinks',         type: 'numeric', sortable: true,  filterKey: 'outlinks_count',          width: 130, fmt: r => r.outlinks_count != null ? r.outlinks_count.toLocaleString('es-ES') : '' },
  external_outlinks_count: { label: 'Outlinks ext.', type: 'numeric', sortable: true,  filterKey: 'external_outlinks_count', width: 140, fmt: r => r.external_outlinks_count != null ? r.external_outlinks_count.toLocaleString('es-ES') : '' },
  pagerank:             { label: 'PageRank',         type: 'numeric', sortable: true,  filterKey: 'pagerank',                width: 140, fmt: r => r.pagerank != null ? r.pagerank.toFixed(4) : '' },
  redirect_url:         { label: 'Redirige a',      type: 'string',  sortable: false, filterKey: 'redirect_url_contains',   width: 260, fmt: r => r.redirect_url ?? '' },
  redirect_type:        { label: 'Cód. redir.',     type: 'numeric', sortable: false, filterKey: 'redirect_type',            width: 130, fmt: r => r.redirect_type ?? '' },
  indexable:            { label: 'Indexable',        type: 'boolean', sortable: false, filterKey: 'indexable',               width: 130, fmt: r => r.indexable === true ? 'Sí' : r.indexable === false ? 'No' : '' },
  indexability_status:  { label: 'Razón noindex',   type: 'string',  sortable: false, filterKey: 'indexability_status_contains', width: 200, fmt: r => r.indexability_status ?? '' },
  is_internal:          { label: 'Interna',          type: 'boolean', sortable: false, filterKey: 'is_internal',             width: 130, fmt: r => r.is_internal === true ? 'Sí' : r.is_internal === false ? 'No' : '' },
  host:                 { label: 'Host',             type: 'string',  sortable: true,  filterKey: 'host_contains',           width: 200, fmt: r => r.host ?? '' },
  // html_meta fields
  title:                { label: 'Título',          type: 'string',  sortable: true,  filterKey: 'title_contains',          width: 260, fmt: r => r.html_meta?.title ?? '' },
  title_len:            { label: 'Long. título',    type: 'numeric', sortable: true,  filterKey: 'title_len',               width: 140, fmt: r => r.html_meta?.title_len ?? '' },
  meta_description:     { label: 'Descripción',     type: 'string',  sortable: false, filterKey: 'description_contains',    width: 300, fmt: r => r.html_meta?.meta_description ?? '' },
  meta_description_len: { label: 'Long. desc.',     type: 'numeric', sortable: true,  filterKey: 'meta_description_len',    width: 140, fmt: r => r.html_meta?.meta_description_len ?? '' },
  canonical_href:       { label: 'Canonical',       type: 'string',  sortable: false, filterKey: 'canonical_contains',      width: 260, fmt: r => r.html_meta?.canonical_href ?? '' },
  meta_robots:          { label: 'Meta robots',     type: 'string',  sortable: false, filterKey: 'meta_robots_contains',    width: 180, fmt: r => r.html_meta?.meta_robots ?? '' },
  og_title:             { label: 'OG title',        type: 'string',  sortable: false, filterKey: 'og_title_contains',       width: 220, fmt: r => r.html_meta?.og_title ?? '' },
  og_image:             { label: 'OG image',        type: 'string',  sortable: false, filterKey: 'og_image_contains',       width: 240, fmt: r => r.html_meta?.og_image ?? '' },
  og_type:              { label: 'OG type',         type: 'string',  sortable: false, filterKey: 'og_type_contains',        width: 140, fmt: r => r.html_meta?.og_type ?? '' },
  og_url:               { label: 'OG url',          type: 'string',  sortable: false, filterKey: 'og_url_contains',         width: 240, fmt: r => r.html_meta?.og_url ?? '' },
  twitter_card:         { label: 'Twitter card',    type: 'string',  sortable: false, filterKey: 'twitter_card_contains',   width: 150, fmt: r => r.html_meta?.twitter_card ?? '' },
  http_version:         { label: 'HTTP',            type: 'string',  sortable: false, filterKey: 'http_version_contains',   width: 130, fmt: r => r.html_meta?.http_version ?? r.http_version ?? '' },
  last_modified:        { label: 'Last-Modified',   type: 'string',  sortable: false, filterKey: 'last_modified_contains',  width: 180, fmt: r => r.last_modified ?? '' },
  last_crawled_at:      { label: 'Último rastreo',  type: 'string',  sortable: true,                                        width: 170, fmt: r => fmt.date(r.last_crawled_at) },
};

const EXP_DEFAULT_COLUMNS = [
  'url','status_code','title','title_len','meta_description','meta_description_len',
  'content_type','content_length','response_time_ms','crawl_depth',
  'word_count','inlinks_count','outlinks_count','pagerank','indexable','canonical_href'
];

const EXP_TABS = [
  { key: 'all',        label: 'Todas',       icon: 'layers',      filter: null,                                   columns: EXP_DEFAULT_COLUMNS },
  { key: 'html',       label: 'HTML',        icon: 'file-text',   filter: { resource_type: 'html' },              columns: ['url','status_code','title','title_len','meta_description','meta_description_len','word_count','response_time_ms','content_length','inlinks_count','outlinks_count','external_outlinks_count','pagerank','indexable','canonical_href','meta_robots'] },
  { key: 'js',         label: 'JavaScript',  icon: 'file-code',   filter: { resource_type: 'js' },                columns: ['url','status_code','content_length','transfer_size','response_time_ms','last_modified'] },
  { key: 'css',        label: 'CSS',         icon: 'palette',     filter: { resource_type: 'css' },               columns: ['url','status_code','content_length','transfer_size','response_time_ms','last_modified'] },
  { key: 'image',      label: 'Imágenes',    icon: 'image',       filter: { resource_type: 'image' },             columns: ['url','status_code','content_length','transfer_size','response_time_ms'] },
  { key: 'pdf',        label: 'PDFs',        icon: 'file',        filter: { resource_type: 'pdf' },               columns: ['url','status_code','content_length','transfer_size','response_time_ms'] },
  { key: '_div1', divider: true },
  { key: 'internal',   label: 'Internas',    icon: 'home',        filter: { is_internal: true },                  columns: ['url','status_code','title','title_len','meta_description','word_count','inlinks_count','outlinks_count','external_outlinks_count','pagerank','crawl_depth','indexable','canonical_href'] },
  { key: 'external',   label: 'Externas',    icon: 'external-link', filter: { is_internal: false },               columns: ['url','status_code','host','content_type','content_length','response_time_ms'] },
  { key: '_div2', divider: true },
  { key: '2xx',        label: '2xx',         icon: 'check-circle', filter: { status_group: '2xx' },               columns: ['url','status_code','title','title_len','meta_description','meta_description_len','word_count','response_time_ms','inlinks_count','outlinks_count','pagerank','indexable','canonical_href'] },
  { key: '3xx',        label: '3xx',         icon: 'arrow-right', filter: { status_group: '3xx' },                columns: ['url','status_code','redirect_url','redirect_type','response_time_ms','inlinks_count','crawl_depth'] },
  { key: '4xx',        label: '4xx',         icon: 'alert-triangle', filter: { status_group: '4xx' },             columns: ['url','status_code','status_text','inlinks_count','crawl_depth','content_type'] },
  { key: '5xx',        label: '5xx',         icon: 'alert-octagon', filter: { status_group: '5xx' },              columns: ['url','status_code','status_text','response_time_ms','crawl_depth','inlinks_count'] },
  { key: '_div3', divider: true },
  { key: 'indexable',  label: 'Indexables',  icon: 'eye',         filter: { indexable: true, is_internal: true }, columns: ['url','status_code','title','title_len','meta_description','word_count','inlinks_count','pagerank','canonical_href'] },
  { key: 'noindex',    label: 'No indexables', icon: 'eye-off',   filter: { indexable: false, is_internal: true },columns: ['url','status_code','indexability_status','title','meta_robots','canonical_href'] },
];

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
    robots_mode: 'respect',
    render_js: false,
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
