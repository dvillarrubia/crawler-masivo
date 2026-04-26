FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libxml2-dev libxslt1-dev zlib1g-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY crawler/requirements.txt /app/crawler/requirements.txt
COPY analysis/requirements.txt /app/analysis/requirements.txt
RUN pip install --no-cache-dir -r crawler/requirements.txt -r analysis/requirements.txt \
    && playwright install chromium --with-deps

COPY shared/ /app/shared/
COPY crawler/ /app/crawler/
COPY analysis/ /app/analysis/

CMD ["python", "crawler/worker.py"]
