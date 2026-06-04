"""Prompt templates loaded at runtime."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache
def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, /, **values: object) -> str:
    """Fill ``{placeholder}`` slots without using ``str.format``.

    The templates embed JSON examples with literal ``{`` / ``}`` braces, which
    ``str.format`` would try (and fail) to interpret as fields. We substitute only
    the named placeholders we were given and leave every other brace untouched.
    """
    rendered = load_prompt(name)
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", str(value))
    return rendered
