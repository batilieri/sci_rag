# Exemplos Práticos de Uso da API

## 1. cURL — teste rápido

### Health check
```bash
curl -X GET https://rag.seudominio.com/v1/health \
  -H "X-API-Key: rag_live_abc123..."
```

### Query simples
```bash
curl -X POST https://rag.seudominio.com/v1/query \
  -H "X-API-Key: rag_live_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "mensagem": "como marcar eliminação K300 no balanço?",
    "cliente": {
      "id_externo": "12345",
      "nome": "Maria Silva",
      "empresa": "Contabilidade ABC",
      "licenca_sci": "Contábil Completo"
    },
    "conversa": {
      "id_externo": "ticket_98765",
      "canal": "whatsapp",
      "historico": []
    }
  }'
```

### Ingerir novo PDF
```bash
curl -X POST https://rag.seudominio.com/v1/admin/ingest \
  -H "X-API-Key: rag_admin_xyz..." \
  -F "file=@FAQ_Folha_Pagamento.pdf" \
  -F 'metadata={"sistema": "SCI Folha", "categoria": "Folha"}'
```

---

## 2. Python — uso direto (sem Nexiry)

```python
import asyncio
import os

import httpx

API_URL = os.getenv("RAG_API_URL", "https://rag.seudominio.com")
API_KEY = os.getenv("RAG_API_KEY")

async def perguntar(mensagem: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{API_URL}/v1/query",
            headers={"X-API-Key": API_KEY},
            json={
                "mensagem": mensagem,
                "cliente": {"id_externo": "teste", "nome": "Teste"},
                "conversa": {"id_externo": "conv_teste", "historico": []},
            },
        )
        resp.raise_for_status()
        return resp.json()

# Uso
resultado = asyncio.run(perguntar("como faço backup no SCI?"))
print(resultado["acao"])  # RESPONDER ou TRANSFERIR_HUMANO
for m in resultado["mensagens"]:
    print(m)
```

---

## 3. JavaScript / Node.js — para o painel web ou n8n

```javascript
async function consultarRAG(mensagem, contexto) {
    const resp = await fetch('https://rag.seudominio.com/v1/query', {
        method: 'POST',
        headers: {
            'X-API-Key': process.env.RAG_API_KEY,
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            mensagem,
            cliente: contexto.cliente,
            conversa: contexto.conversa,
            opcoes: { incluir_debug: false, max_imagens: 3 },
        }),
    });

    if (!resp.ok) {
        const erro = await resp.json();
        throw new Error(`RAG API: ${erro.mensagem}`);
    }

    return await resp.json();
}

// Uso
const resp = await consultarRAG('como gero DRE consolidada?', {
    cliente: { id_externo: 'cli_001', licenca_sci: 'Contábil' },
    conversa: { id_externo: 'conv_001', historico: [] },
});

console.log(resp.mensagens);
```

---

## 4. n8n — usando como webhook intermediário

Se você quiser que algum workflow n8n também consulte a base:

**Node HTTP Request:**
- Method: `POST`
- URL: `https://rag.seudominio.com/v1/query`
- Authentication: `Header Auth` → Name: `X-API-Key`, Value: `{{ $env.RAG_API_KEY }}`
- Body: `JSON` com a estrutura padrão

A resposta vem com `mensagens[]` que podem ser usadas em nodes seguintes (Set, IF, Telegram, Email, etc.).

---

## 5. Nexiry Django — integração completa

No `bot_engine` do Nexiry, no ponto onde processa mensagem do cliente:

```python
# nexiry/bot_engine/processor.py
from nexiry.services.rag_client import RAGClient, RAGAPIPermanentError, RAGAPITransientError
from nexiry.services.evolution import evolution_api
import asyncio, random, logging

log = logging.getLogger(__name__)


async def processar_mensagem(ticket, mensagem):
    """Chamado por celery task a cada msg recebida do WhatsApp."""

    # Pula se atendente já assumiu
    if ticket.bot_bloqueado_ciclo:
        return

    cliente = ticket.cliente

    async with RAGClient() as rag:
        try:
            resp = await rag.query(
                mensagem=mensagem.texto,
                cliente_id=str(cliente.id),
                conversa_id=str(ticket.id),
                cliente_nome=cliente.nome,
                cliente_empresa=cliente.empresa_nome,
                licenca_sci=cliente.licenca_tipo,
                tempo_relacionamento_meses=cliente.meses_de_relacionamento(),
                departamento_atual=ticket.departamento_codigo,
                historico=ticket.ultimas_n_mensagens_serializadas(n=10),
            )
        except RAGAPIPermanentError as e:
            log.error("RAG erro permanente: %s (req_id=%s)", e, e.request_id)
            await ticket.transferir_humano(motivo="rag_payload_invalido")
            return
        except RAGAPITransientError as e:
            log.error("RAG indisponível: %s", e)
            await ticket.transferir_humano(motivo="rag_indisponivel")
            await evolution_api.enviar_texto(
                ticket.numero, "Tive um problema técnico, vou te transferir para um humano."
            )
            return

    # Auditoria
    await RAGAuditoria.objects.acreate(
        ticket_id=ticket.id,
        request_id=resp["request_id"],
        mensagem_cliente=mensagem.texto,
        acao=resp["acao"],
        confianca=resp["confianca"],
        faqs_consultados=resp["faqs_consultados"],
        modelo_usado=resp["metricas"]["modelo_usado"],
        custo_usd=resp["metricas"]["custo_estimado_usd"],
        tempo_ms=resp["metricas"]["tempo_total_ms"],
    )

    # TRANSBORDO
    if resp["acao"] == "TRANSFERIR_HUMANO":
        await ticket.transferir_humano(
            departamento=resp.get("departamento_sugerido") or "suporte_contabil",
            motivo=resp.get("motivo_transbordo", "rag_decidiu"),
        )
        if resp["mensagens"]:
            await evolution_api.enviar_texto(ticket.numero, resp["mensagens"][0]["conteudo"])
        return

    # RESPONDER: envia mensagens uma a uma com delays humanizados
    for msg in resp["mensagens"]:
        try:
            if msg["tipo"] == "texto":
                await evolution_api.enviar_texto(ticket.numero, msg["conteudo"])
            elif msg["tipo"] == "imagem":
                await evolution_api.enviar_imagem(
                    ticket.numero,
                    msg["url"],
                    legenda=msg.get("legenda", ""),
                )
        except Exception as e:
            log.exception("Falha ao enviar mensagem via Evolution: %s", e)
            continue

        await asyncio.sleep(random.uniform(1.2, 2.8))

    # Marca interação
    ticket.ultima_interacao_bot = timezone.now()
    await ticket.asave()
```

---

## 6. Recebendo webhooks da API no Nexiry

A API chama o Nexiry quando eventos importantes acontecem (transbordo, baixa confiança). Crie um endpoint no Nexiry:

```python
# nexiry/webhooks/views.py
import hmac, hashlib, time, json
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def _valida_hmac(body: bytes, signature_header: str, timestamp_header: str) -> bool:
    secret = settings.RAG_WEBHOOK_SECRET.encode()
    try:
        ts = int(timestamp_header)
    except ValueError:
        return False
    if abs(time.time() - ts) > 300:  # 5 min de tolerância
        return False
    payload = f"{ts}.".encode() + body
    expected = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@csrf_exempt
@require_POST
def webhook_rag_events(request):
    sig = request.META.get("HTTP_X_RAG_SIGNATURE", "")
    ts = request.META.get("HTTP_X_RAG_TIMESTAMP", "")

    if not _valida_hmac(request.body, sig, ts):
        return JsonResponse({"erro": "assinatura_invalida"}, status=401)

    event = json.loads(request.body)

    if event["evento"] == "query.transferred_human":
        # Notificar supervisores se muitos transbordos no mesmo tópico
        from nexiry.alertas.tasks import verificar_pico_transbordo
        verificar_pico_transbordo.delay(event["dados"])

    elif event["evento"] == "query.low_confidence":
        # Logar para análise: pode ser gap na base
        from nexiry.analytics.models import GapDeBase
        GapDeBase.objects.create(
            query=event["dados"]["query"],
            confianca=event["dados"]["confianca"],
            faqs_consultados=event["dados"]["faqs_consultados"],
        )

    elif event["evento"] == "feedback.negative":
        # Notificar você para revisar
        from nexiry.notifications import notificar_admin
        notificar_admin.delay(
            titulo="Feedback negativo na IA",
            corpo=f"Cliente: {event['dados']['query']}\nResposta: {event['dados']['resposta']}",
        )

    return JsonResponse({"recebido": True})
```

E na URL do Nexiry:

```python
# nexiry/urls.py
urlpatterns = [
    # ...
    path("webhooks/rag-events", webhook_rag_events, name="rag_webhook"),
]
```

---

## 7. Painel Admin — React (no frontend Nexiry)

Para revisar/editar chunks da base direto no painel:

```jsx
// nexiry-frontend/src/admin/RAGChunkEditor.jsx
import { useState, useEffect } from "react";

const RAG_API = process.env.REACT_APP_RAG_API_URL;
const ADMIN_KEY = process.env.REACT_APP_RAG_ADMIN_KEY; // só admins têm

function listarChunks(faqId) {
    return fetch(`${RAG_API}/v1/admin/chunks?faq_id=${faqId}`, {
        headers: { "X-API-Key": ADMIN_KEY },
    }).then((r) => r.json());
}

function aprovarChunk(chunkId) {
    return fetch(`${RAG_API}/v1/admin/chunks/${chunkId}/approve`, {
        method: "POST",
        headers: { "X-API-Key": ADMIN_KEY },
    });
}

function editarChunk(chunkId, novosCampos) {
    return fetch(`${RAG_API}/v1/admin/chunks/${chunkId}`, {
        method: "PATCH",
        headers: {
            "X-API-Key": ADMIN_KEY,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(novosCampos),
    });
}

export function ChunkEditor({ faqId }) {
    const [chunks, setChunks] = useState([]);

    useEffect(() => {
        listarChunks(faqId).then(setChunks);
    }, [faqId]);

    return (
        <div>
            {chunks.map((c) => (
                <div key={c.id}>
                    <h4>{c.titulo_secao}</h4>
                    <pre>{c.texto_original}</pre>
                    <button onClick={() => aprovarChunk(c.id)}>
                        Aprovar (boost na busca)
                    </button>
                </div>
            ))}
        </div>
    );
}
```

---

## 8. Testando a precisão — benchmark CLI

`scripts/benchmark.py` (no projeto da API):

```bash
docker compose exec api python scripts/benchmark.py --gabarito tests/gabarito.json

# Saída:
# Acerto top-1:     27/30 (90%)
# Acerto top-3:     30/30 (100%)
# Falsos positivos:  0
# Transbordos OK:   5/5
# Tempo médio:     1.8s
# Custo total:     $0.31
```

Onde `gabarito.json` é:

```json
[
  {
    "pergunta": "como marco eliminação K300 no balanço?",
    "faq_esperado": "7085",
    "imagem_esperada_partial": "balanco_patrimonial",
    "deve_transbordar": false
  },
  {
    "pergunta": "analisa esses números do meu balanço de outubro",
    "deve_transbordar": true
  }
]
```
