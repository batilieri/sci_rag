# Integracao Nexiry

Fluxo:

```text
WhatsApp -> Evolution API -> Nexiry Django/Celery -> POST /v1/query -> RAG API
RAG API -> JSON estruturado -> Nexiry -> Evolution API texto/imagem
```

## Cliente Python async

```python
import httpx


class RAGAPITransientError(Exception):
    pass


class RAGAPIPermanentError(Exception):
    pass


class RAGClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def query(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=45.0) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/v1/query",
                    headers={"X-API-Key": self.api_key},
                    json=payload,
                )
            except httpx.RequestError as exc:
                raise RAGAPITransientError(str(exc)) from exc

        if resp.status_code in {400, 401, 403, 404, 422}:
            raise RAGAPIPermanentError(resp.text)
        if resp.status_code >= 500 or resp.status_code == 429:
            raise RAGAPITransientError(resp.text)
        return resp.json()
```

## Tratamento no bot

```python
resp = await rag.query(payload)

if resp["acao"] == "TRANSFERIR_HUMANO":
    await ticket.transferir_humano(
        departamento=resp.get("departamento_sugerido") or "suporte_contabil",
        motivo=resp.get("motivo_transbordo") or "rag_transfer",
    )
    for msg in resp["mensagens"]:
        if msg["tipo"] == "texto":
            await evolution_api.enviar_texto(ticket.numero, msg["conteudo"])
    return

if resp["acao"] == "PEDIR_CLARIFICACAO":
    await evolution_api.enviar_texto(ticket.numero, resp["mensagens"][0]["conteudo"])
    return

for msg in resp["mensagens"]:
    if msg["tipo"] == "texto":
        await evolution_api.enviar_texto(ticket.numero, msg["conteudo"])
    elif msg["tipo"] == "imagem":
        await evolution_api.enviar_imagem(ticket.numero, msg["url"], legenda=msg.get("legenda") or "")
```

## Auditoria recomendada

Persistir no Nexiry:

- `request_id`
- `acao`
- `confianca`
- `faqs_consultados`
- `metricas.modelo_usado`
- `metricas.custo_estimado_usd`
- `metricas.tempo_total_ms`

## Fallbacks

- Erro permanente (`401`, `403`, `422`): corrigir integracao; se ocorrer em atendimento, transferir para humano.
- Erro transiente (`429`, `5xx`, timeout): transferir para humano e registrar indisponibilidade.
- `TRANSFERIR_HUMANO`: bloquear ciclo do bot e mover para `departamento_sugerido`.
