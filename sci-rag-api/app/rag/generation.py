"""LLM generation step: picks a model, builds the prompt, parses + validates JSON."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import orjson

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.llm_clients import (
    LLMResponse,
    call_claude,
    call_deepseek,
    estimate_cost_usd,
    parse_json_or_raise,
)
from app.rag.prompts import load_prompt
from app.rag.retrieval import RetrievedChunk
from app.schemas.common import MensagemHistorico
from app.schemas.query import ClienteContexto

logger = get_logger(__name__)


@dataclass(slots=True)
class GenerationResult:
    parsed: dict[str, Any]
    raw_text: str
    model_used: str
    tokens_input: int
    tokens_output: int
    custo_usd: float
    duracao_ms: int


def _pick_model(
    requested: str,
    top_score: float,
    pergunta_len: int,
    historico_len: int,
) -> str:
    """deepseek for short/simple/high-confidence queries; claude otherwise."""
    settings = get_settings()
    if requested and requested != "auto":
        return requested
    if not settings.anthropic_api_key:
        return "deepseek"
    if not settings.deepseek_api_key:
        return "claude"
    if pergunta_len > 350 or historico_len > 8 or top_score < settings.grey_zone_high:
        return "claude"
    return "deepseek"


def _format_history(historico: list[MensagemHistorico], max_turns: int = 8) -> str:
    recent = historico[-max_turns:]
    if not recent:
        return "(sem historico)"
    return "\n".join(f"[{m.role}] {m.content}" for m in recent)


def _format_cliente(cliente: ClienteContexto) -> str:
    return (
        f"nome={cliente.nome or '-'} | empresa={cliente.empresa or '-'} | "
        f"licenca_sci={cliente.licenca_sci or '-'} | meses={cliente.tempo_relacionamento_meses or '-'}"
    )


def _serialize_chunk(chunk: RetrievedChunk) -> dict[str, Any]:
    p = chunk.payload
    if p.get("tipo_chunk") == "imagem":
        return {
            "chunk_id": chunk.chunk_id,
            "tipo": "imagem",
            "faq_id": p.get("faq_id"),
            "image_asset_id": p.get("image_asset_id"),
            "titulo_janela": p.get("titulo_janela"),
            "menu_caminho_inferido": p.get("menu_caminho_inferido"),
            "descricao_vision_llm": p.get("descricao_vision_llm"),
            "quando_enviar": p.get("quando_enviar") or [],
            "registros_sped_visiveis": p.get("registros_sped_visiveis") or [],
            "score": chunk.score,
            "rerank_score": chunk.rerank_score,
        }
    return {
        "chunk_id": chunk.chunk_id,
        "tipo": "texto",
        "faq_id": p.get("faq_id"),
        "faq_titulo": p.get("faq_titulo"),
        "categoria_principal": p.get("categoria_principal"),
        "chunk_tipo": p.get("chunk_tipo"),
        "titulo_secao": p.get("titulo_secao"),
        "texto_original": (p.get("texto_original") or "")[:2000],
        "menus_caminhos": p.get("menus_caminhos") or [],
        "campos_interface": p.get("campos_interface") or [],
        "registros_sped_mencionados": p.get("registros_sped_mencionados") or [],
        "palavras_chave_exatas": p.get("palavras_chave_exatas") or [],
        "score": chunk.score,
        "rerank_score": chunk.rerank_score,
    }


async def generate(
    *,
    pergunta: str,
    cliente: ClienteContexto,
    historico: list[MensagemHistorico],
    chunks: list[RetrievedChunk],
    queries_reescritas: list[str],
    requested_model: str,
    top_score: float,
) -> GenerationResult:
    template = load_prompt("agente_producao.txt")
    chunks_serialized = [_serialize_chunk(c) for c in chunks]
    chunks_json = orjson.dumps(chunks_serialized, option=orjson.OPT_INDENT_2).decode("utf-8")
    historico_str = _format_history(historico)

    user_content = template.format(
        contexto_cliente=_format_cliente(cliente),
        historico=historico_str,
        pergunta=pergunta,
        queries_reescritas=", ".join(queries_reescritas) or pergunta,
        chunks_json=chunks_json,
    )

    system_msg = (
        "Voce e um agente virtual de suporte tecnico SCI Contabil. "
        "Sempre responda em JSON valido. Nao invente conteudo fora dos chunks."
    )

    backend = _pick_model(requested_model, top_score, len(pergunta), len(historico))
    started = asyncio.get_event_loop().time()

    try:
        if backend == "claude":
            llm_resp: LLMResponse = await call_claude(
                system=system_msg,
                user=user_content,
                temperature=0.0,
                max_tokens=1800,
                json_mode=True,
            )
        else:
            llm_resp = await call_deepseek(
                system=system_msg,
                user=user_content,
                temperature=0.0,
                max_tokens=1800,
                json_mode=True,
            )
    except Exception as exc:
        logger.warning("primary_llm_failed_falling_back", error=str(exc), backend=backend)
        # Failover: try the other provider.
        if backend == "claude":
            llm_resp = await call_deepseek(
                system=system_msg, user=user_content, temperature=0.0, max_tokens=1800, json_mode=True
            )
        else:
            llm_resp = await call_claude(
                system=system_msg, user=user_content, temperature=0.0, max_tokens=1800, json_mode=True
            )

    duration_ms = int((asyncio.get_event_loop().time() - started) * 1000)

    parsed = await _parse_with_retry(
        llm_resp.text,
        system_msg=system_msg,
        user_content=user_content,
        original=llm_resp,
    )

    custo = estimate_cost_usd(llm_resp.model, llm_resp.tokens_input, llm_resp.tokens_output)

    return GenerationResult(
        parsed=parsed,
        raw_text=llm_resp.text,
        model_used=llm_resp.model,
        tokens_input=llm_resp.tokens_input,
        tokens_output=llm_resp.tokens_output,
        custo_usd=custo,
        duracao_ms=duration_ms,
    )


async def _parse_with_retry(
    text: str,
    *,
    system_msg: str,
    user_content: str,
    original: LLMResponse,
    max_retries: int = 1,
) -> dict[str, Any]:
    try:
        return parse_json_or_raise(text)
    except Exception:
        if max_retries <= 0:
            raise
    # Retry once asking the LLM to fix the JSON.
    fix_user = (
        "A resposta abaixo nao e um JSON valido. Reformate ESTRITAMENTE como JSON valido "
        "seguindo o mesmo schema. Nao adicione texto fora do JSON.\n\n"
        f"RESPOSTA INVALIDA:\n{text}"
    )
    try:
        fix = await call_deepseek(
            system=system_msg, user=fix_user, temperature=0.0, max_tokens=1800, json_mode=True
        )
        return parse_json_or_raise(fix.text)
    except Exception as exc:
        logger.warning("json_repair_failed", error=str(exc))
        raise
