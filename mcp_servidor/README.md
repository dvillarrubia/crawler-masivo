# Servidor MCP del crawler

Para hablar con el crawler desde Claude sin abrir la interfaz: «¿qué ha cambiado
en Lopesan?», «¿qué arreglo primero en el último censo de Saunier?», «lanza un
canario de Lopesan», «programa el censo de Saunier los lunes a las 6».

## Conectarlo (decisión 151)

Vive en producción, así que no se instala nada. La URL es siempre la misma:

```
https://crawler-masivo.srv817047.hstgr.cloud/mcp
```

Al conectar se abre una página del crawler que pide la **clave de acceso de la
interfaz** (o solo un clic, si ese navegador ya ha entrado en el crawler). Cada
dispositivo o aplicación autoriza el suyo y pueden convivir todos.

- **claude.ai, Claude Desktop, el móvil y Cowork**: Ajustes → Conectores →
  Añadir conector personalizado → la URL de arriba → Conectar. Se hace una vez
  por cuenta y vale para la web, el escritorio, el móvil y Cowork. En un plan
  de equipo o empresa, lo añade quien administra la organización y cada
  persona le da a Conectar.
- **Claude Code** (cualquier ordenador, sin el repo):
  ```bash
  claude mcp add --transport http crawler https://crawler-masivo.srv817047.hstgr.cloud/mcp
  ```
  y dentro de Claude Code, `/mcp` → crawler → Autenticar.

La autorización caduca a los 90 días sin usarse, y se cae en todos los
dispositivos si cambia la clave de acceso (`API_TOKEN`).

### En local, por stdio (decisión 148)

Sigue funcionando, para Claude Code en un ordenador con el repo:

```bash
pip install -r mcp_servidor/requirements.txt
claude mcp add crawler -- python /ruta/al/repo/mcp_servidor/servidor.py
```

Lee el token de `CRAWLER_API_TOKEN` o de `~/.config/crawler-masivo/api_token`, y
la dirección de `CRAWLER_API_URL` (por defecto, la de producción).

## Herramientas

### Consultar

| Herramienta | Qué devuelve |
|---|---|
| `clientes` | Cada cliente con su último censo, tendencia de errores y alertas abiertas |
| `rastreos` | Los últimos rastreos, de todos o de un cliente |
| `perfiles` | Los perfiles de rastreo de un cliente |
| `informe` | El informe de un censo (o del último del cliente) |
| `problemas` | Los tipos de incidencia por prioridad o por gravedad |
| `urls` | URLs con los filtros del Explorador |
| `comparar` | Qué cambió entre dos censos |
| `alertas` | Las alertas con estado de un cliente |
| `search_console` | Estado de Search Console de un cliente |
| `estado_rastreo` | Cómo va un rastreo en marcha |
| `programaciones` | Los rastreos programados |
| `endpoints` | Los GET de la API, para `consultar` |
| `consultar` | Cualquier GET de la API (no las descargas: CSV, Excel, backup) |

### Cambiar (siempre en dos pasos)

Sin `confirmar` no cambian nada: dicen qué harían y qué está rastreando ya
producción. Claude te lo enseña y solo lo hace si dices que sí.

| Herramienta | Qué hace |
|---|---|
| `lanzar_rastreo` | Un rastreo con la configuración del cliente, o un canario (300 URLs, profundidad 3, 1 h) |
| `cancelar_rastreo` | Cancela uno pendiente o en marcha (lo rastreado se queda) |
| `programar_rastreo` | Un rastreo que se repite (cron) |
| `editar_programacion` | Pausar, reanudar o cambiar cuándo |

Borrar no está: ni rastreos ni programaciones (se pausan).
