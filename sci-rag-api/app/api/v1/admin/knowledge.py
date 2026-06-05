"""Chat-style manual knowledge capture.

POST /v1/admin/knowledge/draft  — turn a layperson's free-text problem+solution into a
                                   structured, high-quality FAQ (preview only, nothing saved).
POST /v1/admin/knowledge/save   — embed + upsert the (reviewed) FAQ into the vector base.

This complements the PDF pipeline (/v1/admin/ingest): same Qdrant collection and payload shape,
so the support agent retrieves manually-entered knowledge exactly like FAQs extracted from PDFs.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.security import ApiKeyContext, require_admin_write
from app.ingestion.chunker import build_text_chunks
from app.ingestion.prompts import render
from app.ingestion.vectorizer import upsert_text_chunks
from app.rag.llm_clients import call_claude, parse_json_or_raise
from app.schemas.knowledge import (
    KnowledgeDraftRequest,
    KnowledgeDraftResponse,
    KnowledgeSaveRequest,
    KnowledgeSaveResponse,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/v1/admin/knowledge", tags=["admin:knowledge"])


@router.post("/draft", response_model=KnowledgeDraftResponse)
@limiter.limit("20/minute")
async def draft_knowledge(
    request: Request,
    body: KnowledgeDraftRequest,
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
) -> KnowledgeDraftResponse:
    """Ask Claude to structure the free-text report. Returns either follow-up questions
    (status=incompleto) or a ready-to-review FAQ (status=ok). Nothing is persisted here."""
    prompt = render(
        "estruturar_conhecimento.txt",
        relato=body.relato.strip(),
        titulo=(body.titulo or "").strip(),
    )
    resp = await call_claude(
        system="Voce estrutura conhecimento de suporte tecnico em JSON valido.",
        user=prompt,
        max_tokens=4096,
        json_mode=True,
    )
    try:
        data = parse_json_or_raise(resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge_draft_parse_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"erro": "resposta_invalida", "mensagem": "A IA nao retornou um JSON valido."},
        ) from exc

    estado = data.get("status")
    if estado == "incompleto":
        return KnowledgeDraftResponse(
            status="incompleto",
            perguntas=[str(p) for p in (data.get("perguntas") or [])][:3],
        )

    faq = data.get("faq")
    if not isinstance(faq, dict) or not faq.get("secoes"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"erro": "faq_incompleto", "mensagem": "Nao foi possivel montar o FAQ."},
        )
    return KnowledgeDraftResponse(status="ok", resumo=data.get("resumo"), faq=faq)


@router.post("/save", response_model=KnowledgeSaveResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def save_knowledge(
    request: Request,
    body: KnowledgeSaveRequest,
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
) -> KnowledgeSaveResponse:
    """Embed the reviewed FAQ and upsert it into Qdrant. Marked as human-reviewed."""
    faq = dict(body.faq)
    if not faq.get("secoes"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"erro": "faq_sem_secoes", "mensagem": "O FAQ precisa de pelo menos uma secao."},
        )

    faq_id = str(faq.get("faq_id") or "").strip() or f"MAN-{uuid.uuid4().hex[:8]}"
    faq["faq_id"] = faq_id

    # Metadata consumed by vectorizer._build_text_payload. A human authored/approved this,
    # so confidence is max and revisado_humano is True.
    faq_meta = dict(faq)
    faq_meta.setdefault("sistema", "SCI")
    faq_meta["source_documento"] = "entrada_manual"
    faq_meta["confianca_extracao"] = 1.0
    faq_meta["revisado_humano"] = True

    text_chunks = build_text_chunks(faq)
    n = await upsert_text_chunks(text_chunks, faq_meta)

    logger.info(
        "knowledge_saved", faq_id=faq_id, chunks=n, key_id=auth.key_id, titulo=faq.get("titulo")
    )
    return KnowledgeSaveResponse(
        faq_id=faq_id,
        titulo=faq.get("titulo"),
        chunks_criados=n,
        chunk_ids=[c.chunk_id for c in text_chunks],
    )
