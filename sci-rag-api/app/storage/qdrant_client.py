"""Qdrant wrapper around AsyncQdrantClient with hybrid-search helpers."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DENSE_NAME = "dense"
SPARSE_NAME = "sparse"
COLBERT_NAME = "colbert"

DENSE_SIZE = 1024
COLBERT_SIZE = 128

INDEXED_TEXT_PAYLOAD_KEYS: tuple[str, ...] = (
    "faq_id",
    "categoria_principal",
    "chunk_tipo",
    "tipo_chunk",
    "revisado_humano",
)

INDEXED_KEYWORD_PAYLOAD_KEYS: tuple[str, ...] = (
    "registros_sped_mencionados",
    "registros_sped_visiveis",
    "palavras_chave_exatas",
    "categorias_secundarias",
)


@lru_cache
def get_qdrant() -> AsyncQdrantClient:
    settings = get_settings()
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        prefer_grpc=False,
        timeout=30,
    )


async def ensure_collection() -> None:
    """Create the configured collection with three named vectors if missing."""
    settings = get_settings()
    client = get_qdrant()
    name = settings.qdrant_collection

    existing = await client.get_collections()
    if any(c.name == name for c in existing.collections):
        return

    await client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_NAME: qmodels.VectorParams(size=DENSE_SIZE, distance=qmodels.Distance.COSINE),
            COLBERT_NAME: qmodels.VectorParams(
                size=COLBERT_SIZE,
                distance=qmodels.Distance.COSINE,
                multivector_config=qmodels.MultiVectorConfig(
                    comparator=qmodels.MultiVectorComparator.MAX_SIM
                ),
            ),
        },
        sparse_vectors_config={
            SPARSE_NAME: qmodels.SparseVectorParams(
                index=qmodels.SparseIndexParams(on_disk=False)
            )
        },
        hnsw_config=qmodels.HnswConfigDiff(m=32, ef_construct=200, on_disk=False),
        optimizers_config=qmodels.OptimizersConfigDiff(memmap_threshold=50_000),
    )

    for field in INDEXED_TEXT_PAYLOAD_KEYS:
        await client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
    for field in INDEXED_KEYWORD_PAYLOAD_KEYS:
        await client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
    logger.info("qdrant_collection_created", collection=name)


def make_point_id(chunk_id: str) -> str:
    """Stable UUID5 derived from chunk_id so the same chunk always maps to the same point."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nexiry-rag:{chunk_id}"))


def build_filter(category_in: list[str] | None = None, faq_in: list[str] | None = None) -> qmodels.Filter | None:
    must: list[qmodels.FieldCondition] = []
    if category_in:
        must.append(
            qmodels.FieldCondition(
                key="categoria_principal",
                match=qmodels.MatchAny(any=category_in),
            )
        )
    if faq_in:
        must.append(qmodels.FieldCondition(key="faq_id", match=qmodels.MatchAny(any=faq_in)))
    if not must:
        return None
    return qmodels.Filter(must=must)


async def upsert_chunks(points: list[qmodels.PointStruct]) -> None:
    if not points:
        return
    settings = get_settings()
    client = get_qdrant()
    await client.upsert(collection_name=settings.qdrant_collection, points=points, wait=True)


async def delete_chunks(chunk_ids: list[str]) -> None:
    if not chunk_ids:
        return
    settings = get_settings()
    client = get_qdrant()
    point_ids = [make_point_id(cid) for cid in chunk_ids]
    await client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.PointIdsList(points=point_ids),
        wait=True,
    )


async def search_hybrid(
    dense_vector: list[float],
    sparse_vector: dict[int, float],
    *,
    top_k: int,
    qdrant_filter: qmodels.Filter | None = None,
) -> list[qmodels.ScoredPoint]:
    """Dense + sparse retrieval with RRF fusion via Query API."""
    settings = get_settings()
    client = get_qdrant()

    sparse_query = qmodels.SparseVector(
        indices=list(sparse_vector.keys()),
        values=list(sparse_vector.values()),
    )

    prefetch = [
        qmodels.Prefetch(query=dense_vector, using=DENSE_NAME, limit=top_k),
        qmodels.Prefetch(query=sparse_query, using=SPARSE_NAME, limit=top_k),
    ]

    response = await client.query_points(
        collection_name=settings.qdrant_collection,
        prefetch=prefetch,
        query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
        limit=top_k,
        query_filter=qdrant_filter,
        with_payload=True,
        with_vectors=False,
    )
    return response.points


async def fetch_payload(chunk_id: str) -> dict[str, Any] | None:
    settings = get_settings()
    client = get_qdrant()
    point_id = make_point_id(chunk_id)
    points = await client.retrieve(
        collection_name=settings.qdrant_collection,
        ids=[point_id],
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return None
    payload = points[0].payload or {}
    payload["_chunk_id"] = chunk_id
    return payload


async def list_chunks(
    *,
    limit: int = 50,
    offset: int | None = None,
    qdrant_filter: qmodels.Filter | None = None,
) -> tuple[list[qmodels.Record], str | None]:
    settings = get_settings()
    client = get_qdrant()
    records, next_offset = await client.scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=qdrant_filter,
        limit=limit,
        offset=offset,
        with_payload=True,
        with_vectors=False,
    )
    return records, next_offset
