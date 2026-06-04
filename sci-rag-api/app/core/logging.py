"""Structured logging configuration backed by structlog."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import get_settings


def _drop_pii(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Best-effort PII drop. Specific scrubber lives in app.rag.guardrails."""
    for key in ("password", "cpf", "cnpj", "senha", "token"):
        if key in event_dict:
            event_dict[key] = "***"
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.app_log_level.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _drop_pii,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.app_env == "development":
        shared_processors.append(structlog.dev.ConsoleRenderer(colors=False))
    else:
        shared_processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        level=level,
        stream=sys.stdout,
        force=True,
    )

    for noisy in ("uvicorn.access", "watchfiles", "httpx"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
