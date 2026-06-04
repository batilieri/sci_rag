"""
Autenticação por API Key + escopo de permissões + HMAC para webhooks.

Localização: app/core/security.py
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from enum import Enum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel


# ═══════════════════════════════════════════════════════════════════
# ESCOPOS
# ═══════════════════════════════════════════════════════════════════

class RequiredScope(str, Enum):
    QUERY = "query"           # consulta padrão
    FEEDBACK = "feedback"     # registrar feedback
    ADMIN_READ = "admin:read" # ler chunks, stats
    ADMIN_WRITE = "admin:write" # ingerir, editar, deletar chunks
    ADMIN_ALL = "admin:*"


class APIKey(BaseModel):
    key_id: str
    nome: str
    escopos: list[str]
    rate_limit_override: int | None = None
    ativo: bool = True
    criada_em: float
    ultimo_uso: float | None = None


# ═══════════════════════════════════════════════════════════════════
# STORAGE DE CHAVES (em produção: Postgres ou Redis)
# ═══════════════════════════════════════════════════════════════════

class APIKeyStore:
    """
    Em produção: trocar para Postgres com índice em hash da chave.
    Aqui um stub em memória só para o esqueleto.
    """

    def __init__(self):
        self._keys: dict[str, APIKey] = {}

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        """Armazenamos SHA-256 da chave, nunca a chave raw."""
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def lookup(self, raw_key: str) -> APIKey | None:
        return self._keys.get(self._hash_key(raw_key))

    def create(self, nome: str, escopos: list[str]) -> tuple[str, APIKey]:
        """
        Retorna (raw_key, registro). A raw_key só pode ser mostrada UMA VEZ.
        Depois disso fica só o hash.
        """
        # Formato: <prefix>_<env>_<random>
        # ex: sci_prod_pk_live_abc123def456...
        prefix = "rag"
        env = "live"
        random_part = secrets.token_urlsafe(32)
        raw_key = f"{prefix}_{env}_{random_part}"

        key_id = secrets.token_hex(8)
        registro = APIKey(
            key_id=key_id,
            nome=nome,
            escopos=escopos,
            criada_em=time.time(),
        )
        self._keys[self._hash_key(raw_key)] = registro
        return raw_key, registro

    def revoke(self, key_id: str) -> bool:
        for h, k in self._keys.items():
            if k.key_id == key_id:
                k.ativo = False
                return True
        return False


# Singleton global (em prod virá via Depends)
_key_store = APIKeyStore()


# ═══════════════════════════════════════════════════════════════════
# DEPENDENCY: VALIDAR API KEY
# ═══════════════════════════════════════════════════════════════════

class APIKeyAuth:
    """
    Use como dependency:
        api_key: Annotated[str, Depends(APIKeyAuth(RequiredScope.QUERY))]
    """

    def __init__(self, required_scope: RequiredScope):
        self.required_scope = required_scope

    async def __call__(
        self,
        request: Request,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> str:
        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "erro": "api_key_ausente",
                    "mensagem": "Header X-API-Key é obrigatório",
                },
            )

        key_record = _key_store.lookup(x_api_key)
        if not key_record or not key_record.ativo:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "erro": "api_key_invalida",
                    "mensagem": "Chave inválida, revogada ou inexistente",
                },
            )

        # Validar escopo
        if not self._has_scope(key_record.escopos, self.required_scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "erro": "escopo_insuficiente",
                    "mensagem": f"Esta chave não tem escopo '{self.required_scope.value}'",
                    "escopos_da_chave": key_record.escopos,
                },
            )

        # Anexa key_id ao request para logging
        request.state.api_key_id = key_record.key_id
        request.state.api_key_nome = key_record.nome

        # Atualiza último uso (assíncrono em prod)
        key_record.ultimo_uso = time.time()

        return x_api_key

    @staticmethod
    def _has_scope(escopos_chave: list[str], required: RequiredScope) -> bool:
        if RequiredScope.ADMIN_ALL.value in escopos_chave:
            return True
        return required.value in escopos_chave


# ═══════════════════════════════════════════════════════════════════
# HMAC PARA WEBHOOKS OUTBOUND
# ═══════════════════════════════════════════════════════════════════

class WebhookSigner:
    """
    Quando a API chama webhooks no SCI (ou outros), assina com HMAC-SHA256.
    O receptor valida com a mesma chave compartilhada.
    """

    def __init__(self, secret: str):
        self.secret = secret.encode()

    def sign(self, body: bytes, timestamp: int | None = None) -> dict[str, str]:
        ts = timestamp or int(time.time())
        payload_to_sign = f"{ts}.".encode() + body
        signature = hmac.new(self.secret, payload_to_sign, hashlib.sha256).hexdigest()
        return {
            "X-RAG-Signature": f"sha256={signature}",
            "X-RAG-Timestamp": str(ts),
        }

    def verify(self, body: bytes, signature_header: str, timestamp_header: str, tolerance_sec: int = 300) -> bool:
        """Para validar webhooks INBOUND (se a API receber webhooks de terceiros)."""
        try:
            ts = int(timestamp_header)
        except ValueError:
            return False

        # Anti-replay
        if abs(time.time() - ts) > tolerance_sec:
            return False

        expected = self.sign(body, timestamp=ts)["X-RAG-Signature"]
        return hmac.compare_digest(expected, signature_header)


# ═══════════════════════════════════════════════════════════════════
# CLI HELPER: gerar nova API key
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gerar nova API key")
    parser.add_argument("--nome", required=True, help="Nome descritivo da chave (ex: 'SCI Produção')")
    parser.add_argument(
        "--escopos",
        nargs="+",
        required=True,
        help="Escopos: query, feedback, admin:read, admin:write, admin:*"
    )
    args = parser.parse_args()

    raw, record = _key_store.create(nome=args.nome, escopos=args.escopos)
    print(f"\n✓ API Key criada (key_id: {record.key_id})")
    print(f"\nCHAVE (mostrada UMA VEZ — salve com segurança):\n\n  {raw}\n")
    print("Use no header de cada request:")
    print(f"  X-API-Key: {raw}")
