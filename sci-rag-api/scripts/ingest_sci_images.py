"""Ingere as imagens (screenshots) das FAQs para que o bot possa enviá-las.

Para cada imagem do catálogo `images.jsonl` (gerado por prepare-rag-base.mjs):
  1. Faz upload do PNG no object storage (MinIO local / R2 em prod).
  2. Cria/atualiza a linha em `rag_image_assets` (Postgres) — dedupe por sha256.
  3. Indexa um image chunk no Qdrant (tipo_chunk=imagem) com descrição sintetizada
     a partir do título da FAQ + contexto do texto ao redor da imagem, para que a
     busca recupere a tela e o LLM decida enviá-la (campo `quando_enviar`).

NÃO roda visão/OCR — usa o texto de contexto que já temos. É suficiente para o
agente relacionar a imagem à FAQ e mandá-la quando o cliente pedir a tela.

Uso (dentro do container api):
    SCI_IMAGES_JSONL=/tmp/sci_rag/index/images.jsonl \
    SCI_IMAGES_BASE=/tmp/sci_rag \
    PYTHONPATH=/srv/app python scripts/ingest_sci_images.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from app.ingestion.chunker import build_image_chunk
from app.ingestion.storage_client import upsert_image_asset
from app.ingestion.vectorizer import upsert_image_chunks
from app.storage.postgres import get_sessionmaker
from app.storage.qdrant_client import ensure_collection

IMAGES_JSONL = os.environ.get("SCI_IMAGES_JSONL", "/tmp/sci_rag/index/images.jsonl")
IMAGES_BASE = Path(os.environ.get("SCI_IMAGES_BASE", "/tmp/sci_rag"))
BATCH = int(os.environ.get("SCI_BATCH", "16"))

_MENU_RE = re.compile(r"(?:Acesse o menu|menu)\s*:?\s*([^\n.]+)", re.IGNORECASE)


def fix_mojibake(s: str | None) -> str:
    """Conserta texto UTF-8 duplo-codificado (ex.: 'contÃ¡bil' -> 'contábil')."""
    if not s:
        return ""
    try:
        repaired = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    # Só aceita o conserto se reduziu a presença dos marcadores típicos de mojibake.
    if repaired.count("Ã") + repaired.count("Â") < s.count("Ã") + s.count("Â"):
        return repaired
    return s


def menu_from_context(after: str) -> str:
    m = _MENU_RE.search(after or "")
    if not m:
        return ""
    return m.group(1).strip(" .>")


def build_description(rec: dict[str, Any]) -> dict[str, Any]:
    title = fix_mojibake(rec.get("title"))
    before = fix_mojibake((rec.get("context") or {}).get("before"))
    after = fix_mojibake((rec.get("context") or {}).get("after"))
    menu = menu_from_context(after)

    # Descrição textual da tela a partir do contexto ao redor da imagem.
    contexto = " ".join(p for p in [before[-300:], after[:300]] if p).strip()
    descricao = f"Captura de tela do procedimento: {title}."
    if contexto:
        descricao += f" Contexto: {contexto}"

    return {
        "ordem_no_faq": rec.get("order_on_page"),
        "tipo_tela": "tela_sistema",
        "titulo_janela": title,
        "descricao_curta": title,
        "descricao_vision_llm": descricao,
        "menu_caminho_inferido": menu,
        "ocr_texto_completo": "",
        "registros_sped_visiveis": [],
        "palavras_chave_exatas": [],
        "quando_enviar": [
            f"Quando o cliente pedir para ver a tela ou o passo a passo de: {title}"
        ],
        "confianca_ocr": 0.0,
    }


def load_rows(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


async def main() -> None:
    await ensure_collection()
    rows = load_rows(IMAGES_JSONL)
    print(f"Lidas {len(rows)} imagens de {IMAGES_JSONL}")

    maker = get_sessionmaker()
    total = 0
    skipped = 0
    pending: list[Any] = []

    async with maker() as session:
        for rec in rows:
            rel = rec.get("file")
            if not rel:
                skipped += 1
                continue
            img_path = IMAGES_BASE / rel
            if not img_path.exists():
                print(f"  [skip] arquivo nao encontrado: {img_path}")
                skipped += 1
                continue

            body = img_path.read_bytes()
            faq_id = str(rec.get("faq_id"))
            image_id = rec["image_id"]
            desc = build_description(rec)

            record = await upsert_image_asset(
                session,
                faq_id=faq_id,
                image_id=image_id,
                body=body,
                width=int(rec.get("width") or 0),
                height=int(rec.get("height") or 0),
                description=desc,
                original_filename=Path(rel).name,
            )

            chunk = build_image_chunk(
                faq_id=faq_id,
                image_asset_id=record.image_asset_id,
                description_json=desc,
                storage={
                    "public_url": record.public_url,
                    "key": record.key,
                    "bucket": record.bucket,
                    "etag": record.etag,
                },
                filename=Path(rel).name,
                tamanho_bytes=record.size_bytes,
                width=record.width,
                height=record.height,
                hash_md5=record.md5,
            )
            pending.append(chunk)

            if len(pending) >= BATCH:
                await session.commit()
                await upsert_image_chunks(pending)
                total += len(pending)
                pending = []
                print(f"  upsert {total}/{len(rows)}")

        await session.commit()
        if pending:
            await upsert_image_chunks(pending)
            total += len(pending)
            print(f"  upsert {total}/{len(rows)}")

    print(f"Concluído: {total} imagens indexadas, {skipped} ignoradas.")


if __name__ == "__main__":
    asyncio.run(main())
