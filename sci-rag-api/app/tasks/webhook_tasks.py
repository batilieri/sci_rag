"""Retriable outbound webhook tasks."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import configure_logging, get_logger
from app.core.webhooks import send_webhook_now
from app.tasks.celery_app import celery_app

configure_logging()
logger = get_logger(__name__)


@celery_app.task(
    name="webhooks.deliver",
    bind=True,
    max_retries=5,
    default_retry_delay=10,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
)
def deliver_webhook_task(
    self,
    event: str,
    dados: dict[str, Any],
    *,
    request_id: str | None = None,
    target_url: str | None = None,
) -> bool:
    logger.info("webhook_dispatch", event=event, attempt=self.request.retries)
    ok = asyncio.run(
        send_webhook_now(event, dados, request_id=request_id, target_url=target_url, timeout_seconds=10)
    )
    if not ok:
        raise RuntimeError(f"webhook {event} returned non-success")
    return True


def enqueue_webhook(
    event: str,
    dados: dict[str, Any],
    *,
    request_id: str | None = None,
    target_url: str | None = None,
) -> None:
    deliver_webhook_task.apply_async(
        kwargs={
            "event": event,
            "dados": dados,
            "request_id": request_id,
            "target_url": target_url,
        },
        queue="webhooks",
    )
