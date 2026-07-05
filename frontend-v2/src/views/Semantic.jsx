import { useEffect, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

const RING_COLORS = {
  Core: "var(--chart-maroon)", Focus: "var(--chart-red)",
  Expansion: "var(--chart-amber)", Peripheral: "var(--chart-blue)",
};

/** Centro semántico nativo: GSC, análisis Gemini, mapa, canibalización,
 *  gap y drift — reconstruido sobre la API existente (sin legacy). */
export default function SemanticView() {
  const { jobId } = useCtx();
  const [tab, setTab] = useState("analisis");

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const statusQ = useAsync(() => api.semanticStatus(jobId), [jobId]);
  if (statusQ.loading) return <Spinner />;
  const status = statusQ.data || { status: "none" };

  const TABS = [
    ["analisis", "Análisis"],
    ["mapa", "Mapa semántico"],
    ["anillos", "Anillos objetivo"],
    ["canibalizacion", "Canibalización"],
    ["gap", "Gap de contenido"],
    ["drift", "Drift"],
    ["consultas", "Consultas→Pasajes"],
    ["anclas", "Anclas"],
  ];

  const TAB_HELP = {
    analisis: "Punto de partida: importa los datos de Search Console y lanza el análisis de embeddings (convierte cada página en un vector que captura de qué habla).",
    mapa: "El sitio dibujado por temas: cada punto es una página, la cercanía es parecido temático y el color es el anillo (del núcleo temático a la periferia).",
    anillos: "Escribe el tema al que QUIERES que apunte el sitio y verás cuánto se aleja el sitio real, qué páginas reforzar y cuáles lo están desviando.",
    canibalizacion: "Pares de páginas que hablan de lo mismo y compiten entre sí en Google. Decidir cuál manda es trabajo humano: se firman en la Cola de firma.",
    gap: "Escribe un tema y comprueba si el sitio lo cubre de verdad, lo roza o le falta contenido.",
    drift: "Páginas con mucho peso que hablan de otra cosa: las que están arrastrando el tema global del sitio hacia otro lado.",
    consultas: "Cruza cada búsqueda real de Search Console con los pasajes del sitio: qué demanda está sin responder, qué respuesta está enterrada y qué texto no responde a nada.",
    anclas: "¿Los textos de los enlaces describen su destino? Detecta anchors genéricos («leer más») y anchors que prometen una cosa distinta de lo que hay al otro lado.",
  };

  return (
    <div>
      <div className="row between">
        <h1 className="page-title">Semántica</h1>
        {status.status === "completed" && (
          <a href={api.semanticExportUrl(jobId)}><button className="secondary">Exportar CSV</button></a>
        )}
      </div>
      <p className="page-sub">
        Análisis del CONTENIDO del sitio con embeddings (Gemini): de qué habla cada página, cómo de
        centrado está el sitio en su tema y dónde compite consigo mismo o le falta cobertura.
      </p>
      <div className="toolbar">
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "" : "secondary"} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      <p className="proxy-tag" style={{ marginTop: 2 }}>{TAB_HELP[tab]}</p>

      {tab === "analisis" && <AnalysisPanel jobId={jobId} status={status} onChanged={statusQ.reload} />}
      {tab === "mapa" && <MapPanel jobId={jobId} status={status} />}
      {tab === "anillos" && <TargetRingsPanel jobId={jobId} status={status} />}
      {tab === "canibalizacion" && <CannibalPanel jobId={jobId} status={status} />}
      {tab === "gap" && <GapPanel jobId={jobId} status={status} />}
      {tab === "drift" && <DriftPanel jobId={jobId} status={status} />}
      {tab === "consultas" && <QueryCoveragePanel jobId={jobId} status={status} />}
      {tab === "anclas" && <AnchorsPanel jobId={jobId} status={status} />}
    </div>
  );
}

function NeedsAnalysis() {
  return <Blocked title="Análisis semántico no ejecutado"
    reason="Lanza el análisis desde la pestaña Análisis (necesita una cuenta Gemini en Cuentas)." />;
}

/* ------------------------------------------------------------------ */
/* Análisis: GSC fetch + lanzar análisis + progreso + métricas de sitio */
/* ------------------------------------------------------------------ */
function AnalysisPanel({ jobId, status, onChanged }) {
  const { clientId } = useCtx();
  const gemQ = useAsync(() => api.geminiAccounts().catch(() => []), []);
  const gscQ = useAsync(() => api.gscAccounts().catch(() => []), []);
  const [form, setForm] = useState({
    gemini_account_id: "", alpha: 0.6, beta: 0.4, cannibal_threshold: 0.92,
    chunking_strategy: "fixed", chunk_embedding_mode: "aggregate",
  });
  const [gsc, setGsc] = useState({ gsc_account_id: "", property_url: "", days: 90 });

  // Pre-rellena con las cuentas del proyecto (Configuración → Cuentas del
  // proyecto) para no elegirlas a mano en cada run.
  useEffect(() => {
    if (!clientId) return;
    api.clientSettings(clientId).then((s) => {
      if (s.status !== "ok") return;
      setForm((f) => (f.gemini_account_id ? f
        : { ...f, gemini_account_id: s.gemini_account_id || "" }));
      setGsc((g) => (g.gsc_account_id ? g : {
        ...g,
        gsc_account_id: s.gsc_account_id || "",
        property_url: s.gsc_property || "",
      }));
    }).catch(() => {});
  }, [clientId]);
  const [properties, setProperties] = useState([]);
  const [msg, setMsg] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(status.status === "running");

  useEffect(() => {
    if (!polling) return;
    const t = setInterval(async () => {
      const s = await api.semanticStatus(jobId).catch(() => null);
      if (s && s.status !== "running") {
        setPolling(false);
        onChanged();
      } else if (s) {
        setMsg(`${s.stage || "procesando"} · ${s.progress || 0}%`);
      }
    }, 3000);
    return () => clearInterval(t);
  }, [polling, jobId]);

  const loadProperties = async (accountId) => {
    setGsc({ ...gsc, gsc_account_id: accountId, property_url: "" });
    setProperties([]);
    if (!accountId) return;
    try {
      const props = await api.gscProperties(accountId);
      setProperties(props.properties || props || []);
    } catch (e) { setError(e.message); }
  };

  const doFetchGsc = async () => {
    setBusy(true); setError(null); setMsg(null);
    try {
      const r = await api.fetchGsc(jobId, gsc);
      setMsg(`GSC importado: ${r.matched} con match, ${r.unmatched ?? 0} sin match (conservadas), ${r.query_rows} filas de queries.`);
      onChanged();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const doAnalyze = async () => {
    setBusy(true); setError(null);
    try {
      await api.semanticAnalyze(jobId, {
        ...form,
        alpha: Number(form.alpha), beta: Number(form.beta),
        cannibal_threshold: Number(form.cannibal_threshold),
      });
      setMsg("Análisis lanzado…");
      setPolling(true);
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const resultsQ = useAsync(
    () => (status.status === "completed" ? api.semanticResults(jobId) : Promise.resolve(null)),
    [jobId, status.status],
  );
  const results = resultsQ.data;

  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
      <div>
        <div className="card" style={{ marginBottom: 12 }}>
          <h3>1 · Datos GSC (opcional pero recomendado)</h3>
          {status.gsc && status.gsc.total > 0 ? (
            <div className="alert warn" style={{ marginBottom: 8 }}>
              ✓ Este run YA tiene datos GSC importados: <b className="num">{fmt(status.gsc.total)}</b> URLs con métricas
              ({fmt(status.gsc.matched)} con match en el rastreo, {fmt(status.gsc.unmatched)} sin match, conservadas como
              posibles huérfanas). Las verás como columnas en el Explorador. Re-importar sobrescribe.
            </div>
          ) : (
            <div className="proxy-tag" style={{ marginBottom: 8 }}>
              Este run aún no tiene datos GSC. Al importarlos, los clics, impresiones y posición aparecen
              como columnas por URL en el Explorador y alimentan striking distance y el análisis semántico.
            </div>
          )}
          {(gscQ.data || []).length === 0 && (
            <div className="proxy-tag">Sin cuentas GSC. <a href="#/cuentas">Añade una en Cuentas →</a></div>
          )}
          {(gscQ.data || []).length > 0 && (
            <>
              <div className="field">
                <label>Cuenta GSC</label>
                <select value={gsc.gsc_account_id} onChange={(e) => loadProperties(e.target.value)}>
                  <option value="">— elegir —</option>
                  {gscQ.data.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
              <div className="form-grid">
                <div className="field">
                  <label>Propiedad</label>
                  {properties.length > 0 ? (
                    <select value={gsc.property_url} onChange={(e) => setGsc({ ...gsc, property_url: e.target.value })}>
                      <option value="">— elegir —</option>
                      {properties.map((p) => {
                        const url = typeof p === "string" ? p : p.siteUrl || p.url;
                        return <option key={url} value={url}>{url}</option>;
                      })}
                    </select>
                  ) : (
                    <input type="text" placeholder="sc-domain:ejemplo.com" value={gsc.property_url}
                      onChange={(e) => setGsc({ ...gsc, property_url: e.target.value })} />
                  )}
                </div>
                <div className="field">
                  <label>Días</label>
                  <input type="number" min={7} max={480} value={gsc.days}
                    onChange={(e) => setGsc({ ...gsc, days: Number(e.target.value) })} />
                </div>
              </div>
              <button className="secondary" disabled={busy || !gsc.gsc_account_id || !gsc.property_url}
                onClick={doFetchGsc}>Importar datos GSC</button>
            </>
          )}
        </div>

        <div className="card">
          <h3>2 · Análisis de embeddings (Gemini)</h3>
          {(gemQ.data || []).length === 0 && (
            <div className="proxy-tag">Sin cuentas Gemini. <a href="#/cuentas">Añade una en Cuentas →</a></div>
          )}
          {(gemQ.data || []).length > 0 && (
            <>
              <div className="field">
                <label>Cuenta Gemini (cada cliente paga sus embeddings)</label>
                <select value={form.gemini_account_id}
                  onChange={(e) => setForm({ ...form, gemini_account_id: e.target.value })}>
                  <option value="">— elegir —</option>
                  {gemQ.data.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
              <p className="proxy-tag">
                El «centro» temático del sitio se calcula ponderando cada página. α y β reparten ese peso
                (deben sumar 1): α = manda la autoridad interna (PageRank), β = mandan los clics reales de
                Search Console. El umbral de canibalización marca cómo de parecidas tienen que ser dos
                páginas para considerarlas competidoras (0.92 = muy parecidas).
              </p>
              <div className="form-grid">
                <div className="field">
                  <label>α (peso de la autoridad interna)</label>
                  <input type="number" step="0.1" min="0" max="1" value={form.alpha}
                    onChange={(e) => setForm({ ...form, alpha: e.target.value })} />
                </div>
                <div className="field">
                  <label>β (peso de los clics de GSC)</label>
                  <input type="number" step="0.1" min="0" max="1" value={form.beta}
                    onChange={(e) => setForm({ ...form, beta: e.target.value })} />
                </div>
                <div className="field">
                  <label>Umbral canibalización</label>
                  <input type="number" step="0.01" min="0.5" max="1" value={form.cannibal_threshold}
                    onChange={(e) => setForm({ ...form, cannibal_threshold: e.target.value })} />
                </div>
                <div className="field">
                  <label>Troceado en pasajes (chunking)</label>
                  <select value={form.chunking_strategy}
                    onChange={(e) => setForm({ ...form, chunking_strategy: e.target.value })}>
                    <option value="fixed">fijo — trozos de tamaño constante (histórico)</option>
                    <option value="semantic">semántico — corta en los cambios de tema y encabezados (mejor para Consultas→Pasajes)</option>
                  </select>
                </div>
              </div>
              <button disabled={busy || polling || !form.gemini_account_id} onClick={doAnalyze}>
                {polling ? "Analizando…" : "Lanzar análisis semántico"}
              </button>
            </>
          )}
          {msg && <div className="alert warn" style={{ marginTop: 10 }}>{msg}</div>}
          {error && <div className="alert" style={{ marginTop: 10 }}>{error}</div>}
          {status.status === "failed" && (
            <div className="alert" style={{ marginTop: 10 }}>Último análisis falló: {status.error_message}</div>
          )}
        </div>
      </div>

      <div>
        {status.status === "completed" && results && (
          <div className="card">
            <h3>Métricas del sitio</h3>
            <div className="facts">
              {Object.entries(results.site_metrics || {}).map(([k, v]) => (
                <div className="fact" key={k}>
                  <div className="k">{k}</div>
                  <div className="v num">{typeof v === "number" ? v.toFixed(3) : String(v)}</div>
                </div>
              ))}
            </div>
            {results.gsc_summary && (
              <>
                <h3>GSC (periodo importado en este run)</h3>
                <div className="facts">
                  <div className="fact"><div className="k">Clics</div><div className="v num">{fmt(results.gsc_summary.total_clicks)}</div></div>
                  <div className="fact"><div className="k">Impresiones</div><div className="v num">{fmt(results.gsc_summary.total_impressions)}</div></div>
                  <div className="fact"><div className="k">CTR medio</div><div className="v num">{results.gsc_summary.avg_ctr != null ? `${(results.gsc_summary.avg_ctr * 100).toFixed(2)}%` : "—"}</div></div>
                  <div className="fact"><div className="k">Posición media</div><div className="v num">{results.gsc_summary.avg_position ?? "—"}</div></div>
                </div>
              </>
            )}
            <div className="proxy-tag">
              {fmt(results.total_pages)} páginas · estrategia {results.config && results.config.chunking_strategy} ·
              modelo {results.config && results.config.embedding_model}
            </div>
          </div>
        )}
        {status.status !== "completed" && (
          <Blocked title="Sin resultados todavía"
            reason={status.status === "running" ? "Análisis en curso…" : "Lanza el análisis para ver las métricas del sitio, el mapa y la canibalización."} />
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Mapa semántico: scatter SVG propio (sin plotly)                     */
/* ------------------------------------------------------------------ */
function MapPanel({ jobId, status }) {
  const [hover, setHover] = useState(null);
  if (status.status !== "completed") return <NeedsAnalysis />;

  const q = useAsync(() => api.semanticResults(jobId), [jobId]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const pages = (q.data.pages || []).filter((p) => p.x != null && p.y != null);
  if (!pages.length) return <Blocked title="Sin coordenadas" reason="El análisis no generó proyección 2D." />;

  const xs = pages.map((p) => p.x), ys = pages.map((p) => p.y);
  const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
  const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];
  const W = 900, H = 560, PAD = 30;
  const sx = (x) => PAD + ((x - minX) / (maxX - minX || 1)) * (W - 2 * PAD);
  const sy = (y) => PAD + ((y - minY) / (maxY - minY || 1)) * (H - 2 * PAD);

  return (
    <div className="card">
      <h3>Mapa semántico (UMAP) · color por anillo, tamaño por peso</h3>
      <div className="row" style={{ gap: 14, marginBottom: 8 }}>
        {Object.entries(RING_COLORS).map(([ring, color]) => (
          <span key={ring} className="sev"><span className="dot" style={{ background: color }} />{ring}</span>
        ))}
      </div>
      <div style={{ overflowX: "auto" }}>
        <svg width={W} height={H} style={{ border: "1px solid var(--hairline)", background: "var(--canvas-muted)" }}>
          {pages.map((p, i) => (
            <circle key={i} cx={sx(p.x)} cy={sy(p.y)}
              r={3 + (p.weight || 0) * 6}
              fill={RING_COLORS[p.ring] || "var(--ink-muted)"}
              fillOpacity={0.75}
              onMouseEnter={() => setHover(p)}
              onMouseLeave={() => setHover(null)}
            />
          ))}
        </svg>
      </div>
      <div className="mono" style={{ minHeight: 34, fontSize: 11.5, marginTop: 6 }}>
        {hover
          ? `${hover.url} · ${hover.ring || "?"} · dist ${hover.distance_to_centroid?.toFixed(3)} · ${hover.clicks != null ? `${fmt(hover.clicks)} clics` : "sin GSC"}`
          : "Pasa el ratón por un punto para ver la URL."}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Anillos objetivo: re-centra el mapa sobre un tema deseado           */
/* ------------------------------------------------------------------ */
function TargetRingsPanel({ jobId, status }) {
  const [theme, setTheme] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (status.status !== "completed") return <NeedsAnalysis />;

  const run = async () => {
    setBusy(true); setError(null);
    try {
      setResult(await api.targetRings(jobId, { target_theme: theme.trim() }));
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="grid" style={{ gridTemplateColumns: "340px 1fr", alignItems: "start" }}>
      <div className="card">
        <h3>Tema objetivo del sitio</h3>
        <input type="text" value={theme} onChange={(e) => setTheme(e.target.value)}
          placeholder="asesoría fiscal para pymes"
          onKeyDown={(e) => e.key === "Enter" && theme.trim() && run()} />
        <button style={{ marginTop: 8 }} disabled={busy || !theme.trim()} onClick={run}>
          {busy ? "Embebiendo…" : "Re-centrar anillos"}
        </button>
        {error && <div className="alert" style={{ marginTop: 8 }}>{error}</div>}
        {result && (
          <div className="card muted" style={{ marginTop: 10 }}>
            <div className="kpi-label">Alineación centro actual ↔ tema</div>
            <div className="kpi-value num">{(result.alignment * 100).toFixed(1)}%</div>
            <div className="facts" style={{ marginTop: 8 }}>
              {Object.entries(result.ring_counts || {}).map(([ring, n]) => (
                <div className="fact" key={ring}>
                  <div className="k"><span className="dot" style={{ background: RING_COLORS[ring] }} /> {ring}</div>
                  <div className="v num">{fmt(n)}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <div>
        {!result && <Blocked title="Anillos objetivo"
          reason="Introduce el tema al que QUIERES que apunte el sitio: se reclasifican los anillos respecto a ese tema y salen las páginas a reforzar o reenfocar." />}
        {result && (
          <>
            <div className="card" style={{ marginBottom: 12 }}>
              <h3>Reforzar — cerca del tema pero con poco peso interno (enlázalas más)</h3>
              {result.reinforce.length === 0 && <div className="proxy-tag">Nada que reforzar.</div>}
              {result.reinforce.length > 0 && (
                <table className="data">
                  <thead><tr><th>URL</th><th>Anillo (tema)</th><th>Anillo (actual)</th>
                    <th className="num">Dist. al tema</th><th className="num">Peso</th><th className="num">Clics</th></tr></thead>
                  <tbody>
                    {result.reinforce.map((r, i) => (
                      <tr key={i}>
                        <td className="cell-url" title={r.url}>{r.url}</td>
                        <td><span className="tag">{r.ring_target}</span></td>
                        <td><span className="tag">{r.ring_current}</span></td>
                        <td className="num">{r.distance_to_target?.toFixed(3)}</td>
                        <td className="num">{r.weight?.toFixed(3)}</td>
                        <td className="num">{r.clicks ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card">
              <h3>Reenfocar — lejos del tema y con mucho peso (tiran del centro hacia otro lado)</h3>
              {result.refocus.length === 0 && <div className="proxy-tag">Nada que reenfocar.</div>}
              {result.refocus.length > 0 && (
                <table className="data">
                  <thead><tr><th>URL</th><th>Anillo (tema)</th><th>Anillo (actual)</th>
                    <th className="num">Dist. al tema</th><th className="num">Peso</th><th className="num">Clics</th></tr></thead>
                  <tbody>
                    {result.refocus.map((r, i) => (
                      <tr key={i}>
                        <td className="cell-url" title={r.url}>{r.url}</td>
                        <td><span className="tag">{r.ring_target}</span></td>
                        <td><span className="tag">{r.ring_current}</span></td>
                        <td className="num">{r.distance_to_target?.toFixed(3)}</td>
                        <td className="num">{r.weight?.toFixed(3)}</td>
                        <td className="num">{r.clicks ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Canibalización — con firma T10                                      */
/* ------------------------------------------------------------------ */
function CannibalPanel({ jobId, status }) {
  const [brand, setBrand] = useState("");
  const [applied, setApplied] = useState("");
  if (status.status !== "completed") return <NeedsAnalysis />;

  const q = useAsync(
    () => api.semanticCannibalization(jobId, { brand: applied }),
    [jobId, applied],
  );
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const pairs = q.data.pairs || q.data || [];

  return (
    <div>
      <div className="toolbar">
        <input type="text" style={{ width: 280 }} placeholder="palabras de marca a excluir (coma)"
          value={brand} onChange={(e) => setBrand(e.target.value)} />
        <button className="secondary" onClick={() => setApplied(brand)}>Filtrar</button>
        <span className="proxy-tag">Los pares también entran en la Cola de firma como issues pendientes.</span>
      </div>
      {pairs.length === 0 && <div className="empty-clean">Sin pares de canibalización sobre el umbral.</div>}
      <div className="table-wrap" style={{ maxHeight: "62vh" }}>
        <table className="data">
          <thead><tr><th>Dominante</th><th>Débil</th><th className="num">Similitud</th></tr></thead>
          <tbody>
            {pairs.map((p, i) => (
              <tr key={i}>
                <td className="cell-url" title={p.url_dominant || p.dominant_url}>{p.url_dominant || p.dominant_url}</td>
                <td className="cell-url" title={p.url_weak || p.weak_url}>{p.url_weak || p.weak_url}</td>
                <td className="num">{(p.cosine_similarity ?? p.similarity) != null ? (p.cosine_similarity ?? p.similarity).toFixed(4) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Gap de contenido                                                     */
/* ------------------------------------------------------------------ */
function GapPanel({ jobId, status }) {
  const [topic, setTopic] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (status.status !== "completed") return <NeedsAnalysis />;

  const run = async () => {
    setBusy(true); setError(null);
    try {
      setResult(await api.semanticGap(jobId, { topic: topic.trim() }));
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const best = result && result.candidates && result.candidates[0];

  return (
    <div className="grid" style={{ gridTemplateColumns: "340px 1fr", alignItems: "start" }}>
      <div className="card">
        <h3>Tema / consulta objetivo</h3>
        <input type="text" value={topic} onChange={(e) => setTopic(e.target.value)}
          placeholder="fiscalidad de criptomonedas para autónomos"
          onKeyDown={(e) => e.key === "Enter" && topic.trim() && run()} />
        <button style={{ marginTop: 8 }} disabled={busy || !topic.trim()} onClick={run}>
          {busy ? "Embebiendo…" : "Analizar cobertura"}
        </button>
        {error && <div className="alert" style={{ marginTop: 8 }}>{error}</div>}
        {best && (
          <div className="card muted" style={{ marginTop: 10 }}>
            <div className="kpi-label">Veredicto</div>
            <div className="editorial" style={{ fontSize: 15 }}>
              {best.similarity_to_topic >= 0.75
                ? "El sitio tiene contenido que cubre este tema."
                : best.similarity_to_topic >= 0.6
                  ? "Cobertura parcial: hay contenido cercano pero ninguna página lo ataca de lleno."
                  : "Gap real: no existe contenido que cubra este tema."}
            </div>
          </div>
        )}
      </div>
      <div>
        {!result && <Blocked title="Gap de contenido" reason="Introduce un tema: verás las páginas más cercanas y si el sitio lo cubre de verdad." />}
        {result && (
          <div className="card">
            <h3>Páginas más cercanas a «{result.topic}»</h3>
            <table className="data">
              <thead>
                <tr><th>URL</th><th className="num">Sim. al tema</th>
                  <th className="num">Sim. al centro del sitio</th>
                  <th className="num">Clics</th><th className="num">Posición</th></tr>
              </thead>
              <tbody>
                {result.candidates.map((c, i) => (
                  <tr key={i}>
                    <td className="cell-url" title={c.url}>{c.url}</td>
                    <td className="num">{c.similarity_to_topic?.toFixed(4)}</td>
                    <td className="num">{c.similarity_to_centroid?.toFixed(4)}</td>
                    <td className="num">{c.clicks ?? "—"}</td>
                    <td className="num">{c.position != null ? c.position.toFixed(1) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* T19 — Cobertura consulta→pasaje                                     */
/* ------------------------------------------------------------------ */
function QueryCoveragePanel({ jobId, status }) {
  const [params, setParams] = useState({
    max_queries: 200, min_impressions: 10, sim_threshold: 0.6,
    buried_min_position: 5, orphan_threshold: 0.5,
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (status.status !== "completed") return <NeedsAnalysis />;

  const q = useAsync(() => api.queryCoverage(jobId), [jobId]);

  const run = async () => {
    setBusy(true); setError(null);
    try {
      await api.runQueryCoverage(jobId, {
        ...params,
        max_queries: Number(params.max_queries),
        min_impressions: Number(params.min_impressions),
        sim_threshold: Number(params.sim_threshold),
        buried_min_position: Number(params.buried_min_position),
        orphan_threshold: Number(params.orphan_threshold),
      });
      q.reload();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  if (q.loading) return <Spinner />;
  const d = q.data || { status: "blocked", reason: "not_run" };
  const rows = d.status === "ok" ? d.queries : [];
  const sorted = rows.slice().sort((a, b) =>
    (a.covered === b.covered ? (b.impressions || 0) - (a.impressions || 0) : a.covered ? 1 : -1));

  return (
    <div>
      <div className="card" style={{ marginBottom: 12 }}>
        <h3>Cobertura consulta→pasaje: ¿qué búsquedas reales tienen un pasaje que las responda?</h3>
        <p className="proxy-tag" style={{ marginTop: 0 }}>
          Toma las búsquedas por las que el sitio aparece en Google (Search Console) y comprueba, una a una,
          si algún pasaje del sitio las responde de verdad. Lo que encuentre (demanda sin responder, respuestas
          enterradas, texto que no responde a nada) entra como propuestas en la Cola de firma.
        </p>
        <div className="form-grid">
          <div className="field"><label>Máx. queries</label>
            <input type="number" min={10} max={1000} value={params.max_queries}
              onChange={(e) => setParams({ ...params, max_queries: e.target.value })} /></div>
          <div className="field"><label>Impresiones mínimas</label>
            <input type="number" min={0} value={params.min_impressions}
              onChange={(e) => setParams({ ...params, min_impressions: e.target.value })} /></div>
          <div className="field"><label>Umbral cobertura (cos)</label>
            <input type="number" step="0.05" min="0" max="1" value={params.sim_threshold}
              onChange={(e) => setParams({ ...params, sim_threshold: e.target.value })} /></div>
          <div className="field"><label>Pos. de chunk "enterrado" ≥</label>
            <input type="number" min={1} value={params.buried_min_position}
              onChange={(e) => setParams({ ...params, buried_min_position: e.target.value })} /></div>
        </div>
        <button disabled={busy} onClick={run}>
          {busy ? "Embebiendo queries…" : d.status === "ok" ? "Recalcular cobertura" : "Calcular cobertura"}
        </button>
        {error && <div className="alert" style={{ marginTop: 8 }}>{error}</div>}
        {d.status === "blocked" && d.reason === "no_gsc_query_data" && (
          <div className="alert warn" style={{ marginTop: 8 }}>
            Sin datos de queries de GSC: impórtalos en la pestaña Análisis.
          </div>
        )}
      </div>

      {d.status === "ok" && (
        <>
          <div className="facts" style={{ marginBottom: 12 }}>
            <div className="fact"><div className="k">Queries analizadas</div><div className="v num">{fmt(d.summary.queries_analyzed)}</div></div>
            <div className="fact"><div className="k">Cubiertas</div><div className="v num">{fmt(d.summary.covered)} ({(d.summary.coverage_ratio * 100).toFixed(0)}%)</div></div>
            <div className="fact"><div className="k">Gaps de pasaje</div><div className="v num">{fmt(d.summary.gaps)}</div></div>
            <div className="fact"><div className="k">Pasajes enterrados</div><div className="v num">{fmt(d.summary.buried)}</div></div>
            <div className="fact"><div className="k">Chunks huérfanos</div><div className="v num">{fmt(d.summary.orphan_chunks)} / {fmt(d.summary.chunks_total)}</div></div>
          </div>
          <div className="card">
            <h3>Queries · sin cubrir primero, por impresiones</h3>
            <div className="table-wrap" style={{ maxHeight: "58vh" }}>
              <table className="data">
                <thead>
                  <tr><th>Query</th><th className="num">Impresiones</th><th className="num">Clics</th>
                    <th className="num">Mejor similitud</th><th>Estado</th><th>Mejor pasaje (URL · posición)</th></tr>
                </thead>
                <tbody>
                  {sorted.map((r, i) => (
                    <tr key={i}>
                      <td title={r.query}>{r.query}</td>
                      <td className="num">{fmt(r.impressions)}</td>
                      <td className="num">{fmt(r.clicks)}</td>
                      <td className="num">{r.best_similarity != null ? r.best_similarity.toFixed(3) : "—"}</td>
                      <td>
                        {!r.covered ? <span className="tag" style={{ color: "var(--chart-red)" }}>gap</span>
                          : r.buried ? <span className="tag" style={{ color: "var(--chart-amber)" }}>enterrado</span>
                            : <span className="tag" style={{ color: "var(--chart-forest)" }}>cubierta</span>}
                      </td>
                      <td className="cell-url" title={r.best_chunk_url || ""}>
                        {r.best_chunk_url ? `${r.best_chunk_url} · #${r.best_chunk_position}` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* T18 — Relevancia de anchors contextuales                            */
/* ------------------------------------------------------------------ */
function AnchorsPanel({ jobId, status }) {
  const [params, setParams] = useState({ mismatch_threshold: 0.35, max_anchors: 300 });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (status.status !== "completed") return <NeedsAnalysis />;

  const run = async () => {
    setBusy(true); setError(null);
    try {
      setResult(await api.anchorRelevance(jobId, {
        mismatch_threshold: Number(params.mismatch_threshold),
        max_anchors: Number(params.max_anchors),
      }));
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div>
      <div className="card" style={{ marginBottom: 12 }}>
        <h3>Relevancia de anchors: ¿los textos de los enlaces describen su destino?</h3>
        <p className="proxy-tag" style={{ marginTop: 0 }}>
          El anchor es la promesa del enlace. Los genéricos («leer más», «aquí») se detectan sin coste;
          el resto se compara semánticamente con la página de destino para cazar enlaces que prometen
          una cosa y llevan a otra. Ambos entran como propuestas en la Cola de firma.
        </p>
        <div className="form-grid">
          <div className="field"><label>Umbral de mismatch (cos)</label>
            <input type="number" step="0.05" min="0" max="1" value={params.mismatch_threshold}
              onChange={(e) => setParams({ ...params, mismatch_threshold: e.target.value })} /></div>
          <div className="field"><label>Máx. anchors a embeber</label>
            <input type="number" min={10} max={2000} value={params.max_anchors}
              onChange={(e) => setParams({ ...params, max_anchors: e.target.value })} /></div>
        </div>
        <button disabled={busy} onClick={run}>{busy ? "Embebiendo anchors…" : "Analizar anchors"}</button>
        {error && <div className="alert" style={{ marginTop: 8 }}>{error}</div>}
        {result && result.status === "blocked" && (
          <div className="alert warn" style={{ marginTop: 8 }}>
            {result.reason === "no_contextual_anchors"
              ? "El run no tiene enlaces contextuales con anchor de texto."
              : "El análisis no tiene vectores de página."}
          </div>
        )}
      </div>

      {result && result.status === "ok" && (
        <>
          <div className="facts" style={{ marginBottom: 12 }}>
            <div className="fact"><div className="k">Grupos anchor→destino</div><div className="v num">{fmt(result.summary.anchor_groups)}</div></div>
            <div className="fact"><div className="k">Embebidos</div><div className="v num">{fmt(result.summary.embedded)}</div></div>
            <div className="fact"><div className="k">Destinos con anchors genéricos</div><div className="v num">{fmt(result.summary.generic_targets)}</div></div>
            <div className="fact"><div className="k">Mismatches</div><div className="v num">{fmt(result.summary.mismatches)}</div></div>
          </div>

          <div className="card" style={{ marginBottom: 12 }}>
            <h3>Anchor ↔ destino sin relación (peor primero)</h3>
            {result.mismatches.length === 0 && <div className="proxy-tag">Sin mismatches bajo el umbral.</div>}
            {result.mismatches.length > 0 && (
              <table className="data">
                <thead><tr><th>Anchor</th><th>Destino</th><th className="num">Similitud</th>
                  <th className="num">Enlaces</th><th>Origen (muestra)</th></tr></thead>
                <tbody>
                  {result.mismatches.map((m, i) => (
                    <tr key={i}>
                      <td title={m.anchor}>«{m.anchor}»</td>
                      <td className="cell-url" title={m.target_url}>{m.target_url}</td>
                      <td className="num">{m.similarity != null ? m.similarity.toFixed(3) : "—"}</td>
                      <td className="num">{fmt(m.n_links)}</td>
                      <td className="cell-url">{(m.sources_sample || [])[0] || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card">
            <h3>Destinos con anchors genéricos (más inlinks genéricos primero)</h3>
            {result.generic.length === 0 && <div className="proxy-tag">Sin anchors genéricos contextuales.</div>}
            {result.generic.length > 0 && (
              <table className="data">
                <thead><tr><th>Destino</th><th className="num">Inlinks genéricos</th><th>Anchors</th></tr></thead>
                <tbody>
                  {result.generic.map((g, i) => (
                    <tr key={i}>
                      <td className="cell-url" title={g.target_url}>{g.target_url}</td>
                      <td className="num">{fmt(g.generic_inlinks)}</td>
                      <td>{(g.anchors || []).map((a) => `«${a}»`).join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Drift                                                                */
/* ------------------------------------------------------------------ */
function DriftPanel({ jobId, status }) {
  if (status.status !== "completed") return <NeedsAnalysis />;
  const q = useAsync(() => api.semanticDrift(jobId), [jobId]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const entries = (q.data && q.data.drift) || [];
  return (
    <div className="card">
      <h3>Páginas que más arrastran el centro semántico (peso × distancia)</h3>
      <p className="proxy-tag">Mucho peso y mucha distancia = la página tira del sitio hacia su tema. Decide si es intencional o dilución.</p>
      {entries.length === 0 && <div className="proxy-tag">Sin candidatas de drift.</div>}
      <table className="data">
        <thead>
          <tr><th>URL</th><th className="num">Drift score</th>
            <th className="num">Distancia</th><th className="num">Peso</th>
            <th className="num">Clics</th><th className="num">Posición</th></tr>
        </thead>
        <tbody>
          {entries.map((a, i) => (
            <tr key={i}>
              <td className="cell-url" title={a.url}>{a.url}</td>
              <td className="num">{a.drift_score?.toFixed(4)}</td>
              <td className="num">{a.distance?.toFixed(4)}</td>
              <td className="num">{a.weight?.toFixed(4)}</td>
              <td className="num">{a.clicks ?? "—"}</td>
              <td className="num">{a.position != null ? a.position.toFixed(1) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
