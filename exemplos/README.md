# 🎯 RAG Cirúrgico para SCI — Base de Conhecimento SCI

Este pacote contém **tudo** que você precisa para construir uma base vetorial de altíssima precisão a partir do PDF de FAQs do sistema SCI Contábil, plugar no SCI, e fazer a IA do WhatsApp responder com texto + prints exatamente como a documentação oficial responde.

---

## 📂 Estrutura dos Arquivos

```
sci_rag/
├── arquitetura/
│   ├── 01_ARQUITETURA_GERAL.md       ← visão completa + stack técnica
│   └── 03_DEPLOY_DOCKER.md            ← docker-compose, dockerfile, .env
├── exemplos/
│   └── 02_EXEMPLO_CHUNK_VETORIZADO.md ← payload JSON real do Qdrant
├── prompts/
│   ├── PROMPT_01_extracao_estruturada.md  ← LLM transforma FAQ em JSON
│   ├── PROMPT_02_descricao_imagem.md      ← Vision LLM descreve telas
│   └── PROMPT_03_agente_rag_producao.md   ← agente que responde no WhatsApp
└── scripts/
    ├── ingest_pdf.py     ← roda 1x para indexar o PDF inteiro
    └── rag_runtime.py    ← chamado a cada mensagem do cliente
```

---

## 🧠 Por que essa arquitetura é "cirúrgica"

A maioria dos sistemas RAG falha porque:
1. Chunking ingênuo (corta no meio de uma instrução)
2. Embedding único (perde precisão para termos técnicos como "K300", "esmaecido")
3. Sem reranking (recall alto, precisão baixa)
4. LLM "preenche lacunas" com conhecimento geral

O design aqui resolve cada um:

| Problema | Solução adotada |
|---|---|
| Chunking ingênuo | **Chunking semântico hierárquico** — parent (FAQ inteiro) + child (subseções) + image (cada print) |
| Embedding único frágil | **BGE-M3 triplo**: denso (semântico) + esparso (BM25 aprendido) + ColBERT (late interaction para reranking) |
| Recall vs precisão | **Pipeline 3 estágios**: busca híbrida com RRF → rerank ColBERT → opcional cross-encoder externo |
| Alucinação | **5 guardrails**: score mínimo, citação obrigatória, NLI checker, out-of-scope, PII scrubber |
| Termos técnicos perdidos | **Payload com `palavras_chave_exatas`** + busca esparsa garante match literal de "K300", "I012", etc. |
| Imagens jamais enviadas | **Cada imagem é chunk próprio** com descrição rica vetorizada + campo `quando_enviar` |
| LLM inventa caminhos de menu | Campos `menus_caminhos` estruturados + prompt do agente proíbe paráfrase |

---

## 🚀 Roadmap de Implementação (sugestão de execução)

### Semana 1 — Infraestrutura base
- [ ] Subir Qdrant via Docker no Oracle Cloud (mesmo VM do SCI ou separado)
- [ ] Configurar MinIO ou Oracle Object Storage para imagens
- [ ] Criar app Django `rag_engine` no SCI
- [ ] Criar app Django `knowledge_ingestion`
- [ ] Configurar credenciais Anthropic + DeepSeek

### Semana 2 — Pipeline de ingestão
- [ ] Instalar dependências pesadas (Docling, BGE-M3, PyMuPDF)
- [ ] Rodar `ingest_pdf.py` com o PDF anexo (22 FAQs) como POC
- [ ] Validar manualmente 5-10 chunks gerados: estão corretos? Imagens descritas? Caminhos preservados?
- [ ] Ajustar prompts 01 e 02 se necessário
- [ ] Indexar todos os FAQs SCI disponíveis

### Semana 3 — Runtime e integração
- [ ] Implementar `rag_runtime.py` no app `rag_engine`
- [ ] Criar tabelas `RAGQueryLog`, `RAGFeedback` no MariaDB
- [ ] Plugar `rag_engine.responder_mensagem()` no fluxo do `bot_engine` existente
- [ ] Implementar `media_dispatcher` para enviar imagens via Evolution API
- [ ] Testar end-to-end com 20 perguntas reais de clientes

### Semana 4 — Tuning e produção
- [ ] Avaliar 50-100 conversas reais: precisão, taxa de transbordo, satisfação
- [ ] Ajustar `THRESHOLDS` no runtime
- [ ] Criar painel admin para revisão humana dos chunks (`revisado_humano: true` dá boost)
- [ ] Habilitar reranking ColBERT (mais caro, mas precisão sobe ~15%)
- [ ] Documentar fluxo para equipe

---

## 💰 Custos Estimados

### Ingestão (uma vez por documento)

| Item | 22 FAQs (PDF amostra) | 500 FAQs (catálogo completo) |
|---|---|---|
| LLM estruturação (Sonnet) | ~$0.50 | ~$10 |
| Vision LLM (Sonnet) | ~$0.75 | ~$22 |
| Embedding BGE-M3 (self-hosted) | $0 | $0 |
| **Total ingestão** | **< $2** | **< $35** |

### Runtime (mensal, supondo 5.000 mensagens/mês)

| Item | Custo |
|---|---|
| Query rewriter (DeepSeek) | ~$2/mês |
| LLM principal mix DeepSeek+Sonnet | ~$15-40/mês |
| Qdrant (Oracle Cloud VM) | ~$10-30/mês (já existe) |
| Object storage | ~$1/mês |
| **Total runtime** | **~$30-75/mês** |

Comparado a 1 atendente humano: **economia de 95%+** com qualidade equivalente em tarefas documentadas.

---

## 🔍 Como Validar a Precisão

Crie um conjunto de **30 perguntas-teste** com gabarito. Exemplos:

| Pergunta-teste | FAQ esperado | Imagem esperada |
|---|---|---|
| "como marco eliminação K300 no balanço?" | 7085 | img tela balanço |
| "opção K300/K315 está cinza, oq faço?" | 7085 | img tela balanço |
| "como gera DRE consolidada com bloco K?" | 7087 | img tela DRE |
| "erro registro I030 livro R, como resolvo?" | 6950 | img erro + img lançamento |
| "como exportar J100 J150 consolidado?" | 7078 | img tela exportação |
| "lançamento K300 com conta participante" | 6693 | img K300 + K310/K315 |
| "comparar saldos I155 entre ECDs" | 6596 | img comparativo |

Para cada teste, mede:
- ✅ **Acerto top-1**: o FAQ correto é o primeiro recuperado?
- ✅ **Imagem correta**: a imagem certa foi anexada?
- ✅ **Caminho de menu literal**: o LLM copiou exatamente "Relatórios > Balanço patrimonial"?
- ✅ **Sem alucinação**: o LLM não inventou nenhum campo/menu?
- ✅ **Transbordo correto**: perguntas fora de escopo geram transferência?

Meta inicial: **>85% de acerto top-1** e **0% de alucinação** (alucinação é critical fail).

---

## 🛡️ Por que NÃO usar n8n + Supabase pgvector para este caso

Você tem experiência com n8n e Supabase, então é tentador resolver tudo lá. Mas para este caso específico, **não recomendo** porque:

1. **n8n não suporta bem chunking hierárquico custom** — você precisaria de muitos nodes Code, perdendo o benefício do low-code.
2. **pgvector não tem busca esparsa nativa** — você perderia o match exato de "K300" que é crítico aqui.
3. **pgvector não suporta multi-vector (ColBERT)** — perde o reranking de altíssima precisão.
4. **Vision LLM em batch via n8n é caro e lento** — fazer isso em Python puro com Celery é 5x mais rápido.

n8n continua ótimo para **outras automações** do SCI (notificações, integrações, workflows simples). Mas para a **espinha dorsal do RAG cirúrgico**, Python + Qdrant + Django integrado é o caminho.

---

## 📌 Próximos Passos Imediatos

1. **Decida o stack** com base nas perguntas que respondi:
   - Banco vetorial: Qdrant (recomendo) ou alternativa
   - Imagens: híbrido OCR + Vision LLM (recomendo) ou alternativa
   - Extração: Docling + Vision LLM (recomendo) ou alternativa

2. **Rode o `ingest_pdf.py`** no PDF que você enviou. Pegue 1 FAQ específico (sugiro o 7085) e inspecione manualmente o JSON gerado. Esse é o seu ponto de validação.

3. **Mostre o output para mim** — posso ajustar os prompts e o pipeline com base no que o LLM produzir na prática.

4. **Depois disso**, integramos no SCI e fazemos os testes end-to-end com Evolution API.

---

Quer que eu aprofunde alguma parte específica? Por exemplo:
- Detalhar o app Django `rag_engine` com models, views, signals
- Mostrar como o `bot_bloqueado_ciclo` se integra com o transbordo do RAG
- Criar o painel React no frontend SCI para revisar/aprovar chunks
- Adaptar o pipeline para também ingerir Word/Excel/manuais de outros sistemas
- Mostrar como rodar o BGE-M3 numa GPU pequena (T4) vs CPU pura
