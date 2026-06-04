"""Hybrid retrieval orchestrator: encode queries, search Qdrant, deduplicate."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.embeddings import EncodedQuery, encode_query
from app.storage import qdrant_client as qdrant
from app.storage.qdrant_client import build_filter

logger = get_logger(__name__)


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    score: float
    payload: dict[str, Any]
    source_query: str | None = None
    rerank_score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def faq_id(self) -> str | None:
        return self.payload.get("faq_id")

    @property
    def is_image(self) -> bool:
        return self.payload.get("tipo_chunk") == "imagem"


async def _search_one(
    query: str,
    encoded: EncodedQuery,
    *,
    top_k: int,
    category_filter: list[str] | None,
) -> list[RetrievedChunk]:
    qfilter = build_filter(category_in=category_filter)
    points = await qdrant.search_hybrid(
        dense_vector=encoded.dense,
        sparse_vector=encoded.sparse,
        top_k=top_k,
        qdrant_filter=qfilter,
    )
    return [
        RetrievedChunk(
            chunk_id=(p.payload or {}).get("chunk_id") or str(p.id),
            score=float(p.score or 0.0),
            payload=p.payload or {},
            source_query=query,
        )
        for p in points
    ]


async def retrieve(
    queries: list[str],
    *,
    top_k: int | None = None,
    category_filter: list[str] | None = None,
) -> list[RetrievedChunk]:
    """Encode each query, search in parallel, then deduplicate by point id keeping max score."""
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k

    encoded_list = await asyncio.gather(*[encode_query(q) for q in queries])
    search_results = await asyncio.gather(
        *[
            _search_one(q, encoded, top_k=top_k, category_filter=category_filter)
            for q, encoded in zip(queries, encoded_list, strict=False)
        ]
    )

    best: dict[str, RetrievedChunk] = {}
    for batch in search_results:
        for chunk in batch:
            existing = best.get(chunk.chunk_id)
            if existing is None or chunk.score > existing.score:
                best[chunk.chunk_id] = chunk

    return sorted(best.values(), key=lambda c: c.score, reverse=True)


def top_score(chunks: list[RetrievedChunk]) -> float:
    return chunks[0].score if chunks else 0.0


def partition_text_image(
    chunks: list[RetrievedChunk],
) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
    text_chunks: list[RetrievedChunk] = []
    image_chunks: list[RetrievedChunk] = []
    for c in chunks:
        if c.is_image:
            image_chunks.append(c)
        else:
            text_chunks.append(c)
    return text_chunks, image_chunks
