"""Encrypt existing per-org OpenAI keys at rest.

``orgs.openai_api_key`` historically held the tenant's third-party credential in
plaintext. The application now encrypts it with Fernet (see
``api.services.crypto``); this migration brings existing rows in line by
encrypting any non-null plaintext value in place.

The migration runs on the privileged Alembic connection (BYPASSRLS), so it sees
every tenant's row. It is idempotent: a value that is already a valid Fernet
token is left untouched, guarding against double-encryption if re-run.

``downgrade()`` decrypts back to plaintext (tolerating rows that were already
plaintext) so the schema/data can be rolled back cleanly.

Revision ID: 016
Revises: 015
Create Date: 2026-07-06
"""

import os

from alembic import op
from api.config import _DEV_ORG_ENCRYPTION_KEY
from api.services.crypto import decrypt_secret, encrypt_secret, get_fernet
from cryptography.fernet import InvalidToken
from sqlalchemy import text

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _secret() -> str:
    """Resolve the encryption secret the same way the app config does.

    Read ORG_ENCRYPTION_KEY from the environment, falling back to the shared dev
    default so local/CI migrations match the app's dev behaviour. We read the env
    directly rather than constructing Settings() (which requires SECRET_KEY etc.).
    """
    return os.environ.get("ORG_ENCRYPTION_KEY") or _DEV_ORG_ENCRYPTION_KEY


def _is_fernet_token(value: str, secret: str) -> bool:
    try:
        get_fernet(secret).decrypt(value.encode("utf-8"))
        return True
    except (InvalidToken, ValueError, TypeError):
        return False


def upgrade() -> None:
    secret = _secret()
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, openai_api_key FROM orgs WHERE openai_api_key IS NOT NULL")
    ).fetchall()
    for row_id, value in rows:
        if not value or _is_fernet_token(value, secret):
            # Empty, or already encrypted — skip (idempotent).
            continue
        conn.execute(
            text("UPDATE orgs SET openai_api_key = :val WHERE id = :id"),
            {"val": encrypt_secret(value, secret), "id": row_id},
        )


def downgrade() -> None:
    secret = _secret()
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, openai_api_key FROM orgs WHERE openai_api_key IS NOT NULL")
    ).fetchall()
    for row_id, value in rows:
        if not value:
            continue
        # decrypt_secret tolerates already-plaintext values (returns them as-is).
        conn.execute(
            text("UPDATE orgs SET openai_api_key = :val WHERE id = :id"),
            {"val": decrypt_secret(value, secret), "id": row_id},
        )
