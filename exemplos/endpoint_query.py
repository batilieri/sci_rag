"""
Endpoint principal de consulta.

Localização: app/api/v1/query.py

Recebe pergunta do cliente + contexto, orquestra todo o pipeline RAG,
retorna resposta estruturada.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.cache import CacheService
from app.core.rate_limit import limiter
from app.core.security import APIKeyAuth, RequiredScope
from app.core.webhooks import WebhookDispatcher
from app.rag.engine import RAGEngine, RAGEngineError
from app.schemas.query import (
    Acao,
    ErroResposta,
    QueryRequest,
    QueryResponse,
)

router = APIRouter(prefix="/v1", tags=["query"])


# ═══════════════════════════════════════════════════════════════════
# Dependencies
# ═══════════════════════════════════════════════════════════════════

def get_rag_engine(request: Request) -> RAGEngine:
    """Engine injetado via app.state (singleton)."""
    return request.app.state.rag_engine


def get_cache(request: Request) -> CacheService:
    return request.app.state.cache


def get_webhook_dispatcher(request: Request) -> WebhookDispatcher:
    return request.app.state.webhook_dispatcher


# ═══════════════════════════════════════════════════════════════════
# POST /v1/query
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/query",
    response_model=QueryResponse,
    responses={
        401: {"model": ErroResposta, "description": "API key inválida"},
        422: {"model": ErroResposta, "description": "Payload inválido"},
        429: {"model": ErroResposta, "description": "Rate limit excedido"},
        503: {"model": ErroResposta, "description": "Serviço degradado"},
    },
    summary="Consultar base de conhecimento e gerar resposta",
    description="""
    Endpoint principal. Recebe a mensagem do cliente + contexto e retorna
    uma resposta estruturada com:

    - Mensagens a enviar (texto/imagem)
    - FAQs consultados (para auditoria)
    - Ação a tomar (RESPONDER ou TRANSFERIR_HUMANO)
    - Métricas de performance e custo

    A API cuida de:
    1. Reescrever a query para melhor recall
    2. Buscar híbrido no Qdrant
    3. Reranking com ColBERT
    4. Gerar resposta com LLM (DeepSeek ou Sonnet)
    5. Aplicar guardrails contra alucinação
    6. Cachear respostas (24h para alta confiança)
    """,
)
@limiter.limit("60/minute")
async def query_endpoint(
    request: Request,
    payload: QueryRequest,
    api_key: Annotated[str, Depends(APIKeyAuth(required_scope=RequiredScope.QUERY))],
    engine: Annotated[RAGEngine, Depends(get_rag_engine)],
    cache: Annotated[CacheService, Depends(get_cache)],
    webhooks: Annotated[WebhookDispatcher, Depends(get_webhook_dispatcher)],
) -> QueryResponse:

    t_start = time.perf_counter()

    # ── 1. Cache lookup
    cache_key = cache.build_key(
        mensagem=payload.mensagem,
        licenca=payload.cliente.licenca_sci,
        departamento=payload.conversa.departamento_atual,
    )

    if not payload.opcoes.bypass_cache:
        cached = await cache.get(cache_key)
        if cached:
            cached.metricas.cache_hit = True
            cached.metricas.tempo_total_ms = int((time.perf_counter() - t_start) * 1000)
            return cached

    # ── 2. Executar pipeline RAG
    try:
        response: QueryResponse = await engine.process(payload)
    except RAGEngineError as e:
        # Erros internos viram 503 — cliente pode tentar de novo
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "erro": "servico_indisponivel",
                "componente": e.componente,
                "mensagem": str(e),
            },
        )

    # ── 3. Cache (só respostas de alta confiança)
    if response.acao == Acao.RESPONDER and response.confianca >= 0.7 and not payload.opcoes.bypass_cache:
        ttl = 24 * 3600 if response.confianca >= 0.9 else 6 * 3600
        await cache.set(cache_key, response, ttl=ttl)

    # ── 4. Webhooks outbound (assíncronos, não bloqueiam a resposta)
    if response.acao == Acao.TRANSFERIR_HUMANO:
        await webhooks.dispatch_async(
            evento="query.transferred_human",
            request_id=response.request_id,
            dados={
                "query": payload.mensagem,
                "cliente_id": payload.cliente.id_externo,
                "conversa_id": payload.conversa.id_externo,
                "motivo": response.motivo_transbordo.value if response.motivo_transbordo else "unknown",
                "confianca": response.confianca,
                "faqs_consultados": [f.faq_id for f in response.faqs_consultados],
                "intencao_detectada": response.intencao_detectada,
            },
        )
    elif response.confianca < 0.5:
        # Mesmo que tenha respondido, baixa confiança é sinal de alerta
        await webhooks.dispatch_async(
            evento="query.low_confidence",
            request_id=response.request_id,
            dados={
                "query": payload.mensagem,
                "confianca": response.confianca,
                "faqs_consultados": [f.faq_id for f in response.faqs_consultados],
            },
        )

    # ── 5. Métricas finais
    response.metricas.tempo_total_ms = int((time.perf_counter() - t_start) * 1000)
    response.metricas.cache_hit = False

    # ── 6. Limpar debug se não foi pedido (segurança)
    if not payload.opcoes.incluir_debug:
        response.debug = None

    return response


# ═══════════════════════════════════════════════════════════════════
# POST /v1/query/stream  (Server-Sent Events)
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/query/stream",
    summary="Versão streaming do endpoint de consulta",
    description="""
    Mesma lógica de /v1/query mas retorna eventos SSE conforme cada etapa
    completa. Útil para painéis web onde você quer mostrar progresso.

    Eventos emitidos:
    - retrieval_started
    - retrieval_completed (com top chunks)
    - generation_started
    - message_chunk (streaming de tokens do LLM)
    - completed (resposta final completa)
    - error
    """,
)
@limiter.limit("30/minute")
async def query_stream_endpoint(
    request: Request,
    payload: QueryRequest,
    api_key: Annotated[str, Depends(APIKeyAuth(required_scope=RequiredScope.QUERY))],
    engine: Annotated[RAGEngine, Depends(get_rag_engine)],
):
    from fastapi.responses import StreamingResponse

    async def event_generator():
        async for event in engine.process_streaming(payload):
            yield f"event: {event['type']}\ndata: {event['data']}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: não bufferizar
        },
    )
