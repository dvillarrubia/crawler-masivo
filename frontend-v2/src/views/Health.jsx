import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { Blocked, EmptyClean, ErrorBox, Spinner, fmt } from "../ui.jsx";

/** Salud del proyecto (Consola): titular editorial del diff + alertas +
 *  robots + flapping + watchlist. */
export default function HealthView() {
  const { clientId, clientJobs } = useCtx();

  if (!clientId) {
    return <Blocked title="Salud del proyecto"
      reason="Esta vista compara runs del mismo proyecto. Selecciona un proyecto en la barra superior." />;
  }

  const completed = clientJobs.filter((j) => j.status === "completed");
  if (completed.length < 2) {
    return <Blocked title="Salud del proyecto"
      reason={`Hacen falta al menos 2 rastreos completados de "${clientId}" para comparar (hay ${completed.length}).`}
      cta={<a href="#/jobs"><button>Ir a Rastreos</button></a>} />;
  }

  const [current, previous] = completed; // ya vienen desc por created_at
  return <HealthBody clientId={clientId} current={current} previous={previous} />;
}

function HealthBody({ clientId, current, previous }) {
  const diffQ = useAsync(
    () => api.diff({ job_a: previous.id, job_b: current.id }),
    [previous.id, current.id],
  );
  const flapQ = useAsync(() => api.flapping({ client_id: clientId, last_n: 6 }), [clientId]);
  const watchQ = useAsync(
    () => api.issues(current.id, { issue_type: "watchlist_check_failed", page_size: 50 }),
    [current.id],
  );

  if (diffQ.loading) return <Spinner />;
  if (diffQ.error) {
    return diffQ.error.status === 409
      ? <Blocked title="Runs no comparables" reason={diffQ.error.message} />
      : <ErrorBox error={diffQ.error} />;
  }

  const d = diffQ.data;
  const robotsChanged = d.robots_changes.filter((r) => r.changed);
  const statusChanges = d.changes.status;
  const flapping = flapQ.data || [];
  const watchFails = watchQ.data ? watchQ.data.items : [];

  return (
    <div>
      <h1 className="page-title">Salud del proyecto</h1>
      <p className="page-sub">
        «{previous.name}» → «{current.name}» · {new Date(current.created_at).toLocaleDateString("es")}
      </p>

      <div className="card muted" style={{ marginBottom: 14 }}>
        <div className="editorial">{headline(d, robotsChanged, watchFails, flapping)}</div>
      </div>

      {watchFails.length > 0 && (
        <div className="alert">
          <b>Watchlist:</b> {watchFails.length} URL(s) de negocio incumplen su check.
          {" "}{watchFails.slice(0, 3).map((i) => i.url).join(" · ")}
        </div>
      )}
      {robotsChanged.map((r) => (
        <div className="alert warn" key={r.host}>
          <b>robots.txt cambió en {r.host}</b>
          <pre className="diff" style={{ marginTop: 8 }}>
            {r.diff.split("\n").map((line, i) => (
              <div key={i} className={line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : ""}>{line}</div>
            ))}
          </pre>
        </div>
      ))}

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
        <HealthKpi label="URLs nuevas" value={d.new_urls} tone={null} />
        <HealthKpi label="URLs desaparecidas" value={d.gone_urls} tone={d.gone_urls > 0 ? "warn" : null} />
        <HealthKpi label="Cambios de status" value={statusChanges} tone={statusChanges > 0 ? "warn" : null} />
        <HealthKpi label="Cambios de indexabilidad" value={d.changes.indexable} tone={d.changes.indexable > 0 ? "bad" : null} />
        <HealthKpi label="Titles cambiados" value={d.changes.title} tone={null} />
        <HealthKpi label="Contenido cambiado" value={d.changes.content} tone={null} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 14 }}>
        <div className="card">
          <h3>Flapping (últimos 6 runs)</h3>
          {flapQ.loading && <Spinner />}
          {!flapQ.loading && flapping.length === 0 &&
            <EmptyClean>Nada alterna entre estados — estable.</EmptyClean>}
          {flapping.slice(0, 20).map((f, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <div className="cell-url" title={f.url}>{f.url}</div>
              <div className="proxy-tag mono">
                {f.field}: {f.sequence.map((s) => String(s.value)).join(" → ")}
              </div>
            </div>
          ))}
        </div>
        <div className="card">
          <h3>Vigilancia</h3>
          {watchQ.loading && <Spinner />}
          {!watchQ.loading && watchFails.length === 0 &&
            <EmptyClean>Todas las URLs de la watchlist pasan sus checks.</EmptyClean>}
          {watchFails.map((i) => (
            <div key={i.id} style={{ marginBottom: 8 }}>
              <div className="cell-url">{i.url}</div>
              <div className="proxy-tag mono">
                {i.details && i.details.label ? `${i.details.label} · ` : ""}
                {i.details ? (i.details.reasons || []).join(", ") : ""}
              </div>
            </div>
          ))}
          <div style={{ marginTop: 10 }}>
            <a href="#/config"><button className="secondary">Gestionar watchlist</button></a>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <a href="#/diff"><button className="secondary">Ver diff completo →</button></a>
      </div>
    </div>
  );
}

function HealthKpi({ label, value, tone }) {
  return (
    <div className="card">
      <div className="kpi-label">{label}</div>
      <div className="display-num num"
        style={{ color: tone === "bad" ? "var(--error)" : tone === "warn" ? "var(--chart-amber)" : undefined }}>
        {fmt(value)}
      </div>
    </div>
  );
}

/** Titular editorial honesto: lo más grave primero. */
function headline(d, robotsChanged, watchFails, flapping) {
  if (watchFails.length > 0)
    return `${watchFails.length} página(s) de negocio han dejado de estar sanas — revísalas antes que nada.`;
  if (robotsChanged.length > 0)
    return `El robots.txt cambió desde el último rastreo. Los desastres de indexación silenciosos empiezan aquí.`;
  if (d.changes.indexable > 0)
    return `${fmt(d.changes.indexable)} URL(s) cambiaron de indexabilidad entre runs.`;
  if (d.changes.status > 0)
    return `${fmt(d.changes.status)} URL(s) cambiaron de status; ${fmt(d.gone_urls)} desaparecieron y ${fmt(d.new_urls)} son nuevas.`;
  if (flapping.length > 0)
    return `${flapping.length} URL(s) alternan entre estados (flapping) — inestabilidad de servidor o de plantilla.`;
  return "Sin cambios estructurales relevantes entre los dos últimos rastreos.";
}
