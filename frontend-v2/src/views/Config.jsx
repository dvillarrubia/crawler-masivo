import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Spinner, fmt } from "../ui.jsx";

/** Configuración del proyecto: segmentos con preview, watchlist,
 *  umbrales sugeridos y estado de fuentes. */
export default function ConfigView() {
  const { clientId } = useCtx();
  if (!clientId) {
    return <Blocked title="Configuración de proyecto"
      reason="Los segmentos, la watchlist y los umbrales viven a nivel de proyecto. Selecciona uno en la barra superior." />;
  }
  return (
    <div>
      <h1 className="page-title">Configuración · {clientId}</h1>
      <p className="page-sub">Se define una vez y se aplica a cada rastreo nuevo del proyecto.</p>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
        <SegmentsPanel clientId={clientId} />
        <div>
          <WatchlistPanel clientId={clientId} />
          <ThresholdsPanel clientId={clientId} />
          <SourcesPanel />
        </div>
      </div>
    </div>
  );
}

/** Segmentos con vista previa obligatoria (T12). */
function SegmentsPanel({ clientId }) {
  const listQ = useAsync(() => api.segments(clientId), [clientId]);
  const { setSegmentId } = useCtx();
  const EMPTY = { name: "", rule_type: "regex", rule: "", priority: 100, is_business: false };
  const [draft, setDraft] = useState(EMPTY);
  const [editingId, setEditingId] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);

  const startEdit = (s) => {
    setEditingId(s.id);
    setDraft({ name: s.name, rule_type: s.rule_type, rule: s.rule, priority: s.priority, is_business: !!s.is_business });
    setPreview(null);
    setError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setDraft(EMPTY);
    setPreview(null);
    setError(null);
  };

  const doPreview = async () => {
    setError(null);
    try {
      // Al editar, la regla en curso sustituye a la guardada (no se duplica).
      const existing = (listQ.data || [])
        .filter((s) => s.id !== editingId)
        .map((s) => ({
          name: s.name, rule_type: s.rule_type, rule: s.rule, priority: s.priority,
        }));
      const res = await api.previewSegments(clientId, [...existing, { ...draft, priority: Number(draft.priority) }]);
      setPreview(res);
    } catch (e) { setError(e.message); }
  };

  const save = async () => {
    setError(null);
    try {
      const payload = { ...draft, priority: Number(draft.priority) };
      if (editingId != null) {
        await api.updateSegment(clientId, editingId, payload);
      } else {
        await api.createSegment(clientId, payload);
      }
      cancelEdit();
      listQ.reload();
    } catch (e) { setError(e.message); }
  };

  return (
    <div className="card">
      <h3>Segmentos</h3>
      {listQ.loading && <Spinner />}
      {(listQ.data || []).map((s) => (
        <div className="row between" key={s.id} style={{ marginBottom: 6 }}>
          <span>
            <b>{s.name}</b> <span className="mono proxy-tag">{s.rule_type}:{s.rule}</span>
            {s.is_business && <span className="tag" style={{ marginLeft: 6 }}>negocio</span>}
          </span>
          <span className="row">
            <span className="tag num">prio {s.priority}</span>
            <button className="secondary" title="Editar" onClick={() => startEdit(s)}>✎</button>
            <button className="secondary" onClick={async () => {
              if (window.confirm(`¿Borrar el segmento "${s.name}"?`)) {
                await api.deleteSegment(clientId, s.id);
                setSegmentId("");
                if (editingId === s.id) cancelEdit();
                listQ.reload();
              }
            }}>×</button>
          </span>
        </div>
      ))}
      {listQ.data && listQ.data.length === 0 &&
        <div className="proxy-tag" style={{ marginBottom: 10 }}>Sin segmentos: el sitio es una lista plana. Define plantillas (blog, producto, categoría…).</div>}

      <div style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 10, marginTop: 8 }}>
        {error && <div className="alert">{error}</div>}
        <div className="form-grid">
          <div className="field">
            <label>Nombre</label>
            <input type="text" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Blog" />
          </div>
          <div className="field">
            <label>Prioridad (menor gana)</label>
            <input type="number" value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value })} />
          </div>
        </div>
        <div className="form-grid">
          <div className="field">
            <label>Tipo de regla</label>
            <select value={draft.rule_type} onChange={(e) => setDraft({ ...draft, rule_type: e.target.value })}>
              <option value="regex">regex sobre el path</option>
              <option value="prefix">prefijo del path</option>
            </select>
          </div>
          <div className="field">
            <label>Regla</label>
            <input type="text" className="mono" value={draft.rule}
              onChange={(e) => setDraft({ ...draft, rule: e.target.value })} placeholder="^/blog/" />
          </div>
        </div>
        <label className="checkbox-row">
          <input type="checkbox" checked={draft.is_business}
            onChange={(e) => setDraft({ ...draft, is_business: e.target.checked })} />
          Sección de negocio (umbrales de arquitectura más estrictos — T23)
        </label>
        <div className="row" style={{ gap: 8 }}>
          {editingId != null && (
            <span className="tag">editando «{(listQ.data || []).find((s) => s.id === editingId)?.name}»</span>
          )}
          <button className="secondary" disabled={!draft.name || !draft.rule} onClick={doPreview}>
            Vista previa
          </button>
          <button disabled={!preview || !draft.name || !draft.rule} onClick={save}
            title="La preview es obligatoria antes de guardar">
            {editingId != null ? "Guardar cambios" : "Guardar segmento"}
          </button>
          {editingId != null && (
            <button className="secondary" onClick={cancelEdit}>Cancelar</button>
          )}
        </div>

        {preview && (
          <div className="card muted" style={{ marginTop: 10 }}>
            <h3>Preview sobre el último rastreo {preview.job_id ? "" : "(sin rastreos aún)"}</h3>
            {preview.entries.map((e) => (
              <div className="row between" key={e.name} style={{ marginBottom: 4 }}>
                <span>{e.name}</span>
                <span className="num">{fmt(e.matched_urls)} URLs</span>
              </div>
            ))}
            <div className="row between" style={{ color: "var(--ink-muted)" }}>
              <span>(sin segmento)</span>
              <span className="num">{fmt(preview.unmatched_urls)}</span>
            </div>
            {preview.entries.some((e) => e.matched_urls === preview.total_urls && preview.total_urls > 0) &&
              <div className="alert warn" style={{ marginTop: 8 }}>Una regla captura TODO el sitio: revisa antes de guardar.</div>}
          </div>
        )}
      </div>
    </div>
  );
}

/** Watchlist (T16). */
function WatchlistPanel({ clientId }) {
  const listQ = useAsync(() => api.watchlist(clientId), [clientId]);
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState(null);

  const add = async () => {
    setError(null);
    try {
      await api.addWatch(clientId, { url, label: label || null });
      setUrl(""); setLabel("");
      listQ.reload();
    } catch (e) { setError(e.message); }
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3>Watchlist — páginas de negocio vigiladas</h3>
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        En cada rastreo se verifica: 200, indexable y canonical a sí misma. El primer incumplimiento es un error.
      </p>
      {listQ.loading && <Spinner />}
      {(listQ.data || []).map((w) => (
        <div className="row between" key={w.id} style={{ marginBottom: 5 }}>
          <span className="cell-url" title={w.url}>{w.label ? <b>{w.label} · </b> : null}{w.url}</span>
          <button className="secondary" onClick={async () => { await api.deleteWatch(clientId, w.id); listQ.reload(); }}>×</button>
        </div>
      ))}
      {error && <div className="alert">{error}</div>}
      <div className="row" style={{ gap: 6, marginTop: 8 }}>
        <input type="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
        <input type="text" placeholder="etiqueta" style={{ width: 140 }} value={label} onChange={(e) => setLabel(e.target.value)} />
        <button disabled={!url} onClick={add}>Añadir</button>
      </div>
    </div>
  );
}

/** Umbrales sugeridos (T16) — solo sugiere. */
function ThresholdsPanel({ clientId }) {
  const q = useAsync(() => api.suggestedThresholds(clientId), [clientId]);

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3>Umbrales sugeridos</h3>
      {q.loading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && !q.data.job_id && <div className="proxy-tag">Sin rastreos completados: nada que sugerir todavía.</div>}
      {q.data && q.data.job_id && (
        <>
          <table className="data" style={{ maxWidth: 380 }}>
            <tbody>
              {Object.entries(q.data.suggestions).map(([k, v]) => (
                <tr key={k}><td className="mono">{k}</td><td className="num">{v == null ? "—" : fmt(v)}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="proxy-tag">Calculados con percentiles del último rastreo. Solo sugerencia: aplícalos al re-analizar o en el próximo job.</p>
        </>
      )}
    </div>
  );
}

function SourcesPanel() {
  return (
    <div className="card">
      <h3>Fuentes de datos</h3>
      <SourceRow name="Crawl" state="Conectado" ok />
      <SourceRow name="Sitemaps" state="Por job (flag ingest_sitemaps)" ok />
      <SourceRow name="Google Search Console" state="Cuentas + import por run" href="#/cuentas" />
      <SourceRow name="Semántica (Gemini)" state="Cuentas + análisis por run" href="#/cuentas" />
      <SourceRow name="Logs de servidor" state="No conectado — sin ingesta de logs" />
    </div>
  );
}

function SourceRow({ name, state, ok, href }) {
  return (
    <div className="row between" style={{ marginBottom: 6 }}>
      <span>{name}</span>
      <span className={`chip ${ok ? "ok" : "off"}`}>
        <span className="dot" />
        {href ? <a href={href}>{state} →</a> : state}
      </span>
    </div>
  );
}
