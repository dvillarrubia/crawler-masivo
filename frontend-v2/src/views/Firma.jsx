import { useMemo, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync, useStored } from "../hooks.js";
import { detailPairs, detailsToText, issueInfo, issueLabel } from "../issueCatalog.js";
import { Blocked, Drawer, EmptyClean, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

/** Zona de trabajo de acciones propuestas: todo lo que la máquina propone
 *  pero no decide. Tabla compacta para escanear + panel de detalle para
 *  decidir con todo el contexto. Regla dura T10: nada se aplica solo. */
const FAMILIES = [
  ["", "Todas"],
  ["enlace", "Enlaces internos"],
  ["canibalizacion", "Canibalización"],
  ["cobertura", "Cobertura"],
  ["anclas", "Anclas"],
  ["entidades", "Entidades"],
];

const STATE_TAG = { pendiente: null, aceptada: "s2xx", rechazada: "s4xx" };

export default function FirmaView() {
  const { jobId } = useCtx();
  const [reviewer, setReviewer] = useStored("firma.reviewer", "");
  const [family, setFamily] = useState("");
  const [state, setState] = useState("pendiente");
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const [fromTo, setFromTo] = useState({ from: "", to: "" });
  const [fromToApplied, setFromToApplied] = useState({ from: "", to: "" });
  const [linkMode, setLinkMode] = useState("sugerencia");
  const [order, setOrder] = useState("prioridad");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState({});
  const [detail, setDetail] = useState(null);        // propuesta abierta
  const [targetDetail, setTargetDetail] = useState(null);  // destino abierto
  const [busy, setBusy] = useState(false);

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const q = useAsync(
    () => api.proposals(jobId, {
      kind: family || undefined, state: state || undefined,
      search: applied || undefined,
      to_contains: fromToApplied.to || undefined,
      from_contains: fromToApplied.from || undefined,
      order, page, page_size: 100,
    }),
    [jobId, family, state, applied, fromToApplied, order, page],
  );

  const d = q.data;
  const items = d && d.status === "ok" ? d.items : [];
  const counts = (d && d.counts) || {};
  const keyOf = (it) => `${it.kind_row}:${it.id}`;
  const selList = useMemo(() => Object.values(selected), [selected]);

  const toggle = (it) => setSelected((s) => {
    const k = keyOf(it); const n = { ...s };
    if (n[k]) delete n[k]; else n[k] = it;
    return n;
  });
  const allVisibleSelected = items.length > 0 && items.every((it) => selected[keyOf(it)]);
  const toggleAll = () => {
    if (allVisibleSelected) { setSelected({}); return; }
    const n = { ...selected };
    for (const it of items) n[keyOf(it)] = it;
    setSelected(n);
  };

  const decide = async (list, decision) => {
    if (!reviewer) { alert("Pon tu nombre arriba para poder decidir."); return; }
    if (!list.length) return;
    setBusy(true);
    try {
      await api.bulkDecision(jobId, {
        decision, decided_by: reviewer,
        items: list.map((it) => ({ kind_row: it.kind_row, id: it.id })),
      });
      setSelected({}); setDetail(null); setTargetDetail(null);
      q.reload();
    } catch (e) { alert(e.message); }
    setBusy(false);
  };

  const runSearch = () => { setApplied(search); setPage(1); };
  const applyFromTo = () => { setFromToApplied({ ...fromTo }); setPage(1); };

  return (
    <div>
      <div className="row between">
        <div>
          <h1 className="page-title">Acciones propuestas</h1>
          <p className="page-sub">
            Tu bandeja de trabajo: todo lo que el análisis PROPONE pero no decide.
            Filtra, abre una propuesta para verla entera y firma o rechaza — suelta o en bloque.
            Nada se aplica solo; cada decisión queda con tu nombre y la fecha.
          </p>
        </div>
        <span>
          <label className="kpi-label" style={{ marginRight: 6 }}>Trabajas como</label>
          <input type="text" style={{ width: 160 }} placeholder="tu nombre"
            value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
        </span>
      </div>

      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        {FAMILIES.map(([k, label]) => {
          const c = k ? counts[k] : Object.values(counts).reduce(
            (a, x) => ({ pendiente: a.pendiente + x.pendiente, total: a.total + x.total }),
            { pendiente: 0, total: 0 });
          const pend = c ? c.pendiente : 0;
          return (
            <button key={k} className={family === k ? "" : "secondary"}
              onClick={() => { setFamily(k); setPage(1); setSelected({}); }}>
              {label}{pend ? <span className="tag num" style={{ marginLeft: 5 }}>{pend}</span> : null}
            </button>
          );
        })}
      </div>

      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        <select value={state} onChange={(e) => { setState(e.target.value); setPage(1); setSelected({}); }}>
          <option value="pendiente">Pendientes</option>
          <option value="aceptada">Aceptadas</option>
          <option value="rechazada">Rechazadas</option>
          <option value="">Todas</option>
        </select>
        <input type="text" style={{ width: 240 }} placeholder="buscar en la URL…"
          value={search} onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runSearch()} />
        <button className="secondary" onClick={runSearch}>Buscar</button>
        {applied && <button className="secondary" onClick={() => { setSearch(""); setApplied(""); }}>× filtro</button>}
        <select value={order} onChange={(e) => setOrder(e.target.value)}>
          <option value="prioridad">Orden: prioridad</option>
          <option value="estado">Orden: estado</option>
        </select>
      </div>

      {family === "enlace" && (
        <div className="toolbar" style={{ flexWrap: "wrap", background: "var(--surface-soft)", padding: "6px 8px", borderRadius: 6 }}>
          <span className="row" style={{ gap: 6 }}>
            <button className={linkMode === "sugerencia" ? "" : "secondary"} onClick={() => setLinkMode("sugerencia")}>Por sugerencia</button>
            <button className={linkMode === "destino" ? "" : "secondary"} onClick={() => setLinkMode("destino")}>URLs a potenciar</button>
          </span>
          <span className="row" style={{ gap: 4 }}>
            <label className="kpi-label">desde</label>
            <input type="text" style={{ width: 150 }} placeholder="/blog"
              value={fromTo.from} onChange={(e) => setFromTo({ ...fromTo, from: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && applyFromTo()} />
          </span>
          <span className="row" style={{ gap: 4 }}>
            <label className="kpi-label">hacia</label>
            <input type="text" style={{ width: 200 }} placeholder="/servicios (la que quieres potenciar)"
              value={fromTo.to} onChange={(e) => setFromTo({ ...fromTo, to: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && applyFromTo()} />
          </span>
          <button className="secondary" onClick={applyFromTo}>Aplicar</button>
          {(fromToApplied.from || fromToApplied.to) &&
            <button className="secondary" onClick={() => { setFromTo({ from: "", to: "" }); setFromToApplied({ from: "", to: "" }); }}>× enlazado</button>}
        </div>
      )}

      {selList.length > 0 && (
        <div className="card muted row between" style={{ padding: "8px 12px", marginBottom: 8 }}>
          <span><b className="num">{selList.length}</b> seleccionadas</span>
          <span className="row" style={{ gap: 8 }}>
            <button disabled={busy} onClick={() => decide(selList, "aceptar")}>Firmar seleccionadas</button>
            <button className="secondary" disabled={busy} onClick={() => decide(selList, "rechazar")}>Rechazar seleccionadas</button>
            <button className="secondary" onClick={() => setSelected({})}>Deseleccionar</button>
          </span>
        </div>
      )}

      {family === "enlace" && linkMode === "destino"
        ? <LinkTargetsPanel jobId={jobId} filters={fromToApplied} onOpen={setTargetDetail} />
        : <ProposalsTable q={q} d={d} items={items} keyOf={keyOf}
            selected={selected} toggle={toggle} allVisibleSelected={allVisibleSelected}
            toggleAll={toggleAll} onOpen={setDetail} page={page} setPage={setPage} />}

      {detail && (
        <ProposalDrawer item={detail} busy={busy} onClose={() => setDetail(null)}
          onDecide={(dec) => decide([detail], dec)} />
      )}
      {targetDetail && (
        <LinkTargetDrawer jobId={jobId} target={targetDetail} reviewer={reviewer}
          onClose={() => setTargetDetail(null)}
          onDone={() => { setTargetDetail(null); q.reload(); }} />
      )}
    </div>
  );
}

/* -- Tabla compacta: se escanea, se selecciona, se abre --------------------- */
function ProposalsTable({ q, d, items, keyOf, selected, toggle, allVisibleSelected,
                          toggleAll, onOpen, page, setPage }) {
  return (
    <>
      {q.loading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {d && d.status === "blocked" && (
        <Blocked title="Aún no hay propuestas"
          reason="Se generan al ejecutar el análisis semántico y el pipeline de entidades sobre este run."
          cta={<a href="#/semantica"><button>Ir a Semántica</button></a>} />
      )}
      {d && d.status === "ok" && (
        <>
          {items.length === 0 && <EmptyClean>Nada que revisar con estos filtros.</EmptyClean>}
          {items.length > 0 && (
            <div className="table-wrap" style={{ maxHeight: "62vh" }}>
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 28 }}>
                      <input type="checkbox" checked={allVisibleSelected} onChange={toggleAll} />
                    </th>
                    <th>Propuesta</th><th>URL</th>
                    <th className="num">Prioridad</th><th>Estado</th><th style={{ width: 24 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => {
                    const k = keyOf(it);
                    const label = it.kind_row === "issue" ? issueLabel(it.issue_type) : it.titulo;
                    return (
                      <tr key={k} style={{ cursor: "pointer", background: selected[k] ? "var(--surface-soft)" : undefined }}
                        onClick={() => onOpen(it)}>
                        <td onClick={(e) => e.stopPropagation()}>
                          <input type="checkbox" checked={!!selected[k]} onChange={() => toggle(it)} />
                        </td>
                        <td><b style={{ fontSize: 12.5 }}>{label}</b></td>
                        <td className="cell-url" title={it.url}>{it.url || "—"}</td>
                        <td className="num">{it.prioridad ? fmt(Math.round(it.prioridad)) : "—"}</td>
                        <td>
                          {it.estado === "pendiente"
                            ? <span className="proxy-tag">pendiente</span>
                            : <span className={`pill ${STATE_TAG[it.estado] || "sother"}`}>
                                {it.estado}{it.decided_by ? ` · ${it.decided_by}` : ""}</span>}
                        </td>
                        <td className="proxy-tag">›</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <Pager page={page} pages={d.pages} onPage={setPage} />
        </>
      )}
    </>
  );
}

/* -- Detalle de una propuesta: todo el contexto + decisión ------------------ */
function ProposalDrawer({ item, busy, onClose, onDecide }) {
  const isIssue = item.kind_row === "issue";
  const label = isIssue ? issueLabel(item.issue_type) : item.titulo;
  const resumen = isIssue
    ? (detailsToText(item.issue_type, item.detalle) || issueInfo(item.issue_type))
    : item.detalle;
  const pairs = isIssue ? detailPairs(item.detalle) : [];

  return (
    <Drawer onClose={onClose}>
      <h2 style={{ marginBottom: 4 }}>{label}</h2>
      <div className="row" style={{ gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        <span className="tag">{item.familia}</span>
        {item.estado === "pendiente"
          ? <span className="proxy-tag">pendiente de decisión</span>
          : <span className={`pill ${STATE_TAG[item.estado] || "sother"}`}>
              {item.estado}{item.decided_by ? ` · ${item.decided_by}` : ""}</span>}
        {item.prioridad ? <span className="tag num">prioridad {fmt(Math.round(item.prioridad))}</span> : null}
      </div>

      {isIssue && (
        <div className="card muted" style={{ marginBottom: 10 }}>
          {issueInfo(item.issue_type)}
        </div>
      )}

      {item.url && (
        <div style={{ marginBottom: 10 }}>
          <div className="kpi-label">URL afectada</div>
          <a href={item.url} target="_blank" rel="noreferrer" className="cell-url">{item.url}</a>
        </div>
      )}
      {!isIssue && item.source_url && (
        <div style={{ marginBottom: 10 }}>
          <div className="kpi-label">Enlace propuesto</div>
          <div className="cell-url" style={{ marginTop: 2 }}>
            <span className="proxy-tag">desde</span> {item.source_url}
          </div>
          <div className="cell-url">
            <span className="proxy-tag">hacia</span> {item.url}
          </div>
        </div>
      )}

      <div style={{ marginBottom: 12 }}>
        <div className="kpi-label">Qué propone</div>
        <div style={{ fontSize: 13.5, lineHeight: 1.5, marginTop: 3 }}>{resumen}</div>
      </div>

      {pairs.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <table className="data">
            <tbody>
              {pairs.map(([k, v], i) => (
                <tr key={i}>
                  <td style={{ color: "var(--ink-muted)", width: "40%" }}>{k}</td>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {item.estado === "pendiente" ? (
        <div className="row" style={{ gap: 8 }}>
          <button disabled={busy} onClick={() => onDecide("aceptar")}>Firmar</button>
          <button className="secondary" disabled={busy} onClick={() => onDecide("rechazar")}>Rechazar</button>
        </div>
      ) : (
        <button className="secondary" disabled={busy}
          onClick={() => onDecide(item.estado === "aceptada" ? "rechazar" : "aceptar")}>
          Cambiar decisión
        </button>
      )}
    </Drawer>
  );
}

/* -- "URLs a potenciar": tabla compacta de destinos ------------------------- */
function LinkTargetsPanel({ jobId, filters, onOpen }) {
  const [page, setPage] = useState(1);
  const q = useAsync(
    () => api.linkTargets(jobId, {
      from_contains: filters.from || undefined,
      to_contains: filters.to || undefined,
      page, page_size: 50,
    }),
    [jobId, filters, page],
  );
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  if (d.status === "blocked") {
    return <Blocked title="URLs a potenciar"
      reason="No hay sugerencias de enlace interno todavía: necesitan el análisis semántico del run." />;
  }
  return (
    <div>
      <p className="proxy-tag" style={{ margin: "4px 0" }}>
        Cada fila es una página que puedes reforzar. «Enlaces» = cuántos enlaces internos recibiría;
        «PageRank» actual te dice dónde hay más margen. Ábrela para ver los orígenes y firmar.
      </p>
      <div className="table-wrap" style={{ maxHeight: "60vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL a potenciar</th><th className="num">Enlaces</th>
              <th className="num">PageRank</th><th>Anchors propuestos</th><th style={{ width: 24 }}></th></tr>
          </thead>
          <tbody>
            {d.items.map((t, i) => (
              <tr key={i} style={{ cursor: "pointer" }} onClick={() => onOpen(t)}>
                <td className="cell-url" title={t.target_url}><b>{t.target_url}</b></td>
                <td className="num"><span className="tag num">{t.n_sugerencias}</span></td>
                <td className="num">{t.pagerank_actual != null ? t.pagerank_actual.toFixed(2) : "—"}</td>
                <td style={{ maxWidth: 260, fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {(t.anchors_propuestos || []).map((a) => `«${a}»`).join(", ") || "—"}</td>
                <td className="proxy-tag">›</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={d.pages} onPage={setPage} />
    </div>
  );
}

/* -- Detalle de una URL a potenciar: sus enlaces entrantes + firmar --------- */
function LinkTargetDrawer({ jobId, target, reviewer, onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const firmar = async () => {
    if (!reviewer) { alert("Pon tu nombre arriba para firmar."); return; }
    setBusy(true);
    try {
      await api.bulkDecision(jobId, {
        decision: "aceptar", decided_by: reviewer,
        items: target.suggestion_ids.map((id) => ({ kind_row: "suggestion", id })),
      });
      onDone();
    } catch (e) { alert(e.message); }
    setBusy(false);
  };
  return (
    <Drawer onClose={onClose}>
      <h2 style={{ marginBottom: 4 }}>Potenciar esta URL</h2>
      <a href={target.target_url} target="_blank" rel="noreferrer" className="cell-url">{target.target_url}</a>
      <div className="facts" style={{ marginTop: 12 }}>
        <div className="fact"><div className="k">Enlaces propuestos</div><div className="v num">{target.n_sugerencias}</div></div>
        <div className="fact"><div className="k">PageRank actual</div><div className="v num">{target.pagerank_actual != null ? target.pagerank_actual.toFixed(2) : "—"}</div></div>
        <div className="fact"><div className="k">Mejor score</div><div className="v num">{target.best_score}</div></div>
      </div>

      {target.anchors_propuestos && target.anchors_propuestos.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="kpi-label">Anchors propuestos</div>
          <div style={{ marginTop: 3 }}>{target.anchors_propuestos.map((a) => `«${a}»`).join(", ")}</div>
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <div className="kpi-label">Vendrían de ({target.origenes.length})</div>
        <div className="table-wrap" style={{ maxHeight: "40vh", marginTop: 4 }}>
          <table className="data">
            <tbody>
              {target.origenes.map((o, i) => (
                <tr key={i}><td className="cell-url" title={o}>{o}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <button disabled={busy} onClick={firmar}>
          Firmar los {target.n_sugerencias} enlaces entrantes
        </button>
      </div>
    </Drawer>
  );
}
