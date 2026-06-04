"""Per-request audit log for /v1/query."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class QueryLog(Base, TimestampMixin):
    __tablename__ = "rag_query_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rag_api_keys.id", ondelete="SET NULL"), nullable=True
    )

    cliente_id_externo: Mapped[str | None] = mapped_column(String(120), index=True)
    conversa_id_externo: Mapped[str | None] = mapped_column(String(120), index=True)
    canal: Mapped[str | None] = mapped_column(String(32))
    departamento_atual: Mapped[str | None] = mapped_column(String(64))

    mensagem_normalizada_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    mensagem_preview: Mapped[str | None] = mapped_column(String(200))

    acao: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    motivo_transbordo: Mapped[str | None] = mapped_column(String(40), index=True)
    departamento_sugerido: Mapped[str | None] = mapped_column(String(64))
    confianca: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    intencao_detectada: Mapped[str | None] = mapped_column(String(255))

    modelo_usado: Mapped[str | None] = mapped_column(String(64))
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    tempo_total_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tempo_busca_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tempo_rerank_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tempo_llm_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_entrada: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_saida: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    custo_estimado_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    top_score_busca: Mapped[float | None] = mapped_column(Float)
    faqs_consultados: Mapped[list[dict] | None] = mapped_column(JSONB)
    guardrails_acionados: Mapped[list[str] | None] = mapped_column(JSONB)

    erros: Mapped[list[dict] | None] = mapped_column(JSONB)
    extras: Mapped[dict | None] = mapped_column(JSONB)
    answer_preview: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
