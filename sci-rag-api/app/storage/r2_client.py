"""Cloudflare R2 client (S3-compatible).

When R2 credentials are absent, MinIOClient (object_storage.py) is the dev fallback.
The R2 client never reads/writes the binary into Postgres or Qdrant — only locator/metadata.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class StoredObject:
    bucket: str
    key: str
    etag: str | None
    public_url: str | None
    content_type: str
    size_bytes: int
    sha256: str
    md5: str | None


@lru_cache
def _aioboto3_session() -> aioboto3.Session:
    return aioboto3.Session()


def _client_kwargs() -> dict[str, Any]:
    settings = get_settings()
    if not settings.use_r2:
        raise RuntimeError("R2 credentials are not configured (R2_ACCOUNT_ID/key/secret).")
    return {
        "service_name": "s3",
        "endpoint_url": settings.r2_endpoint_url,
        "aws_access_key_id": settings.r2_access_key_id,
        "aws_secret_access_key": settings.r2_secret_access_key,
        "region_name": "auto",
        "config": BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    }


def deterministic_key(faq_id: str, image_id: str, extension: str = "png") -> str:
    """sci/faq/{faq_id}/images/{image_id}.{extension}"""
    return f"sci/faq/{faq_id}/images/{image_id}.{extension}"


def public_url_for(key: str) -> str | None:
    settings = get_settings()
    if not settings.r2_public_base_url:
        return None
    base = settings.r2_public_base_url.rstrip("/")
    return f"{base}/{key.lstrip('/')}"


async def put_object(
    *,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str = "image/png",
    metadata: dict[str, str] | None = None,
) -> StoredObject:
    session = _aioboto3_session()
    async with session.client(**_client_kwargs()) as s3:
        response = await s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            Metadata=metadata or {},
        )
    sha = hashlib.sha256(body).hexdigest()
    md5 = hashlib.md5(body, usedforsecurity=False).hexdigest()
    return StoredObject(
        bucket=bucket,
        key=key,
        etag=(response.get("ETag") or "").strip('"') or None,
        public_url=public_url_for(key),
        content_type=content_type,
        size_bytes=len(body),
        sha256=sha,
        md5=md5,
    )


async def head_object(bucket: str, key: str) -> dict[str, Any] | None:
    session = _aioboto3_session()
    async with session.client(**_client_kwargs()) as s3:
        try:
            return await s3.head_object(Bucket=bucket, Key=key)
        except s3.exceptions.NoSuchKey:
            return None
        except Exception as exc:
            err = getattr(exc, "response", {}).get("Error", {})
            if err.get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise


async def delete_object(bucket: str, key: str) -> None:
    session = _aioboto3_session()
    async with session.client(**_client_kwargs()) as s3:
        await s3.delete_object(Bucket=bucket, Key=key)


async def generate_presigned_url(bucket: str, key: str, *, ttl_seconds: int | None = None) -> str:
    settings = get_settings()
    ttl = ttl_seconds or settings.r2_presigned_url_ttl_seconds
    session = _aioboto3_session()
    async with session.client(**_client_kwargs()) as s3:
        return await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl,
        )


async def ping() -> bool:
    settings = get_settings()
    if not settings.use_r2:
        return False
    try:
        session = _aioboto3_session()
        async with session.client(**_client_kwargs()) as s3:
            await s3.list_objects_v2(Bucket=settings.r2_bucket, MaxKeys=1)
        return True
    except Exception as exc:
        logger.warning("r2_ping_failed", error=str(exc))
        return False
