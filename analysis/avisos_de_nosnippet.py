"""Avisos de `data-nosnippet` (decision 170, Ola 7.2).

Lo mide el spider en `html_meta.data_nosnippet` (`shared/data_nosnippet.py`).
Dos tipos, agrupados por (host, plantilla) como los de la decision 108: lo que
se pone en una plantilla esta en todas sus paginas (36, 53, 64, 73), y la fila
va anclada en la URL MENOR del grupo (88), con `paginas_afectadas` y ejemplos.

- `data_nosnippet_contenido_principal` (`warning`): en una pagina indexable,
  `data-nosnippet` cubre el H1 o la mayor parte del contenido principal. Google
  no usa ese texto en el fragmento ni, con las mismas directivas, en AI
  Overviews y AI Mode (137): la pagina sale con un fragmento de lo que sobra (el
  menu, el pie) o sin el. Puede ser deliberado (contenido de pago): la ficha
  pide confirmarlo. Una fila por (host, plantilla): el motivo de cada pagina
  (`h1`, `contenido`, `h1_y_contenido`) va como recuento en `motivos`, no en la
  clave; si no, la misma plantilla salia en dos o tres filas segun cada pagina
  quedara por encima o por debajo del umbral.
- `data_nosnippet_ignorado` (`info`): el atributo en un elemento que no es
  `span`, `div` ni `section`, fuera de lo que Google documenta: no se puede
  contar con que esconda nada. No se afirma que Google lo ignore (su propia
  documentacion lo pone en elementos propios y en un `nav`), solo que no lo
  garantiza; por eso es `info` y no cuenta para el otro aviso.

Solo HTML interno 2xx con respuesta valida (103) e indexable (47). Una pagina
con `nosnippet` o `max-snippet:0` ya no da fragmento entero y ya lleva
`nosnippet_en_indexable`: no se le suma este. NULL = no medido (rastreo
anterior): no se afirma nada (67, 89).
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select

from analysis.avisos_de_html import _Grupos, _plantilla
from shared.data_nosnippet import UMBRAL_PRINCIPAL, esconde_lo_principal
from shared.indexabilidad import limite_de_fragmento

logger = logging.getLogger(__name__)

TIPOS = ("data_nosnippet_contenido_principal", "data_nosnippet_ignorado")


def avisar(analizador) -> None:
    """Va despues de `analyze_indexability`: necesita `Url.indexable`."""
    from analysis.analyzer import _respuesta_valida
    from shared.models import HtmlMeta, Url

    s = analizador.session
    filas = s.execute(
        select(
            Url.id, Url.url, Url.host, Url.indexable,
            HtmlMeta.meta_robots, HtmlMeta.meta_robots_googlebot, HtmlMeta.x_robots_tag,
            HtmlMeta.data_nosnippet,
        )
        .join(HtmlMeta, HtmlMeta.url_id == Url.id)
        .where(
            Url.job_id == analizador.job_id,
            Url.is_html.is_(True),
            Url.is_internal.is_(True),
            Url.status_code >= 200,
            Url.status_code < 300,
            Url.indexable.is_(True),
            _respuesta_valida(),
            # En SQL: casi todas las paginas llevan `{"elementos": 0}`, y en
            # un censo de un millon no se traen para descartarlas aqui.
            or_(HtmlMeta.data_nosnippet["elementos"].as_integer() > 0,
                HtmlMeta.data_nosnippet["ignorados"].as_integer() > 0),
        )
    ).all()

    principal, ignorado = _Grupos(), _Grupos()
    etiquetas: dict[tuple, set[str]] = {}
    medidas: dict[tuple, list[float]] = {}
    motivos: dict[tuple, dict[str, int]] = {}
    for f in filas:
        datos = f.data_nosnippet if isinstance(f.data_nosnippet, dict) else None
        if not datos or not (datos.get("elementos") or datos.get("ignorados")):
            continue
        sin_fragmento, _ = limite_de_fragmento(
            meta_robots={"robots": f.meta_robots, "googlebot": f.meta_robots_googlebot},
            x_robots=f.x_robots_tag)
        if sin_fragmento:
            continue
        host = (f.host or "").lower()
        plantilla = _plantilla(f.url)
        motivo = esconde_lo_principal(datos)
        if motivo:
            clave = (host, plantilla)
            principal.anotar(clave, f.url, f.id)
            cuenta = motivos.setdefault(clave, {})
            cuenta[motivo] = cuenta.get(motivo, 0) + 1
            if isinstance(datos.get("principal"), (int, float)):
                medidas.setdefault(clave, []).append(float(datos["principal"]))
        if datos.get("ignorados"):
            clave = (host, plantilla)
            ignorado.anotar(clave, f.url, f.id)
            etiquetas.setdefault(clave, set()).update(datos.get("etiquetas_ignoradas") or [])

    def _detalle_principal(k):
        fr = medidas.get(k) or []
        d = {"host": k[0], "plantilla": k[1], "motivos": dict(sorted(motivos[k].items())),
             "umbral_contenido": UMBRAL_PRINCIPAL}
        if fr:
            d["fraccion_contenido_mediana"] = sorted(fr)[len(fr) // 2]
        return d

    principal.emitir(analizador, "data_nosnippet_contenido_principal",
                     lambda k: "warning", _detalle_principal)
    ignorado.emitir(analizador, "data_nosnippet_ignorado", lambda k: "info",
                    lambda k: {"host": k[0], "plantilla": k[1],
                               "etiquetas": sorted(etiquetas.get(k) or [])})
    analizador._flush_issues()


__all__ = ["TIPOS", "avisar"]
