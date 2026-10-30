# Instalar en Mac

Vale para Apple Silicon (M1-M4) e Intel. Comprobada de cero en Apple Silicon
con Docker Desktop y Compose v2.32.

## 1. Requisitos

- macOS 13 o posterior.
- **16 GB de RAM** en el Mac, para poder dar 8 GB a Docker (con 8 GB de RAM se
  puede, dando 6 a Docker y sin rastreos con JavaScript pesados).
- **15 GB libres** de disco: las imágenes, con Chromium, pesan varios GB, y la
  base de datos crece con cada rastreo.
- Acceso al repo en GitHub.

## 2. Instalar las herramientas

**Git y Python 3** vienen con las herramientas de línea de comandos de Apple.
En la Terminal:

```bash
xcode-select --install
```

Si dice que ya están instaladas, sigue. Python 3 solo hace falta para lanzar
rastreos desde la terminal; desde la interfaz no.

**Docker Desktop:** descárgalo de <https://www.docker.com/products/docker-desktop/>
eligiendo **Apple Silicon** o **Intel** según tu Mac ( → *Acerca de este Mac*,
campo *Chip* o *Procesador*). Ábrelo una vez y acepta los permisos.

Después, en Docker Desktop → **Settings → Resources**:

- **Memory:** 8 GB (6 como mínimo). El worker se reserva 2 GB y Chromium los
  usa cuando un rastreo renderiza JavaScript.
- **Disk usage limit:** 64 GB o más.

*Apply & restart*. Comprueba en la Terminal:

```bash
docker compose version     # v2.20 o más
```

## 3. Descargar el repo

```bash
cd ~
git clone https://github.com/dvillarrubia/crawler-masivo.git
cd crawler-masivo
```

Si GitHub te pide contraseña, no vale la de tu cuenta: usa `gh auth login`
(instala `gh` con Homebrew) o un token personal.

## 4. Crear el `.env`

```bash
cp .env.example .env
openssl rand -hex 24       # copia lo que salga
open -e .env               # se abre en TextEdit
```

En el `.env`, pega lo que ha salido en la línea `API_TOKEN=`:

```dotenv
API_TOKEN=7f3a…(48 caracteres)
```

Es la clave con la que entrarás en la interfaz. Sin ella la API responde 503
a todo: está cerrada, no abierta. El resto de valores valen tal cual en tu
ordenador. El `.env` no se sube a git; no lo compartas por Slack.

## 5. Arrancar

```bash
docker compose up -d --build
```

La **primera vez** tarda: compila algunas dependencias de Python para ARM y
descarga Chromium. Cuenta con 10-20 minutos. Las siguientes van en un par de
minutos.

La API crea la base de datos al arrancar. En el log del worker puede salir,
solo en los primeros segundos, un error con `relation "clients" does not exist`:
el worker ha mirado antes de que la API creara las tablas. No hay que hacer
nada.

## 6. Comprobar que funciona

```bash
docker compose ps
```

Los cuatro servicios (`postgres`, `redis`, `api`, `crawler`) tienen que estar
`Up`, y Postgres y Redis además `(healthy)`.

```bash
curl http://127.0.0.1:8000/health        # {"status":"ok"}
```

Abre <http://127.0.0.1:8000>: te lleva a `/entrar`, donde se pega el
`API_TOKEN` del `.env`.

**Sigue en [`uso.md`](uso.md#primer-rastreo)** para el primer rastreo.

## Problemas propios del Mac

**`port is already allocated`.** Otro programa usa el 8000, el 5432 o el 6379
(un Postgres o un Redis de Homebrew, por ejemplo: `brew services list`).
Páralo o cambia los puertos: ver [`uso.md`](uso.md#problemas-frecuentes).

**`Cannot connect to the Docker daemon`.** Docker Desktop no está abierto.
Ábrelo y espera a que la ballena de la barra de menús deje de moverse.

**El Mac va lento o el ventilador no para.** Durante un rastreo con
JavaScript, Chromium usa CPU de verdad. Baja `JS_CONCURRENT_REQUESTS` y
`PLAYWRIGHT_MAX_PAGES` en el `.env` (por ejemplo a 2) y haz
`docker compose up -d`. Cuando no lo uses, `docker compose stop` lo para sin
perder nada.

**Docker ocupa mucho disco.** Cada `--build` deja imágenes viejas.
`docker image prune` borra las que no usa ningún contenedor; no toca tus datos.
