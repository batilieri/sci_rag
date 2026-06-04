# SCI RAG

API de **RAG (Retrieval-Augmented Generation)** que responde dúvidas técnicas do sistema **SCI Contábil** (FAQs, ECD, ECF, Bloco K, etc.) com alta precisão — devolvendo texto **e** imagens de apoio exatamente como a documentação oficial responde, e transferindo para um atendente humano quando não tem certeza.

Foi desenhada para ser plugada num orquestrador de atendimento (ex.: bot de WhatsApp via Evolution API): o orquestrador manda a pergunta do cliente, a API devolve a resposta pronta para enviar.

---

## O que ela faz

- 🔍 **Busca híbrida** na base de FAQs (semântica + por palavra-chave exata, como "K300", "I012").
- 🖼️ **Anexa as imagens certas** (prints de tela) junto da resposta, quando fazem sentido.
- 🛡️ **Não inventa**: vários guardrails impedem alucinação e respostas fora de escopo.
- 🤝 **Transbordo seguro**: se a confiança for baixa, devolve `transferir_humano` com o motivo e o departamento sugerido.
- 📊 **Observável**: registra cada consulta (custo, latência, modelo, confiança) e expõe métricas Prometheus.

---

## Como funciona

A resposta de cada pergunta passa por um pipeline de 10 etapas (`sci-rag-api/app/rag/engine.py`):

```
Pergunta do cliente
   │
   ├─ 1. Guardrails de entrada  → remove dados sensíveis (PII), detecta fora de escopo
   ├─ 2. Cache                  → resposta repetida volta na hora
   ├─ 3. Reescrita da query     → gera variações da pergunta para melhorar a busca
   ├─ 4. Busca híbrida (Qdrant) → fusão de embedding denso + esparso (BGE-M3)
   ├─ 5. Guardrail de busca     → se nada relevante, transfere para humano
   ├─ 6. Reranking              → reordena os melhores trechos por relevância
   ├─ 7. Geração (LLM)          → monta resposta em JSON citando os FAQs usados
   ├─ 8. Guardrails de saída    → bloqueia FAQ inventado / confiança abaixo do piso
   ├─ 9. Resolve imagens        → busca URLs das imagens no storage (R2/MinIO)
   └─ 10. Persiste + cache + webhook
   │
   ▼
Resposta (texto + imagens)  ou  transferir_humano
```

### Stack

| Camada | Tecnologia |
|---|---|
| API | FastAPI + Uvicorn/Gunicorn |
| Banco vetorial | Qdrant |
| Embeddings / rerank | BGE-M3 (denso + esparso) via FlagEmbedding |
| LLM | Anthropic Claude e/ou DeepSeek |
| Banco relacional | PostgreSQL (logs, chunks, API keys, imagens) |
| Cache / filas | Redis + Celery |
| Imagens | S3-compatible — Cloudflare R2 (produção) ou MinIO (dev) |
| Ingestão de PDF | Docling + PyMuPDF + Vision LLM |
| Observabilidade | Prometheus + Grafana, logs estruturados (structlog) |

---

## Estrutura do repositório

```
sci_rag/
├── sci-rag-api/          ← A API (o projeto de verdade, pronto para rodar)
│   ├── app/              ← código FastAPI (api, rag, ingestion, storage, models...)
│   ├── scripts/          ← gerar API key, ingerir PDF, reindexar, benchmark
│   ├── tests/            ← testes unitários, integração e e2e
│   ├── docs/             ← documentação de API, webhooks e integração
│   ├── ops/              ← nginx, prometheus, grafana
│   ├── docker-compose.yml
│   └── README.md         ← guia operacional detalhado da API
├── arquitetura/          ← documentos de design da solução
├── exemplos/             ← exemplos de payloads, chunks e prompts (POC original)
└── scripts/              ← scripts da POC inicial (ingest_pdf, rag_runtime)
```

> O conteúdo executável vive em **`sci-rag-api/`**. As pastas `arquitetura/`, `exemplos/` e `scripts/` são o material de design e a prova de conceito que originaram a API.

---

## Como usar

> Pré-requisitos: Docker + Docker Compose. Todos os comandos abaixo rodam dentro de `sci-rag-api/`.

### 1. Subir o ambiente

```bash
cd sci-rag-api
cp .env.example .env          # ajuste as variáveis (veja abaixo)

docker compose up -d postgres redis qdrant
docker compose --profile local-storage up -d minio   # storage de imagens em dev
docker compose up -d api worker
```

Confira a saúde:

```bash
curl http://127.0.0.1:8000/v1/health
```

### 2. Gerar uma API key

Todos os endpoints de negócio exigem o header `X-API-Key`.

```bash
docker compose exec api python scripts/generate_api_key.py \
  --nome "SCI" --escopos query feedback admin:read admin:write
```

### 3. Ingerir a base (PDF de FAQs)

```bash
docker compose exec api python scripts/seed_initial_pdf.py \
  --pdf /srv/app/data/uploads/FAQ_SCI.pdf --async
```

### 4. Consultar

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

A resposta traz `acao` (`responder` ou `transferir_humano`), as `mensagens` (texto e imagens prontas para envio), os `faqs_consultados` e as `metricas` (custo, latência, confiança).

---

## Endpoints principais

| Método | Path | Escopo |
|---|---|---|
| POST | `/v1/query` | `query` |
| POST | `/v1/query/stream` | `query` |
| POST | `/v1/feedback` | `feedback` |
| GET | `/v1/health` | público |
| GET | `/v1/stats` | `admin:read` |
| POST | `/v1/admin/ingest` | `admin:write` |
| GET/PATCH/DELETE | `/v1/admin/chunks/...` | `admin:read` / `admin:write` |
| POST | `/v1/admin/reindex` | `admin:write` |

Documentação completa: [`sci-rag-api/docs/api.md`](sci-rag-api/docs/api.md) · [`webhooks.md`](sci-rag-api/docs/webhooks.md) · [`integration-sci.md`](sci-rag-api/docs/integration-sci.md) · [`postman_collection.json`](sci-rag-api/docs/postman_collection.json)

---

## Configuração

Copie `sci-rag-api/.env.example` para `.env` e preencha. **Nunca** comite segredos reais (o `.env` já está no `.gitignore`).

Obrigatórias em produção:
`POSTGRES_PASSWORD`, `QDRANT_API_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `ANTHROPIC_API_KEY` **ou** `DEEPSEEK_API_KEY`, `WEBHOOK_SECRET`.

---

## Desenvolvimento

```bash
cd sci-rag-api
pip install -e ".[dev]"   # dependências + ferramentas de dev
pytest                    # roda os testes
ruff check .              # lint
```

Benchmark de precisão contra um gabarito:

```bash
docker compose exec api python scripts/benchmark.py \
  --gabarito tests/gabarito.json --api-key "$RAG_API_KEY"
```

---

## Observabilidade

Com `PROMETHEUS_ENABLED=true`, a API expõe `/metrics`. O `docker-compose.yml` inclui Prometheus e Grafana no profile `monitoring`:

```bash
docker compose --profile monitoring up -d
```
