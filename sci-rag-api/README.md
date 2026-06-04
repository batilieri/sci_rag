# SCI RAG API

Microsservico FastAPI standalone para responder duvidas tecnicas do SCI Contabil via RAG, com Qdrant, Redis, Celery, Postgres e armazenamento S3-compatible para imagens (Cloudflare R2 em producao, MinIO em desenvolvimento).

## Subir localmente

```bash
cp .env.example .env
docker compose up -d postgres redis qdrant
docker compose --profile local-storage up -d minio
docker compose up -d api worker
```

Health:

```bash
curl http://127.0.0.1:8000/v1/health
```

Gerar API key:

```bash
docker compose exec api python scripts/generate_api_key.py --nome "SCI" --escopos query feedback admin:read admin:write
```

Ingerir PDF:

```bash
docker compose exec api python scripts/seed_initial_pdf.py --pdf /srv/app/data/uploads/FAQ_SCI.pdf --async
```

Benchmark:

```bash
docker compose exec api python scripts/benchmark.py --gabarito tests/gabarito.json --api-key "$RAG_API_KEY"
```

## Endpoints

Todos os endpoints de negocio usam `X-API-Key`. O healthcheck fica sem autenticacao para orquestradores.

| Metodo | Path | Escopo |
|---|---|---|
| POST | `/v1/query` | `query` |
| POST | `/v1/query/stream` | `query` |
| POST | `/v1/feedback` | `feedback` |
| GET | `/v1/health` | publico |
| GET | `/v1/stats` | `admin:read` |
| POST | `/v1/admin/ingest` | `admin:write` |
| GET | `/v1/admin/ingest/{job_id}` | `admin:write` |
| GET | `/v1/admin/chunks` | `admin:read` |
| GET | `/v1/admin/chunks/{chunk_id}` | `admin:read` |
| PATCH | `/v1/admin/chunks/{chunk_id}` | `admin:write` |
| DELETE | `/v1/admin/chunks/{chunk_id}` | `admin:write` |
| POST | `/v1/admin/chunks/{chunk_id}/approve` | `admin:write` |
| POST | `/v1/admin/reindex` | `admin:write` |

Veja a documentacao completa em:

- [docs/api.md](docs/api.md)
- [docs/webhooks.md](docs/webhooks.md)
- [docs/integration-sci.md](docs/integration-sci.md)
- [docs/postman_collection.json](docs/postman_collection.json)

## Exemplo de consulta

```bash
curl -X POST http://127.0.0.1:8000/v1/query \
  -H "X-API-Key: $RAG_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "mensagem": "opcao K300/K315 esta cinza, o que faco?",
    "cliente": {"id_externo": "12345", "licenca_sci": "Contabil Completo"},
    "conversa": {"id_externo": "ticket_98765", "canal": "whatsapp", "historico": []},
    "opcoes": {"incluir_debug": false, "max_imagens": 3}
  }'
```

## Variaveis principais

Configure `.env` a partir de `.env.example`. Nao use segredos reais no repositorio.

Obrigatorias para producao: `POSTGRES_PASSWORD`, `QDRANT_API_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `ANTHROPIC_API_KEY` ou `DEEPSEEK_API_KEY`, `WEBHOOK_SECRET`.

## Observabilidade

`/metrics` expoe metricas Prometheus quando `PROMETHEUS_ENABLED=true`. O compose inclui Prometheus e Grafana no profile `monitoring`.
