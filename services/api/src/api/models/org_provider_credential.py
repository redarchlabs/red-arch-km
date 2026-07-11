"""Per-org LLM provider credentials for the multi-provider agent org.

Each row holds one org's API key for one provider (anthropic|openai|gemini|…),
Fernet-encrypted at rest exactly like ``workflow_connections.secret_encrypted``.
The key resolver (:func:`api.services.agents.llm.keys.resolve_provider_key`)
prefers an org's own key here and falls back to the central key in settings.

The plaintext is decrypted only at call time and is never returned by any ``*Read``
schema — the admin UI only ever learns whether a key is *configured*.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.models.base import Base, TimestampMixin, UUIDMixin
from api.models.org import Org


class OrgProviderCredential(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "org_provider_credentials"
    __table_args__ = (UniqueConstraint("org_id", "provider", name="uq_org_provider_credential"),)

    # LLM provider name, one of api.services.agents.llm.catalog.VALID_PROVIDERS.
    provider: Mapped[str] = mapped_column(String(40))
    # Fernet ciphertext of the provider API key (services/crypto.py). Never plaintext.
    secret_encrypted: Mapped[str] = mapped_column(Text)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), index=True
    )
    org: Mapped[Org] = relationship()
