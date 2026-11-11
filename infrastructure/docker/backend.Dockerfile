# Build context: repository root  (docker build -f infrastructure/docker/backend.Dockerfile .)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend

ARG EXTRA_PIP="psycopg[binary]>=3.1 opentelemetry-exporter-otlp-proto-http>=1.25"
COPY backend/requirements.txt .
RUN pip install -r requirements.txt ${EXTRA_PIP}

COPY backend/app ./app
COPY knowledge-base /srv/knowledge-base

# run as non-root
RUN useradd --create-home --uid 10001 appuser && mkdir -p /srv/backend/data && chown -R appuser /srv/backend/data
USER appuser

ENV KNOWLEDGE_BASE_PATH=/srv/knowledge-base/sample-documents \
    MOCK_DIRECTORY_PATH=/srv/backend/app/data/mock_directory.json \
    APPLICATION_CATALOG_PATH=/srv/backend/app/data/application_catalog.json \
    DATABASE_URL=sqlite:////srv/backend/data/itsupport.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health/live')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
