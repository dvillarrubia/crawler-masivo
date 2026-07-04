import { useState } from "react";

import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { ErrorBox, Spinner } from "../ui.jsx";

/** Cuentas GSC (service account JSON) y Gemini (API key) — paridad legacy. */
export default function AccountsView() {
  return (
    <div>
      <h1 className="page-title">Cuentas</h1>
      <p className="page-sub">Credenciales por cliente: cada uno paga sus embeddings y accede a su GSC.</p>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
        <GscAccounts />
        <GeminiAccounts />
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
        <button disabled={busy || !name || !credentials} onClick={add}>Añadir cuenta GSC</button>
      </div>
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
