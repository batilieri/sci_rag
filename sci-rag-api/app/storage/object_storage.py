"""Object storage facade: routes to Cloudflare R2 by default, MinIO when R2 disabled.

Both backends are S3-compatible. This facade exposes the minimum surface used by
the ingestion pipeline so the rest of the codebase never has to branch on backend.
"""

from __future__ import annotations

from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig

from app.config import get_settings
from app.core.logging import get_logger
from app.storage import r2_client
from app.storage.r2_client import StoredObject, deterministic_key, public_url_for

logger = get_logger(__name__)


def _minio_kwargs(endpoint: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    return {
        "service_name": "s3",
        "endpoint_url": endpoint or settings.minio_endpoint,
        "aws_access_key_id": settings.minio_user,
        "aws_secret_access_key": settings.minio_pass,
        "region_name": "us-east-1",
        "config": BotoConfig(signature_version="s3v4"),
    }


def active_bucket() -> str:
    settings = get_settings()
    return settings.r2_bucket if settings.use_r2 else settings.minio_bucket


async def put_image(
    *,
    faq_id: str,
    image_id: str,
    body: bytes,
    content_type: str = "image/png",
    metadata: dict[str, str] | None = None,
) -> StoredObject:
    settings = get_settings()
    key = deterministic_key(faq_id, image_id)
    bucket = active_bucket()

    if settings.use_r2:
        return await r2_client.put_object(
            bucket=bucket, key=key, body=body, content_type=content_type, metadata=metadata
        )

    session = aioboto3.Session()
    async with session.client(**_minio_kwargs()) as s3:
        try:
            await s3.head_bucket(Bucket=bucket)
        except Exception:
            await s3.create_bucket(Bucket=bucket)
        response = await s3.put_object(
            Bucket=bucket, Key=key, Body=body, ContentType=content_type, Metadata=metadata or {}
        )
    import hashlib

    return StoredObject(
        bucket=bucket,
        key=key,
        etag=(response.get("ETag") or "").strip('"') or None,
        public_url=None,
        content_type=content_type,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        md5=hashlib.md5(body, usedforsecurity=False).hexdigest(),
    )


async def url_for_delivery(bucket: str, key: str, *, public_url: str | None = None) -> str:
    """Returns the URL the bot should send to the customer.

    Order:
    1. Stored public_url (R2 public bucket).
    2. Computed from R2_PUBLIC_BASE_URL.
    3. Pre-signed URL (R2 or MinIO).
    """
    if public_url:
        return public_url
    settings = get_settings()
    if settings.use_r2:
        computed = public_url_for(key)
        if computed:
            return computed
        return await r2_client.generate_presigned_url(bucket, key)

    # Sign with the public endpoint so the URL is reachable outside the docker net.
    delivery_endpoint = settings.minio_public_endpoint or settings.minio_endpoint
    session = aioboto3.Session()
    async with session.client(**_minio_kwargs(delivery_endpoint)) as s3:
        return await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=settings.r2_presigned_url_ttl_seconds,
        )


async def ping() -> bool:
    settings = get_settings()
    if settings.use_r2:
        return await r2_client.ping()
    try:
        session = aioboto3.Session()
        async with session.client(**_minio_kwargs()) as s3:
            await s3.list_buckets()
        return True
    except Exception as exc:
        logger.warning("minio_ping_failed", error=str(exc))
        return False
