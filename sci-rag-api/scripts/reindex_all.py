"""Re-embed chunks already present in Qdrant."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from app.ingestion.chunker import ImageChunk, TextChunk
from app.ingestion.vectorizer import upsert_image_chunks, upsert_text_chunks
from app.schemas.common import ChunkTipoSemantico
from app.storage.qdrant_client import list_chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex Qdrant chunks with current embedding settings.")
    parser.add_argument("--faq-id", action="append", default=None, help="Limit to one or more FAQ IDs.")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _chunk_tipo(value: Any) -> ChunkTipoSemantico | None:
    if not value:
        return None
    try:
        return ChunkTipoSemantico(value)
    except ValueError:
        return None


def _text_from_payload(payload: dict[str, Any]) -> TextChunk:
    return TextChunk(
        chunk_id=payload.get("chunk_id") or payload.get("_chunk_id"),
        parent_chunk_id=payload.get("parent_chunk_id"),
        faq_id=str(payload.get("faq_id") or ""),
        chunk_index=int(payload.get("chunk_index") or 0),
        chunk_total=int(payload.get("chunk_total") or 1),
        chunk_tipo=_chunk_tipo(payload.get("chunk_tipo")),
        titulo_secao=payload.get("titulo_secao"),
        texto_original=payload.get("texto_original") or "",
        texto_enriquecido_para_embedding=payload.get("texto_enriquecido_para_embedding")
        or payload.get("texto_original")
        or "",
        payload_extra={
            "registros_sped_mencionados": payload.get("registros_sped_mencionados") or [],
            "menus_caminhos": payload.get("menus_caminhos") or [],
            "campos_interface": payload.get("campos_interface") or [],
            "palavras_chave_exatas": payload.get("palavras_chave_exatas") or [],
        },
    )


def _image_from_payload(payload: dict[str, Any]) -> ImageChunk:
    return ImageChunk(
        chunk_id=payload.get("chunk_id") or payload.get("_chunk_id"),
        faq_id=str(payload.get("faq_id") or ""),
        image_asset_id=payload.get("image_asset_id") or payload.get("filename") or "",
        payload_extra=payload,
    )


async def _run(args: argparse.Namespace) -> int:
    offset = None
    scanned = 0
    reindexed = 0

    while True:
        records, offset = await list_chunks(limit=args.batch_size, offset=offset)
        if not records:
            break

        text_chunks: list[tuple[TextChunk, dict[str, Any]]] = []
        image_chunks: list[ImageChunk] = []
        for record in records:
            payload = dict(record.payload or {})
            chunk_id = payload.get("chunk_id") or str(record.id)
            payload.setdefault("chunk_id", chunk_id)
            scanned += 1
            if args.faq_id and str(payload.get("faq_id")) not in set(args.faq_id):
                continue
            if payload.get("tipo_chunk") == "imagem":
                image_chunks.append(_image_from_payload(payload))
            else:
                text_chunks.append((_text_from_payload(payload), payload))

        if args.dry_run:
            reindexed += len(text_chunks) + len(image_chunks)
        else:
            for chunk, meta in text_chunks:
                reindexed += await upsert_text_chunks([chunk], meta)
            reindexed += await upsert_image_chunks(image_chunks)

        if offset is None:
            break

    print(f"scanned={scanned}")
    print(f"reindexed={reindexed}")
    print(f"dry_run={args.dry_run}")
    return 0


def main() -> int:
    return asyncio.run(_run(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
