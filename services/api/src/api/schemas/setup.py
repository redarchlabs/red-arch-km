"""Schemas for the first-run setup wizard endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SetupStatusRead(BaseModel):
    """Public status: whether the instance still needs its first site admin."""

    needs_setup: bool


class SetupClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)


class SetupClaimResponse(BaseModel):
    claimed: bool
