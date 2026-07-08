"""workflow_inbound_endpoints: per-endpoint HMAC signing secret.

Adds ``signing_secret_encrypted`` so an inbound webhook endpoint can require an
HMAC-SHA256 signature (see ``services/workflow/webhook_signing.py``). The secret
is Fernet-encrypted at rest (keyed by ORG_ENCRYPTION_KEY) because the receiver
must recover it to recompute the signature — unlike the URL token, of which only
a hash is stored. NULL keeps legacy endpoints on token-only auth.

Revision ID: 022
Revises: 021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_inbound_endpoints",
        sa.Column("signing_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_inbound_endpoints", "signing_secret_encrypted")
