import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync, useStored } from "../hooks.js";
import { Blocked, EmptyClean, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

/** Cola de firma (T10): sugerencias de enlazado + canibalización firmable.
 *  Regla dura: nada auto-acepta; cada decisión lleva autor y fecha. */
export default function FirmaView() {
  const { jobId } = useCtx();
  const [reviewer, setReviewer] = useStored("firma.reviewer", "");

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  return (
    <div>
      <div className="row between">
        <div>
          <h1 className="page-title">Cola de firma</h1>
          <p className="page-sub">
            Aquí llega todo lo que la máquina PROPONE pero no decide: sugerencias de enlaces internos,
            canibalizaciones, huecos de contenido y anchors malos. Nada se aplica solo — una persona
            lo firma (acepta) o lo rechaza, y la decisión queda registrada con autor y fecha.
          </p>
        </div>
        <span>
          <label className="kpi-label" style={{ marginRight: 6 }}>Firmas como</label>
          <input type="text" style={{ width: 160 }} placeholder="tu nombre"
            value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
        </span>
      </div>

      <SuggestionsQueue jobId={jobId} reviewer={reviewer} />
      <CannibalQueue jobId={jobId} reviewer={reviewer} />
      <CoverageQueue jobId={jobId} reviewer={reviewer} />
      <AnchorQueue jobId={jobId} reviewer={reviewer} />
      <EntityQueue jobId={jobId} reviewer={reviewer} />
    </div>
  );
}

/* Entidades (GLiNER2): canibalización por entidad y funnel roto. */
const ENTITY_LABEL = {
  entity_cannibalization: "canibalización por entidad",
  funnel_mismatch: "fase de funnel equivocada",
};

function EntityQueue({ jobId, reviewer }) {
  const [kind, setKind] = useState("entity_cannibalization");
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.issues(jobId, { issue_type: kind, page, page_size: 50 }),
    [jobId, kind, page],
  );

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;

  const review = async (iid, decision) => {
    if (!reviewer) { alert("Pon tu nombre arriba para firmar."); return; }
    await api.reviewIssue(iid, { review_status: decision, reviewed_by: reviewer });
    q.reload();
  };

  const detail = (i) =>
    kind === "entity_cannibalization"
      ? `«${i.details?.entity}» · ${i.details?.funnel} · domina ${i.details?.dominant_url} · sugerencia: ${i.details?.accion}${i.details?.converge_embeddings ? " · converge con similitud de contenido" : ""}`
      : `página ${i.details?.page_funnel} capturando ${i.details?.n_queries} búsquedas ${i.details?.query_funnel} (${fmt(i.details?.impressions)} imprs)`;

  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="row between">
        <h3>Entidades ({fmt(d.total)})</h3>
        <span className="row" style={{ gap: 6 }}>
          {Object.entries(ENTITY_LABEL).map(([k, label]) => (
            <button key={k} className={kind === k ? "" : "secondary"}
              onClick={() => { setKind(k); setPage(1); }}>{label}</button>
          ))}
        </span>
      </div>
      {d.items.length === 0 && <EmptyClean>Sin propuestas de este tipo. Se generan al ejecutar el pipeline de entidades (GLiNER2) sobre el run.</EmptyClean>}
      <div className="table-wrap" style={{ maxHeight: "40vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL accionable</th><th>Detalle</th><th>Firma</th></tr>
          </thead>
          <tbody>
            {d.items.map((i) => (
              <tr key={i.id}>
                <td className="cell-url" title={i.url}>{i.url}</td>
                <td>{detail(i)}</td>
                <td>
                  {i.review_status === "pending" ? (
                    <span className="row" style={{ gap: 4 }}>
                      <button onClick={() => review(i.id, "signed")}>Firmar</button>
                      <button className="secondary" onClick={() => review(i.id, "rejected")}>Rechazar</button>
                    </span>
                  ) : (
                    <span className="proxy-tag">{i.review_status} · {i.reviewed_by}</span>
                  )}
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

function SuggestionsQueue({ jobId, reviewer }) {
  const [status, setStatus] = useState("pending");
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.linkSuggestions(jobId, { status, page, page_size: 50 }),
    [jobId, status, page],
  );

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;

  if (d.status === "blocked") {
    return (
      <Blocked title="Sugerencias de enlazado"
        reason="Necesitan el análisis semántico del run (vectores de página)."
        cta={<a href="#/semantica"><button>Ir a Semántica</button></a>} />
    );
  }

  const decide = async (sid, decision) => {
    if (!reviewer) { alert("Pon tu nombre arriba para firmar."); return; }
    await api.decideSuggestion(sid, { status: decision, decided_by: reviewer });
    q.reload();
  };

  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="row between">
        <h3>Sugerencias de enlazado interno ({fmt(d.total)})</h3>
        <span className="row" style={{ gap: 6 }}>
          {["pending", "accepted", "rejected"].map((s) => (
            <button key={s} className={status === s ? "" : "secondary"}
              onClick={() => { setStatus(s); setPage(1); }}>{s}</button>
          ))}
        </span>
      </div>
      {d.items.length === 0 && <EmptyClean>Nada {status} en la cola.</EmptyClean>}
      <div className="table-wrap" style={{ maxHeight: "40vh" }}>
        <table className="data">
          <thead>
            <tr>
              <th>Desde</th><th>Hacia (objetivo)</th><th>Anchor propuesto</th>
              <th className="num">Similitud</th><th className="num">PR origen</th>
              <th className="num">Score</th><th>Decisión</th>
            </tr>
          </thead>
          <tbody>
            {d.items.map((s) => (
              <tr key={s.id}>
                <td className="cell-url" title={s.source_url}>{s.source_url}</td>
                <td className="cell-url" title={s.target_url}>{s.target_url}</td>
                <td title={s.proposed_anchor || ""}>{s.proposed_anchor ? `«${s.proposed_anchor}»` : "—"}</td>
                <td className="num">{s.cosine_similarity?.toFixed(4)}</td>
                <td className="num">{s.source_pagerank ?? "—"}</td>
                <td className="num">{s.score?.toFixed(4)}</td>
                <td>
                  {s.status === "pending" ? (
                    <span className="row" style={{ gap: 4 }}>
                      <button onClick={() => decide(s.id, "accepted")}>Aceptar</button>
                      <button className="secondary" onClick={() => decide(s.id, "rejected")}>Rechazar</button>
                    </span>
                  ) : (
                    <span className="proxy-tag">{s.status} · {s.decided_by} · {s.decided_at ? new Date(s.decided_at).toLocaleDateString("es") : ""}</span>
                  )}
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

/* Cobertura consulta→pasaje (T19): gap / enterrado / chunk huérfano. */
const T19_LABEL = {
  passage_gap: "gap de pasaje",
  buried_passage: "pasaje enterrado",
  orphan_chunk: "chunks huérfanos",
};

function CoverageQueue({ jobId, reviewer }) {
  const [kind, setKind] = useState("passage_gap");
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.issues(jobId, { issue_type: kind, page, page_size: 50 }),
    [jobId, kind, page],
  );

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;

  const review = async (iid, decision) => {
    if (!reviewer) { alert("Pon tu nombre arriba para firmar."); return; }
    await api.reviewIssue(iid, { review_status: decision, reviewed_by: reviewer });
    q.reload();
  };

  const detail = (i) => {
    if (kind === "passage_gap")
      return `«${i.details?.query}» · ${fmt(i.details?.impressions)} imprs · mejor sim ${i.details?.best_similarity?.toFixed(3)}`;
    if (kind === "buried_passage")
      return `«${i.details?.query}» · sim ${i.details?.similarity?.toFixed(3)} · chunk #${i.details?.chunk_position}`;
    return `${fmt(i.details?.orphan_chunks)} de ${fmt(i.details?.total_chunks)} chunks sin demanda${i.details?.approximate ? " (aprox.)" : ""}`;
  };

  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="row between">
        <h3>Cobertura consulta→pasaje ({fmt(d.total)})</h3>
        <span className="row" style={{ gap: 6 }}>
          {Object.entries(T19_LABEL).map(([k, label]) => (
            <button key={k} className={kind === k ? "" : "secondary"}
              onClick={() => { setKind(k); setPage(1); }}>{label}</button>
          ))}
        </span>
      </div>
      {d.items.length === 0 && <EmptyClean>Sin issues de este tipo. Se generan al calcular la cobertura en Semántica → Consultas→Pasajes.</EmptyClean>}
      <div className="table-wrap" style={{ maxHeight: "40vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL accionable</th><th>Detalle</th><th>Firma</th></tr>
          </thead>
          <tbody>
            {d.items.map((i) => (
              <tr key={i.id}>
                <td className="cell-url" title={i.url}>{i.url}</td>
                <td>{detail(i)}</td>
                <td>
                  {i.review_status === "pending" ? (
                    <span className="row" style={{ gap: 4 }}>
                      <button onClick={() => review(i.id, "signed")}>Firmar</button>
                      <button className="secondary" onClick={() => review(i.id, "rejected")}>Rechazar</button>
                    </span>
                  ) : (
                    <span className="proxy-tag">{i.review_status} · {i.reviewed_by}</span>
                  )}
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

/* Relevancia de anchors (T18): genéricos y anchor↔destino sin relación. */
const T18_LABEL = {
  anchor_target_mismatch: "anchor sin relación",
  generic_anchor: "anchors genéricos",
};

function AnchorQueue({ jobId, reviewer }) {
  const [kind, setKind] = useState("anchor_target_mismatch");
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.issues(jobId, { issue_type: kind, page, page_size: 50 }),
    [jobId, kind, page],
  );

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;

  const review = async (iid, decision) => {
    if (!reviewer) { alert("Pon tu nombre arriba para firmar."); return; }
    await api.reviewIssue(iid, { review_status: decision, reviewed_by: reviewer });
    q.reload();
  };

  const detail = (i) =>
    kind === "anchor_target_mismatch"
      ? `«${i.details?.anchor}» · sim ${i.details?.similarity?.toFixed(3)} · ${fmt(i.details?.n_links)} enlaces`
      : `${fmt(i.details?.generic_inlinks)} inlinks genéricos: ${(i.details?.anchors || []).slice(0, 4).map((a) => `«${a}»`).join(", ")}`;

  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div className="row between">
        <h3>Relevancia de anchors ({fmt(d.total)})</h3>
        <span className="row" style={{ gap: 6 }}>
          {Object.entries(T18_LABEL).map(([k, label]) => (
            <button key={k} className={kind === k ? "" : "secondary"}
              onClick={() => { setKind(k); setPage(1); }}>{label}</button>
          ))}
        </span>
      </div>
      {d.items.length === 0 && <EmptyClean>Sin issues de este tipo. Se generan en Semántica → Anclas.</EmptyClean>}
      <div className="table-wrap" style={{ maxHeight: "40vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL destino</th><th>Detalle</th><th>Firma</th></tr>
          </thead>
          <tbody>
            {d.items.map((i) => (
              <tr key={i.id}>
                <td className="cell-url" title={i.url}>{i.url}</td>
                <td>{detail(i)}</td>
                <td>
                  {i.review_status === "pending" ? (
                    <span className="row" style={{ gap: 4 }}>
                      <button onClick={() => review(i.id, "signed")}>Firmar</button>
                      <button className="secondary" onClick={() => review(i.id, "rejected")}>Rechazar</button>
                    </span>
                  ) : (
                    <span className="proxy-tag">{i.review_status} · {i.reviewed_by}</span>
                  )}
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

function CannibalQueue({ jobId, reviewer }) {
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.issues(jobId, { issue_type: "semantic_cannibalization", page, page_size: 50 }),
    [jobId, page],
  );

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;

  const review = async (iid, decision) => {
    if (!reviewer) { alert("Pon tu nombre arriba para firmar."); return; }
    await api.reviewIssue(iid, { review_status: decision, reviewed_by: reviewer });
    q.reload();
  };

  return (
    <div className="card">
      <h3>Canibalización semántica firmable ({fmt(d.total)})</h3>
      {d.items.length === 0 && <EmptyClean>Sin pares de canibalización pendientes de firma.</EmptyClean>}
      <div className="table-wrap" style={{ maxHeight: "40vh" }}>
        <table className="data">
          <thead>
            <tr><th>Dominante</th><th>Débil (accionable)</th><th className="num">Similitud</th><th>Firma</th></tr>
          </thead>
          <tbody>
            {d.items.map((i) => (
              <tr key={i.id}>
                <td className="cell-url">{i.details?.dominant_url}</td>
                <td className="cell-url">{i.details?.weak_url || i.url}</td>
                <td className="num">{i.details?.cosine_similarity?.toFixed(4)}</td>
                <td>
                  {i.review_status === "pending" ? (
                    <span className="row" style={{ gap: 4 }}>
                      <button onClick={() => review(i.id, "signed")}>Firmar</button>
                      <button className="secondary" onClick={() => review(i.id, "rejected")}>Rechazar</button>
                    </span>
                  ) : (
                    <span className="proxy-tag">{i.review_status} · {i.reviewed_by}</span>
                  )}
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
