"""BGE-M3 embeddings: dense, sparse, and ColBERT vectors.

The FlagEmbedding model is loaded lazily (it downloads ~2.2GB on first run) and
cached as a process singleton. Encoding runs in a thread pool so we don't block the
event loop.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.flagembedding_compat import patch_gemma2_docstring_constant

logger = get_logger(__name__)


@dataclass(slots=True)
class EncodedQuery:
    dense: list[float]
    sparse: dict[int, float]
    colbert: list[list[float]] | None = None


@dataclass(slots=True)
class EncodedChunk:
    chunk_id: str
    dense: list[float]
    sparse: dict[int, float]
    colbert: list[list[float]] | None = None


# The HuggingFace fast tokenizer inside BGE-M3 is not safe to call from multiple
# threads at once ("Already borrowed"). Inference is CPU/GIL-bound anyway, so we
# serialize encode() calls with this lock — no real throughput lost, race removed.
_ENCODE_LOCK = threading.Lock()


class _BGEM3Holder:
    _instance: Any | None = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> Any:
        if cls._instance is not None:
            return cls._instance
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls._load()
        return cls._instance

    @classmethod
    def _load(cls) -> Any:
        settings = get_settings()
        logger.info("loading_bge_m3", model=settings.embedding_model, device=settings.embedding_device)
        patch_gemma2_docstring_constant()
        from FlagEmbedding import BGEM3FlagModel  # heavy import, kept lazy

        use_fp16 = settings.embedding_device != "cpu"
        return BGEM3FlagModel(
            settings.embedding_model,
            use_fp16=use_fp16,
            devices=settings.embedding_device,
        )


def _to_python_dense(vec: Any) -> list[float]:
    if isinstance(vec, np.ndarray):
        return vec.astype(np.float32).tolist()
    return [float(x) for x in vec]


def _to_python_sparse(weights: Any) -> dict[int, float]:
    items: dict[int, float] = {}
    for token_id, weight in weights.items():
        try:
            idx = int(token_id)
        except (TypeError, ValueError):
            continue
        items[idx] = float(weight)
    return items


def _to_python_colbert(vec: Any) -> list[list[float]]:
    if isinstance(vec, np.ndarray):
        arr = vec.astype(np.float32)
        return arr.tolist()
    return [list(map(float, row)) for row in vec]


def _encode_sync(
    texts: list[str],
    *,
    with_colbert: bool = False,
    batch_size: int | None = None,
) -> list[dict[str, Any]]:
    if not texts:
        return []
    model = _BGEM3Holder.get()
    settings = get_settings()
    with _ENCODE_LOCK:
        out = model.encode(
            texts,
            batch_size=batch_size or settings.embedding_batch_size,
            max_length=8192,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=with_colbert,
        )
    results: list[dict[str, Any]] = []
    for i in range(len(texts)):
        results.append(
            {
                "dense": _to_python_dense(out["dense_vecs"][i]),
                "sparse": _to_python_sparse(out["lexical_weights"][i]),
                "colbert": _to_python_colbert(out["colbert_vecs"][i]) if with_colbert else None,
            }
        )
    return results


async def encode_query(text: str) -> EncodedQuery:
    raw = await asyncio.to_thread(_encode_sync, [text], with_colbert=False)
    payload = raw[0]
    return EncodedQuery(dense=payload["dense"], sparse=payload["sparse"], colbert=None)


async def encode_chunks(
    chunk_ids: list[str],
    texts: list[str],
    *,
    with_colbert: bool = False,
    batch_size: int | None = None,
) -> list[EncodedChunk]:
    if len(chunk_ids) != len(texts):
        raise ValueError("chunk_ids and texts must have the same length")
    raw = await asyncio.to_thread(_encode_sync, texts, with_colbert=with_colbert, batch_size=batch_size)
    return [
        EncodedChunk(
            chunk_id=chunk_ids[i],
            dense=raw[i]["dense"],
            sparse=raw[i]["sparse"],
            colbert=raw[i]["colbert"],
        )
        for i in range(len(texts))
    ]


def warmup_blocking() -> None:
    """Pre-load the model so the first request doesn't pay the cold start."""
    _BGEM3Holder.get()
