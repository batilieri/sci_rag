"""Fail-closed guardrails applied before/after the LLM call.

Each rule returns a `GuardrailResult`. The first rule that triggers `force_transfer=True`
short-circuits the engine and produces a TRANSFERIR_HUMANO response.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.config import get_settings
from app.core.logging import get_logger
from app.schemas.common import MotivoTransbordo

if TYPE_CHECKING:
    from app.rag.retrieval import RetrievedChunk

logger = get_logger(__name__)


SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cpf", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
    ("cnpj", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("password_request", re.compile(r"\b(senha|password|credenciai|acesso\s+ao\s+sistema)\b", re.IGNORECASE)),
)

OUT_OF_SCOPE_TRIGGERS = (
    re.compile(r"\b(receita|culinaria|piada|musica|filme|jogo|namorada|namorado)\b", re.IGNORECASE),
    re.compile(r"\b(politic[ao]|eleic[aã]o|religi[aã]o)\b", re.IGNORECASE),
)

USER_FRUSTRATION_TRIGGERS = (
    re.compile(r"\b(atendente|humano|pessoa real|gerente|porra|merda|inutil|robo idiota)\b", re.IGNORECASE),
    re.compile(r"!{3,}"),
)

SPECIFIC_NUMBER_ANALYSIS = (
    re.compile(r"\b(analisa[r]?|me\s+ajuda\s+a\s+entender)\s+(esses?|essas?)\s+(numeros|valores|saldos|lan[cç]amentos)", re.IGNORECASE),
    re.compile(r"\bmeu(s)?\s+(balanco|balancos|relatorio|dado[s]?|saldo[s]?)\s+(de|do|da)\s+\d", re.IGNORECASE),
)


@dataclass(slots=True)
class GuardrailResult:
    name: str
    triggered: bool
    force_transfer: bool = False
    motivo: MotivoTransbordo | None = None
    mensagem_para_cliente: str | None = None
    departamento_sugerido: str | None = None
    confianca_resultante: float | None = None
    scrubbed_text: str | None = None
    details: dict[str, str] = field(default_factory=dict)


def scrub_pii(text: str) -> tuple[str, list[str]]:
    """Replace sensitive tokens with [REDACTED]. Returns (clean, list of categories matched)."""
    matched: list[str] = []
    clean = text
    for name, pattern in SENSITIVE_PATTERNS:
        if pattern.search(clean):
            matched.append(name)
            clean = pattern.sub("[REDACTED]", clean)
    return clean, matched


def detect_user_request_human(text: str) -> bool:
    return any(p.search(text) for p in USER_FRUSTRATION_TRIGGERS)


def detect_out_of_scope(text: str) -> bool:
    return any(p.search(text) for p in OUT_OF_SCOPE_TRIGGERS)


def detect_specific_data_analysis(text: str) -> bool:
    return any(p.search(text) for p in SPECIFIC_NUMBER_ANALYSIS)


# ---------- pre-LLM guardrails ----------


def check_pre_llm(text: str) -> list[GuardrailResult]:
    """Run all guardrails that can fire BEFORE we call the LLM."""
    results: list[GuardrailResult] = []

    cleaned, matched = scrub_pii(text)
    if matched:
        sensitive_only = {"cpf", "cnpj", "credit_card"}.issuperset(set(matched))
        password_only = matched == ["password_request"]
        if "password_request" in matched or "credit_card" in matched:
            results.append(
                GuardrailResult(
                    name="sensitive_data_request",
                    triggered=True,
                    force_transfer=True,
                    motivo=MotivoTransbordo.SENSITIVE_DATA_REQUEST,
                    departamento_sugerido="suporte_tecnico",
                    mensagem_para_cliente=(
                        "Por seguranca, nao consigo tratar de senhas, acessos ou dados sensiveis por aqui. "
                        "Vou te transferir para um atendente humano agora."
                    ),
                    confianca_resultante=0.0,
                    details={"categories": ",".join(matched)},
                )
            )
        elif sensitive_only:
            # CPF/CNPJ aparece — mask but don't transfer automatically.
            results.append(
                GuardrailResult(
                    name="pii_scrub_applied",
                    triggered=True,
                    scrubbed_text=cleaned,
                    motivo=MotivoTransbordo.PII_DETECTED,
                    details={"categories": ",".join(matched)},
                )
            )
        elif password_only:
            pass

    if detect_user_request_human(text):
        results.append(
            GuardrailResult(
                name="user_requested_human",
                triggered=True,
                force_transfer=True,
                motivo=MotivoTransbordo.USER_REQUESTED,
                departamento_sugerido="suporte_contabil",
                mensagem_para_cliente=(
                    "Sem problema. Vou te transferir para um atendente humano agora, so um momento."
                ),
                confianca_resultante=0.0,
            )
        )

    if detect_out_of_scope(text):
        results.append(
            GuardrailResult(
                name="out_of_scope",
                triggered=True,
                force_transfer=True,
                motivo=MotivoTransbordo.OUT_OF_SCOPE,
                departamento_sugerido="suporte_contabil",
                mensagem_para_cliente=(
                    "Esse tipo de assunto foge do que eu consigo te ajudar aqui. "
                    "Vou te transferir para um atendente humano."
                ),
                confianca_resultante=0.0,
            )
        )

    if detect_specific_data_analysis(text):
        results.append(
            GuardrailResult(
                name="specific_data_analysis",
                triggered=True,
                force_transfer=True,
                motivo=MotivoTransbordo.SENSITIVE_DATA_REQUEST,
                departamento_sugerido="suporte_contabil",
                mensagem_para_cliente=(
                    "Analise especifica de numeros, lancamentos ou saldos do seu balanco precisa de um "
                    "contador analista. Vou te transferir, ok?"
                ),
                confianca_resultante=0.0,
            )
        )

    return results


# ---------- retrieval-time guardrails ----------


def check_retrieval(
    top_score: float,
    chunks: list[RetrievedChunk],
) -> list[GuardrailResult]:
    settings = get_settings()
    results: list[GuardrailResult] = []
    if not chunks:
        results.append(
            GuardrailResult(
                name="no_chunks_retrieved",
                triggered=True,
                force_transfer=True,
                motivo=MotivoTransbordo.NO_RESULTS,
                departamento_sugerido="suporte_contabil",
                mensagem_para_cliente=(
                    "Nao encontrei conteudo na nossa base para responder isso com seguranca. "
                    "Vou te transferir para um atendente humano."
                ),
                confianca_resultante=0.0,
            )
        )
        return results

    if top_score < settings.min_score_top_chunk:
        results.append(
            GuardrailResult(
                name="low_retrieval_score",
                triggered=True,
                force_transfer=True,
                motivo=MotivoTransbordo.LOW_RETRIEVAL_SCORE,
                departamento_sugerido="suporte_contabil",
                mensagem_para_cliente=(
                    "Nao consegui encontrar uma resposta confiavel para isso na minha base. "
                    "Vou te transferir para um atendente humano que vai te ajudar melhor."
                ),
                confianca_resultante=0.0,
                details={"top_score": f"{top_score:.3f}", "threshold": str(settings.min_score_top_chunk)},
            )
        )
    return results


# ---------- post-LLM guardrails ----------


def check_post_llm(
    parsed_response: dict,
    chunks: list[RetrievedChunk],
) -> list[GuardrailResult]:
    """Verify the LLM response satisfies constraints:

    - Cited FAQ ids must come from the retrieved chunks (no hallucinated FAQ ids).
    - Confidence must clear MIN_CONFIANCA_RESPOSTA when acao=RESPONDER.
    - If acao=RESPONDER, mensagens must be non-empty.
    """
    settings = get_settings()
    results: list[GuardrailResult] = []

    retrieved_faq_ids = {c.faq_id for c in chunks if c.faq_id}
    cited_faq_ids = {
        f.get("faq_id")
        for f in parsed_response.get("faqs_consultados") or []
        if isinstance(f, dict) and f.get("faq_id")
    }
    hallucinated = cited_faq_ids - retrieved_faq_ids - {None}
    if hallucinated:
        results.append(
            GuardrailResult(
                name="hallucinated_faq",
                triggered=True,
                force_transfer=True,
                motivo=MotivoTransbordo.HALLUCINATION_DETECTED,
                departamento_sugerido="suporte_contabil",
                mensagem_para_cliente=(
                    "Preciso confirmar uma informacao antes de te responder. "
                    "Vou te passar para um atendente humano."
                ),
                confianca_resultante=0.0,
                details={"faq_ids_invalidos": ",".join(sorted(map(str, hallucinated)))},
            )
        )

    acao = parsed_response.get("acao")
    confianca = float(parsed_response.get("confianca") or 0.0)

    if acao == "RESPONDER" and confianca < settings.min_confianca_resposta:
        results.append(
            GuardrailResult(
                name="low_llm_confidence",
                triggered=True,
                force_transfer=True,
                motivo=MotivoTransbordo.LOW_LLM_CONFIDENCE,
                departamento_sugerido="suporte_contabil",
                mensagem_para_cliente=(
                    "Nao consigo te responder com a seguranca que eu gostaria. "
                    "Vou te transferir para um atendente humano."
                ),
                confianca_resultante=confianca,
                details={"confianca": f"{confianca:.3f}", "min": str(settings.min_confianca_resposta)},
            )
        )

    if acao == "RESPONDER":
        mensagens = parsed_response.get("mensagens") or []
        if not mensagens:
            results.append(
                GuardrailResult(
                    name="empty_response",
                    triggered=True,
                    force_transfer=True,
                    motivo=MotivoTransbordo.INTERNAL_ERROR,
                    departamento_sugerido="suporte_contabil",
                    mensagem_para_cliente=(
                        "Tive um problema para montar a resposta. "
                        "Vou te transferir para um atendente humano."
                    ),
                    confianca_resultante=0.0,
                )
            )

    return results


def first_blocking(results: Iterable[GuardrailResult]) -> GuardrailResult | None:
    for r in results:
        if r.force_transfer:
            return r
    return None


def names(results: Iterable[GuardrailResult]) -> list[str]:
    return [r.name for r in results if r.triggered]
