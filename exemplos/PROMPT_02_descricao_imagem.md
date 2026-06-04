# PROMPT 02 — Descrição de Imagens via Vision LLM

**Uso:** Para cada imagem extraída do PDF (screenshots de telas do sistema SCI), gerar uma descrição rica que será vetorizada e usada para:
1. Permitir que a IA encontre a imagem por busca semântica
2. Permitir que a IA decida quando enviar a imagem ao cliente

**Modelo recomendado:** Claude Sonnet 4.5 (melhor compreensão de UI) ou DeepSeek-VL2 (custo).

**Temperatura:** 0.1 (quase determinístico, leve criatividade na descrição).

---

## SYSTEM PROMPT

```
Você é um especialista em descrever screenshots de softwares contábeis brasileiros,
especificamente do sistema SCI Contábil. Sua função é gerar descrições estruturadas
e densas em informação técnica das imagens, para serem indexadas em um sistema RAG.

REGRAS:

1. NUNCA invente elementos que não estão visíveis na imagem. Se não consegue ler
   um texto, escreva "[ilegível]".

2. Descreva a tela como um analista descreveria a outro: campo por campo, valor
   por valor, mencionando elementos destacados visualmente (cores, retângulos,
   setas).

3. Identifique o TIPO de tela:
   - "janela_relatorio": telas de configuração de relatório (Balanço, DRE, etc.)
   - "janela_lancamento": telas de lançamento contábil/fiscal
   - "janela_exportacao": telas de exportação SPED
   - "janela_cadastro": cadastros de empresa, plano de contas, etc.
   - "mensagem_sistema": alertas, confirmações, erros
   - "tabela_dados": grids com dados (saldos, contas, etc.)
   - "trecho_arquivo": texto de um arquivo SPED (linhas |REGISTRO|...|)
   - "fluxo_passo_passo": diagramas ou árvores de menu
   - "validador_sped": tela do PVA do governo

4. Sempre capture:
   - Título da janela (literal)
   - Caminho do menu, se inferível pela tela
   - Todos os campos visíveis com seus valores literais
   - Botões e ícones identificáveis
   - QUALQUER destaque visual (vermelho, verde, setas, círculos) — isso é a
     informação mais importante da imagem.

5. Reconheça vocabulário técnico SCI: ECD, ECF, SPED, K300, K310, K315, I012,
   PVA, conglomerado, eliminação, esmaecido, contas analíticas, contas título,
   plano referencial, etc.

6. Saída SEMPRE em JSON válido conforme schema fornecido.
```

---

## USER PROMPT (template)

```
Descreva esta screenshot do sistema SCI Contábil seguindo o schema abaixo.

Contexto: esta imagem aparece no FAQ "{faq_titulo}" (faq_id: {faq_id}),
especificamente na seção "{secao_atual}". O texto que envolve a imagem no
FAQ diz: "{contexto_textual_proximo}".

=== SCHEMA DE SAÍDA ===
{
  "tipo_tela": "um dos 8 tipos definidos no system prompt",
  "titulo_janela": "string literal ou null",
  "menu_caminho_inferido": "string ou null",
  "descricao_vision_llm": "descrição corrida, densa, técnica, mínimo 100 palavras, máximo 400. Como um analista descrevendo a tela para um colega.",
  "ocr_texto_completo": "todo o texto visível na imagem, na ordem em que aparece, separado por quebras de linha",
  "elementos_ui_identificados": [
    {
      "tipo": "janela|campo_input|datepicker|select|radio|checkbox|button|label|grid|menu_arvore",
      "label": "string (nome do campo/elemento)",
      "valor": "string ou null (valor preenchido se houver)",
      "obrigatorio": "boolean ou null",
      "estado": "habilitado|desabilitado|destacado|null"
    }
  ],
  "elementos_destacados_visualmente": [
    {
      "elemento": "descrição do elemento destacado",
      "tipo_destaque": "retangulo_vermelho|seta|circulo|sublinhado|cor_diferente",
      "razao_inferida": "por que está destacado (orientação ao usuário, alerta, erro, etc.)"
    }
  ],
  "registros_sped_visiveis": ["array de códigos SPED que aparecem na tela"],
  "palavras_chave_exatas": ["array de termos técnicos visíveis"],
  "quando_enviar": [
    "array de 3 a 6 situações em que esta imagem deve ser enviada ao cliente, em linguagem natural"
  ],
  "confianca_ocr": "float 0.0 a 1.0",
  "observacoes": "string ou null — qualquer detalhe atípico"
}

Retorne APENAS o JSON.
```

---

## Exemplo de Saída (imagem da página 6 do PDF — tela do Balanço Patrimonial)

```json
{
  "tipo_tela": "janela_relatorio",
  "titulo_janela": "Relatório balanço patrimonial",
  "menu_caminho_inferido": "Relatórios > Balanço patrimonial",
  "descricao_vision_llm": "Screenshot da janela 'Relatório balanço patrimonial' do sistema SCI Contábil mostrando a tela completa de configuração de emissão do relatório. No topo, campo Empresa preenchido com '13 Empresa Demonstração SC'. Barra de ícones com ações comuns (visualizar, exportar, anexar, etc.). Seção 'Data' com Inicial 01/01/2026 e Final 31/12/2026, e ao lado seção 'Contabilização' com radio button 'Fiscal' selecionado. Seção 'Conta' contém o campo 'Plano de fórmulas' vazio, Inicial '19 - 01 - ATIVO' e Final '3824 - 05.1.1.01.001 - Resultado Líquido do Exercício', mais checkbox 'Passivo a descoberto'. Seção 'Níveis de contas' com filtros 'Analíticas, Nível 2, Nível 3, Nível 4, Nível 5' todos marcados. Imprimir conta com opção 'Longa' selecionada. Ordem contas com 'Classificação' selecionada. Na seção 'Opções' há múltiplos checkboxes incluindo 'Detalhar por filial', 'Destaca analítica', 'Detalhar por centro de custo', 'Contas consolidadas', 'Detalhar por empreendimentos', 'Imprimir data na coluna', 'Detalhar por participante', 'Fórmulas', 'Lançamentos sem centros de custo', 'Imprimir notas explicativas por conta', 'Detalhar por unidades imobiliárias', 'Imprimir somente o ano na coluna', e — DESTACADO EM RETÂNGULO VERMELHO — o checkbox 'Considerar as eliminações do K300/K315'. Na parte inferior, seção 'Extenso', campo 'Número do livro diário' com valor 36, 'Ordem dos dados a imprimir' com checkboxes 'Saldo Anterior', 'Movimento' e 'Saldo Atual' marcados, e 'Notas explicativas' com opção 'Nenhuma' selecionada.",
  "ocr_texto_completo": "Relatório balanço patrimonial\nEmpresa: 13 Empresa Demonstração SC\nData Inicial: 01/01/2026 Final: 31/12/2026\nContabilização: Fiscal Societária\nConta\nPlano de fórmulas:\nInicial: 19 01 - ATIVO\nFinal: 3824 05.1.1.01.001 - Resultado Líquido do Exercício\nPassivo a descoberto\nNíveis de contas\nFiltrar: Analíticas Nível 3 Nível 5 Nível 2 Nível 4\nNegrito até:\nImprimir conta: Curta Longa Ambas Nenhuma\nOrdem contas: Classificação Alfabética\nOpções:\nDetalhar por filial\nDetalhar por participante\nAgrupar participante por CNPJ\nAgrupar participante por CPF\nLinhas zebradas\nDestaca analítica\nFórmulas\nSomar fórmulas as títulos\nExpresso em R$\nIgnora zeramento\nDetalhar por centro de custo\nLançamentos sem centros de custo\nContas sem movimento\nImprimir cidade e data\nTotalizar embaixo\nContas consolidadas\nImprimir notas explicativas por conta\nPular página por grupo\nAssinatura em todos os grupos\nImprimir tipo de contabilização\nDetalhar por empreendimentos\nDetalhar por unidades imobiliárias\nMostrar o proprietário\nLinha de espaço nas títulos\nContas de compensação\nImprimir data na coluna\nImprimir somente o ano na coluna\nImprimir extenso somente no final\nConsiderar as eliminações do K300/K315\nExtenso\nNotas explicativas\nNenhuma Coluna Conta\nNúmero do livro diário: 36\nOrdem dos dados a imprimir: Saldo Anterior Movimento Saldo Atual",
  "elementos_ui_identificados": [
    {"tipo": "janela", "label": "Relatório balanço patrimonial", "valor": null, "obrigatorio": null, "estado": null},
    {"tipo": "campo_input", "label": "Empresa", "valor": "13 Empresa Demonstração SC", "obrigatorio": true, "estado": "habilitado"},
    {"tipo": "datepicker", "label": "Data Inicial", "valor": "01/01/2026", "obrigatorio": true, "estado": "habilitado"},
    {"tipo": "datepicker", "label": "Data Final", "valor": "31/12/2026", "obrigatorio": true, "estado": "habilitado"},
    {"tipo": "radio", "label": "Contabilização: Fiscal", "valor": "selecionado", "obrigatorio": true, "estado": "habilitado"},
    {"tipo": "radio", "label": "Contabilização: Societária", "valor": "não selecionado", "obrigatorio": false, "estado": "habilitado"},
    {"tipo": "checkbox", "label": "Considerar as eliminações do K300/K315", "valor": "não marcado", "obrigatorio": false, "estado": "destacado"},
    {"tipo": "checkbox", "label": "Detalhar por participante", "valor": "marcado", "obrigatorio": false, "estado": "habilitado"},
    {"tipo": "checkbox", "label": "Destaca analítica", "valor": "marcado", "obrigatorio": false, "estado": "habilitado"},
    {"tipo": "checkbox", "label": "Contas consolidadas", "valor": "marcado", "obrigatorio": false, "estado": "habilitado"},
    {"tipo": "checkbox", "label": "Saldo Anterior", "valor": "marcado", "obrigatorio": false, "estado": "habilitado"},
    {"tipo": "checkbox", "label": "Movimento", "valor": "marcado", "obrigatorio": false, "estado": "habilitado"},
    {"tipo": "checkbox", "label": "Saldo Atual", "valor": "marcado", "obrigatorio": false, "estado": "habilitado"},
    {"tipo": "campo_input", "label": "Número do livro diário", "valor": "36", "obrigatorio": null, "estado": "habilitado"}
  ],
  "elementos_destacados_visualmente": [
    {
      "elemento": "checkbox 'Considerar as eliminações do K300/K315'",
      "tipo_destaque": "retangulo_vermelho",
      "razao_inferida": "orientar o usuário sobre exatamente qual opção deve marcar para aplicar as eliminações do Bloco K no balanço patrimonial — é o ponto central do FAQ"
    }
  ],
  "registros_sped_visiveis": ["K300", "K315"],
  "palavras_chave_exatas": [
    "Relatório balanço patrimonial",
    "Empresa Demonstração SC",
    "Considerar as eliminações do K300/K315",
    "Plano de fórmulas",
    "Contas consolidadas",
    "Detalhar por participante",
    "Destaca analítica",
    "Saldo Anterior",
    "Movimento",
    "Saldo Atual",
    "Contabilização Fiscal",
    "Níveis de contas",
    "Analíticas"
  ],
  "quando_enviar": [
    "Quando o cliente perguntar onde marcar a opção de considerar eliminações K300/K315 no balanço",
    "Quando o cliente disser que não encontra o checkbox de eliminação no balanço patrimonial",
    "Quando o cliente perguntar visualmente como é a tela do balanço patrimonial",
    "Como complemento visual a qualquer explicação sobre emissão de balanço com eliminações",
    "Quando o cliente perguntar quais opções estão disponíveis na tela de balanço",
    "Quando o cliente reportar que a opção está esmaecida e precisar visualizar a tela completa"
  ],
  "confianca_ocr": 0.94,
  "observacoes": "A imagem tem boa resolução. O destaque em vermelho é a informação mais relevante e deve ser preservado nas referências."
}
```

---

## Pipeline de Processamento Sugerido

```python
# Pseudo-código
for imagem in extrair_imagens_pdf(pdf_path):
    # 1. Tesseract OCR para texto bruto (fallback se Vision LLM falhar em algum trecho)
    ocr_baseline = tesseract.image_to_string(imagem, lang='por')

    # 2. Vision LLM com prompt acima
    descricao_json = call_vision_llm(
        imagem,
        contexto={
            "faq_titulo": faq.titulo,
            "faq_id": faq.id,
            "secao_atual": secao_proxima,
            "contexto_textual_proximo": texto_ao_redor_da_imagem
        }
    )

    # 3. Validar e mergear OCR baseline se Vision LLM não capturou tudo
    if len(descricao_json['ocr_texto_completo']) < len(ocr_baseline) * 0.7:
        descricao_json['ocr_texto_completo_complementar'] = ocr_baseline

    # 4. Calcular embedding do texto enriquecido (descricao + ocr + palavras-chave)
    texto_para_embedding = f"""
    {descricao_json['descricao_vision_llm']}
    Texto da tela: {descricao_json['ocr_texto_completo']}
    Palavras-chave: {' '.join(descricao_json['palavras_chave_exatas'])}
    Quando enviar: {' '.join(descricao_json['quando_enviar'])}
    """
    embedding = bge_m3.encode(texto_para_embedding)

    # 5. Salvar imagem em object storage
    storage_url = upload_para_oracle_storage(imagem)

    # 6. Inserir no Qdrant
    qdrant.upsert(
        collection_name="sci_faq_ecd_ecf",
        points=[{
            "id": f"img_{faq.id}_{imagem.index}",
            "vector": {"dense": embedding.dense, "sparse": embedding.sparse, "colbert": embedding.colbert},
            "payload": {**descricao_json, "tipo_chunk": "imagem", "storage_url": storage_url, "faq_id": faq.id}
        }]
    )
```

---

## Custo Estimado

- **Claude Sonnet 4.5 Vision**: ~$0.015 por imagem (alta qualidade)
- **DeepSeek-VL2**: ~$0.002 por imagem (custo-benefício)
- **GPT-4o**: ~$0.01 por imagem

Para 22 FAQs com ~50 imagens (média do PDF anexo): **$0.10 a $0.75** rodando uma vez só.

Para o catálogo completo (estimado 500 FAQs × ~3 imagens/FAQ = 1500 imagens): **$3 a $22**.
