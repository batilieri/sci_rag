"""Compatibility patches for FlagEmbedding with newer Transformers releases."""

from __future__ import annotations


def patch_gemma2_docstring_constant() -> None:
    """FlagEmbedding 1.3.4 imports these docstring constants during package import."""
    try:
        from transformers.models.gemma2 import modeling_gemma2
    except Exception:
        return

    if not hasattr(modeling_gemma2, "GEMMA2_START_DOCSTRING"):
        modeling_gemma2.GEMMA2_START_DOCSTRING = ""
    if not hasattr(modeling_gemma2, "GEMMA2_INPUTS_DOCSTRING"):
        modeling_gemma2.GEMMA2_INPUTS_DOCSTRING = ""
