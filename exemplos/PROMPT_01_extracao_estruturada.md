# PROMPT 01 — Extração Estruturada de FAQs do PDF

**Uso:** Após Docling extrair o texto bruto do PDF, este prompt é enviado ao LLM (DeepSeek V4 Pro ou Claude Sonnet 4.5) para transformar cada bloco de FAQ em JSON estruturado.

**Modelo recomendado:** Claude Sonnet 4.5 (melhor estrutura) ou DeepSeek V4 Pro (custo).

**Temperatura:** 0.0 (determinístico).

---

## SYSTEM PROMPT

```
Você é um extrator especializado em documentação técnica do sistema SCI Contábil
brasileiro. Sua única função é converter blocos de FAQ em JSON rigorosamente
estruturado, sem inventar nenhuma informação.

REGRAS INVIOLÁVEIS:

1. NUNCA invente faq_id, datas, caminhos de menu ou nomes de campos. Se a
   informação não está literalmente no texto, use null.

2. NUNCA traduza, parafraseie ou "melhore" o texto original. Os campos textuais
   devem preservar a redação literal da fonte (incluindo "Sped", "K300/K315", etc.).

3. Os campos "texto_enriquecido_para_embedding" e "perguntas_exemplo" SÃO
   gerados por você — esses são os ÚNICOS campos onde você adiciona conteúdo.

4. Saída sempre em JSON válido, sem markdown, sem comentários, sem ```json```.
   Apenas o objeto JSON puro começando com { e terminando com }.

5. Para identificar tipo de chunk, use estas categorias EXATAS:
   - "introducao": parágrafo inicial explicando o que o FAQ resolve
   - "procedimento": passo-a-passo de como fazer
   - "calculo_regra": explicação de cálculo ou regra de negócio
   - "configuracao": configuração de tela/campo
   - "exemplo": exemplo concreto (incluindo trechos de arquivo SPED)
   - "observacao_importante": avisos, restrições, "atenção"
   - "referencia_cruzada": menção a outros FAQs ou manuais

6. Para registros SPED, capture TODOS os mencionados, no formato literal:
   K300, K310, K315, I012, I015, I030, I050, I051, I150, I155, I157, J005,
   J100, J150, C155, etc.

7. Para menus, capture o caminho COMPLETO no formato "Menu > Submenu > Item".

8. Se houver palavras como "esmaecido", "habilitado", "obrigatório", "destacado",
   capture-as nas palavras-chave exatas — elas são vocabulário do cliente.
```

---

## USER PROMPT (template)

```
Extraia o seguinte bloco de FAQ do sistema SCI Contábil para JSON estruturado
seguindo o schema abaixo.

=== BLOCO DE FAQ BRUTO ===
{faq_bloco_extraido_do_pdf}
=== FIM DO BLOCO ===

=== IMAGENS DESCRITAS (já processadas por Vision LLM) ===
{lista_de_descricoes_de_imagens_com_seus_ids}
=== FIM DAS IMAGENS ===

=== SCHEMA DE SAÍDA ===
{
  "faq_id": "string (ex: '7085')",
  "faq_titulo": "string (título literal do FAQ)",
  "categoria_principal": "string",
  "categorias_secundarias": ["array de strings"],
  "data_cadastro": "string ISO 8601 ou null",
  "data_atualizacao": "string ISO 8601 ou null",
  "url_original": "string ou null",

  "chunks": [
    {
      "chunk_index": "integer (começa em 0)",
      "chunk_tipo": "um dos 7 tipos definidos no system prompt",
      "titulo_secao": "string ou null",
      "texto_original": "string LITERAL do PDF",
      "texto_enriquecido_para_embedding": "versão expandida do texto com sinônimos contextuais, palavras-chave repetidas, e contexto técnico explicitado — esta versão é o que será vetorizada. Mínimo 50 palavras, máximo 250.",
      "registros_sped_mencionados": ["array de códigos exatos"],
      "menus_caminhos": ["array de caminhos completos"],
      "campos_interface": [
        {
          "nome": "string (nome literal do campo na tela)",
          "tipo": "checkbox|select|input|radio|button|datepicker",
          "obrigatorio": "boolean ou null",
          "depende_de": "string ou null (outro campo)"
        }
      ],
      "palavras_chave_exatas": ["array — incluir TODOS os termos técnicos literais"],
      "intencoes_atendidas": ["array de intenções do usuário que este chunk resolve"],
      "perguntas_exemplo": [
        "array de 3 a 5 perguntas que um cliente real faria que este chunk resolve. Use linguagem coloquial brasileira de WhatsApp."
      ],
      "imagens_associadas": ["array de ids das imagens — apenas as efetivamente referenciadas neste chunk"]
    }
  ]
}
=== FIM DO SCHEMA ===

Retorne APENAS o JSON. Nenhum texto antes ou depois.
```

---

## Exemplo de Saída Esperada (entrada = FAQ 7085 do PDF anexo)

```json
{
  "faq_id": "7085",
  "faq_titulo": "Como realizar a emissão do Balanço patrimonial considerando as eliminações do K300/K315?",
  "categoria_principal": "Relatórios NV/Único Fiscal e Contábil",
  "categorias_secundarias": [],
  "data_cadastro": "2026-05-11T14:37:00",
  "data_atualizacao": "2026-05-12T14:41:00",
  "url_original": "modulo/faq/faq.php?faqId=7085&sistemaId=54",
  "chunks": [
    {
      "chunk_index": 0,
      "chunk_tipo": "introducao",
      "titulo_secao": null,
      "texto_original": "O sistema permite a geração do Balanço patrimonial aplicando as eliminações de participações societárias entre empresas do mesmo grupo econômico, registradas nos lançamentos do Bloco K (K300/K310/K315). O sistema utiliza os lançamentos realizados no menu de Lançamentos > Sped - lançamentos do K310/K315 para abater esses valores dos saldos consolidados das contas contábeis.",
      "texto_enriquecido_para_embedding": "FAQ 7085 introdução: o sistema SCI Contábil permite emitir o relatório de Balanço Patrimonial aplicando eliminações de participações societárias entre empresas do mesmo grupo econômico (também chamado de conglomerado ou consolidação contábil). Essas eliminações são registradas nos lançamentos do Bloco K do SPED, especificamente nos registros K300, K310 e K315. O sistema consulta os lançamentos feitos no menu Lançamentos > Sped - lançamentos do K310/K315 para abater (subtrair) esses valores dos saldos consolidados das contas contábeis. Aplicável quando há grupo econômico configurado e necessidade de consolidação.",
      "registros_sped_mencionados": ["K300", "K310", "K315"],
      "menus_caminhos": ["Lançamentos > Sped - lançamentos do K310/K315"],
      "campos_interface": [],
      "palavras_chave_exatas": [
        "balanço patrimonial",
        "eliminações",
        "participações societárias",
        "grupo econômico",
        "Bloco K",
        "K300",
        "K310",
        "K315",
        "saldos consolidados",
        "contas contábeis",
        "consolidação"
      ],
      "intencoes_atendidas": [
        "entender o que são eliminações no balanço patrimonial",
        "saber se o sistema suporta consolidação de grupo econômico",
        "compreender o papel dos registros K300/K310/K315"
      ],
      "perguntas_exemplo": [
        "O sistema faz balanço consolidado de grupo econômico?",
        "Como funciona a eliminação do Bloco K no balanço?",
        "Pra que serve o K300 K310 K315 no balanço?",
        "Onde o sistema busca as eliminações para o balanço?"
      ],
      "imagens_associadas": []
    },
    {
      "chunk_index": 1,
      "chunk_tipo": "procedimento",
      "titulo_secao": "Como emitir o relatório com as eliminações",
      "texto_original": "Acesse o menu Relatórios > Balanço patrimonial. Selecione um Grupo econômico e marque a opção Considerar as eliminações do K300/K315. A opção Considerar as eliminações do K300/K315 fica disponível para seleção somente se um Grupo econômico estiver selecionado. Caso contrário, o campo permanecerá esmaecido.",
      "texto_enriquecido_para_embedding": "FAQ 7085 procedimento: para emitir o Balanço Patrimonial com eliminações do Bloco K no sistema SCI Contábil, siga o caminho: Relatórios > Balanço patrimonial. Na tela do relatório, selecione um Grupo econômico no campo correspondente. Após selecionar o grupo, marque a opção (checkbox) 'Considerar as eliminações do K300/K315'. Importante: este checkbox só fica habilitado (clicável) se houver um Grupo econômico previamente selecionado. Sem grupo selecionado, a opção aparece esmaecida (acinzentada, desabilitada, não clicável). Esse comportamento garante que as eliminações só sejam aplicadas em contexto de consolidação.",
      "registros_sped_mencionados": ["K300", "K315"],
      "menus_caminhos": ["Relatórios > Balanço patrimonial"],
      "campos_interface": [
        {
          "nome": "Grupo econômico",
          "tipo": "select",
          "obrigatorio": true,
          "depende_de": null
        },
        {
          "nome": "Considerar as eliminações do K300/K315",
          "tipo": "checkbox",
          "obrigatorio": false,
          "depende_de": "Grupo econômico"
        }
      ],
      "palavras_chave_exatas": [
        "balanço patrimonial",
        "eliminações",
        "K300/K315",
        "Grupo econômico",
        "esmaecido",
        "marcar opção",
        "checkbox",
        "habilitado",
        "Relatórios"
      ],
      "intencoes_atendidas": [
        "passo a passo para emitir balanço com eliminações",
        "onde marcar a opção de considerar eliminações",
        "por que a opção K300/K315 está esmaecida",
        "como habilitar a opção de eliminações no balanço"
      ],
      "perguntas_exemplo": [
        "Como eu marco para considerar as eliminações no balanço?",
        "Por que o campo K300/K315 está cinza no balanço?",
        "Onde acho a opção de eliminação no balanço patrimonial?",
        "Tô tentando marcar eliminações K300 mas não deixa, o que faço?",
        "Qual o caminho pro balanço com grupo econômico?"
      ],
      "imagens_associadas": ["img_faq_7085_01"]
    },
    {
      "chunk_index": 2,
      "chunk_tipo": "calculo_regra",
      "titulo_secao": "Cálculo da Eliminação",
      "texto_original": "Será a soma de todas as chaves lançadas no registro K310 para uma determinada conta informada no K300. Esse total é subtraído do saldo consolidado da conta. Saldo Anterior: considera lançamentos no K300 anteriores à data de emissão/exportação. Movimento e Saldo Atual: considera lançamentos no K300 dentro do período da escrituração. Quando utilizado plano de fórmulas, a exclusão do valor será aplicada diretamente na linha da demonstração em que consta a conta que recebeu lançamentos do K300. Caso seja uma conta de Participante, a eliminação será aplicada especificamente ao participante informado no lançamento do K300.",
      "texto_enriquecido_para_embedding": "FAQ 7085 cálculo da eliminação no balanço patrimonial: o valor eliminado é a soma de todas as chaves lançadas no registro K310 para uma conta informada no K300. Esse total é subtraído (abatido) do saldo consolidado da conta. Regras temporais: Saldo Anterior considera lançamentos K300 anteriores à data de emissão ou exportação; Movimento e Saldo Atual considera lançamentos K300 dentro do período da escrituração contábil. Comportamento especial: se houver plano de fórmulas configurado, a exclusão é aplicada diretamente na linha da demonstração contábil onde está a conta com lançamentos do K300. Para contas do tipo Participante, a eliminação é específica ao participante informado no K300, não global. Lógica fundamental para conferência de saldos consolidados.",
      "registros_sped_mencionados": ["K300", "K310"],
      "menus_caminhos": [],
      "campos_interface": [],
      "palavras_chave_exatas": [
        "cálculo da eliminação",
        "soma das chaves",
        "K310",
        "K300",
        "saldo consolidado",
        "Saldo Anterior",
        "Movimento",
        "Saldo Atual",
        "plano de fórmulas",
        "linha da demonstração",
        "conta de Participante",
        "participante informado",
        "período da escrituração",
        "data de emissão",
        "exportação"
      ],
      "intencoes_atendidas": [
        "entender como o sistema calcula a eliminação",
        "saber qual valor será abatido",
        "diferenciar saldo anterior de saldo atual no cálculo",
        "como funciona eliminação com conta de participante",
        "comportamento com plano de fórmulas"
      ],
      "perguntas_exemplo": [
        "Como o sistema calcula o valor eliminado no balanço?",
        "Qual a diferença entre saldo anterior e movimento na eliminação?",
        "Se eu usar plano de fórmulas a eliminação muda?",
        "Como funciona eliminação por participante?",
        "De onde sai o valor que vai ser abatido do saldo?"
      ],
      "imagens_associadas": ["img_faq_7085_02", "img_faq_7085_03"]
    }
  ]
}
```

---

## Dicas de Tuning

1. **Sempre processe um FAQ por vez** — não tente mandar o PDF inteiro de uma vez. Custo e qualidade caem.

2. **Valide o JSON com Pydantic** após cada chamada — se vier inválido, retry com `temperature=0.0` e prompt adicional "Sua última resposta tinha JSON inválido em X. Corrija.".

3. **Para `texto_enriquecido_para_embedding`**: este é o segredo da precisão. Ele deve:
   - Repetir palavras-chave importantes (boost natural no embedding)
   - Adicionar sinônimos brasileiros ("esmaecido = acinzentado = desabilitado")
   - Explicitar o contexto que está implícito no texto original
   - Nunca contradizer o texto original

4. **Para `perguntas_exemplo`**: use linguagem real de WhatsApp. "Cliente: como eu faço X?" funciona melhor que "Como realizar X?".

5. **Custo estimado** (Claude Sonnet 4.5): ~$0.02 por FAQ extraído. 500 FAQs = ~$10 total. Rodando uma vez só, vale muito.
