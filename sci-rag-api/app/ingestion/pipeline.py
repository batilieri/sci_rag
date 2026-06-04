"""Ingestion orchestrator. Executes the 13-step pipeline from the spec.

This module is sync at the Celery boundary but uses asyncio internally for I/O.
Call `run_ingestion(job_id, pdf_path)` from a Celery task or a CLI.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.time import utcnow_naive
from app.ingestion.chunker import (
    ImageChunk,
    TextChunk,
    build_image_chunk,
    build_text_chunks,
)
from app.ingestion.pdf_extractor import FaqBlock, RawImage, extract_pdf
from app.ingestion.prompts import load as load_ingest_prompt
from app.ingestion.storage_client import StoredImageRecord, upsert_image_asset
from app.ingestion.vectorizer import upsert_image_chunks, upsert_text_chunks
from app.ingestion.vision_describer import describe_image
from app.models.ingestion_job import IngestionJob
from app.rag.llm_clients import call_claude, parse_json_or_raise
from app.storage.postgres import get_sessionmaker
from app.storage.qdrant_client import ensure_collection

logger = get_logger(__name__)


async def _structure_faq_with_llm(faq_block: FaqBlock) -> dict[str, Any]:
    """Call Claude to convert raw FAQ text into the structured JSON shape we need."""
    prompt_template = load_ingest_prompt("extracao.txt")
    prompt = prompt_template.replace("{faq_text}", faq_block.raw_text[:18000])
    try:
        resp = await call_claude(
            system=(
                "Voce e um extrator de FAQs SCI Contabil. Sempre responda JSON valido. "
                "Nunca invente caminhos de menu, registros SPED ou campos."
            ),
            user=prompt,
            max_tokens=4000,
            temperature=0.0,
            json_mode=True,
        )
        data = parse_json_or_raise(resp.text)
    except Exception as exc:
        logger.warning("structure_faq_failed_using_fallback", error=str(exc), faq_id=faq_block.faq_id)
        return _fallback_structured(faq_block)

    data.setdefault("faq_id", faq_block.faq_id)
    data.setdefault("url_original", faq_block.url_original)
    if not data.get("titulo") and faq_block.titulo:
        data["titulo"] = faq_block.titulo
    return data


def _fallback_structured(faq_block: FaqBlock) -> dict[str, Any]:
    raw_text = faq_block.raw_text
    title = _extract_labeled_line(raw_text, "Titulo") or faq_block.titulo or f"FAQ {faq_block.faq_id}"
    url = _extract_labeled_line(raw_text, "URL original") or faq_block.url_original
    category_text = _extract_labeled_line(raw_text, "Categoria") or ""
    category = "ECD" if "ECD" in category_text.upper() or "SPED ECD" in raw_text.upper() else "OUTROS"
    keywords = _extract_csv_label(raw_text, "Palavras-chave")
    registros = sorted(set(re.findall(r"\b[A-Z]\d{3}\b", raw_text)))
    menu_paths = sorted(set(re.findall(r"\b[A-Z][A-Za-z ]+\s*>\s*[A-Z][A-Za-z0-9 /-]+(?:\s*>\s*[A-Z][A-Za-z0-9 /-]+)*", raw_text)))
    perguntas = _extract_bullets_after(raw_text, "Perguntas que este FAQ deve responder")

    return {
        "faq_id": faq_block.faq_id,
        "titulo": title,
        "url_original": url,
        "categoria_principal": category,
        "categorias_secundarias": [category_text] if category_text else [],
        "sistema": "SCI",
        "modulo": "Contabil" if "CONTABIL" in raw_text.upper() or "SPED ECD" in raw_text.upper() else "Geral",
        "registros_sped_mencionados": registros,
        "relatorios_mencionados": [],
        "menus_caminhos": menu_paths,
        "campos_interface": [],
        "palavras_chave_exatas": keywords,
        "intencoes_atendidas": perguntas,
        "perguntas_exemplo": perguntas,
        "publico_alvo": [],
        "secoes": [
            {
                "titulo_secao": title,
                "chunk_tipo": "procedimento",
                "texto_original": raw_text,
                "texto_enriquecido_para_embedding": raw_text,
                "registros_sped_mencionados": registros,
                "menus_caminhos": menu_paths,
                "campos_interface": [],
                "palavras_chave_exatas": keywords,
            }
        ],
    }


def _extract_labeled_line(text: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}:\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _extract_csv_label(text: str, label: str) -> list[str]:
    value = _extract_labeled_line(text, label)
    if not value:
        return []
    return [item.strip() for item in value.rstrip(".").split(",") if item.strip()]


def _extract_bullets_after(text: str, heading: str) -> list[str]:
    marker = f"{heading}:"
    if marker not in text:
        return []
    _, rest = text.split(marker, 1)
    bullets: list[str] = []
    for line in rest.splitlines():
        stripped = line.strip()
        if not stripped:
            if bullets:
                break
            continue
        if not stripped.startswith("- "):
            if bullets:
                break
            continue
        bullets.append(stripped[2:].strip())
    return bullets


async def _update_job(session: AsyncSession, job: IngestionJob, **fields: Any) -> None:
    for k, v in fields.items():
        setattr(job, k, v)
    await session.flush()
    await session.commit()


async def process_one_faq(
    session: AsyncSession,
    faq_block: FaqBlock,
    *,
    source_documento: str,
) -> tuple[int, int, int]:
    """Returns (faqs_ingeridos, imagens_upadas, chunks_upsertados)."""
    structured = await _structure_faq_with_llm(faq_block)

    # Process images first so chunks can reference them.
    stored_images: list[tuple[RawImage, StoredImageRecord, dict[str, Any]]] = []
    for idx, raw_image in enumerate(faq_block.images):
        description = await describe_image(
            raw_image.bytes_png,
            faq_context=f"FAQ {faq_block.faq_id} - {structured.get('titulo') or ''}",
        )
        description.setdefault("ordem_no_faq", idx)
        stored = await upsert_image_asset(
            session,
            faq_id=faq_block.faq_id,
            image_id=raw_image.image_id,
            body=raw_image.bytes_png,
            width=raw_image.width,
            height=raw_image.height,
            description=description,
        )
        stored_images.append((raw_image, stored, description))

    structured["imagens_associadas"] = [s.image_asset_id for _, s, _ in stored_images]
    structured["source_documento"] = source_documento
    structured["pagina_pdf"] = faq_block.page_start

    text_chunks: list[TextChunk] = build_text_chunks(structured)
    upserted_text = await upsert_text_chunks(text_chunks, structured)

    image_chunks: list[ImageChunk] = []
    for raw, stored, description in stored_images:
        image_chunks.append(
            build_image_chunk(
                faq_id=faq_block.faq_id,
                image_asset_id=stored.image_asset_id,
                description_json=description,
                storage={
                    "bucket": stored.bucket,
                    "key": stored.key,
                    "public_url": stored.public_url,
                    "etag": stored.etag,
                },
                filename=raw.original_filename or f"{stored.image_asset_id}.png",
                tamanho_bytes=stored.size_bytes,
                width=stored.width,
                height=stored.height,
                hash_md5=stored.md5,
            )
        )
    upserted_image = await upsert_image_chunks(image_chunks)

    await session.commit()
    return 1, len(stored_images), upserted_text + upserted_image


async def run_ingestion(job_id: str, pdf_path: str, *, source_documento: str | None = None) -> dict[str, Any]:
    pdf_path_obj = Path(pdf_path)
    sessionmaker = get_sessionmaker()

    started_at = time.perf_counter()
    summary = {
        "job_id": job_id,
        "faqs_detectados": 0,
        "faqs_ingeridos": 0,
        "imagens_extraidas": 0,
        "imagens_upadas": 0,
        "chunks_gerados": 0,
        "chunks_upsertados": 0,
        "errors": [],
    }

    await ensure_collection()

    async with sessionmaker() as session:
        stmt = select(IngestionJob).where(IngestionJob.job_id == job_id)
        job = (await session.execute(stmt)).scalar_one()
        await _update_job(
            session,
            job,
            status="running",
            phase="extraction",
            progresso_pct=5,
            started_at=utcnow_naive(),
        )

        try:
            faqs = await asyncio.to_thread(extract_pdf, pdf_path_obj)
            summary["faqs_detectados"] = len(faqs)
            summary["imagens_extraidas"] = sum(len(f.images) for f in faqs)
            await _update_job(session, job, faqs_detectados=len(faqs), phase="chunking", progresso_pct=15)
        except Exception as exc:
            summary["errors"].append({"fase": "extraction", "mensagem": str(exc)})
            await _update_job(
                session, job, status="failed", phase="extraction", errors=summary["errors"],
                finished_at=utcnow_naive(),
            )
            return summary

        for idx, faq_block in enumerate(faqs):
            try:
                f_in, img_up, chunks_up = await process_one_faq(
                    session, faq_block, source_documento=source_documento or pdf_path_obj.name
                )
                summary["faqs_ingeridos"] += f_in
                summary["imagens_upadas"] += img_up
                summary["chunks_upsertados"] += chunks_up
            except Exception as exc:
                logger.exception("faq_ingest_failed", faq_id=faq_block.faq_id)
                summary["errors"].append(
                    {"fase": "embedding", "faq_id": faq_block.faq_id, "mensagem": str(exc)}
                )

            pct = 15 + (idx + 1) / max(1, len(faqs)) * 80
            await _update_job(
                session,
                job,
                phase="embedding",
                progresso_pct=pct,
                faqs_ingeridos=summary["faqs_ingeridos"],
                imagens_upadas=summary["imagens_upadas"],
                chunks_upsertados=summary["chunks_upsertados"],
            )

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        status = "completed"
        if not summary["faqs_ingeridos"]:
            status = "failed"
        elif summary["errors"]:
            status = "partial"

        await _update_job(
            session,
            job,
            status=status,
            phase="done",
            progresso_pct=100,
            duracao_ms=duration_ms,
            finished_at=utcnow_naive(),
            errors=summary["errors"] or None,
            extras={"summary": json.loads(json.dumps(summary, default=str))},
        )
        summary["status"] = status
        return summary


def run_ingestion_sync(job_id: str, pdf_path: str, *, source_documento: str | None = None) -> dict[str, Any]:
    """Sync wrapper for Celery."""
    return asyncio.run(run_ingestion(job_id, pdf_path, source_documento=source_documento))
