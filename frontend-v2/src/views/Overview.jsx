import { useEffect, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { BarRow, Blocked, ErrorBox, Kpi, Spinner, fmt } from "../ui.jsx";

const GROUP_COLORS = {
  "2xx": "var(--chart-forest)", "3xx": "var(--chart-amber)",
  "4xx": "var(--chart-red)", "5xx": "var(--chart-maroon)",
  not_crawled: "var(--chart-blue)",
};

/** Overview del run: KPIs con delta vs run anterior + distribución. */
export default function OverviewView() {
  const { job, jobId, segmentId, clientJobs } = useCtx();

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior o lanza uno nuevo desde Rastreos." />;

  return job && ["running", "pending"].includes(job.status)
    ? <LiveProgress job={job} />
    : <CompletedOverview jobId={jobId} segmentId={segmentId} clientJobs={clientJobs} />;
}

/** Progreso en vivo sobre job:{id}:progress de Redis. */
function LiveProgress({ job }) {
  const [progress, setProgress] = useState(null);
  const { reloadJobs } = useCtx();

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const p = await api.progress(job.id);
        if (!alive) return;
        setProgress(p);
        if (p.status !== "running" && p.status !== "pending") reloadJobs();
      } catch { /* transitorio */ }
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [job.id]);

  const crawled = progress ? progress.crawled_count : job.total_urls_crawled;
  const maxUrls = (job.config && job.config.max_urls) || 50000;
  const pct = Math.min(100, Math.round((crawled / maxUrls) * 100));

  return (
    <div>
      <h1 className="page-title">Rastreo en curso</h1>
      <p className="page-sub">{job.name} · estado {progress ? progress.status : job.status}</p>
      <div className="card" style={{ maxWidth: 560 }}>
        <div className="display-num num">{fmt(crawled)}</div>
        <div className="kpi-label">URLs rastreadas (límite {fmt(maxUrls)})</div>
        <div className="progressbar" style={{ marginTop: 12 }}><i style={{ width: `${pct}%` }} /></div>
        <div className="row between" style={{ marginTop: 8 }}>
          <span className="proxy-tag">refresco cada 3 s desde Redis</span>
          <button className="secondary" onClick={() => api.cancelJob(job.id)}>Cancelar rastreo</button>
        </div>
      </div>
    </div>
  );
}

function CompletedOverview({ jobId, segmentId, clientJobs }) {
  const statsQ = useAsync(
    () => api.stats(jobId, { segment_id: segmentId }),
    [jobId, segmentId],
  );

  // Delta vs run anterior del mismo cliente (si existe y es comparable)
  const prev = (() => {
    const idx = clientJobs.findIndex((j) => j.id === jobId);
    const me = clientJobs[idx];
    if (!me) return null;
    return clientJobs.slice(idx + 1).find((j) => j.status === "completed") || null;
  })();
  const prevQ = useAsync(
    () => (prev ? api.stats(prev.id, { segment_id: segmentId }) : Promise.resolve(null)),
    [prev ? prev.id : null, segmentId],
  );

  if (statsQ.loading) return <Spinner />;
  if (statsQ.error) return <ErrorBox error={statsQ.error} />;
  const s = statsQ.data;
  const p = prevQ.data;

  const groups = Object.fromEntries(s.urls_by_status_group.map((g) => [g.status_group, g.count]));
  const maxGroup = Math.max(1, ...Object.values(groups));
  const errors = s.issues_by_type.filter((i) => i.severity === "error");
  const topIssues = [...s.issues_by_type].sort((a, b) => b.count - a.count).slice(0, 8);

  const delta = (field) => (p ? s[field] - p[field] : null);

  return (
    <div>
      <h1 className="page-title">Overview del run</h1>
      <p className="page-sub">
        Resumen ejecutivo del rastreo: cuántas URLs se vieron, cómo respondieron, qué incidencias pesan más
        y la velocidad del sitio. {segmentId ? "Filtrado por el segmento seleccionado arriba." : "Sitio completo."}
        {prev && ` Los deltas (▲▼) comparan con el run anterior «${prev.name}».`}
      </p>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
        <Kpi label="URLs rastreadas" value={fmt(s.total_urls)} delta={delta("total_urls")} />
        <Kpi label="Internas" value={fmt(s.internal_count)} delta={delta("internal_count")} />
        <Kpi label="2xx" value={fmt(groups["2xx"] || 0)} />
        <Kpi label="4xx + 5xx" value={fmt((groups["4xx"] || 0) + (groups["5xx"] || 0))} />
        <Kpi
          label="p90 latencia"
          value={s.latency ? `${fmt(s.latency.p90)} ms` : "—"}
          proxy={s.latency ? "sobre response_time del crawl" : "sin timings"}
        />
        <Kpi label="Errores SEO" value={fmt(errors.reduce((a, i) => a + i.count, 0))} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 12 }}>
        <div className="card">
          <h3>Distribución por status</h3>
          <p className="proxy-tag" style={{ marginTop: 0 }}>Cómo respondió el servidor: 2xx = bien · 3xx = redirecciones · 4xx/5xx = errores · not_crawled = conocidas pero no visitadas.</p>
          {Object.entries(groups).map(([g, count]) => (
            <BarRow key={g} label={g} value={count} max={maxGroup}
              color={GROUP_COLORS[g] || "var(--chart-blue)"} />
          ))}
        </div>
        <div className="card">
          <h3>Incidencias prioritarias</h3>
          <p className="proxy-tag" style={{ marginTop: 0 }}>Los 8 tipos con más URLs afectadas. El detalle completo, con explicación de cada tipo, está en Incidencias.</p>
          {topIssues.length === 0 && <div className="empty-clean">Sin incidencias — limpio de verdad, no sin datos.</div>}
          {topIssues.map((i) => (
            <BarRow key={`${i.issue_type}-${i.severity}`} label={i.issue_type}
              value={i.count} max={topIssues[0].count}
              color={i.severity === "error" ? "var(--chart-red)" : i.severity === "warning" ? "var(--chart-amber)" : "var(--chart-blue)"} />
          ))}
        </div>
      </div>

      {s.geo && (
        <div className="card" style={{ marginTop: 12 }}>
          <h3>GEO — contenido crudo vs. renderizado</h3>
          <p className="proxy-tag" style={{ marginTop: 0 }}>Compara cada página descargada sin ejecutar JavaScript contra la versión renderizada.</p>
          <div className="facts">
            <div className="fact"><div className="k">Páginas evaluadas</div><div className="v num">{fmt(s.geo.pages_evaluated)}</div></div>
            <div className="fact"><div className="k">% medio solo tras JS</div><div className="v num">{s.geo.avg_js_content_ratio != null ? `${(s.geo.avg_js_content_ratio * 100).toFixed(1)}%` : "—"}</div></div>
            <div className="fact"><div className="k">Contenido solo tras JS</div><div className="v num">{fmt(s.geo.content_only_after_js)}</div></div>
            <div className="fact"><div className="k">Schema solo tras JS</div><div className="v num">{fmt(s.geo.schema_only_after_js)}</div></div>
          </div>
          <p className="proxy-tag">Lo que solo existe tras ejecutar JS es invisible para los crawlers de IA y el primer pase de Google.</p>
        </div>
      )}

      {s.latency && (
        <div className="card" style={{ marginTop: 12 }}>
          <h3>Latencia por status group (p50 / p90 / p99, ms)</h3>
          <p className="proxy-tag" style={{ marginTop: 0 }}>Tiempo de respuesta del servidor: la mitad de las páginas responde por debajo del p50; el p90/p99 enseña la cola lenta.</p>
          <table className="data" style={{ maxWidth: 480 }}>
            <thead><tr><th>Grupo</th><th className="num">p50</th><th className="num">p90</th><th className="num">p99</th></tr></thead>
            <tbody>
              <tr><td>global</td><td className="num">{fmt(s.latency.p50)}</td><td className="num">{fmt(s.latency.p90)}</td><td className="num">{fmt(s.latency.p99)}</td></tr>
              {Object.entries(s.latency.by_status_group).map(([g, v]) => (
                <tr key={g}><td>{g}</td><td className="num">{fmt(v.p50)}</td><td className="num">{fmt(v.p90)}</td><td className="num">{fmt(v.p99)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="row" style={{ marginTop: 14, gap: 8 }}>
        <a className="btn secondary" href={api.exportUrl(jobId, "urls")}><button className="secondary">Exportar URLs CSV</button></a>
        <a href={api.exportUrl(jobId, "issues")}><button className="secondary">Issues CSV</button></a>
        <a href={api.exportUrl(jobId, "links")}><button className="secondary">Links CSV</button></a>
      </div>
    </div>
  );
}
