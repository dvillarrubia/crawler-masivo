import { useState } from "react";

import UrlDrawer from "../UrlDrawer.jsx";
import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { detailsToText, issueLabel } from "../issueCatalog.js";
import { Blocked, Drawer, ErrorBox, Severity, Spinner, fmt } from "../ui.jsx";

const SCORE_COLOR = (s) =>
  s == null ? "var(--ink-muted)"
    : s >= 80 ? "var(--chart-forest)" : s >= 50 ? "var(--chart-amber)" : "var(--chart-red)";

/** Insights: score global + categorías + recomendaciones. Cada recomendación
 *  con incidencias se puede abrir para ver las URLs afectadas, su detalle,
 *  la ficha de cada una y exportar los datos para trabajarlos. */
export default function InsightsView() {
  const { jobId } = useCtx();
  const [rec, setRec] = useState(null);   // recomendación abierta (drill-down)
  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const q = useAsync(() => api.insights(jobId), [jobId]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;

  return (
    <div>
      <div className="row" style={{ gap: 18, alignItems: "baseline" }}>
        <h1 className="page-title">Insights</h1>
        <span className="display-num num" style={{ color: SCORE_COLOR(d.overall_score) }}>
          {d.overall_score}
        </span>
        <span className="kpi-label">score global</span>
      </div>
      <p className="page-sub">
        Nota de salud SEO de 0 a 100 por categoría, con recomendaciones priorizadas y el número de URLs
        afectadas por cada una. Verde ≥ 80 · ámbar ≥ 50 · rojo &lt; 50. <b>Haz clic en una recomendación</b>
        {" "}para ver las URLs afectadas, abrir su ficha y exportar los datos.
      </p>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", marginTop: 12 }}>
        {d.categories.map((c) => (
          <div className="card" key={c.key}>
            <div className="row between">
              <h3 style={{ margin: 0 }}>{c.name}</h3>
              <span className="display-num num" style={{ fontSize: 22, color: SCORE_COLOR(c.score) }}
                title={c.score == null ? "Sin datos o no aplica: no puntúa ni afecta al score global" : ""}>
                {c.score == null ? "—" : c.score}
              </span>
            </div>
            {Object.keys(c.metrics || {}).length > 0 && (
              <div className="proxy-tag mono" style={{ margin: "6px 0 10px" }}>
                {Object.entries(c.metrics)
                  .filter(([, v]) => typeof v !== "object")
                  .map(([k, v]) => `${k}: ${typeof v === "number" ? fmt(v) : v}`)
                  .join(" · ")}
              </div>
            )}
            {(c.recommendations || []).map((r, i) => {
              const clickable = (r.issue_types || []).length > 0 && r.affected_count > 0;
              return (
                <div key={i}
                  onClick={clickable ? () => setRec({ ...r, category: c.name }) : undefined}
                  style={{
                    borderTop: "1px solid var(--hairline-soft)", padding: "8px 4px",
                    cursor: clickable ? "pointer" : "default",
                    borderRadius: 3,
                  }}
                  className={clickable ? "rec-clickable" : ""}
                  title={clickable ? "Ver las URLs afectadas" : ""}>
                  <div className="row between">
                    <b style={{ fontSize: 12.5 }}>{r.title}{clickable ? " ›" : ""}</b>
                    <span className="tag" style={{
                      background: r.priority === "high" ? "#fbeae8" : r.priority === "medium" ? "#fdf6e3" : "var(--surface-soft)",
                    }}>{r.priority} · {fmt(r.affected_count)}</span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 3 }}>{r.description}</div>
                </div>
              );
            })}
            {(c.recommendations || []).length === 0 && (
              <div className="empty-clean">Nada que recomendar en esta categoría.</div>
            )}
          </div>
        ))}
      </div>

      {rec && <RecDrawer jobId={jobId} rec={rec} onClose={() => setRec(null)} />}
    </div>
  );
}

/* -- Drill-down: URLs afectadas por una recomendación --------------------- */
function RecDrawer({ jobId, rec, onClose }) {
  const [ficha, setFicha] = useState(null);
  const types = rec.issue_types || [];
  const q = useAsync(
    () => Promise.all(types.map((t) => api.issues(jobId, { issue_type: t, page_size: 300 })))
      .then((rs) => rs.flatMap((r, i) => (r.items || []).map((it) => ({ ...it, _type: types[i] })))),
    [jobId, rec.title],
  );

  const items = q.data || [];

  const exportCsv = () => {
    const esc = (s) => `"${String(s ?? "").replace(/"/g, '""')}"`;
    const rows = [["url", "tipo", "severidad", "detalle"]];
    for (const it of items) {
      rows.push([it.url, it._type, it.severity, detailsToText(it._type, it.details) || ""]);
    }
    const csv = rows.map((r) => r.map(esc).join(",")).join("\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${rec.category}-${(rec.title || "recomendacion").slice(0, 40)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <Drawer onClose={onClose}>
      <div className="proxy-tag">{rec.category} · {rec.priority}</div>
      <h2 style={{ margin: "2px 0 6px" }}>{rec.title}</h2>
      <div className="card muted" style={{ marginBottom: 10 }}>{rec.description}</div>

      <div className="row between" style={{ marginBottom: 8 }}>
        <b>{fmt(rec.affected_count)} URLs afectadas</b>
        {items.length > 0 && (
          <button className="secondary" onClick={exportCsv}>Exportar CSV</button>
        )}
      </div>

      {q.loading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && items.length === 0 && (
        <div className="proxy-tag">Sin URLs listables para esta recomendación (puede ser una métrica agregada del sitio).</div>
      )}
      {items.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: "70vh" }}>
          <table className="data">
            <thead><tr><th>URL</th><th>Sev.</th><th>Detalle</th></tr></thead>
            <tbody>
              {items.map((it) => (
                <tr key={`${it._type}-${it.id}`}>
                  <td className="cell-url" title={it.url}>
                    {it.url_id
                      ? <a className="linklike" style={{ cursor: "pointer" }}
                          onClick={() => setFicha(it.url_id)}>{it.url}</a>
                      : it.url}
                  </td>
                  <td><Severity level={it.severity} /></td>
                  <td style={{ maxWidth: 340, whiteSpace: "normal", fontSize: 12, lineHeight: 1.4 }}>
                    {detailsToText(it._type, it.details)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {ficha && <UrlDrawer jobId={jobId} urlId={ficha} onClose={() => setFicha(null)} />}
    </Drawer>
  );
}
