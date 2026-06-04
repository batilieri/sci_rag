# Estrutura do Projeto da API (FastAPI)

## Layout de pastas

```
sci-rag-api/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml             # Poetry ou uv
├── .env.example
├── .gitignore
├── README.md
│
├── app/
│   ├── __init__.py
│   ├── main.py                # entry-point FastAPI
│   ├── config.py              # Pydantic Settings (env vars)
│   ├── dependencies.py        # auth, rate limit, DB, qdrant client
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py      # agrega todos os routers v1
│   │   │   ├── query.py       # POST /v1/query, /v1/query/stream
│   │   │   ├── feedback.py    # POST /v1/feedback
│   │   │   ├── health.py      # GET /v1/health
│   │   │   ├── stats.py       # GET /v1/stats
│   │   │   └── admin/
│   │   │       ├── __init__.py
│   │   │       ├── ingest.py      # POST /v1/admin/ingest
│   │   │       ├── chunks.py      # CRUD de chunks
│   │   │       └── reindex.py
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── security.py        # API key validation, HMAC
│   │   ├── rate_limit.py      # slowapi config
│   │   ├── logging.py         # structured logging (loguru/structlog)
│   │   ├── cache.py           # Redis cache layer
│   │   └── webhooks.py        # outbound webhooks com HMAC
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── query.py           # Pydantic: QueryRequest, QueryResponse, ...
│   │   ├── feedback.py
│   │   ├── ingest.py
│   │   ├── chunk.py
│   │   └── common.py          # shared models (Cliente, Conversa, etc.)
│   │
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── engine.py          # orquestrador: receive query → response
│   │   ├── retrieval.py       # busca híbrida Qdrant
│   │   ├── reranker.py        # ColBERT rerank
│   │   ├── generation.py      # chamada ao LLM principal
│   │   ├── query_rewriter.py  # gera variantes da query
│   │   ├── guardrails.py      # 5 guardrails
│   │   ├── embeddings.py      # BGE-M3 client (singleton)
│   │   ├── llm_clients.py     # Anthropic + DeepSeek
│   │   └── prompts/           # templates
│   │       ├── agente_producao.txt
│   │       └── query_rewriter.txt
│   │
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── pipeline.py        # orquestrador da ingestão
│   │   ├── pdf_extractor.py   # Docling wrapper
│   │   ├── chunker.py         # chunking hierárquico
│   │   ├── vision_describer.py  # Claude Vision
│   │   ├── vectorizer.py      # gera embeddings + upserta
│   │   ├── storage_client.py  # MinIO/Oracle Object Storage
│   │   └── prompts/
│   │       ├── extracao.txt
│   │       └── descricao_imagem.txt
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── qdrant_client.py   # wrapper async
│   │   ├── redis_client.py
│   │   ├── object_storage.py  # MinIO ou OCI
│   │   └── postgres.py        # opcional, para auditoria
│   │
│   ├── tasks/
│   │   ├── __init__.py
│   │   ├── celery_app.py
│   │   ├── ingestion_tasks.py # ingestão pesada async
│   │   └── webhook_tasks.py   # webhooks outbound assíncronos
│   │
│   └── models/                # SQLAlchemy models (se usar Postgres p/ auditoria)
│       ├── __init__.py
│       ├── query_log.py
│       ├── feedback.py
│       └── api_key.py
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_chunker.py
│   │   ├── test_guardrails.py
│   │   └── test_query_rewriter.py
│   ├── integration/
│   │   ├── test_query_endpoint.py
│   │   └── test_ingest_endpoint.py
│   └── e2e/
│       └── test_full_flow.py
│
├── scripts/
│   ├── seed_initial_pdf.py    # primeiro ingest do PDF SCI
│   ├── generate_api_key.py    # CLI para criar chaves
│   ├── reindex_all.py
│   └── benchmark.py           # roda 30 perguntas-teste e mede precisão
│
└── ops/
    ├── nginx.conf             # reverse proxy + TLS
    ├── grafana/
    │   └── dashboard.json
    └── prometheus.yml
```

## Tecnologias por camada

```
┌─────────────────────────────────────────────────────────┐
│ Cliente HTTP (SCI, painel, n8n, etc.)                │
└────────────────────┬────────────────────────────────────┘
                     │
              [nginx + TLS]
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ FastAPI + Uvicorn (workers)                              │
│  - Pydantic v2 validation                                │
│  - API Key auth + rate limit (slowapi)                   │
│  - Outbound webhooks (HMAC)                              │
└──┬────────────┬───────────────┬────────────────────┬───┘
   │            │               │                    │
   ▼            ▼               ▼                    ▼
┌──────┐  ┌──────────┐  ┌──────────────┐  ┌────────────────┐
│Redis │  │ Qdrant   │  │ Object Store │  │ Celery Worker  │
│cache │  │ vectors  │  │ (MinIO/OCI)  │  │ (ingestão)     │
└──────┘  └──────────┘  └──────────────┘  └────────────────┘
                                                  │
                                                  ▼
                                       ┌──────────────────┐
                                       │ LLM APIs         │
                                       │ - Anthropic      │
                                       │ - DeepSeek       │
                                       └──────────────────┘
```

## Arquivos críticos — primeiros a criar

Ordem recomendada de implementação:

1. `pyproject.toml` + `.env.example` (estrutura base)
2. `app/config.py` (Settings)
3. `app/schemas/query.py` (contratos Pydantic)
4. `app/storage/qdrant_client.py`
5. `app/rag/embeddings.py`
6. `app/rag/retrieval.py`
7. `app/rag/generation.py`
8. `app/rag/engine.py` (orquestrador)
9. `app/api/v1/query.py` (endpoint principal)
10. `app/main.py` (montar tudo)
11. `Dockerfile` + `docker-compose.yml`
12. Depois: ingestion pipeline, admin endpoints, observabilidade

## Dependências essenciais (`pyproject.toml`)

```toml
[project]
name = "sci-rag-api"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    # Web framework
    "fastapi[standard]==0.115.0",
    "uvicorn[standard]==0.32.0",
    "gunicorn==23.0.0",

    # Validation + settings
    "pydantic==2.9.2",
    "pydantic-settings==2.6.0",

    # Storage
    "qdrant-client==1.12.0",
    "redis[hiredis]==5.2.0",
    "minio==7.2.0",  # ou oci se for Oracle

    # Embeddings & ML
    "FlagEmbedding==1.3.4",
    "torch==2.3.1",
    "sentence-transformers==3.3.0",

    # PDF & Vision
    "docling==2.8.0",
    "pymupdf==1.24.13",
    "pillow==10.4.0",

    # LLMs
    "anthropic==0.39.0",
    "openai==1.55.0",

    # Async tasks
    "celery[redis]==5.4.0",

    # Observability
    "structlog==24.4.0",
    "logfire==2.0.0",  # opcional, ou prometheus_client
    "prometheus-client==0.21.0",

    # Security
    "python-jose[cryptography]==3.3.0",
    "passlib[bcrypt]==1.7.4",
    "slowapi==0.1.9",

    # Utils
    "httpx==0.27.0",
    "tenacity==9.0.0",  # retry com backoff
    "python-multipart==0.0.12",  # upload de arquivo
]

[tool.uv]
dev-dependencies = [
    "pytest==8.3.0",
    "pytest-asyncio==0.24.0",
    "pytest-cov==5.0.0",
    "httpx==0.27.0",  # test client
    "ruff==0.7.0",
    "mypy==1.13.0",
]
```
