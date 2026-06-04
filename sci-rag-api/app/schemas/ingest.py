"""Schemas for ingestion endpoints and async job tracking."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class IngestJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class IngestPhase(str, Enum):
    UPLOAD = "upload"
    EXTRACTION = "extraction"
    VISION_DESCRIPTION = "vision_description"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    UPSERT = "upsert"
    DONE = "done"


class IngestSubmitResponse(BaseModel):
    job_id: str
    status: IngestJobStatus = IngestJobStatus.QUEUED
    documento: str
    tamanho_bytes: int
    detected_pages: int | None = None
    enqueued_at: datetime


class IngestJobProgress(BaseModel):
    phase: IngestPhase
    progresso_pct: float = Field(0.0, ge=0.0, le=100.0)
    mensagem: str | None = None


class IngestJobError(BaseModel):
    fase: IngestPhase
    mensagem: str
    detalhe: str | None = None


class IngestJobSummary(BaseModel):
    faqs_detectados: int = 0
    faqs_ingeridos: int = 0
    imagens_extraidas: int = 0
    imagens_upadas: int = 0
    chunks_gerados: int = 0
    chunks_upsertados: int = 0
    duracao_ms: int = 0


class IngestJobResponse(BaseModel):
    job_id: str
    status: IngestJobStatus
    documento: str
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: IngestJobProgress
    summary: IngestJobSummary | None = None
    errors: list[IngestJobError] = Field(default_factory=list)


class ReindexRequest(BaseModel):
    """Reindexa todos os chunks regenerando embeddings."""

    scope: Literal["all", "faq", "categoria"] = "all"
    faq_ids: list[str] | None = None
    categorias: list[str] | None = None
    dry_run: bool = False


class ReindexResponse(BaseModel):
    job_id: str
    scope: str
    alvos_estimados: int
    enqueued_at: datetime
