# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

WORKDIR /app

# Toolchain de compilacion. Capa estable a proposito: cambiar esta linea
# invalida TODO lo de abajo, incluido el pip install. g++/libc6-dev hacen
# falta porque hdbscan (y umap-learn por arrastre) no publican ruedas para
# aarch64 y pip los compila desde fuente.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc g++ libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/api/requirements.txt
# Cache de BuildKit para pip. Reutiliza tanto las ruedas descargadas como las
# que pip compila desde fuente (~/.cache/pip/wheels), asi que hdbscan solo se
# compila la primera vez aunque esta capa se invalide despues. Antes iba con
# --no-cache-dir, que forzaba recompilarlo entero en cada build.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r api/requirements.txt

COPY shared/ /app/shared/
COPY api/ /app/api/
COPY POC_centro_semantico/ /app/POC_centro_semantico/
COPY frontend/ /app/frontend/
COPY scripts/ /app/scripts/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
