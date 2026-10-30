# Instalar el crawler

Con Docker se levanta todo: la interfaz, la API, el worker que rastrea,
PostgreSQL y Redis. No hace falta instalar Python ni bases de datos.

## Elige tu guía

| Dónde | Guía | Para qué |
|---|---|---|
| Mac (Apple Silicon o Intel) | [`mac.md`](mac.md) | Usarlo o desarrollar en tu ordenador |
| Windows 10/11 | [`windows.md`](windows.md) | Lo mismo, con WSL 2 |
| Servidor Linux (VPS) | [`vps.md`](vps.md) | Una instalación del equipo, encendida siempre y accesible desde fuera |

Cuando lo tengas funcionando, [`uso.md`](uso.md) explica el primer rastreo, el
día a día, cómo actualizar, las integraciones y los problemas comunes a las
tres plataformas.

## Antes de empezar

- **Cada instalación es independiente de la producción de Lin3s.** Empieza
  vacía: no ve los clientes ni los censos del VPS de producción, y lo que
  rastrees en ella no sube a ningún sitio. Para consultar producción no hace
  falta instalar nada: usa la interfaz web o el conector MCP
  ([`mcp_servidor/README.md`](../../mcp_servidor/README.md)).
- **El repo es privado** (`github.com/dvillarrubia/crawler-masivo`). Pide a
  David que te dé acceso.
- **Rastrear es salir a internet con tu IP.** Un rastreo agresivo puede hacer
  que un WAF bloquee la IP de la oficina o del servidor. Empieza siempre por un
  canario ([`uso.md`](uso.md#primer-rastreo)).

## Qué está comprobado

| | |
|---|---|
| Mac (Apple Silicon, Docker Desktop, Compose v2.32) | Instalado de cero, entrada a la interfaz y rastreo real (29 y 30 de octubre de 2026) |
| VPS | Simulado en local: Caddy con HTTPS delante de la API, cookie de sesión `Secure`, MCP con la URL del servidor, worker con 4 GB, contraseña propia de Postgres, rastreo real, copia de seguridad y restauración. **No** probados en un servidor real: la instalación de Docker en Ubuntu, el firewall y el certificado de Let's Encrypt |
| Windows | **Sin probar en un Windows real.** Los pasos de Docker dentro de WSL son los mismos que en Linux; sí está comprobado que un `.env` con saltos de línea de Windows no rompe los contenedores |
| Actualizar una instalación existente | Probado de una versión anterior a la actual (ver [`uso.md`](uso.md#actualizar-a-la-última-versión)) |

Si sigues una guía y algo no sale como dice, avisa para corregirla.
