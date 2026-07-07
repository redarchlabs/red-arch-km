# WO-2026-07-06 — Ingest Observability & Control

Status: **PLAN — awaiting approval**
Author: pairing session, 2026-07-06

Four requested enhancements around document ingestion:

1. Show **%-complete** while a document is ingesting.
2. **Stop/cancel** an in-progress ingest from the UI.
3. See **celery queue records** in the system-admin page.
4. See the **celery logs for each job**.

## Guiding finding

Status is written only **3 times** today: `PROCESSING` (up front) → terminal `SUCCESS`/`FAILED`. There is no intermediate progress, no persisted task id, and no per-job log store. brain-api's `/api/ingest-document` is a **single blocking call** that returns only final counts — so true "inside-brain-api" progress needs new brain→worker signalling. Features 2 and 4 share one prerequisite: **persist the celery task id on the document.**

Feature 3 is already largely built (`GET /api/admin/celery` + `CeleryMonitor` under the site-admin "Celery" tab); it only lacks *running-task* visibility and auto-refresh.

---

## Slice 0 — Shared prerequisite (small, do first)

Both cancel and per-job logs need to correlate a document to its celery job.

- Add `celery_task_id: str | None` column to `documents` (Alembic migration). Precedent: `WorkflowRunStep.celery_task_id` (`models/workflow.py:212`).
- Persist the task id returned by `dispatch_extract_ingest` / `dispatch_ingest` at `routers/documents.py:213` and `:505` (currently logged and discarded).
- Add `CANCELLED` to `ProcessingStatus` (`models/document.py:43`), the worker status constants, and the UI status union (`ui/src/types/index.ts:48`) — the enum comment warns these three must stay in lockstep.

**Files:** `models/document.py`, new migration, `routers/documents.py`, `repositories/document.py`, worker status constants, `ui/src/types/index.ts`.

---

## Slice A — Progress % (Feature 1)

**v1 (recommended): coarse, worker-stage-based.** The worker emits `report_status(PROCESSING, {stage, percent})` at each stage boundary it already has:

| Stage | ~% |
|-------|----|
| queued / started | 5 |
| downloading original | 15 |
| extracting text (OCR/vision) | 35 |
| posting to brain-api (chunk→embed→facts) | 60 |
| storing / finalizing | 90 |
| done | 100 |

`processing_details` is arbitrary JSONB and the status callback already passes `details` through untyped (`internal.py:49`), so **no schema change is required to transport it** — but we'll add typed optional `stage`/`percent` fields to `DocumentStatusUpdate` for clarity. Frontend already polls every 4s while any doc processes (`useFolderDocuments.ts:80`); render a progress bar in `DocumentRow.tsx` from `processing_details.percent`.

**v2 (deferred): fine-grained brain-api progress.** The 60→90 band is one opaque blocking call. To sub-divide it (per-chunk embedding/fact progress) brain-api must publish progress — proposed: write `job:progress:{document_id}` to Redis during `ingest_service` stages, and either (a) worker polls it, or (b) a new `GET /api/documents/{id}` field reads it. Defer unless the coarse bar isn't enough.

**Files:** `worker/tasks/extract.py`, `worker/tasks/_ingest_common.py`, `routers/internal.py`, `ui/src/components/documents/DocumentRow.tsx`, `ui/src/types/index.ts`.

---

## Slice B — Cancel (Feature 2)

- New `POST /api/documents/{id}/cancel` → `celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")`, set status `CANCELLED`. (Requires Slice 0's persisted task id.)
- **Purge partial writes on cancel.** Ingest is not idempotent for vectors (see `km2-ingest-not-idempotent`); a mid-flight kill can leave partial chunks in Qdrant. On cancel, call brain-api `/api/remove-document` to purge by `document_key`. Fact-store inserts are idempotent, but chunk upserts are not — so purge is the safe move.
- **Robustness:** celery's revoke set is in-memory and lost on worker restart (`acks_late` redelivers). Belt-and-suspenders: also set a `cancel_requested` flag (Redis or the doc row) that the worker checks at each stage boundary and aborts cleanly. `terminate=True` handles the running process; the flag handles redelivery.
- UI: cancel button on any `PROCESSING` doc in `DocumentRow.tsx`; client fn in `ui/src/lib/api/documents.ts`.

**Files:** `routers/documents.py` (cancel route), `tasks/celery_app.py` (revoke), `repositories/document.py`, worker stage-boundary checks, UI.

---

## Slice C — Celery queue viewer: running tasks + auto-refresh (Feature 3)

Mostly done. Extend, don't build:

- `GET /api/admin/celery` currently peeks the Redis broker list = **pending only**. Add `inspect().active()` + `.reserved()` to surface **running** tasks (which is what you actually want when watching an ingest).
- Add polling/auto-refresh to `CeleryMonitor.tsx` (currently manual Refresh only).
- Optional: link to Flower (:5555) in dev.

**Files:** `routers/admin.py`, `schemas/admin.py`, `ui/src/components/site-admin/CeleryMonitor.tsx`, `ui/src/lib/api/celery.ts`. All behind existing `require_site_admin`.

---

## Slice D — Per-job logs (Feature 4, biggest gap)

No per-job log capture exists today. Two options:

- **v1 cheap:** append bounded structured log events into `processing_details.events[]` (already flows worker→internal→repo). Zero new infra; lossy/bounded; rendered in the document detail page. Good enough to see "what happened" for one doc.
- **v2 proper (recommended target):** Redis list/stream keyed by `celery_task_id` (mirrors the beat-heartbeat Redis pattern in `worker/tasks/monitoring.py`), capped length + TTL. Worker appends log lines; new `GET /api/admin/jobs/{task_id}/logs` serves them; drill-in from `CeleryMonitor` and the document page.

Needs Slice 0 (task id) to key logs by job.

**Files:** `worker/tasks/_ingest_common.py`, `worker/tasks/extract.py`, `routers/admin.py` and/or `routers/internal.py`, `ui/src/app/(authenticated)/documents/[id]/page.tsx`, `CeleryMonitor.tsx`.

---

## Recommended sequencing

1. **Slice 0** (prerequisite) — small.
2. **Slice C** (running-tasks + auto-refresh) — small, high immediate value, low risk (read-only, extends existing).
3. **Slice B** (cancel) — medium; the operational win you asked for after the New-Testament incident.
4. **Slice A** (progress %, coarse v1) — medium; visible UX.
5. **Slice D** (per-job logs, v1 then v2) — largest; do last.

Each slice is independently shippable. TDD per repo rules; each backend slice gets unit + integration tests, cancel + progress get an e2e.

## Open decisions for approval

- **D1:** Progress granularity — ship coarse v1 (worker stages) now, defer fine brain-api %? (recommended)
- **D2:** Per-job logs — v1 (`processing_details.events`) first, or go straight to Redis-backed v2?
- **D3:** Cancel scope — document-owner + site-admin, or site-admin only?
- **D4:** Start order — confirm the sequencing above (Slice 0 → C → B → A → D).
