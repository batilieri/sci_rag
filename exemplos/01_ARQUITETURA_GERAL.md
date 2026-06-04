# Arquitetura RAG de Alta Precisão para SCI — Base SCI ECD/ECF

## Objetivo

Construir uma base vetorial **cirúrgica** que permita a IA do SCI responder dúvidas do sistema SCI Contábil (FAQs, ECD, ECF, Bloco K, etc.) **exatamente como a documentação oficial responde**, incluindo:

- Caminho exato do menu (`Relatórios > Balanço patrimonial > ...`)
- Códigos de registro (K300, K310, K315, I012, I050, I155...)
- **Prints das telas do sistema** enviados via WhatsApp
- Quando não souber → transbordo para atendente humano

---

## Visão de Alto Nível

```
┌─────────────────────────────────────────────────────────────────┐
│ FASE 1 — INGESTÃO (offline, roda 1x por documento)              │
└─────────────────────────────────────────────────────────────────┘
  PDF FAQ SCI
     │
     ▼
  [Docling/PyMuPDF] ──► extrai texto + imagens + estrutura
     │
     ▼
  [Parser Customizado] ──► identifica blocos "FAQ X - título"
     │                       separa: pergunta | resposta | imagens
     ▼
  [Vision LLM] ──► para cada imagem:
     │              - OCR do que está escrito na tela
     │              - descrição contextual ("Tela de exportação ECD com
     │                 opção 'Considerar eliminações K300/K315' marcada")
     │              - identifica menu/caminho mostrado
     ▼
  [Chunking Semântico] ──► quebra cada FAQ em chunks coesos
     │                       (1 chunk = 1 conceito completo)
     ▼
  [Enriquecimento] ──► adiciona metadados:
     │                   - faq_id, categoria, palavras-chave
     │                   - registros SPED mencionados (K300, I012...)
     │                   - menu_paths (["Relatórios > BP"])
     │                   - imagens_associadas (["img_001.png"])
     ▼
  [Embedding] ──► gera vetor (BGE-M3 ou OpenAI text-embedding-3-large)
     │
     ▼
  [Qdrant] ──► armazena vetor + payload completo
  [Object Storage] ──► armazena imagens originais (Oracle Object Storage)

┌─────────────────────────────────────────────────────────────────┐
│ FASE 2 — CONSULTA (runtime, cada msg do WhatsApp)               │
└─────────────────────────────────────────────────────────────────┘
  Cliente WhatsApp ──► "Como eliminar K300 no balanço?"
     │
     ▼
  [SCI recebe via Evolution API]
     │
     ▼
  [Query Rewriter LLM] ──► reescreve em 2-3 variantes:
     │                       - "eliminação K300 K315 balanço patrimonial"
     │                       - "como considerar eliminações no BP"
     │                       - "Bloco K balanço grupo econômico"
     ▼
  [Busca Híbrida no Qdrant]
     ├─ Busca densa (embedding semântico)
     ├─ Busca esparsa (BM25 por palavras-chave: "K300", "K315")
     └─ Filtros de metadados (categoria=ECD)
     │
     ▼
  [Reranker — BGE-reranker-v2-m3] ──► top 20 → top 3-5 mais relevantes
     │
     ▼
  [LLM Principal — DeepSeek V4 Pro / Claude Sonnet 4.5]
     │  Recebe: pergunta + chunks recuperados + prompt rigoroso
     │  Retorna: resposta estruturada + lista de imagens a enviar
     ▼
  [Guardrail de Confiança]
     ├─ Score < threshold? → "Vou transferir para atendente humano"
     ├─ Score >= threshold → resposta + envia imagens via Evolution API
     └─ Resposta inventada? (detector) → transbordo
     │
     ▼
  Cliente recebe: texto + prints + caminho do menu
```

---

## Componentes Detalhados

### 1. Camada de Extração

| Camada | Ferramenta | Por quê |
|---|---|---|
| Extração estruturada de PDF | **Docling** (IBM) | Mantém hierarquia, tabelas, posição de imagens; melhor que PyMuPDF puro para FAQs com layout complexo |
| OCR de imagens (fallback) | **Tesseract 5** + **PaddleOCR** | Para textos dentro de prints onde o Vision LLM falhar |
| Vision LLM para descrição de prints | **DeepSeek-VL2** ou **Claude Sonnet 4.5** | Descreve a tela com precisão técnica, identifica botões, campos destacados |

### 2. Camada de Chunking

**Estratégia: Chunking Hierárquico Semântico**

Não usar chunking fixo (500 tokens). Usar **chunking por unidade semântica**:

- **Chunk-pai (parent)**: FAQ inteiro (ex: FAQ 7085 completo) — usado como contexto
- **Chunk-filho (child)**: subseção lógica (ex: "Como emitir", "Cálculo da eliminação", "Saldo Anterior")
- **Chunk-imagem**: cada imagem como chunk independente com descrição vetorizada

Tamanho alvo dos child chunks: **200-400 tokens** (precisão > recall).

### 3. Camada de Embedding

**Modelo recomendado para português técnico contábil:**

| Opção | Dim | Custo | Quando usar |
|---|---|---|---|
| **BGE-M3** (multilíngue, self-hosted) | 1024 | Grátis (GPU) | Padrão recomendado — excelente PT, suporta busca densa + esparsa + multivetor no mesmo modelo |
| **OpenAI text-embedding-3-large** | 3072 | $0.13/1M tokens | Se quiser zero infra |
| **Cohere embed-multilingual-v3** | 1024 | $0.10/1M | Boa alternativa managed |

**Estratégia híbrida**: para cada chunk, gerar **3 representações**:
1. Embedding denso (BGE-M3 dense)
2. Sparse vector (BGE-M3 sparse, equivalente a BM25 aprendido)
3. ColBERT-style multivector (BGE-M3 colbert) — para reranking de altíssima precisão

### 4. Camada de Armazenamento

```
┌──────────────────────────────────────────────────┐
│ Qdrant (Oracle Cloud VM, mesma rede do SCI)   │
├──────────────────────────────────────────────────┤
│ Collection: sci_faq_ecd_ecf                       │
│  ├─ Vector dense (1024)                           │
│  ├─ Vector sparse (BM25-like)                     │
│  ├─ Vector colbert (multivector, late interaction)│
│  └─ Payload (JSON com metadados ricos)            │
└──────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────┐
│ Oracle Object Storage / MinIO                     │
├──────────────────────────────────────────────────┤
│ Imagens originais (PNG, alta resolução)           │
│ URL pré-assinada para envio via Evolution API     │
└──────────────────────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────┐
│ MariaDB (SCI existente)                        │
├──────────────────────────────────────────────────┤
│ Tabela `rag_documents` — metadados mestres        │
│ Tabela `rag_query_logs` — auditoria de consultas  │
│ Tabela `rag_feedback` — feedback do atendente     │
└──────────────────────────────────────────────────┘
```

### 5. Camada de Recuperação

**Pipeline de busca em 3 estágios:**

```python
# Estágio 1: Recuperação ampla (recall)
hits = qdrant.query_points(
    collection_name="sci_faq_ecd_ecf",
    prefetch=[
        Prefetch(query=dense_vec, using="dense", limit=20),
        Prefetch(query=sparse_vec, using="sparse", limit=20),
    ],
    query=FusionQuery(fusion=Fusion.RRF),  # Reciprocal Rank Fusion
    limit=20,
    query_filter=Filter(
        must=[FieldCondition(key="categoria", match=MatchAny(any=["ECD", "ECF"]))]
    )
)

# Estágio 2: Reranking ColBERT (precisão)
reranked = qdrant.query_points(
    collection_name="sci_faq_ecd_ecf",
    query=colbert_vec,
    using="colbert",
    limit=5,
    prefetch=[Prefetch(query=hits, limit=20)]
)

# Estágio 3: Reranker cross-encoder externo (opcional, só se top score < 0.7)
final = bge_reranker.rerank(query, reranked, top_k=3)
```

### 6. Camada de Geração

**Dois modelos em paralelo (A/B):**

- **DeepSeek V4 Pro** — produção, custo-benefício, ótimo PT
- **Claude Sonnet 4.5** — fallback para queries complexas / quando DeepSeek tem confiança baixa

Roteamento:
```
if pergunta_simples and confidence_retrieval > 0.85:
    use DeepSeek V4 Pro
elif pergunta_complexa or multiplas_intencoes:
    use Claude Sonnet 4.5
else:
    use DeepSeek V4 Pro com retry em Sonnet se falhar
```

### 7. Guardrails (CRÍTICO para precisão cirúrgica)

| Guardrail | O que faz |
|---|---|
| **Score mínimo de recuperação** | Se top-1 score < 0.65 → transbordo humano |
| **Citation enforcement** | LLM obrigado a citar `faq_id` no JSON de saída; sem citação válida → bloqueio |
| **Hallucination detector** | Compara claims da resposta com chunks recuperados (NLI cross-encoder) |
| **Out-of-scope detector** | Classificador binário: "é pergunta sobre SCI/contábil?" — se não, transbordo |
| **PII scrubber** | Remove CPF/CNPJ/dados do cliente antes de enviar para o LLM |

---

## Stack Técnica Final

```yaml
extracao:
  - docling==2.x
  - pymupdf==1.24
  - tesseract-ocr (sistema)
  - paddleocr==2.7

processamento:
  - python 3.11
  - celery (já usa no SCI)
  - redis (já usa)

vetores:
  - qdrant==1.12 (docker, Oracle Cloud)
  - FlagEmbedding (BGE-M3)
  - sentence-transformers

armazenamento:
  - qdrant (vetores + payload)
  - mariadb (metadados, já existe)
  - oracle object storage OU minio (imagens)

llm:
  - deepseek-v4-pro (via API)
  - claude-sonnet-4-5 (via Anthropic API)

orquestracao:
  - django (já é o backend SCI)
  - novo app: rag_engine
  - novo app: knowledge_ingestion
```

---

## Fluxo de Integração no SCI

```
┌─────────────────┐
│ WhatsApp        │
└────────┬────────┘
         │
┌────────▼────────┐
│ Evolution API   │
└────────┬────────┘
         │ webhook
┌────────▼────────────────────────────────┐
│ SCI Django                            │
│  ├─ app: atendimento (existente)         │
│  ├─ app: bot_engine (existente)          │
│  │   └─ bot_bloqueado_ciclo lógica       │
│  ├─ NOVO app: rag_engine                 │
│  │   ├─ retrieval.py                     │
│  │   ├─ generation.py                    │
│  │   ├─ guardrails.py                    │
│  │   └─ media_dispatcher.py              │
│  └─ NOVO app: knowledge_ingestion        │
│      ├─ pdf_extractor.py                 │
│      ├─ chunker.py                       │
│      ├─ vision_describer.py              │
│      └─ vectorizer.py                    │
└──────────────────────────────────────────┘
```

A camada `rag_engine` é chamada pelo `bot_engine` quando a IA precisa responder uma dúvida técnica. O `media_dispatcher` envia as imagens via Evolution API quando a resposta as referencia.
