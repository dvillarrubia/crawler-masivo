FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY analysis/requirements.txt /app/analysis/requirements.txt
RUN pip install --no-cache-dir -r analysis/requirements.txt

COPY shared/ /app/shared/
COPY analysis/ /app/analysis/

# El despliegue pasa el SHA del commit; en local queda "dev".
ARG CRAWLER_VERSION=dev
ENV CRAWLER_VERSION=$CRAWLER_VERSION

CMD ["python", "-m", "analysis.analyzer"]
