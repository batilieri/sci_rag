"""POST /v1/admin/reindex — schedule a bulk re-embedding job."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.logging import get_logger
from app.core.security import ApiKeyContext, require_admin_write
from app.schemas.ingest import ReindexRequest, ReindexResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin:reindex"])


@router.post("/reindex", response_model=ReindexResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_reindex(
    payload: ReindexRequest,
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
) -> ReindexResponse:
    job_id = f"reindex_{uuid.uuid4().hex[:16]}"
    # Lazy import to avoid celery import at unit test time.
    from app.tasks.ingestion_tasks import enqueue_reindex

    enqueue_reindex(
        job_id=job_id,
        scope=payload.scope,
        faq_ids=payload.faq_ids,
        categorias=payload.categorias,
        dry_run=payload.dry_run,
    )
    return ReindexResponse(
        job_id=job_id,
        scope=payload.scope,
        alvos_estimados=0,
        enqueued_at=datetime.now(UTC),
    )
