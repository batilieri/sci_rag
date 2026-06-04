"""Reranker using BGE-reranker-v2-m3 (cross-encoder, multilingual)."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from app.config import get_settings
from app.core.logging import get_logger
from app.rag.flagembedding_compat import patch_gemma2_docstring_constant
from app.rag.retrieval import RetrievedChunk

logger = get_logger(__name__)


class _RerankerHolder:
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
        logger.info("loading_reranker", model=settings.reranker_model)
        patch_gemma2_docstring_constant()
        from FlagEmbedding import FlagReranker  # heavy import, kept lazy

        use_fp16 = settings.embedding_device != "cpu"
        return FlagReranker(settings.reranker_model, use_fp16=use_fp16, devices=settings.embedding_device)


def _payload_text_for_rerank(payload: dict[str, Any]) -> str:
    """Build a compact representation for cross-encoder scoring."""
    if payload.get("tipo_chunk") == "imagem":
        parts = [
            payload.get("titulo_janela") or "",
            payload.get("descricao_vision_llm") or "",
            payload.get("ocr_texto_completo") or "",
        ]
    else:
        parts = [
            payload.get("titulo_secao") or "",
            payload.get("texto_original") or "",
            " ".join(payload.get("palavras_chave_exatas") or []),
        ]
    text = " | ".join(p for p in parts if p)
    return text[:2000]


def _rerank_sync(query: str, chunks: list[RetrievedChunk], normalize: bool) -> list[float]:
    model = _RerankerHolder.get()
    pairs = [[query, _payload_text_for_rerank(c.payload)] for c in chunks]
    scores = model.compute_score(pairs, normalize=normalize)
    if isinstance(scores, float):
        scores = [scores]
    return [float(s) for s in scores]


async def rerank(
    query: str,
    chunks: list[RetrievedChunk],
    *,
    top_k: int | None = None,
    normalize: bool = True,
) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = top_k or settings.rerank_top_k
    if not chunks:
        return []
    if not settings.reranker_model:
        return chunks[:top_k]

    try:
        scores = await asyncio.to_thread(_rerank_sync, query, chunks, normalize)
    except Exception as exc:
        logger.warning("rerank_failed_falling_back", error=str(exc))
        return chunks[:top_k]

    for c, s in zip(chunks, scores, strict=False):
        c.rerank_score = s

    reranked = sorted(chunks, key=lambda c: (c.rerank_score or 0.0), reverse=True)
    return reranked[:top_k]
