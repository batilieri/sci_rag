"""GET /v1/stats — aggregated metrics over the last 24h."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import ApiKeyContext, require_admin_read
from app.models.image_asset import ImageAsset
from app.models.query_log import QueryLog
from app.schemas.common import StatsResponse
from app.storage.postgres import get_session
from app.storage.qdrant_client import get_qdrant

router = APIRouter(prefix="/v1", tags=["stats"])


def _percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return float(s[k])


@router.get("/stats", response_model=StatsResponse)
async def stats(
    auth: Annotated[ApiKeyContext, Depends(require_admin_read)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatsResponse:
    settings = get_settings()
    since = datetime.now(UTC) - timedelta(hours=24)

    total_q_stmt = select(func.count()).select_from(QueryLog).where(QueryLog.created_at >= since)
    transb_stmt = (
        select(func.count())
        .select_from(QueryLog)
        .where(QueryLog.created_at >= since, QueryLog.acao == "TRANSFERIR_HUMANO")
    )
    cache_hit_stmt = (
        select(func.count())
        .select_from(QueryLog)
        .where(QueryLog.created_at >= since, QueryLog.cache_hit.is_(True))
    )
    avg_conf_stmt = select(func.coalesce(func.avg(QueryLog.confianca), 0.0)).where(
        QueryLog.created_at >= since
    )
    custo_stmt = select(func.coalesce(func.sum(QueryLog.custo_estimado_usd), 0.0)).where(
        QueryLog.created_at >= since
    )
    erros_stmt = (
        select(func.count())
        .select_from(QueryLog)
        .where(QueryLog.created_at >= since, QueryLog.motivo_transbordo == "internal_error")
    )
    latencias_stmt = select(QueryLog.tempo_total_ms).where(QueryLog.created_at >= since)

    total_q = int((await session.execute(total_q_stmt)).scalar() or 0)
    total_transb = int((await session.execute(transb_stmt)).scalar() or 0)
    total_cache = int((await session.execute(cache_hit_stmt)).scalar() or 0)
    avg_conf = float((await session.execute(avg_conf_stmt)).scalar() or 0.0)
    custo = float((await session.execute(custo_stmt)).scalar() or 0.0)
    erros = int((await session.execute(erros_stmt)).scalar() or 0)
    latencias = [int(r[0] or 0) for r in (await session.execute(latencias_stmt)).all()]

    imagens_stmt = select(func.count()).select_from(ImageAsset).where(ImageAsset.status == "active")
    imagens_indexadas = int((await session.execute(imagens_stmt)).scalar() or 0)

    # Qdrant counts.
    client = get_qdrant()
    try:
        info = await client.get_collection(settings.qdrant_collection)
        chunks_indexados = int(info.points_count or 0)
    except Exception:
        chunks_indexados = 0

    faqs_indexados_stmt = select(func.count(func.distinct(ImageAsset.faq_id)))
    faqs_indexados = int((await session.execute(faqs_indexados_stmt)).scalar() or 0)

    return StatsResponse(
        total_queries_24h=total_q,
        taxa_transbordo_24h=(total_transb / total_q) if total_q else 0.0,
        cache_hit_rate_24h=(total_cache / total_q) if total_q else 0.0,
        latencia_p50_ms=_percentile(latencias, 50),
        latencia_p95_ms=_percentile(latencias, 95),
        latencia_p99_ms=_percentile(latencias, 99),
        confianca_media_24h=round(avg_conf, 4),
        chunks_indexados=chunks_indexados,
        faqs_indexados=faqs_indexados,
        imagens_indexadas=imagens_indexadas,
        custo_llm_24h_usd=round(custo, 4),
        erros_24h=erros,
        timestamp=datetime.now(UTC),
    )
