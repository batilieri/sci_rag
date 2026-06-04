"""Vetoriza a base RAG pré-extraída (chunks.jsonl) direto no Qdrant.

Lê o índice gerado por `prepare-rag-base.mjs` (D:\\sci\\...\\index\\chunks.jsonl),
embeda cada chunk com BGE-M3 (dense + sparse) e faz upsert com o mesmo formato de
payload que o ingester real produz. É o caminho rápido para popular a base de
conhecimento sem rodar o pipeline completo de PDF + visão.

Uso (dentro do container api):
    SCI_CHUNKS_PATH=/tmp/chunks.jsonl PYTHONPATH=/srv/app python scripts/ingest_sci_faq_jsonl.py

Flags via env:
    SCI_CHUNKS_PATH   caminho do chunks.jsonl (default /tmp/chunks.jsonl)
    SCI_BATCH         tamanho do lote de embedding/upsert (default 32)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from qdrant_client.http import models as qmodels

from app.rag.embeddings import encode_chunks
from app.storage.qdrant_client import (
    DENSE_NAME,
    SPARSE_NAME,
    ensure_collection,
    make_point_id,
    upsert_chunks,
)

CHUNKS_PATH = os.environ.get("SCI_CHUNKS_PATH", "/tmp/chunks.jsonl")
BATCH = int(os.environ.get("SCI_BATCH", "32"))


def slugify_categoria(categories: list[str] | None) -> str:
    """'Sped ECF' -> 'sped_ecf'. Usa a primeira categoria como principal."""
    if not categories:
        return "sci"
    raw = categories[0]
    norm = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    norm = re.sub(r"[^a-zA-Z0-9]+", "_", norm).strip("_").lower()
    return norm or "sci"


def embedding_text(chunk: dict[str, Any]) -> str:
    """Texto enriquecido para o embedding: título da FAQ + corpo do chunk."""
    title = chunk.get("title") or ""
    text = chunk.get("text") or ""
    parts = [title, text]
    return "\n".join(p for p in parts if p).strip()


def build_payload(chunk: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    categories = chunk.get("categories") or []
    return {
        "chunk_id": chunk["chunk_id"],
        "tipo_chunk": "texto",
        "chunk_tipo": "procedimento",
        "faq_id": str(chunk.get("faq_id")),
        "faq_titulo": chunk.get("title"),
        "categoria_principal": slugify_categoria(categories),
        "categorias_secundarias": categories[1:] if len(categories) > 1 else [],
        "sistema": "SCI",
        "titulo_secao": chunk.get("title"),
        "texto_original": chunk.get("text") or "",
        "texto_enriquecido_para_embedding": embedding_text(chunk),
        "registros_sped_mencionados": [],
        "menus_caminhos": [],
        "campos_interface": [],
        "palavras_chave_exatas": [],
        "imagens_associadas": chunk.get("image_refs") or [],
        "data_indexacao": now,
        "fonte": {
            "documento": chunk.get("source_pdf"),
            "url_original": chunk.get("source_url"),
            "pagina_pdf": chunk.get("page_start"),
        },
        "confianca_extracao": 0.9,
        "revisado_humano": False,
    }


def load_chunks(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


async def main() -> None:
    await ensure_collection()

    chunks = load_chunks(CHUNKS_PATH)
    print(f"Lidos {len(chunks)} chunks de {CHUNKS_PATH}")

    total = 0
    for start in range(0, len(chunks), BATCH):
        batch = chunks[start : start + BATCH]
        ids = [c["chunk_id"] for c in batch]
        texts = [embedding_text(c) for c in batch]
        encoded = await encode_chunks(ids, texts, with_colbert=False)

        points: list[qmodels.PointStruct] = []
        for c, enc in zip(batch, encoded, strict=True):
            points.append(
                qmodels.PointStruct(
                    id=make_point_id(c["chunk_id"]),
                    vector={
                        DENSE_NAME: enc.dense,
                        SPARSE_NAME: qmodels.SparseVector(
                            indices=list(enc.sparse.keys()),
                            values=list(enc.sparse.values()),
                        ),
                    },
                    payload=build_payload(c),
                )
            )
        await upsert_chunks(points)
        total += len(points)
        print(f"  upsert {total}/{len(chunks)}")

    print(f"Concluído: {total} chunks vetorizados no Qdrant.")


if __name__ == "__main__":
    asyncio.run(main())
