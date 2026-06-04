"""Re-exports of common dependencies for routers/tests to import.

Routers import individual dependencies directly from `app.core.security` / `app.storage.postgres`,
but this module is a stable, narrow surface for external consumers (e.g. integration tests).
"""

from __future__ import annotations

from app.core.security import (
    APIKeyAuth,
    ApiKeyContext,
    RequiredScope,
    require_admin_read,
    require_admin_write,
    require_feedback,
    require_query,
)
from app.storage.postgres import get_session

__all__ = [
    "APIKeyAuth",
    "ApiKeyContext",
    "RequiredScope",
    "require_admin_read",
    "require_admin_write",
    "require_feedback",
    "require_query",
    "get_session",
]
