"""GET /v1/health — validates API, Qdrant, Redis, R2/MinIO, LLM keys, Postgres."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.schemas.common import HealthComponent, HealthResponse
from app.storage import object_storage
from app.storage.postgres import get_engine
from app.storage.qdrant_client import get_qdrant
from app.storage.redis_client import ping as redis_ping

router = APIRouter(prefix="/v1", tags=["health"])


async def _measure(name: str, probe: Callable[[], Awaitable[bool]]) -> HealthComponent:
    started = time.perf_counter()
    try:
        ok = await asyncio.wait_for(probe(), timeout=3.0)
        latency = int((time.perf_counter() - started) * 1000)
        return HealthComponent(nome=name, status="ok" if ok else "down", latency_ms=latency)
    except TimeoutError:
        return HealthComponent(nome=name, status="down", detalhe="timeout")
    except Exception as exc:
        return HealthComponent(nome=name, status="down", detalhe=str(exc)[:200])


async def _postgres_probe() -> bool:
    engine = get_engine()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def _qdrant_probe() -> bool:
    client = get_qdrant()
    res = await client.get_collections()
    return res is not None


async def _llm_probe() -> bool:
    settings = get_settings()
    return bool(settings.anthropic_api_key) or bool(settings.deepseek_api_key)


@router.get("/health", response_model=HealthResponse, summary="Healthcheck completo")
async def health() -> HealthResponse:
    settings = get_settings()
    probes = await asyncio.gather(
        _measure("postgres", _postgres_probe),
        _measure("redis", redis_ping),
        _measure("qdrant", _qdrant_probe),
        _measure("object_storage", object_storage.ping),
        _measure("llm_credentials_configured", _llm_probe),
    )

    has_down = any(p.status == "down" for p in probes)
    has_degraded = any(p.status in ("degraded", "down") for p in probes)
    if has_down and any(p.nome in ("postgres", "qdrant") and p.status == "down" for p in probes):
        overall = "down"
    elif has_degraded:
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(
        status=overall,
        versao=settings.app_version,
        timestamp=datetime.now(UTC),
        componentes=list(probes),
    )
