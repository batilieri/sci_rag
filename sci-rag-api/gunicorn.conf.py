"""Gunicorn configuration tuned for serving the RAG API at scale on one box.

Key idea: the BGE-M3 embedder and the reranker are multi-GB models. Loading one
copy per worker blows past container memory and OOM-kills workers under load.
With ``preload_app`` gunicorn imports the app once in the master; the
``on_starting`` hook then loads the heavy models there, so every forked worker
shares those (read-only) weight pages via copy-on-write instead of allocating
its own multi-GB copy. This also removes the first-request cold start.
"""

from __future__ import annotations

import os

bind = f"0.0.0.0:{os.getenv('APP_PORT', '8000')}"
workers = int(os.getenv("APP_WORKERS", "2"))
worker_class = "uvicorn.workers.UvicornWorker"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Share the heavy ML models across workers (see module docstring).
preload_app = True

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("APP_LOG_LEVEL", "info").lower()


def on_starting(server) -> None:
    """Warm the embedder + reranker once in the master, pre-fork."""
    if os.getenv("RAG_PRELOAD_MODELS", "1") != "1":
        return
    try:
        from app.rag.embeddings import warmup_blocking

        warmup_blocking()
        server.log.info("embedder warmed up (shared with workers via copy-on-write)")
    except Exception as exc:  # never block startup on warmup
        server.log.warning("embedder warmup failed: %s", exc)
    try:
        from app.rag.reranker import _RerankerHolder

        _RerankerHolder.get()
        server.log.info("reranker warmed up (shared with workers via copy-on-write)")
    except Exception as exc:
        server.log.warning("reranker warmup failed: %s", exc)
