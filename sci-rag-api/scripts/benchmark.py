"""Benchmark API responses against a JSON answer key."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(slots=True)
class CaseResult:
    pergunta: str
    ok_top1: bool
    ok_top3: bool
    ok_transbordo: bool
    ok_imagem: bool
    acao: str
    faqs: list[str]
    elapsed_ms: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RAG API benchmark.")
    parser.add_argument("--gabarito", required=True, help="JSON file with benchmark cases.")
    parser.add_argument("--api-url", default=os.getenv("RAG_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--api-key", default=os.getenv("RAG_API_KEY"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--cliente-id", default="benchmark")
    return parser.parse_args()


def _payload(case: dict[str, Any], cliente_id: str) -> dict[str, Any]:
    return {
        "mensagem": case["pergunta"],
        "cliente": {
            "id_externo": cliente_id,
            "nome": "Benchmark",
            "empresa": "Nexiry",
            "licenca_sci": case.get("licenca_sci", "Contabil"),
            "metadata_extra": {"benchmark": True},
        },
        "conversa": {
            "id_externo": f"bench_{abs(hash(case['pergunta']))}",
            "canal": "whatsapp",
            "departamento_atual": "suporte_contabil",
            "historico": [],
        },
        "opcoes": {
            "incluir_debug": False,
            "max_imagens": 3,
            "bypass_cache": True,
            "filtros_categoria": case.get("filtros_categoria"),
        },
    }


async def _run_case(client: httpx.AsyncClient, case: dict[str, Any], cliente_id: str) -> CaseResult:
    started = time.perf_counter()
    response = await client.post("/v1/query", json=_payload(case, cliente_id))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    data = response.json()
    faqs = [str(item.get("faq_id")) for item in data.get("faqs_consultados", [])]
    expected_faq = str(case.get("faq_esperado") or "")
    must_transfer = bool(case.get("deve_transbordar", False))
    image_partial = case.get("imagem_esperada_partial")
    images = [m for m in data.get("mensagens", []) if m.get("tipo") == "imagem"]

    return CaseResult(
        pergunta=case["pergunta"],
        ok_top1=(not expected_faq) or (bool(faqs) and faqs[0] == expected_faq),
        ok_top3=(not expected_faq) or expected_faq in faqs[:3],
        ok_transbordo=(data.get("acao") == "TRANSFERIR_HUMANO") if must_transfer else True,
        ok_imagem=(
            True
            if not image_partial
            else any(image_partial.lower() in json.dumps(img, ensure_ascii=False).lower() for img in images)
        ),
        acao=data.get("acao", ""),
        faqs=faqs,
        elapsed_ms=elapsed_ms,
    )


def _pct(ok: int, total: int) -> float:
    return (ok / total * 100.0) if total else 0.0


async def _run(args: argparse.Namespace) -> int:
    if not args.api_key:
        raise SystemExit("Informe --api-key ou RAG_API_KEY.")
    cases = json.loads(Path(args.gabarito).read_text(encoding="utf-8"))
    headers = {"X-API-Key": args.api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), headers=headers, timeout=args.timeout) as client:
        results = [await _run_case(client, case, args.cliente_id) for case in cases]

    total = len(results)
    top1 = sum(r.ok_top1 for r in results)
    top3 = sum(r.ok_top3 for r in results)
    transfer = sum(r.ok_transbordo for r in results)
    images = sum(r.ok_imagem for r in results)
    avg_ms = int(sum(r.elapsed_ms for r in results) / max(total, 1))

    print(f"Casos:              {total}")
    print(f"Acerto top-1:       {top1}/{total} ({_pct(top1, total):.1f}%)")
    print(f"Acerto top-3:       {top3}/{total} ({_pct(top3, total):.1f}%)")
    print(f"Transbordo correto: {transfer}/{total} ({_pct(transfer, total):.1f}%)")
    print(f"Imagem correta:     {images}/{total} ({_pct(images, total):.1f}%)")
    print(f"Latencia media:     {avg_ms} ms")

    failed = [
        r for r in results if not (r.ok_top1 and r.ok_top3 and r.ok_transbordo and r.ok_imagem)
    ]
    if failed:
        print("")
        print("Falhas:")
        for r in failed:
            print(f"- {r.pergunta!r}: acao={r.acao} faqs={r.faqs} tempo={r.elapsed_ms}ms")
        return 1
    return 0


def main() -> int:
    return asyncio.run(_run(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
