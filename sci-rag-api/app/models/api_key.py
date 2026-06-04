"""API keys table — stores only hashes; raw keys are never persisted."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ApiKey(Base, TimestampMixin):
    __tablename__ = "rag_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    escopos: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rate_limit_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ultimo_uso: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revogada_em: Mapped[datetime | None] = mapped_column(nullable=True)
