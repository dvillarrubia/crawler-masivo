import { useEffect, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { navigate } from "../hooks.js";
import { Modal, StatusPill } from "../ui.jsx";

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
            {clientId ? `Proyecto ${clientId}` : "Todos los proyectos"} · {clientJobs.length} runs
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
            title="Re-análisis sin re-crawl (T17.2)">Re-analizar</button>
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
          include_patterns: form.include_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
          exclude_patterns: form.exclude_patterns.split("\n").map((s) => s.trim()).filter(Boolean),
          analysis_thresholds: { pagerank_version: Number(form.pagerank_version) },
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
      </div>
      <div className="form-grid">
        <div className="field">
          <label>Profundidad máx.</label>
          <input type="number" min={1} max={50} value={form.max_depth} onChange={set("max_depth")} />
        </div>
        <div className="field">
          <label>URLs máx.</label>
          <input type="number" min={1} value={form.max_urls} onChange={set("max_urls")} />
        </div>
        <div className="field">
          <label>robots.txt</label>
          <select value={form.robots_mode} onChange={set("robots_mode")}>
            <option value="respect">respetar</option>
            <option value="audit">auditar (no bloquea)</option>
            <option value="ignore">ignorar</option>
          </select>
        </div>
        <div className="field">
          <label>PageRank</label>
          <select value={form.pagerank_version} onChange={set("pagerank_version")}>
            <option value={2}>v2 (nofollow diluyente + decay 301)</option>
            <option value={1}>v1 (histórico, comparable con jobs viejos)</option>
          </select>
        </div>
      </div>
      <div className="checkbox-row">
        <input id="njs" type="checkbox" checked={form.render_js} onChange={set("render_js")} />
        <label htmlFor="njs">Renderizar JavaScript (Playwright — más lento)</label>
      </div>
      <div className="checkbox-row">
        <input id="nsm" type="checkbox" checked={form.ingest_sitemaps} onChange={set("ingest_sitemaps")} />
        <label htmlFor="nsm">Ingerir sitemaps (habilita huérfanas reales y cobertura)</label>
      </div>
      <div className="form-grid">
        <div className="field">
          <label>Incluir patrones (regex, uno por línea)</label>
          <textarea rows={2} value={form.include_patterns} onChange={set("include_patterns")} />
        </div>
        <div className="field">
          <label>Excluir patrones</label>
          <textarea rows={2} value={form.exclude_patterns} onChange={set("exclude_patterns")} />
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
