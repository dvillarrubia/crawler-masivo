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
"""

from __future__ import annotations

from typing import Any

from shared.dominios import dominio_registrable

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


def _norm(url: str | None) -> str:
    """Forma comparable de una URL, la misma que usa el deduplicador."""
    if not url:
        return ""
    try:
        from w3lib.url import canonicalize_url

        return canonicalize_url(url, keep_fragments=False)
    except Exception:
        return url.strip().rstrip("/")


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

    nuevas = sorted(b.keys() - a.keys())
    desaparecidas = sorted(a.keys() - b.keys()) if concluyente else []

    return {
        "comparable": True,
        "concluyente": concluyente,
        "avisos": avisos,
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
