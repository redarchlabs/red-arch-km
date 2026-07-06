"""Beat/worker observability.

``beat_heartbeat`` is scheduled by celery-beat; each run stamps a heartbeat and a
snapshot of the beat schedule into Redis so the site-admin console can report
whether the workflow scheduler is actually alive. A stale or absent heartbeat
means beat is down and the workflow outbox is no longer being swept. Because the
task runs *on a worker* (beat only enqueues it), a fresh heartbeat also proves
the whole beat -> broker -> worker path is healthy.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import redis as redis_sync

from worker.celery_app import app

logger = logging.getLogger(__name__)

# Keys the API's /admin/celery endpoint reads. Kept in the broker DB (db 0) so a
# single Redis connection serves both the queue and this monitoring metadata.
BEAT_HEARTBEAT_KEY = "celery:beat:heartbeat"
BEAT_SCHEDULE_KEY = "celery:beat:schedule"
BEAT_HEARTBEAT_INTERVAL = float(os.environ.get("BEAT_HEARTBEAT_INTERVAL", "15"))


def _broker_redis() -> redis_sync.Redis:
    url = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    return redis_sync.Redis.from_url(url)


def _schedule_snapshot() -> list[dict[str, Any]]:
    """Flatten the configured beat schedule for display in the admin console."""
    snapshot: list[dict[str, Any]] = []
    for name, entry in app.conf.beat_schedule.items():
        schedule = entry.get("schedule")
        seconds = float(schedule) if isinstance(schedule, (int, float)) else None
        snapshot.append({"name": name, "task": entry.get("task"), "schedule_seconds": seconds})
    return snapshot


@app.task  # type: ignore[untyped-decorator]  # celery's app.task is untyped
def beat_heartbeat() -> None:
    """Stamp beat liveness + the schedule snapshot into Redis for the console."""
    client = _broker_redis()
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"ts": now, "interval": BEAT_HEARTBEAT_INTERVAL})
    # TTL is a backstop: if beat dies the key disappears a few ticks later even
    # if nothing rewrites it, so the console reads "down" rather than a frozen ts.
    ttl = int(max(60, BEAT_HEARTBEAT_INTERVAL * 4))
    try:
        client.set(BEAT_HEARTBEAT_KEY, payload, ex=ttl)
        client.set(BEAT_SCHEDULE_KEY, json.dumps(_schedule_snapshot()))
    except Exception as exc:  # noqa: BLE001 - monitoring must never crash the worker
        logger.warning("beat_heartbeat redis write failed: %s", exc)
    finally:
        client.close()
