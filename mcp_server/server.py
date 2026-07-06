"""Servidor MCP del crawler SEO — "verbos de negocio" para agentes (B1).

Registro fino: toda la lógica vive en `verbs.py` (testeable sin el SDK mcp);
aquí solo se envuelve cada verbo como herramienta MCP y se arranca por stdio.

Uso:
    CRAWLER_API_URL=http://localhost:8000 python -m mcp_server.server
Registro en Claude Code:
    claude mcp add crawler -- python -m mcp_server.server
(ver README.md)
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_server import verbs

mcp = FastMCP("crawler-seo")

# Registra cada verbo como tool MCP conservando nombre y docstring (que es lo
# que el agente ve como descripción de la herramienta).
for _verb in verbs.VERBS:
    mcp.tool()(_verb)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
