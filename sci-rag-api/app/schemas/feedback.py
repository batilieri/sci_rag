"""Schemas for POST /v1/feedback."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    request_id: str = Field(..., description="ID retornado por /v1/query")
    tipo: Literal["positivo", "negativo", "correcao"]
    fonte: Literal["atendente", "cliente"]
    comentario: str | None = Field(None, max_length=2000)
    correcao_sugerida: str | None = Field(None, max_length=4000)


class FeedbackResponse(BaseModel):
    registrado: bool = True
    mensagem: str = "Feedback registrado com sucesso"
    request_id: str
