"""Query rewriting using DeepSeek for low-cost variant generation."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.llm_clients import call_deepseek, parse_json_or_raise
from app.rag.prompts import render_prompt
from app.schemas.common import MensagemHistorico

logger = get_logger(__name__)


@dataclass(slots=True)
class RewriteResult:
    variantes: list[str] = field(default_factory=list)
    intencao_provavel: str | None = None
    termos_chave: list[str] = field(default_factory=list)


def _format_history(historico: list[MensagemHistorico], max_turns: int = 20) -> str:
    recent = historico[-max_turns:]
    if not recent:
        return "(sem historico recente)"
    return "\n".join(f"[{m.role}] {m.content}" for m in recent)


async def rewrite_query(pergunta: str, historico: list[MensagemHistorico]) -> RewriteResult:
    settings = get_settings()
    if not settings.deepseek_api_key:
        # Fall back to the original question only — no fancy expansion without an LLM.
        return RewriteResult(
            variantes=[pergunta], intencao_provavel=None, termos_chave=_naive_terms(pergunta)
        )

    prompt = render_prompt(
        "query_rewriter.txt",
        historico=_format_history(historico),
        pergunta=pergunta,
    )

    try:
        response = await call_deepseek(
            system="Voce e um motor de query rewriting. Retorne apenas JSON.",
            user=prompt,
            max_tokens=600,
            temperature=0.0,
            json_mode=True,
        )
        data = parse_json_or_raise(response.text)
    except Exception as exc:
        logger.warning("query_rewrite_failed", error=str(exc))
        return RewriteResult(variantes=[pergunta], intencao_provavel=None, termos_chave=_naive_terms(pergunta))

    variantes = [v.strip() for v in data.get("variantes", []) if isinstance(v, str) and v.strip()]
    if pergunta not in variantes:
        variantes.insert(0, pergunta)
    variantes = variantes[: settings.query_rewrite_variants]

    return RewriteResult(
        variantes=variantes,
        intencao_provavel=data.get("intencao_provavel"),
        termos_chave=[t for t in data.get("termos_chave", []) if isinstance(t, str)],
    )


def _naive_terms(text: str) -> list[str]:
    return [tok for tok in text.split() if len(tok) > 3][:8]
