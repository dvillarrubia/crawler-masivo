"""Prioridad de una incidencia: gravedad x importancia de las paginas (Ola 4.3,
decision 136).

Ordenar Problemas por gravedad y luego por numero de paginas pone arriba un
error en 3 paginas de etiquetas por delante de un aviso en la home y las
fichas de producto. Lo que se mira en una auditoria es otra cosa: que se
arregla PRIMERO. La prioridad de un tipo es

    peso de la gravedad x suma de la importancia de las paginas afectadas

y la importancia de una pagina, entre 0,1 y 1, sale de tres datos que ya
guarda el analisis:

- `pagerank_score` (0-100, logaritmica anclada al maximo del sitio, decision
  34): cuanta autoridad le llega. Es la senal principal y la que menos se
  adivina.
- `click_depth` (decision 90): a cuantos clics de la home esta. Sin camino
  (NULL) cuenta como la mas lejana: una isla.
- `in_sitemap`: el sitio la declara para indexar.

`indexable` NO entra a proposito: muchas incidencias graves son justo la
causa de que una pagina deje de ser indexable (un canonical a otro dominio,
un noindex puesto por error), y bajarle la importancia por eso hundiria el
hallazgo que mas importa.

Un tipo no puede afectar a mas que el sitio entero: la suma tiene de tope la
importancia de todas las paginas HTML internas con 2xx. Sin el tope, un
hallazgo de plantilla (decisiones 64 y 73: una fila por hallazgo con
`paginas_afectadas`) contaba las mismas paginas una vez por hallazgo: medido
en el censo semanal de Lopesan, `image_missing_alt` sumaba 18.527 paginas en
un sitio de 3.848.

Los pesos son un juicio, no una medida (como los topes de la decision 92):
cada gravedad vale DIEZ veces la siguiente. Con tres veces, un aviso
informativo que cubre todo el sitio (`missing_csp`, que no afecta al
posicionamiento, decision 53) quedaba por encima de los canonicals a otro
dominio, que sacan paginas del indice. Lo que si se mide es el orden que sale
en un censo real, y ese lo valida el equipo SEO.
"""

from __future__ import annotations

from sqlalchemy import case, func

PESO_GRAVEDAD = {"error": 100.0, "warning": 10.0, "info": 1.0}

# Factor por profundidad en clics. Hasta 2 clics es la portada y lo que cuelga
# del menu; a partir de 5, o sin camino desde la home, el buscador llega poco.
FACTOR_PROFUNDIDAD = ((2, 1.0), (3, 0.85), (4, 0.7))
FACTOR_LEJOS = 0.55
FACTOR_FUERA_SITEMAP = 0.85
IMPORTANCIA_MINIMA = 0.1


def importancia(pagerank_score, click_depth, in_sitemap) -> float:
    """Importancia de UNA pagina, entre 0,1 y 1. Funcion pura: la misma regla
    que `expresion_importancia` escribe en SQL (un test compara las dos)."""
    pr = max(0.0, min(100.0, float(pagerank_score or 0)))
    base = 0.25 + 0.75 * pr / 100.0
    if click_depth is None:
        prof = FACTOR_LEJOS
    else:
        prof = next((f for tope, f in FACTOR_PROFUNDIDAD if click_depth <= tope), FACTOR_LEJOS)
    sitemap = 1.0 if in_sitemap else FACTOR_FUERA_SITEMAP
    return max(IMPORTANCIA_MINIMA, base * prof * sitemap)


def expresion_importancia(url_cls):
    """La misma regla que `importancia`, como expresion SQL portable (Postgres
    y SQLite): se suma en el servidor, no trayendo cada URL a Python."""
    pr = func.coalesce(url_cls.pagerank_score, 0.0)
    pr = case((pr > 100.0, 100.0), (pr < 0.0, 0.0), else_=pr)
    base = 0.25 + 0.75 * pr / 100.0
    cd = url_cls.click_depth
    prof = case(
        (cd.is_(None), FACTOR_LEJOS),
        *[(cd <= tope, f) for tope, f in FACTOR_PROFUNDIDAD],
        else_=FACTOR_LEJOS,
    )
    sitemap = case((url_cls.in_sitemap.is_(True), 1.0), else_=FACTOR_FUERA_SITEMAP)
    valor = base * prof * sitemap
    return case((valor < IMPORTANCIA_MINIMA, IMPORTANCIA_MINIMA), else_=valor)


def prioridad(severidad: str, importancia_total: float) -> float:
    return round(PESO_GRAVEDAD.get(severidad, 1.0) * float(importancia_total or 0.0), 2)
