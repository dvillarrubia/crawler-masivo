"""Estados y filtro del modulo `canibalizacion` del analisis enriquecido
(#18, #169 punto 3, decision 175).

**La unidad es el grupo, no la URL.** Lo que se juzga es una busqueda y las
URLs que se reparten sus clics: el aviso `canibalizacion_por_consulta` de la
decision 157 (`analysis/oportunidades_gsc.py`). Una misma URL esta en varios
grupos (en Lopesan, 459 grupos sobre 167 URLs ancla), asi que no puede ser
la clave de nada. Por eso el modulo es `por_grupo` (`shared/enriquecido.py`):

- `grupos_canibalizacion` (el `filtrar_urls` del modulo) devuelve ids de
  INCIDENCIA, no de URL: los avisos del job con alguna URL dentro del alcance,
  los de mas clics en juego primero y como mucho `MAX_GRUPOS`.
- `estados_canibalizacion` (su `construir_estados`) da un `Estado` por aviso,
  con `ambito="consulta"` y `url_id` = la URL donde cuelga el aviso (su ancla:
  la menor del grupo, estable entre censos, no la de mas clics, que es justo
  lo que se invierte en una canibalizacion). El resto de URLs va en el estado.
  En `decisions`, el grupo se reconoce por `url_id` + `estado_enviado.busqueda`
  (o por `clave`, que es el hash del estado).

Ni este fichero ni el modulo llaman a nadie: solo leen la BD.
"""

from __future__ import annotations

import math
import uuid

from shared.decisiones import Estado

TIPO_AVISO = "canibalizacion_por_consulta"
# Grupos que se preguntan como mucho, los de mas clics en juego primero.
# Medido el 24-oct-2026 en Lopesan (censo 1e7ea651): 459 grupos; los 300
# primeros llevan 26.017 de sus 27.993 clics repartidos (el 93 %) y el ultimo
# tiene 16. Con todos, la estimacion apenas cambia (0,02 $), pero una lista de
# 459 no la revisa nadie.
MAX_GRUPOS = 300
# Caracteres del contenido principal POR PAGINA. El contenido medio de esas
# paginas son 4.777 caracteres: sin recorte, un grupo de dos paginas costaria
# diez veces mas y lo que decide si son sustitutas esta en titulo, H1 y el
# primer parrafo.
CARACTERES_CONTENIDO = 500
# La regla de la 157 exige un 20 % de los clics a cada URL: no puede haber mas
# de cinco. El tope es defensivo, por si alguien baja ese minimo.
MAX_PAGINAS = 5


def _trozos(lista: list, n: int = 500):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def redondear(n) -> int | None:
    """Dos cifras significativas: 874 -> 870, 9.299 -> 9.300, 16 -> 16. El
    modelo no necesita el clic exacto, y un recrawl con un trafico parecido da
    el MISMO estado y sale de la cache (decision 84; #169 pide tramos)."""
    if n is None:
        return None
    n = float(n)
    if n < 100:
        return int(round(n))
    paso = 10 ** (int(math.floor(math.log10(n))) - 1)
    return int(round(n / paso) * paso)


def _urls_de_aviso(details) -> list[dict]:
    return [u for u in ((details or {}).get("urls") or []) if isinstance(u, dict) and u.get("url")][:MAX_PAGINAS]


def _ids_por_url(session, job_id, urls: set[str]) -> dict[str, int]:
    from shared.models import Url

    salida: dict[str, int] = {}
    lista = sorted(urls)
    for trozo in _trozos(lista):
        for uid, url in session.query(Url.id, Url.url).filter(
                Url.job_id == _uuid(job_id), Url.url.in_(trozo)):
            salida[url] = uid
    return salida


def grupos_canibalizacion(session, job_id, url_ids: list[int]) -> list[int]:
    """Los avisos `canibalizacion_por_consulta` del job con ALGUNA de sus URLs
    en el alcance (con una carpeta, el grupo entra si una de sus paginas esta
    en ella; por defecto, si alguna es indexable: el ancla puede ser una
    canonicalizada), por clics en juego (o impresiones, si no tiene clics) y
    como mucho `MAX_GRUPOS`. Devuelve ids de incidencia (modulo `por_grupo`)."""
    from shared.models import Issue

    if not url_ids:
        return []
    avisos = session.query(Issue.id, Issue.details).filter(
        Issue.job_id == _uuid(job_id), Issue.issue_type == TIPO_AVISO).all()
    if not avisos:
        return []
    ids = _ids_por_url(session, job_id, {u["url"] for _, d in avisos for u in _urls_de_aviso(d)})
    dentro = set(url_ids)
    elegidos = []
    for iid, d in avisos:
        if any(ids.get(u["url"]) in dentro for u in _urls_de_aviso(d)):
            elegidos.append((-(int((d or {}).get("clics") or 0)), -(int((d or {}).get("impresiones") or 0)),
                             str((d or {}).get("consulta") or ""), iid))
    elegidos.sort()
    return [iid for *_, iid in elegidos[:MAX_GRUPOS]]


def estados_canibalizacion(session, issue_ids: list[int]) -> list[Estado]:
    """Un estado por aviso, en el orden recibido:

    - `busqueda` y el criterio del reparto (`clics`, o `impresiones` si la
      busqueda no tiene clics);
    - por pagina, en el orden del aviso (la de mas parte primero): ruta (no la
      URL entera: con y sin www son el MISMO estado, decision 169), parte de la
      busqueda en %, clics, impresiones y posicion redondeados, si es
      indexable, titulo, primer H1 que se pinta y `CARACTERES_CONTENIDO` del
      contenido principal sin plantilla (como `estados_titulo_h1_contenido`).

    Un aviso que ya no existe (el censo se re-analizo a mitad) no da estado.
    """
    from sqlalchemy import func

    from shared.models import Heading, HtmlMeta, Issue, PageContent

    avisos = {}
    for trozo in _trozos(list(issue_ids)):
        for iid, job_id, url_id, d in session.query(
                Issue.id, Issue.job_id, Issue.url_id, Issue.details).filter(Issue.id.in_(trozo)):
            avisos[iid] = (job_id, url_id, d or {})
    # Las URLs de cada aviso, por job (un aviso solo nombra URLs de su censo).
    por_job: dict = {}
    for job_id, _, d in avisos.values():
        por_job.setdefault(job_id, set()).update(u["url"] for u in _urls_de_aviso(d))
    ids: dict = {}
    for job_id, urls in por_job.items():
        ids[job_id] = _ids_por_url(session, job_id, urls)
    todos = sorted({uid for m in ids.values() for uid in m.values()})
    titulos: dict[int, str] = {}
    h1: dict[int, str] = {}
    contenido: dict[int, str] = {}
    for trozo in _trozos(todos):
        for uid, t in session.query(HtmlMeta.url_id, HtmlMeta.title).filter(HtmlMeta.url_id.in_(trozo)):
            titulos[uid] = t or ""
        for uid, texto in (session.query(Heading.url_id, Heading.text)
                           .filter(Heading.url_id.in_(trozo), Heading.tag == "h1",
                                   Heading.oculto.isnot(True))
                           .order_by(Heading.url_id, Heading.position)):
            h1.setdefault(uid, texto or "")
        for uid, c in session.query(
                PageContent.url_id,
                func.substr(PageContent.content_text, 1, CARACTERES_CONTENIDO)).filter(
                PageContent.url_id.in_(trozo)):
            contenido[uid] = c or ""

    from urllib.parse import urlsplit

    estados = []
    for iid in issue_ids:
        if iid not in avisos:
            continue
        job_id, ancla, d = avisos[iid]
        paginas = []
        for u in _urls_de_aviso(d):
            uid = ids.get(job_id, {}).get(u["url"])
            partes = urlsplit(u["url"])
            ruta = partes.path or "/"
            if partes.query:
                ruta += "?" + partes.query
            paginas.append({
                "ruta": ruta,
                "parte_pct": int(round(float(u.get("parte") or 0) * 20) * 5),
                "clics": redondear(u.get("clics")),
                "impresiones": redondear(u.get("impresiones")),
                "posicion": (max(1, int(round(float(u["posicion"]))))
                             if u.get("posicion") is not None else None),
                "indexable": u.get("indexable"),
                "titulo": titulos.get(uid, ""), "h1": h1.get(uid, ""),
                "contenido": contenido.get(uid, ""),
            })
        estados.append(Estado(
            datos={"busqueda": str(d.get("consulta") or ""),
                   "reparto_por": d.get("reparto_por") or "clics",
                   "paginas": paginas},
            ambito="consulta", url_id=ancla))
    return estados


def efecto_en_el_aviso(sustitutas, recomendacion) -> dict | None:
    """Lo que el juicio haria con el aviso de la 157 (#169: «rebaja o confirma
    la incidencia»). **Apagado mientras las preguntas no tengan umbral**:
    solo actua con decisiones `decidido`, y sin umbral calibrado (#8) ninguna
    lo es (decision 84), asi que hoy siempre devuelve None. Nadie lo llama
    aun: emitir o rebajar avisos desde un modulo esta pendiente (decision 85).

    - sustitutas «no» decidido -> rebajar: es un reparto legitimo.
    - sustitutas «si» decidido -> confirmar, con la recomendacion si tambien
      esta decidida (si no, la recomendacion queda sin decir).
    """
    if sustitutas is None or getattr(sustitutas, "estado", None) != "decidido":
        return None
    if sustitutas.respuesta == "false":
        return {"efecto": "rebajar", "motivo": "reparto_legitimo"}
    if sustitutas.respuesta != "true":
        return None
    salida = {"efecto": "confirmar", "recomendacion": None}
    if recomendacion is not None and getattr(recomendacion, "estado", None) == "decidido":
        salida["recomendacion"] = recomendacion.respuesta
    return salida
