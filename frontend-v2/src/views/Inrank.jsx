import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

/** Enlazado · Inrank: striking distance (T9), delta estructural↔semántico
 *  (T18), simulador what-if (T21) y profundidad de clic real (T23). */
export default function InrankView() {
  const { jobId, segmentId } = useCtx();
  const [tab, setTab] = useState("striking");

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const TABS = [
    ["striking", "Striking distance"],
    ["delta", "Delta PR semántico"],
    ["simulador", "Simulador what-if"],
    ["profundidad", "Profundidad de clic"],
    ["flujos", "Flujos entre secciones"],
    ["aristas", "Aristas del grafo"],
  ];

  const TAB_HELP = {
    striking: "Consultas que rankean en posición 5–15: un empujón de enlazado interno puede subirlas a primera página. Es la cola de trabajo con mejor retorno.",
    delta: "Compara la autoridad estructural (quién te enlaza) con la semántica (quién te enlaza HABLANDO de lo tuyo). Diferencias grandes = autoridad frágil o desaprovechada.",
    simulador: "Prueba enlaces hipotéticos y mira cómo se movería la autoridad de todo el sitio ANTES de tocar nada. No escribe nada en los datos.",
    profundidad: "Cuántos clics reales hacen falta desde la portada hasta cada página. Lo profundo se rastrea menos y posiciona peor.",
    flujos: "Cuánta autoridad fluye de cada sección del sitio a las demás. La foto de arquitectura para sitios grandes.",
    aristas: "La vista agregada del grafo de enlaces: los enlaces repetidos en todo el sitio (menú, footer) colapsan en una sola fila.",
  };

  return (
    <div>
      <h1 className="page-title">Enlazado · Inrank</h1>
      <p className="page-sub">
        Todo lo relativo a cómo se reparte la autoridad por el enlazado interno: dónde empujar,
        qué está mal colgado y qué pasaría si cambias enlaces.
      </p>
      <div className="toolbar">
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "" : "secondary"} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <p className="proxy-tag" style={{ marginTop: 2 }}>{TAB_HELP[tab]}</p>
      {tab === "striking" && <StrikingPanel jobId={jobId} />}
      {tab === "delta" && <DeltaPanel jobId={jobId} segmentId={segmentId} />}
      {tab === "simulador" && <SimulatorPanel jobId={jobId} />}
      {tab === "profundidad" && <DepthPanel jobId={jobId} segmentId={segmentId} />}
      {tab === "flujos" && <FlowsPanel jobId={jobId} />}
      {tab === "aristas" && <EdgesPanel jobId={jobId} />}
    </div>
  );
}

/* -- Matriz agregada de aristas (T22) ---------------------------------------- */
const EDGE_CLASSES = ["contextual", "listado", "breadcrumb", "paginacion", "menu", "footer", "sidebar", "desconocido"];

function EdgesPanel({ jobId }) {
  const [page, setPage] = useState(1);
  const [klass, setKlass] = useState("");
  const q = useAsync(
    () => api.archEdges(jobId, { edge_class: klass, page, page_size: 50 }),
    [jobId, klass, page],
  );
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (d.status === "blocked") {
    return <Blocked title="Aristas del grafo"
      reason="La matriz agregada se materializa con edge_classification=true. Los enlaces sitewide colapsan en una fila (origen *)." />;
  }
  return (
    <div className="card">
      <h3>Vista agregada del grafo · sitewide colapsado en una fila por destino ({fmt(d.total)})</h3>
      <div className="toolbar">
        <select value={klass} onChange={(e) => { setKlass(e.target.value); setPage(1); }}>
          <option value="">todas las clases</option>
          {EDGE_CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="table-wrap" style={{ maxHeight: "60vh" }}>
        <table className="data">
          <thead>
            <tr><th>Origen</th><th>Destino</th><th>Clase</th>
              <th className="num">Páginas</th><th>Anchor (muestra)</th></tr>
          </thead>
          <tbody>
            {d.items.map((e, i) => (
              <tr key={i}>
                <td className="cell-url" title={e.source_url || "sitewide"}>
                  {e.source_url || <span className="tag">sitewide ({fmt(e.n_pages)} páginas)</span>}
                </td>
                <td className="cell-url" title={e.target_url}>{e.target_url}</td>
                <td><span className="tag">{e.edge_class}</span></td>
                <td className="num">{fmt(e.n_pages)}</td>
                <td className="cell-url" title={e.anchor_sample || ""}>{e.anchor_sample || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={d.pages} onPage={setPage} />
    </div>
  );
}

/* -- Flujos de autoridad segmento→segmento (T23, la "vista estrella") ------- */
function FlowsPanel({ jobId }) {
  const q = useAsync(() => api.sectionFlows(jobId), [jobId]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (d.status === "blocked") {
    return <Blocked title="Flujos entre secciones"
      reason="Se calculan con edge_classification=true y segmentos definidos: flujo = amortiguación × PR(origen) × peso de la arista." />;
  }
  const maxFlow = Math.max(...d.flows.map((f) => f.flow), 1e-9);
  return (
    <div className="card">
      <h3>Cómo fluye la autoridad entre secciones · masa total {d.total_flow?.toFixed(3)}</h3>
      <table className="data" style={{ maxWidth: 760 }}>
        <thead><tr><th>Desde</th><th>Hacia</th><th style={{ width: "40%" }}>Flujo</th><th className="num">Valor</th></tr></thead>
        <tbody>
          {d.flows.map((f, i) => (
            <tr key={i}>
              <td><span className="tag">{f.from_segment}</span></td>
              <td><span className="tag">{f.to_segment}</span></td>
              <td>
                <span className="bar" style={{ display: "block", height: 8, background: "var(--surface-soft)", position: "relative" }}>
                  <i style={{ position: "absolute", inset: "0 auto 0 0", width: `${(f.flow / maxFlow) * 100}%`, background: "var(--chart-navy)" }} />
                </span>
              </td>
              <td className="num">{f.flow.toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="proxy-tag">Con más de ~2.000 URLs esta matriz es la vista por defecto de arquitectura, no el grafo nodo a nodo.</p>
    </div>
  );
}

/* -- Striking distance (T9) ------------------------------------------------ */
function StrikingPanel({ jobId }) {
  const [page, setPage] = useState(1);
  const q = useAsync(() => api.strikingDistance(jobId, { page, page_size: 50 }), [jobId, page]);
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (d.status === "blocked") {
    return <Blocked title="Striking distance"
      reason="Necesita datos de GSC del run (posición media por URL)."
      cta={<a href="#/semantica"><button>Importar GSC en Semántica</button></a>} />;
  }
  return (
    <div className="card">
      <h3>Cola de trabajo de enlazado: posición 5–15, ordenado por impresiones ↓ y PageRank ↑ ({fmt(d.total)})</h3>
      <div className="table-wrap" style={{ maxHeight: "60vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL</th><th className="num">Posición</th><th className="num">Impresiones</th>
              <th className="num">Clics</th><th className="num">PageRank</th><th className="num">Inlinks</th></tr>
          </thead>
          <tbody>
            {d.items.map((u, i) => (
              <tr key={i}>
                <td className="cell-url" title={u.url}>{u.url}</td>
                <td className="num">{u.position?.toFixed(1)}</td>
                <td className="num">{fmt(u.impressions)}</td>
                <td className="num">{fmt(u.clicks)}</td>
                <td className="num">{u.pagerank ?? "—"}</td>
                <td className="num">{fmt(u.inlinks_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={d.pages} onPage={setPage} />
    </div>
  );
}

/* -- Delta PR estructural vs semántico (T18) -------------------------------- */
function DeltaPanel({ jobId, segmentId }) {
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.pagerankDelta(jobId, { segment_id: segmentId, page, page_size: 50 }),
    [jobId, segmentId, page],
  );
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (d.status === "blocked") {
    return <Blocked title="PageRank semántico"
      reason="Se calcula al terminar el análisis semántico (pondera cada arista por la similitud origen→destino)."
      cta={<a href="#/semantica"><button>Ir a Semántica</button></a>} />;
  }
  return (
    <div className="card">
      <h3>Delta = estructural − semántico · positivo = sostenida por boilerplate (frágil); negativo = contextual fuerte con poco volumen</h3>
      <div className="table-wrap" style={{ maxHeight: "60vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL</th><th className="num">PR estructural</th>
              <th className="num">PR semántico</th><th className="num">Delta</th></tr>
          </thead>
          <tbody>
            {d.items.map((u, i) => (
              <tr key={i}>
                <td className="cell-url" title={u.url}>{u.url}</td>
                <td className="num">{u.pagerank}</td>
                <td className="num">{u.pagerank_semantic}</td>
                <td className="num" style={{ color: u.delta > 1 ? "var(--chart-red)" : u.delta < -1 ? "var(--chart-forest)" : undefined }}>
                  {u.delta > 0 ? "+" : ""}{u.delta}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={d.pages} onPage={setPage} />
    </div>
  );
}

/* -- Simulador what-if (T21) ------------------------------------------------- */
function SimulatorPanel({ jobId }) {
  const [rows, setRows] = useState([{ from: "", to: "", position: "content" }]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const setRow = (i, k, v) => {
    const next = rows.slice();
    next[i] = { ...next[i], [k]: v };
    setRows(next);
  };

  const run = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      // Resuelve URLs → hashes vía búsqueda exacta en el explorador
      const add = [];
      for (const r of rows) {
        if (!r.from.trim() || !r.to.trim()) continue;
        const [fromRes, toRes] = await Promise.all([
          api.urls(jobId, { search: r.from.trim(), page_size: 1 }),
          api.urls(jobId, { search: r.to.trim(), page_size: 1 }),
        ]);
        const src = fromRes.items.find((u) => u.url === r.from.trim()) || fromRes.items[0];
        const dst = toRes.items.find((u) => u.url === r.to.trim()) || toRes.items[0];
        if (!src || !dst) throw new Error(`No encuentro en el run: ${!src ? r.from : r.to}`);
        add.push({ from_hash: src.url_hash, to_hash: dst.url_hash, position: r.position });
      }
      if (!add.length) throw new Error("Añade al menos un enlace a simular");
      setResult(await api.simulate(jobId, { add, top_n: 25 }));
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
      <div className="card">
        <h3>Enlaces a añadir (simulación pura: no escribe nada)</h3>
        {rows.map((r, i) => (
          <div key={i} className="row" style={{ gap: 6, marginBottom: 6 }}>
            <input type="url" placeholder="URL origen" value={r.from}
              onChange={(e) => setRow(i, "from", e.target.value)} />
            <span>→</span>
            <input type="url" placeholder="URL destino" value={r.to}
              onChange={(e) => setRow(i, "to", e.target.value)} />
            <select style={{ width: 110 }} value={r.position}
              onChange={(e) => setRow(i, "position", e.target.value)}>
              {["content", "nav", "header", "footer", "sidebar"].map((p) =>
                <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        ))}
        <div className="row" style={{ gap: 8 }}>
          <button className="secondary" onClick={() => setRows([...rows, { from: "", to: "", position: "content" }])}>
            + otra fila
          </button>
          <button disabled={busy} onClick={run}>{busy ? "Recalculando grafo…" : "Simular impacto"}</button>
        </div>
        {error && <div className="alert" style={{ marginTop: 8 }}>{error}</div>}
      </div>

      <div>
        {!result && <Blocked title="Impacto calculado antes de implementar"
          reason="Introduce enlaces hipotéticos y recalcula el PageRank v2 del grafo completo en memoria." />}
        {result && (
          <div className="card">
            <h3>{fmt(result.pages_affected)} páginas afectadas · {result.mutations.added} enlaces simulados</h3>
            <table className="data">
              <thead><tr><th>URL</th><th className="num">Antes</th><th className="num">Después</th><th className="num">Δ</th></tr></thead>
              <tbody>
                {result.top_deltas.map((d, i) => (
                  <tr key={i}>
                    <td className="cell-url" title={d.url}>{d.url}</td>
                    <td className="num">{d.pagerank_before}</td>
                    <td className="num">{d.pagerank_after}</td>
                    <td className="num" style={{ color: d.delta > 0 ? "var(--chart-forest)" : "var(--chart-red)" }}>
                      {d.delta > 0 ? "+" : ""}{d.delta}
                    </td>
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

/* -- Profundidad de clic real (T23) ------------------------------------------ */
function DepthPanel({ jobId, segmentId }) {
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.urls(jobId, {
      segment_id: segmentId, page, page_size: 100,
      sort_by: "click_depth", sort_dir: "desc", is_internal: true,
      resource_type: "html",
    }),
    [jobId, segmentId, page],
  );
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  const hasDepth = d.items.some((u) => u.click_depth != null);

  if (!hasDepth) {
    return <Blocked title="Profundidad de clic real"
      reason="Se calcula con edge_classification=true en la config del job (BFS desde la home a través de redirects)." />;
  }

  return (
    <div className="card">
      <h3>Profundidad de clic real (≠ profundidad de descubrimiento) · más profundas primero</h3>
      <div className="table-wrap" style={{ maxHeight: "60vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL</th><th className="num">Clics desde home</th>
              <th className="num">Prof. descubrimiento</th>
              <th className="num">Inlinks contextuales</th><th className="num">Outlinks contextuales</th>
              <th className="num">PageRank</th></tr>
          </thead>
          <tbody>
            {d.items.map((u) => (
              <tr key={u.id}>
                <td className="cell-url" title={u.url}>{u.url}</td>
                <td className="num">{u.click_depth ?? <span className="tag">huérfana de enlazado</span>}</td>
                <td className="num">{u.crawl_depth ?? "—"}</td>
                <td className="num">{u.in_contextual ?? "—"}</td>
                <td className="num">{u.out_contextual ?? "—"}</td>
                <td className="num">{u.pagerank ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={d.pages} onPage={setPage} />
    </div>
  );
}
