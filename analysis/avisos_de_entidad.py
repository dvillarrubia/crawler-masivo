"""`portada_sin_entidad` (decision 229, #199 parte sin modelo).

La portada es donde Google y los buscadores de IA buscan quien esta detras
del sitio: el nombre, el logo, los perfiles (`sameAs`). Lo dicen los datos
estructurados `Organization` (o un subtipo: `LocalBusiness`, `Hotel`...) o
`Person` en un sitio personal. Sin ellos, la entidad se deduce del texto, o no
se deduce.

Una fila por host, anclada en su portada (la raiz; si la raiz no es una pagina
200 indexable, la de un idioma): es un hallazgo del sitio, no de cada pagina
(36). Basta con que UNA portada del host declare la entidad, en cualquier
bloque y a cualquier profundidad (el `publisher` de un `WebSite` cuenta), y en
cualquier formato.

`info`: Google no lo exige, y si el bloque lo inyecta JavaScript (Tag Manager)
en un rastreo sin render, aqui no se ve. Lo dice la ficha.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from urllib.parse import urlsplit

from sqlalchemy import select

from shared.schema_entidades import TIPOS_DE_ENTIDAD, tipos_de
from shared.soft404 import es_portada

logger = logging.getLogger(__name__)

TIPOS = ("portada_sin_entidad",)


def avisar(analizador) -> None:
    from analysis.analyzer import _respuesta_valida
    from shared.models import StructuredData, Url

    s = analizador.session
    candidatas = s.execute(
        select(Url.id, Url.url, Url.host, Url.path).where(
            Url.job_id == analizador.job_id,
            Url.is_internal.is_(True),
            Url.is_html.is_(True),
            Url.status_code == 200,
            Url.indexable.is_(True),
            _respuesta_valida(),
            # Un prefiltro barato; la regla de verdad es `es_portada`.
            Url.path.notlike("%/%/%/%"),
        )
    ).all()
    portadas = [f for f in candidatas if es_portada(f.path or urlsplit(f.url).path)]
    if not portadas:
        return

    tipos_por_url: dict[int, set[str]] = defaultdict(set)
    for url_id, raw in s.execute(
        select(StructuredData.url_id, StructuredData.raw)
        .where(StructuredData.url_id.in_([f.id for f in portadas]))
    ).all():
        tipos_por_url[url_id] |= tipos_de(raw)

    por_host: dict[str, list] = defaultdict(list)
    for f in portadas:
        por_host[(f.host or urlsplit(f.url).hostname or "").lower()].append(f)

    for host, filas in sorted(por_host.items()):
        if any(tipos_por_url[f.id] & TIPOS_DE_ENTIDAD for f in filas):
            continue
        # La raiz antes que la de un idioma; entre iguales, la URL menor (88).
        filas.sort(key=lambda f: (len([t for t in (f.path or "/").split("/") if t]), f.url))
        ancla = filas[0]
        analizador._add_issue(
            ancla.id, "portada_sin_entidad", "info",
            {"host": host, "portadas": [f.url for f in filas[:5]],
             "tipos_en_la_portada": sorted(tipos_por_url[ancla.id])[:20]},
        )
    analizador._flush_issues()


__all__ = ["TIPOS", "avisar"]
