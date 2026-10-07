"""C2: ¿el peso de un `nofollow` se evapora o se reparte entre los follow?

Mide, sin tocar el codigo de produccion, cuanto cambia el PageRank de un censo
real si una pagina que nofollowea parte de sus enlaces reparte SOLO la fraccion
follow (modelo de Google desde 2009) en vez del 100% entre los follow (lo que
hace hoy el codigo, que es el "PageRank sculpting" pre-2009).

La masa perdida no se destruye: se trata como masa colgante, o sea va al
teletransporte (paginas 200 indexables), igual que hace C3 con las noindex.
"""
from __future__ import annotations

import sys
import numpy as np
from sqlalchemy import text

sys.path[:0] = ["/app"]
from shared.database import SessionLocal          # noqa: E402
from analysis.analyzer import SEOAnalyzer         # noqa: E402
import analysis.pagerank as prk                   # noqa: E402

JOB = sys.argv[1]
TOP = 50

s = SessionLocal()
an = SEOAnalyzer(s, JOB)
modo = an._aristas_de_enlaces()
print(f"modo de peso: {modo}")

filas = s.execute(text(
    "SELECT u.id, u.url, u.status_code, u.is_html, u.indexable, "
    "       (u.redirect_url IS NOT NULL) FROM urls u "
    "WHERE u.job_id = :j AND u.is_internal IS TRUE ORDER BY u.id"
), {"j": JOB}).all()
ids = np.array([f[0] for f in filas], dtype=np.int64)
urls = {f[0]: f[1] for f in filas}
cats = [prk.categoria(f[2], f[3], f[4], "x" if f[5] else None) for f in filas]
n = len(ids)
print(f"nodos internos: {n:,}")

ar = s.execute(text("SELECT src, dst, w FROM pr_edges_tmp")).all()
src = np.searchsorted(ids, np.array([a[0] for a in ar], dtype=np.int64))
dst = np.searchsorted(ids, np.array([a[1] for a in ar], dtype=np.int64))
w = np.array([a[2] for a in ar], dtype=np.float64)
print(f"aristas follow: {len(w):,}")

# Fraccion nofollow por pagina de origen, sobre destinos internos DISTINTOS
# (la misma unidad que usa el grafo: una arista por destino).
frac = dict(s.execute(text(
    """
    SELECT from_url_id,
           count(DISTINCT to_url_hash) FILTER (WHERE NOT follow)::float8
             / NULLIF(count(DISTINCT to_url_hash), 0)
    FROM links WHERE job_id = :j AND is_internal
    GROUP BY 1
    """
), {"j": JOB}).all())

peso_saliente = np.bincount(src, weights=w, minlength=n)
# Peso que se va por los nofollow, estimado al peso medio de los follow de esa
# misma pagina: w_perdido = saliente * f/(1-f).
perdido = np.zeros(n)
for i, uid in enumerate(ids):
    f = frac.get(int(uid)) or 0.0
    if 0.0 < f < 1.0 and peso_saliente[i] > 0:
        perdido[i] = peso_saliente[i] * f / (1.0 - f)
afectadas = int((perdido > 0).sum())
print(f"paginas que reparten y nofollowean algo: {afectadas:,} "
      f"({afectadas / max(n,1):.1%} de los nodos)")
print(f"peso perdido / peso total saliente: "
      f"{perdido.sum() / max(peso_saliente.sum(), 1e-9):.1%}")

tele = np.array([c == "indexable" for c in cats], dtype=bool)


def correr(extra: np.ndarray) -> np.ndarray:
    total = peso_saliente + extra
    colgantes = total == 0
    wn = np.where(total[src] > 0, w / total[src], 0.0)
    v = tele / tele.sum() if tele.any() else np.full(n, 1.0 / n)
    pr = v.copy()
    for _ in range(200):
        aporte = np.bincount(dst, weights=pr[src] * wn, minlength=n)
        # Lo que no llega a ningun destino (nofollow) es masa colgante.
        fuga = float(pr.sum() - aporte.sum() - pr[colgantes].sum())
        nuevo = 0.85 * aporte + ((1 - 0.85) + 0.85 * (pr[colgantes].sum() + max(fuga, 0.0))) * v
        if np.abs(nuevo - pr).max() < 1e-9:
            pr = nuevo
            break
        pr = nuevo
    return pr / pr.sum()


a = correr(np.zeros(n))          # hoy: el nofollow no diluye
b = correr(perdido)              # Google: el nofollow consume su parte

print("\nreparto por categoria:")
ra, rb = prk.reparto(a, cats), prk.reparto(b, cats)
for c in prk.CATEGORIAS:
    if ra.get(c) or rb.get(c):
        print(f"  {c:<14} {ra[c]*100:7.2f}% -> {rb[c]*100:7.2f}%")

oa = np.argsort(-a)[:TOP]
ob = np.argsort(-b)[:TOP]
comunes = len(set(oa.tolist()) & set(ob.tolist()))
print(f"\ntop {TOP}: {comunes}/{TOP} paginas en comun")
print(f"top 10 identico y en el mismo orden: {list(oa[:10]) == list(ob[:10])}")

rank_a = np.empty(n, dtype=np.int64); rank_a[np.argsort(-a)] = np.arange(n)
rank_b = np.empty(n, dtype=np.int64); rank_b[np.argsort(-b)] = np.arange(n)
mov = np.abs(rank_a - rank_b)
print(f"movimiento de puesto: mediana {np.median(mov):.0f}, "
      f"p90 {np.percentile(mov,90):.0f}, max {mov.max():,}")

# Lo que de verdad se entrega es `pagerank_score` (0-100 logaritmica).
pa, pb = prk.puntuacion_log(a), prk.puntuacion_log(b)
dd = np.abs(np.asarray(pa, dtype=float) - np.asarray(pb, dtype=float))
print(f"\nSCORE 0-100: cambio medio {dd.mean():.2f} puntos, p99 {np.percentile(dd,99):.2f}, max {dd.max():.2f}")
print(f"SCORE paginas que cambian >=1 punto: {(dd>=1).sum():,} ({(dd>=1).mean():.2%})")
print(f"SCORE paginas que cambian >=5 puntos: {(dd>=5).sum():,} ({(dd>=5).mean():.2%})")

print("\nlos 10 que mas suben al aplicar el criterio de Google:")
for i in np.argsort(rank_b - rank_a)[:10]:
    print(f"  {rank_a[i]+1:>6} -> {rank_b[i]+1:<6} {urls[int(ids[i])][:92]}")
print("\nlos 10 que mas bajan:")
for i in np.argsort(-(rank_b - rank_a))[:10]:
    print(f"  {rank_a[i]+1:>6} -> {rank_b[i]+1:<6} {urls[int(ids[i])][:92]}")

s.rollback(); s.close()
