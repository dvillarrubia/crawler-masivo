"""
Regenera la lista dorada de normalización (fixtures/normalization_golden.json).

SOLO debe ejecutarse cuando se cambie INTENCIONADAMENTE la semántica de
``normalize_url`` (p. ej. T8 con un flag nuevo). El diff del JSON resultante
es la revisión del cambio de semántica: si una URL cambia de hash sin que
esa fuera la intención, el cambio rompe la deduplicación entre crawls.

Uso:  python tests/python/regenerate_normalization_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "crawler"))

from seo_crawler.extractors import compute_url_hash, normalize_url  # noqa: E402

# 30+ URLs representativas: cada una congela una faceta de la semántica
# actual (w3lib canonicalize_url + sha256). Comentario = qué protege.
GOLDEN_URLS: list[str] = [
    # -- básicos ------------------------------------------------------------
    "https://example.com",                                     # raíz sin path → añade /
    "https://example.com/",                                    # raíz con /
    "https://Example.COM/Path/Page",                           # host lowercase, path case-sensitive
    "HTTPS://EXAMPLE.COM/A",                                   # scheme uppercase
    "https://www.example.com/a",                               # www se conserva
    # -- puertos ------------------------------------------------------------
    "https://example.com:443/a",                               # puerto por defecto https
    "http://example.com:80/a",                                 # puerto por defecto http
    "https://example.com:8080/a",                              # puerto no estándar se conserva
    # -- fragmentos y query -------------------------------------------------
    "https://example.com/a#seccion",                           # fragmento se elimina
    "https://example.com/a?b=2&a=1",                           # params se ordenan
    "https://example.com/a?",                                  # query vacía
    "https://example.com/a?x=1&x=2",                           # param repetido
    "https://example.com/a?X=1",                               # nombre de param case-sensitive
    "https://example.com/a?empty=&b=1",                        # valor vacío
    "https://example.com/search?q=hello+world&lang=es",        # + en query
    "https://example.com/a?arr[]=1&arr[]=2",                   # corchetes
    # -- tracking params: HOY SE CONSERVAN (T8 los hará configurables) ------
    "https://example.com/a?utm_source=x&id=5",
    "https://example.com/a?fbclid=123&gclid=456",
    # -- encoding -----------------------------------------------------------
    "https://example.com/a%2Fb",                               # slash codificado
    "https://example.com/a b",                                 # espacio sin codificar
    "https://example.com/ñoño",                                # no-ASCII en path
    "https://example.com/a?q=caf%C3%A9",                       # UTF-8 percent-encoded
    "https://example.com/%7Euser",                             # ~ codificada
    "https://example.com/%41bc",                               # unreserved codificado (A)
    # -- estructura de path -------------------------------------------------
    "https://example.com/trailing/",                           # trailing slash se conserva
    "https://example.com/trailing",                            # ≠ del anterior
    "https://example.com//doble//slash",                       # slashes múltiples
    "https://example.com/a/../b",                              # dot segments
    "https://example.com/a/./b",
    "https://example.com/index.html",                          # index no se recorta
    # -- casos sucios reales (issues url_*) ----------------------------------
    "https://example.com/a;jsessionid=ABC123?x=1",             # session id
    "https://user:pass@example.com/a",                         # credenciales
    "example.com/sin-esquema",                                 # sin scheme
]


def main() -> None:
    entries = [
        {
            "input": url,
            "normalized": normalize_url(url),
            "url_hash": compute_url_hash(url),
        }
        for url in GOLDEN_URLS
    ]
    out = Path(__file__).parent / "fixtures" / "normalization_golden.json"
    out.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{len(entries)} entradas escritas en {out}")


if __name__ == "__main__":
    main()
