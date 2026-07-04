import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, Drawer, ErrorBox, Pager, Severity, Spinner, StatusPill, fmt } from "../ui.jsx";

const COLUMNS = [
  ["url", "URL"],
  ["status_code", "Status", "num"],
  ["resource_type", "Tipo"],
  ["indexable", "Indexable"],
  ["crawl_depth", "Prof.", "num"],
  ["inlinks_count", "Inlinks", "num"],
  ["outlinks_count", "Outlinks", "num"],
  ["pagerank", "PageRank", "num"],
  ["word_count", "Palabras", "num"],
  ["response_time_ms", "ms", "num"],
];

/** Explorador fusionado: sort + chips con conteos (Empresarial) sobre la
 *  API real con filtros de servidor (frontend actual). */
export default function ExplorerView() {
  const { jobId, segmentId } = useCtx();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ status_group: "", is_internal: "", resource_type: "", search: "" });
  const [sort, setSort] = useState({ by: null, dir: "asc" });
  const [detailId, setDetailId] = useState(null);

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const params = {
    page, page_size: 100,
    segment_id: segmentId,
    status_group: filters.status_group,
    resource_type: filters.resource_type,
    search: filters.search,
    is_internal: filters.is_internal,
    sort_by: sort.by, sort_dir: sort.dir,
  };
  const urlsQ = useAsync(() => api.urls(jobId, params),
    [jobId, segmentId, page, JSON.stringify(filters), sort.by, sort.dir]);
  const statsQ = useAsync(() => api.stats(jobId, { segment_id: segmentId }), [jobId, segmentId]);

  const setFilter = (k, v) => { setFilters({ ...filters, [k]: v }); setPage(1); };
  const toggleSort = (col) => setSort((s) =>
    s.by === col ? { by: col, dir: s.dir === "asc" ? "desc" : "asc" } : { by: col, dir: "asc" });

  const groups = statsQ.data
    ? Object.fromEntries(statsQ.data.urls_by_status_group.map((g) => [g.status_group, g.count]))
    : {};

  return (
    <div>
      <h1 className="page-title">Explorador</h1>
      <p className="page-sub">Todas las URLs del run, con filtros de servidor y orden por columna.</p>

      <div className="toolbar">
        {["2xx", "3xx", "4xx", "5xx", "not_crawled"].map((g) => (
          <button key={g}
            className={filters.status_group === g ? "" : "secondary"}
            onClick={() => setFilter("status_group", filters.status_group === g ? "" : g)}>
            {g} <span className="num">({fmt(groups[g] || 0)})</span>
          </button>
        ))}
        <select style={{ width: 130 }} value={filters.resource_type}
          onChange={(e) => setFilter("resource_type", e.target.value)}>
          <option value="">tipo: todos</option>
          {["html", "image", "css", "js", "pdf", "redirect", "other"].map((t) =>
            <option key={t} value={t}>{t}</option>)}
        </select>
        <select style={{ width: 140 }} value={filters.is_internal}
          onChange={(e) => setFilter("is_internal", e.target.value)}>
          <option value="">internas + externas</option>
          <option value="true">solo internas</option>
          <option value="false">solo externas</option>
        </select>
        <input type="text" style={{ width: 240 }} placeholder="buscar en URL o title…"
          value={filters.search}
          onChange={(e) => setFilter("search", e.target.value)} />
      </div>

      {urlsQ.error && <ErrorBox error={urlsQ.error} />}
      {urlsQ.loading ? <Spinner /> : (
        <>
          <div className="table-wrap" style={{ maxHeight: "62vh" }}>
            <table className="data">
              <thead>
                <tr>
                  {COLUMNS.map(([key, label, cls]) => (
                    <th key={key} className={cls || ""} onClick={() => toggleSort(key)}>
                      {label}{sort.by === key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {urlsQ.data.items.map((u) => (
                  <tr key={u.id} onClick={() => setDetailId(u.id)}>
                    <td className="cell-url" title={u.url}>{u.url}</td>
                    <td className="num"><StatusPill group={u.status_group} /> {u.status_code ?? ""}</td>
                    <td>{u.resource_type || "—"}</td>
                    <td>{u.indexable == null ? "—" : u.indexable ? "sí" : "no"}</td>
                    <td className="num">{u.crawl_depth ?? "—"}</td>
                    <td className="num">{fmt(u.inlinks_count)}</td>
                    <td className="num">{fmt(u.outlinks_count)}</td>
                    <td className="num">{u.pagerank ?? "—"}</td>
                    <td className="num">{fmt(u.word_count)}</td>
                    <td className="num">{u.response_time_ms ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row between">
            <Pager page={page} pages={urlsQ.data.pages} onPage={setPage} />
            <span className="proxy-tag num">{fmt(urlsQ.data.total)} URLs</span>
          </div>
        </>
      )}

      {detailId && <UrlDrawer jobId={jobId} urlId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  );
}

/** Ficha de URL (drawer del Empresarial): facts + on-page + inlinks + issues. */
function UrlDrawer({ jobId, urlId, onClose }) {
  const q = useAsync(() => api.urlDetail(jobId, urlId), [jobId, urlId]);

  if (q.loading) return <Drawer onClose={onClose}><Spinner /></Drawer>;
  if (q.error) return <Drawer onClose={onClose}><ErrorBox error={q.error} /></Drawer>;
  const u = q.data;
  const meta = u.html_meta;

  return (
    <Drawer onClose={onClose}>
      <h2>{u.url}</h2>
      <div className="row" style={{ margin: "8px 0" }}>
        <StatusPill group={u.status_group} />
        <span className="tag">{u.resource_type}</span>
        {u.indexable === false && <span className="tag">no indexable · {u.indexability_status || "?"}</span>}
        {u.in_sitemap != null && <span className="tag">{u.in_sitemap ? "en sitemap" : "fuera de sitemap"}</span>}
      </div>

      <div className="facts">
        <Fact k="Profundidad" v={u.crawl_depth} />
        <Fact k="PageRank" v={u.pagerank} />
        <Fact k="Inlinks" v={fmt(u.inlinks_count)} />
        <Fact k="Outlinks" v={fmt(u.outlinks_count)} />
        <Fact k="Palabras" v={fmt(u.word_count)} />
        <Fact k="Latencia" v={u.response_time_ms != null ? `${u.response_time_ms} ms` : null} />
        <Fact k="Content-Type" v={u.content_type} />
        <Fact k="Redirige a" v={u.redirect_url} />
        <Fact k="JS redirect" v={u.js_redirect_url} />
      </div>

      {meta && (
        <div className="card muted" style={{ marginBottom: 12 }}>
          <h3>On-page</h3>
          <Fact k="Title" v={meta.title} wide />
          <Fact k="Description" v={meta.meta_description} wide />
          <Fact k="Canonical" v={meta.canonical_href} wide />
          {meta.meta_refresh && <Fact k="Meta refresh" v={`${meta.meta_refresh_delay ?? "?"}s → ${meta.meta_refresh_url || "?"}`} wide />}
          <Fact k="Robots" v={meta.meta_robots} wide />
        </div>
      )}

      {u.issues.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <h3>Incidencias ({u.issues.length})</h3>
          {u.issues.map((i) => (
            <div key={i.id} className="row" style={{ marginBottom: 6 }}>
              <Severity level={i.severity} />
              <span className="mono">{i.issue_type}</span>
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h3>Inlinks ({u.inlinks.length})</h3>
        {u.inlinks.length === 0 && <div className="proxy-tag">Sin enlaces entrantes registrados.</div>}
        <table className="data">
          <tbody>
            {u.inlinks.slice(0, 50).map((l) => (
              <tr key={l.id}>
                <td className="cell-url" title={l.from_url}>{l.from_url}</td>
                <td>{l.anchor_text || <i className="proxy-tag">sin anchor</i>}</td>
                <td><span className="tag">{l.link_position}</span></td>
                <td>{l.follow === false ? <span className="tag">nofollow</span> : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Drawer>
  );
}

function Fact({ k, v, wide }) {
  if (wide) {
    return (
      <div style={{ marginBottom: 8 }}>
        <div className="k kpi-label">{k}</div>
        <div style={{ fontSize: 12.5, wordBreak: "break-all" }}>{v ?? "—"}</div>
      </div>
    );
  }
  return (
    <div className="fact">
      <div className="k">{k}</div>
      <div className="v num">{v ?? "—"}</div>
    </div>
  );
}
