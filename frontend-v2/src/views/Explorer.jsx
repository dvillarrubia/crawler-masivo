import { marked } from "marked";
import { useEffect, useMemo, useRef, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync, useStored } from "../hooks.js";
import { detailsToText, issueInfo, issueLabel } from "../issueCatalog.js";
import { Blocked, Drawer, ErrorBox, Pager, Severity, Spinner, StatusPill, fmt } from "../ui.jsx";

/** Definición de columnas: key API, etiqueta, tipo, filtro de servidor.
 *  filter: 'range' usa <key>_gte/_lte; 'contains' usa <param>; null sin filtro. */
// Cada columna lleva su explicación (desc): visible al pasar el ratón por la
// cabecera y por el selector de columnas. «—» = sin dato para esa URL.
const COLUMNS = [
  { key: "url", label: "URL", num: false, filter: null, always: true,
    desc: "La dirección de la página. Clic o Enter para abrir su ficha completa." },
  { key: "status_code", label: "Status", num: true, filter: "range", def: true,
    desc: "Código de respuesta del servidor: 200 = bien · 3xx = redirección · 4xx = no existe/prohibida · 5xx = el servidor falla." },
  { key: "resource_type", label: "Tipo", num: false, filter: null, def: true,
    desc: "Qué es la URL: página HTML, imagen, CSS, JavaScript, PDF, redirección…" },
  { key: "indexable", label: "Indexable", num: false, filter: null, def: true,
    desc: "¿Puede aparecer en Google? «no» si tiene noindex, canonical hacia otra página, error o redirección." },
  { key: "crawl_depth", label: "Prof. rastreo", num: true, filter: "range", def: true,
    desc: "A cuántos saltos de la semilla se DESCUBRIÓ la URL durante el rastreo. No es lo mismo que los clics reales desde la portada." },
  { key: "click_depth", label: "Clics desde home", num: true, filter: "range", def: false,
    desc: "Clics reales necesarios desde la portada para llegar. «sin camino» = no se llega clicando. Requiere activar la clasificación de enlaces en el rastreo." },
  { key: "inlinks_count", label: "Inlinks", num: true, filter: "range", def: true,
    desc: "Cuántos enlaces internos APUNTAN a esta página. Más inlinks = más autoridad recibida." },
  { key: "outlinks_count", label: "Outlinks", num: true, filter: "range", def: true,
    desc: "Cuántos enlaces salen de esta página hacia otras del sitio." },
  { key: "in_contextual", label: "Inlinks contenido", num: true, filter: "range", def: false,
    desc: "De sus inlinks, cuántos vienen del TEXTO de otras páginas (no de menús, footers o listados). Son los que más valen. Requiere clasificación de enlaces." },
  { key: "out_contextual", label: "Outlinks contenido", num: true, filter: "range", def: false,
    desc: "Cuántos enlaces salen desde el TEXTO de esta página (no de su plantilla). Requiere clasificación de enlaces." },
  { key: "pagerank", label: "PageRank", num: true, filter: "range", def: true,
    desc: "Autoridad interna de 0 a 10 según el grafo de enlaces del propio sitio. 10 = la página más enlazada/importante." },
  { key: "pagerank_semantic", label: "PR semántico", num: true, filter: "range", def: false,
    desc: "Autoridad contando solo los enlaces que llegan desde páginas del MISMO tema. Si es mucho menor que el PageRank, la página se sostiene por plantilla, no por relevancia. Requiere el análisis semántico." },
  // Métricas de Search Console (aparecen al importar GSC en Semántica)
  { key: "gsc_clicks", label: "Clics GSC", num: true, filter: "range", def: true,
    desc: "Clics reales desde Google en el periodo importado de Search Console. Requiere importar GSC en Semántica." },
  { key: "gsc_impressions", label: "Imprs. GSC", num: true, filter: "range", def: true,
    desc: "Veces que la página apareció en los resultados de Google (haya clic o no). Requiere importar GSC." },
  { key: "gsc_position", label: "Pos. GSC", num: true, filter: "range", def: true,
    desc: "Posición media en los resultados de Google. 1–10 = primera página. Requiere importar GSC." },
  { key: "gsc_ctr", label: "CTR GSC", num: true, filter: null, def: false,
    desc: "Porcentaje de impresiones que acabaron en clic. Bajo con muchas impresiones = título/descripción poco atractivos o posición baja." },
  { key: "word_count", label: "Palabras", num: true, filter: "range", def: true,
    desc: "Palabras de texto visible en la página." },
  { key: "unique_word_count", label: "Palabras propias", num: true, filter: "range", def: false,
    desc: "Palabras que quedan tras descontar la plantilla que se repite en toda la sección (menús, bloques legales…). Pocas = página «hueca». Requiere la capa de contenido único." },
  { key: "boilerplate_ratio", label: "% plantilla", num: true, filter: "range", def: false,
    desc: "Qué parte del texto de la página es plantilla repetida. 0,8 = el 80% no es contenido propio. Requiere la capa de contenido único." },
  { key: "js_content_ratio", label: "% solo JS", num: true, filter: "range", def: false,
    desc: "Qué parte del contenido solo existe tras ejecutar JavaScript — invisible para los buscadores de IA y el primer pase de Google. Requiere el análisis GEO del rastreo." },
  { key: "text_ratio", label: "% texto", num: true, filter: "range", def: false,
    desc: "Proporción de texto visible frente a código en el peso de la página. Muy bajo = casi todo es HTML/JS." },
  { key: "response_time_ms", label: "Latencia (ms)", num: true, filter: "range", def: true,
    desc: "Milisegundos que tardó el servidor en responder esta URL durante el rastreo." },
  { key: "transfer_size", label: "Peso (bytes)", num: true, filter: "range", def: false,
    desc: "Bytes transferidos al descargar la página." },
  { key: "url_length", label: "Long. URL", num: true, filter: "range", def: false,
    desc: "Caracteres de la URL completa. Más de ~115 se considera demasiado larga." },
  { key: "folder_depth", label: "Carpetas", num: true, filter: "range", def: false,
    desc: "Niveles de carpeta en la ruta: /ropa/hombre/camisas = 3." },
  { key: "in_sitemap", label: "Sitemap", num: false, filter: null, def: false,
    desc: "¿Está declarada en el sitemap.xml del sitio? Requiere haber activado la lectura de sitemaps en el rastreo." },
  { key: "title", label: "Title", num: false, filter: "contains", param: "title_contains", def: false, meta: true,
    desc: "La etiqueta <title> de la página: el texto azul del resultado en Google." },
  { key: "canonical", label: "Canonical", num: false, filter: "contains", param: "canonical_contains", def: false, metaField: "canonical_href",
    desc: "A qué URL declara pertenecer el contenido. Si apunta a otra página, esta versión cede su indexación." },
  { key: "host", label: "Host", num: false, filter: "contains", param: "host_contains", def: false,
    desc: "El dominio o subdominio de la URL." },
];

export default function ExplorerView() {
  const { jobId, segmentId } = useCtx();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ status_group: "", is_internal: "", resource_type: "", search: "" });
  const [colFilters, setColFilters] = useState({});   // draft de filtros por columna
  const [applied, setApplied] = useState({});          // filtros aplicados
  const [sort, setSort] = useState({ by: null, dir: "asc" });
  const [detailId, setDetailId] = useState(null);
  const [selIdx, setSelIdx] = useState(-1);
  const [visibleKeys, setVisibleKeys] = useStored(
    "explorer.columns.v3",  // v3: + columnas GSC por defecto
    COLUMNS.filter((c) => c.always || c.def).map((c) => c.key),
  );
  const [showCols, setShowCols] = useState(false);
  const tableRef = useRef(null);

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const visible = COLUMNS.filter((c) => visibleKeys.includes(c.key) || c.always);

  const params = useMemo(() => ({
    page, page_size: 100,
    segment_id: segmentId,
    status_group: filters.status_group,
    resource_type: filters.resource_type,
    search: filters.search,
    is_internal: filters.is_internal,
    sort_by: sort.by, sort_dir: sort.dir,
    ...applied,
  }), [page, segmentId, filters, sort, applied]);

  const urlsQ = useAsync(() => api.urls(jobId, params), [jobId, JSON.stringify(params)]);
  const statsQ = useAsync(() => api.stats(jobId, { segment_id: segmentId }), [jobId, segmentId]);

  const setFilter = (k, v) => { setFilters({ ...filters, [k]: v }); setPage(1); };
  const toggleSort = (col) => setSort((s) =>
    s.by === col ? { by: col, dir: s.dir === "asc" ? "desc" : "asc" } : { by: col, dir: "asc" });

  const applyColFilters = () => {
    const out = {};
    for (const c of COLUMNS) {
      if (c.filter === "range") {
        const lo = colFilters[`${c.key}_gte`], hi = colFilters[`${c.key}_lte`];
        if (lo) out[`${c.key}_gte`] = lo;
        if (hi) out[`${c.key}_lte`] = hi;
      } else if (c.filter === "contains") {
        const v = colFilters[c.param];
        if (v) out[c.param] = v;
      }
    }
    setApplied(out);
    setPage(1);
  };

  // Navegación por teclado: ↑/↓ mueven la selección, Enter abre, Esc cierra
  useEffect(() => {
    const onKey = (e) => {
      if (detailId && e.key === "Escape") { setDetailId(null); return; }
      if (detailId || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
      const items = urlsQ.data ? urlsQ.data.items : [];
      if (!items.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelIdx((i) => Math.min(items.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelIdx((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter" && selIdx >= 0) {
        setDetailId(items[selIdx].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [urlsQ.data, selIdx, detailId]);

  useEffect(() => {
    const row = tableRef.current?.querySelector("tr[data-selected='true']");
    row?.scrollIntoView({ block: "nearest" });
  }, [selIdx]);

  const groups = statsQ.data
    ? Object.fromEntries(statsQ.data.urls_by_status_group.map((g) => [g.status_group, g.count]))
    : {};

  const cellValue = (u, c) => {
    if (c.key === "url") return <span className="cell-url" title={u.url}>{u.url}</span>;
    if (c.key === "status_code") return <><StatusPill group={u.status_group} /> {u.status_code ?? ""}</>;
    if (c.key === "indexable") return u.indexable == null ? "—" : u.indexable ? "sí" : "no";
    if (c.key === "in_sitemap") return u.in_sitemap == null ? "—" : u.in_sitemap ? "sí" : "no";
    if (c.key === "click_depth" && u.click_depth == null && u.is_html) return <span className="tag">sin camino</span>;
    if (c.key === "gsc_ctr") return u.gsc_ctr == null ? "—" : `${(u.gsc_ctr * 100).toFixed(2)}%`;
    if (c.key === "gsc_position") return u.gsc_position == null ? "—" : u.gsc_position.toFixed(1);
    if (c.key === "title" || c.key === "canonical") {
      const meta = u.html_meta || {};
      const v = c.key === "title" ? meta.title : meta.canonical_href;
      return <span style={{ maxWidth: 260, display: "inline-block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={v || ""}>{v || "—"}</span>;
    }
    const v = u[c.key];
    return v == null ? "—" : c.num ? fmt(v) : String(v);
  };

  return (
    <div>
      <div className="row between">
        <div>
          <h1 className="page-title">Explorador</h1>
          <p className="page-sub">
            Todas las URLs del rastreo con todo lo que se sabe de cada una (status, title, autoridad,
            profundidad, contenido…). Filtra por cualquier columna, ordena, elige columnas con «Columnas ▾»
            y muévete con ↑ ↓; Enter abre la ficha completa de la URL.
          </p>
        </div>
        <span className="row" style={{ gap: 6 }}>
          <button className="secondary" onClick={() => setShowCols(!showCols)}>Columnas ▾</button>
          <a href={api.exportUrl(jobId, "urls")}><button className="secondary">CSV</button></a>
        </span>
      </div>

      {showCols && (
        <div className="card muted" style={{ marginBottom: 10 }}>
          <p className="proxy-tag" style={{ marginTop: 0 }}>
            Marca las columnas que quieras ver. Cada una lleva su explicación debajo — también aparece al
            pasar el ratón por la cabecera de la tabla.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "6px 16px" }}>
            {COLUMNS.filter((c) => !c.always).map((c) => (
              <label key={c.key} className="checkbox-row" style={{ margin: 0, alignItems: "flex-start" }}>
                <input type="checkbox" checked={visibleKeys.includes(c.key)}
                  onChange={(e) => setVisibleKeys(
                    e.target.checked
                      ? [...visibleKeys, c.key]
                      : visibleKeys.filter((k) => k !== c.key),
                  )} />
                <span>
                  <b style={{ fontSize: 12 }}>{c.label}</b>
                  <span style={{ display: "block", fontSize: 11, color: "var(--ink-muted)", lineHeight: 1.35 }}>
                    {c.desc}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </div>
      )}

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
        <input type="text" style={{ width: 220 }} placeholder="buscar en URL o title…"
          value={filters.search}
          onChange={(e) => setFilter("search", e.target.value)} />
      </div>

      <p className="proxy-tag" style={{ margin: "4px 0 8px" }}>
        Pasa el ratón por cualquier cabecera para ver qué significa esa columna. «—» = sin dato: o la métrica
        no aplica a esa URL (p. ej. GSC en una imagen), o la capa que la calcula no se activó en este rastreo
        (PR semántico → análisis semántico · clics desde home y enlaces de contenido → clasificación de
        enlaces · % solo JS → GEO · % plantilla → contenido único · métricas GSC → importar Search Console).
      </p>

      {urlsQ.error && <ErrorBox error={urlsQ.error} />}
      {urlsQ.loading ? <Spinner /> : (
        <>
          <div className="table-wrap" style={{ maxHeight: "58vh" }} ref={tableRef}>
            <table className="data">
              <thead>
                <tr>
                  {visible.map((c) => (
                    <th key={c.key} className={c.num ? "num" : ""} title={c.desc}
                      onClick={() => toggleSort(c.key)}>
                      {c.label}{sort.by === c.key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                    </th>
                  ))}
                </tr>
                <tr className="filter-row">
                  {visible.map((c) => (
                    <th key={c.key} onClick={(e) => e.stopPropagation()}>
                      {c.filter === "range" && (
                        <span className="row" style={{ gap: 3 }}>
                          <input type="number" placeholder="≥" style={{ width: 54, padding: "2px 4px", fontSize: 11 }}
                            value={colFilters[`${c.key}_gte`] || ""}
                            onChange={(e) => setColFilters({ ...colFilters, [`${c.key}_gte`]: e.target.value })}
                            onKeyDown={(e) => e.key === "Enter" && applyColFilters()} />
                          <input type="number" placeholder="≤" style={{ width: 54, padding: "2px 4px", fontSize: 11 }}
                            value={colFilters[`${c.key}_lte`] || ""}
                            onChange={(e) => setColFilters({ ...colFilters, [`${c.key}_lte`]: e.target.value })}
                            onKeyDown={(e) => e.key === "Enter" && applyColFilters()} />
                        </span>
                      )}
                      {c.filter === "contains" && (
                        <input type="text" placeholder="contiene…" style={{ width: 120, padding: "2px 4px", fontSize: 11 }}
                          value={colFilters[c.param] || ""}
                          onChange={(e) => setColFilters({ ...colFilters, [c.param]: e.target.value })}
                          onKeyDown={(e) => e.key === "Enter" && applyColFilters()} />
                      )}
                      {c.key === "url" && (
                        <button className="secondary" style={{ padding: "2px 8px", fontSize: 11 }}
                          onClick={applyColFilters}>Aplicar filtros</button>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {urlsQ.data.items.map((u, i) => (
                  <tr key={u.id} data-selected={i === selIdx}
                    style={i === selIdx ? { outline: "2px solid var(--ink)", outlineOffset: -2 } : undefined}
                    onClick={() => { setSelIdx(i); setDetailId(u.id); }}>
                    {visible.map((c) => (
                      <td key={c.key} className={c.num ? "num" : ""}>{cellValue(u, c)}</td>
                    ))}
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

/* ------------------------------------------------------------------ */
/* Contenido extraído: renderizado / markdown fuente / texto plano      */
/* ------------------------------------------------------------------ */
function ContentPanel({ u }) {
  const [mode, setMode] = useState("render");
  const pc = u.page_content;

  if (!pc || (!pc.content_markdown && !pc.content_text)) {
    return (
      <div className="card muted">
        No hay contenido extraído para esta URL. Ocurre si no es HTML, si respondió con error
        o si el rastreo se lanzó con la extracción de contenido desactivada.
      </div>
    );
  }

  const MODES = [
    ["render", "Renderizado", !!pc.content_markdown],
    ["md", "Markdown (fuente)", !!pc.content_markdown],
    ["text", "Texto plano", !!pc.content_text],
  ];

  const rendered = () => {
    try {
      // se escapa el HTML crudo antes de parsear: el contenido viene de
      // sitios rastreados y no debe poder inyectar etiquetas
      return marked.parse((pc.content_markdown || "").replace(/</g, "&lt;"));
    } catch {
      return "<pre>" + (pc.content_markdown || "").replace(/</g, "&lt;") + "</pre>";
    }
  };

  return (
    <div>
      <div className="toolbar" style={{ marginBottom: 8 }}>
        {MODES.filter(([, , ok]) => ok).map(([k, label]) => (
          <button key={k} className={mode === k ? "" : "secondary"}
            style={{ padding: "3px 10px", fontSize: 11.5 }}
            onClick={() => setMode(k)}>{label}</button>
        ))}
        <span className="proxy-tag num">
          {fmt(pc.content_length)} caracteres · el contenido principal de la página, sin menús ni plantilla
        </span>
      </div>
      {mode === "render" && (
        <div className="card" style={{ maxHeight: "58vh", overflowY: "auto", lineHeight: 1.55, fontSize: 13.5 }}
          dangerouslySetInnerHTML={{ __html: rendered() }} />
      )}
      {mode === "md" && (
        <pre className="card mono" style={{ maxHeight: "58vh", overflowY: "auto", whiteSpace: "pre-wrap", fontSize: 12 }}>
          {pc.content_markdown}
        </pre>
      )}
      {mode === "text" && (
        <pre className="card" style={{ maxHeight: "58vh", overflowY: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.5 }}>
          {pc.content_text}
        </pre>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Ficha de URL completa                                                */
/* ------------------------------------------------------------------ */
function UrlDrawer({ jobId, urlId, onClose }) {
  const q = useAsync(() => api.urlDetail(jobId, urlId), [jobId, urlId]);
  const [tab, setTab] = useState("resumen");

  if (q.loading) return <Drawer onClose={onClose}><Spinner /></Drawer>;
  if (q.error) return <Drawer onClose={onClose}><ErrorBox error={q.error} /></Drawer>;
  const u = q.data;
  const meta = u.html_meta;

  const TABS = [
    ["resumen", "Resumen"],
    ["onpage", "On-page"],
    ["contenido", "Contenido"],
    ["enlaces", `Enlaces (${u.inlinks.length}/${u.outlinks.length})`],
    ["recursos", `Recursos (${u.resources.length})`],
    ["datos", "Datos estructurados"],
    ["seguridad", "Seguridad"],
  ];

  return (
    <Drawer onClose={onClose}>
      <h2>{u.url}</h2>
      <div className="row" style={{ margin: "8px 0", flexWrap: "wrap" }}>
        <StatusPill group={u.status_group} />
        <span className="tag">{u.resource_type}</span>
        {u.indexable === false && <span className="tag">no indexable · {u.indexability_status || "?"}</span>}
        {u.in_sitemap != null && <span className="tag">{u.in_sitemap ? "en sitemap" : "fuera de sitemap"}</span>}
        {u.js_redirect_url && <span className="tag">JS redirect</span>}
      </div>

      <div className="toolbar">
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "" : "secondary"}
            style={{ padding: "3px 10px", fontSize: 11.5 }}
            onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "resumen" && (
        <div className="facts">
          <Fact k="Prof. descubrimiento" v={u.crawl_depth} />
          <Fact k="Clics desde home" v={u.click_depth} />
          <Fact k="PageRank" v={u.pagerank} />
          <Fact k="PR semántico" v={u.pagerank_semantic} />
          <Fact k="Inlinks" v={fmt(u.inlinks_count)} />
          <Fact k="Outlinks" v={fmt(u.outlinks_count)} />
          <Fact k="Inlinks contextuales" v={u.in_contextual} />
          <Fact k="Outlinks contextuales" v={u.out_contextual} />
          <Fact k="Palabras" v={fmt(u.word_count)} />
          <Fact k="Palabras únicas (sin plantilla)" v={u.unique_word_count} />
          <Fact k="% plantilla" v={u.boilerplate_ratio != null ? `${(u.boilerplate_ratio * 100).toFixed(1)}%` : null} />
          <Fact k="% solo tras JS" v={u.js_content_ratio != null ? `${(u.js_content_ratio * 100).toFixed(1)}%` : null} />
          <Fact k="Clics GSC" v={u.gsc_clicks != null ? fmt(u.gsc_clicks) : null} />
          <Fact k="Impresiones GSC" v={u.gsc_impressions != null ? fmt(u.gsc_impressions) : null} />
          <Fact k="Posición media GSC" v={u.gsc_position != null ? u.gsc_position.toFixed(1) : null} />
          <Fact k="CTR GSC" v={u.gsc_ctr != null ? `${(u.gsc_ctr * 100).toFixed(2)}%` : null} />
          <Fact k="Latencia" v={u.response_time_ms != null ? `${u.response_time_ms} ms` : null} />
          <Fact k="Content-Type" v={u.content_type} />
          <Fact k="Redirige a" v={u.redirect_url} />
          <Fact k="JS redirect" v={u.js_redirect_url} />
        </div>
      )}

      {tab === "resumen" && u.issues.length > 0 && (
        <div className="card" style={{ marginTop: 10 }}>
          <h3>Incidencias ({u.issues.length})</h3>
          {u.issues.map((i) => (
            <div key={i.id} style={{ marginBottom: 8 }}>
              <div className="row">
                <Severity level={i.severity} />
                <b style={{ fontSize: 12.5 }} title={`${i.issue_type} — ${issueInfo(i.issue_type)}`}>{issueLabel(i.issue_type)}</b>
                {i.review_status && <span className="tag">{i.review_status}</span>}
              </div>
              <div className="proxy-tag" style={{ marginLeft: 2 }}>
                {detailsToText(i.issue_type, i.details) || issueInfo(i.issue_type)}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === "contenido" && <ContentPanel u={u} />}

      {tab === "onpage" && (
        <>
          {meta ? (
            <div className="card muted">
              <Fact k="Title" v={meta.title} wide />
              <Fact k="Description" v={meta.meta_description} wide />
              <Fact k="Canonical" v={meta.canonical_href} wide />
              {meta.meta_refresh && <Fact k="Meta refresh" v={`${meta.meta_refresh_delay ?? "?"}s → ${meta.meta_refresh_url || "?"}`} wide />}
              <Fact k="Robots" v={meta.meta_robots} wide />
              <Fact k="OG title" v={meta.og_title} wide />
              <Fact k="rel next/prev" v={[meta.rel_next, meta.rel_prev].filter(Boolean).join(" · ") || null} wide />
            </div>
          ) : <div className="proxy-tag">Sin metadatos HTML.</div>}
          {u.headings.length > 0 && (
            <div className="card" style={{ marginTop: 10 }}>
              <h3>Encabezados ({u.headings.length})</h3>
              {u.headings.map((h) => (
                <div key={h.id} style={{ fontSize: 12.5, marginBottom: 3, paddingLeft: (parseInt(h.tag[1], 10) - 1) * 14 }}>
                  <span className="tag">{h.tag}</span> {h.text || <i className="proxy-tag">vacío</i>}
                </div>
              ))}
            </div>
          )}
          {u.hreflangs.length > 0 && (
            <div className="card" style={{ marginTop: 10 }}>
              <h3>Hreflang ({u.hreflangs.length})</h3>
              {u.hreflangs.map((h) => (
                <div key={h.id} className="row between" style={{ fontSize: 12 }}>
                  <span className="tag">{h.lang}</span>
                  <span className="cell-url">{h.href}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {tab === "enlaces" && (
        <>
          <div className="card">
            <h3>Inlinks ({u.inlinks.length})</h3>
            {u.inlinks.length === 0 && <div className="proxy-tag">Sin enlaces entrantes registrados.</div>}
            <table className="data">
              <tbody>
                {u.inlinks.slice(0, 100).map((l) => (
                  <tr key={l.id}>
                    <td className="cell-url" title={l.from_url}>{l.from_url}</td>
                    <td>{l.anchor_text || <i className="proxy-tag">sin anchor</i>}</td>
                    <td><span className="tag">{l.edge_class || l.link_position}</span></td>
                    <td>{l.follow === false ? <span className="tag">nofollow</span> : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card" style={{ marginTop: 10 }}>
            <h3>Outlinks ({u.outlinks.length})</h3>
            <table className="data">
              <tbody>
                {u.outlinks.slice(0, 100).map((l) => (
                  <tr key={l.id}>
                    <td className="cell-url" title={l.to_url}>{l.to_url}</td>
                    <td>{l.anchor_text || ""}</td>
                    <td><span className="tag">{l.edge_class || l.link_position}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "recursos" && (
        <div className="card">
          <h3>Recursos ({u.resources.length})</h3>
          <table className="data">
            <tbody>
              {u.resources.map((r) => (
                <tr key={r.id}>
                  <td><span className="tag">{r.resource_type}</span></td>
                  <td className="cell-url" title={r.resource_url}>{r.resource_url}</td>
                  <td>{r.alt_text || (r.resource_type === "image" ? <i className="proxy-tag">sin alt</i> : "")}</td>
                  <td>{r.is_mixed_content ? <span className="tag">mixed content</span> : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "datos" && (
        <div className="card">
          <h3>Datos estructurados ({u.structured_data.length})</h3>
          {u.structured_data.length === 0 && <div className="proxy-tag">Sin datos estructurados.</div>}
          {u.structured_data.map((s) => (
            <div key={s.id} style={{ marginBottom: 10 }}>
              <div className="row" style={{ gap: 6 }}>
                <span className="tag">{s.format}</span>
                <b style={{ fontSize: 12.5 }}>{s.schema_type || "?"}</b>
                {s.visible_without_js === false && <span className="tag">solo tras JS</span>}
              </div>
              <pre className="diff" style={{ maxHeight: 160, overflow: "auto" }}>
                {JSON.stringify(s.raw, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}

      {tab === "seguridad" && (
        <div className="card">
          <h3>Cabeceras de seguridad</h3>
          {u.security ? (
            <div className="facts">
              <Fact k="HTTPS" v={u.security.is_https ? "sí" : "no"} />
              <Fact k="HSTS" v={u.security.has_hsts ? "sí" : "no"} />
              <Fact k="CSP" v={u.security.has_csp ? "sí" : "no"} />
              <Fact k="X-Content-Type-Options" v={u.security.has_x_content_type_options ? "sí" : "no"} />
              <Fact k="X-Frame-Options" v={u.security.has_x_frame_options ? "sí" : "no"} />
              <Fact k="Referrer-Policy" v={u.security.referrer_policy} />
              <Fact k="Mixed content" v={u.security.has_mixed_content ? "sí" : "no"} />
            </div>
          ) : <div className="proxy-tag">Sin datos de seguridad.</div>}
        </div>
      )}
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
