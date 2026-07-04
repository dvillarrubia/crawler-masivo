import { useState } from "react";

import { useCtx } from "../App.jsx";
import { api } from "../api.js";
import { useAsync } from "../hooks.js";
import { EDGE_CLASS_INFO } from "../issueCatalog.js";
import { Blocked, ErrorBox, Pager, Spinner, fmt } from "../ui.jsx";

/** Grafo de enlaces con la clase de arista y el contexto DOM de cada uno. */
export default function LinksView() {
  const { jobId } = useCtx();
  const [page, setPage] = useState(1);

  if (!jobId) return <Blocked title="Sin run seleccionado" reason="Elige un run en la barra superior." />;

  const q = useAsync(() => api.links(jobId, { page, page_size: 100 }), [jobId, page]);

  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} />;

  return (
    <div>
      <div className="row between">
        <div>
          <h1 className="page-title">Enlaces</h1>
          <p className="page-sub">
            Todos los enlaces internos encontrados ({fmt(q.data.total)}), con su texto (anchor), en qué zona
            de la página estaban y qué papel juegan. La «clase» dice si el enlace viene del contenido
            (el que más vale), del menú, del footer, de un listado o de la paginación — pasa el ratón por
            la etiqueta para ver la explicación.
          </p>
        </div>
        <a href={api.exportUrl(jobId, "links")}><button className="secondary">Exportar CSV</button></a>
      </div>

      <div className="table-wrap" style={{ maxHeight: "68vh" }}>
        <table className="data">
          <thead>
            <tr>
              <th>Origen</th><th>Destino</th><th>Anchor</th>
              <th>Posición</th><th>Clase</th><th>Contexto DOM</th>
              <th>Follow</th><th>Tipo</th>
            </tr>
          </thead>
          <tbody>
            {q.data.items.map((l) => (
              <tr key={l.id}>
                <td className="cell-url" title={l.from_url}>{l.from_url}</td>
                <td className="cell-url" title={l.to_url}>{l.to_url}</td>
                <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {l.anchor_text || <i className="proxy-tag">sin anchor</i>}
                </td>
                <td><span className="tag">{l.link_position || "—"}</span></td>
                <td>{l.edge_class
                  ? <span className="tag" title={EDGE_CLASS_INFO[l.edge_class] || ""}>{l.edge_class}</span>
                  : <span title="El run se lanzó sin la capa de clasificación de enlaces">—</span>}</td>
                <td className="mono" style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis" }}
                    title={l.dom_container || ""}>
                  {l.dom_ancestor ? `<${l.dom_ancestor}>` : ""} {l.dom_container || ""}
                </td>
                <td>{l.follow === false ? <span className="tag">nofollow</span> : "sí"}</td>
                <td>{l.link_type || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager page={page} pages={q.data.pages} onPage={setPage} />
    </div>
  );
}
