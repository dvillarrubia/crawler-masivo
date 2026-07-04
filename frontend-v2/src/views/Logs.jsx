import { Blocked } from "../ui.jsx";

/** D4: vista Logs en estado bloqueado-por-fuente. No se finge nada. */
export default function LogsView() {
  return (
    <div>
      <h1 className="page-title">Logs de servidor</h1>
      <p className="page-sub">Crawl budget real, bots de IA, latencia de Googlebot, time-to-index.</p>
      <Blocked
        title="Fuente no conectada"
        reason={
          "No hay ingesta de logs de servidor en este ciclo. Cuando exista, esta vista mostrará hits de bots, " +
          "presupuesto de rastreo real y detección de crawlers de IA. Modo degradado futuro: GSC Crawl Stats."
        }
      />
    </div>
  );
}
