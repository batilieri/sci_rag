"""Schemas for admin chunk inspection/editing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ChunkTipoSemantico, TipoChunk


class ChunkFonte(BaseModel):
    documento: str
    url_original: str | None = None
    pagina_pdf: int | None = None


class TextChunkPayload(BaseModel):
    tipo_chunk: TipoChunk = TipoChunk.TEXTO
    faq_id: str
    faq_titulo: str
    categoria_principal: str | None = None
    categorias_secundarias: list[str] = Field(default_factory=list)
    sistema: str | None = None
    modulo: str | None = None
    versao_sistema: str | None = None
    chunk_index: int = 0
    chunk_total: int = 1
    chunk_tipo: ChunkTipoSemantico | None = None
    parent_chunk_id: str | None = None
    titulo_secao: str | None = None
    texto_original: str
    texto_enriquecido_para_embedding: str
    registros_sped_mencionados: list[str] = Field(default_factory=list)
    relatorios_mencionados: list[str] = Field(default_factory=list)
    menus_caminhos: list[str] = Field(default_factory=list)
    campos_interface: list[str] = Field(default_factory=list)
    palavras_chave_exatas: list[str] = Field(default_factory=list)
    imagens_associadas: list[str] = Field(default_factory=list)
    intencoes_atendidas: list[str] = Field(default_factory=list)
    perguntas_exemplo: list[str] = Field(default_factory=list)
    publico_alvo: list[str] = Field(default_factory=list)
    data_cadastro_faq: datetime | None = None
    data_atualizacao_faq: datetime | None = None
    data_indexacao: datetime | None = None
    fonte: ChunkFonte
    confianca_extracao: float = 0.9
    revisado_humano: bool = False


class ImageChunkPayload(BaseModel):
    tipo_chunk: TipoChunk = TipoChunk.IMAGEM
    faq_id: str
    filename: str
    image_asset_id: str
    storage_url: str | None = None
    storage_path_interno: str | None = None
    r2_bucket: str | None = None
    r2_key: str | None = None
    r2_public_url: str | None = None
    r2_etag: str | None = None
    hash_md5: str | None = None
    tamanho_bytes: int
    dimensoes: dict[str, int] = Field(default_factory=dict)
    tipo_tela: str | None = None
    titulo_janela: str | None = None
    menu_caminho_inferido: str | None = None
    descricao_vision_llm: str | None = None
    ocr_texto_completo: str | None = None
    elementos_ui_identificados: list[dict[str, Any]] = Field(default_factory=list)
    elementos_destacados_visualmente: list[str] = Field(default_factory=list)
    registros_sped_visiveis: list[str] = Field(default_factory=list)
    palavras_chave_exatas: list[str] = Field(default_factory=list)
    quando_enviar: list[str] = Field(default_factory=list)
    contexto_faq: str | None = None
    modelo_vision_usado: str | None = None
    data_descricao: datetime | None = None
    confianca_ocr: float = 0.0
    revisado_humano: bool = False


class ChunkListItem(BaseModel):
    chunk_id: str
    tipo_chunk: TipoChunk
    faq_id: str
    titulo: str
    categoria_principal: str | None = None
    revisado_humano: bool = False
    confianca_extracao: float = 0.0
    data_indexacao: datetime | None = None


class ChunkListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ChunkListItem]


class ChunkDetailResponse(BaseModel):
    chunk_id: str
    tipo_chunk: TipoChunk
    payload: dict[str, Any]
    vector_summary: dict[str, Any] = Field(default_factory=dict)


class ChunkUpdateRequest(BaseModel):
    """Campos editaveis por admin. Texto editado dispara re-embedding."""

    texto_original: str | None = Field(None, max_length=10_000)
    texto_enriquecido_para_embedding: str | None = Field(None, max_length=12_000)
    titulo_secao: str | None = None
    chunk_tipo: ChunkTipoSemantico | None = None
    menus_caminhos: list[str] | None = None
    campos_interface: list[str] | None = None
    palavras_chave_exatas: list[str] | None = None
    quando_enviar: list[str] | None = None
    descricao_vision_llm: str | None = None
    confianca_extracao: float | None = Field(None, ge=0.0, le=1.0)
    revisado_humano: bool | None = None
    extra_payload: dict[str, Any] | None = None


class ChunkApprovalResponse(BaseModel):
    chunk_id: str
    revisado_humano: bool = True
    updated_at: datetime
