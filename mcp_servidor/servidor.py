"""Servidor MCP de solo lectura sobre la API del crawler (decision 148).

    claude mcp add crawler -- python /ruta/al/repo/mcp_servidor/servidor.py

Lee el token de `CRAWLER_API_TOKEN` o de `~/.config/crawler-masivo/api_token`
y la direccion de `CRAWLER_API_URL` (por defecto, produccion). Todas las
herramientas van marcadas como de solo lectura.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from mcp_servidor import herramientas as H  # noqa: E402

servidor = MCPServer("crawler-seo", instructions=(
    "Datos del crawler SEO de Lin3s: clientes, censos (rastreos completos de un sitio), "
    "informe de cada censo, problemas por prioridad, URLs, comparación entre censos, "
    "alertas abiertas y Search Console. Solo lectura. Para «qué arreglar primero», usa "
    "`problemas` con orden prioridad; para una visión de conjunto, `informe`."))
LECTURA = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
_api: H.Api | None = None


def api() -> H.Api:
    global _api
    if _api is None:
        _api = H.Api()
    return _api


@servidor.tool(annotations=LECTURA)
def clientes() -> list[dict]:
    """Todos los clientes con su último censo, la tendencia de errores y las alertas abiertas."""
    return H.clientes(api())


@servidor.tool(annotations=LECTURA)
def rastreos(cliente: str | None = None, limite: int = 10) -> list[dict]:
    """Los últimos rastreos, de todos o de un cliente (id, slug o nombre)."""
    return H.rastreos(api(), cliente, limite)


@servidor.tool(annotations=LECTURA)
def informe(rastreo: str | None = None, cliente: str | None = None) -> dict:
    """El informe de un censo; sin `rastreo`, el último censo del cliente."""
    return H.informe(api(), rastreo, cliente)


@servidor.tool(annotations=LECTURA)
def problemas(rastreo: str, orden: str = "prioridad", gravedad: str | None = None, limite: int = 30) -> list[dict]:
    """Los tipos de incidencia de un censo, por prioridad o por gravedad (error, warning, info)."""
    return H.problemas(api(), rastreo, orden, gravedad, limite)


@servidor.tool(annotations=LECTURA)
def urls(rastreo: str, filtros: dict | None = None, orden: str | None = None,
         descendente: bool = True, limite: int = 50) -> dict:
    """URLs de un censo con filtros del Explorador (status_group=4xx, issue_type=..., indexable=true, gsc_clics_gte=10...)."""
    return H.urls(api(), rastreo, filtros, orden, descendente, limite)


@servidor.tool(annotations=LECTURA)
def comparar(rastreo: str, con: str) -> dict:
    """Qué ha cambiado entre dos censos del mismo sitio."""
    return H.comparar(api(), rastreo, con)


@servidor.tool(annotations=LECTURA)
def alertas(cliente: str, cerradas: bool = False) -> list[dict]:
    """Las alertas con estado de un cliente: abiertas o, con `cerradas`, las resueltas."""
    return H.alertas(api(), cliente, cerradas)


@servidor.tool(annotations=LECTURA)
def search_console(cliente: str) -> list[dict]:
    """Estado de Search Console de un cliente."""
    return H.search_console(api(), cliente)


if __name__ == "__main__":
    servidor.run("stdio")
