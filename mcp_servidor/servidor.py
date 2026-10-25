"""Servidor MCP sobre la API del crawler (decisiones 148 y 151).

En local, por stdio (Claude Code en el ordenador de quien lo usa):

    claude mcp add crawler -- python /ruta/al/repo/mcp_servidor/servidor.py

Lee el token de `CRAWLER_API_TOKEN` o de `~/.config/crawler-masivo/api_token`
y la direccion de `CRAWLER_API_URL` (por defecto, produccion). En remoto
(claude.ai, Cowork, Claude Code sin instalar nada) lo sirve la propia API en
`/mcp` con OAuth: `mcp_servidor/remoto.py`. Las herramientas son las mismas:
las de lectura, marcadas como tales, y las que lanzan, cancelan o programan
rastreos (`acciones.py`), que solo actuan con `confirmar=True`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from mcp_servidor import acciones as A  # noqa: E402
from mcp_servidor import herramientas as H  # noqa: E402

INSTRUCCIONES = (
    "Datos del crawler SEO de Lin3s: clientes, censos (rastreos completos de un sitio), "
    "informe de cada censo, problemas por prioridad, URLs, comparación entre censos, "
    "alertas abiertas y Search Console. Para «qué arreglar primero», usa `problemas` con orden "
    "prioridad; para una visión de conjunto, `informe`; para lo que no tenga herramienta, "
    "`endpoints` y `consultar`. Lanzar, cancelar y programar rastreos van en dos pasos: primero "
    "sin `confirmar` (no cambia nada y dice qué haría y qué está rastreando ya producción), se "
    "lo enseñas a quien te habla, y solo si lo aprueba, otra vez con confirmar=true. Producción "
    "es compartida: si ya hay un rastreo en marcha, dilo antes de lanzar otro.")
LECTURA = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
ESCRITURA = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                            open_world_hint=True)
CORTA = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True,
                        open_world_hint=False)
_api: H.Api | None = None


def _api_local() -> H.Api:
    global _api
    if _api is None:
        _api = H.Api()
    return _api


def crear_servidor(api: Callable[[], H.Api] = _api_local, **opciones: Any) -> MCPServer:
    """El servidor con sus herramientas: las de lectura (148) y, desde la 151,
    lanzar, cancelar y programar rastreos y la consulta libre. `api` da el cliente HTTP (el de
    quien lo usa en stdio, el interno en remoto); `opciones` van a MCPServer
    (la autenticacion del remoto)."""
    servidor = MCPServer("crawler-seo", instructions=INSTRUCCIONES, **opciones)

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
    def problemas(rastreo: str, orden: str = "prioridad", gravedad: str | None = None,
                  limite: int = 30) -> list[dict]:
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
        """Las alertas con estado de un cliente: abiertas o, con `cerradas`, las cerradas.
        Con `motivo_cierre` (serie_pausada o serie_borrada) no se resolvieron: se dejo de
        rastrear su serie y nadie comprobo si siguen."""
        return H.alertas(api(), cliente, cerradas)

    @servidor.tool(annotations=LECTURA)
    def search_console(cliente: str) -> list[dict]:
        """Estado de Search Console de un cliente."""
        return H.search_console(api(), cliente)

    @servidor.tool(annotations=LECTURA)
    def endpoints(filtro: str | None = None) -> list[dict]:
        """Los GET de la API (ruta, qué da, parámetros) para usarlos con `consultar`. `filtro` busca en ruta y descripción."""
        return H.endpoints(api(), filtro)

    @servidor.tool(annotations=LECTURA)
    def consultar(ruta: str, parametros: dict | None = None) -> Any:
        """Cualquier GET de la API, sin el prefijo /api (p. ej. `/jobs/<id>/urls/detalle`, `/clients/<id>/evolucion`). Los filtros, en `parametros`."""
        return H.consultar(api(), ruta, parametros)

    @servidor.tool(annotations=LECTURA)
    def estado_rastreo(rastreo: str) -> dict:
        """Cómo va un rastreo: estado, URLs rastreadas, cola pendiente y motivo de fin."""
        return A.estado_rastreo(api(), rastreo)

    @servidor.tool(annotations=LECTURA)
    def perfiles(cliente: str) -> list[dict]:
        """Los perfiles de rastreo de un cliente (semillas y configuración propias)."""
        return H.perfiles(api(), cliente)

    @servidor.tool(annotations=LECTURA)
    def programaciones(cliente: str | None = None) -> list[dict]:
        """Los rastreos programados, de todos o de un cliente: cron, si está activa y la próxima vez."""
        return A.programaciones(api(), cliente)

    @servidor.tool(annotations=ESCRITURA)
    def lanzar_rastreo(cliente: str, perfil: str | None = None, canario: bool = False,
                       ajustes: dict | None = None, semillas: list[str] | None = None,
                       nombre: str | None = None, confirmar: bool = False) -> dict:
        """Lanza un rastreo de un cliente con su configuración (o un canario: 300 URLs, profundidad 3, 1 h). Sin `confirmar` solo simula. `ajustes` cambia claves de la config (`max_urls`, `render_js`, `crawl_behavior.request_delay`...)."""
        return A.lanzar_rastreo(api(), cliente, perfil, canario, ajustes, semillas, nombre, confirmar)

    @servidor.tool(annotations=CORTA)
    def cancelar_rastreo(rastreo: str, confirmar: bool = False) -> dict:
        """Cancela un rastreo pendiente o en marcha (lo rastreado se queda). Sin `confirmar` solo dice cuál."""
        return A.cancelar_rastreo(api(), rastreo, confirmar)

    @servidor.tool(annotations=ESCRITURA)
    def programar_rastreo(cliente: str, cron: str, perfil: str | None = None, canario: bool = False,
                          zona_horaria: str = "Europe/Madrid", ajustes: dict | None = None,
                          semillas: list[str] | None = None, nombre: str | None = None,
                          confirmar: bool = False) -> dict:
        """Programa un rastreo que se repite (cron de cinco campos: `0 6 * * 1` = lunes a las 6). Sin `confirmar` solo simula."""
        return A.programar_rastreo(api(), cliente, cron, perfil, canario, zona_horaria, ajustes, semillas,
                                   nombre, confirmar)

    @servidor.tool(annotations=ESCRITURA)
    def editar_programacion(programacion: str, activa: bool | None = None, cron: str | None = None,
                            zona_horaria: str | None = None, confirmar: bool = False) -> dict:
        """Pausa (`activa=false`), reanuda o cambia el cuándo de un rastreo programado. Sin `confirmar` solo simula."""
        return A.editar_programacion(api(), programacion, activa, cron, zona_horaria, confirmar)

    return servidor


if __name__ == "__main__":
    crear_servidor().run("stdio")
