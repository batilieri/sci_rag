"""Async Redis client singleton."""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as redis_async

from app.config import get_settings


@lru_cache
def get_redis() -> redis_async.Redis:
    settings = get_settings()
    return redis_async.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
        socket_timeout=2.0,
        socket_connect_timeout=2.0,
        health_check_interval=30,
    )


async def ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


async def shutdown_redis() -> None:
    client = get_redis()
    await client.aclose()
