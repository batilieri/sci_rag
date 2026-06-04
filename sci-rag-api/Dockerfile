# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        libmagic1 \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-por \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

COPY pyproject.toml README.md ./
RUN pip install --upgrade pip wheel \
    && pip install --prefix=/install \
        "fastapi[standard]==0.115.0" "uvicorn[standard]==0.32.0" "gunicorn==23.0.0" \
        "pydantic==2.9.2" "pydantic-settings==2.6.0" \
        "qdrant-client[fastembed]==1.12.0" "redis[hiredis]==5.2.0" \
        "boto3==1.35.36" "aioboto3==13.2.0" \
        "anthropic==0.39.0" "openai==1.55.0" \
        "celery[redis]==5.4.0" \
        "structlog==24.4.0" "prometheus-client==0.21.0" \
        "passlib[bcrypt]==1.7.4" "slowapi==0.1.9" \
        "httpx==0.27.0" "tenacity==9.0.0" "python-multipart==0.0.12" \
        "sqlalchemy[asyncio]==2.0.36" "asyncpg==0.30.0" "alembic==1.14.0" "psycopg2-binary==2.9.10" \
        "unidecode==1.3.8" "orjson==3.10.10"

# Heavier ML deps installed separately so layer can be cached / skipped for "api-light" builds.
FROM base AS ml
RUN pip install --prefix=/install \
        "torch==2.3.1" \
        "FlagEmbedding==1.3.4" \
        "sentence-transformers==3.3.0" \
        "docling==2.8.0" \
        "pymupdf==1.24.13" \
        "pillow==10.4.0" \
        "pytesseract==0.3.13"

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libmagic1 \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-por \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ml /install /usr/local

RUN pip install --no-cache-dir "packaging==24.1" "transformers==4.44.2"

WORKDIR /srv/app
COPY . .

RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /srv/app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/v1/health || exit 1

CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "4", "-b", "0.0.0.0:8000", \
     "--timeout", "120", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-"]
