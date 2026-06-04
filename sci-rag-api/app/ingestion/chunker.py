"""Hierarchical chunking: parent (FAQ-wide), child (semantic sections), image (one per screenshot).

Sections in the structured FAQ JSON drive `child` chunks. When a section exceeds the soft token
budget, it is split on paragraph boundaries. Tokens are estimated as ~4 chars per token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.common import ChunkTipoSemantico, TipoChunk

CHILD_MIN_TOKENS = 200
CHILD_MAX_TOKENS = 400
CHARS_PER_TOKEN = 4


@dataclass(slots=True)
class TextChunk:
    chunk_id: str
    parent_chunk_id: str | None
    faq_id: str
    chunk_index: int
    chunk_total: int
    chunk_tipo: ChunkTipoSemantico | None
    titulo_secao: str | None
    texto_original: str
    texto_enriquecido_para_embedding: str
    payload_extra: dict[str, Any] = field(default_factory=dict)
    tipo_chunk: TipoChunk = TipoChunk.TEXTO


@dataclass(slots=True)
class ImageChunk:
    chunk_id: str
    faq_id: str
    image_asset_id: str
    payload_extra: dict[str, Any]
    tipo_chunk: TipoChunk = TipoChunk.IMAGEM


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in paragraphs if p.strip()]


def _pack_paragraphs(paragraphs: list[str], max_tokens: int, min_tokens: int) -> list[str]:
    """Greedy packing of paragraphs into chunks within the [min, max] token budget."""
    chunks: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for p in paragraphs:
        ptokens = _estimate_tokens(p)
        if buf_tokens + ptokens > max_tokens and buf_tokens >= min_tokens:
            chunks.append("\n\n".join(buf))
            buf, buf_tokens = [p], ptokens
            continue
        if ptokens > max_tokens:
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_tokens = [], 0
            for segment in _hard_split(p, max_tokens):
                chunks.append(segment)
            continue
        buf.append(p)
        buf_tokens += ptokens
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _hard_split(text: str, max_tokens: int) -> list[str]:
    max_chars = max_tokens * CHARS_PER_TOKEN
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    buf = ""
    for s in sentences:
        if len(buf) + len(s) + 1 > max_chars:
            if buf:
                out.append(buf.strip())
            buf = s
        else:
            buf = f"{buf} {s}".strip() if buf else s
    if buf:
        out.append(buf.strip())
    return out


def _coerce_chunk_tipo(value: Any) -> ChunkTipoSemantico | None:
    if value is None:
        return None
    if isinstance(value, ChunkTipoSemantico):
        return value
    try:
        return ChunkTipoSemantico(value)
    except ValueError:
        return None


def build_text_chunks(structured_faq: dict[str, Any]) -> list[TextChunk]:
    """Generate parent + child text chunks from the LLM-structured FAQ JSON."""
    faq_id = str(structured_faq["faq_id"])
    secoes = structured_faq.get("secoes") or []

    # Parent chunk: the full FAQ joined together.
    parent_chunk_id = f"faq_{faq_id}_parent"
    parent_text = "\n\n".join(
        f"{s.get('titulo_secao') or ''}\n{s.get('texto_original') or ''}".strip()
        for s in secoes
    ).strip()
    parent_chunk = TextChunk(
        chunk_id=parent_chunk_id,
        parent_chunk_id=None,
        faq_id=faq_id,
        chunk_index=0,
        chunk_total=0,  # filled after children
        chunk_tipo=None,
        titulo_secao=None,
        texto_original=parent_text,
        texto_enriquecido_para_embedding=parent_text,
        payload_extra={"is_parent": True},
    )

    children: list[TextChunk] = []
    index_counter = 1
    for sec in secoes:
        titulo = sec.get("titulo_secao")
        chunk_tipo = _coerce_chunk_tipo(sec.get("chunk_tipo"))
        original = sec.get("texto_original") or ""
        enriquecido = sec.get("texto_enriquecido_para_embedding") or original

        paragraphs_original = _split_paragraphs(original)
        packed_originals = _pack_paragraphs(paragraphs_original, CHILD_MAX_TOKENS, CHILD_MIN_TOKENS) or [
            original
        ]
        paragraphs_enriquecido = _split_paragraphs(enriquecido)
        packed_enriched = _pack_paragraphs(
            paragraphs_enriquecido, CHILD_MAX_TOKENS, CHILD_MIN_TOKENS
        ) or [enriquecido]

        # Align list lengths: pad with the last enriched piece.
        max_n = max(len(packed_originals), len(packed_enriched))
        while len(packed_originals) < max_n:
            packed_originals.append(packed_originals[-1])
        while len(packed_enriched) < max_n:
            packed_enriched.append(packed_enriched[-1])

        for orig, enr in zip(packed_originals, packed_enriched, strict=False):
            child_id = f"faq_{faq_id}_chunk_{index_counter:03d}"
            children.append(
                TextChunk(
                    chunk_id=child_id,
                    parent_chunk_id=parent_chunk_id,
                    faq_id=faq_id,
                    chunk_index=index_counter,
                    chunk_total=0,  # filled below
                    chunk_tipo=chunk_tipo,
                    titulo_secao=titulo,
                    texto_original=orig,
                    texto_enriquecido_para_embedding=enr,
                    payload_extra={
                        "registros_sped_mencionados": sec.get("registros_sped_mencionados") or [],
                        "menus_caminhos": sec.get("menus_caminhos") or [],
                        "campos_interface": sec.get("campos_interface") or [],
                        "palavras_chave_exatas": sec.get("palavras_chave_exatas") or [],
                    },
                )
            )
            index_counter += 1

    total = len(children) + 1
    parent_chunk.chunk_total = total
    for c in children:
        c.chunk_total = total
    return [parent_chunk, *children]


def build_image_chunk(
    faq_id: str,
    image_asset_id: str,
    description_json: dict[str, Any],
    storage: dict[str, Any],
    *,
    filename: str,
    tamanho_bytes: int,
    width: int,
    height: int,
    hash_md5: str | None,
) -> ImageChunk:
    payload = {
        "tipo_chunk": TipoChunk.IMAGEM.value,
        "faq_id": faq_id,
        "filename": filename,
        "image_asset_id": image_asset_id,
        "storage_url": storage.get("public_url"),
        "storage_path_interno": storage.get("key"),
        "r2_bucket": storage.get("bucket"),
        "r2_key": storage.get("key"),
        "r2_public_url": storage.get("public_url"),
        "r2_etag": storage.get("etag"),
        "hash_md5": hash_md5,
        "tamanho_bytes": tamanho_bytes,
        "dimensoes": {"largura": width, "altura": height},
        "tipo_tela": description_json.get("tipo_tela"),
        "titulo_janela": description_json.get("titulo_janela"),
        "menu_caminho_inferido": description_json.get("menu_caminho_inferido"),
        "descricao_vision_llm": description_json.get("descricao_vision_llm"),
        "ocr_texto_completo": description_json.get("ocr_texto_completo"),
        "elementos_ui_identificados": description_json.get("elementos_ui_identificados") or [],
        "elementos_destacados_visualmente": description_json.get("elementos_destacados_visualmente") or [],
        "registros_sped_visiveis": description_json.get("registros_sped_visiveis") or [],
        "palavras_chave_exatas": description_json.get("palavras_chave_exatas") or [],
        "quando_enviar": description_json.get("quando_enviar") or [],
        "confianca_ocr": float(description_json.get("confianca_ocr") or 0.0),
    }
    return ImageChunk(
        chunk_id=f"faq_{faq_id}_img_{image_asset_id}",
        faq_id=faq_id,
        image_asset_id=image_asset_id,
        payload_extra=payload,
    )


def text_chunk_text_for_embedding(chunk: TextChunk) -> str:
    """Concatenate fields that we want the embedding to capture."""
    extras = chunk.payload_extra
    parts = [
        chunk.titulo_secao or "",
        chunk.texto_enriquecido_para_embedding,
        " ".join(extras.get("registros_sped_mencionados") or []),
        " ".join(extras.get("menus_caminhos") or []),
        " ".join(extras.get("palavras_chave_exatas") or []),
    ]
    return "\n".join(p for p in parts if p).strip()


def image_chunk_text_for_embedding(payload: dict[str, Any]) -> str:
    parts = [
        payload.get("titulo_janela") or "",
        payload.get("menu_caminho_inferido") or "",
        payload.get("descricao_vision_llm") or "",
        payload.get("ocr_texto_completo") or "",
        " ".join(payload.get("registros_sped_visiveis") or []),
        " ".join(payload.get("palavras_chave_exatas") or []),
        " ".join(payload.get("quando_enviar") or []),
    ]
    return "\n".join(p for p in parts if p).strip()
