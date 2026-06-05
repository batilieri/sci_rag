"""Testa anexos no chat: TXT e PDF (one-off, sem gravar na base).
Roda no HOST contra 127.0.0.1:8088.
"""

import base64
import json
import sys
import urllib.request

API = "http://127.0.0.1:8088/v1/query"
KEY = "rag_live_teste_validacao_123"


def ask(mensagem, anexo_b64, anexo_mime, anexo_nome):
    payload = {
        "mensagem": mensagem,
        "cliente": {"id_externo": "c1"},
        "conversa": {"id_externo": "t_anexo", "canal": "web", "historico": []},
        "opcoes": {"incluir_debug": True, "bypass_cache": True},
        "anexo_base64": anexo_b64,
        "anexo_mime": anexo_mime,
        "anexo_nome": anexo_nome,
    }
    req = urllib.request.Request(
        API, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def show(label, r):
    print(f"=== {label} ===")
    print("  acao:", r.get("acao"), "| conf:", r.get("confianca"))
    print("  faqs:", [f["faq_id"] for f in r.get("faqs_consultados", [])])
    for m in r.get("mensagens", []):
        if m.get("tipo") == "texto":
            print("  >", m.get("conteudo", "")[:160])
    print()


def main():
    # 1) TXT
    txt = "Relato do cliente: nao consigo habilitar o registro K315, ele aparece cinza no Bloco K do SPED Fiscal. Como resolver?"
    b64 = base64.b64encode(txt.encode("utf-8")).decode()
    show("TXT (K315 cinza)", ask("Me ajuda com o que esta nesse arquivo", b64, "text/plain", "relato.txt"))

    # 2) PDF (um FAQ do SCI)
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
    if pdf_path:
        pb64 = base64.b64encode(open(pdf_path, "rb").read()).decode()
        show("PDF (FAQ SCI)", ask("O que esse documento explica e como aplico?", pb64, "application/pdf", "faq.pdf"))


if __name__ == "__main__":
    main()
