# Fase 3 (D1): build de la SPA nueva (frontend-v2) en una stage de node
FROM node:22-alpine AS frontend-build
WORKDIR /fe
COPY frontend-v2/package.json frontend-v2/package-lock.json* ./
RUN npm install --no-fund --no-audit
COPY frontend-v2/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY shared/ /app/shared/
COPY api/ /app/api/
# T17.2: reanalysis endpoint runs the analyzer in-process
COPY analysis/ /app/analysis/
COPY POC_centro_semantico/ /app/POC_centro_semantico/
COPY frontend/ /app/frontend/
COPY --from=frontend-build /fe/dist/ /app/frontend-v2/dist/
COPY scripts/ /app/scripts/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
