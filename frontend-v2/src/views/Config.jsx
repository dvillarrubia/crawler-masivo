import { useEffect, useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, ErrorBox, Spinner, fmt } from "../ui.jsx";

/** Configurador del cliente, por pestañas y explicado para alguien que
 *  no sabe de SEO. Se define una vez y se aplica a cada rastreo nuevo. */
const CONFIG_TABS = [
  ["cuentas", "Cuentas y fuentes",
    "De dónde saca datos este proyecto: la cuenta de Google que paga los análisis y de qué propiedad de Search Console vienen los clics. Empieza por aquí — sin cuentas, buena parte del análisis no tiene con qué trabajar."],
  ["estructura", "Estructura del sitio",
    "Trocea el sitio en secciones (blog, producto, servicios…) para poder filtrar cualquier vista por ellas, y marca las páginas importantes que quieres vigilar en cada rastreo."],
  ["entidades", "Entidades",
    "Enseña al análisis qué cosas del negocio buscar en las páginas y en las búsquedas (productos, servicios, categorías…). Es lo que alimenta las propuestas de canibalización y cobertura por entidad."],
  ["umbrales", "Umbrales",
    "A partir de qué valores el análisis marca cada problema. Los valores por defecto siguen las prácticas habituales; ajústalos solo si sabes lo que haces."],
];

export default function ConfigView() {
  const { clientId } = useCtx();
  const [tab, setTab] = useState("cuentas");
  if (!clientId) {
    return <Blocked title="Configuración de proyecto"
      reason="Todo lo de aquí vive a nivel de PROYECTO (cliente): cuentas, secciones, entidades y umbrales. Selecciona un proyecto en la barra superior." />;
  }
  const intro = (CONFIG_TABS.find(([k]) => k === tab) || CONFIG_TABS[0])[2];
  return (
    <div>
      <h1 className="page-title">Configuración · {clientId}</h1>
      <p className="page-sub">
        El configurador del proyecto: se define una vez y se aplica a cada rastreo nuevo.
      </p>
      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        {CONFIG_TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "" : "secondary"} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <div className="card muted" style={{ marginBottom: 12 }}>{intro}</div>

      {tab === "cuentas" && (
        <>
          <ClientAccountsPanel clientId={clientId} />
          <SourcesPanel />
        </>
      )}
      {tab === "estructura" && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
          <SegmentsPanel clientId={clientId} />
          <WatchlistPanel clientId={clientId} />
        </div>
      )}
      {tab === "entidades" && (
        <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
          <ExtractionSchemaPanel clientId={clientId} />
          <div>
            <CatalogPanel clientId={clientId} />
            <EntityStatusPanel />
          </div>
        </div>
      )}
      {tab === "umbrales" && <ThresholdsPanel clientId={clientId} />}
    </div>
  );
}

/** Cuentas y propiedad del cliente: qué credenciales usa este proyecto. */
function ClientAccountsPanel({ clientId }) {
  const settingsQ = useAsync(() => api.clientSettings(clientId), [clientId]);
  const gemQ = useAsync(() => api.geminiAccounts().catch(() => []), []);
  const gscQ = useAsync(() => api.gscAccounts().catch(() => []), []);
  const [draft, setDraft] = useState(null);
  const [msg, setMsg] = useState(null);
  const [error, setError] = useState(null);

  const s = settingsQ.data || {};
  const value = draft || {
    gemini_account_id: s.gemini_account_id || "",
    gsc_account_id: s.gsc_account_id || "",
    gsc_property: s.gsc_property || "",
  };

  const save = async () => {
    setError(null); setMsg(null);
    try {
      await api.saveClientSettings(clientId, {
        gemini_account_id: value.gemini_account_id || null,
        gsc_account_id: value.gsc_account_id || null,
        gsc_property: value.gsc_property || null,
      });
      setMsg("Guardado. La consola pre-rellenará estas cuentas en Semántica y en los pipelines.");
      settingsQ.reload();
    } catch (e) { setError(e.message); }
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3>Cuentas del proyecto</h3>
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        Qué cuenta de Gemini paga los análisis de este cliente y de qué propiedad de Search Console
        se importan sus datos. Las cuentas se dan de alta en <a href="#/cuentas">Cuentas</a>.
      </p>
      {settingsQ.loading && <Spinner />}
      <div className="form-grid">
        <div className="field">
          <label>Cuenta Gemini</label>
          <select value={value.gemini_account_id}
            onChange={(e) => setDraft({ ...value, gemini_account_id: e.target.value })}>
            <option value="">— sin asignar —</option>
            {(gemQ.data || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label>Cuenta Search Console</label>
          <select value={value.gsc_account_id}
            onChange={(e) => setDraft({ ...value, gsc_account_id: e.target.value })}>
            <option value="">— sin asignar —</option>
            {(gscQ.data || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
      </div>
      <div className="field">
        <label>Propiedad GSC</label>
        <input type="text" placeholder="sc-domain:cliente.com" value={value.gsc_property}
          onChange={(e) => setDraft({ ...value, gsc_property: e.target.value })} />
      </div>
      {error && <div className="alert">{error}</div>}
      {msg && <div className="alert warn">{msg}</div>}
      <button onClick={save}>Guardar cuentas</button>
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
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        Reglas sobre la ruta de la URL que clasifican cada página en una plantilla (ej.: <code>^/blog/</code> → Blog).
        Gana la primera regla que encaje por orden de prioridad. La vista previa contra el último rastreo
        es obligatoria antes de guardar: evita reglas que capturan todo o nada.
      </p>
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
          Sección de negocio — sus páginas se vigilan con umbrales más estrictos (profundidad de clic, enlaces desde contenido)
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

/** Filas nombre+descripción para tipos de entidad (añadir/quitar). */
function TypeRows({ rows, onChange, placeholderNombre, placeholderDesc }) {
  const set = (i, k, v) => {
    const next = rows.slice();
    next[i] = { ...next[i], [k]: v };
    onChange(next);
  };
  return (
    <>
      {rows.map((r, i) => (
        <div key={i} className="row" style={{ gap: 6, marginBottom: 6, alignItems: "flex-start" }}>
          <input type="text" style={{ width: 130 }} className="mono" placeholder={placeholderNombre}
            value={r.nombre} onChange={(e) => set(i, "nombre", e.target.value.toLowerCase())} />
          <input type="text" style={{ flex: 1 }} placeholder={placeholderDesc}
            value={r.descripcion} onChange={(e) => set(i, "descripcion", e.target.value)} />
          <button className="secondary" title="Quitar"
            onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="secondary" onClick={() => onChange([...rows, { nombre: "", descripcion: "" }])}>
        + añadir tipo
      </button>
    </>
  );
}

const EMPTY_SCHEMA_FORM = {
  resolubles: [{ nombre: "", descripcion: "" }],
  senal: [],
  catalogo_fuente: "generado",
  tipo_pagina: "",
  resolucion_alta: 0.85,
  resolucion_baja: 0.6,
};

/** Qué entidades busca el pipeline en este cliente — FORMULARIO
 *  (el YAML es interno: lo genera y valida el servidor). */
function ExtractionSchemaPanel({ clientId }) {
  const q = useAsync(() => api.extractionSchema(clientId), [clientId]);
  const [draft, setDraft] = useState(null);
  const [msg, setMsg] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const parsed = q.data && q.data.parsed;
  const form = draft || (parsed ? {
    resolubles: parsed.resolubles,
    senal: parsed.senal,
    catalogo_fuente: parsed.catalogo_fuente,
    tipo_pagina: (parsed.tipo_pagina || []).join(", "),
    resolucion_alta: parsed.resolucion_alta,
    resolucion_baja: parsed.resolucion_baja,
  } : EMPTY_SCHEMA_FORM);

  const save = async () => {
    setBusy(true); setError(null); setMsg(null);
    try {
      const payload = {
        resolubles: form.resolubles.filter((r) => r.nombre && r.descripcion),
        senal: form.senal.filter((r) => r.nombre && r.descripcion),
        catalogo_fuente: form.catalogo_fuente,
        tipo_pagina: form.tipo_pagina.split(",").map((s) => s.trim()).filter(Boolean),
        resolucion_alta: Number(form.resolucion_alta),
        resolucion_baja: Number(form.resolucion_baja),
      };
      const r = await api.saveExtractionSchemaForm(clientId, payload);
      setMsg(`Guardado: ${r.resolubles.length} tipos con catálogo, ${r.senal.length} de señal.`);
      setDraft(null);
      q.reload();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3>Extracción de entidades — qué buscar en este cliente</h3>
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        Define los tipos de cosa que el análisis de entidades debe reconocer en las páginas y en las
        búsquedas. Cada tipo lleva una descripción en lenguaje natural: es la instrucción que recibe
        el modelo, escríbela como se lo explicarías a una persona.
      </p>
      {q.loading && <Spinner />}

      <h3 style={{ fontSize: 12.5 }}>Entidades con catálogo (se resuelven a un id)</h3>
      <Hint>Lo nuclear del negocio: producto, servicio, categoría… Ej.: <b>servicio</b> — «Servicio profesional concreto que se ofrece, como 'diseño de tienda online'».</Hint>
      <TypeRows rows={form.resolubles} onChange={(rows) => setDraft({ ...form, resolubles: rows })}
        placeholderNombre="servicio" placeholderDesc="Descripción en lenguaje natural de qué es este tipo…" />

      <h3 style={{ fontSize: 12.5, marginTop: 12 }}>Entidades de señal (no se resuelven)</h3>
      <Hint>Evidencia de intención: problemas que expresa el usuario, atributos, ganas de contactar…</Hint>
      <TypeRows rows={form.senal} onChange={(rows) => setDraft({ ...form, senal: rows })}
        placeholderNombre="problema" placeholderDesc="Ej.: Problema o necesidad que expresa el usuario…" />

      <div className="form-grid" style={{ marginTop: 12 }}>
        <div className="field">
          <label>Origen del catálogo</label>
          <select value={form.catalogo_fuente}
            onChange={(e) => setDraft({ ...form, catalogo_fuente: e.target.value })}>
            <option value="generado">generado — se siembra del propio crawl y lo validas abajo</option>
            <option value="feed">feed — lo cargas tú (alta manual en el catálogo)</option>
            <option value="crawl">crawl — derivado del rastreo</option>
          </select>
        </div>
        <div className="field">
          <label>Tipos de página (separados por comas)</label>
          <input type="text" placeholder="servicio, caso_exito, blog, landing, contacto"
            value={form.tipo_pagina}
            onChange={(e) => setDraft({ ...form, tipo_pagina: e.target.value })} />
          <Hint>Las plantillas de este vertical. El funnel (TOFU/MOFU/BOFU) es universal y va siempre.</Hint>
        </div>
      </div>

      {error && <div className="alert" style={{ marginTop: 6 }}>{error}</div>}
      {msg && <div className="alert warn" style={{ marginTop: 6 }}>{msg}</div>}
      <div className="row" style={{ gap: 8, marginTop: 6 }}>
        <button disabled={busy} onClick={save}>{busy ? "Validando…" : "Guardar"}</button>
        {q.data && q.data.status === "empty" && !draft && (
          <span className="proxy-tag">Este cliente aún no tiene definición de entidades.</span>
        )}
      </div>
    </div>
  );
}

function Hint({ children }) {
  return <p className="proxy-tag" style={{ margin: "2px 0 6px" }}>{children}</p>;
}

/** Catálogo de entidades: la validación humana del catálogo generado. */
function CatalogPanel({ clientId }) {
  const [search, setSearch] = useState("");
  const [applied, setApplied] = useState("");
  const q = useAsync(
    () => api.entityCatalog(clientId, { search: applied, page_size: 30 }),
    [clientId, applied],
  );
  const [nuevo, setNuevo] = useState({ name: "", entity_type: "" });
  const [error, setError] = useState(null);

  const add = async () => {
    setError(null);
    try {
      await api.addCatalogEntry(clientId, nuevo);
      setNuevo({ name: "", entity_type: "" });
      q.reload();
    } catch (e) { setError(e.message); }
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3>Catálogo de entidades {q.data ? `(${fmt(q.data.total)})` : ""}</h3>
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        La lista canónica de cosas del cliente (sus servicios, productos…). El pipeline resuelve las
        menciones contra este catálogo. Si el origen es «generado», se siembra del crawl y aquí lo
        depuras: borra el ruido y añade lo que falte.
      </p>
      <div className="row" style={{ gap: 6, marginBottom: 8 }}>
        <input type="text" placeholder="buscar…" value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && setApplied(search)} />
        <button className="secondary" onClick={() => setApplied(search)}>Buscar</button>
      </div>
      {q.loading && <Spinner />}
      {q.data && q.data.items.map((e) => (
        <div className="row between" key={e.entity_id} style={{ marginBottom: 4 }}>
          <span>
            <b style={{ fontSize: 12.5 }}>{e.name}</b>{" "}
            <span className="tag">{e.entity_type}</span>{" "}
            <span className="proxy-tag">{e.source}{e.embedded ? "" : " · sin embeber aún"}</span>
          </span>
          <button className="secondary" title="Borrar del catálogo" onClick={async () => {
            if (window.confirm(`¿Borrar «${e.name}» del catálogo?`)) {
              await api.deleteCatalogEntry(clientId, e.entity_id);
              q.reload();
            }
          }}>×</button>
        </div>
      ))}
      {q.data && q.data.items.length === 0 && (
        <div className="proxy-tag">Catálogo vacío: se sembrará al ejecutar el pipeline (origen «generado») o añade entradas a mano.</div>
      )}
      {error && <div className="alert">{error}</div>}
      <div className="row" style={{ gap: 6, marginTop: 8 }}>
        <input type="text" placeholder="nombre de la entidad" value={nuevo.name}
          onChange={(e) => setNuevo({ ...nuevo, name: e.target.value })} />
        <input type="text" style={{ width: 120 }} className="mono" placeholder="tipo"
          value={nuevo.entity_type}
          onChange={(e) => setNuevo({ ...nuevo, entity_type: e.target.value })} />
        <button disabled={!nuevo.name || !nuevo.entity_type} onClick={add}>Añadir</button>
      </div>
    </div>
  );
}

/** Estado del pipeline de entidades para el run seleccionado. */
function EntityStatusPanel() {
  const { jobId } = useCtx();
  if (!jobId) {
    return (
      <div className="card" style={{ marginBottom: 12 }}>
        <h3>Pipeline de entidades — estado del run</h3>
        <p className="proxy-tag">Selecciona un run arriba para ver si sus entidades están extraídas.</p>
      </div>
    );
  }
  return <EntityStatusInner jobId={jobId} />;
}

const PIPELINE_LABELS = {
  queued: "En cola",
  running: "Ejecutándose",
  done: "Completado",
  partial: "Completado con avisos",
  failed: "Error",
};

function EntityStatusInner({ jobId }) {
  const [tick, setTick] = useState(0);
  const q = useAsync(() => api.entitiesStatus(jobId), [jobId, tick]);
  const state = q.data?.pipeline?.state;
  // Mientras hay una pasada en cola o en marcha, refrescar solo.
  useEffect(() => {
    if (state !== "queued" && state !== "running") return;
    const t = setTimeout(() => setTick((x) => x + 1), 8000);
    return () => clearTimeout(t);
  }, [state, tick]);
  if (q.loading && !q.data) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data;
  const p = d.pipeline;
  const resolved = Object.entries(d.resolved || {}).map(([k, v]) => `${v} por ${k}`).join(" · ");
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <h3>Pipeline de entidades — estado del run</h3>
      {p && (
        <p className="proxy-tag">
          {PIPELINE_LABELS[p.state] || p.state}
          {p.steps ? ` · pasos: ${p.steps.join(", ")}` : ""}
          {p.reasons ? ` · disparado por: ${p.reasons.join(", ")}` : p.reason ? ` · disparado por: ${p.reason}` : ""}
          {p.seconds != null ? ` · ${p.seconds}s` : ""}
          {p.state === "failed" && p.error ? ` — ${p.error}` : ""}
          {(p.notes || []).length > 0 ? ` — ${p.notes.join("; ")}` : ""}
        </p>
      )}
      <div className="facts">
        <div className="fact"><div className="k">Definición</div><div className="v">{d.has_schema ? "✓" : "falta"}</div></div>
        <div className="fact"><div className="k">Menciones</div><div className="v num">{fmt(d.mentions)}</div></div>
        <div className="fact"><div className="k">Labels</div><div className="v num">{fmt(d.labels)}</div></div>
        <div className="fact"><div className="k">Entidades en queries</div><div className="v num">{fmt(d.query_entities)}</div></div>
        <div className="fact"><div className="k">Catálogo</div><div className="v num">{fmt(d.catalog_entries)}</div></div>
      </div>
      {resolved && <p className="proxy-tag">Resueltas: {resolved}</p>}
      {Object.keys(d.issues || {}).length > 0 && (
        <p className="proxy-tag">
          Propuestas generadas: {Object.entries(d.issues).map(([k, v]) => `${v} ${k}`).join(" · ")}
          {" "}— revísalas en <a href="#/incidencias">Incidencias</a> y <a href="#/firma">Firma</a>.
        </p>
      )}
      {d.mentions === 0 && !p && (
        <p className="proxy-tag">
          Se ejecuta solo cuando entran datos (rastreo completado, import de GSC,
          definición guardada) si el worker de entidades está levantado:
          <span className="mono" style={{ fontSize: 10.5 }}> docker compose --profile gliner up -d</span>
        </p>
      )}
    </div>
  );
}

function SourcesPanel() {
  // Estados honestos: nada de verdes decorativos — el chip de crawl solo
  // está OK si el proyecto tiene al menos un rastreo completado.
  const { clientJobs } = useCtx();
  const hasCompleted = (clientJobs || []).some((j) => j.status === "completed");
  return (
    <div className="card">
      <h3>Fuentes de datos</h3>
      <SourceRow name="Crawl" ok={hasCompleted}
        state={hasCompleted ? "Con rastreos completados" : "Sin rastreos completados aún"} />
      <SourceRow name="Sitemaps" state="Se activa por rastreo (opción «Leer los sitemaps»)" />
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
