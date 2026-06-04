"""Celery tasks for ingestion and reindex jobs."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from app.core.logging import configure_logging, get_logger
from app.core.webhooks import send_webhook_now
from app.ingestion.pipeline import run_ingestion_sync
from app.tasks.celery_app import celery_app

configure_logging()
logger = get_logger(__name__)


@celery_app.task(name="ingestion.run", bind=True, max_retries=2, default_retry_delay=60)
def run_ingest_task(self, job_id: str, pdf_path: str, source_documento: str | None = None) -> dict[str, Any]:
    logger.info("ingestion_task_started", job_id=job_id, pdf_path=pdf_path)
    try:
        summary = run_ingestion_sync(job_id, pdf_path, source_documento=source_documento)
    except Exception as exc:
        logger.exception("ingestion_task_failed", job_id=job_id)
        raise self.retry(exc=exc) from exc

    with suppress(RuntimeError):
        asyncio.run(
            send_webhook_now(
                "ingest.completed",
                summary,
                request_id=job_id,
            )
        )

    logger.info("ingestion_task_completed", job_id=job_id, summary=summary)
    return summary


@celery_app.task(name="ingestion.reindex")
def run_reindex_task(
    job_id: str,
    scope: str,
    faq_ids: list[str] | None = None,
    categorias: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    logger.info("reindex_task_started", job_id=job_id, scope=scope, dry_run=dry_run)
    # Full bulk reindex is delegated to scripts/reindex_all.py. The Celery task scope is to
    # support scoped re-embedding when chunk payloads are edited via the admin UI.
    return {
        "job_id": job_id,
        "scope": scope,
        "dry_run": dry_run,
        "completed": False,
        "note": "Implementacao plena vive em scripts/reindex_all.py; aqui apenas registramos a chamada.",
    }


# Convenience wrappers used by FastAPI endpoints.
def enqueue_ingest(*, job_id: str, pdf_path: str, source_documento: str | None) -> None:
    run_ingest_task.apply_async(
        kwargs={"job_id": job_id, "pdf_path": pdf_path, "source_documento": source_documento},
        queue="ingestion",
    )


def enqueue_reindex(
    *,
    job_id: str,
    scope: str,
    faq_ids: list[str] | None,
    categorias: list[str] | None,
    dry_run: bool,
) -> None:
    run_reindex_task.apply_async(
        kwargs={
            "job_id": job_id,
            "scope": scope,
            "faq_ids": faq_ids,
            "categorias": categorias,
            "dry_run": dry_run,
        },
        queue="ingestion",
    )
