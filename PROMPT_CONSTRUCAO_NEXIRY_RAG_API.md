# Prompt de construcao - Nexiry RAG API SCI

Voce e um engenheiro senior full-stack/backend especializado em FastAPI, sistemas RAG de alta precisao, Qdrant, processamento de PDF, embeddings multilingues, filas assíncronas e integracoes via API. Construa uma API RAG standalone para o Nexiry responder duvidas tecnicas do sistema SCI Contabil via WhatsApp com precisao, imagens de apoio e transbordo seguro para atendimento humano.

## Objetivo

Criar um microsservico independente chamado `nexiry-rag-api`, com contrato HTTP versionado em `/v1`, capaz de:

- Ingerir PDFs de FAQs do SCI Contabil.
- Extrair texto, screenshots e metadados das FAQs.
- Gerar chunks semanticos hierarquicos e chunks de imagem.
- Vetorizar tudo no Qdrant usando BGE-M3 com vetores dense, sparse e ColBERT.
- Receber perguntas do Nexiry via HTTP.
- Fazer query rewriting, busca hibrida, reranking, geracao com LLM e guardrails.
- Retornar uma resposta estruturada com textos, imagens, confianca, FAQs consultados, metricas e acao operacional.
- Transferir para humano quando nao houver base suficiente, houver risco de alucinacao, baixa confianca ou pedido fora do escopo.

O sistema deve ser construido para producao, com Docker, autenticacao por API key, rate limit, cache Redis, webhooks HMAC, logs, metricas, healthchecks, testes e benchmark de precisao.

## Direcao arquitetural obrigatoria

Use FastAPI como API standalone. O Nexiry Django deve ser apenas consumidor da API, chamando `POST /v1/query` e recebendo o JSON de resposta. Nao implemente o RAG como app Django acoplado ao Nexiry.

Stack principal:

- Python 3.11+
- FastAPI 0.115+
- Pydantic v2 e pydantic-settings
- Uvicorn/Gunicorn
- Qdrant 1.12+
- Redis para cache e broker Celery
- Celery para ingestao pesada e webhooks
- Cloudflare R2 para armazenar imagens extraidas dos PDFs
- MinIO apenas como alternativa local/dev compativel com S3
- Postgres para API keys, logs, feedback, jobs e referencias das imagens no R2
- BGE-M3 via FlagEmbedding para dense, sparse e ColBERT
- Docling + PyMuPDF para extracao de PDF
- OCR fallback com Tesseract/PaddleOCR quando necessario
- Anthropic Claude Sonnet para extracao/vision/fallback de alta qualidade
- DeepSeek para query rewriting e geracao de baixo custo quando aplicavel
- Docker Compose com API, worker, Qdrant, Redis, MinIO, Postgres, Nginx e opcional Prometheus/Grafana

## Estrutura esperada do projeto

Crie o layout:

```text
nexiry-rag-api/
  app/
    main.py
    config.py
    dependencies.py
    api/v1/
      router.py
      query.py
      feedback.py
      health.py
      stats.py
      admin/ingest.py
      admin/chunks.py
      admin/reindex.py
    core/
      security.py
      rate_limit.py
      logging.py
      cache.py
      webhooks.py
    schemas/
      query.py
      feedback.py
      ingest.py
      chunk.py
      common.py
    rag/
      engine.py
      retrieval.py
      reranker.py
      generation.py
      query_rewriter.py
      guardrails.py
      embeddings.py
      llm_clients.py
      prompts/
    ingestion/
      pipeline.py
      pdf_extractor.py
      chunker.py
      vision_describer.py
      vectorizer.py
      storage_client.py
      prompts/
    storage/
      qdrant_client.py
      redis_client.py
      object_storage.py
      r2_client.py
      postgres.py
    tasks/
      celery_app.py
      ingestion_tasks.py
      webhook_tasks.py
    models/
      query_log.py
      feedback.py
      api_key.py
      image_asset.py
  scripts/
    seed_initial_pdf.py
    generate_api_key.py
    reindex_all.py
    benchmark.py
  tests/
    unit/
    integration/
    e2e/
  ops/
    nginx.conf
    prometheus.yml
    grafana/dashboard.json
  Dockerfile
  docker-compose.yml
  pyproject.toml
  .env.example
  README.md
```

## Contratos HTTP obrigatorios

Implemente estes endpoints:

- `POST /v1/query`: endpoint principal. Recebe mensagem, cliente, conversa e opcoes. Retorna resposta estruturada.
- `POST /v1/query/stream`: SSE opcional para painel web.
- `POST /v1/feedback`: registra feedback positivo, negativo ou correcao.
- `GET /v1/health`: valida API, Qdrant, Redis, Cloudflare R2/Object Storage e LLMs.
- `GET /v1/stats`: estatisticas agregadas.
- `POST /v1/admin/ingest`: upload multipart de PDF para ingestao assíncrona.
- `GET /v1/admin/ingest/{job_id}`: status da ingestao.
- `GET /v1/admin/chunks`: lista chunks paginados com filtros.
- `GET /v1/admin/chunks/{chunk_id}`: detalhe.
- `PATCH /v1/admin/chunks/{chunk_id}`: edicao manual.
- `DELETE /v1/admin/chunks/{chunk_id}`: remocao.
- `POST /v1/admin/chunks/{chunk_id}/approve`: marca `revisado_humano=true`.
- `POST /v1/admin/reindex`: reindexacao controlada.

Use header `X-API-Key` em todos os endpoints, com escopos:

- `query`
- `feedback`
- `admin:read`
- `admin:write`
- `admin:*`

Armazene apenas hash SHA-256 das API keys. A chave raw deve ser exibida uma unica vez no script de geracao.

## Schema de request de query

`POST /v1/query` deve aceitar:

```json
{
  "mensagem": "to tentando emitir o balanco com eliminacoes K300 mas a opcao fica cinza",
  "cliente": {
    "id_externo": "12345",
    "nome": "Maria Silva",
    "empresa": "Contabilidade ABC LTDA",
    "licenca_sci": "Contabil Completo",
    "tempo_relacionamento_meses": 18,
    "metadata_extra": {}
  },
  "conversa": {
    "id_externo": "ticket_98765",
    "canal": "whatsapp",
    "departamento_atual": "suporte_contabil",
    "historico": [
      {"role": "user", "content": "boa tarde", "timestamp": "2026-05-23T10:30:00Z"}
    ]
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

## Schema de response de query

Retorne sempre um `request_id` e uma `acao`:

- `RESPONDER`
- `TRANSFERIR_HUMANO`
- `PEDIR_CLARIFICACAO`

Formato:

```json
{
  "request_id": "req_xxx",
  "acao": "RESPONDER",
  "confianca": 0.94,
  "departamento_sugerido": null,
  "motivo_transbordo": null,
  "intencao_detectada": "resolver opcao K300/K315 esmaecida no balanco patrimonial",
  "necessita_followup": false,
  "mensagens": [
    {
      "ordem": 0,
      "tipo": "texto",
      "conteudo": "A opcao 'Considerar as eliminacoes do K300/K315' so fica disponivel quando voce seleciona um Grupo economico na mesma tela do relatorio."
    },
    {
      "ordem": 1,
      "tipo": "imagem",
    "url": "https://r2-public.example.com/sci/faq/7085/img_01.png",
      "legenda": "Tela do Balanco patrimonial - opcao em destaque",
      "mime_type": "image/png"
    }
  ],
  "faqs_consultados": [
    {
      "faq_id": "7085",
      "titulo": "Como realizar a emissao do Balanco patrimonial considerando as eliminacoes do K300/K315?",
      "score": 0.92,
      "url_original": "https://areadocliente.sci10.com.br/modulo/faq/faq.php?faqId=7085&sistemaId=54",
      "chunks_usados": ["faq_7085_chunk_001"]
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
    "modelo_usado": "deepseek-v4-pro",
    "cache_hit": false
  },
  "debug": null
}
```

Para transbordo, responda HTTP 200 com:

```json
{
  "acao": "TRANSFERIR_HUMANO",
  "confianca": 0.0,
  "departamento_sugerido": "suporte_contabil",
  "motivo_transbordo": "low_retrieval_score",
  "mensagens": [
    {
      "ordem": 0,
      "tipo": "texto",
      "conteudo": "Vou te transferir para um atendente humano que vai conseguir te ajudar melhor nesse caso. So um momento."
    }
  ],
  "faqs_consultados": []
}
```

## Pipeline de ingestao obrigatorio

Implemente ingestao de PDF nesta ordem:

1. Extrair texto estruturado com Docling.
2. Extrair imagens com PyMuPDF preservando pagina, hash, tamanho e dimensoes.
3. Identificar blocos de FAQ pelo padrao `faq_id`, titulo e URL original.
4. Associar imagens ao FAQ correto por posicao/pagina/contexto. Evite heuristica grosseira quando houver dados de layout.
5. Descrever cada imagem com Vision LLM antes de estruturar o FAQ textual.
6. Estruturar cada FAQ com LLM em JSON validado por Pydantic.
7. Gerar chunks:
   - parent: FAQ inteiro.
   - child: secoes semanticas de 200 a 400 tokens.
   - image: cada screenshot como chunk independente.
8. Gerar `texto_enriquecido_para_embedding` com termos tecnicos, sinonimos e contexto, sem contradizer o original.
9. Gerar embeddings BGE-M3:
   - dense 1024 dim.
   - sparse lexical weights.
   - ColBERT multivector 128 dim.
10. Salvar imagens no Cloudflare R2 em chave deterministica.
11. Registrar ou atualizar a referencia da imagem no Postgres.
12. Upsertar pontos no Qdrant com payload rico, incluindo `image_asset_id` e referencias R2.
13. Registrar job, erros, metricas e resumo de ingestao.

## Alimentacao do banco com imagens e R2

Use Cloudflare R2 como armazenamento oficial das imagens extraidas dos PDFs. O banco relacional nao deve guardar binario de imagem. Ele deve guardar apenas metadados, chaves R2 e referencias necessarias para localizar e enviar a imagem ao cliente.

Fluxo obrigatorio para cada imagem:

1. Extrair a imagem do PDF com PyMuPDF/Docling.
2. Calcular `hash_sha256` e/ou `hash_md5` dos bytes.
3. Normalizar o formato para PNG quando necessario.
4. Gerar uma chave R2 deterministica:

```text
sci/faq/{faq_id}/images/{image_id}.png
```

Exemplo:

```text
sci/faq/7085/images/img_faq_7085_01.png
```

5. Fazer upload para o bucket R2 com `content_type=image/png`.
6. Salvar no Postgres um registro na tabela `rag_image_assets`.
7. Salvar no Qdrant, no payload do chunk de imagem, o `image_asset_id`, `r2_bucket`, `r2_key` e URL publica ou dados para gerar URL assinada.
8. Quando o runtime decidir enviar uma imagem, ele deve buscar a referencia por `image_asset_id`, gerar a URL de envio e retornar essa URL no item `mensagens[]` com `tipo="imagem"`.

Tabela obrigatoria `rag_image_assets`:

```sql
CREATE TABLE rag_image_assets (
    id UUID PRIMARY KEY,
    image_id VARCHAR(120) UNIQUE NOT NULL,
    faq_id VARCHAR(32) NOT NULL,
    source_document_id UUID NULL,
    chunk_id VARCHAR(160) NULL,
    original_filename VARCHAR(255) NULL,
    r2_bucket VARCHAR(120) NOT NULL,
    r2_key TEXT NOT NULL,
    r2_public_url TEXT NULL,
    r2_etag VARCHAR(160) NULL,
    content_type VARCHAR(80) NOT NULL DEFAULT 'image/png',
    tamanho_bytes BIGINT NOT NULL,
    width INTEGER NULL,
    height INTEGER NULL,
    hash_sha256 CHAR(64) NOT NULL,
    hash_md5 CHAR(32) NULL,
    ordem_no_faq INTEGER NULL,
    tipo_tela VARCHAR(80) NULL,
    titulo_janela TEXT NULL,
    descricao_curta TEXT NULL,
    menu_caminho_inferido TEXT NULL,
    registros_sped_visiveis JSONB NOT NULL DEFAULT '[]',
    palavras_chave_exatas JSONB NOT NULL DEFAULT '[]',
    quando_enviar JSONB NOT NULL DEFAULT '[]',
    revisado_humano BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(40) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (r2_bucket, r2_key)
);
```

Regras de armazenamento:

- Nao salve bytes da imagem no Postgres.
- Nao salve bytes da imagem no Qdrant.
- Qdrant deve guardar somente payload textual/metadados para busca e `image_asset_id` para resolver o envio.
- Se o bucket R2 tiver dominio publico, salve `r2_public_url`.
- Se o bucket R2 for privado, salve apenas `r2_bucket` e `r2_key`, e gere URL assinada no momento da resposta.
- A URL assinada deve ter TTL curto, por exemplo 15 a 60 minutos.
- O envio via Evolution API deve usar a URL publica/assinada gerada a partir da referencia do R2.
- Ao reprocessar o mesmo PDF, deduplique por `hash_sha256` e/ou `(faq_id, image_id)`.
- Ao editar uma imagem no admin, atualize Postgres, Qdrant e, se necessario, o objeto correspondente no R2.

Variaveis obrigatorias de ambiente para R2:

```bash
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=rag-images
R2_PUBLIC_BASE_URL=https://r2-public.example.com
R2_PRESIGNED_URL_TTL_SECONDS=3600
```

Use cliente S3-compatible (`boto3`, `aioboto3` ou equivalente) apontando para:

```text
https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com
```

## Schema do payload no Qdrant

Cada chunk de texto deve ter:

- `tipo_chunk`: `"texto"`
- `faq_id`
- `faq_titulo`
- `categoria_principal`
- `categorias_secundarias`
- `sistema`
- `modulo`
- `versao_sistema`
- `chunk_index`
- `chunk_total`
- `chunk_tipo`: `introducao`, `procedimento`, `calculo_regra`, `configuracao`, `exemplo`, `observacao_importante` ou `referencia_cruzada`
- `parent_chunk_id`
- `titulo_secao`
- `texto_original`
- `texto_enriquecido_para_embedding`
- `registros_sped_mencionados`
- `relatorios_mencionados`
- `menus_caminhos`
- `campos_interface`
- `palavras_chave_exatas`
- `imagens_associadas`
- `intencoes_atendidas`
- `perguntas_exemplo`
- `publico_alvo`
- `data_cadastro_faq`
- `data_atualizacao_faq`
- `data_indexacao`
- `fonte.documento`
- `fonte.url_original`
- `fonte.pagina_pdf`
- `confianca_extracao`
- `revisado_humano`

Cada chunk de imagem deve ter:

- `tipo_chunk`: `"imagem"`
- `faq_id`
- `filename`
- `image_asset_id`
- `storage_url`
- `storage_path_interno`
- `r2_bucket`
- `r2_key`
- `r2_public_url`
- `r2_etag`
- `hash_md5`
- `tamanho_bytes`
- `dimensoes`
- `tipo_tela`
- `titulo_janela`
- `menu_caminho_inferido`
- `descricao_vision_llm`
- `ocr_texto_completo`
- `elementos_ui_identificados`
- `elementos_destacados_visualmente`
- `registros_sped_visiveis`
- `palavras_chave_exatas`
- `quando_enviar`
- `contexto_faq`
- `modelo_vision_usado`
- `data_descricao`
- `confianca_ocr`
- `revisado_humano`

Crie indices de payload no Qdrant para:

- `faq_id`
- `categoria_principal`
- `registros_sped_mencionados`
- `chunk_tipo`
- `tipo_chunk`
- `revisado_humano`

## Collection Qdrant

Collection: `sci_faq_ecd_ecf`

Vetores:

- `dense`: size 1024, cosine.
- `sparse`: sparse vector.
- `colbert`: size 128, cosine, multivector com `MAX_SIM`; use para reranking.

## Pipeline de consulta obrigatorio

`RAGEngine.process()` deve executar:

1. Receber `QueryRequest`.
2. Fazer PII scrubber antes de enviar texto a LLM.
3. Reescrever a query em 2 a 3 variantes tecnicas usando historico recente:
   - expandir abreviacoes como BP para Balanco Patrimonial.
   - manter codigos literais como K300, K315, I012.
   - resolver referencias como "esse erro".
4. Gerar embeddings da query com BGE-M3.
5. Buscar no Qdrant com dense + sparse e fusion RRF.
6. Buscar ate 20 candidatos por variante e deduplicar por point id.
7. Aplicar filtros quando `filtros_categoria` vier no request.
8. Fazer reranking ColBERT para top 5.
9. Opcionalmente usar cross-encoder quando top score estiver em zona cinzenta.
10. Separar chunks de texto e imagem.
11. Escolher modelo:
   - DeepSeek para pergunta simples e score alto.
   - Claude Sonnet para pergunta longa, multiplos topicos, historico longo ou score menor que 0.80.
12. Montar prompt do agente com historico, perfil do cliente, chunks e imagens.
13. Gerar JSON do LLM em temperatura 0.0.
14. Validar a resposta com Pydantic.
15. Aplicar guardrails.
16. Intercalar mensagens de texto e imagem pela ordem indicada.
17. Para cada imagem selecionada, resolver `image_asset_id` no Postgres e gerar `url` a partir do R2.
18. Registrar metricas, logs e FAQs consultados.
19. Cachear somente respostas confiaveis.

## Guardrails obrigatorios

Implemente com comportamento fail-closed:

- Se `top_score < MIN_SCORE_TOP_CHUNK` (padrao 0.65), force `TRANSFERIR_HUMANO`.
- Se `confianca < MIN_CONFIANCA_RESPOSTA` (padrao 0.70), force `TRANSFERIR_HUMANO`.
- Se o LLM citar FAQ que nao veio dos chunks recuperados, force `TRANSFERIR_HUMANO`.
- Se a pergunta estiver fora do escopo SCI/contabil, force `TRANSFERIR_HUMANO`.
- Se houver pedido de senha, acesso, cadastro, financeiro, dados sensiveis ou analise de numeros especificos do cliente, force `TRANSFERIR_HUMANO`.
- Se o cliente pedir humano ou demonstrar irritacao clara, force `TRANSFERIR_HUMANO`.
- Se detectar PII sensivel no prompt, mas a pergunta ainda for respondível, remova/mascare antes do LLM.
- Opcional: usar NLI/cross-encoder para verificar se claims da resposta sao suportados pelos chunks.
- Nunca inventar caminho de menu, nome de campo, registro SPED ou comportamento do sistema.
- Caminhos de menu devem ser copiados literalmente de `menus_caminhos`.
- Codigos SPED devem ser preservados literalmente: `K300`, `K310`, `K315`, `I012`, `I050`, `I155`, `J100`, `J150`, etc.

## Regras do agente de resposta

O agente deve responder em portugues brasileiro, direto, profissional, sem emojis e sem informalidade excessiva.

O cliente final nao deve saber que existe busca vetorial, chunks, Qdrant ou prompt. O agente deve se apresentar apenas como atendente virtual da empresa configurada.

Respostas para WhatsApp:

- 3 a 8 linhas quando possivel.
- Dividir em bolhas quando houver muitos passos.
- Uma pergunta de clarificacao por vez.
- Enviar imagens quando o chunk de imagem tiver relevancia alta e `quando_enviar` combinar com a duvida.
- Nao usar conhecimento geral fora da base recuperada.
- Sempre preencher `faqs_consultados`.

## Cache

Use Redis para cache de respostas.

Chave:

```text
hash(mensagem_normalizada + cliente.licenca_sci + conversa.departamento_atual)
```

Normalizacao:

- lowercase.
- remover acentos.
- remover pontuacao.
- normalizar espacos.

TTL:

- Confianca >= 0.90: 24h.
- Confianca entre 0.70 e 0.90: 6h.
- Transbordo: nao cachear.

## Webhooks outbound

Dispare webhooks assinados por HMAC-SHA256 para:

- `query.transferred_human`
- `query.low_confidence`
- `ingest.completed`
- `feedback.negative`

Headers:

- `X-RAG-Signature: sha256=<hmac>`
- `X-RAG-Timestamp: <unix_timestamp>`

Use tolerancia anti-replay de 5 minutos no receptor.

## Observabilidade

Monitore:

- latencia p50, p95 e p99 de `/v1/query`
- taxa de transbordo
- distribuicao de confianca
- intencoes nao respondidas
- custo diario de LLM
- erros por endpoint
- cache hit/miss
- tempo de busca, rerank e LLM
- chunks e FAQs mais consultados

Inclua logs estruturados com `request_id`, `api_key_id`, `cliente.id_externo`, `conversa.id_externo`, acao, confianca e modelo usado. Nao logue PII sensivel nem debug em producao por padrao.

## Docker e deploy

Crie `docker-compose.yml` com:

- `api`: FastAPI em `127.0.0.1:8000`, healthcheck `/v1/health`.
- `worker`: Celery para filas `ingestion` e `webhooks`.
- `qdrant`: porta interna 6333/6334, API key, volume persistente.
- `redis`: cache e broker, maxmemory 1GB, policy LRU.
- `minio`: opcional somente para desenvolvimento local via profile `local-storage`; em producao use Cloudflare R2.
- `postgres`: API keys, logs, feedback e jobs.
- `nginx`: TLS, reverse proxy, body upload 50M, rate limit por `X-API-Key`.
- `prometheus` e `grafana` em profile opcional `monitoring`.

Crie `.env.example` com:

- `POSTGRES_PASSWORD`
- `QDRANT_API_KEY`
- `MINIO_USER`
- `MINIO_PASS`
- `R2_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`
- `R2_PUBLIC_BASE_URL`
- `R2_PRESIGNED_URL_TTL_SECONDS`
- `ANTHROPIC_API_KEY`
- `DEEPSEEK_API_KEY`
- `WEBHOOK_SECRET`
- `WEBHOOK_NEXIRY_URL`
- `GRAFANA_PASSWORD`
- `NEXIRY_API_KEY`

Nao commitar segredos reais.

## Integracao com Nexiry

Forneca exemplo de cliente Python async para Django/Celery do Nexiry:

- Enviar `POST /v1/query`.
- Tratar erros permanentes como payload invalido.
- Tratar erros transientes como indisponibilidade e transbordar.
- Se `acao == TRANSFERIR_HUMANO`, bloquear ciclo do bot e transferir para `departamento_sugerido`.
- Se `acao == RESPONDER`, enviar cada item de `mensagens` via Evolution API:
  - `tipo=texto`: enviar texto.
  - `tipo=imagem`: enviar imagem por URL com legenda.
- Registrar auditoria no Nexiry com `request_id`, `acao`, `confianca`, `faqs_consultados`, `modelo_usado`, custo e tempo.

## Testes obrigatorios

Crie testes unitarios para:

- chunker.
- PII scrubber.
- query rewriter.
- guardrails.
- validacao de schemas.
- cache key normalization.
- assinatura HMAC.
- API key scopes.

Crie testes de integracao para:

- `POST /v1/query`.
- `POST /v1/feedback`.
- `POST /v1/admin/ingest`.
- healthcheck degradado quando Qdrant/Redis falham.

Crie benchmark CLI:

```bash
python scripts/benchmark.py --gabarito tests/gabarito.json
```

Metas:

- top-1 >= 85%.
- top-3 >= 95%.
- 0% de alucinacao em caminhos de menu, campos e codigos SPED.
- transbordo correto para perguntas fora de escopo.
- imagem correta quando a pergunta pedir orientacao visual.

Inclua gabarito inicial com perguntas como:

- "como marco eliminacao K300 no balanco?" -> FAQ 7085.
- "opcao K300/K315 esta cinza, o que faco?" -> FAQ 7085.
- "como gera DRE consolidada com bloco K?" -> FAQ 7087.
- "erro registro I030 livro R, como resolvo?" -> FAQ 6950.
- "como exportar J100 J150 consolidado?" -> FAQ 7078.
- "lancamento K300 com conta participante" -> FAQ 6693.
- "comparar saldos I155 entre ECDs" -> FAQ 6596.
- "analisa esses numeros do meu balanco" -> deve transbordar.

## Ordem de implementacao

Implemente nesta ordem:

1. `pyproject.toml`, `.env.example`, `Dockerfile`, `docker-compose.yml`.
2. `app/config.py` com Pydantic Settings.
3. Schemas Pydantic de query, feedback, ingest e chunk.
4. `app/core/security.py` com API key e HMAC.
5. `app/core/cache.py`, rate limit e logging.
6. Wrappers de Qdrant, Redis, Object Storage e Postgres.
7. `app/rag/embeddings.py`.
8. `app/rag/retrieval.py`.
9. `app/rag/reranker.py`.
10. `app/rag/query_rewriter.py`.
11. `app/rag/generation.py`.
12. `app/rag/guardrails.py`.
13. `app/rag/engine.py`.
14. `app/api/v1/query.py`, health, feedback e stats.
15. Pipeline de ingestao e endpoints admin.
16. Scripts de API key, seed PDF, reindex e benchmark.
17. Testes unitarios, integracao e e2e.
18. README com comandos de deploy e exemplos cURL.

## Criterios de aceite

A entrega so esta completa quando:

- `docker compose up -d` sobe API, worker, Qdrant, Redis e Postgres; MinIO sobe apenas no profile local quando R2 nao for usado em desenvolvimento.
- `GET /v1/health` retorna status OK com componentes verificados.
- `scripts/generate_api_key.py` cria uma chave com escopos e salva apenas hash.
- `POST /v1/admin/ingest` aceita PDF e cria job assíncrono.
- O PDF de FAQs gera chunks de texto e imagem no Qdrant.
- As imagens ficam armazenadas no Cloudflare R2.
- O Postgres guarda as referencias das imagens em `rag_image_assets`.
- Os chunks de imagem no Qdrant guardam `image_asset_id`, `r2_bucket` e `r2_key`.
- A resposta de `/v1/query` resolve a referencia R2 e retorna URL publica ou assinada para envio via Evolution API.
- `POST /v1/query` responde uma pergunta sobre K300/K315 com FAQ correto e imagem quando relevante.
- Perguntas fora de escopo retornam `TRANSFERIR_HUMANO`.
- Debug so aparece quando `opcoes.incluir_debug=true`.
- Transbordos nao entram no cache.
- Webhooks sao assinados por HMAC.
- Testes automatizados passam.
- Benchmark inicial atinge as metas definidas.

## Documentação obrigatória dos endpoints

Documente todos os endpoints da API em `README.md` e garanta que o Swagger/OpenAPI do FastAPI esteja completo.

Para cada endpoint, incluir:

- Método HTTP e path.
- Objetivo do endpoint.
- Headers obrigatórios, incluindo `X-API-Key`.
- Escopos exigidos da API key.
- Schema completo do request.
- Schema completo do response.
- Exemplos reais em cURL.
- Possíveis códigos de status HTTP.
- Regras de erro e fallback.
- Observações de segurança.
- Exemplo de payload para integração com Nexiry.
- Exemplo de resposta para `RESPONDER`, `TRANSFERIR_HUMANO` e `PEDIR_CLARIFICACAO`.

Também documente:

- Webhooks outbound.
- Headers HMAC: `X-RAG-Signature` e `X-RAG-Timestamp`.
- Como validar assinatura HMAC no receptor.
- Variáveis de ambiente relacionadas.
- Fluxo completo Nexiry → RAG → Evolution API.
- Collection Postman ou Insomnia exportável em `docs/postman_collection.json`.

Crie uma pasta:

docs/
  api.md
  webhooks.md
  integration-nexiry.md
  postman_collection.json

## Restricoes finais

- Nao use n8n ou Supabase pgvector como nucleo do RAG.
- Nao use chunking fixo simples como estrategia principal.
- Nao gere resposta sem citacao de FAQ consultado quando `acao=RESPONDER`.
- Nao exponha detalhes internos ao cliente final.
- Nao hardcode segredos.
- Nao salve API keys raw.
- Nao assuma que o LLM sempre retorna JSON valido; valide e faca retry controlado.
- Nao deixe falhas de guardrail seguirem para o usuario; em duvida, transborde.
- Preserve literalmente textos tecnicos, caminhos de menu, nomes de campos e codigos SPED vindos da base.
