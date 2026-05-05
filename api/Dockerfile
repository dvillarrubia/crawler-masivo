FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cu126 \
    && pip install --no-cache-dir -r api/requirements.txt

COPY shared/ /app/shared/
COPY api/ /app/api/
COPY POC_centro_semantico/ /app/POC_centro_semantico/
COPY frontend/ /app/frontend/
COPY scripts/ /app/scripts/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
