"""Create an ingestion job for an initial PDF and optionally process it inline."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.time import utcnow_naive
from app.ingestion.pipeline import run_ingestion_sync
from app.models.ingestion_job import IngestionJob
from app.schemas.ingest import IngestJobStatus, IngestPhase
from app.storage.postgres import get_sessionmaker, init_db


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the RAG collection from one PDF.")
    parser.add_argument("--pdf", required=True, help="Path to a PDF file.")
    parser.add_argument("--job-id", default=None, help="Optional deterministic job id.")
    parser.add_argument("--async", dest="enqueue", action="store_true", help="Enqueue Celery instead of inline run.")
    parser.add_argument("--documento", default=None, help="Document display name.")
    return parser.parse_args()


async def create_job(pdf_path: Path, *, job_id: str | None, documento: str | None) -> str:
    await init_db()
    resolved_job_id = job_id or f"seed_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        job = IngestionJob(
            job_id=resolved_job_id,
            documento=documento or pdf_path.name,
            storage_path=str(pdf_path),
            tamanho_bytes=pdf_path.stat().st_size,
            status=IngestJobStatus.QUEUED.value,
            phase=IngestPhase.UPLOAD.value,
            progresso_pct=0,
            submitted_at=utcnow_naive(),
        )
        session.add(job)
        await session.commit()
    return resolved_job_id


def main() -> int:
    args = parse_args()
    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise SystemExit(f"PDF invalido: {pdf_path}")

    job_id = asyncio.run(create_job(pdf_path, job_id=args.job_id, documento=args.documento))
    print(f"job_id: {job_id}")

    if args.enqueue:
        from app.tasks.ingestion_tasks import enqueue_ingest

        enqueue_ingest(job_id=job_id, pdf_path=str(pdf_path), source_documento=args.documento or pdf_path.name)
        print("status: queued")
        return 0

    summary = run_ingestion_sync(job_id, str(pdf_path), source_documento=args.documento or pdf_path.name)
    print("status:", summary.get("status", "unknown"))
    print("faqs_detectados:", summary.get("faqs_detectados", 0))
    print("faqs_ingeridos:", summary.get("faqs_ingeridos", 0))
    print("chunks_upsertados:", summary.get("chunks_upsertados", 0))
    if summary.get("errors"):
        print("errors:", summary["errors"])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
