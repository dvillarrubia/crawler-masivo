import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { navigate } from "../hooks.js";
import { Modal } from "../ui.jsx";

/** Rastreos: gestión real de jobs + modal "Nuevo rastreo" (superconjunto del legacy). */
export default function JobsView() {
  const { clientJobs, clientId, setJobId, reloadJobs } = useCtx();
  const [showNew, setShowNew] = useState(false);

  const open = (j) => {
    setJobId(j.id);
    navigate("overview");
  };

  return (
    <div>
      <div className="row between">
        <div>
          <h1 className="page-title">Rastreos</h1>
          <p className="page-sub">
            Cada fila es un rastreo completo del sitio (un "run"): el robot recorre las páginas,
            guarda todo lo que ve y al terminar ejecuta el análisis SEO automáticamente.
            {" "}{clientId ? `Proyecto ${clientId}` : "Todos los proyectos"} · {clientJobs.length} runs.
          </p>
        </div>
        <span className="row" style={{ gap: 6 }}>
          <ImportButton onDone={reloadJobs} />
          <button onClick={() => setShowNew(true)}>+ Nuevo rastreo</button>
        </span>
      </div>

      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Nombre</th><th>Proyecto</th><th>Estado</th>
              <th className="num">URLs</th><th className="num">Fallidas</th>
              <th>Creado</th><th></th>
            </tr>
          </thead>
          <tbody>
            {clientJobs.map((j) => (
              <tr key={j.id} onClick={() => open(j)}>
                <td>{j.name}</td>
                <td>{j.client_id || "—"}</td>
                <td><JobStatus job={j} /></td>
                <td className="num">{j.total_urls_crawled.toLocaleString("es")}</td>
                <td className="num">{j.total_urls_failed.toLocaleString("es")}</td>
                <td>{new Date(j.created_at).toLocaleString("es")}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <JobActions job={j} onDone={reloadJobs} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showNew && (
        <NewCrawlModal
          clientId={clientId}
          onClose={() => setShowNew(false)}
          onCreated={(job) => {
            setShowNew(false);
            reloadJobs();
            setJobId(job.id);
            navigate("overview");
          }}
        />
      )}
    </div>
  );
}

/** Importa un backup ZIP (paridad legacy). */
function ImportButton({ onDone }) {
  const [busy, setBusy] = useState(false);
  const pick = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".zip";
    input.onchange = async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      setBusy(true);
      try {
        const r = await api.importJob(file, {});
        alert(`Importado como job ${r.new_job_id}. Filas: ${JSON.stringify(r.rows_imported)}`);
        onDone();
      } catch (e) { alert(`Import falló: ${e.message}`); }
      setBusy(false);
    };
    input.click();
  };
  return (
    <button className="secondary" disabled={busy} onClick={pick}>
      {busy ? "Importando…" : "Importar backup"}
    </button>
  );
}

function JobStatus({ job }) {
  const map = { completed: "s2xx", running: "s3xx", pending: "sother",
                failed: "s4xx", cancelled: "sother" };
  return <span className={`pill ${map[job.status] || "sother"}`}>{job.status}</span>;
}

function JobActions({ job, onDone }) {
  const [busy, setBusy] = useState(false);
  const act = async (fn, confirmMsg) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true);
    try { await fn(); onDone(); } catch (e) { alert(e.message); }
    setBusy(false);
  };
  return (
    <span className="row">
      {["running", "pending"].includes(job.status) && (
        <button className="secondary" disabled={busy}
          onClick={() => act(() => api.cancelJob(job.id))}>Cancelar</button>
      )}
      {["cancelled", "failed"].includes(job.status) && (
        <button className="secondary" disabled={busy}
          onClick={() => act(() => api.resumeJob(job.id))}
          title="Reanuda el crawl desde la frontera pendiente">Reanudar</button>
      )}
      {job.status === "completed" && (
        <>
          <button className="secondary" disabled={busy}
            onClick={() => act(() => api.reanalyze(job.id))}
            title="Vuelve a ejecutar el análisis SEO sobre los datos ya rastreados, sin re-rastrear. Útil tras cambiar segmentos o umbrales.">Re-analizar</button>
          <a href={api.backupUrl(job.id)} title="Backup ZIP completo (NDJSON)">
            <button className="secondary">Backup</button>
          </a>
        </>
      )}
      <button className="secondary" disabled={busy}
        onClick={() => act(() => api.deleteJob(job.id),
          `¿Borrar el job "${job.name}" y todos sus datos?`)}>Borrar</button>
    </span>
  );
}

/** Ayuda breve bajo un campo del formulario. */
function Hint({ children }) {
  return <p className="proxy-tag" style={{ margin: "3px 0 0" }}>{children}</p>;
}

/** Checkbox con título y explicación de qué activa. */
function FlagRow({ id, checked, onChange, title, children }) {
  return (
    <div className="checkbox-row" style={{ alignItems: "flex-start" }}>
      <input id={id} type="checkbox" checked={checked} onChange={onChange} />
      <label htmlFor={id}>
        <b>{title}</b>
        {children && <span style={{ display: "block", fontSize: 11.5, color: "var(--ink-muted)" }}>{children}</span>}
      </label>
    </div>
  );
}

/** Campo numérico compacto con etiqueta y ayuda opcional. */
function NumField({ label, value, onChange, hint, min = 0, max, step }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type="number" min={min} max={max} step={step} value={value} onChange={onChange} />
      {hint && <Hint>{hint}</Hint>}
    </div>
  );
}

const UA_PRESETS = {
  chrome_win: ["Chrome · Windows", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"],
  chrome_mac: ["Chrome · macOS", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"],
  safari_mac: ["Safari · macOS", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"],
  firefox_win: ["Firefox · Windows", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"],
  edge_win: ["Edge · Windows", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"],
  googlebot: ["Googlebot", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"],
  custom: ["Personalizado…", ""],
};

/** "clave: valor" por línea → objeto. */
const parseKV = (text, sep) => {
  const out = {};
  for (const line of (text || "").split("\n")) {
    const idx = line.indexOf(sep);
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return out;
};

/** Modal "Nuevo rastreo": superconjunto del legacy + capas v2, por pestañas. */
function NewCrawlModal({ clientId, onClose, onCreated }) {
  const [tab, setTab] = useState("general");
  const [form, setForm] = useState({
    name: "", client_id: clientId || "", seeds: "",
    // General
    max_depth: 3, max_urls: 50000, robots_mode: "respect",
    user_agent_preset: "chrome_win", user_agent_custom: "",
    // Qué rastrear
    render_js: false, ingest_sitemaps: true, follow_external: false,
    crawl_subdomains: false, follow_nofollow: false,
    crawl_images: true, crawl_css: true, crawl_js: true, crawl_pdfs: true,
    crawl_fonts: false, crawl_svg: true, crawl_other: true,
    check_external_resources: false,
    include_patterns: "", exclude_patterns: "",
    max_url_length: 0, max_folder_depth: 0,
    // Velocidad
    concurrent_requests: 32, concurrent_requests_per_domain: 8,
    download_timeout: 30, retry_count: 2, request_delay: 0,
    autothrottle_enabled: true, autothrottle_target_concurrency: 8,
    max_runtime_hours: 6,
    // HTTP
    custom_headers: "", accept_language: "", cookies: "",
    basic_auth_user: "", basic_auth_password: "",
    // Extracción
    extract_structured_data: true, extract_hreflang: true,
    extract_security_headers: true, extract_page_content: true,
    store_raw_html: false, geo_analysis: false,
    // Análisis
    pagerank_version: 2, detect_soft_404: true, trap_detection: true,
    edge_classification: true, near_duplicates: true, unique_content: true,
    title_min_length: 10, title_max_length: 60,
    description_min_length: 50, description_max_length: 160,
    min_word_count: 200, max_redirect_chain_length: 2, max_outlinks: 100,
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) =>
    setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = {
        name: form.name || form.seeds.split("\n")[0],
        client_id: form.client_id || null,
        seeds: form.seeds.split("\n").map((s) => s.trim()).filter(Boolean),
        config: {
          max_depth: Number(form.max_depth),
          max_urls: Number(form.max_urls),
          robots_mode: form.robots_mode,
          user_agent: form.user_agent_preset === "custom"
            ? form.user_agent_custom
            : UA_PRESETS[form.user_agent_preset][1],
          render_js: form.render_js,
          ingest_sitemaps: form.ingest_sitemaps,
          follow_external: form.follow_external,
          detect_soft_404: form.detect_soft_404,
          edge_classification: form.edge_classification,
          geo_analysis: form.geo_analysis,
          trap_detection: { enabled: form.trap_detection },
          concurrent_requests: Number(form.concurrent_requests),
          concurrent_requests_per_domain: Number(form.concurrent_requests_per_domain),
          include_patterns: form.include_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
          exclude_patterns: form.exclude_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
          resource_types: {
            crawl_images: form.crawl_images, crawl_css: form.crawl_css,
            crawl_js: form.crawl_js, crawl_pdfs: form.crawl_pdfs,
            crawl_fonts: form.crawl_fonts, crawl_svg: form.crawl_svg,
            crawl_other: form.crawl_other,
            check_external_resources: form.check_external_resources,
          },
          crawl_behavior: {
            download_timeout: Number(form.download_timeout),
            retry_count: Number(form.retry_count),
            request_delay: Number(form.request_delay),
            autothrottle_enabled: form.autothrottle_enabled,
            autothrottle_target_concurrency: Number(form.autothrottle_target_concurrency),
            follow_nofollow: form.follow_nofollow,
            crawl_subdomains: form.crawl_subdomains,
            max_runtime_hours: Number(form.max_runtime_hours),
          },
          url_filters: {
            max_url_length: Number(form.max_url_length),
            max_folder_depth: Number(form.max_folder_depth),
          },
          extraction: {
            extract_structured_data: form.extract_structured_data,
            extract_hreflang: form.extract_hreflang,
            extract_security_headers: form.extract_security_headers,
            extract_page_content: form.extract_page_content,
            store_raw_html: form.store_raw_html,
          },
          http: {
            custom_headers: parseKV(form.custom_headers, ":"),
            accept_language: form.accept_language,
            cookies: parseKV(form.cookies, "="),
            basic_auth_user: form.basic_auth_user,
            basic_auth_password: form.basic_auth_password,
          },
          analysis_thresholds: {
            pagerank_version: Number(form.pagerank_version),
            near_duplicate_detection: form.near_duplicates ? "simhash" : "off",
            unique_content_analysis: form.unique_content,
            title_min_length: Number(form.title_min_length),
            title_max_length: Number(form.title_max_length),
            description_min_length: Number(form.description_min_length),
            description_max_length: Number(form.description_max_length),
            min_word_count: Number(form.min_word_count),
            max_redirect_chain_length: Number(form.max_redirect_chain_length),
            max_outlinks: Number(form.max_outlinks),
          },
        },
      };
      const job = await api.createJob(payload);
      onCreated(job);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  const TABS = [
    ["general", "General"],
    ["spider", "Qué rastrear"],
    ["velocidad", "Velocidad"],
    ["http", "HTTP"],
    ["extraccion", "Extracción"],
    ["analisis", "Análisis"],
  ];

  return (
    <Modal title="Nuevo rastreo" onClose={onClose}>
      {error && <div className="alert">{error}</div>}

      <div className="form-grid">
        <div className="field">
          <label>Nombre</label>
          <input type="text" value={form.name} onChange={set("name")} placeholder="Auditoría julio" />
        </div>
        <div className="field">
          <label>Proyecto (client_id)</label>
          <input type="text" value={form.client_id} onChange={set("client_id")} placeholder="mi-cliente" />
          <Hint>Agrupa los rastreos de un mismo sitio: habilita el diff entre runs, la salud del proyecto y los segmentos.</Hint>
        </div>
      </div>
      <div className="field">
        <label>Semillas (una URL por línea)</label>
        <textarea rows={2} value={form.seeds} onChange={set("seeds")} placeholder="https://www.ejemplo.com/" />
        <Hint>Puntos de partida. Normalmente basta la portada: el robot descubre el resto siguiendo enlaces.</Hint>
      </div>

      <div className="toolbar" style={{ margin: "10px 0 8px" }}>
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "" : "secondary"}
            style={{ padding: "3px 10px", fontSize: 11.5 }}
            onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "general" && (
        <>
          <div className="form-grid">
            <NumField label="Profundidad máx." value={form.max_depth} onChange={set("max_depth")}
              min={1} max={50} hint="Clics desde la semilla. 3–4 cubre la mayoría de sitios." />
            <NumField label="URLs máx." value={form.max_urls} onChange={set("max_urls")}
              min={1} hint="Tope de seguridad: el rastreo se detiene al alcanzarlo." />
            <div className="field">
              <label>robots.txt</label>
              <select value={form.robots_mode} onChange={set("robots_mode")}>
                <option value="respect">respetar</option>
                <option value="audit">auditar (no bloquea)</option>
                <option value="ignore">ignorar</option>
              </select>
              <Hint>«Respetar» = como Google. «Auditar» = entra igualmente pero marca lo que estaría bloqueado. «Ignorar» = se lo salta sin marcar.</Hint>
            </div>
            <div className="field">
              <label>Identidad del navegador (user-agent)</label>
              <select value={form.user_agent_preset} onChange={set("user_agent_preset")}>
                {Object.entries(UA_PRESETS).map(([k, [label]]) => (
                  <option key={k} value={k}>{label}</option>
                ))}
              </select>
              <Hint>Cómo se presenta el robot ante el servidor. «Googlebot» sirve para destapar cloaking (webs que enseñan contenido distinto a Google).</Hint>
            </div>
          </div>
          {form.user_agent_preset === "custom" && (
            <div className="field">
              <label>User-agent personalizado</label>
              <input type="text" className="mono" value={form.user_agent_custom}
                onChange={set("user_agent_custom")} placeholder="MiBot/1.0 (+https://…)" />
            </div>
          )}
        </>
      )}

      {tab === "spider" && (
        <>
          <FlagRow id="f-js" checked={form.render_js} onChange={set("render_js")}
            title="Renderizar JavaScript">
            Abre cada página en un navegador real (Chromium) para ver el contenido que solo aparece tras ejecutar JS.
            Necesario en webs React/Vue/etc. Mucho más lento: actívalo solo si el sitio lo necesita.
          </FlagRow>
          <FlagRow id="f-sm" checked={form.ingest_sitemaps} onChange={set("ingest_sitemaps")}
            title="Leer los sitemaps del sitio">
            Cruza los sitemap.xml con el rastreo: detecta huérfanas reales, sitemaps incompletos y fechas falsas.
          </FlagRow>
          <FlagRow id="f-ext" checked={form.follow_external} onChange={set("follow_external")}
            title="Seguir enlaces externos">
            Rastrear también las páginas de OTROS dominios enlazadas desde el sitio. Normalmente no: dispara el volumen.
          </FlagRow>
          <FlagRow id="f-sub" checked={form.crawl_subdomains} onChange={set("crawl_subdomains")}
            title="Rastrear subdominios">
            Tratar blog.ejemplo.com, tienda.ejemplo.com… como parte del sitio.
          </FlagRow>
          <FlagRow id="f-nf" checked={form.follow_nofollow} onChange={set("follow_nofollow")}
            title="Seguir enlaces nofollow">
            Entrar también por enlaces marcados nofollow (Google puede hacerlo). Se registran igual aunque no se sigan.
          </FlagRow>

          <h3 style={{ margin: "12px 0 4px" }}>Recursos a registrar</h3>
          <Hint>Qué tipos de archivo referenciados se apuntan como recursos de cada página (no se «navegan», se registran y se comprueban).</Hint>
          <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr 1fr", marginTop: 6 }}>
            <FlagRow id="r-img" checked={form.crawl_images} onChange={set("crawl_images")} title="Imágenes" />
            <FlagRow id="r-css" checked={form.crawl_css} onChange={set("crawl_css")} title="CSS" />
            <FlagRow id="r-js" checked={form.crawl_js} onChange={set("crawl_js")} title="JavaScript" />
            <FlagRow id="r-pdf" checked={form.crawl_pdfs} onChange={set("crawl_pdfs")} title="PDFs" />
            <FlagRow id="r-font" checked={form.crawl_fonts} onChange={set("crawl_fonts")} title="Fuentes" />
            <FlagRow id="r-svg" checked={form.crawl_svg} onChange={set("crawl_svg")} title="SVG" />
            <FlagRow id="r-other" checked={form.crawl_other} onChange={set("crawl_other")} title="Otros" />
            <FlagRow id="r-extres" checked={form.check_external_resources} onChange={set("check_external_resources")} title="Verificar externos" />
          </div>

          <div className="form-grid" style={{ marginTop: 10 }}>
            <div className="field">
              <label>Incluir solo estas rutas (regex, una por línea)</label>
              <textarea rows={2} value={form.include_patterns} onChange={set("include_patterns")} />
              <Hint>Si pones algo, solo se rastrean las URLs que encajen. Ej.: <code>^/blog/</code></Hint>
            </div>
            <div className="field">
              <label>Excluir estas rutas</label>
              <textarea rows={2} value={form.exclude_patterns} onChange={set("exclude_patterns")} />
              <Hint>Ej.: <code>/carrito</code>, <code>\?orden=</code></Hint>
            </div>
          </div>
          <div className="form-grid">
            <NumField label="Longitud máx. de URL (0 = sin límite)" value={form.max_url_length}
              onChange={set("max_url_length")} hint="Las URLs más largas ni se rastrean." />
            <NumField label="Profundidad máx. de carpetas (0 = sin límite)" value={form.max_folder_depth}
              onChange={set("max_folder_depth")} hint="Corta rutas tipo /a/b/c/d/e/… más profundas que esto." />
          </div>
        </>
      )}

      {tab === "velocidad" && (
        <>
          <Hint>Cuánto aprieta el robot al servidor. Los valores por defecto son seguros; súbelos solo si el servidor aguanta, o bájalos (y añade retardo) para sitios delicados.</Hint>
          <div className="form-grid" style={{ marginTop: 6 }}>
            <NumField label="Peticiones simultáneas" value={form.concurrent_requests}
              onChange={set("concurrent_requests")} min={1} max={128}
              hint="Total de peticiones en vuelo a la vez." />
            <NumField label="Simultáneas por dominio" value={form.concurrent_requests_per_domain}
              onChange={set("concurrent_requests_per_domain")} min={1} max={64}
              hint="Las que caen sobre un mismo dominio. Esta es la que nota el servidor." />
            <NumField label="Timeout de descarga (s)" value={form.download_timeout}
              onChange={set("download_timeout")} min={5} max={180}
              hint="Se abandona la página si tarda más." />
            <NumField label="Reintentos" value={form.retry_count}
              onChange={set("retry_count")} min={0} max={10}
              hint="Reintentos ante errores temporales (502, 503, timeouts…)." />
            <NumField label="Retardo entre peticiones (s)" value={form.request_delay}
              onChange={set("request_delay")} step={0.1}
              hint="Pausa fija entre peticiones. 0 = sin pausa." />
            <NumField label="Duración máxima (horas)" value={form.max_runtime_hours}
              onChange={set("max_runtime_hours")} min={1} max={72}
              hint="El rastreo se corta solo al llegar aquí." />
          </div>
          <FlagRow id="f-at" checked={form.autothrottle_enabled} onChange={set("autothrottle_enabled")}
            title="Acelerador automático (autothrottle)">
            Ajusta la velocidad sobre la marcha según lo rápido que responda el servidor: acelera si va sobrado y frena si sufre.
          </FlagRow>
          {form.autothrottle_enabled && (
            <div className="form-grid">
              <NumField label="Concurrencia objetivo del acelerador" value={form.autothrottle_target_concurrency}
                onChange={set("autothrottle_target_concurrency")} min={1} max={32} step={0.5}
                hint="A cuántas peticiones simultáneas intenta estabilizarse." />
            </div>
          )}
        </>
      )}

      {tab === "http" && (
        <>
          <Hint>Para sitios que exigen algo especial: entornos de staging con contraseña, contenido por idioma, cookies de sesión o cabeceras a medida.</Hint>
          <div className="form-grid" style={{ marginTop: 6 }}>
            <div className="field">
              <label>Cabeceras personalizadas (una por línea, clave: valor)</label>
              <textarea rows={2} className="mono" value={form.custom_headers}
                onChange={set("custom_headers")} placeholder="X-Bypass-Cache: 1" />
            </div>
            <div className="field">
              <label>Cookies (una por línea, clave=valor)</label>
              <textarea rows={2} className="mono" value={form.cookies}
                onChange={set("cookies")} placeholder="sesion=abc123" />
            </div>
          </div>
          <div className="form-grid">
            <div className="field">
              <label>Accept-Language</label>
              <input type="text" value={form.accept_language} onChange={set("accept_language")}
                placeholder="es-ES,es;q=0.9" />
              <Hint>Idioma que pide el robot. Útil en sitios que sirven contenido distinto según idioma.</Hint>
            </div>
            <div className="field">
              <label>Autenticación básica (usuario / contraseña)</label>
              <div className="row" style={{ gap: 6 }}>
                <input type="text" value={form.basic_auth_user} onChange={set("basic_auth_user")} placeholder="usuario" />
                <input type="password" value={form.basic_auth_password} onChange={set("basic_auth_password")} placeholder="contraseña" />
              </div>
              <Hint>Para entornos de pre-producción protegidos con HTTP Basic Auth.</Hint>
            </div>
          </div>
        </>
      )}

      {tab === "extraccion" && (
        <>
          <Hint>Qué se guarda de cada página además de lo básico. Desactivar algo aligera el rastreo pero deja su vista sin datos.</Hint>
          <FlagRow id="e-sd" checked={form.extract_structured_data} onChange={set("extract_structured_data")}
            title="Datos estructurados">
            JSON-LD, microdata y RDFa (schema.org): lo que alimenta los resultados enriquecidos de Google.
          </FlagRow>
          <FlagRow id="e-hl" checked={form.extract_hreflang} onChange={set("extract_hreflang")}
            title="Hreflang">
            Las relaciones de idioma/país entre versiones de cada página.
          </FlagRow>
          <FlagRow id="e-sec" checked={form.extract_security_headers} onChange={set("extract_security_headers")}
            title="Cabeceras de seguridad">
            HTTPS, HSTS, CSP, X-Frame-Options… — alimenta la pestaña Seguridad y sus incidencias.
          </FlagRow>
          <FlagRow id="e-pc" checked={form.extract_page_content} onChange={set("extract_page_content")}
            title="Contenido de la página">
            El texto principal limpio (y su versión markdown). Necesario para la semántica, el contenido único y los near-duplicates.
          </FlagRow>
          <FlagRow id="e-raw" checked={form.store_raw_html} onChange={set("store_raw_html")}
            title="Guardar el HTML completo">
            Almacena el HTML bruto de cada página. Multiplica el espacio en disco; solo para auditorías forenses.
          </FlagRow>
          <FlagRow id="e-geo" checked={form.geo_analysis} onChange={set("geo_analysis")}
            title="Comparar HTML crudo vs. renderizado (GEO)">
            Descarga cada página también SIN ejecutar JavaScript y compara: lo que solo existe tras el JS es invisible
            para los buscadores de IA y el primer pase de Google. Duplica las peticiones.
          </FlagRow>
        </>
      )}

      {tab === "analisis" && (
        <>
          <div className="form-grid">
            <div className="field">
              <label>Cálculo de autoridad (PageRank)</label>
              <select value={form.pagerank_version} onChange={set("pagerank_version")}>
                <option value={2}>v2 — realista (recomendado)</option>
                <option value={1}>v1 — histórico</option>
              </select>
              <Hint>v2 imita mejor a Google: los nofollow pierden autoridad y las redirecciones la rebajan. v1 solo para comparar con rastreos antiguos.</Hint>
            </div>
          </div>
          <FlagRow id="a-soft" checked={form.detect_soft_404} onChange={set("detect_soft_404")}
            title="Detectar soft-404">
            Aprende qué pinta tiene la página de error del sitio y marca las que devuelven «200 OK» siendo errores disfrazados.
          </FlagRow>
          <FlagRow id="a-trap" checked={form.trap_detection} onChange={set("trap_detection")}
            title="Detectar trampas de rastreo">
            Corta los patrones de URLs infinitas (calendarios, filtros…) y los reporta, porque a Google le pasaría lo mismo.
          </FlagRow>
          <FlagRow id="a-edge" checked={form.edge_classification} onChange={set("edge_classification")}
            title="Clasificar los enlaces por su papel (arquitectura)">
            Distingue enlaces de contenido, menú, footer, listados y paginación. Habilita profundidad de clic real, flujos entre secciones y checks de arquitectura.
          </FlagRow>
          <FlagRow id="a-dup" checked={form.near_duplicates} onChange={set("near_duplicates")}
            title="Detectar contenido casi duplicado">
            Encuentra páginas casi idénticas aunque no sean copias exactas (huella simhash).
          </FlagRow>
          <FlagRow id="a-uniq" checked={form.unique_content} onChange={set("unique_content")}
            title="Medir el contenido único de cada página">
            Descuenta la plantilla repetida de la sección y calcula cuánto texto propio queda. Detecta páginas «huecas».
          </FlagRow>

          <h3 style={{ margin: "12px 0 4px" }}>Umbrales de las incidencias</h3>
          <Hint>A partir de qué valores se marca cada problema. Los defectos siguen las prácticas habituales; ajústalos al criterio del proyecto.</Hint>
          <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr", marginTop: 6 }}>
            <NumField label="Title: mín. caracteres" value={form.title_min_length} onChange={set("title_min_length")} />
            <NumField label="Title: máx. caracteres" value={form.title_max_length} onChange={set("title_max_length")} />
            <NumField label="Description: mín." value={form.description_min_length} onChange={set("description_min_length")} />
            <NumField label="Description: máx." value={form.description_max_length} onChange={set("description_max_length")} />
            <NumField label="Palabras mínimas por página" value={form.min_word_count} onChange={set("min_word_count")} />
            <NumField label="Saltos de redirección tolerados" value={form.max_redirect_chain_length} onChange={set("max_redirect_chain_length")} />
            <NumField label="Enlaces salientes máx. por página" value={form.max_outlinks} onChange={set("max_outlinks")} />
          </div>
        </>
      )}

      <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
        <button className="secondary" onClick={onClose}>Cancelar</button>
        <button disabled={busy || !form.seeds.trim()} onClick={submit}>
          {busy ? "Creando…" : "Lanzar rastreo"}
        </button>
      </div>
    </Modal>
  );
}
