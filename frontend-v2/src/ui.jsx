/** Componentes compartidos: patrón bloqueado/vacío, KPIs, pills, drawer, modal. */

export function Spinner({ label = "Cargando…" }) {
  return <div className="spin">{label}</div>;
}

export function ErrorBox({ error }) {
  return <div className="alert">Error: {String(error.message || error)}</div>;
}

/** Patrón de tres estados de la Consola: dato real / bloqueado / vacío legítimo. */
export function Blocked({ title, reason, cta }) {
  return (
    <div className="blocked">
      <div className="title">{title}</div>
      <div>{reason}</div>
      {cta && <div className="cta">{cta}</div>}
    </div>
  );
}

export function EmptyClean({ children }) {
  return <div className="empty-clean">✓ {children}</div>;
}

export function Kpi({ label, value, delta, proxy }) {
  return (
    <div className="card">
      <div className="kpi-label">{label}</div>
      <div className="display-num num">{value ?? "—"}</div>
      {delta != null && (
        <div className={`kpi-delta ${delta > 0 ? "up" : delta < 0 ? "down" : ""}`}>
          {delta > 0 ? "▲" : delta < 0 ? "▼" : "="} {Math.abs(delta).toLocaleString("es")}
          <span className="proxy-tag"> vs run anterior</span>
        </div>
      )}
      {proxy && <div className="proxy-tag">{proxy}</div>}
    </div>
  );
}

export function StatusPill({ group }) {
  const cls = ["2xx", "3xx", "4xx", "5xx"].includes(group) ? `s${group}` : "sother";
  return <span className={`pill ${cls}`}>{group || "—"}</span>;
}

export function Severity({ level }) {
  return (
    <span className={`sev ${level}`}>
      <span className="dot" />
      {level}
    </span>
  );
}

export function Drawer({ onClose, children }) {
  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <div className="drawer">
        <button className="close" onClick={onClose} aria-label="Cerrar">×</button>
        {children}
      </div>
    </>
  );
}

export function Modal({ title, onClose, children }) {
  return (
    <div className="modal-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <button className="close" onClick={onClose} aria-label="Cerrar">×</button>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Pager({ page, pages, onPage }) {
  if (!pages || pages <= 1) return null;
  return (
    <div className="pager">
      <button className="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>←</button>
      <span className="num">{page} / {pages}</span>
      <button className="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>→</button>
    </div>
  );
}

export function BarRow({ label, value, max, color }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="bar-row">
      <span style={{ width: 110, flex: "none" }}>{label}</span>
      <span className="bar"><i style={{ width: `${pct}%`, background: color }} /></span>
      <span className="num" style={{ width: 70, textAlign: "right" }}>{value.toLocaleString("es")}</span>
    </div>
  );
}

export const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("es"));
