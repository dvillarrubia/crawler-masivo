import { useMemo, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Spinner, fmt } from "../ui.jsx";

/** Rendimiento en el tiempo. Dos ejes complementarios:
 *  - Por rastreos (B3): evolución rastreo a rastreo — datos estructurales
 *    (URLs, incidencias, PageRank) que solo cambian cuando rastreas.
 *  - Por fechas (agnóstico a rastreos): la serie diaria real de GSC/GA4,
 *    consultable por rangos e independiente de cuándo se rastreó. */
export default function PerformanceView() {
  const { clientId } = useCtx();
  const [mode, setMode] = useState("dates");
  if (!clientId) {
    return <Blocked title="Rendimiento del proyecto"
      reason="El rendimiento se mira por proyecto. Selecciona uno en la barra superior." />;
  }
  return (
    <div>
      <h1 className="page-title">Rendimiento</h1>
      <div className="toolbar" style={{ marginBottom: 4 }}>
        <label className="kpi-label">Ver:</label>
        <button className={mode === "dates" ? "" : "secondary"}
          onClick={() => setMode("dates")}>Por fechas (GSC/GA4)</button>
        <button className={mode === "crawls" ? "" : "secondary"}
          onClick={() => setMode("crawls")}>Por rastreos</button>
      </div>
      {mode === "dates"
        ? <DateRangeReport clientId={clientId} />
        : <CrawlPerformance clientId={clientId} />}
    </div>
  );
}

/* -- Evolución por rastreos (B3) -------------------------------------------- */
function CrawlPerformance({ clientId }) {
  const { segments } = useCtx();
  const [scope, setScope] = useState({ kind: "site" });
  const params = scope.kind === "segment" ? { segment_id: scope.segment_id }
    : scope.kind === "watchlist" ? { watchlist: true } : {};
  const q = useAsync(() => api.performanceTimeline(clientId, params), [clientId, scope]);
  const sum = useAsync(() => api.performanceSummary(clientId, params), [clientId, scope]);

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;

  if (d.status === "blocked") {
    return <Blocked title="Rendimiento del proyecto"
      reason="Aún no hay rastreos completados de este proyecto."
      cta={<a href="#/jobs"><button>Ir a Rastreos</button></a>} />;
  }

  return (
    <div>
      <p className="page-sub">
        Cómo evoluciona el proyecto rastreo a rastreo: clics e impresiones de Search Console,
        posición media, incidencias y autoridad. Sigue el sitio entero o solo lo importante —
        una sección (servicios, cursos, categorías) o tus URLs vigiladas.
      </p>

      {/* Qué seguir: sitio entero / una sección / las URLs vigiladas */}
      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        <label className="kpi-label">Seguir:</label>
        <button className={scope.kind === "site" ? "" : "secondary"}
          onClick={() => setScope({ kind: "site" })}>Sitio entero</button>
        <button className={scope.kind === "watchlist" ? "" : "secondary"}
          onClick={() => setScope({ kind: "watchlist" })}>URLs vigiladas</button>
        {segments && segments.length > 0 && (
          <select value={scope.kind === "segment" ? scope.segment_id : ""}
            onChange={(e) => e.target.value
              ? setScope({ kind: "segment", segment_id: Number(e.target.value) })
              : setScope({ kind: "site" })}>
            <option value="">— una sección —</option>
            {segments.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
        {d.scope && d.scope.kind !== "site" && (
          <span className="tag">{d.scope.kind === "watchlist" ? "solo URLs vigiladas" : "solo la sección elegida"}</span>
        )}
      </div>

      {sum.data && sum.data.status === "ok" && <SummaryCards summary={sum.data} />}

      <EvolutionChart points={d.points} metrics={d.metrics} />

      {scope.kind === "watchlist" && <WatchlistDetail clientId={clientId} />}

      <RunsTable points={d.points} metrics={d.metrics} />
    </div>
  );
}

/* -- Evolución de cada URL vigilada, una a una ------------------------------ */
function WatchlistDetail({ clientId }) {
  const q = useAsync(() => api.watchlistTimeline(clientId), [clientId]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (d.status === "blocked") {
    return (
      <div className="card" style={{ marginBottom: 16 }}>
        <h3>URLs vigiladas, una a una</h3>
        <p className="proxy-tag">
          {d.reason === "watchlist_vacia"
            ? <>No hay URLs vigiladas. Añade las importantes (servicios, cursos, categorías) en <a href="#/config">Configuración → Estructura del sitio</a>.</>
            : "Aún no hay rastreos completados."}
        </p>
      </div>
    );
  }
  const fmtDelta = (serie, field) => {
    const vals = serie.filter((s) => s[field] != null);
    if (vals.length < 2) return null;
    return vals[vals.length - 1][field] - vals[0][field];
  };
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h3>URLs vigiladas, una a una ({d.urls.length})</h3>
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        La evolución de cada página importante por separado: primer rastreo con datos → último.
      </p>
      <div className="table-wrap" style={{ maxHeight: "50vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL vigilada</th><th className="num">Clics (Δ)</th>
              <th className="num">Impresiones (Δ)</th><th className="num">Posición (Δ)</th></tr>
          </thead>
          <tbody>
            {d.urls.map((u, i) => {
              const last = [...u.serie].reverse().find((s) => s.clicks != null);
              const dc = fmtDelta(u.serie, "clicks");
              const di = fmtDelta(u.serie, "impressions");
              const dp = fmtDelta(u.serie, "position");
              return (
                <tr key={i}>
                  <td className="cell-url" title={u.url}>{u.label ? <b>{u.label} · </b> : null}{u.url}</td>
                  {!u.tiene_datos
                    ? <td colSpan={3} className="proxy-tag">sin datos GSC en el histórico</td>
                    : <>
                        <td className="num">{fmt(last.clicks)}{dc != null && dc !== 0 ? <span style={{ color: dc > 0 ? "var(--chart-forest)" : "var(--chart-red)", fontSize: 11 }}> {dc > 0 ? "+" : ""}{fmt(dc)}</span> : null}</td>
                        <td className="num">{fmt(last.impressions)}{di != null && di !== 0 ? <span style={{ color: di > 0 ? "var(--chart-forest)" : "var(--chart-red)", fontSize: 11 }}> {di > 0 ? "+" : ""}{fmt(di)}</span> : null}</td>
                        <td className="num">{last.position != null ? last.position.toFixed(1) : "—"}{dp != null && dp !== 0 ? <span style={{ color: dp < 0 ? "var(--chart-forest)" : "var(--chart-red)", fontSize: 11 }}> {dp > 0 ? "+" : ""}{dp.toFixed(1)}</span> : null}</td>
                      </>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* -- Comparación destacada actual vs referencia ----------------------------- */
function SummaryCards({ summary }) {
  const order = ["gsc_clicks", "gsc_impressions", "gsc_position", "issues_total"];
  return (
    <div style={{ marginBottom: 16 }}>
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        «{summary.referencia.name}» ({summary.referencia.date?.slice(0, 10)}) →
        «{summary.actual.name}» ({summary.actual.date?.slice(0, 10)})
        {summary.hay_corte_normalizacion &&
          " · ⚠ hubo un cambio de reglas de URL en el medio: la comparación puede no ser exacta"}
      </p>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        {order.map((k) => {
          const m = summary.metricas[k];
          if (!m) return null;
          const delta = m.delta;
          const good = delta == null ? null
            : (m.lower_better ? delta < 0 : delta > 0);
          const color = good == null || delta === 0 ? "var(--ink-muted)"
            : good ? "var(--chart-forest)" : "var(--chart-red)";
          return (
            <div className="card" key={k}>
              <div className="kpi-label">{m.label}</div>
              <div className="display-num num">{m.actual == null ? "—" : fmt(m.actual)}</div>
              {delta != null && (
                <div className="num" style={{ color, fontSize: 13 }}>
                  {delta > 0 ? "▲ +" : delta < 0 ? "▼ " : ""}{fmt(delta)} vs referencia
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* -- Gráfico de líneas SVG de la métrica elegida ---------------------------- */
function EvolutionChart({ points, metrics }) {
  const [metric, setMetric] = useState("gsc_clicks");
  const meta = metrics.find((m) => m.key === metric) || metrics[0];

  const series = useMemo(() =>
    points.map((p, i) => ({ i, x: p.date, v: p.metrics[metric], name: p.name, comparable: p.comparable })),
    [points, metric]);
  const vals = series.map((s) => s.v).filter((v) => v != null);

  if (vals.length < 2) {
    return (
      <div className="card" style={{ marginBottom: 16 }}>
        <MetricPicker metric={metric} setMetric={setMetric} metrics={metrics} />
        <p className="proxy-tag">Hacen falta al menos 2 rastreos con esta métrica para dibujar la evolución.</p>
      </div>
    );
  }

  const W = 900, H = 300, PAD = 40;
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const n = series.length;
  const sx = (i) => PAD + (i / (n - 1 || 1)) * (W - 2 * PAD);
  const sy = (v) => H - PAD - ((v - min) / range) * (H - 2 * PAD);
  const pts = series.filter((s) => s.v != null);
  const path = pts.map((s, k) => `${k === 0 ? "M" : "L"} ${sx(s.i)} ${sy(s.v)}`).join(" ");

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <MetricPicker metric={metric} setMetric={setMetric} metrics={metrics} />
      <div style={{ overflowX: "auto" }}>
        <svg width={W} height={H} style={{ border: "1px solid var(--hairline)", background: "var(--canvas-muted)" }}>
          <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--hairline)" />
          <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--hairline)" />
          <text x={PAD - 6} y={sy(max)} textAnchor="end" fontSize="10" fill="var(--ink-muted)">{fmt(max)}</text>
          <text x={PAD - 6} y={sy(min)} textAnchor="end" fontSize="10" fill="var(--ink-muted)">{fmt(min)}</text>
          <path d={path} fill="none" stroke="var(--chart-navy)" strokeWidth="2" />
          {pts.map((s, k) => (
            <g key={k}>
              <circle cx={sx(s.i)} cy={sy(s.v)} r={4}
                fill={s.comparable ? "var(--chart-navy)" : "var(--chart-amber)"}>
                <title>{s.name} · {meta.label}: {fmt(s.v)}{s.comparable ? "" : " (no comparable: cambió la normalización)"}</title>
              </circle>
              {k === pts.length - 1 && (
                <text x={sx(s.i)} y={sy(s.v) - 8} textAnchor="middle" fontSize="10" fill="var(--ink)">{fmt(s.v)}</text>
              )}
            </g>
          ))}
        </svg>
      </div>
      <p className="proxy-tag" style={{ marginTop: 6 }}>
        Cada punto es un rastreo, en orden cronológico.
        {meta.lower_better ? " En esta métrica, bajar es mejor." : ""}
        {series.some((s) => !s.comparable && s.i > 0) &&
          " Los puntos ámbar marcan un cambio de reglas de URL (no comparables con el anterior)."}
      </p>
    </div>
  );
}

/* ==========================================================================
 * INFORME POR FECHAS — agnóstico a los rastreos (serie diaria GSC/GA4)
 * ======================================================================== */
const PRESETS = [
  { key: "7d", label: "Últimos 7 días", days: 7 },
  { key: "28d", label: "Últimos 28 días", days: 28 },
  { key: "90d", label: "Últimos 90 días", days: 90 },
  { key: "custom", label: "Personalizado", days: null },
];

function isoDaysAgo(days) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

function DateRangeReport({ clientId }) {
  const [source, setSource] = useState("gsc");
  const [preset, setPreset] = useState("28d");
  const [gran, setGran] = useState("day");
  const [watchlist, setWatchlist] = useState(false);
  const [from, setFrom] = useState(isoDaysAgo(28));
  const [to, setTo] = useState(isoDaysAgo(1));

  // Al elegir un preset (no personalizado) se recalculan las fechas.
  const applyPreset = (p) => {
    setPreset(p.key);
    if (p.days != null) { setFrom(isoDaysAgo(p.days)); setTo(isoDaysAgo(1)); }
  };

  const cov = useAsync(() => api.metricsCoverage(clientId), [clientId]);
  const params = {
    date_from: from, date_to: to, granularity: gran,
    compare: "previous", source,
    ...(source === "gsc" && watchlist ? { watchlist: true } : {}),
  };
  const q = useAsync(() => api.metricsReport(clientId, params),
    [clientId, from, to, gran, source, watchlist]);

  return (
    <div>
      <p className="page-sub">
        La serie diaria real de Search Console y Analytics, por rango de fechas —
        independiente de cuándo se rastreó. Elige un periodo y se compara solo con el
        inmediatamente anterior de la misma duración.
      </p>

      <CoverageBar cov={cov.data} source={source} />

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <button className={source === "gsc" ? "" : "secondary"}
          onClick={() => setSource("gsc")}>Search Console</button>
        <button className={source === "ga4" ? "" : "secondary"}
          onClick={() => setSource("ga4")}>Analytics (GA4)</button>
        <span style={{ flex: "0 0 12px" }} />
        {PRESETS.map((p) => (
          <button key={p.key} className={preset === p.key ? "" : "secondary"}
            onClick={() => applyPreset(p)}>{p.label}</button>
        ))}
      </div>

      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <label className="kpi-label">Del</label>
        <input type="date" value={from}
          onChange={(e) => { setFrom(e.target.value); setPreset("custom"); }} />
        <label className="kpi-label">al</label>
        <input type="date" value={to}
          onChange={(e) => { setTo(e.target.value); setPreset("custom"); }} />
        <label className="kpi-label">Agrupar:</label>
        <select value={gran} onChange={(e) => setGran(e.target.value)}>
          <option value="day">Por día</option>
          <option value="week">Por semana</option>
          <option value="month">Por mes</option>
        </select>
        {source === "gsc" && (
          <label className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={watchlist}
              onChange={(e) => setWatchlist(e.target.checked)} />
            Solo URLs vigiladas
          </label>
        )}
      </div>

      {q.loading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && q.data.status === "blocked" && (
        <Blocked title="Sin datos para este periodo"
          reason={q.data.reason === "sin_datos_ga4"
            ? "No hay datos de GA4 sincronizados. Conecta una cuenta GA4 y sincroniza el rango en Configuración → Cuentas y fuentes."
            : q.data.reason === "sin_datos_gsc_diarios"
            ? "No hay serie diaria de Search Console para estas fechas. Sincroniza el rango (Configuración → Cuentas y fuentes) para poblar el histórico."
            : "No hay datos para el rango elegido."} />
      )}
      {q.data && q.data.status === "ok" && <RangeReportBody data={q.data} />}
    </div>
  );
}

function CoverageBar({ cov, source }) {
  if (!cov) return null;
  const c = source === "ga4" ? cov.ga4 : cov.gsc;
  if (!c || !c.filas) {
    return (
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        Sin histórico {source === "ga4" ? "de GA4" : "diario de Search Console"} todavía —
        sincroniza un rango para empezar a acumular la serie.
      </p>
    );
  }
  return (
    <p className="proxy-tag" style={{ marginTop: 0 }}>
      Histórico disponible: <b>{c.desde}</b> → <b>{c.hasta}</b> ({fmt(c.filas)} días de datos)
    </p>
  );
}

function RangeReportBody({ data }) {
  const [metric, setMetric] = useState(data.metrics[0].key);
  const series = data.series;
  const cmp = data.comparacion;

  return (
    <div>
      {/* Tarjetas de comparación vs periodo anterior */}
      <div style={{ margin: "12px 0" }}>
        {data.rango_anterior && (
          <p className="proxy-tag" style={{ marginTop: 0 }}>
            {data.rango.desde} → {data.rango.hasta} comparado con el periodo anterior
            ({data.rango_anterior.desde} → {data.rango_anterior.hasta})
            {data.scope.kind === "watchlist" ? " · solo URLs vigiladas" : ""}
          </p>
        )}
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
          {data.metrics.map((m) => {
            const c = cmp[m.key];
            if (!c) return null;
            const delta = c.delta;
            const good = delta == null || delta === 0 ? null
              : (c.lower_better ? delta < 0 : delta > 0);
            const color = good == null ? "var(--ink-muted)"
              : good ? "var(--chart-forest)" : "var(--chart-red)";
            const pct = (c.anterior && typeof c.anterior === "number" && c.anterior !== 0 && delta != null)
              ? Math.round((delta / c.anterior) * 1000) / 10 : null;
            return (
              <div className="card" key={m.key}>
                <div className="kpi-label">{m.label}</div>
                <div className="display-num num">{c.actual == null ? "—" : fmt(c.actual)}</div>
                {delta != null && (
                  <div className="num" style={{ color, fontSize: 13 }}>
                    {delta > 0 ? "▲ +" : delta < 0 ? "▼ " : ""}{fmt(delta)}
                    {pct != null ? ` (${pct > 0 ? "+" : ""}${pct}%)` : ""}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Gráfico de la métrica elegida a lo largo del rango */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="toolbar" style={{ marginBottom: 8, flexWrap: "wrap" }}>
          {data.metrics.map((m) => (
            <button key={m.key} className={metric === m.key ? "" : "secondary"}
              onClick={() => setMetric(m.key)}>{m.label}</button>
          ))}
        </div>
        <RangeChart series={series} metric={metric}
          meta={data.metrics.find((m) => m.key === metric)} />
      </div>

      {/* Tabla de la serie por bucket */}
      <div className="card">
        <h3>Serie ({data.granularity === "month" ? "mensual" : data.granularity === "week" ? "semanal" : "diaria"})</h3>
        <div className="table-wrap" style={{ maxHeight: "50vh" }}>
          <table className="data">
            <thead>
              <tr><th>Periodo</th>{data.metrics.map((m) => <th key={m.key} className="num">{m.label}</th>)}</tr>
            </thead>
            <tbody>
              {series.map((s, i) => (
                <tr key={i}>
                  <td className="mono">{s.bucket}</td>
                  {data.metrics.map((m) => {
                    const v = s[m.key];
                    const fmtd = v == null ? "—"
                      : m.key === "position" ? v.toFixed(1)
                      : m.key === "ctr" ? v.toFixed(2)
                      : fmt(v);
                    return <td key={m.key} className="num">{fmtd}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function RangeChart({ series, metric, meta }) {
  const pts = series.map((s, i) => ({ i, v: s[metric], label: s.bucket })).filter((s) => s.v != null);
  if (pts.length < 2) {
    return <p className="proxy-tag">Hacen falta al menos 2 periodos con datos para dibujar la evolución.</p>;
  }
  const W = 900, H = 280, PAD = 44;
  const vals = pts.map((s) => s.v);
  const min = Math.min(...vals, 0), max = Math.max(...vals);
  const range = max - min || 1;
  const n = pts.length;
  const sx = (i) => PAD + (i / (n - 1 || 1)) * (W - 2 * PAD);
  const sy = (v) => H - PAD - ((v - min) / range) * (H - 2 * PAD);
  const path = pts.map((s, k) => `${k === 0 ? "M" : "L"} ${sx(k)} ${sy(s.v)}`).join(" ");
  const area = `${path} L ${sx(n - 1)} ${H - PAD} L ${sx(0)} ${H - PAD} Z`;

  return (
    <div style={{ overflowX: "auto" }}>
      <svg width={W} height={H} style={{ border: "1px solid var(--hairline)", background: "var(--canvas-muted)" }}>
        <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} stroke="var(--hairline)" />
        <line x1={PAD} y1={PAD} x2={PAD} y2={H - PAD} stroke="var(--hairline)" />
        <text x={PAD - 6} y={sy(max)} textAnchor="end" fontSize="10" fill="var(--ink-muted)">{fmt(max)}</text>
        <text x={PAD - 6} y={sy(min)} textAnchor="end" fontSize="10" fill="var(--ink-muted)">{fmt(min)}</text>
        <path d={area} fill="var(--chart-navy)" opacity="0.08" />
        <path d={path} fill="none" stroke="var(--chart-navy)" strokeWidth="2" />
        {pts.map((s, k) => (
          <circle key={k} cx={sx(k)} cy={sy(s.v)} r={3} fill="var(--chart-navy)">
            <title>{s.label} · {meta?.label}: {fmt(s.v)}</title>
          </circle>
        ))}
        {/* etiquetas de primer y último periodo */}
        <text x={sx(0)} y={H - PAD + 14} textAnchor="start" fontSize="10" fill="var(--ink-muted)">{pts[0].label}</text>
        <text x={sx(n - 1)} y={H - PAD + 14} textAnchor="end" fontSize="10" fill="var(--ink-muted)">{pts[n - 1].label}</text>
      </svg>
      <p className="proxy-tag" style={{ marginTop: 6 }}>
        {meta?.lower_better ? "En esta métrica, bajar es mejor." : "Serie por fecha, no por rastreo."}
      </p>
    </div>
  );
}

function MetricPicker({ metric, setMetric, metrics }) {
  return (
    <div className="toolbar" style={{ marginBottom: 8, flexWrap: "wrap" }}>
      {metrics.map((m) => (
        <button key={m.key} className={metric === m.key ? "" : "secondary"}
          onClick={() => setMetric(m.key)}>{m.label}</button>
      ))}
    </div>
  );
}

/* -- Tabla de todos los runs con todas las métricas ------------------------- */
function RunsTable({ points, metrics }) {
  return (
    <div className="card">
      <h3>Todos los rastreos</h3>
      <div className="table-wrap" style={{ maxHeight: "50vh" }}>
        <table className="data">
          <thead>
            <tr>
              <th>Rastreo</th><th>Fecha</th>
              {metrics.map((m) => <th key={m.key} className="num">{m.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {points.slice().reverse().map((p, i) => (
              <tr key={i}>
                <td>{p.name}{!p.comparable && p !== points[0] ? <span className="tag" style={{ marginLeft: 4 }} title="Cambió la normalización respecto al anterior">≠norm</span> : null}</td>
                <td className="mono">{p.date ? p.date.slice(0, 10) : "—"}</td>
                {metrics.map((m) => {
                  const v = p.metrics[m.key];
                  const fmtd = v == null ? "—"
                    : m.key === "gsc_position" ? v.toFixed(1)
                    : m.key === "pagerank_avg" ? v.toFixed(2)
                    : fmt(v);
                  return <td key={m.key} className="num">{fmtd}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
