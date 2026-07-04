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
          <p className="page-sub">Checks de juicio: solo un humano los acepta o rechaza. Autor y fecha quedan registrados.</p>
        </div>
        <span>
          <label className="kpi-label" style={{ marginRight: 6 }}>Firmas como</label>
          <input type="text" style={{ width: 160 }} placeholder="tu nombre"
            value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
        </span>
      </div>

      <SuggestionsQueue jobId={jobId} reviewer={reviewer} />
      <CannibalQueue jobId={jobId} reviewer={reviewer} />
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
              <th>Desde</th><th>Hacia (objetivo)</th>
              <th className="num">Similitud</th><th className="num">PR origen</th>
              <th className="num">Score</th><th>Decisión</th>
            </tr>
          </thead>
          <tbody>
            {d.items.map((s) => (
              <tr key={s.id}>
                <td className="cell-url" title={s.source_url}>{s.source_url}</td>
                <td className="cell-url" title={s.target_url}>{s.target_url}</td>
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
