# API HTTP

Base URL local: `http://127.0.0.1:8000`.

Header obrigatorio para endpoints protegidos:

```http
X-API-Key: rag_live_...
```

Escopos aceitos: `query`, `feedback`, `admin:read`, `admin:write`, `admin:*`.

## POST /v1/query

Objetivo: receber mensagem do SCI, executar RAG e retornar uma acao operacional.

Escopo: `query`.

Request:

```json
{
  "mensagem": "opcao K300/K315 esta cinza, o que faco?",
  "cliente": {
    "id_externo": "12345",
    "nome": "Maria Silva",
    "empresa": "Contabilidade ABC",
    "licenca_sci": "Contabil Completo",
    "tempo_relacionamento_meses": 18,
    "metadata_extra": {}
  },
  "conversa": {
    "id_externo": "ticket_98765",
    "canal": "whatsapp",
    "departamento_atual": "suporte_contabil",
    "historico": []
  },
  "opcoes": {
    "modelo_preferido": "auto",
    "incluir_debug": false,
    "max_imagens": 3,
    "bypass_cache": false,
    "threshold_confianca_minima": null,
    "filtros_categoria": ["ECD", "ECF"]
  }
}
```

Response `RESPONDER`:

```json
{
  "request_id": "req_abc",
  "acao": "RESPONDER",
  "confianca": 0.94,
  "departamento_sugerido": null,
  "motivo_transbordo": null,
  "intencao_detectada": "resolver opcao K300/K315 esmaecida",
  "necessita_followup": false,
  "mensagens": [
    {"ordem": 0, "tipo": "texto", "conteudo": "A opcao fica disponivel quando um Grupo economico e selecionado."},
    {"ordem": 1, "tipo": "imagem", "url": "https://r2.example/sci/faq/7085/images/img.png", "legenda": "Tela do Balanco", "mime_type": "image/png"}
  ],
  "faqs_consultados": [
    {"faq_id": "7085", "titulo": "Balanco com K300/K315", "score": 0.92, "url_original": "https://areadocliente.sci10.com.br/modulo/faq/faq.php?faqId=7085&sistemaId=54", "chunks_usados": ["faq_7085_chunk_001"]}
  ],
  "metricas": {
    "tempo_total_ms": 1840,
    "tempo_busca_ms": 120,
    "tempo_rerank_ms": 80,
    "tempo_llm_ms": 1620,
    "tokens_entrada": 2840,
    "tokens_saida": 312,
    "custo_estimado_usd": 0.0091,
    "modelo_usado": "deepseek-chat",
    "cache_hit": false
  },
  "debug": null
}
```

Response `TRANSFERIR_HUMANO`:

```json
{
  "request_id": "req_abc",
  "acao": "TRANSFERIR_HUMANO",
  "confianca": 0.0,
  "departamento_sugerido": "suporte_contabil",
  "motivo_transbordo": "low_retrieval_score",
  "necessita_followup": false,
  "mensagens": [{"ordem": 0, "tipo": "texto", "conteudo": "Vou te transferir para um atendente humano que vai conseguir te ajudar melhor nesse caso. So um momento."}],
  "faqs_consultados": [],
  "metricas": {"tempo_total_ms": 30, "modelo_usado": "-", "cache_hit": false}
}
```

Response `PEDIR_CLARIFICACAO`: mesmo schema de `QueryResponse`, com `acao="PEDIR_CLARIFICACAO"` e uma pergunta objetiva em `mensagens`.

cURL:

```bash
curl -X POST "$RAG_API_URL/v1/query" \
  -H "X-API-Key: $RAG_API_KEY" \
  -H "Content-Type: application/json" \
  -d @payload-query.json
```

Status: `200`, `401`, `403`, `422`, `429`, `500`.

Regras: debug so aparece com `opcoes.incluir_debug=true`; transbordo nao e cacheado; respostas devem citar FAQs quando `acao=RESPONDER`.

## POST /v1/query/stream

Objetivo: expor SSE para painel web.

Escopo: `query`.

Request: mesmo schema de `/v1/query`.

Response: `text/event-stream` com eventos `started`, `final` ou `error`.

## POST /v1/feedback

Objetivo: registrar feedback de resposta.

Escopo: `feedback`.

Request:

```json
{
  "request_id": "req_abc",
  "tipo": "negativo",
  "fonte": "atendente",
  "comentario": "Resposta incompleta",
  "correcao_sugerida": "Mencionar o Grupo economico"
}
```

Response `201`:

```json
{"registrado": true, "mensagem": "Feedback registrado com sucesso", "request_id": "req_abc"}
```

Status: `201`, `401`, `403`, `404`, `422`, `429`.

## GET /v1/health

Objetivo: verificar API, Postgres, Redis, Qdrant, object storage e credenciais LLM.

Response:

```json
{
  "status": "ok",
  "versao": "0.1.0",
  "timestamp": "2026-05-23T20:00:00Z",
  "componentes": [{"nome": "qdrant", "status": "ok", "latency_ms": 10}]
}
```

Status: `200`.

## GET /v1/stats

Objetivo: estatisticas agregadas das ultimas 24h.

Escopo: `admin:read`.

Response: `StatsResponse` com contadores de queries, transbordo, cache, latencia, chunks, imagens e custo.

## POST /v1/admin/ingest

Objetivo: upload multipart de PDF e criacao de job Celery.

Escopo: `admin:write`.

Request:

```bash
curl -X POST "$RAG_API_URL/v1/admin/ingest" \
  -H "X-API-Key: $RAG_ADMIN_KEY" \
  -F "file=@FAQ_SCI.pdf"
```

Response `202`:

```json
{"job_id": "ingest_abc", "status": "queued", "documento": "FAQ_SCI.pdf", "tamanho_bytes": 1234, "enqueued_at": "2026-05-23T20:00:00Z"}
```

## GET /v1/admin/ingest/{job_id}

Objetivo: consultar andamento de ingestao.

Escopo: `admin:write`.

Response: `IngestJobResponse` com status, fase, progresso, resumo e erros.

## Admin chunks

`GET /v1/admin/chunks`: lista chunks paginados por `page`, `page_size`, `faq_id`, `categoria`, `tipo_chunk`, `revisado_humano`.

`GET /v1/admin/chunks/{chunk_id}`: retorna payload completo do Qdrant.

`PATCH /v1/admin/chunks/{chunk_id}`: edita campos permitidos. Alterar texto dispara reembedding.

`DELETE /v1/admin/chunks/{chunk_id}`: remove chunk do Qdrant.

`POST /v1/admin/chunks/{chunk_id}/approve`: marca `revisado_humano=true`.

Escopos: leitura usa `admin:read`; escrita usa `admin:write`.

## POST /v1/admin/reindex

Objetivo: agendar reindexacao controlada.

Escopo: `admin:write`.

Request:

```json
{"scope": "all", "faq_ids": null, "categorias": null, "dry_run": false}
```

Response `202`:

```json
{"job_id": "reindex_abc", "scope": "all", "alvos_estimados": 0, "enqueued_at": "2026-05-23T20:00:00Z"}
```

## Erros

Formato comum:

```json
{"erro": "validacao", "mensagem": "Payload invalido", "campos": [{"campo": "mensagem", "erro": "Field required"}]}
```

Fallbacks: erros transientes do RAG devem levar o SCI a transferir para humano; erros permanentes de payload devem ser corrigidos pelo integrador.
