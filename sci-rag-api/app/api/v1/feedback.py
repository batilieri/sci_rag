"""POST /v1/feedback — records explicit user/agent feedback for a prior query."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.security import ApiKeyContext, require_feedback
from app.core.webhooks import send_webhook_now
from app.models.feedback import Feedback
from app.models.query_log import QueryLog
from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.storage.postgres import get_session

logger = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["feedback"])


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("120/minute")
async def submit_feedback(
    request: Request,
    payload: FeedbackRequest,
    auth: Annotated[ApiKeyContext, Depends(require_feedback)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FeedbackResponse:
    # Ensure the request_id exists; otherwise reject (avoid orphan feedback rows).
    stmt = select(QueryLog).where(QueryLog.request_id == payload.request_id)
    qlog = (await session.execute(stmt)).scalar_one_or_none()
    if qlog is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "erro": "request_id_inexistente",
                "mensagem": f"Nao encontrei uma query com request_id={payload.request_id}",
            },
        )

    record = Feedback(
        request_id=payload.request_id,
        tipo=payload.tipo,
        fonte=payload.fonte,
        comentario=payload.comentario,
        correcao_sugerida=payload.correcao_sugerida,
    )
    session.add(record)
    await session.commit()

    if payload.tipo == "negativo":
        await send_webhook_now(
            "feedback.negative",
            {
                "request_id": payload.request_id,
                "comentario": payload.comentario,
                "correcao_sugerida": payload.correcao_sugerida,
                "fonte": payload.fonte,
            },
            request_id=payload.request_id,
        )

    return FeedbackResponse(registrado=True, request_id=payload.request_id)
