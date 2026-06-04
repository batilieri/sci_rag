# Webhooks Outbound

A API dispara webhooks best-effort para o SCI em eventos operacionais.

Eventos:

- `query.transferred_human`
- `query.low_confidence`
- `ingest.completed`
- `feedback.negative`

Headers:

```http
X-RAG-Signature: sha256=<hmac>
X-RAG-Timestamp: <unix_timestamp>
Content-Type: application/json
```

Envelope:

```json
{
  "evento": "query.transferred_human",
  "timestamp": "2026-05-23T20:00:00Z",
  "request_id": "req_abc",
  "dados": {
    "motivo": "low_retrieval_score",
    "departamento_sugerido": "suporte_contabil"
  }
}
```

## Validacao HMAC

Assinatura:

```text
hmac_sha256(WEBHOOK_SECRET, "<timestamp>.<body_bytes>")
```

Exemplo Python:

```python
import hashlib
import hmac
import time


def valida(body: bytes, signature_header: str, timestamp_header: str, secret: str) -> bool:
    try:
        ts = int(timestamp_header)
    except ValueError:
        return False
    if abs(time.time() - ts) > 300:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        f"{ts}.".encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

Variaveis:

- `WEBHOOK_SECRET`
- `WEBHOOK_SCI_URL`

Seguranca:

- rejeite timestamps fora de 5 minutos;
- compare assinaturas com `hmac.compare_digest`;
- trate webhooks como idempotentes usando `request_id`.
