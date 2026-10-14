"""Comparar dos censos del mismo sitio: qué cambió entre uno y otro.

Vive en `shared/` y no en `analysis/` porque lo necesitan la API —que no lleva
`analysis/` en su imagen— y el worker. Es la misma razón que las reglas de
robots (decisión 23), la indexabilidad (39) y los dominios (64).

Las cuatro decisiones de criterio son las de #32, y cada una evita una forma
distinta de mentir:

1. **Las URLs se emparejan por el texto normalizado**, no por `urls.id`, que
   cambia entre censos.
2. **Un censo truncado no permite afirmar ausencias.** Si uno de los dos lados
   paró por `max_urls`, por tiempo o por estancamiento, decir «estas 4.000
   URLs han desaparecido» es falso: simplemente no se llegó a ellas. En ese
   caso el resultado se marca **no concluyente** y las ausencias no se cuentan.
3. **Dos orígenes distintos no se comparan.** Producción contra preproducción
   es legítimo, pero sin un mapeo explícito de host el resultado es «ha
   desaparecido el sitio entero».
4. **La versión del código viaja en el resultado** (decisión 66). Si los dos
   censos se hicieron con código distinto, una diferencia puede ser nuestra y
   no del sitio; eso hay que decirlo antes de atribuir nada.
5. **Dos ALCANCES distintos tampoco se comparan del todo.** El guardia del
   punto 3 mira el host, y dos rastreos del mismo host pueden ser dos cosas
   distintas: medido con los dos censos de cst.gov.sa del mismo día, uno
   sembrado en el árbol castellano y otro en el inglés, **0 semillas en común**
   de 4.780 — y la comparación afirmaba que habían desaparecido 834 páginas que
   nunca estuvieron en el alcance. Las ausencias solo se pueden afirmar si cada
   semilla del censo ANTERIOR está también en el posterior. No es un umbral: es
   una contención, y distingue los casos buenos sin ajustar nada (Lopesan
   2.935/2.935, el canario de penguin 1.950/1.950, CST 0/4.780). La Jaccard
   serviría de poco: el canario de penguin da 0,022 contra su censo completo y
   es un subconjunto limpio.
6. **El orden lo pone la FECHA, no quién llame.** «Antes» y «ahora» no pueden
   depender de qué censo se elija primero en un desplegable: una página que
   sale del índice y una que entra son hallazgos opuestos. Si no se sabe la
   fecha de alguno de los dos, no se afirma ninguna dirección y no se emite
   ninguna alerta.

Y la capa de alertas, que es lo que convierte una lista de diferencias en algo
que alguien mira: un cambio que afecta a una **parte grande de las páginas
indexables** no es trabajo editorial, es una plantilla, una configuración o un
despliegue. Ver `REGLAS` y la decisión 68.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import logging
import os
import time

from shared.dominios import dominio_registrable
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)


# Motivos de cierre que dejan un censo incompleto: con cualquiera de ellos no
# se puede afirmar que una URL haya desaparecido.
MOTIVOS_TRUNCADO = frozenset({
    "max_urls_reached", "max_runtime_reached", "stalled", "cancelled",
    "closespider_itemcount", "closespider_pagecount", "closespider_timeout",
})

# Cuántas URLs de ejemplo se devuelven por cada tipo de cambio.
MUESTRA = 10

# Cuánto tiene que moverse el recuento de palabras para contarlo como cambio.
# Un 20%: por debajo es ruido de plantilla (una fecha, un contador, un
# "hace 3 días"), y la decisión 13 ya avisa de que los sitios varían solos.
UMBRAL_PALABRAS = 0.20

# Cuándo un cambio deja de ser trabajo de una página y pasa a ser una alerta.
# El 5% de las indexables, con un piso de 20 páginas porque en un censo de 30
# URLs «el 10%» son tres y no significa nada. Medido contra los pares de censos
# reales disponibles: con estos valores no salta ninguna alerta por la variación
# normal de un sitio entre dos rastreos (decisión 13) y sí salta el caso de
# Lopesan, 2.367 páginas canonicalizadas a un host de preproducción.
PCT_ALERTA = 0.05
MINIMO_PAGINAS = 20
# Una plantilla entera rota se diluye en el total: las 2.367 de Lopesan son el
# 99,5% de su plantilla y el 11% del sitio. Por eso también se mira por forma de
# ruta, con un umbral alto porque ahí el denominador es pequeño.
PCT_ALERTA_PLANTILLA = 0.50

# Qué decide Google con cada cosa. Sin esta columna una alerta es un número
# (regla 0 de docs/PRIORIDADES.md).
REGLAS: dict[str, tuple[str, str]] = {
    "canonical_a_otro_host": ("critical",
        "Google consolida estas páginas en una URL de OTRO host: dejan de "
        "aparecer en resultados y su autoridad se va fuera del sitio. Suele ser "
        "una fuga de un entorno de preproducción o de un CDN."),
    # Esta no mira si la página era indexable, porque lo que se va fuera del
    # sitio es el DESTINO de la consolidación. Medido en Lopesan: de las 2.367
    # páginas que acabaron apuntando a `webserver-lopesan-prd.lfr.cloud`, 1.832
    # ya estaban canonicalizadas a una URL legítima del sitio, así que la regla
    # de «era indexable y deja de serlo» las descartaba enteras.
    "canonical_a_otra_url": ("critical",
        "Google consolida estas páginas en la URL del canonical: dejan de "
        "aparecer en resultados y su autoridad pasa a la otra."),
    "rotas": ("critical",
        "Una página que responde 4xx o 5xx sale del índice, y con ella sus "
        "enlaces internos (decisión 57)."),
    "salen_del_indice": ("critical",
        "Google las saca del índice por algún otro motivo (noindex, robots): "
        "dejan de poder aparecer en resultados."),
    "redirigidas": ("warning",
        "Google indexa el destino, no esta URL. Si la redirección es "
        "intencionada no hay nada que arreglar; si no, son páginas perdidas."),
    "pierden_contenido": ("warning",
        "Menos texto en la misma URL es menos contenido que posicionar. Mirar "
        "antes si el cambio es nuestro: lo dice el aviso de versión."),
    "cambia_el_titulo": ("warning",
        "El título es el enlace del resultado: Google lo vuelve a evaluar y el "
        "CTR se mueve. Un cambio masivo es una plantilla, no una redacción."),
    "desaparecen": ("warning",
        "Ya no se llega a ellas desde el sitio, así que Google tampoco: dejan "
        "de recibir enlaces internos y acaban saliendo del índice."),
    "entran_en_el_indice": ("info",
        "Páginas que antes no podían aparecer en resultados y ahora sí. Si no "
        "era lo que se buscaba, es contenido entrando al índice sin querer."),
}


def _norm(url: str | None) -> str:
    """Forma comparable de una URL, la misma que usa el deduplicador.

    Sin `w3lib` el criterio NO es parecido, es el contrario en los dos casos
    que importan: `?a=1&b=2` deja de casar con `?b=2&a=1` (dos URLs donde hay
    una) y `/a/` empieza a casar con `/a` (una donde hay dos, y normalmente
    una redirige a la otra). Estuvo así en la imagen de la API, que no llevaba
    w3lib, de modo que el mismo par de censos se comparaba con un criterio
    distinto según dónde corriera el código. No se tapa: si falta, revienta.
    """
    if not url:
        return ""
    from w3lib.url import canonicalize_url

    return canonicalize_url(url, keep_fragments=False)


def _host(url: str | None) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""


def _truncado(estado: str | None, motivo: str | None) -> bool:
    return estado != "completed" or (motivo or "") in MOTIVOS_TRUNCADO


def _aplicar_mapa(url: str, mapa: dict[str, str]) -> str:
    """Reescribe el host de una URL según el mapa `pre.x.com -> x.com`."""
    if not mapa:
        return url
    h = _host(url)
    destino = mapa.get(h)
    if not destino:
        return url
    return url.replace(f"//{h}", f"//{destino}", 1)


def comparar_censos(
    paginas_a: list[dict[str, Any]],
    paginas_b: list[dict[str, Any]],
    *,
    job_a: dict[str, Any],
    job_b: dict[str, Any],
    mapa_hosts: dict[str, str] | None = None,
    umbral_palabras: float = UMBRAL_PALABRAS,
) -> dict[str, Any]:
    """Compara dos listas de páginas ya cargadas. Función pura, sin BD.

    Cada página es un dict con: `url`, `status_code`, `indexability_status`,
    `canonical_href`, `title`, `h1`, `word_count`.
    """
    mapa_hosts = mapa_hosts or {}

    # El orden lo pone la FECHA. Si no, "antes" y "ahora" los decide el
    # desplegable: la misma pareja de censos diria que 2.367 paginas salen del
    # indice o que entran, segun cual se eligiera primero. Es el mismo error que
    # tenia el umbral de palabras, que daba 1.639 cambios en un sentido y 439 en
    # el otro.
    fa, fb = job_a.get("fecha"), job_b.get("fecha")
    direccion_sabida = bool(fa and fb and fa != fb)
    if direccion_sabida and fa > fb:
        paginas_a, paginas_b = paginas_b, paginas_a
        job_a, job_b = job_b, job_a

    # Se compara el HOST, no el dominio registrable: `pre.x.com` y `x.com`
    # comparten dominio pero sus URLs no casan ni una, asi que el resultado
    # seria "ha desaparecido el sitio entero". Lo mismo con `www` y sin `www`.
    # Para eso esta el mapeo explicito.
    host_a = _host(job_a.get("semilla"))
    host_b = _host(job_b.get("semilla"))
    host_a_map = _host(_aplicar_mapa(job_a.get("semilla") or "", mapa_hosts))
    host_b_map = _host(_aplicar_mapa(job_b.get("semilla") or "", mapa_hosts))
    if host_a_map and host_b_map and host_a_map != host_b_map:
        misma_casa = dominio_registrable(host_a_map) == dominio_registrable(host_b_map)
        return {
            "comparable": False,
            "motivo": (
                f"son dos origenes distintos ({host_a} y {host_b})"
                + (", del mismo dominio pero no el mismo host" if misma_casa else "")
                + ". Sin un mapeo explicito de host el resultado seria que ha "
                "desaparecido el sitio entero."
            ),
        }

    truncado_a = _truncado(job_a.get("status"), job_a.get("finish_reason"))
    truncado_b = _truncado(job_b.get("status"), job_b.get("finish_reason"))
    concluyente = not (truncado_a or truncado_b)

    avisos: list[str] = []

    if truncado_a or truncado_b:
        # Solo cuando algo esta DE VERDAD truncado: `concluyente` tambien lo
        # pone a False el guardia de alcance, y con el mensaje colgado de ahi
        # salia un "NO CONCLUYENTE:  no termino entero" sin sujeto, repitiendo
        # ademas lo que el otro aviso ya decia mejor.
        cual = []
        if truncado_a:
            cual.append(f"el primero ({job_a.get('finish_reason') or job_a.get('status')})")
        if truncado_b:
            cual.append(f"el segundo ({job_b.get('finish_reason') or job_b.get('status')})")
        avisos.append(
            "NO CONCLUYENTE: " + " y ".join(cual) + " no termino entero, asi que "
            "no se puede afirmar que falte ninguna URL ni que se haya resuelto nada."
        )
    # Dos rastreos del MISMO host pueden tener dos alcances distintos. Las
    # ausencias solo se pueden afirmar si cada semilla del censo anterior esta
    # tambien en el posterior: si no, lo que "falta" puede no haber estado
    # nunca en el alcance.
    sem_a, sem_b = job_a.get("semillas"), job_b.get("semillas")
    if sem_a is not None and sem_b is not None:
        conj_b = {_norm(_aplicar_mapa(u, mapa_hosts)) for u in sem_b}
        fuera = [u for u in sem_a
                 if _norm(_aplicar_mapa(u, mapa_hosts)) not in conj_b]
        if fuera:
            concluyente = False
            avisos.append(
                f"ALCANCES DISTINTOS: {len(fuera)} de las {len(sem_a)} semillas "
                f"del censo anterior no estan en el posterior (p. ej. "
                f"{fuera[0]}), asi que no se puede afirmar que falte ninguna "
                f"URL: puede no haber estado nunca en el alcance."
            )

    # El render cambia lo que se ve, no lo que hay: una plantilla que monta sus
    # enlaces con JavaScript solo los enseña al rastreo que renderiza, que es
    # justo lo que mide `js_check` (decision 35). No hay en los datos ninguna
    # pareja de censos que difiera en esto, asi que el aviso sale de ese
    # mecanismo y no de una medida.
    if (job_a.get("render_js") is not None and job_b.get("render_js") is not None
            and bool(job_a["render_js"]) != bool(job_b["render_js"])):
        avisos.append(
            "Uno de los dos censos renderiza JavaScript y el otro no: el que "
            "renderiza ve enlaces y texto que el otro no llega a ver, asi que "
            "una diferencia puede ser del render y no del sitio."
        )
    ver_a, ver_b = job_a.get("crawler_version"), job_b.get("crawler_version")
    if not ver_a or not ver_b:
        # Desconocido NO es igual. Si uno de los dos censos es anterior al
        # sello de version (decision 66), callarse equivale a afirmar que se
        # hicieron con el mismo codigo, y eso es justo lo que no se sabe.
        # Comprobado con dos censos reales de progym: 439 paginas cambian su
        # recuento de palabras y el cambio es NUESTRO (la extraccion de
        # contenido), no del sitio.
        avisos.append(
            "No se sabe con que version del crawler se hizo "
            + ("el primer censo" if not ver_a else "el segundo censo")
            + (" ni el segundo" if not ver_a and not ver_b else "")
            + ": cualquier diferencia podria ser nuestra y no del sitio."
        )
    elif ver_a != ver_b:
        avisos.append(
            f"Los dos censos se hicieron con versiones distintas del crawler "
            f"({ver_a} y {ver_b}): una diferencia puede ser nuestra y no del "
            f"sitio."
        )

    # Con que se ANALIZO es otra pregunta, y hace falta igual: un re-analisis
    # cambia las cifras de un censo sin que cambie el sitio ni el rastreo.
    # Medido en penguin, un censo de julio re-analizado con el codigo de
    # octubre. Solo se avisa cuando se SABE que difieren: aqui, a diferencia
    # del rastreo, el desconocido ya lo cubre el aviso de arriba.
    ana_a, ana_b = job_a.get("analisis_version"), job_b.get("analisis_version")
    if ana_a and ana_b and ana_a != ana_b:
        avisos.append(
            f"Los dos censos se ANALIZARON con versiones distintas ({ana_a} y "
            f"{ana_b}): un re-analisis cambia las cifras sin que cambie el "
            f"sitio, asi que parte de la diferencia puede ser nuestra."
        )

    def indexar(paginas):
        return {
            _norm(_aplicar_mapa(p["url"], mapa_hosts)): p
            for p in paginas
            if p.get("url")
        }

    a, b = indexar(paginas_a), indexar(paginas_b)
    comunes = a.keys() & b.keys()

    cambios: dict[str, list[dict[str, Any]]] = {
        "status_code": [], "indexability_status": [], "canonical_href": [],
        "title": [], "h1": [], "word_count": [],
    }
    # Páginas por regla de alerta, y el denominador de cada regla: cuántas
    # podían haberle pasado. "2.367 páginas" no dice nada sin "de 2.379".
    afectadas: dict[str, list[dict[str, Any]]] = {r: [] for r in REGLAS}
    # Por forma de ruta, para poder decir "99,5% de ESTA plantilla".
    bases: dict[str, dict[str, int]] = {
        "era_indexable": defaultdict(int),
        "no_era_indexable": defaultdict(int),
        "era_indexable_en_el_primero": defaultdict(int),
        "todas_las_comunes": defaultdict(int),
    }

    for clave in sorted(comunes):
        pa, pb = a[clave], b[clave]
        for campo in ("status_code", "indexability_status", "canonical_href", "title", "h1"):
            va, vb = pa.get(campo), pb.get(campo)
            if va != vb:
                cambios[campo].append({"url": pb.get("url"), "antes": va, "ahora": vb})
        wa, wb = pa.get("word_count") or 0, pb.get("word_count") or 0
        # El denominador es el MAYOR de los dos, no el "antes": si no,
        # comparar A con B y B con A da resultados distintos. Medido con dos
        # censos reales de progym: 1.639 paginas en un sentido y 439 en el
        # otro, con los mismos datos. Pasar de 100 a 130 palabras y de 130 a
        # 100 es el mismo cambio y tiene que contarse igual.
        mayor = max(wa, wb)
        if mayor and abs(wb - wa) / mayor >= umbral_palabras:
            cambios["word_count"].append({"url": pb.get("url"), "antes": wa, "ahora": wb})

        _clasificar(pa, pb, afectadas, bases, umbral_palabras)

    nuevas = sorted(b.keys() - a.keys())
    desaparecidas = sorted(a.keys() - b.keys()) if concluyente else []
    if concluyente:
        # Solo las que podían aparecer en resultados: que deje de estar
        # enlazada una `?utm` o un `/page/2` no es un hallazgo. El denominador
        # es todo el primer censo, no solo las comunes: una URL que desaparece
        # no está en las comunes por definición.
        for pagina in a.values():
            if _era_indexable(pagina):
                bases["era_indexable_en_el_primero"][_firma(pagina.get("url"))] += 1
        afectadas["desaparecen"] = [
            {"url": a[k]["url"]} for k in desaparecidas if _era_indexable(a[k])]

    resultado = {
        "comparable": True,
        "concluyente": concluyente,
        "avisos": avisos,
        "antes": _sin_semillas(job_a),
        "ahora": _sin_semillas(job_b),
        "direccion_sabida": direccion_sabida,
        "urls": {
            "en_ambos": len(comunes),
            "nuevas": len(nuevas),
            "desaparecidas": len(desaparecidas) if concluyente else None,
            "desaparecidas_no_afirmables": len(a.keys() - b.keys()) if not concluyente else 0,
            "ejemplos_nuevas": [b[k]["url"] for k in nuevas[:MUESTRA]],
            "ejemplos_desaparecidas": [a[k]["url"] for k in desaparecidas[:MUESTRA]],
        },
        "cambios": {
            campo: {"total": len(filas), "ejemplos": filas[:MUESTRA]}
            for campo, filas in cambios.items()
        },
    }
    if direccion_sabida:
        resultado["alertas"] = _alertas(afectadas, bases)
    else:
        resultado["alertas"] = []
        avisos.append(
            "No se emiten alertas: sin la fecha de los dos censos no se sabe "
            "cual es el ANTES, y una pagina que sale del indice y una que entra "
            "son hallazgos opuestos."
        )
    return resultado


def _sin_semillas(job: dict[str, Any]) -> dict[str, Any]:
    """El resumen del job sin la lista de semillas, que puede ser enorme.

    Se necesitan para comprobar el alcance, pero devolverlas serian 87.429
    URLs en la respuesta del censo de penguin. Queda el recuento, que es lo
    unico que alguien va a leer.
    """
    if "semillas" not in job:
        return job
    recortado = dict(job)
    recortado["n_semillas"] = len(recortado.pop("semillas") or [])
    return recortado


def _era_indexable(pagina: dict[str, Any]) -> bool:
    """Si Google podía posicionarla: 200 y sin nada que la saque del índice."""
    return (pagina.get("status_code") == 200
            and (pagina.get("indexability_status") or "") == "Indexable")


def _canonical_a_otra(pagina: dict[str, Any]) -> str | None:
    """El canonical de la página si apunta a OTRA URL; None si es la suya."""
    can = pagina.get("canonical_href")
    if not can:
        return None
    return can if _norm(can) != _norm(pagina.get("url")) else None


# A qué conjunto de páginas se compara cada regla. "2.367 páginas" no dice
# nada sin "de 2.379", y el conjunto no es el mismo para todas: una página que
# ENTRA en el índice se cuenta sobre las que no estaban, no sobre las que sí.
BASE_DE_LA_REGLA: dict[str, str] = {
    "canonical_a_otro_host": "todas_las_comunes",
    "canonical_a_otra_url": "era_indexable",
    "rotas": "era_indexable",
    "salen_del_indice": "era_indexable",
    "redirigidas": "era_indexable",
    "pierden_contenido": "era_indexable",
    "cambia_el_titulo": "era_indexable",
    "entran_en_el_indice": "no_era_indexable",
    "desaparecen": "era_indexable_en_el_primero",
}


def _canonical_fuera(pagina: dict[str, Any]) -> str | None:
    """El canonical si apunta a un host distinto del de la propia página."""
    can = pagina.get("canonical_href")
    hc = _host(can)
    return can if can and hc and hc != _host(pagina.get("url")) else None


def _firma(url: str | None) -> str:
    from urllib.parse import urlparse
    try:
        return firma_de_ruta(urlparse(url or "").path)
    except Exception:
        return "?"


def _clasificar(pa, pb, afectadas, bases, umbral_palabras) -> None:
    """Pone una página en la regla que nombra su CAUSA, y solo en una.

    Un aviso por hallazgo (decisión 36): una página que pierde el índice porque
    le han puesto un canonical a otra URL no se cuenta además en
    `salen_del_indice`, que diría lo mismo sin decir qué arreglar.
    """
    url = pb.get("url")
    bases["todas_las_comunes"][_firma(url)] += 1

    # El canonical se va a otro host: se mira ANTES de todo lo demás y sin
    # exigir que la página fuera indexable. Lo que abandona el sitio es el
    # destino de la consolidación, no esta página.
    fuera_antes = _canonical_fuera(pa)
    fuera_ahora = _canonical_fuera(pb)
    if fuera_ahora and not fuera_antes:
        afectadas["canonical_a_otro_host"].append(
            {"url": url, "antes": pa.get("canonical_href"), "ahora": fuera_ahora})

    if not _era_indexable(pa):
        bases["no_era_indexable"][_firma(url)] += 1
        if _era_indexable(pb):
            afectadas["entran_en_el_indice"].append({"url": url})
        return

    bases["era_indexable"][_firma(url)] += 1

    codigo = pb.get("status_code")
    can_antes, can_ahora = _canonical_a_otra(pa), _canonical_a_otra(pb)
    if can_ahora and not can_antes:
        # Si el destino está fuera del sitio ya se contó arriba: un aviso por
        # hallazgo (decisión 36), y el de fuera dice qué arreglar.
        if not fuera_ahora:
            afectadas["canonical_a_otra_url"].append(
                {"url": url, "antes": pa.get("canonical_href"), "ahora": can_ahora})
    elif codigo and 400 <= codigo < 600:
        afectadas["rotas"].append({"url": url, "antes": 200, "ahora": codigo})
    elif codigo and 300 <= codigo < 400:
        afectadas["redirigidas"].append({"url": url, "antes": 200, "ahora": codigo})
    elif not _era_indexable(pb):
        afectadas["salen_del_indice"].append(
            {"url": url, "antes": pa.get("indexability_status"),
             "ahora": pb.get("indexability_status")})
    else:
        # Sigue indexable: los cambios de grado, que no la sacan del índice.
        wa, wb = pa.get("word_count") or 0, pb.get("word_count") or 0
        if wa and (wa - wb) / wa >= umbral_palabras:
            afectadas["pierden_contenido"].append(
                {"url": url, "antes": wa, "ahora": wb})
        if pa.get("title") != pb.get("title"):
            afectadas["cambia_el_titulo"].append(
                {"url": url, "antes": pa.get("title"), "ahora": pb.get("title")})


def _alertas(afectadas, bases) -> list[dict[str, Any]]:
    """Las reglas que superan el umbral, en el sitio o en una plantilla.

    Dos disparadores, porque una plantilla entera rota se diluye en el total:
    las 2.367 páginas de Lopesan son el 99,5% de su plantilla y el 11% del
    sitio. Si solo se mirara el total, el fallo más grave que hemos encontrado
    en un cliente no habría disparado nada.
    """
    salida = []
    for regla, (severidad, que_decide) in REGLAS.items():
        filas = afectadas[regla]
        base = bases[BASE_DE_LA_REGLA[regla]]
        de = sum(base.values())
        if not filas or not de:
            continue

        por_plantilla: dict[str, int] = {}
        for f in filas:
            firma = _firma(f.get("url"))
            por_plantilla[firma] = por_plantilla.get(firma, 0) + 1
        plantillas = sorted(
            ({"plantilla": k, "paginas": v, "de": base.get(k, v),
              "pct": round(100 * v / max(1, base.get(k, v)), 1)}
             for k, v in por_plantilla.items()),
            key=lambda d: (-d["pct"], -d["paginas"]))

        pct = len(filas) / de
        salta_sitio = len(filas) >= MINIMO_PAGINAS and pct >= PCT_ALERTA
        salta_plantilla = any(
            p["paginas"] >= MINIMO_PAGINAS and p["pct"] >= PCT_ALERTA_PLANTILLA * 100
            for p in plantillas)
        if not (salta_sitio or salta_plantilla):
            continue
        salida.append({
            "regla": regla,
            "severidad": severidad,
            "que_decide_google": que_decide,
            "paginas": len(filas),
            "de": de,
            "pct": round(pct * 100, 1),
            "por": "sitio" if salta_sitio else "plantilla",
            "plantillas": plantillas[:5],
            "ejemplos": filas[:MUESTRA],
        })
    orden = {"critical": 0, "warning": 1, "info": 2}
    return sorted(salida, key=lambda d: (orden[d["severidad"]], -d["paginas"]))


def incidencias_de_censo(db, job_id) -> list[dict[str, Any]]:
    """Las incidencias de un censo con lo que las identifica entre censos.

    «El mismo hallazgo» es `(issue_type, url, group_key)` (#32). Con tipo y
    URL no basta: un hreflang mal escrito emite una incidencia por código de
    idioma, y un título duplicado pertenece a un grupo. La URL va por su
    TEXTO, no por `urls.id`, que cambia entre censos.
    """
    from shared.models import Issue, Url

    filas = (
        db.query(Issue.issue_type, Issue.severity, Issue.group_key, Url.url)
        .join(Url, Url.id == Issue.url_id)
        .filter(Issue.job_id == job_id)
        .all()
    )
    return [
        {"issue_type": f.issue_type, "severity": f.severity,
         "group_key": f.group_key, "url": f.url}
        for f in filas
    ]


def comparar_incidencias(
    incidencias_a: list[dict[str, Any]],
    incidencias_b: list[dict[str, Any]],
    *,
    mapa_hosts: dict[str, str] | None = None,
    puede_afirmar_resueltas: bool = True,
    urls_del_nuevo: set[str] | None = None,
    analisis_a: str | None = None,
    analisis_b: str | None = None,
) -> dict[str, Any]:
    """Qué se arregló, qué apareció y qué sigue, entre dos censos.

    Es lo que convierte una auditoría suelta en un seguimiento: «qué arregló
    el cliente desde la vez pasada» no se puede contestar comparando páginas.

    `puede_afirmar_resueltas` es la decisión 2 de #32 llevada a las
    incidencias: si el censo ANTERIOR está truncado, que un hallazgo no
    aparezca ahora puede ser que no se llegara a su página. Se cuentan aparte
    y no se llaman «resueltas».

    `urls_del_nuevo` cierra el mismo agujero por el otro lado, y hace falta
    aunque los dos censos terminen enteros: **una incidencia en una página que
    ya no está en el censo no está resuelta, no se sabe**. Medido con dos
    censos reales de progym en los que desaparecieron 1.205 URLs: sin esto el
    informe decía **4.794 resueltas** y las tres primeras clases —1.152
    `noindex_page`, 1.146 `canonical_missing`, 938 `hreflang_missing_return`—
    eran páginas que sencillamente no se volvieron a visitar. Decírselo así a
    un cliente es felicitarle por un trabajo que no ha hecho.
    """
    # Dos censos analizados con codigo distinto NO se comparan por incidencias.
    # Un aviso no basta: no impide que la cifra sea falsa ni que alguien la
    # copie a un informe. Medido el 2026-10-13, el dia que se anadio
    # `group_key`: el censo de referencia de Lopesan tenia 11.414 incidencias
    # y CERO claves, y el siguiente las tendria todas. Ningun hallazgo
    # agrupado —duplicados, hreflang, imagenes, datos estructurados— habria
    # casado, y los miles afectados habrian salido como "resuelto" y "nuevo" a
    # la vez. Es la misma regla que las ausencias de un censo truncado
    # (decision 67): lo que no se puede afirmar, no se afirma.
    #
    # Desconocido tampoco vale. Un censo anterior al sello (decision 72) pudo
    # analizarse con cualquier cosa, y callarse equivale a afirmar que fue con
    # el mismo codigo.
    if not (analisis_a and analisis_b and analisis_a == analisis_b):
        cual = (f"{analisis_a or 'desconocida'} y {analisis_b or 'desconocida'}")
        return {
            "comparable": False,
            "motivo": (
                f"los dos censos se analizaron con versiones distintas del "
                f"codigo ({cual}), asi que un hallazgo puede aparecer como "
                f"resuelto solo porque la regla que lo emitia ha cambiado. "
                f"Para compararlos, vuelve a analizar el mas antiguo con el "
                f"codigo de hoy."
            ),
        }

    mapa_hosts = mapa_hosts or {}

    def clave(i):
        return (i["issue_type"],
                _norm(_aplicar_mapa(i.get("url") or "", mapa_hosts)),
                i.get("group_key"))

    a = {clave(i): i for i in incidencias_a}
    b = {clave(i): i for i in incidencias_b}

    nuevas = [b[k] for k in b.keys() - a.keys()]
    siguen = [b[k] for k in a.keys() & b.keys()]

    idas, sin_pagina = [], []
    for k in a.keys() - b.keys():
        if urls_del_nuevo is not None and k[1] not in urls_del_nuevo:
            sin_pagina.append(a[k])      # su pagina no se volvio a visitar
        else:
            idas.append(a[k])

    def por_tipo(lista):
        cuenta: dict[str, dict[str, Any]] = {}
        for i in lista:
            fila = cuenta.setdefault(
                i["issue_type"], {"tipo": i["issue_type"],
                                  "severidad": i.get("severity"), "total": 0})
            fila["total"] += 1
        orden = {"error": 0, "warning": 1, "info": 2}
        return sorted(cuenta.values(),
                      key=lambda f: (orden.get(f["severidad"], 3), -f["total"]))

    resultado = {
        "comparable": True,
        "nuevas": {"total": len(nuevas), "por_tipo": por_tipo(nuevas),
                   "ejemplos": [i["url"] for i in nuevas[:MUESTRA]]},
        "siguen": {"total": len(siguen), "por_tipo": por_tipo(siguen)},
    }
    resultado["sin_pagina_en_el_nuevo"] = len(sin_pagina)
    if puede_afirmar_resueltas:
        resultado["resueltas"] = {
            "total": len(idas), "por_tipo": por_tipo(idas),
            "ejemplos": [i["url"] for i in idas[:MUESTRA]]}
        resultado["resueltas_no_afirmables"] = 0
    else:
        # Ni se enumeran: una lista de "quizas resueltas" se lee como una
        # lista de resueltas en cuanto alguien la copia a un informe.
        resultado["resueltas"] = None
        resultado["resueltas_no_afirmables"] = len(idas) + len(sin_pagina)
        resultado["sin_pagina_en_el_nuevo"] = 0
    return resultado


# --- Acceso a base de datos -------------------------------------------------
#
# Las dos consultas viven aqui y no en el router de la API porque las necesitan
# los DOS: el endpoint y el worker, que compara cada rastreo recien terminado
# con el anterior del mismo sitio. Tenerlas por duplicado es como se llega a un
# desacuerdo silencioso entre dos cifras que dicen medir lo mismo (decisiones
# 23 y 39).


def paginas_de_censo(db, job_id) -> list[dict[str, Any]]:
    """Las páginas HTML de un censo con los campos que se comparan.

    Solo HTML: comparar el `title` de un PDF o de una imagen no dice nada, y
    el ruido taparía los cambios que sí importan.
    """
    from sqlalchemy import func, or_

    from shared.models import Heading, HtmlMeta, Url

    filas = (
        db.query(
            Url.url, Url.status_code, Url.indexability_status, Url.word_count,
            HtmlMeta.canonical_href, HtmlMeta.title,
        )
        .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
        .filter(Url.job_id == job_id, Url.is_internal.is_(True), Url.is_html.is_(True))
        .all()
    )
    # El primer h1 que se PINTA de cada página (decisión 46: los ocultos se
    # guardan pero no cuentan).
    h1s = dict(
        db.query(Url.url, func.min(Heading.text))
        .join(Heading, Heading.url_id == Url.id)
        .filter(
            Url.job_id == job_id, Heading.tag == "h1",
            or_(Heading.oculto.is_(False), Heading.oculto.is_(None)),
        )
        .group_by(Url.url)
        .all()
    )
    return [
        {
            "url": f.url, "status_code": f.status_code,
            "indexability_status": f.indexability_status,
            "canonical_href": f.canonical_href, "title": f.title,
            "h1": h1s.get(f.url), "word_count": f.word_count,
        }
        for f in filas
    ]


def resumen_de_job(job) -> dict[str, Any]:
    """Lo que la comparación necesita saber del rastreo, no de sus páginas."""
    return {
        "semilla": (job.seeds or [None])[0],
        # Las semillas ENTERAS, no solo la primera: dos rastreos del mismo host
        # pueden tener dos alcances distintos, y entonces lo que "falta" puede
        # no haber estado nunca en el alcance (decisión 70).
        "semillas": list(job.seeds or []),
        "render_js": (job.config or {}).get("render_js"),
        "status": job.status,
        "finish_reason": job.finish_reason,
        "crawler_version": job.crawler_version,
        "analisis_version": getattr(job, "analisis_version", None),
        "nombre": job.name,
        "fecha": job.started_at.isoformat() if job.started_at else None,
    }


def censo_anterior(db, job):
    """Con qué censo hay que comparar este, si hay alguno.

    **Si el rastreo lo lanzó una programación, se compara con el anterior de
    ESA programación**, no con lo último que pasara por ese host. La regla del
    host sola elige mal en cuanto alguien lanza algo a mano: comprobado el
    2026-10-09 en producción, el segundo canario de Lopesan se comparó con el
    primero —117 URLs en común de 250— en vez de con el censo completo. Llevado
    a un rastreo semanal de 5.000 páginas, se compararía contra un canario de
    250 que alguien lanzó el martes.

    Y no es cosmético: **las alertas se miden sobre las páginas que están en
    los dos censos**. Si la referencia cubre 250 páginas y el fallo afecta a
    2.319 que no están entre ellas, la alerta NO salta. El guardia de alcance
    (decisión 70) evita afirmar ausencias falsas, pero no evita ese silencio, y
    un silencio se lee igual que «no ha cambiado nada».

    Para un rastreo lanzado a mano se mantiene la regla del host: es lo único
    que se sabe de él.
    """
    from shared.models import Job

    if not job.seeds or not job.started_at:
        return None

    programacion = getattr(job, "programacion_id", None)
    if programacion:
        de_la_serie = (
            db.query(Job)
            .filter(Job.id != job.id, Job.programacion_id == programacion,
                    Job.status == "completed", Job.started_at.isnot(None),
                    Job.started_at < job.started_at)
            .order_by(Job.started_at.desc())
            .first()
        )
        if de_la_serie is not None:
            return de_la_serie
        # Primera ejecución de la serie: se cae a la regla del host, que ahora
        # elige por ALCANCE y no solo por fecha. Ver abajo.

    host = _host(job.seeds[0])
    if not host:
        return None
    # Se recorre de más reciente a más antiguo. En lotes y no con un `limit`:
    # con un tope fijo, en una instalación con varios clientes los rastreos de
    # los demás se comen la ventana y el censo anterior de ESTE sitio queda
    # fuera — y la ausencia de alerta se lee igual que «no ha cambiado nada».
    # El host no se puede filtrar en SQL sin atarse a Postgres: `seeds` es JSON
    # y la primera semilla no es una columna.
    #
    # Y entre los del mismo host se prefiere el que ABARQUE lo mismo, no el más
    # reciente. Medido en producción el 2026-10-13: el primer censo semanal de
    # Lopesan (3.040 semillas) eligió como referencia un **rastreo de prueba de
    # 40 URLs** de cuatro días antes, porque era el más reciente de ese host.
    # Resultado: 40 URLs en común de 3.884. Los guardias hicieron su trabajo
    # —no concluyente, sin alertas, nada falso afirmado— pero con 40 páginas en
    # común no se habría visto nada **aunque el sitio se hubiera roto**, y eso
    # se lee igual que «no ha cambiado nada», que es contra lo que existen las
    # decisiones 67 a 78.
    #
    # «Abarcar lo mismo» es la contención de la decisión 70: que las semillas
    # del candidato cubran las de este censo. Si ninguno la cumple se devuelve
    # el más reciente, que es lo de antes: comparar con algo del mismo sitio
    # sigue siendo mejor que no comparar con nada.
    consulta = (
        db.query(Job)
        .filter(Job.id != job.id, Job.status == "completed",
                Job.started_at.isnot(None), Job.started_at < job.started_at)
        .order_by(Job.started_at.desc())
    )
    mias = {_norm(u) for u in (job.seeds or [])}
    mas_reciente = None
    for c in consulta.yield_per(200):
        if not c.seeds or _host(c.seeds[0]) != host:
            continue
        if mas_reciente is None:
            mas_reciente = c
        if mias and mias <= {_norm(u) for u in c.seeds}:
            return c
    return mas_reciente


REANALIZAR_LA_REFERENCIA = os.getenv("REANALIZAR_LA_REFERENCIA", "1") != "0"


def _poner_al_dia_el_anterior(db, anterior, job) -> bool:
    """Re-analiza el censo de referencia si se analizó con otro código.

    Sin esto, la decisión 91 deja la comparación de incidencias **inservible en
    la práctica**: se despliega a diario, así que entre dos rastreos de una
    serie semanal casi siempre habrá cambiado el analizador, y entonces no se
    dan cifras. Visto en el primer rastreo automático de Lopesan, el
    2026-10-14: los dos censos salieron con `6ae6004` y `c5d863e` y el diff se
    calló. Callarse es mejor que mentir, pero no es una función que sirva.

    Volver a analizar el anterior es barato —246 s en ese censo— y lo deja
    comparable. Lo que NO es gratis es que **cambia sus cifras guardadas**: si
    alguien exportó un CSV de ese censo, ya no cuadrará. Por eso se registra en
    el log y viaja en el resultado como `anterior_reanalizado`, y por eso se
    puede apagar con `REANALIZAR_LA_REFERENCIA=0`.

    Devuelve si se volvió a analizar.
    """
    if not REANALIZAR_LA_REFERENCIA:
        return False
    actual = getattr(job, "analisis_version", None)
    previa = getattr(anterior, "analisis_version", None)
    if not actual or previa == actual:
        return False
    try:
        from analysis.analyzer import run_analysis
    except ImportError:
        # La imagen de la API no lleva `analysis/`: ahi no se re-analiza nada,
        # solo se compara lo que haya.
        return False
    inicio = time.time()
    logger.info(
        "El censo de referencia %s se analizo con %s y este con %s: se vuelve "
        "a analizar para poder comparar las incidencias",
        anterior.id, previa or "(desconocida)", actual)
    try:
        run_analysis(str(anterior.id))
    except Exception:
        logger.exception(
            "No se pudo re-analizar %s; la comparacion de incidencias se "
            "quedara sin hacer, pero las alertas de pagina siguen", anterior.id)
        return False
    db.expire(anterior)
    logger.info("Censo de referencia %s re-analizado en %.0f s",
                anterior.id, time.time() - inicio)
    return True


def comparar_con_el_anterior(db, job) -> dict[str, Any] | None:
    """Compara un rastreo recién terminado con el anterior del mismo sitio.

    Es lo que convierte la comparación en una alerta: nadie va a entrar a
    elegir dos censos en un desplegable para enterarse de que media sección se
    ha ido del índice. En Lopesan eso pasó y se entregó como un aviso `info`
    entre 315.119 incidencias.
    """
    anterior = censo_anterior(db, job)
    if anterior is None:
        return None
    reanalizado = _poner_al_dia_el_anterior(db, anterior, job)
    resumen_anterior = resumen_de_job(anterior)
    r = comparar_censos(
        paginas_de_censo(db, anterior.id),
        paginas_de_censo(db, job.id),
        job_a=resumen_anterior,
        job_b=resumen_de_job(job),
    )
    incidencias = None
    if r.get("comparable"):
        # El diff de incidencias va aparte y NO puede tumbar el de paginas: las
        # alertas criticas —media seccion fuera del indice— son lo que hay que
        # entregar aunque el recuento de "que se arreglo" falle. Antes de esto,
        # cualquier problema con `issues` habria dejado `comparacion` a NULL
        # por el best-effort del worker, y con ella las alertas.
        try:
            # «Resuelta» solo se puede afirmar si el censo ANTERIOR esta
            # entero: si paro por `max_urls`, que un hallazgo no aparezca ahora
            # puede ser que no se llegara a su pagina.
            anterior_entero = not _truncado(
                resumen_anterior.get("status"),
                resumen_anterior.get("finish_reason"))
            paginas_nuevas = paginas_de_censo(db, job.id)
            incidencias = comparar_incidencias(
                incidencias_de_censo(db, anterior.id),
                incidencias_de_censo(db, job.id),
                puede_afirmar_resueltas=anterior_entero,
                urls_del_nuevo={_norm(p["url"]) for p in paginas_nuevas},
                analisis_a=resumen_anterior.get("analisis_version"),
                analisis_b=getattr(job, "analisis_version", None),
            )
        except Exception:
            logger.exception(
                "No se pudo comparar las incidencias de %s con %s; las alertas "
                "de pagina siguen", job.id, anterior.id)
    # Lo que se guarda es el veredicto, no el censo entero: los ejemplos y las
    # plantillas ya van dentro de cada alerta.
    return {
        "incidencias": incidencias,
        # Si el censo anterior se volvio a analizar para poder comparar, se
        # dice: sus cifras han cambiado respecto a lo que se viera antes, y
        # quien mire un informe viejo de ese censo tiene derecho a saberlo.
        "anterior_reanalizado": reanalizado,
        "comparado_con": str(anterior.id),
        "nombre_anterior": anterior.name,
        "fecha_anterior": resumen_de_job(anterior)["fecha"],
        "comparable": r.get("comparable", False),
        "motivo": r.get("motivo"),
        "concluyente": r.get("concluyente"),
        "avisos": r.get("avisos", []),
        "urls": r.get("urls"),
        "alertas": r.get("alertas", []),
    }
