"""Schemas for the global site-admin console endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class AdminUserUpdate(BaseModel):
    """Site-admin-editable user fields.

    Username/email are Clerk-owned (resynced on every login) so they are
    deliberately absent; extra="forbid" makes sending them a 422.
    """

    model_config = ConfigDict(extra="forbid")

    is_site_admin: bool | None = None
    is_active: bool | None = None


class UserMembershipSummary(BaseModel):
    """One org membership of a user, as seen from the global console."""

    model_config = ConfigDict(from_attributes=True)

    membership_id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    is_org_admin: bool


class ComponentStatus(BaseModel):
    status: str  # "ok" | "error"
    latency_ms: float | None = None
    detail: str | None = None


class SystemStatusRead(BaseModel):
    version: str
    components: dict[str, ComponentStatus]


class CeleryQueueItem(BaseModel):
    """One pending message peeked from the Celery broker queue."""

    task: str | None = None
    id: str | None = None
    eta: str | None = None
    args: str | None = None
    kwargs: str | None = None


class BeatScheduleEntry(BaseModel):
    """One configured periodic task, as published by the running beat process."""

    name: str
    task: str | None = None
    schedule_seconds: float | None = None


class BeatStatus(BaseModel):
    status: str  # "ok" | "stale" | "down"
    last_tick: str | None = None
    age_seconds: float | None = None
    detail: str | None = None


class CeleryStatusRead(BaseModel):
    """Beat liveness + schedule, plus a peek at the broker queue's pending work."""

    queue_name: str
    depth: int
    items: list[CeleryQueueItem]
    truncated: bool
    beat: BeatStatus
    schedule: list[BeatScheduleEntry]
