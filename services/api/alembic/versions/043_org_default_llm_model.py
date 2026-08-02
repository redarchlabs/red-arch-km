"""Per-org default LLM model — pin a whole org to local or 3rd-party inference.

Adds ``orgs.default_llm_model`` (nullable). When set, every LLM call made on
behalf of the org that does not name a model explicitly (workflow LLM nodes with
``config.model``, chat answer synthesis, summaries) uses this model id instead
of the platform defaults (``OPENAI_CHAT_MODEL`` / ``OPENAI_SUMMARY_MODEL``).
Combined with ``OPENAI_MODEL_ROUTES`` (model id -> endpoint) this pins an entire
org to a local server or to hosted OpenAI without touching its workflows.
NULL keeps today's behaviour byte for byte.

Revision ID: 043
Revises: 042
"""

import sqlalchemy as sa
from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("default_llm_model", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orgs", "default_llm_model")
