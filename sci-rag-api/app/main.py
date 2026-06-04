"""FastAPI application entrypoint."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette import status

from app.api.v1.router import v1_router
from app.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter
from app.schemas.common import ErroResposta
from app.storage.postgres import init_db, shutdown_db
from app.storage.qdrant_client import ensure_collection
from app.storage.redis_client import shutdown_redis

logger = get_logger(__name__)

REQUEST_COUNT = Counter(
    "rag_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "rag_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info("app_starting", app=settings.app_name, version=settings.app_version)
    try:
        await init_db()
        await ensure_collection()
    except Exception as exc:
        logger.warning("startup_dependency_init_failed", error=str(exc))
    yield
    await shutdown_redis()
    await shutdown_db()
    logger.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SCI RAG API",
        version=settings.app_version,
        description=(
            "API standalone para consulta RAG das FAQs SCI Contabil, ingestao de PDFs, "
            "feedback, administracao de chunks e webhooks assinados."
        ),
        lifespan=lifespan,
        default_response_class=JSONResponse,
        responses={
            status.HTTP_401_UNAUTHORIZED: {"model": ErroResposta},
            status.HTTP_403_FORBIDDEN: {"model": ErroResposta},
            status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErroResposta},
            status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErroResposta},
        },
    )

    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["X-API-Key", "Content-Type", "X-RAG-Signature", "X-RAG-Timestamp"],
    )

    app.middleware("http")(_metrics_middleware)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)

    app.include_router(v1_router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        if not get_settings().prometheus_enabled:
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


async def _metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    path = request.scope.get("route").path if request.scope.get("route") else request.url.path
    try:
        response = await call_next(request)
    except Exception:
        REQUEST_COUNT.labels(request.method, path, "500").inc()
        REQUEST_LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
        raise

    REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
    return response


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    retry_after = getattr(exc, "retry_after", None)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "erro": "rate_limit",
            "mensagem": "Limite de requisicoes excedido",
            "retry_after_seconds": retry_after,
            "componente": get_remote_address(request),
        },
    )


async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    campos: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        campos.append({"campo": loc or "payload", "erro": err.get("msg", "invalid")})
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "erro": "validacao",
            "mensagem": "Payload invalido",
            "campos": campos,
        },
    )


app = create_app()
