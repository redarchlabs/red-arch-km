"""Organization schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    use_knowledge_graph: bool = True


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    use_knowledge_graph: bool | None = None
    # Per-org OpenAI key (used by the config-assistant + AI OCR). Accepted in
    # plaintext at this boundary and encrypted at rest by the router before it
    # reaches the DB (services/crypto.py). Empty string clears it. Never
    # returned in OrgRead — reads go through the internal decrypt path only.
    openai_api_key: str | None = Field(default=None, max_length=500)
    # Org-wide default LLM model id (routed via OPENAI_MODEL_ROUTES, so it pins
    # the org to local or 3rd-party inference). Empty string clears it back to
    # the platform default; None means "no change".
    default_llm_model: str | None = Field(default=None, max_length=100)
    # NOTE: home_view_id is deliberately NOT here. The landing view is an
    # org-admin concern (it points at a view that org authored and owns), so it
    # is set through PATCH /orgs/{org_id}/settings — see OrgSettingsUpdate.


class OrgSettingsUpdate(BaseModel):
    """Org-admin-writable org settings (``PATCH /orgs/{org_id}/settings``).

    Deliberately much narrower than :class:`OrgUpdate`: tenancy and cost fields
    (name, knowledge-graph provisioning, per-org API key, LLM pin) stay
    site-admin only. Unlike OrgUpdate this is a *replacement* payload — the
    field is single, so an omitted or null ``home_view_id`` clears the home view
    rather than meaning "no change", and no sentinel value is needed.
    """

    home_view_id: uuid.UUID | None = None


class OrgRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    use_knowledge_graph: bool
    home_view_id: uuid.UUID | None = None
    default_llm_model: str | None = None


class DimensionCreate(BaseModel):
    """Shared schema for Region, Department, Role, Group creation."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class DimensionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    permission_number: int
