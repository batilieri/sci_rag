"""SlowAPI rate limiting keyed by X-API-Key (falls back to client IP)."""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings


def api_key_or_ip(request: Request) -> str:
    raw = request.headers.get("X-API-Key")
    if raw:
        # Use first 12 chars to avoid storing the full secret in memory keys.
        return f"key:{raw[:12]}"
    return f"ip:{get_remote_address(request)}"


def build_limiter() -> Limiter:
    settings = get_settings()
    return Limiter(
        key_func=api_key_or_ip,
        storage_uri=settings.redis_url,
        strategy="moving-window",
        default_limits=["240/minute"],
    )


limiter = build_limiter()
