"""Shared enums and base models used across multiple endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Acao(str, Enum):
    RESPONDER = "RESPONDER"
    TRANSFERIR_HUMANO = "TRANSFERIR_HUMANO"
    PEDIR_CLARIFICACAO = "PEDIR_CLARIFICACAO"


class Canal(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    WEB = "web"
    EMAIL = "email"
    OUTRO = "outro"


class TipoMensagem(str, Enum):
    TEXTO = "texto"
    IMAGEM = "imagem"
    ARQUIVO = "arquivo"
    AUDIO = "audio"


class ModeloLLM(str, Enum):
    AUTO = "auto"
    DEEPSEEK_CHAT = "deepseek-chat"
    DEEPSEEK_V4_PRO = "deepseek-v4-pro"
    CLAUDE_SONNET_45 = "claude-sonnet-4-5"


class MotivoTransbordo(str, Enum):
    LOW_RETRIEVAL_SCORE = "low_retrieval_score"
    LOW_LLM_CONFIDENCE = "low_llm_confidence"
    OUT_OF_SCOPE = "out_of_scope"
    HALLUCINATION_DETECTED = "hallucination_detected"
    PII_DETECTED = "pii_detected"
    USER_REQUESTED = "user_requested"
    SENSITIVE_DATA_REQUEST = "sensitive_data_request"
    NO_RESULTS = "no_results"
    INTERNAL_ERROR = "internal_error"
    UNSUPPORTED_INTENT = "unsupported_intent"


class ChunkTipoSemantico(str, Enum):
    INTRODUCAO = "introducao"
    PROCEDIMENTO = "procedimento"
    CALCULO_REGRA = "calculo_regra"
    CONFIGURACAO = "configuracao"
    EXEMPLO = "exemplo"
    OBSERVACAO_IMPORTANTE = "observacao_importante"
    REFERENCIA_CRUZADA = "referencia_cruzada"


class TipoChunk(str, Enum):
    TEXTO = "texto"
    IMAGEM = "imagem"


class MensagemHistorico(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(..., max_length=4000)
    timestamp: datetime | None = None


class ErroValidacaoCampo(BaseModel):
    campo: str
    erro: str


class ErroResposta(BaseModel):
    erro: str
    mensagem: str
    request_id: str | None = None
    campos: list[ErroValidacaoCampo] | None = None
    retry_after_seconds: int | None = None
    componente: str | None = None


class HealthComponent(BaseModel):
    nome: str
    status: Literal["ok", "degraded", "down", "unknown"]
    latency_ms: int | None = None
    detalhe: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    versao: str
    timestamp: datetime
    componentes: list[HealthComponent]


class StatsResponse(BaseModel):
    total_queries_24h: int
    taxa_transbordo_24h: float
    cache_hit_rate_24h: float
    latencia_p50_ms: float
    latencia_p95_ms: float
    latencia_p99_ms: float
    confianca_media_24h: float
    chunks_indexados: int
    faqs_indexados: int
    imagens_indexadas: int
    custo_llm_24h_usd: float
    erros_24h: int
    timestamp: datetime
