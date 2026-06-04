"""
Pipeline completo de ingestão de PDF para Qdrant.

Estrutura:
    1. Extrai PDF com Docling (estrutura) + PyMuPDF (imagens)
    2. Identifica blocos de FAQ por regex/heurística
    3. Envia cada FAQ para LLM (PROMPT_01) → JSON estruturado
    4. Envia cada imagem para Vision LLM (PROMPT_02) → descrição rica
    5. Gera embeddings BGE-M3 (denso + esparso + colbert)
    6. Upserta no Qdrant + salva imagens no Object Storage

Uso:
    python ingest_pdf.py --pdf ./FAQ_SCI.pdf --collection sci_faq_ecd_ecf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from docling.document_converter import DocumentConverter
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVector,
    VectorParams,
    SparseVectorParams,
    MultiVectorConfig,
    MultiVectorComparator,
)

# LLM clients
from anthropic import Anthropic  # Claude Sonnet 4.5
from openai import OpenAI         # DeepSeek (compatível OpenAI)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════

QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = "sci_faq_ecd_ecf"
OBJECT_STORAGE_BASE = "https://storage.sci.com/sci/faq"  # placeholder
EMBEDDING_DIM = 1024  # BGE-M3

# Carregue os prompts do disco (PROMPT_01 e PROMPT_02 que criamos)
PROMPT_EXTRACAO = Path("prompts/PROMPT_01_extracao_estruturada.md").read_text(encoding="utf-8")
PROMPT_VISION = Path("prompts/PROMPT_02_descricao_imagem.md").read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ImagemExtraida:
    id: str
    faq_id: str
    pagina_pdf: int
    bytes_imagem: bytes
    hash_md5: str
    width: int
    height: int
    contexto_textual_proximo: str = ""
    descricao_estruturada: dict | None = None
    storage_url: str = ""


@dataclass
class FAQExtraida:
    faq_id: str
    titulo_bruto: str
    bloco_completo_texto: str
    pagina_inicio: int
    pagina_fim: int
    imagens: list[ImagemExtraida] = field(default_factory=list)
    json_estruturado: dict | None = None


# ═══════════════════════════════════════════════════════════════════
# FASE 1: EXTRAÇÃO DO PDF
# ═══════════════════════════════════════════════════════════════════

def extrair_pdf(pdf_path: Path) -> tuple[str, list[ImagemExtraida]]:
    """
    Usa Docling para extrair texto estruturado preservando hierarquia.
    Usa PyMuPDF para extrair imagens com posição.
    """
    log.info(f"Extraindo PDF: {pdf_path}")

    # Docling: melhor estrutura
    converter = DocumentConverter()
    docling_result = converter.convert(pdf_path)
    texto_estruturado = docling_result.document.export_to_markdown()

    # PyMuPDF: extração precisa de imagens
    imagens_brutas: list[ImagemExtraida] = []
    doc = fitz.open(pdf_path)
    for page_idx, page in enumerate(doc):
        for img_idx, img in enumerate(page.get_images(full=True)):
            xref = img[0]
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            img_hash = hashlib.md5(img_bytes).hexdigest()

            # Contexto textual: 200 chars antes e depois da imagem na página
            page_text = page.get_text()
            contexto = page_text[:500]  # simplificado; em produção, calcular bbox

            imagens_brutas.append(ImagemExtraida(
                id=f"img_pg{page_idx+1}_idx{img_idx}_{img_hash[:8]}",
                faq_id="",  # preenchido depois ao associar com FAQ
                pagina_pdf=page_idx + 1,
                bytes_imagem=img_bytes,
                hash_md5=img_hash,
                width=base_image.get("width", 0),
                height=base_image.get("height", 0),
                contexto_textual_proximo=contexto,
            ))

    doc.close()
    log.info(f"Extraído: {len(texto_estruturado)} chars de texto, {len(imagens_brutas)} imagens")
    return texto_estruturado, imagens_brutas


def identificar_blocos_faq(texto_completo: str, imagens: list[ImagemExtraida]) -> list[FAQExtraida]:
    """
    Heurística para separar FAQs.
    Padrão observado no PDF SCI: "NNNN - Título da FAQ" seguido de
    "(modulo/faq/faq.php?faqId=NNNN&sistemaId=54)"
    """
    pattern = re.compile(
        r'(?P<faq_id>\d{4,5})\s*-\s*(?P<titulo>[^\n]+?)\?\s*\n'
        r'\(modulo/faq/faq\.php\?faqId=(?P=faq_id)&sistemaId=\d+\)',
        re.MULTILINE
    )

    matches = list(pattern.finditer(texto_completo))
    log.info(f"Identificados {len(matches)} blocos de FAQ")

    faqs: list[FAQExtraida] = []
    for i, m in enumerate(matches):
        inicio = m.start()
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto_completo)
        bloco = texto_completo[inicio:fim]

        faqs.append(FAQExtraida(
            faq_id=m.group("faq_id"),
            titulo_bruto=m.group("titulo").strip(),
            bloco_completo_texto=bloco,
            pagina_inicio=0,  # calcular via offsets se necessário
            pagina_fim=0,
        ))

    # Associar imagens aos FAQs (heurística simples por proximidade de página)
    # Em produção, melhorar com bbox-aware matching
    for img in imagens:
        # Achar FAQ que provavelmente contém essa imagem
        # Aqui: associa pela página mais próxima — simplificação
        if faqs:
            img.faq_id = faqs[min(len(faqs)-1, img.pagina_pdf // 2)].faq_id
            for faq in faqs:
                if faq.faq_id == img.faq_id:
                    faq.imagens.append(img)
                    break

    return faqs


# ═══════════════════════════════════════════════════════════════════
# FASE 2: LLM — ESTRUTURAÇÃO COM PROMPT_01
# ═══════════════════════════════════════════════════════════════════

def estruturar_faq_com_llm(faq: FAQExtraida, client: Anthropic, modelo: str = "claude-sonnet-4-5-20250929") -> dict:
    """Chama Claude/DeepSeek com PROMPT_01."""

    # Monta lista de descrições de imagens (que já foram processadas antes)
    imgs_resumo = "\n".join([
        f"- {img.id}: {img.descricao_estruturada.get('descricao_vision_llm', '')[:200] if img.descricao_estruturada else 'pendente'}"
        for img in faq.imagens
    ])

    system = extrair_secao_system(PROMPT_EXTRACAO)
    user_template = extrair_secao_user(PROMPT_EXTRACAO)
    user_msg = user_template.format(
        faq_bloco_extraido_do_pdf=faq.bloco_completo_texto,
        lista_de_descricoes_de_imagens_com_seus_ids=imgs_resumo
    )

    response = client.messages.create(
        model=modelo,
        max_tokens=4096,
        temperature=0.0,
        system=system,
        messages=[{"role": "user", "content": user_msg}]
    )
    texto = response.content[0].text
    return json.loads(texto)


def descrever_imagem_com_llm(img: ImagemExtraida, faq_titulo: str, client: Anthropic) -> dict:
    """Chama Claude Sonnet 4.5 com PROMPT_02 para descrever a imagem."""
    import base64
    img_b64 = base64.b64encode(img.bytes_imagem).decode()

    system = extrair_secao_system(PROMPT_VISION)
    user_template = extrair_secao_user(PROMPT_VISION)
    user_msg = user_template.format(
        faq_titulo=faq_titulo,
        faq_id=img.faq_id,
        secao_atual="(inferir pela imagem)",
        contexto_textual_proximo=img.contexto_textual_proximo[:300]
    )

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=2048,
        temperature=0.1,
        system=system,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": user_msg}
            ]
        }]
    )
    return json.loads(response.content[0].text)


def extrair_secao_system(prompt_md: str) -> str:
    """Extrai bloco entre '## SYSTEM PROMPT' e próximo '##'."""
    m = re.search(r'## SYSTEM PROMPT\s*\n+```\s*\n(.*?)```', prompt_md, re.DOTALL)
    return m.group(1).strip() if m else ""


def extrair_secao_user(prompt_md: str) -> str:
    """Extrai bloco entre '## USER PROMPT' e próximo '##'."""
    m = re.search(r'## USER PROMPT.*?\n+```\s*\n(.*?)```', prompt_md, re.DOTALL)
    return m.group(1).strip() if m else ""


# ═══════════════════════════════════════════════════════════════════
# FASE 3: EMBEDDING
# ═══════════════════════════════════════════════════════════════════

_bge_model: BGEM3FlagModel | None = None

def get_embedding_model() -> BGEM3FlagModel:
    global _bge_model
    if _bge_model is None:
        log.info("Carregando BGE-M3...")
        _bge_model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)
    return _bge_model


def gerar_embeddings_trio(texto: str) -> dict:
    """Gera dense + sparse + colbert num só passe."""
    model = get_embedding_model()
    output = model.encode(
        [texto],
        batch_size=1,
        max_length=8192,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=True
    )
    return {
        "dense": output['dense_vecs'][0].tolist(),
        "sparse": output['lexical_weights'][0],  # dict {token_id: weight}
        "colbert": output['colbert_vecs'][0].tolist()
    }


# ═══════════════════════════════════════════════════════════════════
# FASE 4: QDRANT
# ═══════════════════════════════════════════════════════════════════

def criar_collection_se_nao_existe(client: QdrantClient, name: str):
    if client.collection_exists(name):
        log.info(f"Collection '{name}' já existe.")
        return

    log.info(f"Criando collection '{name}'...")
    client.create_collection(
        collection_name=name,
        vectors_config={
            "dense": VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            "colbert": VectorParams(
                size=128,
                distance=Distance.COSINE,
                multivector_config=MultiVectorConfig(comparator=MultiVectorComparator.MAX_SIM),
                hnsw_config={"m": 0}  # ColBERT só usado em reranking, não em busca direta
            ),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams()
        }
    )

    # Índices de payload para filtros rápidos
    for field_name, schema in [
        ("faq_id", "keyword"),
        ("categoria_principal", "keyword"),
        ("registros_sped_mencionados", "keyword"),
        ("chunk_tipo", "keyword"),
        ("tipo_chunk", "keyword"),
        ("revisado_humano", "bool"),
    ]:
        client.create_payload_index(collection_name=name, field_name=field_name, field_schema=schema)


def upsert_chunk(client: QdrantClient, point_id: str, embeddings: dict, payload: dict):
    sparse = SparseVector(
        indices=list(embeddings["sparse"].keys()),
        values=list(embeddings["sparse"].values())
    )
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(
            id=point_id,
            vector={
                "dense": embeddings["dense"],
                "sparse": sparse,
                "colbert": embeddings["colbert"],
            },
            payload=payload
        )]
    )


# ═══════════════════════════════════════════════════════════════════
# FASE 5: OBJECT STORAGE (placeholder — adapte para Oracle/MinIO)
# ═══════════════════════════════════════════════════════════════════

def upload_imagem(img: ImagemExtraida) -> str:
    """
    Em produção: usar oci.object_storage ou boto3 (S3-compatible) para MinIO.
    Aqui só salvamos local e retornamos URL fictícia.
    """
    local_dir = Path(f"./storage/{img.faq_id}")
    local_dir.mkdir(parents=True, exist_ok=True)
    local_file = local_dir / f"{img.id}.png"
    local_file.write_bytes(img.bytes_imagem)
    url = f"{OBJECT_STORAGE_BASE}/{img.faq_id}/{img.id}.png"
    return url


# ═══════════════════════════════════════════════════════════════════
# ORQUESTRADOR PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

def processar_pdf(pdf_path: Path, anthropic_key: str):
    qdrant = QdrantClient(url=QDRANT_URL)
    anthropic = Anthropic(api_key=anthropic_key)

    criar_collection_se_nao_existe(qdrant, COLLECTION_NAME)

    # 1. Extrai
    texto_estruturado, imagens = extrair_pdf(pdf_path)
    faqs = identificar_blocos_faq(texto_estruturado, imagens)

    # 2. Para cada FAQ: descreve imagens, estrutura texto, gera embeddings, upserta
    for faq in faqs:
        log.info(f"Processando FAQ {faq.faq_id} - {faq.titulo_bruto[:60]}...")

        # 2a. Descreve cada imagem PRIMEIRO (porque PROMPT_01 usa as descrições)
        for img in faq.imagens:
            try:
                img.descricao_estruturada = descrever_imagem_com_llm(img, faq.titulo_bruto, anthropic)
                img.storage_url = upload_imagem(img)
            except Exception as e:
                log.error(f"Erro descrevendo imagem {img.id}: {e}")
                continue

        # 2b. Estrutura o FAQ textual
        try:
            faq.json_estruturado = estruturar_faq_com_llm(faq, anthropic)
        except Exception as e:
            log.error(f"Erro estruturando FAQ {faq.faq_id}: {e}")
            continue

        # 2c. Insere chunks de texto
        for chunk in faq.json_estruturado.get("chunks", []):
            texto_emb = chunk["texto_enriquecido_para_embedding"]
            embeddings = gerar_embeddings_trio(texto_emb)

            payload = {
                "tipo_chunk": "texto",
                "faq_id": faq.json_estruturado["faq_id"],
                "faq_titulo": faq.json_estruturado["faq_titulo"],
                "categoria_principal": faq.json_estruturado["categoria_principal"],
                **chunk,
            }

            point_id = f"faq_{faq.faq_id}_chunk_{chunk['chunk_index']:03d}"
            upsert_chunk(qdrant, point_id, embeddings, payload)

        # 2d. Insere chunks de imagem
        for img in faq.imagens:
            if not img.descricao_estruturada:
                continue

            texto_emb = (
                f"{img.descricao_estruturada['descricao_vision_llm']} "
                f"Texto da tela: {img.descricao_estruturada['ocr_texto_completo']} "
                f"Palavras-chave: {' '.join(img.descricao_estruturada['palavras_chave_exatas'])} "
                f"Quando enviar: {' '.join(img.descricao_estruturada['quando_enviar'])}"
            )
            embeddings = gerar_embeddings_trio(texto_emb)

            payload = {
                "tipo_chunk": "imagem",
                "faq_id": faq.faq_id,
                "storage_url": img.storage_url,
                "hash_md5": img.hash_md5,
                **img.descricao_estruturada,
            }

            upsert_chunk(qdrant, img.id, embeddings, payload)

        log.info(f"✓ FAQ {faq.faq_id} processado: {len(faq.json_estruturado.get('chunks', []))} chunks de texto + {len(faq.imagens)} imagens")

    log.info("✓ Ingestão concluída.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--anthropic-key", type=str, required=True)
    args = parser.parse_args()
    processar_pdf(args.pdf, args.anthropic_key)
