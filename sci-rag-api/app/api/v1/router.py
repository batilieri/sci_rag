"""Aggregate all v1 routers."""

from fastapi import APIRouter

from app.api.v1 import feedback, health, query, stats
from app.api.v1.admin import chunks, ingest, knowledge, reindex

v1_router = APIRouter()
v1_router.include_router(query.router)
v1_router.include_router(feedback.router)
v1_router.include_router(health.router)
v1_router.include_router(stats.router)
v1_router.include_router(ingest.router)
v1_router.include_router(chunks.router)
v1_router.include_router(knowledge.router)
v1_router.include_router(reindex.router)
