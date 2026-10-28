# Páginas para `tests/test_parse_atajos.py`

Comprimidas con gzip. El test ejecuta `SeoSpider.parse` sobre cada una con el
camino de extracción de antes y con el de ahora (decisión 215) y exige la
misma salida.

| Fichero | Origen | Licencia |
|---|---|---|
| `sintetica_portada.html.gz`, `sintetica_ficha.html.gz` | Generadas por el sitio sintético del banco de pruebas de #284 (megamenú, migas, pie, JSON-LD, hreflang, srcset) | Propias |
| `boe_portada.html.gz` | Portada de https://www.boe.es/ (28-oct-2026) | Reutilización de la información del sector público (Ley 37/2007), citando la fuente: Agencia Estatal Boletín Oficial del Estado |
| `wikipedia_barcelona.html.gz` | https://es.wikipedia.org/wiki/Barcelona (28-oct-2026) | CC BY-SA 4.0, autores de Wikipedia |

Páginas de terceros con derechos no van aquí (el repo es público): se pasan en
local con `PARSE_ATAJOS_PAGINAS=<carpeta con .html>`.
