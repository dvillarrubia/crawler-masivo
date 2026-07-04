import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

const CHANGE_LABELS = {
  new: "Nuevas", gone: "Desaparecidas", status: "Status",
  indexable: "Indexabilidad", canonical: "Canonical", title: "Title",
  depth: "Profundidad", pagerank: "PageRank", content: "Contenido",
};

export default function DiffView() {
  const { clientId, clientJobs, segmentId } = useCtx();
  const completed = clientJobs.filter((j) => j.status === "completed");

  const [jobA, setJobA] = useState("");
  const [jobB, setJobB] = useState("");
  const [change, setChange] = useState(null);
  const [page, setPage] = useState(1);

  const a = jobA || (completed[1] && completed[1].id) || "";
  const b = jobB || (completed[0] && completed[0].id) || "";

  if (!clientId) return <Blocked title="Diff entre crawls" reason="Selecciona un proyecto para comparar sus runs." />;
  if (completed.length < 2)
    return <Blocked title="Diff entre crawls" reason="Hacen falta al menos 2 rastreos completados del proyecto." />;

  return (
    <div>
      <h1 className="page-title">Diff entre crawls</h1>
      <p className="page-sub">Comparación por url_hash; solo runs con la misma semántica de normalización (T8).</p>

      <div className="toolbar">
        <label className="kpi-label">Base</label>
        <select style={{ width: 260 }} value={a} onChange={(e) => { setJobA(e.target.value); setChange(null); }}>
          {completed.map((j) => <option key={j.id} value={j.id}>{j.name} · {new Date(j.created_at).toLocaleDateString("es")}</option>)}
        </select>
        <span>→</span>
        <label className="kpi-label">Comparado</label>
        <select style={{ width: 260 }} value={b} onChange={(e) => { setJobB(e.target.value); setChange(null); }}>
          {completed.map((j) => <option key={j.id} value={j.id}>{j.name} · {new Date(j.created_at).toLocaleDateString("es")}</option>)}
        </select>
      </div>

      {a && b && a !== b && (
        <DiffBody jobA={a} jobB={b} segmentId={segmentId}
          change={change} setChange={(c) => { setChange(c); setPage(1); }}
          page={page} setPage={setPage} />
      )}
      {a === b && <Blocked title="Elige dos runs distintos" reason="La base y el comparado son el mismo run." />}
    </div>
  );
}

function DiffBody({ jobA, jobB, segmentId, change, setChange, page, setPage }) {
  const summaryQ = useAsync(
    () => api.diff({ job_a: jobA, job_b: jobB, segment_id: segmentId }),
    [jobA, jobB, segmentId],
  );
  const detailQ = useAsync(
    () => (change
      ? api.diffUrls({ job_a: jobA, job_b: jobB, change, page, page_size: 50, segment_id: segmentId })
      : Promise.resolve(null)),
    [jobA, jobB, change, page, segmentId],
  );

  if (summaryQ.loading) return <Spinner />;
  if (summaryQ.error) {
    return summaryQ.error.status === 409
      ? <Blocked title="Runs no comparables" reason={summaryQ.error.message} />
      : <ErrorBox error={summaryQ.error} />;
  }
  const d = summaryQ.data;
  const cells = [
    ["new", d.new_urls], ["gone", d.gone_urls],
    ...Object.entries(d.changes),
  ];

  return (
    <>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))" }}>
        {cells.map(([key, count]) => (
          <div key={key} className="card" onClick={() => count > 0 && setChange(key)}
            style={{ cursor: count > 0 ? "pointer" : "default",
                     outline: change === key ? "2px solid var(--ink)" : "none" }}>
            <div className="kpi-label">{CHANGE_LABELS[key] || key}</div>
            <div className="display-num num">{fmt(count)}</div>
          </div>
        ))}
      </div>

      {change && detailQ.loading && <Spinner />}
      {change && detailQ.data && (
        <div style={{ marginTop: 14 }}>
          <div className="table-wrap" style={{ maxHeight: "50vh" }}>
            <table className="data">
              <thead><tr><th>URL</th><th>Antes</th><th>Después</th></tr></thead>
              <tbody>
                {detailQ.data.items.map((e, i) => (
                  <tr key={i}>
                    <td className="cell-url" title={e.url}>{e.url}</td>
                    <td className="mono">{e.old_value == null ? "—" : String(e.old_value)}</td>
                    <td className="mono">{e.new_value == null ? "—" : String(e.new_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pages={detailQ.data.pages} onPage={setPage} />
        </div>
      )}
    </>
  );
}
