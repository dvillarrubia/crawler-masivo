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
5. **El orden lo pone la FECHA, no quién llame.** «Antes» y «ahora» no pueden
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

from shared.dominios import dominio_registrable
from shared.plantillas import firma_de_ruta

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
    if not concluyente:
        cual = []
        if truncado_a:
            cual.append(f"el primero ({job_a.get('finish_reason') or job_a.get('status')})")
        if truncado_b:
            cual.append(f"el segundo ({job_b.get('finish_reason') or job_b.get('status')})")
        avisos.append(
            "NO CONCLUYENTE: " + " y ".join(cual) + " no termino entero, asi que "
            "no se puede afirmar que falte ninguna URL ni que se haya resuelto nada."
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
        "antes": job_a,
        "ahora": job_b,
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
