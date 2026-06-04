"""API key auth + HMAC signing.

API keys are stored as SHA-256 hashes in Postgres (table `rag_api_keys`).
The raw key is shown ONCE by `scripts/generate_api_key.py`.
"""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.api_key import ApiKey as ApiKeyModel
from app.storage.postgres import get_session


class RequiredScope(str, Enum):
    QUERY = "query"
    FEEDBACK = "feedback"
    ADMIN_READ = "admin:read"
    ADMIN_WRITE = "admin:write"
    ADMIN_ALL = "admin:*"


@dataclass(slots=True)
class ApiKeyContext:
    key_id: str
    nome: str
    escopos: list[str]
    rate_limit_override: int | None = None


KEY_PREFIX = "rag_live_"


def hash_api_key(raw_key: str) -> str:
    """SHA-256 of the raw key bytes. We never persist the raw value."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_raw_key() -> tuple[str, str]:
    """Returns (raw_key_to_show_once, sha256_hash_to_persist)."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_api_key(raw)


def _has_scope(escopos: list[str], required: RequiredScope) -> bool:
    if RequiredScope.ADMIN_ALL.value in escopos:
        return True
    if required is RequiredScope.ADMIN_READ and RequiredScope.ADMIN_WRITE.value in escopos:
        return True
    return required.value in escopos


async def _lookup_active_key(session: AsyncSession, raw_key: str) -> ApiKeyModel | None:
    digest = hash_api_key(raw_key)
    stmt = select(ApiKeyModel).where(ApiKeyModel.key_hash == digest, ApiKeyModel.ativo.is_(True))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


class APIKeyAuth:
    """FastAPI dependency that validates the X-API-Key header for a given scope."""

    def __init__(self, required_scope: RequiredScope):
        self.required_scope = required_scope

    async def __call__(
        self,
        request: Request,
        session: Annotated[AsyncSession, Depends(get_session)],
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> ApiKeyContext:
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "erro": "api_key_ausente",
                    "mensagem": "Header X-API-Key e obrigatorio",
                },
            )

        key_record = await _lookup_active_key(session, x_api_key)
        if key_record is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "erro": "api_key_invalida",
                    "mensagem": "Chave invalida, revogada ou inexistente",
                },
            )

        escopos = list(key_record.escopos or [])
        if not _has_scope(escopos, self.required_scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "erro": "escopo_insuficiente",
                    "mensagem": f"Esta chave nao tem escopo '{self.required_scope.value}'",
                    "escopos_da_chave": escopos,
                },
            )

        ctx = ApiKeyContext(
            key_id=key_record.key_id,
            nome=key_record.nome,
            escopos=escopos,
            rate_limit_override=key_record.rate_limit_override,
        )
        request.state.api_key = ctx

        # Fire-and-forget update of last-used timestamp; avoid blocking the hot path.
        await session.execute(
            update(ApiKeyModel)
            .where(ApiKeyModel.id == key_record.id)
            .values(ultimo_uso=int(time.time()))
        )
        await session.commit()

        return ctx


require_query = APIKeyAuth(RequiredScope.QUERY)
require_feedback = APIKeyAuth(RequiredScope.FEEDBACK)
require_admin_read = APIKeyAuth(RequiredScope.ADMIN_READ)
require_admin_write = APIKeyAuth(RequiredScope.ADMIN_WRITE)


class WebhookSigner:
    """HMAC-SHA256 signer for outbound webhooks. Tolerance of 5 minutes for replay protection."""

    def __init__(self, secret: str | None = None):
        secret = secret or get_settings().webhook_secret
        if not secret or len(secret) < 16:
            raise ValueError("WEBHOOK_SECRET must be at least 16 bytes")
        self.secret = secret.encode("utf-8")

    def sign(self, body: bytes, timestamp: int | None = None) -> dict[str, str]:
        ts = timestamp if timestamp is not None else int(time.time())
        payload = f"{ts}.".encode() + body
        signature = hmac.new(self.secret, payload, hashlib.sha256).hexdigest()
        return {
            "X-RAG-Signature": f"sha256={signature}",
            "X-RAG-Timestamp": str(ts),
        }

    def verify(
        self,
        body: bytes,
        signature_header: str,
        timestamp_header: str,
        tolerance_sec: int = 300,
    ) -> bool:
        try:
            ts = int(timestamp_header)
        except (TypeError, ValueError):
            return False
        if abs(time.time() - ts) > tolerance_sec:
            return False
        expected = self.sign(body, timestamp=ts)["X-RAG-Signature"]
        return hmac.compare_digest(expected, signature_header)


def get_webhook_signer() -> WebhookSigner:
    return WebhookSigner()
