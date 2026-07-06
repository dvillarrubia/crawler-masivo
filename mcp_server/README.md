# Servidor MCP del crawler SEO (B1)

Expone el crawler como **verbos de negocio** para agentes (Claude Code y
cualquier cliente MCP): lanzar rastreos, seguir su estado, sacar el resumen
de incidencias y preguntar por los datos de un job — sin hablar con los 8
routers REST de bajo nivel.

Es una capa **fina sobre la API REST** (una sola fuente de verdad): no toca
la base de datos ni Redis; llama a `http://localhost:8000` por HTTP.

## Verbos disponibles

| Verbo | Qué hace |
|-------|----------|
| `listar_proyectos()` | Proyectos (clientes) con nº de rastreos |
| `listar_rastreos(proyecto?, estado?, limite?)` | Rastreos, del más reciente |
| `lanzar_rastreo(url, nombre?, proyecto?, max_urls?, max_prof?, render_js?)` | Crea y encola un rastreo → devuelve `job_id` |
| `estado_rastreo(job_id)` | Estado + avance en vivo |
| `resumen_rastreo(job_id)` | Resumen ejecutivo (URLs, incidencias por severidad, hosts, latencia) |
| `top_incidencias(job_id, limite?)` | Incidencias más frecuentes con severidad |
| `buscar_urls(job_id, contiene?, grupo_estado?, limite?)` | Busca URLs (código, indexable, PageRank) |
| `detalle_url(job_id, url_id)` | Ficha completa de una URL |
| `preguntar_a_los_datos(job_id, pregunta)` | Dossier compacto del rastreo para responder una pregunta |
| `cancelar_rastreo(job_id)` | Cancela un rastreo en curso |

## Requisitos

```bash
pip install -r mcp_server/requirements.txt
```

La API del crawler debe estar corriendo (`docker compose up -d`). El
servidor MCP lee la URL de la API de `CRAWLER_API_URL` (por defecto
`http://localhost:8000`).

## Registrar en Claude Code

Desde la raíz del repo:

```bash
claude mcp add crawler -- python -m mcp_server.server
```

O con una URL de API distinta:

```bash
claude mcp add crawler -e CRAWLER_API_URL=http://localhost:8000 -- python -m mcp_server.server
```

### Con autenticación activada (API key por proyecto)

Si la API corre con `API_AUTH_ENABLED=1`, pasa la key del proyecto en
`CRAWLER_API_KEY`. La key ata el MCP a ESE proyecto: solo verá y tocará sus
datos.

```bash
claude mcp add crawler \
  -e CRAWLER_API_URL=http://localhost:8000 \
  -e CRAWLER_API_KEY=sk_tu_clave_del_proyecto \
  -- python -m mcp_server.server
```

Para emitir una key: `POST /api/clients/{proyecto}/api-keys` con el header
`X-Admin-Token: <ADMIN_TOKEN>` (la clave entera se devuelve una sola vez).

Comprueba que está conectado con `/mcp` dentro de Claude Code. Luego ya
puedes pedir cosas como:

> «Lanza un rastreo de https://ejemplo.com y cuando acabe dame el top de
> incidencias» — el agente encadena `lanzar_rastreo` → `estado_rastreo` →
> `top_incidencias`.

## Prueba rápida sin Claude (stdio manual)

```bash
CRAWLER_API_URL=http://localhost:8000 python -m mcp_server.server
```

Queda a la espera por stdio (protocolo MCP). Para una prueba funcional de
la lógica sin el protocolo, ver `tests/python/test_mcp_server.py` (mockea la
capa HTTP).

## Seguridad (pendiente, fuera del POC)

La API no tiene autenticación (CORS abierto). Este servidor asume acceso
local de confianza. Antes de exponerlo: API key por proyecto o un proxy
autenticado. Es el «B1 · auth» del backlog.
