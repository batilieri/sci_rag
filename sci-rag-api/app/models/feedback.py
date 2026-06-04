"""Feedback table — links to QueryLog by request_id."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Feedback(Base, TimestampMixin):
    __tablename__ = "rag_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    fonte: Mapped[str] = mapped_column(String(20), nullable=False)
    comentario: Mapped[str | None] = mapped_column(Text)
    correcao_sugerida: Mapped[str | None] = mapped_column(Text)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rag_api_keys.id", ondelete="SET NULL"), nullable=True
    )
