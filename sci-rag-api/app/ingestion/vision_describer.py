"""Vision LLM wrapper that turns a screenshot into structured JSON metadata."""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.core.logging import get_logger
from app.ingestion.prompts import render
from app.rag.llm_clients import call_vision_claude, parse_json_or_raise

logger = get_logger(__name__)


async def describe_image(
    image_bytes: bytes,
    *,
    faq_context: str | None = None,
    image_mime: str = "image/png",
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.anthropic_api_key:
        return _stub_description(faq_context)

    prompt = render("descricao_imagem.txt", faq_context=faq_context or "(sem contexto)")
    try:
        resp = await call_vision_claude(
            system="Voce descreve telas do sistema SCI Contabil. Retorne JSON valido apenas.",
            user_text=prompt,
            image_bytes=image_bytes,
            image_mime=image_mime,
            # Telas densas (tabelas, planos de contas) geram muito OCR; 2200 tokens
            # truncava o JSON e quebrava o parse. 4096 cobre as telas cheias.
            max_tokens=4096,
            temperature=0.0,
        )
        data = parse_json_or_raise(resp.text)
        data.setdefault("modelo_vision_usado", resp.model)
        return data
    except Exception as exc:
        logger.warning("vision_describe_failed", error=str(exc))
        return _stub_description(faq_context)


def _stub_description(faq_context: str | None) -> dict[str, Any]:
    """Used when no vision API is configured (dev mode)."""
    return {
        "tipo_tela": "outro",
        "titulo_janela": None,
        "menu_caminho_inferido": None,
        "descricao_curta": "(descricao automatica indisponivel)",
        "descricao_vision_llm": "(descricao automatica indisponivel)",
        "ocr_texto_completo": "",
        "elementos_ui_identificados": [],
        "elementos_destacados_visualmente": [],
        "registros_sped_visiveis": [],
        "palavras_chave_exatas": [],
        "quando_enviar": [],
        "confianca_ocr": 0.0,
        "modelo_vision_usado": "stub",
    }
