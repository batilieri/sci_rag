"""PDF extraction: structured text via Docling + raw image extraction via PyMuPDF.

The output is a list of `FaqBlock`s, each containing the body text and a list of `RawImage`s
attached to that FAQ. Association heuristics:
  * detect FAQ headers (line containing 'faq_id=', 'FAQ id', or matching the SCI URL format).
  * each image is attached to the FAQ block whose page range it falls inside.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


FAQ_URL_RE = re.compile(
    r"https?://[^\s]*faqId=(\d+)",
    re.IGNORECASE,
)
FAQ_ID_LINE_RE = re.compile(r"\bFAQ\s*(?:ID|id|n[oº])\s*[:\-=]?\s*(\d+)", re.IGNORECASE)
TITLE_LIKE_RE = re.compile(r"^[A-Z][^\n]{8,}$", re.MULTILINE)


@dataclass(slots=True)
class RawImage:
    image_id: str
    page_index: int
    bytes_png: bytes
    width: int
    height: int
    hash_sha256: str
    hash_md5: str
    original_filename: str | None = None


@dataclass(slots=True)
class FaqBlock:
    faq_id: str
    titulo: str | None
    url_original: str | None
    raw_text: str
    page_start: int
    page_end: int
    images: list[RawImage] = field(default_factory=list)


def _hash(b: bytes) -> tuple[str, str]:
    return (
        hashlib.sha256(b).hexdigest(),
        hashlib.md5(b, usedforsecurity=False).hexdigest(),
    )


def _normalize_image_to_png(bytes_in: bytes, *, ext_hint: str | None = None) -> tuple[bytes, int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(bytes_in)) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        width, height = im.size
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), width, height


def _split_into_faq_blocks(pages: list[str], page_images: dict[int, list[RawImage]]) -> list[FaqBlock]:
    """Split the concatenated text into FAQ blocks using the recognized header pattern."""
    full_text = "\n".join(pages)
    block_positions: list[tuple[int, int, str, str | None]] = []  # (start, page_index, faq_id, url)

    cursor = 0
    for line in full_text.splitlines(keepends=True):
        m_url = FAQ_URL_RE.search(line)
        m_id = FAQ_ID_LINE_RE.search(line)
        faq_id: str | None = None
        url: str | None = None
        if m_url:
            faq_id = m_url.group(1)
            url = m_url.group(0)
        elif m_id:
            faq_id = m_id.group(1)
        if faq_id:
            # compute current page
            running = 0
            page_idx = 0
            for i, page in enumerate(pages):
                if running + len(page) >= cursor:
                    page_idx = i
                    break
                running += len(page) + 1
            if block_positions and faq_id == block_positions[-1][2] and cursor - block_positions[-1][0] < 1000:
                prev_start, prev_page, prev_id, prev_url = block_positions[-1]
                block_positions[-1] = (prev_start, prev_page, prev_id, prev_url or url)
                cursor += len(line)
                continue
            block_positions.append((cursor, page_idx, faq_id, url))
        cursor += len(line)

    if not block_positions:
        synthetic_id = uuid.uuid4().hex[:8]
        all_images: list[RawImage] = [img for imgs in page_images.values() for img in imgs]
        return [
            FaqBlock(
                faq_id=f"unknown_{synthetic_id}",
                titulo=None,
                url_original=None,
                raw_text=full_text.strip(),
                page_start=0,
                page_end=len(pages) - 1,
                images=all_images,
            )
        ]

    blocks: list[FaqBlock] = []
    for i, (start, page_idx, faq_id, url) in enumerate(block_positions):
        end = block_positions[i + 1][0] if i + 1 < len(block_positions) else len(full_text)
        text = full_text[start:end].strip()
        end_page = block_positions[i + 1][1] if i + 1 < len(block_positions) else len(pages) - 1
        title_match = TITLE_LIKE_RE.search(text[:400])
        titulo = title_match.group(0).strip() if title_match else None
        imgs: list[RawImage] = []
        for p in range(page_idx, end_page + 1):
            imgs.extend(page_images.get(p, []))
        blocks.append(
            FaqBlock(
                faq_id=faq_id,
                titulo=titulo,
                url_original=url,
                raw_text=text,
                page_start=page_idx,
                page_end=end_page,
                images=imgs,
            )
        )
    return blocks


def extract_pdf(pdf_path: Path) -> list[FaqBlock]:
    """Run Docling for text + PyMuPDF for images. Falls back to PyMuPDF-only text if Docling fails."""
    text_pages, page_images = _extract_with_pymupdf(pdf_path)

    if sum(len(p.strip()) for p in text_pages) >= 500:
        return _split_into_faq_blocks(text_pages, page_images)

    try:
        docling_pages = _extract_with_docling(pdf_path)
        if docling_pages and any(p.strip() for p in docling_pages):
            text_pages = docling_pages
    except Exception as exc:
        logger.warning("docling_failed_fallback_pymupdf", error=str(exc))

    return _split_into_faq_blocks(text_pages, page_images)


def _extract_with_pymupdf(pdf_path: Path) -> tuple[list[str], dict[int, list[RawImage]]]:
    import pymupdf  # type: ignore[import-untyped]

    doc = pymupdf.open(str(pdf_path))
    text_pages: list[str] = []
    page_images: dict[int, list[RawImage]] = {}
    try:
        for page_idx, page in enumerate(doc):
            text_pages.append(page.get_text("text") or "")
            page_images[page_idx] = []
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base = doc.extract_image(xref)
                except Exception:
                    continue
                raw_bytes = base.get("image") or b""
                if not raw_bytes:
                    continue
                ext = base.get("ext") or "png"
                try:
                    png_bytes, width, height = _normalize_image_to_png(raw_bytes, ext_hint=ext)
                except Exception as exc:
                    logger.warning("image_normalize_failed", error=str(exc))
                    continue
                sha, md5 = _hash(png_bytes)
                image_id = f"img_{sha[:16]}"
                page_images[page_idx].append(
                    RawImage(
                        image_id=image_id,
                        page_index=page_idx,
                        bytes_png=png_bytes,
                        width=width,
                        height=height,
                        hash_sha256=sha,
                        hash_md5=md5,
                        original_filename=None,
                    )
                )
    finally:
        doc.close()
    return text_pages, page_images


def _extract_with_docling(pdf_path: Path) -> list[str]:
    """Best-effort Docling extraction returning per-page text. Optional dependency."""
    try:
        from docling.document_converter import DocumentConverter  # type: ignore[import-untyped]
    except Exception:
        return []
    converter = DocumentConverter()
    result = converter.convert(source=str(pdf_path))
    pages: list[str] = []
    doc = getattr(result, "document", None)
    if doc is None or not hasattr(doc, "pages"):
        markdown = result.document.export_to_markdown() if hasattr(result, "document") else ""
        return [markdown]
    for page in doc.pages:  # type: ignore[attr-defined]
        try:
            pages.append(page.export_to_text())
        except Exception:
            pages.append("")
    return pages


def needs_ocr(text: str, *, min_chars: int = 200) -> bool:
    """If a page has very little text, OCR is likely required."""
    stripped = re.sub(r"\s+", "", text or "")
    return len(stripped) < min_chars


def run_ocr_fallback(image_bytes: bytes, lang: str = "por") -> str:
    """Tesseract fallback for image-only pages."""
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return ""
    with Image.open(io.BytesIO(image_bytes)) as im:
        return pytesseract.image_to_string(im, lang=lang) or ""
