"""Leitura de anexos enviados no chat (one-off): extrai TEXTO de PDF/TXT.

Diferente da ingestão (`pipeline.py`), aqui o conteúdo NÃO é gravado na base — só é
usado para entender a pergunta e então buscar nas informações já salvas. Imagens são
tratadas à parte (Claude vision), no engine.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.ingestion.pdf_extractor import _normalize_image_to_png

logger = get_logger(__name__)

MAX_PDF_IMAGES = 3


def read_pdf(pdf_bytes: bytes) -> tuple[str, list[bytes]]:
    """Devolve (texto_concatenado, [pngs]) de um PDF em memória, via PyMuPDF.

    Os PNGs (poucos) só são usados se o texto for escasso (PDF escaneado).
    """
    import pymupdf  # type: ignore[import-untyped]

    text_parts: list[str] = []
    images: list[bytes] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            text_parts.append(page.get_text("text") or "")
            if len(images) >= MAX_PDF_IMAGES:
                continue
            for img in page.get_images(full=True):
                if len(images) >= MAX_PDF_IMAGES:
                    break
                try:
                    base = doc.extract_image(img[0])
                except Exception:
                    continue
                raw = base.get("image") or b""
                if not raw:
                    continue
                try:
                    png, _w, _h = _normalize_image_to_png(raw, ext_hint=base.get("ext") or "png")
                except Exception:
                    continue
                images.append(png)
    finally:
        doc.close()
    return "\n".join(text_parts).strip(), images


def read_txt(txt_bytes: bytes) -> str:
    """Decodifica TXT tentando UTF-8 e Latin-1 antes de cair para 'replace'."""
    for enc in ("utf-8", "latin-1"):
        try:
            return txt_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return txt_bytes.decode("utf-8", errors="replace")
