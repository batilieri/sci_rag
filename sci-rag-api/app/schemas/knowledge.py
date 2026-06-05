"""Schemas for manual knowledge capture (chat-style admin: describe an error → high-quality FAQ)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class KnowledgeDraftRequest(BaseModel):
    """A layperson's free-text description of a problem and how to solve it.

    The UI sends everything gathered so far (original report + answers to follow-up
    questions) concatenated in ``relato`` — the endpoint is stateless and re-evaluates
    the whole thing each time.
    """

    relato: str = Field(..., min_length=3, description="Relato livre do problema + solucao")
    titulo: str | None = Field(default=None, description="Titulo opcional sugerido pelo atendente")


class KnowledgeDraftResponse(BaseModel):
    status: Literal["ok", "incompleto"]
    perguntas: list[str] = Field(default_factory=list)
    resumo: str | None = None
    faq: dict[str, Any] | None = None


class KnowledgeSaveRequest(BaseModel):
    """The structured FAQ (as returned by /draft, possibly edited by the human) to persist."""

    faq: dict[str, Any] = Field(..., description="FAQ estruturado a indexar na base vetorial")


class KnowledgeSaveResponse(BaseModel):
    faq_id: str
    titulo: str | None = None
    chunks_criados: int
    chunk_ids: list[str]
