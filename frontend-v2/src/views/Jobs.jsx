import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { navigate } from "../hooks.js";
import { Modal } from "../ui.jsx";

/** Rastreos: gestión real de jobs + modal "Nuevo rastreo" (del Empresarial). */
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
        <span style={{ display: "block", fontSize: 11.5, color: "var(--ink-muted)" }}>{children}</span>
      </label>
    </div>
  );
}

/** Modal "Nuevo rastreo": mapea casi 1:1 al JobConfig real. */
function NewCrawlModal({ clientId, onClose, onCreated }) {
  const [form, setForm] = useState({
    name: "",
    client_id: clientId || "",
    seeds: "",
    max_depth: 3,
    max_urls: 50000,
    render_js: false,
    robots_mode: "respect",
    ingest_sitemaps: true,
    include_patterns: "",
    exclude_patterns: "",
    pagerank_version: 2,
    // capas de análisis extra (todas opcionales)
    detect_soft_404: true,
    trap_detection: true,
    edge_classification: true,
    near_duplicates: true,
    unique_content: true,
    geo_analysis: false,
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
          render_js: form.render_js,
          robots_mode: form.robots_mode,
          ingest_sitemaps: form.ingest_sitemaps,
          detect_soft_404: form.detect_soft_404,
          edge_classification: form.edge_classification,
          geo_analysis: form.geo_analysis,
          trap_detection: { enabled: form.trap_detection },
          include_patterns: form.include_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
          exclude_patterns: form.exclude_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
          analysis_thresholds: {
            pagerank_version: Number(form.pagerank_version),
            near_duplicate_detection: form.near_duplicates ? "simhash" : "off",
            unique_content_analysis: form.unique_content,
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
        </div>
      </div>
      <div className="field">
        <label>Semillas (una URL por línea)</label>
        <textarea rows={3} value={form.seeds} onChange={set("seeds")} placeholder="https://www.ejemplo.com/" />
        <Hint>Puntos de partida del rastreo. Normalmente basta con la portada: el robot descubre el resto siguiendo enlaces.</Hint>
      </div>
      <div className="form-grid">
        <div className="field">
          <label>Profundidad máx.</label>
          <input type="number" min={1} max={50} value={form.max_depth} onChange={set("max_depth")} />
          <Hint>Cuántos clics desde la semilla como máximo. 3–4 cubre la mayoría de sitios.</Hint>
        </div>
        <div className="field">
          <label>URLs máx.</label>
          <input type="number" min={1} value={form.max_urls} onChange={set("max_urls")} />
          <Hint>Tope de seguridad: el rastreo se detiene al alcanzarlo.</Hint>
        </div>
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
          <label>Cálculo de autoridad (PageRank)</label>
          <select value={form.pagerank_version} onChange={set("pagerank_version")}>
            <option value={2}>v2 — realista (recomendado)</option>
            <option value={1}>v1 — histórico</option>
          </select>
          <Hint>v2 imita mejor a Google: los nofollow pierden autoridad y las redirecciones la rebajan. Usa v1 solo si necesitas comparar con rastreos antiguos hechos con v1.</Hint>
        </div>
      </div>

      <h3 style={{ margin: "14px 0 6px" }}>Cómo rastrear</h3>
      <FlagRow id="njs" checked={form.render_js} onChange={set("render_js")}
        title="Renderizar JavaScript">
        Abre cada página en un navegador real (Chromium) para ver el contenido que solo aparece tras ejecutar JS.
        Necesario en webs hechas con React/Vue/etc. Mucho más lento y pesado: actívalo solo si el sitio lo necesita.
      </FlagRow>
      <FlagRow id="nsm" checked={form.ingest_sitemaps} onChange={set("ingest_sitemaps")}
        title="Leer los sitemaps del sitio">
        Descarga los sitemap.xml y los cruza con el rastreo. Es lo que permite detectar huérfanas reales
        (URLs que el sitemap declara pero a las que no se llega navegando) y sitemaps incompletos o con fechas falsas.
      </FlagRow>

      <h3 style={{ margin: "14px 0 6px" }}>Capas de análisis (se ejecutan al terminar el rastreo)</h3>
      <FlagRow id="nsoft" checked={form.detect_soft_404} onChange={set("detect_soft_404")}
        title="Detectar soft-404">
        Pide una URL inventada a cada dominio para aprender qué pinta tiene su página de error,
        y luego marca las páginas que devuelven «200 OK» pero en realidad son un error disfrazado.
      </FlagRow>
      <FlagRow id="ntrap" checked={form.trap_detection} onChange={set("trap_detection")}
        title="Detectar trampas de rastreo">
        Corta los patrones de URLs infinitas (calendarios, combinaciones de filtros…) para no quemar el
        presupuesto de rastreo, y los reporta como incidencia porque a Google le pasaría lo mismo.
      </FlagRow>
      <FlagRow id="nedge" checked={form.edge_classification} onChange={set("edge_classification")}
        title="Clasificar los enlaces por su papel (arquitectura)">
        Distingue si cada enlace viene del contenido, del menú, del footer, de un listado o de la paginación.
        Habilita la profundidad de clic real, los flujos de autoridad entre secciones y los checks de arquitectura.
      </FlagRow>
      <FlagRow id="ndup" checked={form.near_duplicates} onChange={set("near_duplicates")}
        title="Detectar contenido casi duplicado">
        Encuentra páginas casi idénticas aunque no sean copias exactas (huella simhash del texto).
        Candidatas a fusionarse o diferenciarse.
      </FlagRow>
      <FlagRow id="nuniq" checked={form.unique_content} onChange={set("unique_content")}
        title="Medir el contenido único de cada página">
        Descuenta la plantilla repetida de la sección (menús, bloques legales…) y calcula cuánto texto propio
        queda de verdad en cada página. Detecta fichas y posts «huecos».
      </FlagRow>
      <FlagRow id="ngeo" checked={form.geo_analysis} onChange={set("geo_analysis")}
        title="Comparar HTML crudo vs. renderizado (GEO)">
        Descarga cada página también SIN ejecutar JavaScript y compara: lo que solo existe tras el JS es invisible
        para los buscadores de IA (ChatGPT, Perplexity…) y para el primer pase de Google. Duplica las peticiones.
      </FlagRow>

      <div className="form-grid" style={{ marginTop: 10 }}>
        <div className="field">
          <label>Incluir solo estas rutas (regex, una por línea)</label>
          <textarea rows={2} value={form.include_patterns} onChange={set("include_patterns")} />
          <Hint>Si pones algo aquí, solo se rastrean las URLs que encajen. Ej.: <code>^/blog/</code></Hint>
        </div>
        <div className="field">
          <label>Excluir estas rutas</label>
          <textarea rows={2} value={form.exclude_patterns} onChange={set("exclude_patterns")} />
          <Hint>Las URLs que encajen no se rastrean. Ej.: <code>/carrito</code>, <code>\?orden=</code></Hint>
        </div>
      </div>
      <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
        <button className="secondary" onClick={onClose}>Cancelar</button>
        <button disabled={busy || !form.seeds.trim()} onClick={submit}>
          {busy ? "Creando…" : "Lanzar rastreo"}
        </button>
      </div>
    </Modal>
  );
}
