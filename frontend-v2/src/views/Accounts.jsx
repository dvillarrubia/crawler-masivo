import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { ErrorBox, Spinner } from "../ui.jsx";

/** Cuentas GSC / Gemini / GA4 (credenciales) + sincronización de la serie
 *  diaria que alimenta el informe por fechas. */
export default function AccountsView() {
  const { clientId } = useCtx();
  return (
    <div>
      <h1 className="page-title">Cuentas y fuentes</h1>
      <p className="page-sub">
        Credenciales para las fuentes externas. <b>Search Console</b>: una service account de Google con acceso
        a la propiedad del cliente — trae clics, impresiones y consultas reales. <b>Analytics (GA4)</b>: sesiones,
        usuarios, conversiones e ingresos por canal. <b>Gemini</b>: la API key que paga los embeddings del análisis
        semántico — cada cliente usa la suya y asume su propio coste.
      </p>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
        <GscAccounts />
        <GeminiAccounts />
        <Ga4Accounts clientId={clientId} />
      </div>
      <DailySyncPanel clientId={clientId} />
    </div>
  );
}

/* -- Descubrir propiedades desde el JSON pegado (GSC + GA4) ----------------- */
function DiscoverProperties({ credentials, onPickGsc, onPickGa4 }) {
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const run = async () => {
    setBusy(true); setErr(null); setRes(null);
    let parsed;
    try { parsed = JSON.parse(credentials); }
    catch { setErr("El JSON de la service account no es válido"); setBusy(false); return; }
    try { setRes(await api.discoverProperties(parsed)); }
    catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div style={{ margin: "8px 0" }}>
      <button type="button" className="secondary" disabled={busy || !credentials}
        onClick={run}>{busy ? "Consultando a Google…" : "Descubrir propiedades"}</button>
      {err && <div className="alert" style={{ marginTop: 6 }}>{err}</div>}
      {res && (
        <div className="proxy-tag" style={{ marginTop: 8 }}>
          {res.service_account && <div style={{ marginBottom: 6 }}>Cuenta: <span className="mono">{res.service_account}</span></div>}
          {/* GSC */}
          <div style={{ marginBottom: 6 }}>
            <b>Search Console</b>{" "}
            {res.gsc.ok
              ? (res.gsc.properties.length
                  ? <span>({res.gsc.properties.length})</span>
                  : <span>— sin propiedades accesibles</span>)
              : <span style={{ color: "var(--chart-red)" }}>— {res.gsc.error}</span>}
            {res.gsc.ok && res.gsc.properties.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                {res.gsc.properties.map((p) => (
                  <button type="button" key={p}
                    className={onPickGsc ? "" : "secondary"}
                    style={{ fontSize: 11, padding: "2px 6px", cursor: onPickGsc ? "pointer" : "default" }}
                    onClick={() => onPickGsc && onPickGsc(p)} title={onPickGsc ? "Usar esta propiedad" : ""}>
                    {p}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* GA4 */}
          <div>
            <b>Analytics (GA4)</b>{" "}
            {res.ga4.ok
              ? (res.ga4.properties.length
                  ? <span>({res.ga4.properties.length})</span>
                  : <span>— sin propiedades accesibles</span>)
              : <span style={{ color: "var(--ink-muted)" }}>— {res.ga4.error}</span>}
            {res.ga4.ok && res.ga4.properties.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                {res.ga4.properties.map((p) => (
                  <button type="button" key={p.property_id}
                    className={onPickGa4 ? "" : "secondary"}
                    style={{ fontSize: 11, padding: "2px 6px", cursor: onPickGa4 ? "pointer" : "default" }}
                    onClick={() => onPickGa4 && onPickGa4(p.property_id)}
                    title={onPickGa4 ? "Usar esta propiedad" : ""}>
                    {p.display_name} <span className="mono">· {p.property_id.replace("properties/", "")}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* -- Cuentas GA4 ------------------------------------------------------------ */
function Ga4Accounts({ clientId }) {
  const cid = clientId || "_";
  const q = useAsync(() => api.ga4Accounts(cid), [cid]);
  const [name, setName] = useState("");
  const [propertyId, setPropertyId] = useState("");
  const [credentials, setCredentials] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    setBusy(true); setError(null);
    try {
      let parsed;
      try { parsed = JSON.parse(credentials); }
      catch { throw new Error("El JSON de la service account no es válido"); }
      await api.addGa4Account(cid, { name, property_id: propertyId, credentials_json: parsed });
      setName(""); setPropertyId(""); setCredentials("");
      q.reload();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <h3>Google Analytics 4</h3>
      {q.loading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {(q.data || []).map((a) => (
        <div className="row between" key={a.id} style={{ marginBottom: 6 }}>
          <span><b>{a.name}</b> <span className="proxy-tag mono">{a.property_id}</span></span>
          <button className="secondary" onClick={async () => {
            if (window.confirm(`¿Borrar la cuenta GA4 "${a.name}"?`)) {
              await api.deleteGa4Account(cid, a.id);
              q.reload();
            }
          }}>×</button>
        </div>
      ))}
      {q.data && q.data.length === 0 && <div className="proxy-tag" style={{ marginBottom: 8 }}>Sin cuentas GA4.</div>}

      <div style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 10 }}>
        {error && <div className="alert">{error}</div>}
        <div className="field">
          <label>Nombre</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="cliente-x GA4" />
        </div>
        <div className="field">
          <label>JSON de la service account</label>
          <textarea rows={4} className="mono" value={credentials}
            onChange={(e) => setCredentials(e.target.value)}
            placeholder='{"type": "service_account", ...}' />
          <div className="hint">La service account debe tener rol de lector en la propiedad GA4.</div>
        </div>
        {/* Pega el JSON y elige la propiedad de la lista, sin teclear el ID */}
        <DiscoverProperties credentials={credentials} onPickGa4={setPropertyId} />
        <div className="field">
          <label>Property ID {propertyId ? <span className="proxy-tag">· elegido</span> : null}</label>
          <input type="text" className="mono" value={propertyId}
            onChange={(e) => setPropertyId(e.target.value)} placeholder="properties/123456789 (o elígelo arriba)" />
          <div className="hint">Se rellena solo al pulsar una propiedad descubierta; también puedes escribirlo.</div>
        </div>
        <button disabled={busy || !name || !propertyId || !credentials} onClick={add}>Añadir cuenta GA4</button>
      </div>
    </div>
  );
}

function GscAccounts() {
  const q = useAsync(() => api.gscAccounts(), []);
  const [name, setName] = useState("");
  const [credentials, setCredentials] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    setBusy(true); setError(null);
    try {
      let parsed;
      try {
        parsed = JSON.parse(credentials);
      } catch {
        throw new Error("El JSON de la service account no es válido");
      }
      await api.addGscAccount({ name, credentials_json: parsed });
      setName(""); setCredentials("");
      q.reload();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <h3>Google Search Console</h3>
      {q.loading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {(q.data || []).map((a) => (
        <div className="row between" key={a.id} style={{ marginBottom: 6 }}>
          <span><b>{a.name}</b> <span className="proxy-tag">{new Date(a.created_at).toLocaleDateString("es")}</span></span>
          <button className="secondary" onClick={async () => {
            if (window.confirm(`¿Borrar la cuenta GSC "${a.name}"?`)) {
              await api.deleteGscAccount(a.id);
              q.reload();
            }
          }}>×</button>
        </div>
      ))}
      {q.data && q.data.length === 0 && <div className="proxy-tag" style={{ marginBottom: 8 }}>Sin cuentas GSC.</div>}

      <div style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 10 }}>
        {error && <div className="alert">{error}</div>}
        <div className="field">
          <label>Nombre</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="cliente-x GSC" />
        </div>
        <div className="field">
          <label>JSON de la service account</label>
          <textarea rows={5} className="mono" value={credentials}
            onChange={(e) => setCredentials(e.target.value)}
            placeholder='{"type": "service_account", ...}' />
          <div className="hint">La cuenta de servicio debe tener acceso a la propiedad en GSC.</div>
        </div>
        {/* Comprueba qué propiedades (GSC y GA4) ve esta credencial antes de guardar */}
        <DiscoverProperties credentials={credentials} />
        <button disabled={busy || !name || !credentials} onClick={add}>Añadir cuenta GSC</button>
      </div>
    </div>
  );
}

/* -- Sincronización de la serie diaria (alimenta el informe por fechas) ----- */
function isoDaysAgo(days) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

function DailySyncPanel({ clientId }) {
  const [source, setSource] = useState("gsc");
  const gsc = useAsync(() => api.gscAccounts(), []);
  const ga4 = useAsync(() => api.ga4Accounts(clientId || "_"), [clientId]);
  const configs = useAsync(
    () => (clientId ? api.syncConfigs(clientId) : Promise.resolve([])), [clientId]);
  const [accountId, setAccountId] = useState("");
  // Propiedades de la cuenta GSC elegida → desplegable en vez de teclear
  const gscProps = useAsync(
    () => (source === "gsc" && accountId
      ? api.gscProperties(accountId).catch(() => null) : Promise.resolve(null)),
    [source, accountId]);
  const [property, setProperty] = useState("");
  const [byPage, setByPage] = useState(true);
  const [daily, setDaily] = useState(true);
  const [from, setFrom] = useState(isoDaysAgo(90));
  const [to, setTo] = useState(isoDaysAgo(2));
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  if (!clientId) {
    return (
      <div className="card" style={{ marginTop: 16 }}>
        <h3>Sincronizar histórico diario</h3>
        <p className="proxy-tag">Selecciona un proyecto en la barra superior para sincronizar su serie diaria de GSC/GA4.</p>
      </div>
    );
  }

  const run = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      if (source === "gsc") {
        if (!accountId || !property) throw new Error("Elige cuenta GSC y propiedad");
        const r = await api.syncGscDaily(clientId, {
          gsc_account_id: accountId, property_url: property,
          start_date: from, end_date: to, by_page: byPage,
        });
        setResult(`GSC: ${r.rows} filas sincronizadas (${r.range[0]} → ${r.range[1]})`);
      } else {
        if (!accountId) throw new Error("Elige cuenta GA4");
        const r = await api.syncGa4Daily(clientId, {
          ga4_account_id: accountId, start_date: from, end_date: to,
        });
        setResult(`GA4: ${r.rows} filas sincronizadas (${r.range[0]} → ${r.range[1]})`);
      }
      // Si se pidió, deja la fuente programada para el cron diario.
      if (daily) {
        const prop = source === "gsc" ? property
          : (ga4.data || []).find((a) => a.id === accountId)?.property_id || accountId;
        await api.saveSyncConfig(clientId, {
          source, account_id: accountId, property: prop,
          by_page: byPage, enabled: true,
        });
        configs.reload();
      }
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const accounts = source === "gsc" ? (gsc.data || []) : (ga4.data || []);

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>Sincronizar histórico diario</h3>
      <p className="proxy-tag" style={{ marginTop: 0 }}>
        Descarga la serie <b>día a día</b> del rango elegido — es lo que alimenta el informe
        <b> Rendimiento → Por fechas</b>. La primera vez trae todo el histórico (hasta 16 meses en GSC);
        marca <b>"a diario"</b> y el cron refresca solo los días nuevos cada madrugada. Reemplaza el rango (idempotente).
      </p>
      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <label className="kpi-label">Fuente:</label>
        <button className={source === "gsc" ? "" : "secondary"}
          onClick={() => { setSource("gsc"); setAccountId(""); }}>Search Console</button>
        <button className={source === "ga4" ? "" : "secondary"}
          onClick={() => { setSource("ga4"); setAccountId(""); }}>Analytics (GA4)</button>
      </div>
      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          <option value="">— cuenta {source === "gsc" ? "GSC" : "GA4"} —</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        {source === "gsc" && gscProps.data && gscProps.data.properties && gscProps.data.properties.length > 0 ? (
          <select style={{ minWidth: 260 }} value={property}
            onChange={(e) => setProperty(e.target.value)}>
            <option value="">— elige propiedad —</option>
            {gscProps.data.properties.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        ) : source === "gsc" && (
          <input type="text" style={{ minWidth: 240 }} value={property}
            onChange={(e) => setProperty(e.target.value)}
            placeholder={gscProps.loading ? "cargando propiedades…" : "https://www.cliente.com/  (o sc-domain:cliente.com)"} />
        )}
      </div>
      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8 }}>
        <label className="kpi-label">Del</label>
        <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        <label className="kpi-label">al</label>
        <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        {source === "gsc" && (
          <label className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={byPage} onChange={(e) => setByPage(e.target.checked)} />
            También por URL (para seguir URLs vigiladas)
          </label>
        )}
        <label className="kpi-label" style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={daily} onChange={(e) => setDaily(e.target.checked)} />
          Sincronizar a diario (cron)
        </label>
        <button disabled={busy} onClick={run}>{busy ? "Sincronizando…" : "Sincronizar rango"}</button>
      </div>
      {error && <div className="alert">{error}</div>}
      {result && <div className="proxy-tag" style={{ color: "var(--chart-forest)" }}>✓ {result}</div>}

      <ScheduledSyncs clientId={clientId} configs={configs} />
    </div>
  );
}

/* -- Sincronizaciones programadas (lo que refresca el cron) ----------------- */
function ScheduledSyncs({ clientId, configs }) {
  const [busyId, setBusyId] = useState(null);
  const rows = configs.data || [];
  if (configs.loading) return <Spinner />;

  const toggle = async (c) => {
    await api.saveSyncConfig(clientId, {
      source: c.source, account_id: c.account_id, property: c.property,
      by_page: c.by_page, enabled: !c.enabled,
    });
    configs.reload();
  };
  const runNow = async (c) => {
    setBusyId(c.id);
    try { await api.runSyncConfig(clientId, c.id); } catch { /* el estado se ve en last_status */ }
    setBusyId(null);
    configs.reload();
  };
  const remove = async (c) => {
    if (!window.confirm(`¿Quitar del cron la sincronización de ${c.property}?`)) return;
    await api.deleteSyncConfig(clientId, c.id);
    configs.reload();
  };

  return (
    <div style={{ borderTop: "1px solid var(--hairline-soft)", marginTop: 12, paddingTop: 10 }}>
      <div className="row between">
        <b>Sincronización automática diaria</b>
        <span className="proxy-tag">el cron corre cada madrugada (05:00 UTC)</span>
      </div>
      {rows.length === 0 && (
        <p className="proxy-tag">Nada programado. Marca "Sincronizar a diario" arriba para que el cron mantenga el histórico al día.</p>
      )}
      {rows.length > 0 && (
        <table className="data" style={{ marginTop: 8 }}>
          <thead>
            <tr><th>Fuente</th><th>Propiedad</th><th>Último resultado</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} style={{ opacity: c.enabled ? 1 : 0.5 }}>
                <td><span className="tag">{c.source.toUpperCase()}</span></td>
                <td className="cell-url mono" title={c.property}>{c.property}</td>
                <td className="proxy-tag">
                  {c.last_status
                    ? <span style={{ color: c.last_status.startsWith("error") ? "var(--chart-red)" : "var(--chart-forest)" }}>{c.last_status}</span>
                    : "— nunca ejecutado —"}
                  {c.last_synced_at ? <span> · {new Date(c.last_synced_at).toLocaleString("es")}</span> : null}
                </td>
                <td className="num" style={{ whiteSpace: "nowrap" }}>
                  <button className="secondary" disabled={busyId === c.id}
                    onClick={() => runNow(c)} title="Ejecutar ahora">{busyId === c.id ? "…" : "▷"}</button>{" "}
                  <button className="secondary" onClick={() => toggle(c)}
                    title={c.enabled ? "Pausar" : "Reanudar"}>{c.enabled ? "⏸" : "▶"}</button>{" "}
                  <button className="secondary" onClick={() => remove(c)} title="Quitar">×</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function GeminiAccounts() {
  const q = useAsync(() => api.geminiAccounts(), []);
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    setBusy(true); setError(null);
    try {
      await api.addGeminiAccount({ name, api_key: apiKey });
      setName(""); setApiKey("");
      q.reload();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <h3>Gemini (embeddings)</h3>
      {q.loading && <Spinner />}
      {q.error && <ErrorBox error={q.error} />}
      {(q.data || []).map((a) => (
        <div className="row between" key={a.id} style={{ marginBottom: 6 }}>
          <span><b>{a.name}</b> <span className="proxy-tag">{new Date(a.created_at).toLocaleDateString("es")}</span></span>
          <button className="secondary" onClick={async () => {
            if (window.confirm(`¿Borrar la cuenta Gemini "${a.name}"?`)) {
              await api.deleteGeminiAccount(a.id);
              q.reload();
            }
          }}>×</button>
        </div>
      ))}
      {q.data && q.data.length === 0 && <div className="proxy-tag" style={{ marginBottom: 8 }}>Sin cuentas Gemini.</div>}

      <div style={{ borderTop: "1px solid var(--hairline-soft)", paddingTop: 10 }}>
        {error && <div className="alert">{error}</div>}
        <div className="field">
          <label>Nombre</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="cliente-x Gemini" />
        </div>
        <div className="field">
          <label>API key</label>
          <input type="text" className="mono" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder="AIza…" />
        </div>
        <button disabled={busy || !name || !apiKey} onClick={add}>Añadir cuenta Gemini</button>
      </div>
    </div>
  );
}
