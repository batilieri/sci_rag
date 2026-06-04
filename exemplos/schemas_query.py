"""
Schemas Pydantic — contratos da API.

Localização: app/schemas/query.py

Validação de entrada/saída do endpoint /v1/query.
Tudo aqui é parte do contrato público — versionar mudanças com cuidado.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════

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
    AUTO = "auto"  # API decide com base na complexidade
    DEEPSEEK_V4_PRO = "deepseek-v4-pro"
    CLAUDE_SONNET_45 = "claude-sonnet-4-5"
    DEEPSEEK_CHAT = "deepseek-chat"


class MotivoTransbordo(str, Enum):
    LOW_RETRIEVAL_SCORE = "low_retrieval_score"
    LOW_LLM_CONFIDENCE = "low_llm_confidence"
    OUT_OF_SCOPE = "out_of_scope"
    HALLUCINATION_DETECTED = "hallucination_detected"
    PII_DETECTED = "pii_detected"
    USER_REQUESTED = "user_requested"
    SENSITIVE_DATA_REQUEST = "sensitive_data_request"


# ═══════════════════════════════════════════════════════════════════
# REQUEST
# ═══════════════════════════════════════════════════════════════════

class ClienteContexto(BaseModel):
    """Perfil do cliente para personalizar a resposta."""
    id_externo: str = Field(..., description="ID do cliente no sistema chamador (Nexiry)")
    nome: str | None = Field(None, max_length=200)
    empresa: str | None = Field(None, max_length=200)
    licenca_sci: str | None = Field(None, description="Tipo de licença do sistema SCI")
    tempo_relacionamento_meses: int | None = Field(None, ge=0)
    metadata_extra: dict = Field(default_factory=dict, description="Campos arbitrários")


class MensagemHistorico(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(..., max_length=4000)
    timestamp: datetime | None = None


class ConversaContexto(BaseModel):
    id_externo: str = Field(..., description="ID da conversa/ticket no sistema chamador")
    canal: Canal = Canal.WHATSAPP
    departamento_atual: str | None = None
    historico: list[MensagemHistorico] = Field(
        default_factory=list,
        max_length=20,
        description="Últimas mensagens da conversa, ordenadas cronologicamente"
    )


class OpcoesQuery(BaseModel):
    modelo_preferido: ModeloLLM = ModeloLLM.AUTO
    incluir_debug: bool = False
    max_imagens: int = Field(3, ge=0, le=10)
    bypass_cache: bool = False
    threshold_confianca_minima: float | None = Field(
        None, ge=0.0, le=1.0,
        description="Sobrescreve threshold padrão de transbordo"
    )
    filtros_categoria: list[str] | None = Field(
        None, description="Restringir busca a categorias específicas (ex: ['ECD', 'ECF'])"
    )


class QueryRequest(BaseModel):
    """
    POST /v1/query
    """
    mensagem: str = Field(..., min_length=1, max_length=4000)
    cliente: ClienteContexto
    conversa: ConversaContexto
    opcoes: OpcoesQuery = Field(default_factory=OpcoesQuery)

    @field_validator("mensagem")
    @classmethod
    def validate_mensagem(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("mensagem não pode ser vazia ou só espaços")
        return v


# ═══════════════════════════════════════════════════════════════════
# RESPONSE
# ═══════════════════════════════════════════════════════════════════

class MensagemSaidaTexto(BaseModel):
    ordem: int
    tipo: Literal[TipoMensagem.TEXTO] = TipoMensagem.TEXTO
    conteudo: str = Field(..., max_length=4000)


class MensagemSaidaImagem(BaseModel):
    ordem: int
    tipo: Literal[TipoMensagem.IMAGEM] = TipoMensagem.IMAGEM
    url: str = Field(..., description="URL pré-assinada da imagem no object storage")
    legenda: str | None = Field(None, max_length=200)
    mime_type: str = "image/png"
    largura: int | None = None
    altura: int | None = None
    tamanho_bytes: int | None = None


MensagemSaida = MensagemSaidaTexto | MensagemSaidaImagem


class FAQConsultado(BaseModel):
    faq_id: str
    titulo: str
    score: float = Field(..., ge=0.0, le=1.0)
    url_original: str | None = None
    chunks_usados: list[str] = Field(default_factory=list, description="IDs dos chunks específicos")


class MetricasResposta(BaseModel):
    tempo_total_ms: int
    tempo_busca_ms: int
    tempo_rerank_ms: int
    tempo_llm_ms: int
    tokens_entrada: int
    tokens_saida: int
    custo_estimado_usd: float
    modelo_usado: str
    cache_hit: bool = False


class DebugInfo(BaseModel):
    """Só presente se opcoes.incluir_debug=True. NÃO logar em produção."""
    queries_reescritas: list[str]
    top_chunks: list[dict]
    raciocinio_llm: str | None
    guardrails_acionados: list[str]
    embedding_modelo: str


class QueryResponse(BaseModel):
    """
    Resposta de POST /v1/query.
    """
    request_id: str = Field(default_factory=lambda: f"req_{uuid4().hex[:24]}")
    acao: Acao
    confianca: float = Field(..., ge=0.0, le=1.0)
    departamento_sugerido: str | None = None
    motivo_transbordo: MotivoTransbordo | None = None
    intencao_detectada: str | None = None
    necessita_followup: bool = False
    mensagens: list[MensagemSaida]
    faqs_consultados: list[FAQConsultado] = Field(default_factory=list)
    metricas: MetricasResposta
    debug: DebugInfo | None = None


# ═══════════════════════════════════════════════════════════════════
# ERROR RESPONSES
# ═══════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════
# FEEDBACK
# ═══════════════════════════════════════════════════════════════════

class FeedbackRequest(BaseModel):
    request_id: str
    tipo: Literal["positivo", "negativo", "correcao"]
    fonte: Literal["atendente", "cliente"]
    comentario: str | None = Field(None, max_length=2000)
    correcao_sugerida: str | None = Field(None, max_length=4000)


class FeedbackResponse(BaseModel):
    registrado: bool = True
    mensagem: str = "Feedback registrado com sucesso"


# ═══════════════════════════════════════════════════════════════════
# WEBHOOK PAYLOADS (outbound)
# ═══════════════════════════════════════════════════════════════════

class WebhookEvent(BaseModel):
    """Estrutura padrão de qualquer webhook outbound."""
    evento: str  # "query.transferred_human", "ingest.completed", etc.
    timestamp: datetime
    request_id: str | None = None
    dados: dict

    # Para validação HMAC do lado do receptor:
    # header X-RAG-Signature: sha256=<hmac_sha256(secret, body)>
    # header X-RAG-Timestamp: <unix_timestamp>
