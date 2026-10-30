# Instalar en un servidor (VPS)

Para una instalación del equipo, encendida siempre y a la que se entra desde
el navegador de cada uno. Un servidor Linux con Docker, Caddy delante para el
HTTPS, y copias de seguridad cada noche.

> **Esto NO es la producción de Lin3s.** Esa vive en su propio VPS, se
> despliega sola al mergear a `master` y la opera David
> ([`despliegue-y-entorno.md`](../referencia/despliegue-y-entorno.md)). Esta
> guía monta otra instalación, independiente y vacía.

Lo probado y lo que no, en el [`README`](README.md#qué-está-comprobado).

## 1. El servidor

Ubuntu Server 24.04 o 22.04 LTS, en cualquier proveedor (Hetzner, Hostinger,
OVH, DigitalOcean…):

| | Mínimo | Recomendado |
|---|---|---|
| CPU | 2 | 4 |
| RAM | 4 GB (con 4 GB de swap) | 8 GB |
| Disco | 40 GB SSD | 80 GB SSD o más |

El disco es lo que más crece: un sitio de 50.000 URLs ocupa varios GB en la
base de datos, y cada censo se guarda.

**Cómo se entrará.** Decide antes una de las dos:

- **Con dominio** (lo normal para un equipo): un subdominio, como
  `crawler.tuempresa.com`, con HTTPS. En el DNS, un registro **A** que apunte
  a la IP del servidor, **antes** de arrancar (Caddy pide el certificado al
  arrancar y falla si el dominio aún no apunta ahí).
- **Sin dominio**, con un túnel SSH: nadie más ve el crawler, y cada persona
  necesita acceso SSH al servidor. Vale para una sola persona o para probar.

## 2. Preparar el servidor

Entra por SSH como `root` (o con el usuario que te dé el proveedor) y crea un
usuario para el crawler:

```bash
adduser crawler
usermod -aG sudo crawler
```

Desde aquí, todo **como ese usuario** (`su - crawler`, o entra por SSH con él).

**Actualizar e instalar Git:**

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3 ufw
```

**Firewall:** solo SSH y, si usas dominio, la web.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Ojo: **Docker se salta `ufw`** con los puertos que publica. Por eso
`docker-compose.yml` publica Postgres, Redis y la API solo en `127.0.0.1`:
**no lo cambies a `0.0.0.0`**, o la base de datos quedaría abierta a internet.

**Swap** (imprescindible con 4 GB de RAM; con 8 GB, recomendable):

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Docker** (script oficial; instala Docker Engine y el plugin Compose):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker crawler
```

Sal y vuelve a entrar para que valga el grupo `docker`, y comprueba:

```bash
docker compose version     # v2.20 o más
```

Docker arranca solo con el servidor, y los contenedores del crawler también
(`restart: unless-stopped`).

## 3. Descargar el repo

El repo es privado. En un servidor, lo mejor es una **clave de despliegue**:
solo lee este repo, y si el servidor se compromete no da acceso a nada más.

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/crawler_deploy
cat ~/.ssh/crawler_deploy.pub
```

Pásale esa línea a David para que la añada en GitHub (repo → *Settings →
Deploy keys*, **sin** permiso de escritura). Después:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-crawler
  HostName github.com
  IdentityFile ~/.ssh/crawler_deploy
EOF
cd ~
git clone git@github-crawler:dvillarrubia/crawler-masivo.git
cd crawler-masivo
```

**Clónalo en `~/crawler-masivo`**: el nombre de la carpeta da nombre a los
volúmenes de Docker (`crawler-masivo_pgdata`…), y el script de copias de
seguridad de abajo cuenta con él.

## 4. Crear el `.env`

```bash
cp .env.example .env
chmod 600 .env
echo "API_TOKEN=$(openssl rand -hex 24)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
nano .env
```

Cambia en el `.env`:

```dotenv
# La clave de acceso a la interfaz (la primera línea que ha salido)
API_TOKEN=…

# Una contraseña de verdad para Postgres (la segunda), y LA MISMA dentro de DATABASE_URL
POSTGRES_PASSWORD=…
DATABASE_URL=postgresql+psycopg2://crawler:…@postgres:5432/crawler_db

# La dirección pública del crawler: los enlaces de los avisos llevan aquí
API_PUBLIC_URL=https://crawler.tuempresa.com
```

Y añade al final una de estas dos líneas:

```dotenv
# Con dominio: el servidor MCP anuncia esta dirección (si no, anuncia la de producción de Lin3s)
MCP_URL_PUBLICA=https://crawler.tuempresa.com
# Sin dominio: apaga el MCP remoto
MCP_REMOTO=0
```

- La contraseña de Postgres solo se lee al **crear** la base de datos (el
  primer arranque). Cambiarla después no cambia la de la base de datos.
- `.env` tiene los secretos del servidor. `chmod 600` hace que solo lo lea tu
  usuario. No lo copies a ningún sitio.
- Generando las claves con `openssl rand -hex`, no llevan caracteres que
  haya que escapar en `DATABASE_URL`.

## 5. HTTPS con Caddy (si usas dominio)

[Caddy](https://caddyserver.com) se pone delante de la API, pide y renueva solo
el certificado de Let's Encrypt, y redirige `http` a `https`. Crea dos
ficheros en `~/crawler-masivo` (git los ignora; son de este servidor):

`Caddyfile`, con tu dominio:

```
crawler.tuempresa.com {
	reverse_proxy api:8000
}
```

`docker-compose.override.yml`:

```yaml
services:
  caddy:
    image: caddy:2.8
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - api

  # Con 8 GB de RAM o más: más memoria para el worker y para Chromium.
  # Con 4 GB, borra este bloque.
  crawler:
    shm_size: '2gb'
    deploy:
      resources:
        limits:
          memory: 4g

volumes:
  caddy_data:
  caddy_config:
```

Docker Compose lee `docker-compose.override.yml` solo, así que todos los
comandos de las guías (`docker compose up -d`, etc.) son los mismos.
`caddy_data` guarda los certificados: no lo borres, o Caddy tendrá que pedir
otros (Let's Encrypt limita cuántos se piden por semana).

**Sin dominio**, no crees ninguno de los dos ficheros (o deja en el override
solo el bloque de `crawler`).

## 6. Arrancar

```bash
docker compose up -d --build
```

La primera vez tarda (compila dependencias y descarga Chromium): de 10 a 30
minutos según el servidor. La API crea la base de datos al arrancar; un error
de `relation "clients" does not exist` en el log del worker durante los
primeros segundos es normal.

```bash
docker compose ps                         # todo Up; postgres y redis (healthy)
curl http://127.0.0.1:8000/health         # {"status":"ok"}
docker compose logs caddy | grep -i certificate   # con dominio: "certificate obtained successfully"
```

## 7. Entrar

**Con dominio:** <https://crawler.tuempresa.com>, con el `API_TOKEN`.

**Sin dominio:** desde tu ordenador, abre un túnel y déjalo abierto mientras
lo uses:

```bash
ssh -N -L 8000:127.0.0.1:8000 crawler@IP_DEL_SERVIDOR
```

y entra en <http://127.0.0.1:8000>.

**Sigue en [`uso.md`](uso.md#primer-rastreo)** para el primer rastreo. Los
rastreos se lanzan desde la interfaz; para `lanzar_job.py` desde tu
ordenador, apúntalo al servidor con `--api https://crawler.tuempresa.com`.

## 8. Copias de seguridad

Lo que hay que guardar: la **base de datos** y dos volúmenes con ficheros
(`archivo`, con los enlaces archivados de los censos viejos, y `secretos`, con
las llaves de Search Console y Slack subidas desde la interfaz).

Crea `~/backup-crawler.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd ~/crawler-masivo
DESTINO=~/backups
mkdir -p "$DESTINO" && chmod 700 "$DESTINO"
FECHA=$(date +%F)

docker compose exec -T postgres pg_dump -U crawler -Fc crawler_db > "$DESTINO/crawler_db_$FECHA.dump"
docker run --rm \
  -v crawler-masivo_archivo:/archivo:ro -v crawler-masivo_secretos:/secretos:ro \
  -v "$DESTINO":/out alpine tar czf "/out/ficheros_$FECHA.tgz" -C / archivo secretos

# Se guardan 7 días
find "$DESTINO" \( -name 'crawler_db_*.dump' -o -name 'ficheros_*.tgz' \) -mtime +7 -delete
```

```bash
chmod +x ~/backup-crawler.sh
~/backup-crawler.sh && ls -lh ~/backups      # pruébalo una vez a mano
crontab -e
```

y añade (cada noche a las 3:30):

```
30 3 * * * /home/crawler/backup-crawler.sh >> /home/crawler/backup.log 2>&1
```

- **Una copia en el mismo servidor no es una copia.** Llévate `~/backups` a
  otro sitio cada cierto tiempo (`rsync` o `scp` a otra máquina, o un
  almacenamiento S3 con `rclone`).
- El `.tgz` lleva las llaves de Search Console y Slack: trátalo como un secreto.
- `pg_dump` puede hacerse con un rastreo en marcha: la copia queda coherente.
  Pero carga el servidor; por eso de noche.

**Restaurar** (borra lo que haya en la base de datos y pone la copia):

```bash
cd ~/crawler-masivo
docker compose stop api crawler
docker compose exec -T postgres dropdb -U crawler crawler_db
docker compose exec -T postgres createdb -U crawler crawler_db
docker compose exec -T postgres pg_restore -U crawler -d crawler_db --no-owner < ~/backups/crawler_db_FECHA.dump
docker run --rm -v crawler-masivo_archivo:/archivo -v crawler-masivo_secretos:/secretos \
  -v ~/backups:/in alpine tar xzf /in/ficheros_FECHA.tgz -C /
docker compose start api crawler
```

## 9. Mantenimiento

- **Actualizar:** como en cualquier plataforma,
  [`uso.md`](uso.md#actualizar-a-la-última-versión). Corta el rastreo en
  marcha: avisa al equipo antes.
- **Disco:** `df -h /` de vez en cuando. Cada actualización deja imágenes
  viejas; `docker image prune -f` las borra sin tocar los datos. Lo que más
  ocupa son los censos: los viejos se borran desde la interfaz.
- **Actualizaciones de Ubuntu:** `sudo apt update && sudo apt upgrade -y` cada
  mes. Si pide reiniciar, los contenedores vuelven solos (y el rastreo en
  marcha se corta).

## Problemas propios del servidor

**Caddy no consigue el certificado** (`docker compose logs caddy`). El DNS aún
no apunta a la IP del servidor (`dig +short crawler.tuempresa.com`), o el
puerto 80 está cerrado (en `ufw` o en el firewall del panel del proveedor,
que es otro distinto). Arréglalo y `docker compose restart caddy`.

**`502 Bad Gateway` en el navegador.** Caddy funciona pero la API no:
`docker compose ps` y `docker compose logs api`.

**El worker se muere (`Killed`, código 137) o el servidor se queda colgado.**
Falta memoria. Comprueba la swap (`free -h`), baja `JS_CONCURRENT_REQUESTS` y
`PLAYWRIGHT_MAX_PAGES` en el `.env` (a 2 en un servidor de 4 GB) y
`docker compose up -d`.

**Los sitios devuelven 403 o captcha desde el servidor y no desde la
oficina.** Muchos WAF desconfían de las IP de centros de datos. Baja el ritmo
(ver [`uso.md`](uso.md#problemas-frecuentes)); si no basta, ese cliente se
rastrea mejor desde otra IP.
