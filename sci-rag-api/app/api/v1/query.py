"""POST /v1/query and /v1/query/stream."""

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import limiter
from app.core.security import ApiKeyContext, require_query
from app.rag.engine import get_engine
from app.schemas.query import QueryRequest, QueryResponse
from app.storage.postgres import get_session

router = APIRouter(prefix="/v1", tags=["query"])


@router.post(
    "/query",
    response_model=QueryResponse,
    response_model_exclude_none=True,
    summary="Consulta RAG principal",
)
@limiter.limit("60/minute")
async def query(
    request: Request,
    payload: QueryRequest,
    auth: Annotated[ApiKeyContext, Depends(require_query)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QueryResponse:
    engine = get_engine(session)
    return await engine.process(payload, api_key_id=auth.key_id)


@router.post(
    "/query/stream",
    summary="Stream incremental da resposta (SSE)",
)
@limiter.limit("60/minute")
async def query_stream(
    request: Request,
    payload: QueryRequest,
    auth: Annotated[ApiKeyContext, Depends(require_query)],
    session: Annotated[AsyncSession, Depends(get_session)],
):
    engine = get_engine(session)

    async def _producer():
        # Emit a quick "started" event then the final payload. The current engine is non-streaming,
        # so this preserves SSE semantics without overpromising LLM streaming.
        yield _sse_event("started", json.dumps({"status": "processing"}))
        try:
            response = await engine.process(payload, api_key_id=auth.key_id)
            yield _sse_event("final", response.model_dump_json(exclude_none=True))
        except Exception as exc:
            yield _sse_event(
                "error", json.dumps({"erro": "internal_error", "mensagem": str(exc)})
            )
            return
        await asyncio.sleep(0.05)

    return StreamingResponse(_producer(), media_type="text/event-stream")


def _sse_event(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"
