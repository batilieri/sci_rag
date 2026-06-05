"""Valida a descrição por visão (Claude) em poucas imagens, sem gastar muito.

Uso (dentro do container api):
    PYTHONPATH=/srv/app python scripts/test_vision.py /tmp/images/7282/faq_7282_p001_img001.png
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.ingestion.vision_describer import describe_image


async def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        print("informe ao menos um caminho de imagem")
        return
    for p in paths:
        body = Path(p).read_bytes()
        out = await describe_image(
            body,
            faq_context="Tela de uma FAQ do sistema SCI Contabil (modulos ECD/ECF e SPED Fiscal).",
        )
        print(f"===== {p} =====")
        print(f"modelo: {out.get('modelo_vision_usado')}")
        print(f"tipo_tela: {out.get('tipo_tela')}")
        print(f"titulo_janela: {out.get('titulo_janela')}")
        print(f"menu_caminho: {out.get('menu_caminho_inferido')}")
        print(f"descricao_curta: {out.get('descricao_curta')}")
        print(f"registros_sped_visiveis: {out.get('registros_sped_visiveis')}")
        print(f"quando_enviar: {out.get('quando_enviar')}")
        ocr = out.get("ocr_texto_completo") or ""
        print(f"OCR ({len(ocr)} chars): {ocr[:600]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
