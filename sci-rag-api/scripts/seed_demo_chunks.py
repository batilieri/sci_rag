"""Seed a handful of synthetic SCI FAQ text chunks straight into Qdrant.

This is a DEMO/dev helper to exercise the answer path without running the full
PDF ingestion pipeline. It embeds each chunk with BGE-M3 (dense + sparse) and
upserts it with the same payload shape the real ingester produces.

Usage (inside the api container):
    PYTHONPATH=/srv/app python scripts/seed_demo_chunks.py
"""

from __future__ import annotations

import asyncio

from qdrant_client.http import models as qmodels

from app.rag.embeddings import encode_chunks
from app.storage.qdrant_client import (
    DENSE_NAME,
    SPARSE_NAME,
    ensure_collection,
    make_point_id,
    upsert_chunks,
)

# (chunk_id, faq_id, faq_titulo, titulo_secao, texto, menus, campos, sped, keywords)
DEMO_CHUNKS = [
    {
        "chunk_id": "faq_7085_chunk_001",
        "faq_id": "7085",
        "faq_titulo": "Registros K300/K315 do Bloco K aparecem cinza (desabilitados)",
        "categoria_principal": "sped_fiscal",
        "titulo_secao": "Habilitar registros do Bloco K",
        "texto_original": (
            "Quando as opcoes K300 e K315 aparecem em cinza (desabilitadas) na tela do Bloco K, "
            "significa que o periodo da escrituracao ainda nao esta com o controle de estoque "
            "habilitado. Para liberar, acesse Movimento > SPED Fiscal > Parametros do Bloco K e "
            "marque a opcao 'Escriturar Bloco K completo'. Depois selecione o periodo e clique em "
            "Recalcular. Os registros K300 (estoque escriturado de terceiros) e K315 (estoque de "
            "terceiros em poder da empresa) ficam habilitados apos o recalculo."
        ),
        "menus_caminhos": ["Movimento > SPED Fiscal > Parametros do Bloco K"],
        "campos_interface": ["Escriturar Bloco K completo", "Recalcular"],
        "registros_sped_mencionados": ["K300", "K315"],
        "palavras_chave_exatas": ["K300", "K315", "Bloco K", "cinza", "desabilitado"],
    },
    {
        "chunk_id": "faq_7085_chunk_002",
        "faq_id": "7085",
        "faq_titulo": "Registros K300/K315 do Bloco K aparecem cinza (desabilitados)",
        "categoria_principal": "sped_fiscal",
        "titulo_secao": "Quando o recalculo nao habilita",
        "texto_original": (
            "Se apos marcar 'Escriturar Bloco K completo' e recalcular os registros K300 e K315 "
            "continuarem cinza, verifique se o perfil da empresa no SPED Fiscal e o perfil A. "
            "Perfis B e C nao escrituram esses registros de estoque de terceiros. O perfil e "
            "definido em Cadastros > Empresa > SPED Fiscal, campo 'Perfil de apresentacao'."
        ),
        "menus_caminhos": ["Cadastros > Empresa > SPED Fiscal"],
        "campos_interface": ["Perfil de apresentacao"],
        "registros_sped_mencionados": ["K300", "K315"],
        "palavras_chave_exatas": ["K300", "K315", "perfil A", "estoque de terceiros"],
    },
]


async def main() -> None:
    await ensure_collection()

    chunk_ids = [c["chunk_id"] for c in DEMO_CHUNKS]
    texts = [f"{c['faq_titulo']}\n{c['titulo_secao']}\n{c['texto_original']}" for c in DEMO_CHUNKS]
    encoded = await encode_chunks(chunk_ids, texts, with_colbert=False)

    points: list[qmodels.PointStruct] = []
    for c, enc in zip(DEMO_CHUNKS, encoded, strict=True):
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
                payload={
                    "chunk_id": c["chunk_id"],
                    "tipo_chunk": "texto",
                    "chunk_tipo": "procedimento",
                    "faq_id": c["faq_id"],
                    "faq_titulo": c["faq_titulo"],
                    "categoria_principal": c["categoria_principal"],
                    "titulo_secao": c["titulo_secao"],
                    "texto_original": c["texto_original"],
                    "menus_caminhos": c["menus_caminhos"],
                    "campos_interface": c["campos_interface"],
                    "registros_sped_mencionados": c["registros_sped_mencionados"],
                    "palavras_chave_exatas": c["palavras_chave_exatas"],
                    "revisado_humano": True,
                    "fonte": {"url_original": None},
                },
            )
        )

    await upsert_chunks(points)
    print(f"Seeded {len(points)} demo chunks into Qdrant.")


if __name__ == "__main__":
    asyncio.run(main())
