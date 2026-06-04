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
from app.rag.prompts import render_prompt
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
    """Return the canonical backend ("deepseek" or "claude") to use for this query.

    Honours an explicit request, but only routes to a provider whose key is set —
    so a DeepSeek-only deployment never tries to call Claude.
    """
    settings = get_settings()
    has_claude = bool(settings.anthropic_api_key)
    has_deepseek = bool(settings.deepseek_api_key)

    # Explicit per-request preference (e.g. "deepseek-chat", "claude-sonnet-4-5").
    if requested and requested != "auto":
        if requested.startswith("claude"):
            return "claude" if has_claude else "deepseek"
        return "deepseek" if has_deepseek else "claude"

    # auto: only one provider configured -> use it.
    if not has_claude:
        return "deepseek"
    if not has_deepseek:
        return "claude"

    # Both configured: route by the operator's chosen primary, escalating the
    # harder/lower-confidence queries to Claude.
    if pergunta_len > 350 or historico_len > 8 or top_score < settings.grey_zone_high:
        return "claude"
    return settings.llm_primary_provider


async def _call_backend(backend: str, *, system: str, user: str) -> LLMResponse:
    if backend == "claude":
        return await call_claude(
            system=system, user=user, temperature=0.0, max_tokens=1800, json_mode=True
        )
    return await call_deepseek(
        system=system, user=user, temperature=0.0, max_tokens=1800, json_mode=True
    )


def _provider_configured(backend: str) -> bool:
    settings = get_settings()
    return bool(settings.anthropic_api_key) if backend == "claude" else bool(settings.deepseek_api_key)


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
    chunks_serialized = [_serialize_chunk(c) for c in chunks]
    chunks_json = orjson.dumps(chunks_serialized, option=orjson.OPT_INDENT_2).decode("utf-8")
    historico_str = _format_history(historico)

    user_content = render_prompt(
        "agente_producao.txt",
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
        llm_resp: LLMResponse = await _call_backend(backend, system=system_msg, user=user_content)
    except Exception as exc:
        # Fail over only to a provider that is actually configured. In a
        # DeepSeek-only deployment there is nothing to fall back to, so surface
        # the original error instead of crashing on an unconfigured Claude call.
        other = "deepseek" if backend == "claude" else "claude"
        if not _provider_configured(other):
            logger.error("primary_llm_failed_no_failover", error=str(exc), backend=backend)
            raise
        logger.warning(
            "primary_llm_failed_falling_back", error=str(exc), backend=backend, failover=other
        )
        llm_resp = await _call_backend(other, system=system_msg, user=user_content)

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
    # Repair with whichever provider is configured (DeepSeek preferred for cost).
    repair_backend = "deepseek" if _provider_configured("deepseek") else "claude"
    try:
        fix = await _call_backend(repair_backend, system=system_msg, user=fix_user)
        return parse_json_or_raise(fix.text)
    except Exception as exc:
        logger.warning("json_repair_failed", error=str(exc))
        raise
