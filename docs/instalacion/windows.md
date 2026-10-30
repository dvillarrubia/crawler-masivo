# Instalar en Windows

Windows 10 (22H2) u 11, con Docker Desktop sobre **WSL 2**: el crawler corre
en un Linux (Ubuntu) dentro de Windows, y tú lo usas desde el navegador de
Windows como cualquier web.

> **Esta guía no está probada en un Windows real.** Los pasos dentro de Ubuntu
> son los de Linux, que sí lo están. Si algo no sale como dice, avisa para
> corregirla.

**Todos los comandos desde el paso 3 van en la terminal de Ubuntu**, no en
PowerShell ni en `cmd`.

## 1. Requisitos

- **16 GB de RAM** en el ordenador, para poder dar 8 GB a Linux (con 8 GB se
  puede, dando 6 y sin rastreos con JavaScript pesados).
- **20 GB libres** en el disco `C:`.
- La virtualización activada en la BIOS. Casi todos los portátiles la traen
  activada; si `wsl --install` se queja de ella, es esto.
- Permisos de administrador para instalar WSL y Docker Desktop. En un portátil
  gestionado por la empresa, pide que te lo instalen.
- Acceso al repo en GitHub.

## 2. Instalar WSL 2 y Docker Desktop

**WSL con Ubuntu.** En PowerShell **como administrador**
(botón derecho → *Ejecutar como administrador*):

```powershell
wsl --install -d Ubuntu
```

Reinicia cuando lo pida. Al volver se abre Ubuntu y pide un usuario y una
contraseña para Linux (no tienen que ser los de Windows; apúntalos, la
contraseña la pide `sudo`).

**Memoria para Linux.** Docker en WSL 2 usa la memoria que le dé WSL, no la
que pongas en Docker Desktop. Crea el fichero `C:\Users\<tu usuario>\.wslconfig`
(en el Bloc de notas, *Guardar como* → *Todos los archivos*, sin `.txt`):

```ini
[wsl2]
memory=8GB
swap=4GB
```

y en PowerShell, `wsl --shutdown` para que lo lea.

**Docker Desktop.** Descárgalo de
<https://www.docker.com/products/docker-desktop/> e instálalo con la opción
**Use WSL 2 instead of Hyper-V** marcada. Ábrelo y en **Settings**:

- **General:** *Use the WSL 2 based engine* marcado.
- **Resources → WSL integration:** activa **Ubuntu**.

*Apply & restart*.

## 3. Preparar Ubuntu

Abre **Ubuntu** desde el menú Inicio y comprueba que ve Docker:

```bash
docker compose version     # v2.20 o más
```

Si dice `command not found`, falta activar la integración con Ubuntu del
paso anterior.

Instala Git y Python (puede que ya estén):

```bash
sudo apt update && sudo apt install -y git python3 openssl
```

## 4. Descargar el repo, dentro de Linux

```bash
cd ~
git clone https://github.com/dvillarrubia/crawler-masivo.git
cd crawler-masivo
```

**Clónalo en `~` (tu carpeta de Linux), no en `/mnt/c/...`.** Desde las
carpetas de Windows, construir las imágenes es mucho más lento y Git puede
cambiar los finales de línea de los ficheros. Para ver los ficheros desde el
Explorador de Windows, ve a `\\wsl$\Ubuntu\home\<tu usuario>\crawler-masivo`.

Si GitHub te pide contraseña, no vale la de tu cuenta: usa un token personal
(GitHub → *Settings → Developer settings → Personal access tokens*) o
`gh auth login`.

## 5. Crear el `.env`

```bash
cp .env.example .env
openssl rand -hex 24       # copia lo que salga
nano .env
```

En `nano`, baja hasta `API_TOKEN=` y pega lo que ha salido (botón derecho
pega en la terminal). Guarda con `Ctrl+O`, `Intro`, y sal con `Ctrl+X`.

```dotenv
API_TOKEN=7f3a…(48 caracteres)
```

Es la clave con la que entrarás en la interfaz. Sin ella la API responde 503
a todo. El resto de valores valen tal cual. El `.env` no se sube a git; no lo
compartas por Slack.

Si prefieres editar con VS Code, instala la extensión **WSL** y abre la
carpeta con `code .` desde Ubuntu.

## 6. Arrancar

```bash
docker compose up -d --build
```

La **primera vez** tarda: compila algunas dependencias de Python y descarga
Chromium. Cuenta con 10-20 minutos. Las siguientes van en un par de minutos.

La API crea la base de datos al arrancar. En el log del worker puede salir,
solo en los primeros segundos, un error con `relation "clients" does not exist`:
el worker ha mirado antes de que la API creara las tablas. No hay que hacer
nada.

## 7. Comprobar que funciona

```bash
docker compose ps
```

Los cuatro servicios (`postgres`, `redis`, `api`, `crawler`) tienen que estar
`Up`, y Postgres y Redis además `(healthy)`.

```bash
curl http://127.0.0.1:8000/health        # {"status":"ok"}
```

Abre <http://localhost:8000> **en el navegador de Windows**: te lleva a
`/entrar`, donde se pega el `API_TOKEN` del `.env`.

**Sigue en [`uso.md`](uso.md#primer-rastreo)** para el primer rastreo. Los
comandos de esa guía, también en la terminal de Ubuntu.

## Problemas propios de Windows

**`docker: command not found` en Ubuntu.** Docker Desktop no está abierto, o
falta *Settings → Resources → WSL integration → Ubuntu*.

**El ordenador se queda sin memoria (`Vmmem` o `VmmemWSL` en el Administrador
de tareas).** Es Linux con Docker dentro. Baja `memory=` en el `.wslconfig` y
haz `wsl --shutdown`. Si con menos memoria el worker se muere en rastreos con
JavaScript, baja también `JS_CONCURRENT_REQUESTS` y `PLAYWRIGHT_MAX_PAGES` en
el `.env`. Cuando no lo uses, `docker compose stop` lo para sin perder nada.

**`localhost:8000` no abre en el navegador de Windows.** Prueba
<http://127.0.0.1:8000>. Si tampoco, comprueba en Ubuntu con `curl` (paso 7):
si ahí responde, reinicia Docker Desktop.

**`port is already allocated`.** Otro programa de Windows usa el 8000, el 5432
o el 6379. Cambia los puertos: ver [`uso.md`](uso.md#problemas-frecuentes).

**`wsl --install` falla por la virtualización.** Hay que activarla en la BIOS
(*Intel VT-x* o *AMD-V/SVM*), o pedírselo a quien gestione el portátil.
