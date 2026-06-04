"""Time helpers for database-facing values."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    """Return UTC without tzinfo for legacy timestamp columns."""
    return datetime.now(UTC).replace(tzinfo=None)
