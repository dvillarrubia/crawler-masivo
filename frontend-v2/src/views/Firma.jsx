import { useMemo, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync, useStored } from "../hooks.js";
import { detailsToText, issueInfo, issueLabel } from "../issueCatalog.js";
import { Blocked, EmptyClean, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

/** Zona de trabajo de acciones propuestas: todo lo que la máquina propone
 *  pero no decide (enlaces internos, canibalización, cobertura, anclas,
 *  entidades), con filtros y acciones por lotes. Regla dura T10: nada se
 *  aplica solo — lo firma o rechaza una persona, con autor y fecha. */
const FAMILIES = [
  ["", "Todas"],
  ["enlace", "Enlaces internos"],
  ["canibalizacion", "Canibalización"],
  ["cobertura", "Cobertura"],
  ["anclas", "Anclas"],
  ["entidades", "Entidades"],
];

const STATE_TAG = {
  pendiente: null,
  aceptada: "s2xx",
  rechazada: "s4xx",
};

export default function FirmaView() {
  const { jobId } = useCtx();
  const [reviewer, setReviewer] = useStored("firma.reviewer", "");
  const [family, setFamily] = useState("");
  const [state, setState] = useState("pendiente");
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  // Filtros SEO de enlazado: origen y destino por separado
  const [fromTo, setFromTo] = useState({ from: "", to: "" });
  const [fromToApplied, setFromToApplied] = useState({ from: "", to: "" });
  const [linkMode, setLinkMode] = useState("sugerencia");  // sugerencia | destino
  const [order, setOrder] = useState("prioridad");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState({});   // key -> item
  const [busy, setBusy] = useState(false);

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const q = useAsync(
    () => api.proposals(jobId, {
      kind: family || undefined,
      state: state || undefined,
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

  const toggle = (it) => {
    const k = keyOf(it);
    setSelected((s) => {
      const n = { ...s };
      if (n[k]) delete n[k]; else n[k] = it;
      return n;
    });
  };
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
      setSelected({});
      q.reload();
    } catch (e) { alert(e.message); }
    setBusy(false);
  };

  const runSearch = () => { setApplied(search); setPage(1); };

  return (
    <div>
      <div className="row between">
        <div>
          <h1 className="page-title">Acciones propuestas</h1>
          <p className="page-sub">
            Tu bandeja de trabajo: todo lo que el análisis PROPONE pero no decide.
            Filtra, selecciona y firma o rechaza — en bloque si quieres. Nada se aplica
            solo; cada decisión queda con tu nombre y la fecha.
          </p>
        </div>
        <span>
          <label className="kpi-label" style={{ marginRight: 6 }}>Trabajas como</label>
          <input type="text" style={{ width: 160 }} placeholder="tu nombre"
            value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
        </span>
      </div>

      {/* Pestañas por familia con contador de pendientes */}
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

      {/* Filtros de estado / búsqueda / orden */}
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

      {/* Filtros específicos de enlazado interno — cómo piensa un SEO */}
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
              onKeyDown={(e) => e.key === "Enter" && (setFromToApplied({ ...fromTo }), setPage(1))} />
          </span>
          <span className="row" style={{ gap: 4 }}>
            <label className="kpi-label">hacia</label>
            <input type="text" style={{ width: 170 }} placeholder="/servicios (la que quieres potenciar)"
              value={fromTo.to} onChange={(e) => setFromTo({ ...fromTo, to: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && (setFromToApplied({ ...fromTo }), setPage(1))} />
          </span>
          <button className="secondary" onClick={() => { setFromToApplied({ ...fromTo }); setPage(1); }}>Aplicar</button>
          {(fromToApplied.from || fromToApplied.to) &&
            <button className="secondary" onClick={() => { setFromTo({ from: "", to: "" }); setFromToApplied({ from: "", to: "" }); }}>× enlazado</button>}
        </div>
      )}

      {family === "enlace" && linkMode === "destino" && (
        <LinkTargetsPanel jobId={jobId} filters={fromToApplied} reviewer={reviewer} />
      )}

      {/* Barra de acciones por lote (aparece con selección) */}
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

      {/* La vista "URLs a potenciar" reemplaza la tabla normal */}
      {family === "enlace" && linkMode === "destino"
        ? null
        : <ProposalsTable
            q={q} d={d} items={items} selected={selected} toggle={toggle}
            allVisibleSelected={allVisibleSelected} toggleAll={toggleAll}
            decide={decide} page={page} setPage={setPage} />}
    </div>
  );
}

/* -- Tabla de propuestas (bandeja principal) -------------------------------- */
function ProposalsTable({ q, d, items, selected, toggle, allVisibleSelected,
                          toggleAll, decide, page, setPage }) {
  const keyOf = (it) => `${it.kind_row}:${it.id}`;
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
          {items.length === 0 && (
            <EmptyClean>Nada que revisar con estos filtros.</EmptyClean>
          )}
          {items.length > 0 && (
            <div className="table-wrap" style={{ maxHeight: "60vh" }}>
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 28 }}>
                      <input type="checkbox" checked={allVisibleSelected} onChange={toggleAll} />
                    </th>
                    <th>Propuesta</th><th>URL</th><th>Detalle</th>
                    <th className="num">Prioridad</th><th>Estado</th><th>Decisión</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => {
                    const k = keyOf(it);
                    const label = it.kind_row === "issue" ? issueLabel(it.issue_type) : it.titulo;
                    const detalle = it.kind_row === "issue"
                      ? (detailsToText(it.issue_type, it.detalle) || issueInfo(it.issue_type))
                      : it.detalle;
                    return (
                      <tr key={k} data-selected={!!selected[k]}
                        style={selected[k] ? { background: "var(--surface-soft)" } : undefined}>
                        <td><input type="checkbox" checked={!!selected[k]} onChange={() => toggle(it)} /></td>
                        <td><b style={{ fontSize: 12.5 }}
                          title={it.kind_row === "issue" ? issueInfo(it.issue_type) : ""}>{label}</b></td>
                        <td className="cell-url" title={it.url}>{it.url || "—"}</td>
                        <td style={{ maxWidth: 340, whiteSpace: "normal", fontSize: 12, lineHeight: 1.4 }}>{detalle}</td>
                        <td className="num">{it.prioridad ? fmt(Math.round(it.prioridad)) : "—"}</td>
                        <td>
                          {it.estado === "pendiente"
                            ? <span className="proxy-tag">pendiente</span>
                            : <span className={`pill ${STATE_TAG[it.estado] || "sother"}`}>
                                {it.estado}{it.decided_by ? ` · ${it.decided_by}` : ""}</span>}
                        </td>
                        <td>
                          {it.estado === "pendiente" ? (
                            <span className="row" style={{ gap: 4 }}>
                              <button onClick={() => decide([it], "aceptar")}>Firmar</button>
                              <button className="secondary" onClick={() => decide([it], "rechazar")}>Rechazar</button>
                            </span>
                          ) : (
                            <button className="secondary" title="Volver a pendiente no está: re-decide en bloque"
                              onClick={() => decide([it], it.estado === "aceptada" ? "rechazar" : "aceptar")}>
                              cambiar
                            </button>
                          )}
                        </td>
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

/* -- "URLs a potenciar": sugerencias de enlace agrupadas por destino -------- */
function LinkTargetsPanel({ jobId, filters, reviewer }) {
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
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

  const firmarDestino = async (t) => {
    if (!reviewer) { alert("Pon tu nombre arriba para firmar."); return; }
    setBusy(true);
    try {
      await api.bulkDecision(jobId, {
        decision: "aceptar", decided_by: reviewer,
        items: t.suggestion_ids.map((id) => ({ kind_row: "suggestion", id })),
      });
      q.reload();
    } catch (e) { alert(e.message); }
    setBusy(false);
  };

  return (
    <div>
      <p className="proxy-tag" style={{ margin: "4px 0" }}>
        Cada fila es una página que quieres reforzar, con todos los enlaces internos que se le
        propondrían. Las que más se reforzarían van primero; el PageRank actual te dice dónde hay
        más margen. «Firmar todos» acepta de golpe los enlaces entrantes de esa URL.
      </p>
      <div className="table-wrap" style={{ maxHeight: "58vh" }}>
        <table className="data">
          <thead>
            <tr><th>URL a potenciar</th><th className="num">Enlaces propuestos</th>
              <th className="num">PageRank actual</th><th>Anchors propuestos</th>
              <th>Vendrían de</th><th></th></tr>
          </thead>
          <tbody>
            {d.items.map((t, i) => (
              <tr key={i}>
                <td className="cell-url" title={t.target_url}><b>{t.target_url}</b></td>
                <td className="num"><span className="tag num">{t.n_sugerencias}</span></td>
                <td className="num">{t.pagerank_actual != null ? t.pagerank_actual.toFixed(2) : "—"}</td>
                <td style={{ maxWidth: 220, fontSize: 12 }}>
                  {(t.anchors_propuestos || []).map((a) => `«${a}»`).join(", ") || "—"}</td>
                <td className="cell-url" style={{ fontSize: 11.5 }} title={(t.origenes || []).join("\n")}>
                  {(t.origenes || []).slice(0, 2).join(" · ")}{t.origenes.length > 2 ? ` +${t.origenes.length - 2}` : ""}</td>
                <td>
                  <button disabled={busy} onClick={() => firmarDestino(t)}
                    title="Firmar todos los enlaces entrantes propuestos de esta URL">
                    Firmar todos ({t.n_sugerencias})
                  </button>
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
