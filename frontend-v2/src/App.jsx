import { createContext, useContext, useMemo } from "react";

import { api } from "./api.js";
import { navigate, useAsync, useHashRoute, useStored } from "./hooks.js";
import { Spinner } from "./ui.jsx";
import ConfigView from "./views/Config.jsx";
import DiffView from "./views/DiffView.jsx";
import ExplorerView from "./views/Explorer.jsx";
import HealthView from "./views/Health.jsx";
import IssuesView from "./views/Issues.jsx";
import JobsView from "./views/Jobs.jsx";
import LogsView from "./views/Logs.jsx";
import OverviewView from "./views/Overview.jsx";

/** Contexto global: proyecto (client_id) + run (job) + segmento (T12). */
const Ctx = createContext(null);
export const useCtx = () => useContext(Ctx);

const NAV = [
  { group: "Proyecto", items: [
    ["salud", "Salud del proyecto"],
    ["jobs", "Rastreos"],
  ]},
  { group: "Run", items: [
    ["overview", "Overview"],
    ["explorer", "Explorador"],
    ["issues", "Incidencias"],
    ["diff", "Diff entre crawls"],
  ]},
  { group: "Análisis", items: [
    ["semantica", "Semántica", "legacy"],
    ["logs", "Logs", "blocked"],
  ]},
  { group: "Ajustes", items: [
    ["config", "Configuración"],
  ]},
];

export default function App() {
  const route = useHashRoute();
  const view = route[0] || "jobs";

  const [clientId, setClientId] = useStored("ctx.client", "");
  const [jobId, setJobId] = useStored("ctx.job", "");
  const [segmentId, setSegmentId] = useStored("ctx.segment", "");

  const jobsQ = useAsync(() => api.jobs({ page_size: 100 }), []);
  const jobs = jobsQ.data ? jobsQ.data.items : [];

  const clients = useMemo(() => {
    const set = new Set(jobs.map((j) => j.client_id).filter(Boolean));
    return [...set].sort();
  }, [jobs]);

  const clientJobs = useMemo(
    () => (clientId ? jobs.filter((j) => j.client_id === clientId) : jobs),
    [jobs, clientId],
  );

  const job = jobs.find((j) => j.id === jobId) || null;

  const segsQ = useAsync(
    () => (clientId ? api.segments(clientId) : Promise.resolve([])),
    [clientId],
  );
  const segments = segsQ.data || [];

  const ctx = {
    clientId, setClientId,
    jobId, setJobId,
    segmentId: segmentId ? Number(segmentId) : null, setSegmentId,
    job, jobs, clientJobs, clients, segments,
    reloadJobs: jobsQ.reload,
  };

  const views = {
    salud: HealthView,
    jobs: JobsView,
    overview: OverviewView,
    explorer: ExplorerView,
    issues: IssuesView,
    diff: DiffView,
    logs: LogsView,
    config: ConfigView,
  };
  const View = views[view] || JobsView;

  return (
    <Ctx.Provider value={ctx}>
      <div className="shell">
        <nav className="sidebar">
          <div className="brand">
            Crawler SEO
            <small>Consola · LIN3S</small>
          </div>
          {NAV.map(({ group, items }) => (
            <div key={group}>
              <div className="group">{group}</div>
              {items.map(([key, label, kind]) =>
                kind === "legacy" ? (
                  <a key={key} href="/legacy" target="_blank" rel="noreferrer">
                    {label} <span className="lock">↗</span>
                  </a>
                ) : (
                  <a
                    key={key}
                    href={`#/${key}`}
                    className={view === key ? "active" : ""}
                  >
                    {label}
                    {kind === "blocked" && <span className="lock">fuente ✗</span>}
                  </a>
                ),
              )}
            </div>
          ))}
        </nav>

        <div className="main">
          <ContextBar />
          <div className="content">
            {jobsQ.loading ? <Spinner /> : <View />}
          </div>
        </div>
      </div>
    </Ctx.Provider>
  );
}

function ContextBar() {
  const { clientId, setClientId, jobId, setJobId, segmentId, setSegmentId,
          clients, clientJobs, job, segments } = useCtx();

  return (
    <div className="contextbar">
      <span>
        <label>Proyecto</label>
        <select value={clientId} onChange={(e) => { setClientId(e.target.value); setJobId(""); setSegmentId(""); }}>
          <option value="">— todos —</option>
          {clients.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </span>
      <span>
        <label>Run</label>
        <select value={jobId} onChange={(e) => { setJobId(e.target.value); navigate(window.location.hash.slice(2) || "overview"); }}>
          <option value="">— elegir —</option>
          {clientJobs.map((j) => (
            <option key={j.id} value={j.id}>
              {j.name} · {new Date(j.created_at).toLocaleDateString("es")} · {j.status}
            </option>
          ))}
        </select>
      </span>
      {segments.length > 0 && (
        <span>
          <label>Segmento</label>
          <select value={segmentId ?? ""} onChange={(e) => setSegmentId(e.target.value)}>
            <option value="">— todo el sitio —</option>
            {segments.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </span>
      )}
      <div className="sources">
        <SourceChip label="crawl" state={job ? (job.status === "completed" ? "ok" : "warn") : "off"} />
        <SourceChip label="GSC" state="off" title="Conéctalo desde la vista legacy de semántica" />
        <SourceChip label="semántica" state="off" title="Disponible en /legacy hasta el re-skin" />
        <SourceChip label="logs" state="off" title="Fuente no conectada (sin ingesta de logs)" />
      </div>
    </div>
  );
}

function SourceChip({ label, state, title }) {
  return (
    <span className={`chip ${state}`} title={title || ""}>
      <span className="dot" />
      {label} {state === "ok" ? "✓" : state === "warn" ? "…" : "✗"}
    </span>
  );
}
