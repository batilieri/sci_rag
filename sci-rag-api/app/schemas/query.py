"""Schemas for POST /v1/query and POST /v1/query/stream."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import (
    Acao,
    Canal,
    MensagemHistorico,
    ModeloLLM,
    MotivoTransbordo,
    TipoMensagem,
)


class ClienteContexto(BaseModel):
    id_externo: str = Field(..., description="ID do cliente no sistema chamador")
    nome: str | None = Field(None, max_length=200)
    empresa: str | None = Field(None, max_length=200)
    licenca_sci: str | None = Field(None, description="Tipo de licenca do sistema SCI")
    tempo_relacionamento_meses: int | None = Field(None, ge=0)
    metadata_extra: dict = Field(default_factory=dict)


class ConversaContexto(BaseModel):
    id_externo: str
    canal: Canal = Canal.WHATSAPP
    departamento_atual: str | None = None
    historico: list[MensagemHistorico] = Field(default_factory=list, max_length=20)


class OpcoesQuery(BaseModel):
    modelo_preferido: ModeloLLM = ModeloLLM.AUTO
    incluir_debug: bool = False
    max_imagens: int = Field(3, ge=0, le=10)
    bypass_cache: bool = False
    threshold_confianca_minima: float | None = Field(None, ge=0.0, le=1.0)
    filtros_categoria: list[str] | None = None


class QueryRequest(BaseModel):
    mensagem: str = Field(..., min_length=1, max_length=4000)
    cliente: ClienteContexto
    conversa: ConversaContexto
    opcoes: OpcoesQuery = Field(default_factory=OpcoesQuery)
    # Print/foto que o cliente enviou (base64, sem o prefixo data:). Quando presente,
    # o Claude "lê" a imagem (OCR + descrição) e o conteudo entra na busca e na resposta.
    imagem_base64: str | None = Field(None, description="Imagem do cliente em base64 (opcional)")
    imagem_mime: str = Field("image/png", description="MIME da imagem enviada")
    # Anexo genérico do chat: imagem, PDF ou TXT (base64). A IA LÊ o arquivo só para
    # entender a pergunta e buscar nas informações já salvas — NÃO grava na base.
    anexo_base64: str | None = Field(None, description="Anexo do chat em base64 (imagem/pdf/txt)")
    anexo_mime: str | None = Field(None, description="MIME do anexo (ex: application/pdf, text/plain)")
    anexo_nome: str | None = Field(None, max_length=255, description="Nome do arquivo anexado")

    @field_validator("mensagem")
    @classmethod
    def _strip_mensagem(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("mensagem nao pode ser vazia ou so espacos")
        return v


class MensagemSaidaTexto(BaseModel):
    ordem: int
    tipo: Literal[TipoMensagem.TEXTO] = TipoMensagem.TEXTO
    conteudo: str = Field(..., max_length=4000)


class MensagemSaidaImagem(BaseModel):
    ordem: int
    tipo: Literal[TipoMensagem.IMAGEM] = TipoMensagem.IMAGEM
    url: str
    legenda: str | None = Field(None, max_length=200)
    mime_type: str = "image/png"
    largura: int | None = None
    altura: int | None = None
    tamanho_bytes: int | None = None


MensagemSaida = Annotated[
    MensagemSaidaTexto | MensagemSaidaImagem,
    Field(discriminator="tipo"),
]


class FAQConsultado(BaseModel):
    faq_id: str
    titulo: str
    score: float = Field(..., ge=0.0, le=1.0)
    url_original: str | None = None
    chunks_usados: list[str] = Field(default_factory=list)


class MetricasResposta(BaseModel):
    tempo_total_ms: int
    tempo_busca_ms: int = 0
    tempo_rerank_ms: int = 0
    tempo_llm_ms: int = 0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    custo_estimado_usd: float = 0.0
    modelo_usado: str
    cache_hit: bool = False


class DebugInfo(BaseModel):
    """Apenas em modo debug — nao logar em producao por padrao."""

    queries_reescritas: list[str] = Field(default_factory=list)
    top_chunks: list[dict] = Field(default_factory=list)
    raciocinio_llm: str | None = None
    guardrails_acionados: list[str] = Field(default_factory=list)
    embedding_modelo: str | None = None


class QueryResponse(BaseModel):
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
