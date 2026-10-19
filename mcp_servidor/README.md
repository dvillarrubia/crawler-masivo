# Servidor MCP del crawler (solo lectura)

Para preguntarle a Claude Code o a Cowork por los datos del crawler sin abrir
la interfaz: «¿qué ha cambiado en Lopesan?», «¿qué arreglo primero en el último
censo de Saunier?», «¿qué páginas con tráfico de Google dan 404?».

## Instalar (en el ordenador de quien lo usa)

```bash
pip install -r mcp_servidor/requirements.txt
claude mcp add crawler -- python /ruta/al/repo/mcp_servidor/servidor.py
```

Lee el token de `CRAWLER_API_TOKEN` o de `~/.config/crawler-masivo/api_token`
(el mismo que la interfaz), y la dirección de `CRAWLER_API_URL` (por defecto,
la de producción).

## Herramientas

Todas de solo lectura: no lanza, borra ni cambia nada.

| Herramienta | Qué devuelve |
|---|---|
| `clientes` | Cada cliente con su último censo, tendencia de errores y alertas abiertas |
| `rastreos` | Los últimos rastreos, de todos o de un cliente |
| `informe` | El informe de un censo (o del último del cliente) |
| `problemas` | Los tipos de incidencia por prioridad o por gravedad |
| `urls` | URLs con los filtros del Explorador |
| `comparar` | Qué cambió entre dos censos |
| `alertas` | Las alertas con estado de un cliente |
| `search_console` | Estado de Search Console de un cliente |
