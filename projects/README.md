# projects/ — configuración por cliente

Cada cliente tiene su carpeta con un `config.json` que es, tal cual, el campo `config`
de `POST /api/jobs`. **Todo lo específico del cliente viaja en ese JSON**: el repo, las
imágenes Docker y los scripts son los mismos para todos. Las carpetas de cliente no se
versionan (solo `_ejemplo/`); guárdalas en el vault o en Drive.

```
projects/
  _ejemplo/config.json   ← plantilla para un cliente nuevo (versionada)
  <cliente>/config.json  ← reglas de ese cliente (fuera de git, ver .gitignore)
  <cliente>/README.md    ← por qué cada regla (opcional, pero ahorra rehacer el sondeo)
```

## Qué se configura por cliente

| Clave | Para qué |
|---|---|
| `_seeds` | semillas por defecto (las claves con `_` no se envían a la API) |
| `robots_mode`, `exclude_patterns`, `include_patterns` | qué se rastrea (regex o glob sobre la URL) |
| `use_sitemap`, `sitemap_urls` | ingesta de sitemaps (por defecto los de robots.txt) |
| `resource_types.*` | si se descargan imágenes, CSS, JS, PDF |
| `extraction.strip_promo_blocks` | patrones genéricos de promo (`carousel`, `newsletter`…). Ponlo a `false` si el hero con el H1 va en un carrusel |
| `extraction.custom_boilerplate_selectors` | selectores CSS del ruido propio del cliente (modales, formularios, relacionados, pie dentro de `<main>`) |
| `templates` | reglas `{nombre, patron}` (regex sobre el path, la primera que casa manda). Las usan `check_js_templates.py` (automático al acabar) y `check_content_quality.py` para muestrear por plantilla |
| `http.accept_language`, `user_agent`, `impersonate` | identidad HTTP |
| `concurrent_requests*`, `crawl_behavior.*` | ritmo; con WAF empezar en 4 / 2 / `request_delay` 0.5 |
| `crawl_behavior.autothrottle_target_concurrency` | **la concurrencia efectiva**: AutoThrottle ajusta el retardo para mantener esta media, por debajo de `concurrent_requests*`. Un 2.0 heredado del canario dejó un rastreo con JS a 15 págs/min con 8/6 configurado. Ponlo igual que `concurrent_requests_per_domain` |

## Cómo lanzar

```bash
python scripts/lanzar_job.py <cliente> --canary               # primero, siempre
python scripts/lanzar_job.py <cliente>                        # completo, semillas del config
python scripts/lanzar_job.py <cliente> --set render_js=true   # sobreescribir una clave
python scripts/lanzar_job.py <cliente> --api "$CRAWLER_API"   # otra API (def. http://localhost:8000)
python scripts/lanzar_job.py <cliente> --dry-run              # ver el JSON sin enviarlo
```

## Alta de un cliente nuevo

1. `cp -r projects/_ejemplo projects/<cliente>` y poner `_seeds` y `accept_language`.
2. Sondear (`crawl_toolkit.py probe`) y lanzar el canario.
3. Con `check_content_quality.py <job> --muestras 3` y una consulta a `page_content`,
   ajustar `custom_boilerplate_selectors` y `strip_promo_blocks` hasta que el texto
   empiece en el H1 y acabe en el último párrafo, sin pie ni formularios.
4. Escribir `templates` con las rutas del cliente.
5. Lanzar el completo. Documentar en `projects/<cliente>/README.md` lo que costó descubrir.
