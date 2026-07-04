import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

/** Frescura (T5): qué cambió de contenido entre este run y otro anterior. */
export default function FreshnessView() {
  const { jobId, clientJobs } = useCtx();
  const [compareTo, setCompareTo] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(true);
  const [page, setPage] = useState(1);

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const others = clientJobs.filter((j) => j.id !== jobId && j.status === "completed");
  const target = compareTo || (others[0] && others[0].id) || "";

  return (
    <div>
      <h1 className="page-title">Frescura</h1>
      <p className="page-sub">Cambio de contenido (body_hash) entre dos runs del mismo proyecto, cruzado por url_hash.</p>

      {others.length === 0 ? (
        <Blocked title="Sin run de comparación"
          reason="Hace falta otro rastreo completado del mismo proyecto." />
      ) : (
        <>
          <div className="toolbar">
            <label className="kpi-label">Comparar con</label>
            <select style={{ width: 280 }} value={target}
              onChange={(e) => { setCompareTo(e.target.value); setPage(1); }}>
              {others.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.name} · {new Date(j.created_at).toLocaleDateString("es")}
                </option>
              ))}
            </select>
            <label className="checkbox-row" style={{ margin: 0 }}>
              <input type="checkbox" checked={onlyChanged}
                onChange={(e) => { setOnlyChanged(e.target.checked); setPage(1); }} />
              solo cambiadas
            </label>
          </div>
          {target && (
            <FreshnessTable jobId={jobId} compareTo={target}
              onlyChanged={onlyChanged} page={page} setPage={setPage} />
          )}
        </>
      )}
    </div>
  );
}

function FreshnessTable({ jobId, compareTo, onlyChanged, page, setPage }) {
  const q = useAsync(
    () => api.freshness(jobId, {
      compare_to: compareTo, only_changed: onlyChanged, page, page_size: 100,
    }),
    [jobId, compareTo, onlyChanged, page],
  );

  if (q.loading) return <Spinner />;
  if (q.error) {
    return q.error.status === 409
      ? <Blocked title="Runs no comparables" reason={q.error.message} />
      : <ErrorBox error={q.error} />;
  }
  const d = q.data;

  return (
    <>
      <p className="proxy-tag num">{fmt(d.total)} URLs {onlyChanged ? "con contenido cambiado" : "comparadas"}</p>
      <div className="table-wrap" style={{ maxHeight: "62vh" }}>
        <table className="data">
          <thead>
            <tr>
              <th>URL</th><th>Contenido</th><th>Last-Modified</th>
              <th>lastmod sitemap</th><th>Vista por primera vez</th>
            </tr>
          </thead>
          <tbody>
            {d.items.map((u, i) => (
              <tr key={i}>
                <td className="cell-url" title={u.url}>{u.url}</td>
                <td>
                  {u.is_new ? <span className="tag">nueva</span>
                    : u.body_changed ? <span className="pill s3xx">cambiado</span>
                    : <span className="pill s2xx">igual</span>}
                </td>
                <td className="mono">{u.last_modified || "—"}</td>
                <td className="mono">{u.sitemap_lastmod ? new Date(u.sitemap_lastmod).toLocaleDateString("es") : "—"}</td>
                <td>{u.first_seen_at ? new Date(u.first_seen_at).toLocaleDateString("es") : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={d.pages} onPage={setPage} />
    </>
  );
}
