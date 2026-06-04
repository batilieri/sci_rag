"""Generate and persist an API key hash.

The raw API key is printed exactly once. Only the SHA-256 hash is stored in
Postgres, as required by the API contract.
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import RequiredScope, generate_raw_key, hash_api_key
from app.models import Base
from app.models.api_key import ApiKey


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a SCI RAG API key.")
    parser.add_argument("--nome", required=True, help="Human-readable key name.")
    parser.add_argument(
        "--escopos",
        nargs="+",
        required=True,
        help="Scopes: query feedback admin:read admin:write admin:*",
    )
    parser.add_argument("--rate-limit-override", type=int, default=None)
    parser.add_argument(
        "--raw-key",
        default=None,
        help="Optional externally generated raw key. Useful only for controlled migration.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override sync SQLAlchemy DSN. Defaults to POSTGRES_* settings.",
    )
    return parser.parse_args()


def validate_scopes(scopes: list[str]) -> list[str]:
    valid = {scope.value for scope in RequiredScope}
    invalid = sorted(set(scopes) - valid)
    if invalid:
        raise SystemExit(f"Escopos invalidos: {', '.join(invalid)}. Validos: {', '.join(sorted(valid))}")
    return scopes


def main() -> int:
    args = parse_args()
    scopes = validate_scopes(args.escopos)
    settings = get_settings()
    engine = create_engine(args.database_url or settings.postgres_sync_dsn, future=True)

    Base.metadata.create_all(bind=engine)

    if args.raw_key:
        raw_key = args.raw_key
        key_hash = hash_api_key(raw_key)
    else:
        raw_key, key_hash = generate_raw_key()

    key_id = f"key_{uuid.uuid4().hex[:16]}"

    with Session(engine) as session:
        existing = session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash)).scalar_one_or_none()
        if existing is not None:
            raise SystemExit(f"Ja existe uma chave com este hash: {existing.key_id}")

        record = ApiKey(
            key_id=key_id,
            key_hash=key_hash,
            nome=args.nome,
            escopos=scopes,
            rate_limit_override=args.rate_limit_override,
            ativo=True,
        )
        session.add(record)
        session.commit()

    print("API key criada.")
    print(f"key_id: {key_id}")
    print(f"nome: {args.nome}")
    print(f"escopos: {', '.join(scopes)}")
    print("")
    print("CHAVE RAW (mostrada uma unica vez):")
    print(raw_key)
    print("")
    print("Guarde esta chave no cofre de segredos do consumidor. Ela nao pode ser recuperada do banco.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
