"""Async ingestion job tracker."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "rag_ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    documento: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    tamanho_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    detected_pages: Mapped[int | None] = mapped_column(Integer)

    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rag_api_keys.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(40), nullable=False, default="upload")
    progresso_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    faqs_detectados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    faqs_ingeridos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imagens_extraidas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imagens_upadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_gerados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_upsertados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    submitted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duracao_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    errors: Mapped[list[dict] | None] = mapped_column(JSONB)
    extras: Mapped[dict | None] = mapped_column(JSONB)
