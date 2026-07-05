import { marked } from "marked";
import { useState } from "react";

import { api } from "./api.js";
import { useAsync } from "./hooks.js";
import { detailsToText, issueInfo, issueLabel } from "./issueCatalog.js";
import { Drawer, ErrorBox, Severity, Spinner, StatusPill, fmt } from "./ui.jsx";

/* ------------------------------------------------------------------ */
/* Ficha de URL completa — compartida por Explorador e Incidencias.    */
/* ------------------------------------------------------------------ */
export default function UrlDrawer({ jobId, urlId, onClose }) {
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
      <div className="row between" style={{ alignItems: "flex-start", gap: 8 }}>
        <h2 style={{ wordBreak: "break-all" }}>{u.url}</h2>
        <a href={u.url} target="_blank" rel="noopener noreferrer"
          title="Abrir la página en el navegador">
          <button className="secondary" style={{ whiteSpace: "nowrap" }}>Ver la web ↗</button>
        </a>
      </div>
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
