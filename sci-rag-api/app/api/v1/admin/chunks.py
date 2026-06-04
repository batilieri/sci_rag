"""Admin CRUD for chunks stored in Qdrant."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from qdrant_client.http import models as qmodels

from app.core.logging import get_logger
from app.core.security import ApiKeyContext, require_admin_read, require_admin_write
from app.ingestion.chunker import TextChunk
from app.ingestion.vectorizer import upsert_text_chunks
from app.schemas.chunk import (
    ChunkApprovalResponse,
    ChunkDetailResponse,
    ChunkListItem,
    ChunkListResponse,
    ChunkUpdateRequest,
)
from app.schemas.common import ChunkTipoSemantico, TipoChunk
from app.storage import qdrant_client as qdrant

logger = get_logger(__name__)
router = APIRouter(prefix="/v1/admin/chunks", tags=["admin:chunks"])


def _record_to_list_item(record: qmodels.Record) -> ChunkListItem:
    payload = record.payload or {}
    return ChunkListItem(
        chunk_id=payload.get("chunk_id") or str(record.id),
        tipo_chunk=TipoChunk(payload.get("tipo_chunk", "texto")),
        faq_id=payload.get("faq_id", ""),
        titulo=payload.get("faq_titulo") or payload.get("titulo_janela") or "",
        categoria_principal=payload.get("categoria_principal"),
        revisado_humano=bool(payload.get("revisado_humano", False)),
        confianca_extracao=float(payload.get("confianca_extracao") or 0.0),
        data_indexacao=_parse_dt(payload.get("data_indexacao")),
    )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


@router.get("", response_model=ChunkListResponse)
async def list_chunks(
    auth: Annotated[ApiKeyContext, Depends(require_admin_read)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    faq_id: Annotated[str | None, Query()] = None,
    categoria: Annotated[str | None, Query()] = None,
    tipo_chunk: Annotated[TipoChunk | None, Query()] = None,
    revisado_humano: Annotated[bool | None, Query()] = None,
) -> ChunkListResponse:
    must: list[qmodels.FieldCondition] = []
    if faq_id:
        must.append(qmodels.FieldCondition(key="faq_id", match=qmodels.MatchValue(value=faq_id)))
    if categoria:
        must.append(
            qmodels.FieldCondition(key="categoria_principal", match=qmodels.MatchValue(value=categoria))
        )
    if tipo_chunk:
        must.append(
            qmodels.FieldCondition(key="tipo_chunk", match=qmodels.MatchValue(value=tipo_chunk.value))
        )
    if revisado_humano is not None:
        must.append(
            qmodels.FieldCondition(
                key="revisado_humano", match=qmodels.MatchValue(value=revisado_humano)
            )
        )
    qfilter = qmodels.Filter(must=must) if must else None

    # Qdrant scroll uses an opaque offset token; we paginate by walking it `page-1` times.
    offset = None
    for _ in range(page - 1):
        _, offset = await qdrant.list_chunks(limit=page_size, offset=offset, qdrant_filter=qfilter)
        if offset is None:
            return ChunkListResponse(total=0, page=page, page_size=page_size, items=[])

    records, _ = await qdrant.list_chunks(limit=page_size, offset=offset, qdrant_filter=qfilter)
    items = [_record_to_list_item(r) for r in records]

    return ChunkListResponse(total=len(items), page=page, page_size=page_size, items=items)


@router.get("/{chunk_id}", response_model=ChunkDetailResponse)
async def get_chunk(
    chunk_id: str,
    auth: Annotated[ApiKeyContext, Depends(require_admin_read)],
) -> ChunkDetailResponse:
    payload = await qdrant.fetch_payload(chunk_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"erro": "chunk_inexistente", "mensagem": chunk_id},
        )
    tipo = TipoChunk(payload.get("tipo_chunk", "texto"))
    return ChunkDetailResponse(chunk_id=chunk_id, tipo_chunk=tipo, payload=payload)


@router.patch("/{chunk_id}", response_model=ChunkDetailResponse)
async def update_chunk(
    chunk_id: str,
    update: ChunkUpdateRequest,
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
) -> ChunkDetailResponse:
    payload = await qdrant.fetch_payload(chunk_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"erro": "chunk_inexistente", "mensagem": chunk_id},
        )

    tipo = TipoChunk(payload.get("tipo_chunk", "texto"))
    needs_reembed = False

    updated = dict(payload)
    if update.texto_original is not None:
        updated["texto_original"] = update.texto_original
        needs_reembed = True
    if update.texto_enriquecido_para_embedding is not None:
        updated["texto_enriquecido_para_embedding"] = update.texto_enriquecido_para_embedding
        needs_reembed = True
    if update.titulo_secao is not None:
        updated["titulo_secao"] = update.titulo_secao
    if update.chunk_tipo is not None:
        updated["chunk_tipo"] = update.chunk_tipo.value
    if update.menus_caminhos is not None:
        updated["menus_caminhos"] = update.menus_caminhos
    if update.campos_interface is not None:
        updated["campos_interface"] = update.campos_interface
    if update.palavras_chave_exatas is not None:
        updated["palavras_chave_exatas"] = update.palavras_chave_exatas
    if update.quando_enviar is not None:
        updated["quando_enviar"] = update.quando_enviar
    if update.descricao_vision_llm is not None:
        updated["descricao_vision_llm"] = update.descricao_vision_llm
    if update.confianca_extracao is not None:
        updated["confianca_extracao"] = update.confianca_extracao
    if update.revisado_humano is not None:
        updated["revisado_humano"] = update.revisado_humano
    if update.extra_payload:
        updated.update(update.extra_payload)

    if needs_reembed and tipo == TipoChunk.TEXTO:
        chunk = TextChunk(
            chunk_id=chunk_id,
            parent_chunk_id=updated.get("parent_chunk_id"),
            faq_id=updated.get("faq_id", ""),
            chunk_index=int(updated.get("chunk_index", 0)),
            chunk_total=int(updated.get("chunk_total", 1)),
            chunk_tipo=_safe_chunk_tipo(updated.get("chunk_tipo")),
            titulo_secao=updated.get("titulo_secao"),
            texto_original=updated.get("texto_original", ""),
            texto_enriquecido_para_embedding=updated.get(
                "texto_enriquecido_para_embedding", updated.get("texto_original", "")
            ),
            payload_extra={
                "registros_sped_mencionados": updated.get("registros_sped_mencionados") or [],
                "menus_caminhos": updated.get("menus_caminhos") or [],
                "campos_interface": updated.get("campos_interface") or [],
                "palavras_chave_exatas": updated.get("palavras_chave_exatas") or [],
            },
        )
        await upsert_text_chunks([chunk], updated)
    else:
        # Re-encode is not required; just update payload via a setPayload call.
        from app.config import get_settings as _gs

        client = qdrant.get_qdrant()
        point_id = qdrant.make_point_id(chunk_id)
        await client.set_payload(
            collection_name=_gs().qdrant_collection,
            payload=updated,
            points=[point_id],
            wait=True,
        )

    return ChunkDetailResponse(chunk_id=chunk_id, tipo_chunk=tipo, payload=updated)


def _safe_chunk_tipo(value: Any) -> ChunkTipoSemantico | None:
    if not value:
        return None
    try:
        return ChunkTipoSemantico(value)
    except ValueError:
        return None


@router.delete("/{chunk_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chunk(
    chunk_id: str,
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
) -> None:
    payload = await qdrant.fetch_payload(chunk_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"erro": "chunk_inexistente", "mensagem": chunk_id},
        )
    await qdrant.delete_chunks([chunk_id])


@router.post("/{chunk_id}/approve", response_model=ChunkApprovalResponse)
async def approve_chunk(
    chunk_id: str,
    auth: Annotated[ApiKeyContext, Depends(require_admin_write)],
) -> ChunkApprovalResponse:
    payload = await qdrant.fetch_payload(chunk_id)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"erro": "chunk_inexistente", "mensagem": chunk_id},
        )
    payload["revisado_humano"] = True
    payload["confianca_extracao"] = max(float(payload.get("confianca_extracao") or 0.0), 1.0)

    client = qdrant.get_qdrant()
    from app.config import get_settings

    settings = get_settings()
    await client.set_payload(
        collection_name=settings.qdrant_collection,
        payload=payload,
        points=[qdrant.make_point_id(chunk_id)],
        wait=True,
    )
    return ChunkApprovalResponse(chunk_id=chunk_id, updated_at=datetime.now(UTC))
