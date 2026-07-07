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


class CeleryActiveTask(BaseModel):
    """One task currently executing on a worker (from ``inspect().active()``)."""

    task: str | None = None
    id: str | None = None
    worker: str | None = None
    args: str | None = None
    kwargs: str | None = None
    # Best-effort document id pulled from the ingest task's payload, so the
    # console can deep-link to that document's job logs, show progress, cancel.
    document_id: str | None = None
    # Ingest progress joined from the document row (when document_id resolved).
    status: str | None = None
    percent: int | None = None
    stage: str | None = None


class JobCancelResult(BaseModel):
    """Result of a site-admin cancelling a document's ingest job."""

    document_id: str
    status: str


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
    # Tasks currently executing on a worker (not just queued). Empty if no
    # worker replied to the inspect broadcast.
    active: list[CeleryActiveTask] = []


class JobLogEntry(BaseModel):
    """One structured line from an ingest job's Redis log list."""

    ts: str | None = None
    level: str | None = None
    stage: str | None = None
    message: str | None = None


class JobLogsRead(BaseModel):
    """An ingest job's log lines (oldest first), for the console drill-in."""

    document_id: str
    events: list[JobLogEntry]


class SentEmailAddress(BaseModel):
    """One From/To/Cc/Bcc participant of a captured message."""

    name: str | None = None
    address: str


class SentEmailSummary(BaseModel):
    """One captured message as shown in the console list (headers + snippet)."""

    id: str
    # ``from_addr`` rather than ``from`` — the latter is a Python keyword.
    from_addr: SentEmailAddress | None = None
    to: list[SentEmailAddress] = []
    subject: str
    created: str | None = None
    size: int | None = None
    attachments: int = 0
    snippet: str | None = None


class SentEmailListRead(BaseModel):
    """Captured messages, or an ``available=False`` marker when Mailpit isn't running.

    Mailpit is a dev/staging container; production has no capture, so an
    unreachable API is a normal, non-error state the console renders as such.
    """

    available: bool
    total: int = 0
    messages: list[SentEmailSummary] = []
    detail: str | None = None


class SentEmailDetailRead(BaseModel):
    """One captured message's full headers + body (text and/or HTML)."""

    id: str
    from_addr: SentEmailAddress | None = None
    to: list[SentEmailAddress] = []
    cc: list[SentEmailAddress] = []
    bcc: list[SentEmailAddress] = []
    subject: str
    date: str | None = None
    text: str | None = None
    html: str | None = None
    attachments: list[str] = []
