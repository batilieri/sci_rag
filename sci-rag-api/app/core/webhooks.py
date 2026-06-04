"""Outbound webhook dispatch with HMAC signing and Celery-backed retries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import orjson

from app.config import get_settings
from app.core.logging import get_logger
from app.core.security import WebhookSigner

logger = get_logger(__name__)

WEBHOOK_EVENTS = (
    "query.transferred_human",
    "query.low_confidence",
    "ingest.completed",
    "feedback.negative",
)


def build_event_envelope(event: str, dados: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    if event not in WEBHOOK_EVENTS:
        raise ValueError(f"evento desconhecido: {event}")
    return {
        "evento": event,
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": request_id,
        "dados": dados,
    }


async def send_webhook_now(
    event: str,
    dados: dict[str, Any],
    *,
    request_id: str | None = None,
    target_url: str | None = None,
    timeout_seconds: float = 5.0,
) -> bool:
    """Synchronous (best-effort) HTTP send. Use Celery for retries via tasks/webhook_tasks.py."""
    settings = get_settings()
    url = target_url or settings.webhook_nexiry_url
    if not url:
        logger.warning("webhook_skip_no_target", webhook_event=event)
        return False

    envelope = build_event_envelope(event, dados, request_id=request_id)
    body = orjson.dumps(envelope)
    signer = WebhookSigner()
    headers = signer.sign(body)
    headers["Content-Type"] = "application/json"
    headers["User-Agent"] = "nexiry-rag-api/0.1"

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, content=body, headers=headers)
        if response.status_code >= 400:
            logger.warning(
                "webhook_non_2xx",
                webhook_event=event,
                status_code=response.status_code,
                body=response.text[:500],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("webhook_dispatch_failed", webhook_event=event, error=str(exc))
        return False
