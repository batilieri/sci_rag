"""POST /v1/admin/ingest and GET /v1/admin/ingest/{job_id}."""

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import limiter
from app.core.security import ApiKeyContext, require_admin_write
from app.core.time import utcnow_naive
from app.models.ingestion_job import IngestionJob
from app.schemas.ingest import (
    IngestJobError,
    IngestJobProgress,
    IngestJobResponse,
    IngestJobStatus,
    IngestJobSummary,
    IngestPhase,
    IngestSubmitResponse,
)
from app.storage.postgres import get_session

logger = get_logger(__name__)
router = APIRouter(prefix="/v1/admin", tags=["admin:ingest"])

UPLOAD_DIR = Path("/srv/app/data/uploads")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post(
    "/ingest",
    response_model=IngestSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("5/minute")
async def submit_ingest(
    request: Request,
    file: Annotated[UploadFile, File(..., description="PDF de FAQs SCI")],
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IngestSubmitResponse:
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"erro": "tipo_invalido", "mensagem": "envie um arquivo .pdf"},
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    job_id = f"ingest_{uuid.uuid4().hex[:16]}"
    target = UPLOAD_DIR / f"{job_id}_{file.filename or 'document.pdf'}"

    total = 0
    with target.open("wb") as fh:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                fh.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail={"erro": "arquivo_grande", "mensagem": "limite e 50MB"},
                )
            fh.write(chunk)

    now = utcnow_naive()
    job = IngestionJob(
        job_id=job_id,
        documento=file.filename or target.name,
        storage_path=str(target),
        tamanho_bytes=total,
        status=IngestJobStatus.QUEUED.value,
        phase=IngestPhase.UPLOAD.value,
        progresso_pct=0,
        submitted_at=now,
    )
    session.add(job)
    await session.commit()

    # Lazy import to avoid celery hard-dep when running unit tests.
    from app.tasks.ingestion_tasks import enqueue_ingest

    enqueue_ingest(job_id=job_id, pdf_path=str(target), source_documento=file.filename)

    return IngestSubmitResponse(
        job_id=job_id,
        status=IngestJobStatus.QUEUED,
        documento=job.documento,
        tamanho_bytes=total,
        enqueued_at=now,
    )


@router.get("/ingest/{job_id}", response_model=IngestJobResponse)
async def get_ingest_status(
    job_id: str,
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IngestJobResponse:
    stmt = select(IngestionJob).where(IngestionJob.job_id == job_id)
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"erro": "job_inexistente", "mensagem": job_id},
        )

    progress = IngestJobProgress(
        phase=IngestPhase(job.phase) if job.phase in {p.value for p in IngestPhase} else IngestPhase.UPLOAD,
        progresso_pct=float(job.progresso_pct or 0),
    )
    summary = IngestJobSummary(
        faqs_detectados=job.faqs_detectados,
        faqs_ingeridos=job.faqs_ingeridos,
        imagens_extraidas=job.imagens_extraidas,
        imagens_upadas=job.imagens_upadas,
        chunks_gerados=job.chunks_gerados,
        chunks_upsertados=job.chunks_upsertados,
        duracao_ms=job.duracao_ms,
    )
    errors = [
        IngestJobError(
            fase=IngestPhase(e.get("fase", "extraction")) if e.get("fase") in {p.value for p in IngestPhase} else IngestPhase.EXTRACTION,
            mensagem=e.get("mensagem", ""),
            detalhe=e.get("detalhe"),
        )
        for e in (job.errors or [])
    ]

    return IngestJobResponse(
        job_id=job.job_id,
        status=IngestJobStatus(job.status) if job.status in {s.value for s in IngestJobStatus} else IngestJobStatus.QUEUED,
        documento=job.documento,
        submitted_at=job.submitted_at or job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        progress=progress,
        summary=summary,
        errors=errors,
    )
