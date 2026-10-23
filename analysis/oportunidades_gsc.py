"""Search Console: lo que PUEDE traer trafico (decision 157, #166).

Despues de lo que cuesta trafico (`avisos_de_gsc`, decision 142), y con las
mismas reglas: la ventana de 90 dias que acaba el dia del censo, y nada
afirmado sin datos que lo cubran. Lee lo que baja `shared/gsc_detalle.py`
(busquedas, dispositivos y sitemaps segun Google).

- ``oportunidad_posicion`` (info): indexable, posicion media 4-20 con
  impresiones de sobra, y poco enlazada (PageRank en el cuartil bajo de las
  indexables, o a 4 clics o mas de la home). Propone hasta cinco paginas desde
  las que enlazarla: indexables, con mas PageRank, que no la enlazan ya, y
  primero las que salen por sus mismas busquedas; si no, las de su plantilla.
- ``ctr_bajo_para_posicion`` (info): menos clics de los que da el cuartil bajo
  de la curva del PROPIO sitio para las posiciones de sus busquedas (sin las
  de marca, si el cliente las declara). No dice por
  que: puede ser la SERP (AI Overviews, anuncios), que no vemos. Lleva al lado
  lo que ya sabemos del snippet (title generico o cortado, description ausente
  o igual al title).
- ``canibalizacion_por_consulta`` (una por busqueda; warning desde 100 clics,
  info por debajo): dos o mas URLs del sitio se reparten los clics de una
  busqueda con al menos 10 (o, sin clics, las impresiones de una con 1.000 o
  mas), cada una con al menos el 20 %. Sin las que son la misma URL o del
  mismo grupo canonical/redireccion, ni la variante con parametros y su URL
  limpia, ni las alternativas de idioma unidas por hreflang, ni la home en
  busquedas de marca. No
  decide si hay que fusionar o diferenciar: eso es #18.
- ``sitemap_no_enviado_a_google`` (info), ``sitemap_no_leido_por_google``
  (warning) y ``sitemap_con_errores_en_google`` (warning): de los sitemaps que
  leyo el rastreo, el que no esta enviado en Search Console, el que Google no
  descarga desde hace mas de 30 dias y el que tiene errores. «No enviado» no
  es «no leido»: Google tambien los lee del robots.txt y la API solo lista los
  enviados.

Y en `jobs.gsc_resumen.oportunidades`: la curva de CTR y el rendimiento por
plantilla y por profundidad (que secciones rinden y cuales no).
"""

from __future__ import annotations

import logging
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

TIPOS = ("oportunidad_posicion", "ctr_bajo_para_posicion", "canibalizacion_por_consulta",
         "sitemap_no_enviado_a_google", "sitemap_no_leido_por_google", "sitemap_con_errores_en_google")

MAX_FUENTES = 5
# Oportunidades y CTR bajos que se avisan como mucho, los de mas clics por
# ganar primero. Sin tope, en Lopesan salian 1.503 oportunidades (el 59 % de
# sus indexables esta en 4-20 con mas de 500 impresiones) y una lista asi no
# la trabaja nadie.
MAX_OPORTUNIDADES = 100
MAX_CTR_BAJO = 100
CONSULTAS_POR_PAGINA = 10
# Paginas (o filas busqueda x pagina) de un tramo de la curva por debajo de las
# cuales su cuartil es ruido.
MIN_PAGINAS_TRAMO = 10
MIN_FILAS_TRAMO = 30
# Una busqueda de una pagina entra en la curva con al menos estas impresiones
# en la ventana: con 3 impresiones, un clic es un 33 % de CTR.
MIN_IMPRESIONES_CONSULTA = 50
FRACCION_ESPERADA = 0.5
# Busquedas canibalizadas que se avisan como mucho, las de mas clics primero.
MAX_CANIBALIZACIONES = 1000
SITEMAP_DIAS_SIN_LEER = 30
SNIPPET = ("title_missing", "title_generico", "title_max_pixels", "title_too_long",
           "description_missing", "description_igual_title")
LOTE = 1000


def _cuantil(valores: list[float], q: float) -> float:
    v = sorted(valores)
    if not v:
        return 0.0
    pos = q * (len(v) - 1)
    i = int(pos)
    return v[i] if i + 1 >= len(v) else v[i] + (v[i + 1] - v[i]) * (pos - i)


def tramo(posicion: float | None) -> str | None:
    if posicion is None or posicion < 1:
        return None
    if posicion < 1.5:
        return "1"
    if posicion < 2.5:
        return "2"
    if posicion < 3.5:
        return "3"
    if posicion < 5.5:
        return "4-5"
    if posicion < 10.5:
        return "6-10"
    if posicion < 20.5:
        return "11-20"
    return None


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(t.lower().split())


def es_de_marca(consulta: str, marcas: list[str]) -> bool:
    c = _normal(consulta)
    return any(m and m in c for m in (_normal(x) for x in marcas or []))


def idioma_de_ruta(path: str) -> str:
    """El tramo de idioma de la ruta (`/en/`, `/blog/de/`), o '' si no tiene:
    una fuente en otro idioma no es un enlace que vaya a poner nadie."""
    from shared.plantillas import es_idioma

    for tramo_ in [t for t in (path or "/").split("/") if t][:2]:
        if es_idioma(tramo_):
            return tramo_.lower()
    return ""


def _es_home(url: str) -> bool:
    p = urlsplit(url)
    return (p.path or "/") == "/" and not p.query


def cubre(propiedad: str, url: str) -> bool:
    """Si la propiedad de Search Console abarca esa URL."""
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if propiedad.startswith("sc-domain:"):
        dominio = propiedad.split(":", 1)[1].strip().lower()
        return host == dominio or host.endswith("." + dominio)
    return url.lower().startswith(propiedad.lower())


def avisar_sin_tumbar(an) -> dict[str, Any] | None:
    """`avisar` en un savepoint y con el try cubriendolo entero (decision 74):
    un dato raro de Search Console no puede llevarse por delante el analisis
    ni deshacer lo que ya lleva hecho (un rollback a mitad de `run_all` borraba
    las incidencias y el PageRank escritos antes)."""
    an._flush_issues()
    pendientes = list(an._pending_issues)
    try:
        with an.session.begin_nested():
            return avisar(an)
    except Exception:
        an._pending_issues = pendientes
        logger.exception("Oportunidades de Search Console del job %s: fallo; el analisis sigue", an.job_id)
        return None


def _clave_segura(url: str | None) -> str | None:
    """`clave_de_url` sin lanzar: un href del HTML puede ser cualquier cosa
    (`https://[lang].x.com/`, un puerto imposible) y `clave_url` lo rechaza."""
    from shared.gsc import clave_de_url

    if not url:
        return None
    try:
        return clave_de_url(url)
    except Exception:  # noqa: BLE001
        return None


def avisar(an) -> dict[str, Any] | None:
    from analysis.avisos_de_gsc import VENTANA_DIAS, _dia_del_censo
    from shared.models import Job

    an._flush_issues()
    job = an.session.query(Job).filter(Job.id == an.job_id).one_or_none()
    if job is None or not job.cliente_id or not isinstance(job.gsc_resumen, dict):
        return None
    t = (job.config or {}).get("analysis_thresholds") or {}
    hasta = _dia_del_censo(job)
    desde = hasta - timedelta(days=VENTANA_DIAS - 1)
    ctx = _Contexto(an, job, t, desde, hasta)
    if not ctx.urls:
        return None
    resumen: dict[str, Any] = {}
    consultas = ctx.consultas()
    resumen["oportunidad_posicion"] = _oportunidades(ctx, consultas)
    resumen["canibalizacion_por_consulta"] = _canibalizacion(ctx, consultas)
    resumen["ctr_bajo_para_posicion"], resumen["curva_ctr"] = _ctr_bajo(ctx, consultas)
    resumen["sitemaps"] = _sitemaps(ctx)
    resumen["por_plantilla"], resumen["por_profundidad"] = _rendimiento(ctx)
    resumen["consultas_desde"] = ctx.consultas_desde
    an._flush_issues()
    job.gsc_resumen = {**job.gsc_resumen, "oportunidades": resumen}
    logger.info("Oportunidades de Search Console del job %s: %s", an.job_id,
                {k: v for k, v in resumen.items() if isinstance(v, int)})
    return resumen


class _Contexto:
    def __init__(self, an, job, t: dict, desde, hasta):
        from shared.gsc import clave_de_url
        from shared.models import GscEstado, Url
        from shared.plantillas import plantilla_de, reglas_de_plantilla

        self.an, self.job, self.desde, self.hasta = an, job, desde, hasta
        self.min_imp = int(t.get("gsc_oportunidad_min_impresiones", 500))
        self.pos_min = float(t.get("gsc_oportunidad_posicion_min", 4.0))
        self.pos_max = float(t.get("gsc_oportunidad_posicion_max", 20.0))
        self.profundidad = int(t.get("gsc_oportunidad_profundidad", 4))
        self.ctr_min_imp = int(t.get("gsc_ctr_min_impresiones", 200))
        self.ctr_min_faltan = int(t.get("gsc_ctr_min_clics_que_faltan", 20))
        self.canibal_parte = float(t.get("gsc_canibalizacion_min_parte", 0.2))
        self.canibal_min_imp = int(t.get("gsc_canibalizacion_min_impresiones", 1000))
        self.canibal_min_clics = int(t.get("gsc_canibalizacion_min_clics", 10))
        self.canibal_clics_grave = int(t.get("gsc_canibalizacion_clics_grave", 100))
        self.marcas = list((job.config or {}).get("marcas") or [])
        reglas = reglas_de_plantilla((job.config or {}).get("templates"))
        # Solo las propiedades que el cliente tiene activas: si paso de una de
        # prefijo a `sc-domain:`, la vieja sigue en la base y sus filas se
        # contarian dos veces.
        from shared.models import Client

        cliente = an.session.get(Client, job.cliente_id)
        activas = {str((i.get("parametros") or {}).get("propiedad") or "").strip()
                   for i in (getattr(cliente, "integraciones", None) or [])
                   if isinstance(i, dict) and i.get("tipo") == "gsc" and i.get("activa", True)}
        self.estados = [e for e in an.session.query(GscEstado).filter(GscEstado.cliente_id == job.cliente_id)
                        if not activas or e.propiedad in activas]
        self.propiedades = sorted({e.propiedad for e in self.estados})
        self.urls = [u for u in an.session.execute(
            select(Url.id, Url.url, Url.url_hash, Url.path, Url.is_internal, Url.is_html, Url.status_code,
                   Url.indexable, Url.noindex, Url.indexability_status, Url.pagerank_score,
                   Url.click_depth, Url.redirect_url, Url.gsc_clics, Url.gsc_impresiones, Url.gsc_posicion)
            .where(Url.job_id == an.job_id)).all()]
        self.urls = [u for u in self.urls if _clave_segura(u.url)]
        self.por_clave = {clave_de_url(u.url): u for u in self.urls}
        self.clave_de_id = {u.id: k for k, u in self.por_clave.items()}
        self.plantilla = {u.id: plantilla_de(u.path or urlsplit(u.url).path, reglas) for u in self.urls}
        self.consultas_desde = None

    def indexable(self, u) -> bool:
        return bool(u.is_internal and u.is_html and u.indexable and u.status_code == 200
                    and not u.noindex)

    def consultas(self) -> dict[str, dict[str, list]]:
        """{url_clave: {consulta: [clics, impresiones, pos_x_imp]}} en la ventana."""
        from shared.models import GscConsulta as C

        for e in self.estados:
            st = (e.detalle or {}).get("consultas") or {}
            if st.get("primer_dia"):
                self.consultas_desde = st["primer_dia"]
        salida: dict[str, dict[str, list]] = defaultdict(dict)
        if not self.propiedades:
            return salida
        try:
            with self.an.session.begin_nested():
                filas = self.an.session.execute(
                    select(C.url_clave, C.consulta, func.sum(C.clics), func.sum(C.impresiones),
                           func.sum(C.posicion * C.impresiones))
                    .where(C.cliente_id == self.job.cliente_id, C.propiedad.in_(self.propiedades),
                           C.fecha >= self.desde, C.fecha <= self.hasta)
                    .group_by(C.url_clave, C.consulta)).all()
        except Exception:  # noqa: BLE001 -- base sin migrar: un savepoint, no un rollback
            return {}
        for clave, consulta, clics, imp, pos_x in filas:
            salida[clave][consulta] = [int(clics or 0), int(imp or 0), float(pos_x or 0)]
        return salida


def _oportunidades(ctx: _Contexto, consultas: dict) -> int:
    from shared.models import Link

    indexables = [u for u in ctx.urls if ctx.indexable(u)]
    if not indexables:
        return 0
    scores = [float(u.pagerank_score) for u in indexables if u.pagerank_score is not None]
    profundidades = [u.click_depth for u in indexables if u.click_depth is not None]
    p25 = _cuantil(scores, 0.25) if scores else None
    # `pagerank_score` es entero de 0 a 100, con muchos empates: el cuartil
    # bajo incluye a los que estan justo en el, salvo que eso meta a mas de la
    # mitad de las indexables (en Lopesan, p25 46 y mediana 48).
    p25_incluye = p25 is not None and sum(1 for x in scores if x <= p25) <= len(scores) / 2
    # «Lejos» es relativo al sitio: en Lopesan la mitad de las indexables estan
    # a 5 clics o mas de la home, y con un 4 fijo todo era lejos.
    lejos = max(ctx.profundidad, _cuantil(profundidades, 0.75)) if profundidades else ctx.profundidad
    # El CTR de las paginas del sitio que ya estan en el top 3: lo que ganaria
    # una oportunidad si llegara.
    top3 = [u.gsc_clics / u.gsc_impresiones for u in indexables
            if u.gsc_posicion is not None and u.gsc_posicion < 3.5 and (u.gsc_impresiones or 0) >= 100]
    ctr_top3 = _cuantil(top3, 0.5) if len(top3) >= MIN_PAGINAS_TRAMO else None
    objetivos = []
    for u in indexables:
        if (u.gsc_posicion is None or not (ctx.pos_min <= u.gsc_posicion <= ctx.pos_max)
                or (u.gsc_impresiones or 0) < ctx.min_imp):
            continue
        motivos = []
        if p25 is not None and u.pagerank_score is not None and (
                u.pagerank_score < p25 or (u.pagerank_score == p25 and p25_incluye)):
            motivos.append("pagerank_bajo")
        if u.click_depth is not None and u.click_depth > lejos:
            motivos.append("profundidad")
        if motivos:
            ganables = (max(0, round(ctr_top3 * u.gsc_impresiones - (u.gsc_clics or 0)))
                        if ctr_top3 is not None else None)
            objetivos.append((u, motivos, ganables))
    if not objetivos:
        return 0
    objetivos.sort(key=lambda o: (-(o[2] or 0), -(o[0].gsc_impresiones or 0), o[0].url))
    if len(objetivos) > MAX_OPORTUNIDADES:
        logger.info("Oportunidades: %d, se avisan las %d con mas clics por ganar",
                    len(objetivos), MAX_OPORTUNIDADES)
    objetivos = objetivos[:MAX_OPORTUNIDADES]

    # Quien enlaza ya a cada objetivo: una lectura de `links` para todo el lote,
    # filtrando el job EN `links` (decisiones 37 y 41).
    ya_enlazan: dict[str, set[int]] = defaultdict(set)
    hashes = [u.url_hash for u, _, _ in objetivos]
    for i in range(0, len(hashes), LOTE):
        for origen, destino in ctx.an.session.execute(
                select(Link.from_url_id, Link.to_url_hash)
                .where(Link.job_id == ctx.an.job_id, Link.to_url_hash.in_(hashes[i:i + LOTE]))):
            ya_enlazan[destino].add(origen)

    # Que paginas indexables salen por cada busqueda (con sus impresiones).
    por_consulta: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    for u in indexables:
        for consulta, (_, imp, _) in (consultas.get(ctx.clave_de_id[u.id]) or {}).items():
            por_consulta[consulta].append((imp, u))
    por_plantilla: dict[str, list] = defaultdict(list)
    for u in indexables:
        if u.pagerank_score is not None:
            por_plantilla[ctx.plantilla[u.id]].append(u)
    for lista in por_plantilla.values():
        lista.sort(key=lambda x: (-x.pagerank_score, x.url))

    n = 0
    for u, motivos, ganables in objetivos:
        propias = consultas.get(ctx.clave_de_id[u.id]) or {}
        top = sorted(propias.items(), key=lambda kv: (-kv[1][1], kv[0]))[:CONSULTAS_POR_PAGINA]
        excluir = ya_enlazan.get(u.url_hash, set()) | {u.id}
        minimo = u.pagerank_score if u.pagerank_score is not None else -1

        idioma = idioma_de_ruta(u.path or urlsplit(u.url).path)

        def vale(f) -> bool:
            return (f.id not in excluir and f.pagerank_score is not None and f.pagerank_score > minimo
                    and f.is_internal and idioma_de_ruta(f.path or urlsplit(f.url).path) == idioma)

        candidatas: dict[int, dict[str, Any]] = {}
        for consulta, _ in top:
            for imp, f in por_consulta.get(consulta, []):
                if not vale(f):
                    continue
                c = candidatas.setdefault(f.id, {"u": f, "imp": 0, "consulta": consulta})
                c["imp"] += imp
        fuentes = sorted(candidatas.values(), key=lambda c: (-c["imp"], -c["u"].pagerank_score, c["u"].url))
        elegidas = [{"url": c["u"].url, "pagerank_score": c["u"].pagerank_score,
                     "consulta_compartida": c["consulta"], "misma_plantilla": False}
                    for c in fuentes[:MAX_FUENTES]]
        if len(elegidas) < MAX_FUENTES:
            vistas = {c["u"].id for c in fuentes[:MAX_FUENTES]}
            for f in por_plantilla.get(ctx.plantilla[u.id], []):
                if len(elegidas) >= MAX_FUENTES:
                    break
                if f.id in vistas or not vale(f):
                    continue
                elegidas.append({"url": f.url, "pagerank_score": f.pagerank_score,
                                 "consulta_compartida": None, "misma_plantilla": True})
        ctx.an._add_issue(u.id, "oportunidad_posicion", "info", {
            "posicion": u.gsc_posicion, "impresiones": u.gsc_impresiones, "clics": u.gsc_clics,
            "pagerank_score": u.pagerank_score, "pagerank_p25": p25, "click_depth": u.click_depth,
            "profundidad_lejos": lejos, "motivos": motivos,
            "ctr_top3_del_sitio": round(ctr_top3, 4) if ctr_top3 is not None else None,
            "clics_por_ganar": ganables,
            "consultas": [{"consulta": q, "impresiones": v[1], "clics": v[0],
                           "posicion": round(v[2] / v[1], 1) if v[1] else None} for q, v in top[:5]],
            "fuentes": elegidas,
            "ventana": [ctx.desde.isoformat(), ctx.hasta.isoformat()],
        })
        n += 1
    return n


class _Grupos:
    """Que URLs son, para una busqueda, la MISMA pagina (union-find por clave):

    - la variante y su canonical, y la URL que redirige y su destino;
    - la variante con parametros (`?partner=`, `?utm_`) y la URL limpia del
      censo: es `trafico_en_variante_con_parametros`, no canibalizacion;
    - las alternativas de idioma unidas por hreflang: Google ensena a cada
      usuario la suya, no compiten. Medido en Lopesan: sin esto, las 15
      busquedas con mas clics eran la ficha de un hotel en /es/ y en /en/.
    """

    def __init__(self):
        self.padre: dict[str, str] = {}

    def raiz(self, k: str) -> str:
        while self.padre.get(k, k) != k:
            self.padre[k] = self.padre.get(self.padre[k], self.padre[k])
            k = self.padre[k]
        return k

    def unir(self, a: str, b: str) -> None:
        ra, rb = self.raiz(a), self.raiz(b)
        if ra != rb:
            self.padre[max(ra, rb)] = min(ra, rb)


def _grupos(ctx: _Contexto, url_de: dict[str, str]) -> _Grupos:
    from analysis.avisos_de_gsc import _sin_query
    from shared.models import Hreflang, HtmlMeta

    g = _Grupos()
    for u in ctx.urls:
        destino = _clave_segura(u.redirect_url)
        if destino:
            g.unir(ctx.clave_de_id[u.id], destino)
    limpias = {_sin_query(u.url): ctx.clave_de_id[u.id] for u in ctx.urls
               if u.is_internal and not urlsplit(u.url).query}
    for clave, url in url_de.items():
        if urlsplit(url).query and _sin_query(url) in limpias:
            g.unir(clave, limpias[_sin_query(url)])
    ids = [u.id for u in ctx.urls if u.is_internal]
    for i in range(0, len(ids), LOTE):
        lote = ids[i:i + LOTE]
        for uid, href, cab in ctx.an.session.execute(
                select(HtmlMeta.url_id, HtmlMeta.canonical_href, HtmlMeta.canonical_header)
                .where(HtmlMeta.url_id.in_(lote))):
            destino = _clave_segura((href or "").strip() or cab)
            if destino:
                g.unir(ctx.clave_de_id[uid], destino)
        for uid, href, en_body in ctx.an.session.execute(
                select(Hreflang.url_id, Hreflang.href, Hreflang.en_body).where(Hreflang.url_id.in_(lote))):
            destino = _clave_segura(href)
            if destino and not en_body:
                g.unir(ctx.clave_de_id[uid], destino)
    return g


def _canibalizacion(ctx: _Contexto, consultas: dict) -> int:
    from analysis.analyzer import _clave_de_grupo
    from shared.models import GscPagina

    if not consultas:
        return 0
    por_consulta: dict[str, dict[str, list]] = defaultdict(dict)
    for clave, qs in consultas.items():
        for consulta, v in qs.items():
            por_consulta[consulta][clave] = v
    url_de: dict[str, str] = {k: u.url for k, u in ctx.por_clave.items()}
    faltan = {k for qs in por_consulta.values() if len(qs) > 1 for k in qs if k not in url_de}
    faltan_l = sorted(faltan)
    for i in range(0, len(faltan_l), LOTE):
        for clave, url in ctx.an.session.execute(
                select(GscPagina.url_clave, func.min(GscPagina.url))
                .where(GscPagina.cliente_id == ctx.job.cliente_id,
                       GscPagina.url_clave.in_(faltan_l[i:i + LOTE]))
                .group_by(GscPagina.url_clave)):
            url_de[clave] = url
    grupos = _grupos(ctx, url_de)
    hallazgos = []
    for consulta, por_url in por_consulta.items():
        if len(por_url) < 2:
            continue
        clics = sum(v[0] for v in por_url.values())
        imp = sum(v[1] for v in por_url.values())
        # Con clics, desde `gsc_canibalizacion_min_clics`; sin ninguno, solo
        # por impresiones y con muchas. En Lopesan, con 100 impresiones y sin
        # minimo de clics, pasaban de 1.000 busquedas de 1 a 9 clics.
        if clics < ctx.canibal_min_clics and not (clics == 0 and imp >= ctx.canibal_min_imp):
            continue
        # Lo del mismo grupo canonical/redireccion es una sola URL para Google.
        juntas: dict[str, list] = {}
        for clave, v in por_url.items():
            k = grupos.raiz(clave)
            acc = juntas.setdefault(k, [0, 0, 0.0, []])
            acc[0] += v[0]
            acc[1] += v[1]
            acc[2] += v[2]
            acc[3].append(clave)
        marca = es_de_marca(consulta, ctx.marcas)
        base = 0 if clics > 0 else 1
        total = clics if clics > 0 else imp
        repartidas = []
        for k, acc in juntas.items():
            # Solo compite lo que el censo tiene y responde 200: una URL que
            # solo esta en Search Console puede ser la vieja de un cambio de
            # slug con 301 a mitad de ventana, que no compite con la nueva sino
            # que la precede.
            del_censo = [c for c in acc[3] if c in ctx.por_clave and ctx.por_clave[c].status_code == 200]
            if not del_censo:
                continue
            k = max(del_censo, key=lambda c: (por_url[c][base], c))
            url = ctx.por_clave[k].url
            if marca and _es_home(url):
                continue
            if acc[base] / total >= ctx.canibal_parte:
                repartidas.append((k, url, acc))
        if len(repartidas) < 2:
            continue
        hallazgos.append((clics, imp, consulta, repartidas, repartidas, base, total))
    hallazgos.sort(key=lambda h: (-h[0], -h[1], h[2]))
    if len(hallazgos) > MAX_CANIBALIZACIONES:
        logger.info("Canibalizacion: %d busquedas, se avisan las %d con mas clics",
                    len(hallazgos), MAX_CANIBALIZACIONES)
    for clics, imp, consulta, repartidas, en_censo, base, total in hallazgos[:MAX_CANIBALIZACIONES]:
        # Ancla estable: la URL menor del grupo, no la de mas clics, que es
        # justo lo que se invierte en una canibalizacion.
        ancla = min(en_censo, key=lambda r: r[1])
        urls = []
        for k, url, acc in sorted(repartidas, key=lambda r: (-r[2][base], r[1])):
            u = ctx.por_clave.get(k)
            urls.append({
                "url": url, "clics": acc[0], "impresiones": acc[1],
                "parte": round(acc[base] / total, 3),
                "posicion": round(acc[2] / acc[1], 1) if acc[1] else None,
                "en_censo": u is not None,
                "indexable": bool(u.indexable) if u is not None else None,
                "estado": (u.indexability_status if u is not None else None),
                "plantilla": ctx.plantilla.get(u.id) if u is not None else None,
            })
        # Gravedad por consecuencia (decision 92): una busqueda con pocos clics
        # repartidos es informativa; con muchos, es trafico que se diluye.
        gravedad = "warning" if clics >= ctx.canibal_clics_grave else "info"
        ctx.an._add_issue(ctx.por_clave[ancla[0]].id, "canibalizacion_por_consulta", gravedad, {
            "consulta": consulta, "clics": clics, "impresiones": imp,
            "reparto_por": "clics" if base == 0 else "impresiones", "urls": urls,
            "ventana": [ctx.desde.isoformat(), ctx.hasta.isoformat()],
        }, group_key=_clave_de_grupo(consulta))
    return min(len(hallazgos), MAX_CANIBALIZACIONES)


def _ctr_bajo(ctx: _Contexto, consultas: dict) -> tuple[int, dict | None]:
    """La curva de CTR del sitio por tramo de posicion, de las filas busqueda x
    pagina (sin las de marca si estan declaradas), y las paginas que se quedan
    por debajo de su cuartil bajo en el conjunto de sus busquedas.

    Por pagina y no por busqueda: lo que se arregla es el snippet de la
    pagina. Para cada pagina, lo esperado es la suma de impresiones x CTR
    mediano del tramo de CADA busqueda suya, y lo minimo, con el cuartil bajo:
    una pagina que sale en 3.ª posicion por una cosa y en 15.ª por otra no se
    compara con un solo tramo.
    """
    from shared.models import Issue

    if not consultas:
        return 0, None
    filas = []
    for clave, qs in consultas.items():
        for consulta, (clics, imp, pos_x) in qs.items():
            if imp < MIN_IMPRESIONES_CONSULTA or (ctx.marcas and es_de_marca(consulta, ctx.marcas)):
                continue
            tr = tramo(pos_x / imp)
            if tr is not None:
                filas.append((clave, consulta, clics, imp, tr))
    por_tramo: dict[str, list[float]] = defaultdict(list)
    for _, _, clics, imp, tr in filas:
        por_tramo[tr].append(clics / imp)
    curva = {tr: {"filas": len(v), "p25": round(_cuantil(v, 0.25), 4), "mediana": round(_cuantil(v, 0.5), 4),
                  "valida": len(v) >= MIN_FILAS_TRAMO} for tr, v in por_tramo.items()}
    resumen = {"tramos": curva, "sin_marcas": not ctx.marcas}
    por_pagina: dict[str, dict[str, Any]] = {}
    for clave, consulta, clics, imp, tr in filas:
        c = curva[tr]
        if not c["valida"]:
            continue
        d = por_pagina.setdefault(clave, {"clics": 0, "imp": 0, "esperado": 0.0, "minimo": 0.0, "qs": []})
        d["clics"] += clics
        d["imp"] += imp
        d["esperado"] += c["mediana"] * imp
        d["minimo"] += c["p25"] * imp
        d["qs"].append((c["mediana"] * imp - clics, consulta, clics, imp, tr))
    bajas = []
    for clave, d in por_pagina.items():
        u = ctx.por_clave.get(clave)
        if u is None or not ctx.indexable(u) or d["imp"] < ctx.ctr_min_imp:
            continue
        faltan = d["esperado"] - d["clics"]
        # Por debajo del cuartil bajo O de la mitad de lo esperado: en un sitio
        # con mucha cola larga sin clics el cuartil bajo de casi todos los
        # tramos es 0 (Lopesan, con las busquedas de marca fuera) y «por debajo
        # de 0» no pasa nunca.
        if (d["clics"] < max(d["minimo"], FRACCION_ESPERADA * d["esperado"])
                and faltan >= ctx.ctr_min_faltan):
            bajas.append((faltan, u, d))
    bajas.sort(key=lambda b: (-b[0], b[1].url))
    if len(bajas) > MAX_CTR_BAJO:
        logger.info("CTR bajo: %d paginas, se avisan las %d con mas clics que faltan", len(bajas), MAX_CTR_BAJO)
    bajas = bajas[:MAX_CTR_BAJO]
    snippet: dict[int, list[str]] = defaultdict(list)
    ids = sorted(u.id for _, u, _ in bajas)
    for i in range(0, len(ids), LOTE):
        for uid, tipo in ctx.an.session.execute(
                select(Issue.url_id, Issue.issue_type)
                .where(Issue.job_id == ctx.an.job_id, Issue.url_id.in_(ids[i:i + LOTE]),
                       Issue.issue_type.in_(SNIPPET))):
            snippet[uid].append(tipo)
    for faltan, u, d in bajas:
        d["qs"].sort(key=lambda q: (-q[0], q[1]))
        ctx.an._add_issue(u.id, "ctr_bajo_para_posicion", "info", {
            "impresiones": d["imp"], "clics": d["clics"], "ctr": round(d["clics"] / d["imp"], 4),
            "ctr_esperado": round(d["esperado"] / d["imp"], 4), "clics_que_faltan": round(faltan),
            "consultas": [{"consulta": q, "clics": c, "impresiones": i, "tramo": tr}
                          for _, q, c, i, tr in d["qs"][:5]],
            "snippet": sorted(set(snippet.get(u.id, []))),
            "sin_marcas": not ctx.marcas,
            "ventana": [ctx.desde.isoformat(), ctx.hasta.isoformat()],
        })
    return len(bajas), resumen


def _sitemaps(ctx: _Contexto) -> dict[str, int] | None:
    from analysis.analyzer import _clave_de_grupo
    from analysis.avisos_de_sitemaps import _Anclas
    from shared.models import GscSitemap

    resumen_spider = getattr(ctx.job, "sitemaps", None) or {}
    ficheros = [f for f in resumen_spider.get("ficheros") or [] if f.get("status") == 200]
    sabidos = [e for e in ctx.estados if (e.detalle or {}).get("sitemaps") is not None]
    if not ficheros or not sabidos:
        return None
    try:
        with ctx.an.session.begin_nested():
            de_google = {(s.propiedad, s.path): s for s in ctx.an.session.query(GscSitemap)
                         .filter(GscSitemap.cliente_id == ctx.job.cliente_id).all()}
    except Exception:  # noqa: BLE001 -- base sin migrar: un savepoint, no un rollback
        return None
    # Los hijos de un indice no salen en la lista de Google: solo se miran los
    # indices, o todos si el rastreo no leyo ningun indice.
    indices = [f for f in ficheros if f.get("hijos")]
    mirar = indices or ficheros
    anclas = _Anclas(ctx.an)
    limite = datetime.combine(ctx.hasta, datetime.min.time(), timezone.utc) - timedelta(
        days=SITEMAP_DIAS_SIN_LEER)
    cuenta = {"no_enviados": 0, "no_leidos": 0, "con_errores": 0}
    def aware(t):
        return t.replace(tzinfo=t.tzinfo or timezone.utc) if t else None

    for f in mirar:
        url = f["url"]
        props = sorted(e.propiedad for e in sabidos if cubre(e.propiedad, url))
        ancla = anclas.de_fichero(url) if props else None
        if ancla is None:
            continue
        # En CUALQUIERA de las propiedades que cubren la URL: con una de prefijo
        # y otra de dominio, el sitemap puede estar enviado solo en una.
        enviados = [de_google[(p, url)] for p in props if (p, url) in de_google]
        if not enviados:
            cuenta["no_enviados"] += 1
            ctx.an._add_issue(ancla, "sitemap_no_enviado_a_google", "info", {
                "fichero": url, "propiedades": props,
                "enviados": sorted(p for (prop, p) in de_google if prop in props)[:10],
            }, group_key=_clave_de_grupo(url))
            continue
        g = max(enviados, key=lambda x: (aware(x.descargado) or datetime.min.replace(tzinfo=timezone.utc)))
        descargado, enviado = aware(g.descargado), aware(g.enviado)
        # Un sitemap enviado hace menos de 30 dias puede no haberse bajado aun:
        # eso no es «no lo lee».
        if (descargado is None or descargado < limite) and (enviado is None or enviado < limite):
            cuenta["no_leidos"] += 1
            ctx.an._add_issue(ancla, "sitemap_no_leido_por_google", "warning", {
                "fichero": url, "propiedad": g.propiedad,
                "descargado": descargado.isoformat() if descargado else None,
                "enviado": enviado.isoformat() if enviado else None,
                "pendiente": g.pendiente, "dias": SITEMAP_DIAS_SIN_LEER,
            }, group_key=_clave_de_grupo(url))
        if (g.errores or 0) > 0:
            cuenta["con_errores"] += 1
            ctx.an._add_issue(ancla, "sitemap_con_errores_en_google", "warning", {
                "fichero": url, "propiedad": g.propiedad, "errores": g.errores, "avisos": g.avisos,
                "descargado": descargado.isoformat() if descargado else None,
            }, group_key=_clave_de_grupo(url))
    return cuenta


def _rendimiento(ctx: _Contexto) -> tuple[list[dict], list[dict]]:
    """Clics, impresiones, CTR y posicion por plantilla y por profundidad, con
    cuantas indexables tiene cada grupo y cuantas salen en Google."""

    def acumular(grupos: dict, clave, u) -> None:
        g = grupos.setdefault(clave, {"indexables": 0, "con_impresiones": 0, "clics": 0,
                                      "impresiones": 0, "pos_x": 0.0})
        g["indexables"] += 1
        imp = u.gsc_impresiones or 0
        if imp:
            g["con_impresiones"] += 1
            g["clics"] += u.gsc_clics or 0
            g["impresiones"] += imp
            g["pos_x"] += (u.gsc_posicion or 0) * imp

    plantillas: dict[str, dict] = {}
    profundidades: dict[str, dict] = {}
    for u in ctx.urls:
        if not ctx.indexable(u) or u.gsc_impresiones is None:
            continue
        acumular(plantillas, ctx.plantilla[u.id], u)
        d = u.click_depth
        acumular(profundidades, "sin dato" if d is None else (str(d) if d < 10 else "10+"), u)

    def salida(grupos: dict) -> list[dict]:
        filas = []
        for clave, g in grupos.items():
            filas.append({"grupo": clave, "indexables": g["indexables"],
                          "con_impresiones": g["con_impresiones"], "clics": g["clics"],
                          "impresiones": g["impresiones"],
                          "ctr": round(g["clics"] / g["impresiones"], 4) if g["impresiones"] else None,
                          "posicion": round(g["pos_x"] / g["impresiones"], 1) if g["impresiones"] else None})
        return filas

    por_plantilla = sorted(salida(plantillas), key=lambda f: (-f["clics"], -f["impresiones"], f["grupo"]))[:50]
    orden = {str(i): i for i in range(10)} | {"10+": 10, "sin dato": 11}
    por_profundidad = sorted(salida(profundidades), key=lambda f: orden.get(f["grupo"], 12))
    return por_plantilla, por_profundidad
