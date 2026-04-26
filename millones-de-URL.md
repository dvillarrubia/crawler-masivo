# Crawls de millones de URLs con colas y Scrapy distribuido

Este documento describe cómo organizar crawls distribuidos para llegar sin problemas a cientos de miles o millones de URLs usando colas (Redis/Kafka) y múltiples workers Scrapy.[web:23][web:24][web:29][web:30]

## 1. Enfoque general

Objetivo: tener un sistema en el que:

- Lances jobs (crawls) desde una API o panel.
- Uses una cola central para repartir URLs entre workers.
- Puedas añadir/quitar workers sin perder el estado.
- Evites recrawlear la misma URL varias veces si no quieres.

Patrones típicos:

- **Scrapy + Redis (`scrapy-redis`)**: scheduler y dupefilter compartidos.[web:23]
- **Scrapy Cluster (Redis + Kafka)**: arquitectura más avanzada con frontier distribuido.[web:24]
- **Cola propia (ej. Rabbit, SQS) + scheduler custom** si quieres máximo control.

## 2. Patrón Scrapy + Redis (scrapy-redis)

### 2.1. Componentes

- Redis:
  - Cola de Requests.
  - Filtro de duplicados (set de fingerprints).
- Varios workers Scrapy:
  - Mismo código de spiders, conectados al mismo Redis.
- Semillas (start URLs):
  - Insertadas mediante CLI/API en listas de Redis.[web:23]

### 2.2. Configuración básica

En `settings.py` del proyecto:

```python
SCHEDULER = "scrapy_redis.scheduler.Scheduler"
DUPEFILTER_CLASS = "scrapy_redis.dupefilter.RFPDupeFilter"

SCHEDULER_PERSIST = True
SCHEDULER_FLUSH_ON_START = False

REDIS_URL = "redis://user:password@redis:6379"

CONCURRENT_REQUESTS = 64
CONCURRENT_REQUESTS_PER_DOMAIN = 16
AUTOTHROTTLE_ENABLED = True
```

Spider tipo `RedisSpider`:

```python
from scrapy_redis.spiders import RedisSpider

class SiteSpider(RedisSpider):
    name = "site_spider"
    redis_key = "site_spider:start_urls"

    def parse(self, response):
        # extracción SEO + descubrimiento interno
        # yield items + new requests (response.follow)
        ...
```

### 2.3. Lanzar workers y seeds

- Workers (en X servidores):

```bash
scrapy crawl site_spider
```

- Seeds:

```bash
redis-cli LPUSH site_spider:start_urls "https://ejemplo.com/"
```

Todos los workers comparten cola y filtro de duplicados, por lo que el sistema se comporta como un crawler distribuido.[web:23][web:30]

## 3. Patrón Scrapy Cluster (Redis + Kafka)

### 3.1. Visión general

Scrapy Cluster (istresearch) es un proyecto que monta:

- Redis:
  - Estado de URLs (frontera).
- Kafka:
  - Ingesta de URLs/peticiones y eventos.
- Spiders Scrapy:
  - Funcionan como “workers” que consumen de Redis/Kafka.[web:24]

Ventajas:

- Multi-tenant (varios proyectos y dominios).
- Control fino de reintentos, prioridades, profundidad.
- Observabilidad mejor (logs/eventos en Kafka).[web:24][web:27]

### 3.2. Flujo típico

1. Envías un mensaje a Kafka con un nuevo crawl o URL seed.
2. Los componentes de Scrapy Cluster lo pasan a Redis/frontier.
3. Los spiders consumen URLs, las crawlean y empujan resultados a tu sistema (Elastic, S3, BBDD…).[web:24]
4. Puedes escalar añadiendo más instancias de spiders.

Ideal cuando tus volúmenes se van a millones de URLs de muchos dominios y quieres modularidad.

## 4. Consejos para llegar a millones de URLs

### 4.1. Control de profundidad y dominios

- Definir políticas por job:
  - `max_depth`, `allowed_domains`, `exclude_patterns`.
- No rastrear enlaces infinitos (calendarios, facetas, parámetros de tracking).  
- Redistribuir trabajo por dominio para no bloquear con dominios muy grandes.

### 4.2. Afinar Scrapy para crawls amplios

- Subir `CONCURRENT_REQUESTS` hasta lo que aguante tu red/CPU sin bans (test incremental).[web:30]
- Usar `AUTOTHROTTLE_ENABLED = True` para autorregular el ritmo.
- Rotar IPs/proxies si vas a crawls muy agresivos multi‑dominio.
- Activar timeouts, reintentos máximos y circuit breakers para dominios problemáticos.[web:26]

### 4.3. Gestión de memoria y estado

- No guardes todo en memoria:
  - Escribe `items` a BBDD/cola otra vez, no acumules grandes listas en el spider.
- Usa `SCHEDULER_PERSIST = True` para poder pausar y reanudar crawls largos.
- Controles periódicos:
  - Jobs scheduler que revise tamaño de colas, número de workers vivos, backlog.

### 4.4. Partición por jobs/proyectos

- Una cola/señal por job:
  - Ej: `crawl:{job_id}:start_urls`, `crawl:{job_id}:requests`.
- Metadata en `Request.meta`:
  - `job_id`, `client_id`, `depth_limit`.
- Posibilidad de parar un job:
  - Flag en BBDD/Redis que el spider consulta y deja de encolar nuevas URLs para ese job.

## 5. Fuentes adicionales recomendadas

- Documentación “Broad Crawls” de Scrapy: optimizaciones específicas para crawls grandes.[web:30]
- Guías de pipelines escalables con Scrapy (persistencia en BBDD, colas intermedias, etc.).[web:29][web:44]