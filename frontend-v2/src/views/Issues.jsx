import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { issueInfo } from "../issueCatalog.js";
import { Blocked, EmptyClean, ErrorBox, Pager, Severity, Spinner, fmt } from "../ui.jsx";

/** Agrupación por capas: cada tipo tiene su descripción en issueCatalog. */
const LAYERS = {
  "Técnico": [
    "status_4xx", "status_5xx", "redirect_chain", "meta_refresh_redirect",
    "js_redirect", "canonical_chain", "canonical_loop", "slow_page",
    "soft_404", "http_url", "mixed_content", "missing_hsts", "missing_csp",
    "in_sitemap_not_crawled", "crawled_not_in_sitemap", "orphan_not_in_crawl",
    "watchlist_check_failed", "crawl_trap_detected", "stale_lastmod",
  ],
  "On-page": [
    "missing_title", "title_too_short", "title_too_long", "duplicate_title",
    "missing_description", "description_too_short", "description_too_long",
    "duplicate_description", "missing_h1", "multiple_h1", "image_missing_alt",
    "low_word_count", "low_text_ratio", "duplicate_content",
    "near_duplicate_content", "low_unique_content",
  ],
  "Enlazado y arquitectura": [
    "orphan_page", "link_orphan", "excessive_click_depth",
    "no_contextual_inlinks", "authority_sink", "deep_pagination",
    "hierarchy_imbalance", "high_outlink_count", "equity_leak",
  ],
  "URLs": [
    "url_too_long", "url_non_ascii", "url_uppercase", "url_underscores",
    "url_multiple_slashes", "url_has_parameters", "url_non_seo_friendly",
    "url_cms_faceted",
  ],
  "Semántica y cobertura (se firman a mano)": [
    "semantic_cannibalization", "passage_gap", "buried_passage",
    "orphan_chunk", "generic_anchor", "anchor_target_mismatch",
  ],
};

export default function IssuesView() {
  const { jobId, segmentId } = useCtx();
  const [selected, setSelected] = useState(null); // issue_type
  const [page, setPage] = useState(1);

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const statsQ = useAsync(() => api.stats(jobId, { segment_id: segmentId }), [jobId, segmentId]);
  const detailQ = useAsync(
    () => (selected
      ? api.issues(jobId, { issue_type: selected, page, page_size: 50, segment_id: segmentId })
      : Promise.resolve(null)),
    [jobId, selected, page, segmentId],
  );

  if (statsQ.loading) return <Spinner />;
  if (statsQ.error) return <ErrorBox error={statsQ.error} />;

  const counts = {};
  for (const i of statsQ.data.issues_by_type) {
    counts[i.issue_type] = { count: i.count, severity: i.severity };
  }
  const known = new Set(Object.values(LAYERS).flat());
  const others = Object.keys(counts).filter((t) => !known.has(t));
  const total = Object.values(counts).reduce((a, c) => a + c.count, 0);

  return (
    <div>
      <h1 className="page-title">Incidencias</h1>
      <p className="page-sub">
        Todos los problemas que el análisis encontró en este rastreo, agrupados por capa.
        Haz clic en un tipo para ver su explicación y las URLs afectadas.
        {" "}<b className="num">{fmt(total)}</b> incidencias {segmentId ? "en el segmento seleccionado" : "en el run"}.
      </p>

      {total === 0 && <EmptyClean>Cero incidencias en este corte — limpio de verdad, no sin datos.</EmptyClean>}

      <div className="grid" style={{ gridTemplateColumns: "300px 1fr", alignItems: "start" }}>
        <div>
          {Object.entries(LAYERS).map(([layer, types]) => {
            const present = types.filter((t) => counts[t]);
            if (!present.length) return null;
            return (
              <div className="card" style={{ marginBottom: 10, padding: "10px 12px" }} key={layer}>
                <h3>{layer}</h3>
                {present.map((t) => (
                  <IssueRow key={t} type={t} meta={counts[t]}
                    active={selected === t}
                    onClick={() => { setSelected(selected === t ? null : t); setPage(1); }} />
                ))}
              </div>
            );
          })}
          {others.length > 0 && (
            <div className="card" style={{ padding: "10px 12px" }}>
              <h3>Otros</h3>
              {others.map((t) => (
                <IssueRow key={t} type={t} meta={counts[t]}
                  active={selected === t}
                  onClick={() => { setSelected(selected === t ? null : t); setPage(1); }} />
              ))}
            </div>
          )}
        </div>

        <div>
          {!selected && <Blocked title="Detalle" reason="Selecciona un tipo de incidencia a la izquierda: verás qué significa y las URLs afectadas." />}
          {selected && (
            <div className="card muted" style={{ marginBottom: 10 }}>
              <b className="mono">{selected}</b>
              <div style={{ marginTop: 4 }}>{issueInfo(selected)}</div>
            </div>
          )}
          {selected && detailQ.loading && <Spinner />}
          {selected && detailQ.data && (
            <>
              <div className="table-wrap" style={{ maxHeight: "62vh" }}>
                <table className="data">
                  <thead><tr><th>URL</th><th>Severidad</th><th>Detalles</th></tr></thead>
                  <tbody>
                    {detailQ.data.items.map((i) => (
                      <tr key={i.id}>
                        <td className="cell-url" title={i.url}>{i.url}</td>
                        <td><Severity level={i.severity} /></td>
                        <td className="mono" style={{ maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis" }}>
                          {i.details ? JSON.stringify(i.details) : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager page={page} pages={detailQ.data.pages} onPage={setPage} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function IssueRow({ type, meta, active, onClick }) {
  return (
    <div className="row between" onClick={onClick} title={issueInfo(type)}
      style={{ cursor: "pointer", padding: "3px 4px",
               background: active ? "var(--surface-soft)" : "transparent" }}>
      <span className="sev-row"><Severity level={meta.severity} /> <span className="mono">{type}</span></span>
      <span className="num">{fmt(meta.count)}</span>
    </div>
  );
}
