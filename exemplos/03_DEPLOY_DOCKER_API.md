# Docker Compose — RAG API Standalone

Stack independente do Nexiry. Roda em VM dedicada (recomendado) ou na mesma VM do Nexiry.

## `docker-compose.yml`

```yaml
version: "3.9"

services:
  # ═══════════════════════════════════════════════════
  # API FastAPI (escalável horizontalmente)
  # ═══════════════════════════════════════════════════
  api:
    build:
      context: .
      dockerfile: Dockerfile
    image: nexiry-rag-api:latest
    container_name: rag_api
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"  # bind localhost; nginx faz o TLS
    depends_on:
      qdrant:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    environment:
      # App
      ENV: production
      LOG_LEVEL: info
      API_KEYS_DB_URL: postgresql+asyncpg://rag:${POSTGRES_PASSWORD}@postgres:5432/rag

      # Qdrant
      QDRANT_URL: http://qdrant:6333
      QDRANT_API_KEY: ${QDRANT_API_KEY}
      QDRANT_COLLECTION: sci_faq_ecd_ecf

      # Redis
      REDIS_URL: redis://redis:6379/0

      # MinIO
      MINIO_ENDPOINT: minio:9000
      MINIO_ACCESS_KEY: ${MINIO_USER}
      MINIO_SECRET_KEY: ${MINIO_PASS}
      MINIO_BUCKET: rag-images
      MINIO_SECURE: "false"

      # LLMs
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}

      # Webhooks outbound
      WEBHOOK_SECRET: ${WEBHOOK_SECRET}
      WEBHOOK_NEXIRY_URL: ${WEBHOOK_NEXIRY_URL}

      # Tuning
      MIN_SCORE_TOP_CHUNK: "0.65"
      MIN_CONFIANCA_RESPOSTA: "0.70"
      MAX_CHUNKS_NO_CONTEXTO: "5"
    volumes:
      - bge_cache:/root/.cache/huggingface
      - ./app:/app/app:ro  # hot-reload em dev; remove em prod
    networks:
      - rag_net
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 6G  # BGE-M3 FP16 ~3GB + workers
        reservations:
          memory: 3G

  # ═══════════════════════════════════════════════════
  # Worker Celery (ingestão pesada, webhooks async)
  # ═══════════════════════════════════════════════════
  worker:
    build:
      context: .
      dockerfile: Dockerfile
    image: nexiry-rag-api:latest
    container_name: rag_worker
    restart: unless-stopped
    command: ["celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=info", "-Q", "ingestion,webhooks"]
    depends_on:
      - redis
      - qdrant
      - minio
    environment:
      # Mesmas vars do api
      QDRANT_URL: http://qdrant:6333
      QDRANT_API_KEY: ${QDRANT_API_KEY}
      QDRANT_COLLECTION: sci_faq_ecd_ecf
      REDIS_URL: redis://redis:6379/0
      MINIO_ENDPOINT: minio:9000
      MINIO_ACCESS_KEY: ${MINIO_USER}
      MINIO_SECRET_KEY: ${MINIO_PASS}
      MINIO_BUCKET: rag-images
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
      WEBHOOK_SECRET: ${WEBHOOK_SECRET}
    volumes:
      - bge_cache:/root/.cache/huggingface
      - ./uploads:/uploads
    networks:
      - rag_net
    deploy:
      resources:
        limits:
          memory: 6G

  # ═══════════════════════════════════════════════════
  # Qdrant
  # ═══════════════════════════════════════════════════
  qdrant:
    image: qdrant/qdrant:v1.12.0
    container_name: rag_qdrant
    restart: unless-stopped
    ports:
      - "127.0.0.1:6333:6333"
      - "127.0.0.1:6334:6334"
    volumes:
      - qdrant_data:/qdrant/storage
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
      QDRANT__STORAGE__OPTIMIZERS__INDEXING_THRESHOLD: 10000
      QDRANT__TELEMETRY_DISABLED: "true"
    networks:
      - rag_net
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:6333/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 4G

  # ═══════════════════════════════════════════════════
  # Redis (cache + Celery broker)
  # ═══════════════════════════════════════════════════
  redis:
    image: redis:7-alpine
    container_name: rag_redis
    restart: unless-stopped
    command: ["redis-server", "--maxmemory", "1gb", "--maxmemory-policy", "allkeys-lru", "--appendonly", "yes"]
    volumes:
      - redis_data:/data
    networks:
      - rag_net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 5s
      retries: 3

  # ═══════════════════════════════════════════════════
  # MinIO (object storage para imagens)
  # ═══════════════════════════════════════════════════
  minio:
    image: minio/minio:latest
    container_name: rag_minio
    restart: unless-stopped
    ports:
      - "127.0.0.1:9000:9000"
      - "127.0.0.1:9001:9001"
    volumes:
      - minio_data:/data
    environment:
      MINIO_ROOT_USER: ${MINIO_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_PASS}
    command: server /data --console-address ":9001"
    networks:
      - rag_net
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 5s
      retries: 3

  # ═══════════════════════════════════════════════════
  # Postgres (API keys, query logs, feedback)
  # ═══════════════════════════════════════════════════
  postgres:
    image: postgres:16-alpine
    container_name: rag_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: rag
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: rag
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - rag_net
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "rag"]
      interval: 30s
      timeout: 5s
      retries: 3

  # ═══════════════════════════════════════════════════
  # Nginx (reverse proxy + TLS + rate limit edge)
  # ═══════════════════════════════════════════════════
  nginx:
    image: nginx:1.27-alpine
    container_name: rag_nginx
    restart: unless-stopped
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./ops/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ops/certs:/etc/nginx/certs:ro
    depends_on:
      - api
    networks:
      - rag_net

  # ═══════════════════════════════════════════════════
  # Observabilidade (opcional)
  # ═══════════════════════════════════════════════════
  prometheus:
    image: prom/prometheus:latest
    container_name: rag_prometheus
    restart: unless-stopped
    volumes:
      - ./ops/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    networks:
      - rag_net
    profiles: ["monitoring"]

  grafana:
    image: grafana/grafana:latest
    container_name: rag_grafana
    restart: unless-stopped
    ports:
      - "127.0.0.1:3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
      - ./ops/grafana/dashboard.json:/etc/grafana/provisioning/dashboards/rag.json:ro
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
    networks:
      - rag_net
    profiles: ["monitoring"]


networks:
  rag_net:
    driver: bridge

volumes:
  qdrant_data:
  redis_data:
  minio_data:
  postgres_data:
  bge_cache:
  prometheus_data:
  grafana_data:
```

## `Dockerfile`

```dockerfile
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# Sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tesseract-ocr tesseract-ocr-por \
        libgl1 libglib2.0-0 \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependências pesadas (cache layer)
COPY pyproject.toml uv.lock* ./
RUN pip install uv && \
    uv pip install --system torch==2.3.1 FlagEmbedding==1.3.4 docling==2.8.0

# ── Demais dependências
RUN uv pip install --system -r pyproject.toml

# ── Código
COPY app /app/app
COPY scripts /app/scripts

# ── Pré-baixa BGE-M3 (evita download no boot)
RUN python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

## `.env.example`

```bash
# ── Postgres
POSTGRES_PASSWORD=mude-aqui-senha-forte

# ── Qdrant
QDRANT_API_KEY=gere-com-openssl-rand-base64-32

# ── MinIO
MINIO_USER=rag_admin
MINIO_PASS=mude-aqui-senha-forte

# ── LLMs
ANTHROPIC_API_KEY=sk-ant-...
DEEPSEEK_API_KEY=sk-...

# ── Webhooks
WEBHOOK_SECRET=gere-com-openssl-rand-hex-32
WEBHOOK_NEXIRY_URL=https://nexiry.seudominio.com/webhooks/rag-events

# ── Grafana (se habilitar profile monitoring)
GRAFANA_PASSWORD=mude-aqui

# ── Para o Nexiry consumir (gerar com python scripts/generate_api_key.py)
NEXIRY_API_KEY=rag_live_xxxxx...
```

## `ops/nginx.conf` (resumo essencial)

```nginx
worker_processes auto;
events { worker_connections 1024; }

http {
    upstream rag_api {
        server api:8000;
        keepalive 32;
    }

    # Rate limit edge — defesa em profundidade
    limit_req_zone $http_x_api_key zone=per_key:10m rate=100r/m;

    server {
        listen 443 ssl http2;
        server_name rag.seudominio.com;

        ssl_certificate /etc/nginx/certs/fullchain.pem;
        ssl_certificate_key /etc/nginx/certs/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;

        # Body upload size — para POST /v1/admin/ingest
        client_max_body_size 50M;

        location / {
            limit_req zone=per_key burst=20 nodelay;

            proxy_pass http://rag_api;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # Streaming/SSE
            proxy_buffering off;
            proxy_read_timeout 120s;
        }
    }

    # Redirect HTTP → HTTPS
    server {
        listen 80;
        server_name rag.seudominio.com;
        return 301 https://$host$request_uri;
    }
}
```

## Subindo

```bash
# 1. Clonar / criar projeto
mkdir nexiry-rag-api && cd nexiry-rag-api

# 2. Copiar todos os arquivos do pacote

# 3. Gerar secrets
openssl rand -base64 32   # QDRANT_API_KEY
openssl rand -hex 32      # WEBHOOK_SECRET

# 4. Configurar .env
cp .env.example .env
nano .env

# 5. Build + subir
docker compose build
docker compose up -d

# 6. Verificar
curl http://localhost:8000/v1/health
# {"status": "ok", "qdrant": "ok", "redis": "ok", "minio": "ok"}

# 7. Gerar primeira API key (para o Nexiry consumir)
docker compose exec api python scripts/generate_api_key.py \
    --nome "Nexiry Produção" \
    --escopos query feedback

# Output:
# CHAVE (mostrada UMA VEZ — salve com segurança):
#   rag_live_abc123def456...

# 8. Ingerir o PDF inicial
docker compose exec api python scripts/seed_initial_pdf.py \
    --pdf /uploads/FAQ_SCI_Contabil.pdf

# 9. Configurar o Nexiry para apontar para a API
# No .env do Nexiry:
# RAG_API_URL=https://rag.seudominio.com
# RAG_API_KEY=rag_live_abc123...
```
