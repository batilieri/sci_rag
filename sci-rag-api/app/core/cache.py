"""Redis-backed response cache.

Cache key = sha256 of normalized question + license + departamento.
TTL depends on confidence band; transbordo responses are never cached.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

import orjson

from app.config import get_settings
from app.core.logging import get_logger
from app.storage.redis_client import get_redis

logger = get_logger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_message(text: str) -> str:
    """Lowercase, strip accents, drop punctuation, collapse whitespace."""
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def build_cache_key(mensagem: str, licenca_sci: str | None, departamento: str | None) -> str:
    norm = normalize_message(mensagem)
    payload = f"{norm}||{licenca_sci or ''}||{departamento or ''}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"rag:query:{digest}"


def ttl_for_confidence(confianca: float) -> int | None:
    """Returns None when the response should not be cached."""
    settings = get_settings()
    if confianca >= 0.90:
        return settings.cache_ttl_high
    if confianca >= settings.min_confianca_resposta:
        return settings.cache_ttl_medium
    return None


class ResponseCache:
    def __init__(self) -> None:
        self._client = get_redis()

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        try:
            raw = await self._client.get(cache_key)
        except Exception as exc:
            logger.warning("cache_get_failed", error=str(exc))
            return None
        if raw is None:
            return None
        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            return None

    async def set(self, cache_key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        try:
            await self._client.set(cache_key, orjson.dumps(value), ex=ttl_seconds)
        except Exception as exc:
            logger.warning("cache_set_failed", error=str(exc))

    async def delete(self, cache_key: str) -> None:
        try:
            await self._client.delete(cache_key)
        except Exception as exc:
            logger.warning("cache_delete_failed", error=str(exc))


def get_response_cache() -> ResponseCache:
    return ResponseCache()
