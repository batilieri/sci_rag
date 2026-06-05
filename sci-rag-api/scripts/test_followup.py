"""Diagnostico de conversa: pergunta 1, depois uma pergunta de continuacao
('onde encontro essa tela?') carregando o historico, e mostra o que o rewriter
fez e se transferiu ou respondeu. Roda no HOST contra 127.0.0.1:8088.
"""

import json
import urllib.request

API = "http://127.0.0.1:8088/v1/query"
KEY = "rag_live_teste_validacao_123"


def ask(mensagem, conversa_id, historico):
    payload = {
        "mensagem": mensagem,
        "cliente": {"id_externo": "c1"},
        "conversa": {"id_externo": conversa_id, "canal": "web", "historico": historico},
        "opcoes": {"incluir_debug": True, "bypass_cache": True},
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def first_text(resp):
    for m in resp.get("mensagens", []):
        if m.get("tipo") == "texto":
            return m.get("conteudo", "")
    return ""


def main():
    conv = "diag_followup_1"
    q1 = "Qual a tela que eu vinculo o saldo negativo de IRPJ na ECF?"
    r1 = ask(q1, conv, [])
    a1 = first_text(r1)
    print("Q1:", q1)
    print("  acao:", r1.get("acao"), "| conf:", r1.get("confianca"))
    print("  A1:", a1[:120])
    print()

    historico = [
        {"role": "user", "content": q1},
        {"role": "assistant", "content": a1},
    ]
    q2 = "Onde encontro essa tela?"
    r2 = ask(q2, conv, historico)
    dbg = r2.get("debug") or {}
    print("Q2 (continuacao):", q2)
    print("  acao:", r2.get("acao"), "| conf:", r2.get("confianca"))
    print("  queries_reescritas:", dbg.get("queries_reescritas"))
    print("  faqs:", [f["faq_id"] for f in r2.get("faqs_consultados", [])])
    print("  A2:", first_text(r2)[:160])


if __name__ == "__main__":
    main()
