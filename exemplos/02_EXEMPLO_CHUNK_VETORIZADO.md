# Schema do Chunk Vetorizado — Exemplo Real

Este documento mostra **exatamente** como cada pedaço da FAQ vira um vetor no Qdrant.

## Estrutura do Payload

Cada ponto no Qdrant tem:
- **Vetor denso** (1024 dim, BGE-M3)
- **Vetor esparso** (BGE-M3 sparse)
- **Vetor ColBERT** (multivector para reranking)
- **Payload** (metadados ricos abaixo)

---

## Exemplo 1: Chunk de TEXTO (FAQ 7085 — Balanço com eliminações K300/K315)

```json
{
  "id": "faq_7085_chunk_002",
  "vectors": {
    "dense": [0.0234, -0.1245, 0.0891, "... 1024 valores ..."],
    "sparse": {
      "indices": [102, 4521, 8934, 12045, 23145],
      "values": [0.89, 0.76, 0.65, 0.54, 0.43]
    },
    "colbert": [
      [0.12, -0.34, "..."],
      [0.45, 0.11, "..."],
      "... N tokens × 128 dim ..."
    ]
  },
  "payload": {
    "faq_id": "7085",
    "faq_titulo": "Como realizar a emissão do Balanço patrimonial considerando as eliminações do K300/K315?",
    "categoria_principal": "Relatórios NV/Único Fiscal e Contábil",
    "categorias_secundarias": ["SPED ECD", "Balanço Patrimonial"],
    "sistema": "SCI Contábil",
    "modulo": "Contábil",
    "versao_sistema": "Novo Visual",

    "chunk_index": 2,
    "chunk_total": 5,
    "chunk_tipo": "procedimento",
    "parent_chunk_id": "faq_7085_parent",

    "titulo_secao": "Como emitir o relatório com as eliminações",

    "texto_original": "Acesse o menu Relatórios > Balanço patrimonial. Selecione um Grupo econômico e marque a opção Considerar as eliminações do K300/K315. A opção Considerar as eliminações do K300/K315 fica disponível para seleção somente se um Grupo econômico estiver selecionado. Caso contrário, o campo permanecerá esmaecido.",

    "texto_enriquecido_para_embedding": "FAQ 7085 - Balanço Patrimonial com eliminações K300 K315. Procedimento: como emitir o relatório de Balanço Patrimonial considerando as eliminações de participações societárias do Bloco K. Caminho: Relatórios > Balanço patrimonial. Pré-requisito: Grupo econômico selecionado. Opção a marcar: Considerar as eliminações do K300/K315. Comportamento: campo fica esmaecido (desabilitado) sem grupo econômico selecionado. Palavras-chave: balanço patrimonial, eliminações, K300, K315, Bloco K, grupo econômico, conglomerado, participações societárias, consolidação.",

    "registros_sped_mencionados": ["K300", "K310", "K315"],
    "relatorios_mencionados": ["Balanço Patrimonial"],
    "menus_caminhos": [
      "Relatórios > Balanço patrimonial",
      "Lançamentos > Sped - lançamentos do K310/K315"
    ],

    "campos_interface": [
      {"nome": "Grupo econômico", "tipo": "select", "obrigatorio": true},
      {"nome": "Considerar as eliminações do K300/K315", "tipo": "checkbox", "depende_de": "Grupo econômico"}
    ],

    "palavras_chave_exatas": [
      "balanço patrimonial",
      "K300",
      "K315",
      "K310",
      "eliminações",
      "grupo econômico",
      "Bloco K",
      "esmaecido",
      "participações societárias",
      "consolidação contábil"
    ],

    "imagens_associadas": [
      {
        "id": "img_faq_7085_01",
        "filename": "faq_7085_balanco_patrimonial_tela.png",
        "url": "https://storage.sci.com/sci/faq/7085/img_01.png",
        "descricao_curta": "Tela 'Relatório balanço patrimonial' com a opção 'Considerar as eliminações do K300/K315' destacada em vermelho",
        "tipo": "screenshot_tela_sistema",
        "ordem": 1
      }
    ],

    "intencoes_atendidas": [
      "emitir balanço patrimonial consolidado",
      "como aplicar eliminações no balanço",
      "configurar bloco K no balanço",
      "balanço de grupo econômico"
    ],

    "perguntas_exemplo": [
      "Como gero o balanço considerando eliminações K300?",
      "Onde marco para considerar eliminações no balanço?",
      "Por que a opção K300/K315 está esmaecida no balanço?",
      "Como faço balanço de grupo econômico?"
    ],

    "publico_alvo": ["contador", "analista_contabil", "auxiliar_contabil"],

    "data_cadastro_faq": "2026-05-11T14:37:00",
    "data_atualizacao_faq": "2026-05-12T14:41:00",
    "data_indexacao": "2026-05-23T10:00:00",

    "fonte": {
      "documento": "FAQ_SCI_Contabil.pdf",
      "url_original": "https://areadocliente.sci10.com.br/modulo/faq/faq.php?faqId=7085&sistemaId=54",
      "pagina_pdf": 5
    },

    "confianca_extracao": 0.98,
    "revisado_humano": false
  }
}
```

---

## Exemplo 2: Chunk de IMAGEM (mesmo FAQ 7085)

A imagem vira um chunk **separado**, com vetor próprio (gerado a partir da descrição rica feita por Vision LLM).

```json
{
  "id": "img_faq_7085_01",
  "vectors": {
    "dense": [0.0567, 0.2134, -0.0445, "... 1024 valores ..."],
    "sparse": {"indices": [...], "values": [...]},
    "colbert": [[...], [...]]
  },
  "payload": {
    "tipo_chunk": "imagem",
    "faq_id": "7085",

    "filename": "faq_7085_balanco_patrimonial_tela.png",
    "storage_url": "https://storage.sci.com/sci/faq/7085/img_01.png",
    "storage_path_interno": "sci/faq/7085/img_01.png",
    "hash_md5": "a3f5b8c2d9e1...",
    "tamanho_bytes": 84521,
    "dimensoes": {"width": 1024, "height": 768},

    "descricao_vision_llm": "Screenshot da janela 'Relatório balanço patrimonial' do sistema SCI Contábil. A janela mostra campos de configuração organizados em seções: Data (Inicial: 01/01/2026, Final: 31/12/2026), Conta (Plano de fórmulas, Inicial: 19 ATIVO, Final: 3824 Resultado Líquido do Exercício), Níveis de contas (filtros Analíticas, Nível 2, 3, 4, 5 marcados), Ordem contas, Imprimir conta, Opções, Extenso, e Ordem dos dados a imprimir. Destacado em vermelho na seção 'Opções' está o checkbox 'Considerar as eliminações do K300/K315'. Visível também: opções 'Detalhar por participante', 'Destaca analítica', 'Contas consolidadas'. Barra superior com ícones de navegação e ações.",

    "ocr_texto_completo": "Relatório balanço patrimonial\nEmpresa: 13 Empresa Demonstração SC\nData Inicial: 01/01/2026 Final: 31/12/2026\nContabilização: Fiscal\nConta\nPlano de fórmulas:\nInicial: 19 01 - ATIVO\nFinal: 3824 05.1.1.01.001 - Resultado Líquido do Exercício\nPassivo a descoberto\nNíveis de contas\nFiltrar: Analíticas, Nível 2, Nível 3, Nível 4, Nível 5\nNegrito até:\nImprimir conta: Curta, Longa, Ambas, Nenhuma\nOrdem contas: Classificação, Alfabética\nOpções:\nDetalhar por filial\nDetalhar por participante\nAgrupar participante por CNPJ\nAgrupar participante por CPF\nLinhas zebradas\nDestaca analítica\nFórmulas\nSomar fórmulas as títulos\nExpresso em R$\nIgnora zeramento\n...\nConsiderar as eliminações do K300/K315\n...",

    "elementos_ui_identificados": [
      {"tipo": "janela", "titulo": "Relatório balanço patrimonial"},
      {"tipo": "campo_input", "label": "Empresa", "valor": "13 Empresa Demonstração SC"},
      {"tipo": "datepicker", "label": "Data Inicial", "valor": "01/01/2026"},
      {"tipo": "datepicker", "label": "Data Final", "valor": "31/12/2026"},
      {"tipo": "radio_group", "label": "Contabilização", "opcoes": ["Fiscal", "Societária"], "selecionado": "Fiscal"},
      {"tipo": "checkbox", "label": "Considerar as eliminações do K300/K315", "destacado": true, "cor_destaque": "vermelho"}
    ],

    "elementos_destacados_visualmente": [
      {
        "elemento": "checkbox 'Considerar as eliminações do K300/K315'",
        "tipo_destaque": "retângulo vermelho",
        "razao": "indicar ao usuário a opção a ser marcada"
      }
    ],

    "contexto_faq": "Esta imagem é referenciada na seção 'Como emitir o relatório com as eliminações' do FAQ 7085, ilustrando exatamente onde o usuário deve marcar a opção para aplicar as eliminações do Bloco K no Balanço Patrimonial.",

    "palavras_chave_exatas": [
      "balanço patrimonial",
      "K300/K315",
      "eliminações",
      "tela balanço",
      "print balanço",
      "configuração balanço",
      "opção esmaecida",
      "grupo econômico"
    ],

    "menu_caminho_ilustrado": "Relatórios > Balanço patrimonial",

    "quando_enviar": [
      "Quando cliente pergunta onde marcar opção de eliminação no balanço",
      "Quando cliente diz que não encontra a opção K300/K315",
      "Quando cliente pergunta visualmente onde está o checkbox",
      "Como complemento visual a qualquer explicação sobre balanço com eliminações"
    ],

    "modelo_vision_usado": "claude-sonnet-4-5-20250929",
    "data_descricao": "2026-05-23T10:00:00",
    "confianca_ocr": 0.96,
    "revisado_humano": false
  }
}
```

---

## Por que essa estrutura entrega "precisão cirúrgica"

| Característica | Como ajuda |
|---|---|
| **`texto_enriquecido_para_embedding`** diferente do `texto_original` | O embedding é gerado em cima de uma versão **aumentada** com sinônimos, contexto e palavras-chave. Isso melhora drasticamente o recall semântico em PT-BR técnico. |
| **`palavras_chave_exatas`** + busca esparsa | Garante que termos exatos como "K300", "I012", "esmaecido" sejam encontrados mesmo se o embedding semântico falhar. |
| **`perguntas_exemplo`** | Vetorizadas também (HyDE inverso) — quando cliente faz pergunta parecida, casa instantaneamente. |
| **`menus_caminhos`** estruturados | LLM consegue copiar **literalmente** o caminho na resposta, sem inventar. |
| **`imagens_associadas`** com `quando_enviar` | Sistema sabe quais prints anexar à resposta. |
| **`registros_sped_mencionados`** | Permite filtros precisos: "todos os FAQs sobre K300" vira filtro de payload, não busca semântica. |
| **Chunks separados para imagem** | Cliente pode perguntar "me manda o print de onde marca eliminação" e a busca acha a imagem diretamente. |
| **`revisado_humano`** | Você pode marcar os melhores chunks após revisão, e dar boost neles. |

---

## Tamanho Aproximado da Base

Para o PDF de 22 FAQs (a amostra que você enviou):
- ~22 chunks-pai
- ~80-110 chunks-filho (texto)
- ~40-60 chunks de imagem
- **Total: ~150-200 pontos no Qdrant**

Por FAQ completa do SCI (suponha 500 FAQs):
- **Total estimado: 3.500-5.000 pontos**

Tamanho em disco no Qdrant (com 3 tipos de vetor + payload rico):
- **~80-150 MB** — caberia até num servidor pequeno, mas use 4GB RAM mínimo para performance.
