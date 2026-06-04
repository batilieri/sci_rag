# RAG API Standalone — Arquitetura

## Mudança de paradigma

Antes pensei em integrar o RAG como app Django **dentro** do Nexiry. Você quer o oposto: o RAG é um **microsserviço independente** que expõe endpoints HTTP/webhook. O Nexiry vira apenas um **cliente** dessa API.

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ┌─────────────────┐         HTTP POST          ┌─────────────┐ │
│  │ Nexiry          │ ─────────────────────────► │             │ │
│  │ (Django)        │   /v1/query                │  RAG API    │ │
│  │                 │ ◄───────────────────────── │  (FastAPI)  │ │
│  │ bot_engine      │   JSON: mensagens + imgs   │             │ │
│  └─────────────────┘                            └──────┬──────┘ │
│                                                        │        │
│                                                        ▼        │
│                                              ┌─────────────────┐│
│                                              │  Qdrant          ││
│                                              │  + Object Store  ││
│                                              └─────────────────┘│
│                                                                  │
│  Outros consumidores futuros:                                    │
│  - Painel interno (React, consulta a base)                       │
│  - n8n workflows                                                 │
│  - Outros sistemas seus                                          │
│  - Webhooks externos                                             │
└──────────────────────────────────────────────────────────────────┘
```

## Stack da API

| Camada | Escolha | Por quê |
|---|---|---|
| Framework | **FastAPI 0.115+** | Async nativo (importante para chamadas LLM), Swagger automático, Pydantic v2 nativo, mais performático que Django REST |
| Servidor ASGI | **Uvicorn + Gunicorn workers** | Padrão de mercado, escala bem |
| Validação | **Pydantic v2** | Type safety nos payloads de entrada/saída |
| Autenticação | **API Key (header `X-API-Key`) + HMAC opcional para webhooks** | Simples, suficiente para uso interno + integrações |
| Rate limiting | **slowapi** (Redis-backed) | Proteção básica, evita abuso de API key vazada |
| Cache de respostas | **Redis** | Perguntas iguais nas últimas 24h não pagam LLM 2x |
| Filas async | **Celery + Redis** | Para ingestão pesada de novos PDFs em background |
| Observabilidade | **Logfire** ou **OpenTelemetry + Grafana** | Métricas, traces, custo por query |
| Deploy | **Docker + docker-compose** no Oracle Cloud | Stack que você já domina |

## Endpoints da API

### Endpoints públicos (consumidos pelo Nexiry e outros)

| Método | Path | O que faz |
|---|---|---|
| `POST` | `/v1/query` | Consulta principal. Recebe mensagem do cliente + contexto, retorna resposta estruturada com mensagens + imagens. |
| `POST` | `/v1/query/stream` | Versão streaming via SSE. Útil para painéis web; o Nexiry provavelmente não precisa. |
| `POST` | `/v1/feedback` | Cliente/atendente reporta se a resposta foi boa. Alimenta retreinamento. |
| `GET` | `/v1/health` | Healthcheck (Qdrant, Redis, LLM APIs) |
| `GET` | `/v1/stats` | Estatísticas agregadas (queries/dia, taxa de transbordo, etc.) |

### Endpoints admin (gestão da base)

| Método | Path | O que faz |
|---|---|---|
| `POST` | `/v1/admin/ingest` | Upload de PDF para ingestão (async, retorna `job_id`) |
| `GET` | `/v1/admin/ingest/{job_id}` | Status da ingestão |
| `GET` | `/v1/admin/chunks` | Lista chunks da base (paginado, filtros) |
| `GET` | `/v1/admin/chunks/{chunk_id}` | Detalhe de um chunk |
| `PATCH` | `/v1/admin/chunks/{chunk_id}` | Editar/corrigir um chunk manualmente |
| `DELETE` | `/v1/admin/chunks/{chunk_id}` | Remover chunk problemático |
| `POST` | `/v1/admin/chunks/{chunk_id}/approve` | Marca como `revisado_humano=true` (dá boost na busca) |
| `POST` | `/v1/admin/reindex` | Reindexar tudo (raro, mas necessário se trocar modelo de embedding) |

### Webhooks (chamadas que SAEM da API)

A API também pode **chamar webhooks** externos quando eventos importantes acontecem:

| Evento | Quando dispara | Payload típico |
|---|---|---|
| `query.transferred_human` | Toda vez que decide transbordo | `{query, motivo, confianca, faqs_consultados}` |
| `query.low_confidence` | Confiança < 0.5 (mesmo que tenha respondido) | idem |
| `ingest.completed` | Ingestão de PDF termina | `{job_id, faqs_indexados, erros}` |
| `feedback.negative` | Cliente marca thumbs-down | `{query, resposta, motivo_relatado}` |

O Nexiry pode escutar esses webhooks para tomar ações: notificar você no Slack/Telegram quando muitos transbordos acontecem no mesmo tópico, por exemplo.

---

## Modelos de Request/Response (contratos da API)

### `POST /v1/query`

**Request:**
```json
{
  "mensagem": "tô tentando emitir o balanço com eliminações K300 mas a opção fica cinza",
  "cliente": {
    "id_externo": "12345",
    "nome": "Maria Silva",
    "empresa": "Contabilidade ABC LTDA",
    "licenca_sci": "Contábil Completo",
    "tempo_relacionamento_meses": 18
  },
  "conversa": {
    "id_externo": "ticket_98765",
    "canal": "whatsapp",
    "departamento_atual": "suporte_contabil",
    "historico": [
      {"role": "user", "content": "boa tarde", "timestamp": "2026-05-23T10:30:00Z"},
      {"role": "assistant", "content": "Boa tarde! Como posso ajudar?", "timestamp": "2026-05-23T10:30:05Z"},
      {"role": "user", "content": "tô tentando emitir o balanço com eliminações K300 mas a opção fica cinza", "timestamp": "2026-05-23T10:30:30Z"}
    ]
  },
  "opcoes": {
    "modelo_preferido": "auto",
    "incluir_debug": false,
    "max_imagens": 3
  }
}
```

**Response (200 OK):**
```json
{
  "request_id": "req_01HKGZ9P3W4X5Y6Z7A8B9C0D",
  "acao": "RESPONDER",
  "confianca": 0.94,
  "departamento_sugerido": null,
  "intencao_detectada": "resolver opção K300/K315 esmaecida no balanço patrimonial",
  "necessita_followup": false,
  "mensagens": [
    {
      "ordem": 0,
      "tipo": "texto",
      "conteudo": "A opção 'Considerar as eliminações do K300/K315' só fica disponível quando você seleciona um Grupo econômico na mesma tela do relatório."
    },
    {
      "ordem": 1,
      "tipo": "texto",
      "conteudo": "Em Relatórios > Balanço patrimonial, preencha o campo Grupo econômico primeiro. Após selecioná-lo, o checkbox sai do estado esmaecido e fica habilitado para marcar."
    },
    {
      "ordem": 2,
      "tipo": "imagem",
      "url": "https://storage.nexiry.com/sci/faq/7085/img_01.png",
      "legenda": "Tela do Balanço patrimonial — opção em destaque",
      "mime_type": "image/png"
    },
    {
      "ordem": 3,
      "tipo": "texto",
      "conteudo": "O retângulo vermelho na imagem mostra exatamente onde está o checkbox."
    }
  ],
  "faqs_consultados": [
    {
      "faq_id": "7085",
      "titulo": "Como realizar a emissão do Balanço patrimonial considerando as eliminações do K300/K315?",
      "score": 0.92,
      "url_original": "https://areadocliente.sci10.com.br/modulo/faq/faq.php?faqId=7085&sistemaId=54"
    }
  ],
  "metricas": {
    "tempo_total_ms": 1840,
    "tempo_busca_ms": 120,
    "tempo_rerank_ms": 80,
    "tempo_llm_ms": 1620,
    "tokens_entrada": 2840,
    "tokens_saida": 312,
    "custo_estimado_usd": 0.0091,
    "modelo_usado": "deepseek-v4-pro"
  }
}
```

**Response — Transbordo (200 OK, mas com acao diferente):**
```json
{
  "request_id": "req_01HKGZ9P3W4X5Y6Z7A8B9C0E",
  "acao": "TRANSFERIR_HUMANO",
  "confianca": 0.45,
  "departamento_sugerido": "suporte_contabil",
  "motivo_transbordo": "low_retrieval_score",
  "intencao_detectada": "análise de dados específicos da empresa",
  "necessita_followup": false,
  "mensagens": [
    {
      "ordem": 0,
      "tipo": "texto",
      "conteudo": "Vou te transferir para um atendente humano que vai conseguir te ajudar melhor nesse caso. Só um momento."
    }
  ],
  "faqs_consultados": [],
  "metricas": {...}
}
```

**Erros possíveis:**
```json
// 401 — API key inválida
{"erro": "api_key_invalida", "mensagem": "Header X-API-Key ausente ou inválido"}

// 422 — Payload inválido
{"erro": "validacao", "campos": [{"campo": "mensagem", "erro": "obrigatório"}]}

// 429 — Rate limit
{"erro": "rate_limit", "retry_after_seconds": 12}

// 503 — Serviço degradado
{"erro": "servico_indisponivel", "componente": "qdrant", "mensagem": "Tente novamente em alguns segundos"}
```

---

### `POST /v1/feedback`

Permite o Nexiry registrar quando um atendente marcou a resposta como boa/ruim:

**Request:**
```json
{
  "request_id": "req_01HKGZ9P3W4X5Y6Z7A8B9C0D",
  "tipo": "positivo|negativo|correcao",
  "fonte": "atendente|cliente",
  "comentario": "Resposta correta mas faltou mencionar o caso de empresas estrangeiras",
  "correcao_sugerida": null
}
```

---

### `POST /v1/admin/ingest`

**Request (multipart/form-data):**
```
file: FAQ_SCI_Contabil.pdf
metadata: {"sistema": "SCI Contábil", "categoria": "ECD/ECF", "versao_doc": "2026.05"}
```

**Response (202 Accepted):**
```json
{
  "job_id": "ing_01HKGZ9P3W4X5Y6Z7A8B9C0F",
  "status": "queued",
  "estimativa_minutos": 12,
  "websocket_url": "wss://api.../v1/admin/ingest/ing_.../progress"
}
```

---

## Autenticação

### API Key (header `X-API-Key`)
Padrão para todas as chamadas. Cada consumidor (Nexiry, painel admin, n8n) tem sua própria chave com escopo:

```
nexiry_prod_pk_live_<32 chars>  → escopo: query, feedback
admin_painel_sk_live_<32 chars> → escopo: tudo
n8n_workflows_pk_live_<32 chars> → escopo: query
```

### HMAC para webhooks (recomendado mas opcional)
Quando a API chama o webhook do Nexiry, assina o payload:

```
POST https://nexiry.com/webhooks/rag-events
Headers:
  X-RAG-Signature: sha256=abc123...
  X-RAG-Timestamp: 1716480000
Body: {...}
```

O Nexiry valida HMAC antes de processar.

---

## Cache Inteligente

Pergunta idêntica nas últimas **24h** retorna cache (custa $0 e responde em ~30ms). Chave do cache:

```
hash(mensagem_normalizada + cliente.licenca + departamento_atual)
```

Normalização: lowercase, remove acentos, remove pontuação, ordena palavras-chave. "Como marco K300?" e "como marco k300" geram a mesma chave.

**TTL diferenciado:**
- Resposta com confiança > 0.9 → cache 24h
- Resposta com confiança 0.7-0.9 → cache 6h
- Transbordo → não cacheia (cada caso pode ser diferente)

---

## Observabilidade — o que monitorar

| Métrica | Por quê |
|---|---|
| Latência p50, p95, p99 (`/v1/query`) | UX no WhatsApp |
| Taxa de transbordo % | Indica gaps na base |
| Distribuição de confiança | Identificar zona cinzenta |
| Top intenções não respondidas | Priorizar FAQs a indexar |
| Custo LLM diário ($) | Controle de gasto |
| Erros por endpoint | Saúde do serviço |
| Hits/misses do cache | Eficiência de custo |

Painel sugerido (Grafana): 1 dashboard com 8 widgets cobrindo isso.

---

## Resumo das mudanças vs proposta anterior

| Antes | Agora |
|---|---|
| App Django dentro do Nexiry | Microsserviço FastAPI independente |
| Acoplado ao código do Nexiry | API com contrato versionado (`/v1/...`) |
| Só o Nexiry consome | Qualquer cliente (painel, n8n, outros) consome |
| Deploy junto do Nexiry | Deploy independente (pode ter VM própria) |
| Crash do RAG = crash do Nexiry | Crash do RAG = Nexiry só não tem IA temporariamente |
| Sem rate limit / sem auth multi-cliente | API keys com escopo, rate limit por chave |
