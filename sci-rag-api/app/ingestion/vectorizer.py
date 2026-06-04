"""Embed chunks (BGE-M3) and upsert them into Qdrant with rich payload."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from qdrant_client.http import models as qmodels

from app.core.logging import get_logger
from app.ingestion.chunker import (
    ImageChunk,
    TextChunk,
    image_chunk_text_for_embedding,
    text_chunk_text_for_embedding,
)
from app.rag.embeddings import encode_chunks
from app.schemas.common import TipoChunk
from app.storage.qdrant_client import (
    COLBERT_NAME,
    DENSE_NAME,
    SPARSE_NAME,
    make_point_id,
    upsert_chunks,
)

logger = get_logger(__name__)


def _build_text_payload(chunk: TextChunk, faq_meta: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "chunk_id": chunk.chunk_id,
        "tipo_chunk": TipoChunk.TEXTO.value,
        "faq_id": chunk.faq_id,
        "faq_titulo": faq_meta.get("titulo"),
        "categoria_principal": faq_meta.get("categoria_principal"),
        "categorias_secundarias": faq_meta.get("categorias_secundarias") or [],
        "sistema": faq_meta.get("sistema") or "SCI",
        "modulo": faq_meta.get("modulo"),
        "versao_sistema": faq_meta.get("versao_sistema"),
        "chunk_index": chunk.chunk_index,
        "chunk_total": chunk.chunk_total,
        "chunk_tipo": chunk.chunk_tipo.value if chunk.chunk_tipo else None,
        "parent_chunk_id": chunk.parent_chunk_id,
        "titulo_secao": chunk.titulo_secao,
        "texto_original": chunk.texto_original,
        "texto_enriquecido_para_embedding": chunk.texto_enriquecido_para_embedding,
        "registros_sped_mencionados": chunk.payload_extra.get("registros_sped_mencionados")
        or faq_meta.get("registros_sped_mencionados")
        or [],
        "relatorios_mencionados": faq_meta.get("relatorios_mencionados") or [],
        "menus_caminhos": chunk.payload_extra.get("menus_caminhos") or faq_meta.get("menus_caminhos") or [],
        "campos_interface": chunk.payload_extra.get("campos_interface")
        or faq_meta.get("campos_interface")
        or [],
        "palavras_chave_exatas": chunk.payload_extra.get("palavras_chave_exatas")
        or faq_meta.get("palavras_chave_exatas")
        or [],
        "imagens_associadas": faq_meta.get("imagens_associadas") or [],
        "intencoes_atendidas": faq_meta.get("intencoes_atendidas") or [],
        "perguntas_exemplo": faq_meta.get("perguntas_exemplo") or [],
        "publico_alvo": faq_meta.get("publico_alvo") or [],
        "data_cadastro_faq": faq_meta.get("data_cadastro_faq"),
        "data_atualizacao_faq": faq_meta.get("data_atualizacao_faq"),
        "data_indexacao": now,
        "fonte": {
            "documento": faq_meta.get("source_documento"),
            "url_original": faq_meta.get("url_original"),
            "pagina_pdf": faq_meta.get("pagina_pdf"),
        },
        "confianca_extracao": float(faq_meta.get("confianca_extracao", 0.9)),
        "revisado_humano": bool(faq_meta.get("revisado_humano", False)),
    }


def _sparse_to_qdrant(sparse: dict[int, float]) -> qmodels.SparseVector:
    return qmodels.SparseVector(indices=list(sparse.keys()), values=list(sparse.values()))


def _build_vector_payload(encoded) -> dict[str, Any]:
    vectors: dict[str, Any] = {
        DENSE_NAME: encoded.dense,
        SPARSE_NAME: _sparse_to_qdrant(encoded.sparse),
    }
    if encoded.colbert is not None:
        vectors[COLBERT_NAME] = encoded.colbert
    return vectors


async def upsert_text_chunks(text_chunks: list[TextChunk], faq_meta: dict[str, Any]) -> int:
    if not text_chunks:
        return 0
    ids = [c.chunk_id for c in text_chunks]
    texts = [text_chunk_text_for_embedding(c) for c in text_chunks]
    encoded = await encode_chunks(ids, texts, with_colbert=False)
    points = []
    for chunk, vec in zip(text_chunks, encoded, strict=False):
        points.append(
            qmodels.PointStruct(
                id=make_point_id(chunk.chunk_id),
                vector=_build_vector_payload(vec),
                payload=_build_text_payload(chunk, faq_meta),
            )
        )
    await upsert_chunks(points)
    return len(points)


async def upsert_image_chunks(image_chunks: list[ImageChunk]) -> int:
    if not image_chunks:
        return 0
    ids = [c.chunk_id for c in image_chunks]
    texts = [image_chunk_text_for_embedding(c.payload_extra) for c in image_chunks]
    encoded = await encode_chunks(ids, texts, with_colbert=False)
    points = []
    for chunk, vec in zip(image_chunks, encoded, strict=False):
        payload = dict(chunk.payload_extra)
        payload["chunk_id"] = chunk.chunk_id
        payload["data_indexacao"] = datetime.now(UTC).isoformat()
        payload["revisado_humano"] = bool(payload.get("revisado_humano", False))
        points.append(
            qmodels.PointStruct(
                id=make_point_id(chunk.chunk_id),
                vector=_build_vector_payload(vec),
                payload=payload,
            )
        )
    await upsert_chunks(points)
    return len(points)
