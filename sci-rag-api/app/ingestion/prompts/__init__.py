"""Ingestion prompt templates."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent


@lru_cache
def load(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")
