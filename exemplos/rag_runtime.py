"""
Runtime de consulta RAG — chamado a cada mensagem do cliente no SCI.

Pipeline:
    1. Recebe mensagem do cliente
    2. (Opcional) Query rewriting via LLM pequeno
    3. Gera embeddings da query (dense + sparse + colbert)
    4. Busca híbrida no Qdrant com RRF fusion
    5. Reranking ColBERT
    6. Monta contexto e chama LLM principal com PROMPT_03
    7. Aplica guardrails
    8. Retorna JSON estruturado para o orquestrador SCI enviar via Evolution API

Uso (como módulo Django):
    from rag_engine.runtime import responder_mensagem
    resultado = responder_mensagem(mensagem, contexto_cliente)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchAny,
    MatchValue,
    SparseVector,
    Prefetch,
    FusionQuery,
    Fusion,
    NamedVector,
    NamedSparseVector,
)

from anthropic import Anthropic
from openai import OpenAI  # DeepSeek

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CONFIG (idealmente lido de Django settings)
# ═══════════════════════════════════════════════════════════════════

QDRANT_URL = "http://qdrant:6333"
COLLECTION = "sci_faq_ecd_ecf"

THRESHOLDS = {
    "min_score_top_chunk": 0.65,
    "min_confianca_resposta": 0.70,
    "min_score_imagem_envio": 0.75,
    "max_chunks_no_contexto": 5,
}

PROMPT_AGENTE = Path("prompts/PROMPT_03_agente_rag_producao.md").read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ContextoCliente:
    nome: str
    empresa: str
    licenca: str
    tempo_cliente: str
    historico_recente: list[dict]  # [{role: 'user'|'assistant', content: str}, ...]
    departamento_atual: str | None = None


@dataclass
class ChunkRecuperado:
    point_id: str
    score: float
    payload: dict


@dataclass
class RespostaRAG:
    acao: str  # RESPONDER | TRANSFERIR_HUMANO | PEDIR_CLARIFICACAO
    confianca: float
    mensagens: list[str]
    imagens_a_enviar: list[dict]
    faqs_consultados: list[str]
    intencao_detectada: str
    departamento_sugerido: str | None
    necessita_followup: bool
    debug: dict


# ═══════════════════════════════════════════════════════════════════
# EMBEDDING (carregamento lazy, singleton)
# ═══════════════════════════════════════════════════════════════════

_embedding_model: BGEM3FlagModel | None = None


def get_embedding_model() -> BGEM3FlagModel:
    global _embedding_model
    if _embedding_model is None:
        log.info("Carregando BGE-M3...")
        _embedding_model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)
    return _embedding_model


def encode_query(texto: str) -> dict:
    model = get_embedding_model()
    out = model.encode(
        [texto],
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=True,
        max_length=512,  # queries são curtas
    )
    return {
        "dense": out["dense_vecs"][0].tolist(),
        "sparse_indices": list(out["lexical_weights"][0].keys()),
        "sparse_values": list(out["lexical_weights"][0].values()),
        "colbert": out["colbert_vecs"][0].tolist(),
    }


# ═══════════════════════════════════════════════════════════════════
# QUERY REWRITER (opcional mas melhora muito o recall)
# ═══════════════════════════════════════════════════════════════════

def reescrever_query(query_original: str, historico: list[dict], llm_client: OpenAI) -> list[str]:
    """
    Gera 2-3 variantes da query para aumentar recall.
    Usa DeepSeek (custo baixo, latência baixa).
    """
    historico_str = "\n".join([f"{m['role']}: {m['content']}" for m in historico[-3:]])

    prompt = f"""Você é um reescritor de queries para um sistema RAG sobre o software SCI Contábil.

A pergunta do cliente pode usar gírias, abreviações, ou referenciar conversas anteriores. Sua função é
gerar 3 variantes técnicas e completas da pergunta, otimizadas para busca semântica em uma base de FAQs.

Cada variante deve:
- Expandir abreviações (ex: "BP" -> "Balanço Patrimonial")
- Adicionar contexto técnico relevante (ex: "K300" sozinho -> "registro K300 do Bloco K SPED ECD")
- Resolver referências anafóricas (ex: "esse erro" usando histórico)
- Ser uma frase completa, não pergunta

Histórico recente da conversa:
{historico_str}

Pergunta original do cliente:
"{query_original}"

Retorne APENAS um JSON com formato:
{{"variantes": ["variante 1", "variante 2", "variante 3"]}}
"""

    response = llm_client.chat.completions.create(
        model="deepseek-chat",  # ajustar para deepseek-v4-pro quando disponível
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=300,
    )
    parsed = json.loads(response.choices[0].message.content)
    return [query_original] + parsed.get("variantes", [])


# ═══════════════════════════════════════════════════════════════════
# BUSCA HÍBRIDA
# ═══════════════════════════════════════════════════════════════════

def buscar_hibrido(queries: list[str], qdrant: QdrantClient, categoria_filter: list[str] | None = None) -> list[ChunkRecuperado]:
    """
    Busca híbrida: dense + sparse, fundido com Reciprocal Rank Fusion (RRF).
    Cada query reescrita contribui — chunks que aparecem em múltiplas queries sobem.
    """
    all_results: dict[str, ChunkRecuperado] = {}

    payload_filter = None
    if categoria_filter:
        payload_filter = Filter(must=[
            FieldCondition(key="categoria_principal", match=MatchAny(any=categoria_filter))
        ])

    for q in queries:
        embs = encode_query(q)

        # Busca híbrida com prefetch + RRF
        result = qdrant.query_points(
            collection_name=COLLECTION,
            prefetch=[
                Prefetch(
                    query=embs["dense"],
                    using="dense",
                    limit=20,
                    filter=payload_filter
                ),
                Prefetch(
                    query=SparseVector(
                        indices=embs["sparse_indices"],
                        values=embs["sparse_values"]
                    ),
                    using="sparse",
                    limit=20,
                    filter=payload_filter
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=15,
            with_payload=True,
        )

        for hit in result.points:
            existing = all_results.get(str(hit.id))
            if existing is None or hit.score > existing.score:
                all_results[str(hit.id)] = ChunkRecuperado(
                    point_id=str(hit.id),
                    score=hit.score,
                    payload=hit.payload
                )

    # Ordena por score
    return sorted(all_results.values(), key=lambda c: c.score, reverse=True)


def rerank_colbert(query: str, candidatos: list[ChunkRecuperado], qdrant: QdrantClient, top_k: int = 5) -> list[ChunkRecuperado]:
    """
    Reranking final com ColBERT (late interaction).
    Mais caro mas muito mais preciso que dense puro.
    """
    embs = encode_query(query)

    # Busca apenas no subconjunto de IDs candidatos
    ids_candidatos = [c.point_id for c in candidatos[:30]]

    if not ids_candidatos:
        return []

    result = qdrant.query_points(
        collection_name=COLLECTION,
        query=embs["colbert"],
        using="colbert",
        limit=top_k,
        with_payload=True,
        query_filter=Filter(must=[
            FieldCondition(key="point_id", match=MatchAny(any=ids_candidatos))  # ajustar para uso real
        ]) if False else None,  # placeholder — Qdrant não filtra por point_id assim diretamente; ajuste em produção
    )

    return [
        ChunkRecuperado(point_id=str(h.id), score=h.score, payload=h.payload)
        for h in result.points
    ]


# ═══════════════════════════════════════════════════════════════════
# MONTAGEM DE CONTEXTO PARA O LLM
# ═══════════════════════════════════════════════════════════════════

def separar_chunks_e_imagens(chunks: list[ChunkRecuperado]) -> tuple[list[ChunkRecuperado], list[ChunkRecuperado]]:
    texto = [c for c in chunks if c.payload.get("tipo_chunk") == "texto"]
    imagens = [c for c in chunks if c.payload.get("tipo_chunk") == "imagem"]
    return texto, imagens


def montar_user_prompt(query: str, ctx: ContextoCliente, chunks_texto: list[ChunkRecuperado], chunks_imagem: list[ChunkRecuperado]) -> str:
    historico_str = "\n".join([f"{m['role']}: {m['content']}" for m in ctx.historico_recente[-5:]])

    chunks_str_parts = []
    for i, c in enumerate(chunks_texto[:THRESHOLDS["max_chunks_no_contexto"]], start=1):
        p = c.payload
        chunks_str_parts.append(
            f"--- CHUNK {i} (score: {c.score:.3f}, faq_id: {p.get('faq_id')}) ---\n"
            f"Tipo: {p.get('chunk_tipo')}\n"
            f"FAQ: {p.get('faq_titulo')}\n"
            f"Seção: {p.get('titulo_secao')}\n"
            f"Conteúdo:\n{p.get('texto_original')}\n\n"
            f"Menus mencionados: {p.get('menus_caminhos')}\n"
            f"Registros SPED: {p.get('registros_sped_mencionados')}\n"
            f"Imagens disponíveis para este chunk: {p.get('imagens_associadas')}\n"
        )

    imagens_str_parts = []
    for c in chunks_imagem:
        if c.score < THRESHOLDS["min_score_imagem_envio"]:
            continue
        p = c.payload
        imagens_str_parts.append(
            f"Imagem {c.point_id}:\n"
            f"  Tela: {p.get('tipo_tela')}\n"
            f"  Descrição: {p.get('descricao_vision_llm', '')[:300]}\n"
            f"  Quando enviar: {p.get('quando_enviar')}\n"
        )

    return f"""═══ HISTÓRICO RECENTE DA CONVERSA ═══
{historico_str}

═══ MENSAGEM ATUAL DO CLIENTE ═══
{query}

═══ PERFIL DO CLIENTE ═══
Nome: {ctx.nome}
Empresa: {ctx.empresa}
Tipo de licença SCI: {ctx.licenca}
Já é cliente há: {ctx.tempo_cliente}

═══ BASE_DE_CONHECIMENTO ═══

{chr(10).join(chunks_str_parts)}

--- IMAGENS RECUPERADAS ---
{chr(10).join(imagens_str_parts) if imagens_str_parts else "(nenhuma imagem com score suficiente)"}

═══ INSTRUÇÃO FINAL ═══

Com base APENAS na BASE_DE_CONHECIMENTO acima e no histórico da conversa,
responda à mensagem atual do cliente seguindo o schema JSON definido no system prompt.

Lembre-se: se a base não cobre a pergunta, responda com acao "TRANSFERIR_HUMANO".
"""


def extrair_system_prompt() -> str:
    m = re.search(r'## SYSTEM PROMPT\s*\n+```\s*\n(.*?)```', PROMPT_AGENTE, re.DOTALL)
    return m.group(1).strip() if m else ""


# ═══════════════════════════════════════════════════════════════════
# GERAÇÃO COM LLM PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

def gerar_resposta(user_prompt: str, modelo_principal: str, anthropic: Anthropic | None, deepseek: OpenAI | None) -> dict:
    system = extrair_system_prompt().replace("{NOME_EMPRESA}", "BDM Contabilidade")  # ajustar

    if modelo_principal.startswith("claude"):
        resp = anthropic.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            temperature=0.0,
            system=system,
            messages=[{"role": "user", "content": user_prompt}]
        )
        return json.loads(resp.content[0].text)
    else:
        resp = deepseek.chat.completions.create(
            model="deepseek-chat",  # trocar por v4-pro
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=1024,
        )
        return json.loads(resp.choices[0].message.content)


# ═══════════════════════════════════════════════════════════════════
# GUARDRAILS
# ═══════════════════════════════════════════════════════════════════

def aplicar_guardrails(resposta: dict, chunks: list[ChunkRecuperado]) -> dict:
    """Camada extra de segurança."""
    top_score = chunks[0].score if chunks else 0.0

    # Guardrail 1: score do top chunk muito baixo → força transbordo
    if top_score < THRESHOLDS["min_score_top_chunk"]:
        log.warning(f"Top score {top_score:.3f} < threshold; forçando transbordo")
        return {
            **resposta,
            "acao": "TRANSFERIR_HUMANO",
            "mensagens": ["Vou te transferir para um atendente humano que vai conseguir te ajudar melhor nesse caso. Só um momento."],
            "imagens_a_enviar": [],
        }

    # Guardrail 2: confiança da LLM muito baixa
    if resposta.get("confianca", 0) < THRESHOLDS["min_confianca_resposta"]:
        log.warning(f"Confiança {resposta.get('confianca')} < threshold; forçando transbordo")
        resposta["acao"] = "TRANSFERIR_HUMANO"
        resposta["mensagens"] = ["Vou te transferir para um atendente humano. Só um momento."]
        resposta["imagens_a_enviar"] = []

    # Guardrail 3: faqs_consultados deve estar nos IDs reais recuperados
    faqs_validos = {c.payload.get("faq_id") for c in chunks}
    consultados = resposta.get("faqs_consultados", [])
    invalidos = [f for f in consultados if f not in faqs_validos]
    if invalidos:
        log.error(f"LLM citou FAQs não recuperados: {invalidos} — possível alucinação")
        resposta["acao"] = "TRANSFERIR_HUMANO"
        resposta["mensagens"] = ["Vou te transferir para um atendente humano. Só um momento."]
        resposta["imagens_a_enviar"] = []

    return resposta


# ═══════════════════════════════════════════════════════════════════
# ORQUESTRADOR PÚBLICO
# ═══════════════════════════════════════════════════════════════════

def responder_mensagem(
    mensagem: str,
    ctx: ContextoCliente,
    anthropic: Anthropic | None = None,
    deepseek: OpenAI | None = None,
    qdrant: QdrantClient | None = None,
) -> RespostaRAG:

    qdrant = qdrant or QdrantClient(url=QDRANT_URL)
    deepseek = deepseek or OpenAI(api_key="...", base_url="https://api.deepseek.com")
    anthropic = anthropic or Anthropic()

    # 1. Reescreve query
    queries = reescrever_query(mensagem, ctx.historico_recente, deepseek)
    log.info(f"Queries reescritas: {queries}")

    # 2. Busca híbrida
    candidatos = buscar_hibrido(queries, qdrant)
    log.info(f"Recuperados {len(candidatos)} candidatos")

    # 3. Rerank ColBERT (opcional — ative quando estiver tunado)
    # finalists = rerank_colbert(mensagem, candidatos, qdrant, top_k=5)
    finalists = candidatos[:8]

    # 4. Separa texto vs imagem
    chunks_texto, chunks_imagem = separar_chunks_e_imagens(finalists)

    # 5. Decisão de modelo
    pergunta_simples = len(mensagem.split()) < 20 and (chunks_texto and chunks_texto[0].score > 0.85)
    modelo = "deepseek" if pergunta_simples else "claude-sonnet-4-5"

    # 6. Monta prompt e chama LLM
    user_prompt = montar_user_prompt(mensagem, ctx, chunks_texto, chunks_imagem)
    resposta_bruta = gerar_resposta(user_prompt, modelo, anthropic, deepseek)

    # 7. Guardrails
    resposta_final = aplicar_guardrails(resposta_bruta, finalists)

    return RespostaRAG(
        acao=resposta_final["acao"],
        confianca=resposta_final.get("confianca", 0.0),
        mensagens=resposta_final.get("mensagens", []),
        imagens_a_enviar=resposta_final.get("imagens_a_enviar", []),
        faqs_consultados=resposta_final.get("faqs_consultados", []),
        intencao_detectada=resposta_final.get("intencao_detectada", ""),
        departamento_sugerido=resposta_final.get("departamento_sugerido"),
        necessita_followup=resposta_final.get("necessita_followup", False),
        debug={
            "modelo_usado": modelo,
            "queries_reescritas": queries,
            "top_chunks_scores": [c.score for c in finalists[:5]],
            "top_chunks_faqs": [c.payload.get("faq_id") for c in finalists[:5]],
        }
    )
