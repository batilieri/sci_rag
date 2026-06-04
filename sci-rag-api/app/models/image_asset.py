"""Persistent metadata for images uploaded to Cloudflare R2 (or MinIO fallback).

Binary bytes never live here — only locator (bucket/key/url) and descriptive metadata.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImageAsset(Base, TimestampMixin):
    __tablename__ = "rag_image_assets"
    __table_args__ = (
        UniqueConstraint("r2_bucket", "r2_key", name="uq_rag_image_assets_bucket_key"),
        CheckConstraint("tamanho_bytes >= 0", name="ck_rag_image_assets_tamanho_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    image_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    faq_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chunk_id: Mapped[str | None] = mapped_column(String(160), index=True)
    original_filename: Mapped[str | None] = mapped_column(String(255))

    r2_bucket: Mapped[str] = mapped_column(String(120), nullable=False)
    r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    r2_public_url: Mapped[str | None] = mapped_column(Text)
    r2_etag: Mapped[str | None] = mapped_column(String(160))
    content_type: Mapped[str] = mapped_column(String(80), nullable=False, default="image/png")

    tamanho_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hash_md5: Mapped[str | None] = mapped_column(String(32))

    ordem_no_faq: Mapped[int | None] = mapped_column(Integer)
    tipo_tela: Mapped[str | None] = mapped_column(String(80))
    titulo_janela: Mapped[str | None] = mapped_column(Text)
    descricao_curta: Mapped[str | None] = mapped_column(Text)
    menu_caminho_inferido: Mapped[str | None] = mapped_column(Text)

    registros_sped_visiveis: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    palavras_chave_exatas: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    quando_enviar: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    revisado_humano: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active", index=True)
