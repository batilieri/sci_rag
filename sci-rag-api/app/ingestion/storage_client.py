"""Bridge between the ingestion pipeline and object_storage + ImageAsset Postgres rows."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.image_asset import ImageAsset
from app.storage import object_storage

logger = get_logger(__name__)


@dataclass(slots=True)
class StoredImageRecord:
    image_asset_id: str
    db_id: str
    bucket: str
    key: str
    public_url: str | None
    etag: str | None
    sha256: str
    md5: str | None
    size_bytes: int
    width: int
    height: int
    content_type: str
    reused: bool


async def upsert_image_asset(
    session: AsyncSession,
    *,
    faq_id: str,
    image_id: str,
    body: bytes,
    width: int,
    height: int,
    description: dict | None = None,
    original_filename: str | None = None,
) -> StoredImageRecord:
    """Idempotent upload: dedupe by hash_sha256, store metadata in Postgres."""
    description = description or {}
    import hashlib

    sha256 = hashlib.sha256(body).hexdigest()

    stmt = select(ImageAsset).where(ImageAsset.hash_sha256 == sha256)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return StoredImageRecord(
            image_asset_id=existing.image_id,
            db_id=str(existing.id),
            bucket=existing.r2_bucket,
            key=existing.r2_key,
            public_url=existing.r2_public_url,
            etag=existing.r2_etag,
            sha256=existing.hash_sha256,
            md5=existing.hash_md5,
            size_bytes=existing.tamanho_bytes,
            width=existing.width or width,
            height=existing.height or height,
            content_type=existing.content_type,
            reused=True,
        )

    stored = await object_storage.put_image(
        faq_id=faq_id,
        image_id=image_id,
        body=body,
        content_type="image/png",
        metadata={"faq_id": faq_id, "image_id": image_id},
    )

    asset = ImageAsset(
        image_id=image_id,
        faq_id=faq_id,
        original_filename=original_filename,
        r2_bucket=stored.bucket,
        r2_key=stored.key,
        r2_public_url=stored.public_url,
        r2_etag=stored.etag,
        content_type=stored.content_type,
        tamanho_bytes=stored.size_bytes,
        width=width,
        height=height,
        hash_sha256=stored.sha256,
        hash_md5=stored.md5,
        ordem_no_faq=description.get("ordem_no_faq"),
        tipo_tela=description.get("tipo_tela"),
        titulo_janela=description.get("titulo_janela"),
        descricao_curta=description.get("descricao_curta"),
        menu_caminho_inferido=description.get("menu_caminho_inferido"),
        registros_sped_visiveis=description.get("registros_sped_visiveis") or [],
        palavras_chave_exatas=description.get("palavras_chave_exatas") or [],
        quando_enviar=description.get("quando_enviar") or [],
    )
    session.add(asset)
    await session.flush()

    return StoredImageRecord(
        image_asset_id=image_id,
        db_id=str(asset.id),
        bucket=stored.bucket,
        key=stored.key,
        public_url=stored.public_url,
        etag=stored.etag,
        sha256=stored.sha256,
        md5=stored.md5,
        size_bytes=stored.size_bytes,
        width=width,
        height=height,
        content_type=stored.content_type,
        reused=False,
    )
