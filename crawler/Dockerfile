# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

# Toolchain de compilacion + libs de sistema. Capa estable a proposito:
# cambiar esta linea invalida TODO lo de abajo (pip install y la descarga de
# Chromium), que es exactamente lo que hizo que un build tardase 30 min.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc g++ libc6-dev libxml2-dev libxslt1-dev zlib1g-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY crawler/requirements.txt /app/crawler/requirements.txt
COPY analysis/requirements.txt /app/analysis/requirements.txt
# Cache de BuildKit para pip. Reutiliza las ruedas descargadas y tambien las
# que pip compila desde fuente (~/.cache/pip/wheels), asi que lo que no tiene
# rueda para aarch64 se compila una sola vez y no en cada build. Antes iba con
# --no-cache-dir, que impedia justo esto.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r crawler/requirements.txt -r analysis/requirements.txt

# Chromium en su propia capa, separada del pip install. No se puede cachear
# con --mount: los cache mounts no se persisten en la imagen final y el
# navegador no existiria en runtime. Aislarla evita que un cambio en el codigo
# o en una dependencia arrastre la descarga entera del navegador.
RUN playwright install chromium --with-deps

COPY shared/ /app/shared/
COPY crawler/ /app/crawler/
COPY analysis/ /app/analysis/
# El worker ejecuta scripts/check_js_templates.py al cerrar un rastreo sin JS.
COPY scripts/ /app/scripts/

CMD ["python", "crawler/worker.py"]
