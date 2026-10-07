#!/usr/bin/env python3
"""Lanza un rastreo con la configuracion de un cliente (projects/<cliente>/config.json).

    python scripts/lanzar_job.py <cliente> --canary                 # semillas de _seeds del config
    python scripts/lanzar_job.py <cliente> --seed https://www.ejemplo.com/es --seed https://www.ejemplo.com/en
    python scripts/lanzar_job.py <cliente> --seeds-file seeds.txt --api "$CRAWLER_API"
    python scripts/lanzar_job.py <cliente> --set render_js=true --set max_urls=1000

Todo lo que es del cliente (filtros, selectores de plantilla, plantillas, idioma,
ritmo) vive en su JSON; este script solo lo envia a la API. Sin dependencias.

Sale con 3 si la API ha descartado alguna clave de la configuracion: el job
queda encolado, pero NO con lo que se pidio.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Un canario: pocas URLs, poca profundidad, una hora. Lo demas igual que el
# rastreo completo para que valide la MISMA configuracion.
CANARY = {
    "max_depth": 3, "max_urls": 300, "crawl_behavior.max_runtime_hours": 1,
    # Suave a proposito: el canario mide como trata el WAF a un rastreador
    # timido. OJO: AutoThrottle mantiene ESTA concurrencia media, por encima de
    # concurrent_requests*; en el rastreo completo vale la del config.
    "crawl_behavior.autothrottle_target_concurrency": 2.0,
}


def _poner(cfg: dict, clave: str, valor):
    partes = clave.split(".")
    d = cfg
    for p in partes[:-1]:
        d = d.setdefault(p, {})
    d[partes[-1]] = valor


def _claves(d, prefijo=""):
    """Todas las rutas de claves de un dict anidado: {"a": {"b": 1}} -> {"a.b"}."""
    salida = set()
    for k, v in d.items():
        ruta = f"{prefijo}{k}"
        if isinstance(v, dict) and v:
            salida |= _claves(v, ruta + ".")
        else:
            salida.add(ruta)
    return salida


def _lo_que_la_api_no_guardo(enviado: dict, guardado: dict) -> list[str]:
    """Claves que se enviaron y la API no guardo.

    Pydantic v2 descarta en SILENCIO lo que no esta declarado en `JobConfig`
    (decision 33): el formulario mandaba `use_sitemap`, el schema no lo
    declaraba, y desmarcar la casilla no tenia NINGUN efecto. Le pasa a
    cualquier clave nueva y a cualquier error de tecleo, en el config de un
    cliente o en un `--set`. Un rastreo que no es el que se pidio da un informe
    que nadie sabe que esta leyendo mal, asi que se dice aqui, que es el unico
    momento en que alguien esta mirando.
    """
    return sorted(_claves(enviado) - _claves(guardado))


def _parsear_valor(texto: str):
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return texto


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cliente", help="carpeta en projects/<cliente>/ con config.json")
    ap.add_argument("--seed", action="append", default=[], help="URL semilla (repetible)")
    ap.add_argument("--seeds-file", help="fichero con una URL por linea")
    ap.add_argument("--name", help="nombre del job (def. '<cliente> (full)' o 'CANARY - <cliente>')")
    ap.add_argument("--client-id", help="def. <cliente> (o <cliente>-canary)")
    ap.add_argument("--canary", action="store_true", help="300 URLs, profundidad 3, 1 h")
    ap.add_argument("--set", action="append", default=[], metavar="CLAVE=VALOR",
                    help="sobreescribir una clave del config (anidada con puntos): render_js=true, crawl_behavior.request_delay=1")
    ap.add_argument("--api", default=os.getenv("CRAWLER_API", "http://localhost:8000"))
    ap.add_argument("--dry-run", action="store_true", help="imprime el JSON y no lo envia")
    args = ap.parse_args()

    ruta = os.path.join(RAIZ, "projects", args.cliente, "config.json")
    if not os.path.exists(ruta):
        print(f"No existe {ruta}. Copia projects/_ejemplo/config.json y ajustalo.", file=sys.stderr)
        return 2
    with open(ruta, encoding="utf-8") as f:
        cfg = json.load(f)

    seeds = list(args.seed)
    if args.seeds_file:
        with open(args.seeds_file, encoding="utf-8") as f:
            seeds += [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not seeds:
        seeds = cfg.pop("_seeds", []) or []
    else:
        cfg.pop("_seeds", None)
    if not seeds:
        print("Faltan semillas: --seed, --seeds-file o '_seeds' en el config.", file=sys.stderr)
        return 2
    for k in [k for k in cfg if k.startswith("_")]:
        cfg.pop(k)  # claves de documentacion, no van a la API

    if args.canary:
        for k, v in CANARY.items():
            _poner(cfg, k, v)
    for item in args.set:
        clave, _, valor = item.partition("=")
        _poner(cfg, clave, _parsear_valor(valor))

    nombre = args.name or (f"CANARY - {args.cliente}" if args.canary else f"{args.cliente} (full)")
    client_id = args.client_id or (f"{args.cliente}-canary" if args.canary else args.cliente)
    job = {"name": nombre, "client_id": client_id, "seeds": seeds, "config": cfg}
    cuerpo = json.dumps(job, ensure_ascii=False).encode("utf-8")
    if args.dry_run:
        print(json.dumps(job, ensure_ascii=False, indent=2))
        return 0

    req = urllib.request.Request(
        f"{args.api.rstrip('/')}/api/jobs", data=cuerpo,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:800]}", file=sys.stderr)
        return 1
    print(f"{datos.get('id')} {datos.get('status')}  ->  {args.api.rstrip('/')}/api/jobs/{datos.get('id')}")

    tiradas = _lo_que_la_api_no_guardo(cfg, datos.get("config") or {})
    if tiradas:
        print("\n*** La API NO ha guardado estas claves de la configuracion:",
              file=sys.stderr)
        for k in tiradas:
            print(f"      {k}", file=sys.stderr)
        print("    El rastreo se ha encolado SIN ellas. Si es un error de tecleo,"
              " corrige el config y relanza;\n    si son claves nuevas, hay que "
              "declararlas en `JobConfig` (api/schemas.py).\n    Cancelar: "
              f"curl -X PATCH {args.api.rstrip('/')}/api/jobs/{datos.get('id')}/cancel",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
