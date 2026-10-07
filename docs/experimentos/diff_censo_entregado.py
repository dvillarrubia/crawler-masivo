#!/usr/bin/env python3
"""Qué cambiaría en un censo ya entregado si se re-analizara hoy. Sin tocarlo.

Hace, para un job del VPS, lo que `docs/DIFF_CENSOS_ENTREGADOS.md` describe:

1. congela lo que el VPS devuelve HOY (`/stats` e `/insights`),
2. descarga el censo entero por backup y lo importa en local con un id nuevo,
3. **verifica que la copia es fiel** antes de tocar nada —sin esto, cualquier
   diferencia podría ser del transporte y no del análisis—,
4. lo re-analiza en local y compara.

Producción no se toca en ningún momento: el único trabajo que se re-analiza es
la copia local.

    python docs/experimentos/diff_censo_entregado.py <job_id_del_vps> [--local http://localhost:8000]

Depende de que el stack local esté levantado y de que la imagen del crawler
tenga el código con el que se quiere comparar.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request

VPS = "https://crawler-masivo.srv817047.hstgr.cloud"


def pedir(base: str, ruta: str, timeout: int = 300):
    with urllib.request.urlopen(base + ruta, timeout=timeout) as r:
        return json.load(r)


def categorias(insights: dict) -> dict[str, int]:
    return {c["name"]: c["score"] for c in insights.get("categories", [])}


def por_tipo(stats: dict) -> dict[str, int]:
    it = stats.get("issues_by_type") or {}
    if isinstance(it, list):
        return {x["issue_type"]: x["count"] for x in it}
    return dict(it)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_id")
    ap.add_argument("--vps", default=VPS)
    ap.add_argument("--local", default="http://localhost:8000")
    ap.add_argument("--zip", default="/tmp/censo.zip")
    args = ap.parse_args()

    print(f"1. congelando lo que el VPS devuelve hoy para {args.job_id[:8]}…")
    antes_stats = pedir(args.vps, f"/api/jobs/{args.job_id}/stats")
    antes_ins = pedir(args.vps, f"/api/jobs/{args.job_id}/insights")
    print(f"   nota {antes_ins['overall_score']}, "
          f"{sum(por_tipo(antes_stats).values()):,} incidencias")

    print("2. descargando el censo…")
    subprocess.run(
        ["curl", "-sS", "--max-time", "3600",
         f"{args.vps}/api/jobs/{args.job_id}/backup", "-o", args.zip],
        check=True,
    )

    print("3. importando en local…")
    salida = subprocess.run(
        ["curl", "-sS", "--max-time", "3600", "-X", "POST",
         f"{args.local}/api/jobs/import", "-F", f"file=@{args.zip}",
         "-F", "preserve_job_id=false"],
        check=True, capture_output=True, text=True,
    ).stdout
    nuevo = json.loads(salida)["new_job_id"]
    print(f"   copia local: {nuevo}")

    copia_ins = pedir(args.local, f"/api/jobs/{nuevo}/insights")
    if categorias(copia_ins) != categorias(antes_ins) or \
            copia_ins["overall_score"] != antes_ins["overall_score"]:
        print("   LA COPIA NO ES FIEL: el diff no valdria nada. Abortando.",
              file=sys.stderr)
        print(f"   VPS   {antes_ins['overall_score']} {categorias(antes_ins)}",
              file=sys.stderr)
        print(f"   copia {copia_ins['overall_score']} {categorias(copia_ins)}",
              file=sys.stderr)
        return 2
    print("   copia fiel (mismas notas en todas las categorias)")

    print("4. re-analizando la copia…")
    subprocess.run(
        ["docker", "run", "--rm", "--network", "crawler-masivo_default",
         "-e", "DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_db",
         "-e", "REDIS_URL=redis://redis:6379/0", "-e", "PYTHONPATH=/repo",
         "-w", "/repo", "-v", f"{subprocess.check_output(['pwd'], text=True).strip()}:/repo",
         "crawler-masivo-crawler:latest", "python", "-c",
         f"from analysis.analyzer import run_analysis; run_analysis('{nuevo}')"],
        check=True,
    )

    despues_stats = pedir(args.local, f"/api/jobs/{nuevo}/stats")
    despues_ins = pedir(args.local, f"/api/jobs/{nuevo}/insights")

    print(f"\n{'categoria':<24}{'entregado':>10}{'ahora':>8}")
    print(f"{'GLOBAL':<24}{antes_ins['overall_score']:>10}{despues_ins['overall_score']:>8}")
    for nombre, valor in categorias(antes_ins).items():
        ahora = categorias(despues_ins).get(nombre)
        marca = "" if ahora == valor else f"   {ahora - valor:+d}"
        print(f"{nombre:<24}{valor:>10}{ahora:>8}{marca}")

    a, b = por_tipo(antes_stats), por_tipo(despues_stats)
    print(f"\n{'incidencia':<34}{'entregado':>11}{'ahora':>9}")
    for t in sorted(set(a) | set(b), key=lambda t: -abs(b.get(t, 0) - a.get(t, 0)))[:18]:
        if a.get(t, 0) != b.get(t, 0):
            print(f"{t:<34}{a.get(t,0):>11,}{b.get(t,0):>9,}")
    print(f"{'TOTAL':<34}{sum(a.values()):>11,}{sum(b.values()):>9,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
