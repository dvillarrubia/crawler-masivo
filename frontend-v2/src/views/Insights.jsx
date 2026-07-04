import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Spinner, fmt } from "../ui.jsx";

const SCORE_COLOR = (s) =>
  s >= 80 ? "var(--chart-forest)" : s >= 50 ? "var(--chart-amber)" : "var(--chart-red)";

/** Insights (paridad legacy): score global + categorías + recomendaciones. */
export default function InsightsView() {
  const { jobId } = useCtx();
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

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", marginTop: 12 }}>
        {d.categories.map((c) => (
          <div className="card" key={c.key}>
            <div className="row between">
              <h3 style={{ margin: 0 }}>{c.name}</h3>
              <span className="display-num num" style={{ fontSize: 22, color: SCORE_COLOR(c.score) }}>{c.score}</span>
            </div>
            <div className="proxy-tag mono" style={{ margin: "6px 0 10px" }}>
              {Object.entries(c.metrics || {}).slice(0, 4)
                .map(([k, v]) => `${k}: ${typeof v === "number" ? fmt(v) : v}`)
                .join(" · ")}
            </div>
            {(c.recommendations || []).map((r, i) => (
              <div key={i} style={{ borderTop: "1px solid var(--hairline-soft)", padding: "8px 0" }}>
                <div className="row between">
                  <b style={{ fontSize: 12.5 }}>{r.title}</b>
                  <span className={`tag`} style={{
                    background: r.priority === "high" ? "#fbeae8" : r.priority === "medium" ? "#fdf6e3" : "var(--surface-soft)",
                  }}>{r.priority} · {fmt(r.affected_count)}</span>
                </div>
                <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 3 }}>{r.description}</div>
              </div>
            ))}
            {(c.recommendations || []).length === 0 && (
              <div className="empty-clean">Nada que recomendar en esta categoría.</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
