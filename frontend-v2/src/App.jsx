import { createContext, useContext, useMemo } from "react";

import { api } from "./api.js";
import { navigate, useAsync, useHashRoute, useStored } from "./hooks.js";
import { Spinner } from "./ui.jsx";
import AccountsView from "./views/Accounts.jsx";
import ConfigView from "./views/Config.jsx";
import DiffView from "./views/DiffView.jsx";
import ExplorerView from "./views/Explorer.jsx";
import FirmaView from "./views/Firma.jsx";
import FreshnessView from "./views/Freshness.jsx";
import HealthView from "./views/Health.jsx";
import InrankView from "./views/Inrank.jsx";
import InsightsView from "./views/Insights.jsx";
import IssuesView from "./views/Issues.jsx";
import JobsView from "./views/Jobs.jsx";
import LinksView from "./views/Links.jsx";
import LogsView from "./views/Logs.jsx";
import OverviewView from "./views/Overview.jsx";
import SemanticView from "./views/Semantic.jsx";

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
    ["links", "Enlaces"],
    ["insights", "Insights"],
    ["diff", "Diff entre crawls"],
    ["freshness", "Frescura"],
  ]},
  { group: "Análisis", items: [
    ["inrank", "Enlazado · Inrank"],
    ["semantica", "Semántica"],
    ["firma", "Cola de firma"],
    ["logs", "Logs", "blocked"],
  ]},
  { group: "Ajustes", items: [
    ["config", "Configuración"],
    ["cuentas", "Cuentas"],
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

  // Estado real de fuentes del run activo (chips honestos)
  const semStatusQ = useAsync(
    () => (jobId ? api.semanticStatus(jobId).catch(() => null) : Promise.resolve(null)),
    [jobId],
  );
  const semStatus = semStatusQ.data;

  const ctx = {
    clientId, setClientId,
    jobId, setJobId,
    segmentId: segmentId ? Number(segmentId) : null, setSegmentId,
    job, jobs, clientJobs, clients, segments, semStatus,
    reloadJobs: jobsQ.reload,
    reloadSegments: segsQ.reload,
  };

  const views = {
    salud: HealthView,
    jobs: JobsView,
    overview: OverviewView,
    explorer: ExplorerView,
    issues: IssuesView,
    links: LinksView,
    insights: InsightsView,
    diff: DiffView,
    freshness: FreshnessView,
    inrank: InrankView,
    semantica: SemanticView,
    firma: FirmaView,
    logs: LogsView,
    config: ConfigView,
    cuentas: AccountsView,
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
              {items.map(([key, label, kind]) => (
                <a
                  key={key}
                  href={`#/${key}`}
                  className={view === key ? "active" : ""}
                >
                  {label}
                  {kind === "blocked" && <span className="lock">fuente ✗</span>}
                </a>
              ))}
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
          clients, clientJobs, job, segments, semStatus } = useCtx();

  const semState = semStatus && semStatus.status === "completed" ? "ok"
    : semStatus && semStatus.status === "running" ? "warn" : "off";
  const gscState = semStatus && semStatus.has_gsc_data ? "ok" : "off";

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
        <SourceChip label="GSC" state={gscState}
          title={gscState === "off"
            ? "Este run no tiene datos de Search Console: impórtalos en Semántica → Análisis"
            : `Datos GSC importados: ${(semStatus && semStatus.gsc && semStatus.gsc.total) || "?"} URLs con métricas`} />
        <SourceChip label="semántica" state={semState} title={semState === "off" ? "Lanza el análisis desde Semántica" : ""} />
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
