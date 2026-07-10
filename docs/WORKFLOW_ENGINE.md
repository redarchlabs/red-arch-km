# Workflow Engine (BPMN 2.0.2)

How KM2's workflow automation is implemented: a durable, token-based execution
engine with BPMN 2.0.2 semantics, a React-Flow designer, an SSE live-run overlay,
and an AI-assistant toolset — built as an additive layer over the original
change-driven automation, with **zero behaviour change** to pre-existing
workflows.

- **Backend:** `services/api/src/api/services/workflow/*`, `routers/{workflows,inbound,internal}.py`, `models/workflow.py`, `repositories/workflow.py`, migrations `018–020`.
- **Frontend:** `ui/src/components/workflows/*`, `ui/src/lib/api/{workflows,connections,inboundEndpoints,runStream}.ts`, `ui/src/app/(authenticated)/workflows/*`.
- **AI tools:** `services/api/src/api/services/agent.py`.

---

## 1. Goals & shape

The original engine was a single-path graph *walker*: it evaluated one cursor
from the trigger, resolved condition/switch branches at evaluation time, and ran a
flat list of `action` nodes linearly. It was well-built (outbox-driven,
RANGE-partitioned, `FOR UPDATE SKIP LOCKED` claiming, per-event savepoints, RLS
tenant downgrade, idempotent `ON CONFLICT`) but had no parallelism, error
handling, events, variables, human tasks, sub-processes, or integrations.

This effort adds the **full BPMN 2.0.2 element set and execution semantics** while
reusing every proven pattern. The single most important design choice is the
**dual-engine cutover**: the legacy walker stays; a new token engine is selected
per version, so existing published v1 workflows run byte-for-byte unchanged.

---

## 2. The shared `definition` contract (schema_version 2)

A workflow version stores a graph: `{schema_version, nodes:[{id,type,data,position}], edges:[{source,target,source_handle}]}`. It's the single artifact the frontend and backend agree on.

Adopting the **Camunda split**, a node's `type` is a *BPMN category* that drives
both the on-canvas shape and the job-vs-wait token semantics; the concrete subtype
lives in `data`:

| `type` | Renders as | `data` subtype | Token semantics |
|---|---|---|---|
| `trigger` | start circle | `operations`, `field_filter`, `schedule`, `source` | starts a run |
| `task` | rounded rect | `task_type`: service·send·script·businessRule·user·receive·call·subProcess·manual | service/send/script/businessRule = **sync job**; user/receive/call/subProcess/manual = **wait-state** |
| `gateway` | diamond | `gateway_type`: exclusive·parallel·inclusive·event_based | route / fork / join |
| `event` | circle | `position`: intermediate·end·boundary + `event_type`: timer·message·signal·error·escalation·terminate·none | catch / throw / park / kill |

- **Legacy 5 types** (`action`, `condition`, `switch`, `delay`, plus `merge`/`passthrough`) are *interpreted, never rewritten* — published versions are immutable by DB trigger. `services/workflow/compat.py` normalizes them to the BPMN categories at **read time only**.
- **Edge handles** (`source_handle`): `true` · `false` · `default` · `error` · `boundary` · `case-<id>` · `null`.
- **Structural validation** is a Pydantic model (`schemas/workflow_definition.py`, `WorkflowDefinitionModel`); **semantic** rules (reachability, gateway arity, boundary attachment, loop-progress) live in `services/workflow/validation.py` and are mirrored rule-for-rule by the frontend's `validation.ts`. `constants.py` is the shared vocabulary both sides import.

**Engine selection** (`dispatcher._use_token_engine`): `schema_version >= 2` OR any
new node type → token engine; else the legacy walker. Behind
`WORKFLOW_TOKEN_ENGINE_ENABLED` (default on).

---

## 3. Token execution engine

`services/workflow/engine.py` — `TokenEngine`. A run advances as **durable tokens**
(the single legacy cursor generalized to many cursors per run).

### Storage (migration 018)
`workflow_run_tokens` — a partitioned table, `created_at` pinned to `run.created_at`
so a run's tokens co-locate in one partition. Columns: `seq` (BigInt identity =
claim order), `node_id`, `arrived_from_node_id`/`arrived_via_handle` (join
edge-counting), `status` (active→running→waiting|completed|dead), `wait_kind`
(timer·user_task·receive·join·boundary·subprocess·retry·event_based), `resume_at`,
`correlation_key`, `parent_token_id`, `depth`, `lease_owner`/`leased_at` (crash
reaper), `data` JSONB. FORCE RLS + partition maintenance via the same helpers as
the other partitioned tables. Migration 018 also adds `workflow_runs`
`variables`/`step_seq`/`parent_run_id`/`parent_token_id`/`dead_letter` and
`workflow_run_steps.token_id`.

### Concurrency invariants (carried from the outbox path)
1. **Exactly-once claim** — `advance_tokens` claims a cross-org batch of `active`
   tokens with `UPDATE … WHERE status='active' … FOR UPDATE SKIP LOCKED`.
2. **Per-run serialization** — before mutating a run, `pg_try_advisory_xact_lock(hashtext(run_id))`.
   Two workers can't double-fire a join or double-allocate `step_seq`.
3. **Per-tenant RLS downgrade** — claim cross-org on the privileged role, then
   `_enter_tenant(org_id)` (`SET LOCAL ROLE app_user` + tenant GUC) per token; a
   handler never runs on the privileged role.
4. **Crash reaper** — a `running` token whose lease is older than `LEASE_TTL_SECONDS`
   is requeued by `resume_due_tokens`.
5. **Budgets** — `MAX_RUN_STEPS` (run-wide `step_seq`), `MAX_TOKENS_PER_RUN`,
   `MAX_TOKEN_DEPTH` guarantee termination, which lets us **allow bounded loops**
   (cycles are legal).

### The loop
- `start_run` seeds one `active` token per trigger node.
- `advance_tokens(limit)` — the cross-org sweep (worker beat → internal endpoint).
- `drive_run(run)` — advance ONE run to quiescence within the caller's txn/tenant
  (synchronous dispatch, manual run, tests). Respects the advisory lock: if another
  worker holds it, returns `{skipped}` rather than racing.
- `_advance_one(token)` → `_dispatch(node)` → a `NodeOutcome` → `_apply`. Outcome
  kinds: `advance` · `emit` (join fire) · `park` · `noop` · `complete` · `terminate`
  · `fail`. `_settle_run` recomputes run status from token statuses.

---

## 4. Control flow — gateways, joins, loops

`_dispatch_gateway`:
- **Exclusive** (`_exclusive_route`) — `data.expr` (true/false) or `data.cases` with a `default`; jsonlogic-evaluated against the run context.
- **Parallel fork** — emit a token on every out-edge. **AND-join** (`_parallel_join`) — buffer arriving tokens (`waiting/join`), fire only when every incoming edge has a buffered token.
- **Inclusive OR-join** (`_inclusive_join`) — **reachability / dead-path aware**: fire once NO other live token can still reach the join (`_forward_reachable_nodes` = forward BFS over static edges). This converges correctly after an exclusive split, where an AND-join would deadlock.
- **End events** — `none`→complete, `terminate`→kill all live tokens + succeed, `error`→fail the run (an uncaught throw).

---

## 5. Error handling (P1)

- **Retry** (`services/workflow/retry.py`) — opt-in per task via `data.retry = {max_attempts, base_delay_seconds, max_delay_seconds}`. A retryable failure parks the token `waiting/retry` with a **full-jitter exponential** `resume_at`; the timer sweep reactivates it to re-dispatch the same node (retry = timed token re-entry, no new machinery). Tasks with no policy keep legacy fail-fast.
- **Error boundary events** (`_error_boundary_for`) — an error boundary attached to a task turns a terminal failure into try/catch: the token routes to the boundary node → its error path; the run does **not** fail or dead-letter. Precedence: **error boundary > `continue_on_error` > dead-letter fail**. Prefers a code-specific boundary, falls back to catch-all.
- **Dead-letter + replay** — an exhausted, uncaught failure sets `run.dead_letter = True` (surfaced as a DLQ badge, replayable via the `retry_workflow_run` tool, which reactivates dead tokens and re-drives).

---

## 6. Data — variables, decisions, transforms

- **Run variables** — `workflow_runs.variables` JSONB, merged at the DB level (`variables || cast(:patch AS jsonb)`) so parallel tokens setting different keys don't clobber; synced in-memory (sessions use `expire_on_commit=False`). Expression context = `{before, after, vars}`.
- **Decision-table task** (`businessRule`, `decision.py`) — an ordered rule list (jsonlogic conditions, first/collect hit policy) whose outputs become run variables; the home for data-derivation branching.
- **Script/transform task** (`script`, `expression.py`) — a `{var: jsonlogic-expr}` mapping (Tier-1 JSON template). **Deliberately no arbitrary/Turing-complete code** — jsonlogic only (whitelisted ops, no attribute access, no eval).
- **`capture`** — any task may set `data.capture = "var"` to publish its output as a run variable (e.g. an `http_request` response).

---

## 7. Human tasks (P3)

Wait-state tasks (user/receive/manual) park on first arrival. `signal_token(run, node_id/correlation_key, variables, output)` completes a parked token: it merges decision `variables` (so a gateway can route on the outcome), stamps a `_completed` marker, and reactivates it; the re-dispatch records the completion and advances (mirrors the timer `_timer_armed` pattern).

Exposed two ways: the `complete_workflow_task` **assistant tool** and the
`POST /workflows/runs/{run_id}/complete-task` **REST endpoint** (the product
user-task inbox: `ui/.../workflows/inbox` + `UserTaskActions`, Approve/Reject →
`{approved}`).

---

## 8. Timers & scheduling (P3)

- **Intermediate timer catch** — parks `waiting/timer` with `resume_at`; the sweep resumes it.
- **Timer/escalation boundary** — a wait task with an attached timer boundary parks with an SLA `resume_at` + an `_armed` marker (single-token model, no racing sibling). On re-dispatch, an armed-but-not-completed token means the timer fired → route the interrupting escalation path; **completion always wins the race** (checked first).
- **Cron scheduling** (`schedule.py is_schedule_due`) — a scheduled trigger's `schedule` accepts `{cron: "<5-field>"}` (croniter) as well as `{every_minutes: N}`; wired into `run_due_schedules`. Catch-up convention consistent with the interval case.

---

## 9. Integrations (P4)

- **Connections + secrets** (migration 019, `workflow_connections`, FORCE RLS) — a reusable, org-scoped credential; the secret is **Fernet-encrypted** at rest (`services/crypto.py`, `ORG_ENCRYPTION_KEY`) and decrypted **only at execute time** in the runner (`ActionExecutor._resolve_connection`) — never in the definition, a step output, an input snapshot, or a log. REST CRUD redacts the secret to `has_secret`.
- **`http_request` connector task** — resolves a connection, injects auth (bearer / api-key header / basic), and calls `base_url + path` (or a literal url). Carries the **same deny-by-default SSRF guard** as `send_webhook` (host must be allow-listed and not a private IP). In `SIDE_EFFECTING_ACTIONS` (a manual run needs a real record).
- **Inbound webhooks** (migration 020, `workflow_inbound_endpoints`, hashed token) — a public `POST /api/inbound/{token}` (routers/inbound.py) resolves the endpoint by token hash on a privileged session, **downgrades to the endpoint's org**, and seeds a run with the JSON body as input (`trigger_operation="webhook"`); the sweep drives it. Admin CRUD mints the token once.
- **Call activity / sub-process** — a `call`/`subProcess` task starts a **child workflow run** (`parent_run_id`/`parent_token_id`/`depth+1`). Synchronous inline drive when the child completes within the parent's transaction (the child is freshly created, so its advisory lock is uncontended — no deadlock). If the child parks on its own wait, the parent parks `waiting/subprocess` and `_signal_parent` (a conditional raw UPDATE, safe without the parent lock since the token is parked) reactivates it when the child terminates. `MAX_TOKEN_DEPTH` bounds recursion.

### Record read/write actions (record-state platform)

- **`get_record`** (read-only) — load a record's live fields into a run variable: `{target_slug, mode: by_id|latest|first, record_id?, filters?, capture}`. Output is the record's slug-keyed fields (or `{}` when none match) → read downstream as `{{ vars.<capture>.<field> }}`. The read-back the engine otherwise lacked (`update_record_field` only ever touched the triggering record).
- **`update_record`** — write **multiple** fields of a targeted record: `{target_slug?, mode, record_id?/filters?, values}`. Omit `target_slug` to update the triggering record. `values`/`filters` render `{"$ref": ...}` **and** `{{ }}` templates. Writes emit an outbox change event — an announcer keyed off the same entity must only READ, never write it back (loop).
- Both resolve the target entity via `ctx.repo_for_slug` (org-scoped, RLS) — same-org any-entity, never cross-tenant.

### Inline dispatch on entity change (`run_inline_on_change`)

- A workflow flagged `run_inline_on_change` (migration **024**; `PATCH /workflows`, MCP `km2_update_workflow`) fires **synchronously in the record-write request** (`entity_records._dispatch_inline_workflows`) instead of waiting for the beat sweep — for latency-sensitive reactions (a robot announcing a state change the instant it's saved).
- It keys off the **exact outbox row this request wrote** (`repo.last_change_event`, not a re-query — avoids racing a concurrent writer), runs in a `begin_nested()` savepoint under a hard time budget (`_INLINE_DISPATCH_BUDGET_SECONDS`), and swallows failures so the **record write always commits**. The real outbox row stays `pending`, so the beat sweep **dedups** the inline runs (same `workflow × outbox event`) and still fires any non-inline workflows on the same change. A cheap partial-indexed EXISTS gate (migration **027**) keeps the per-write cost flat.
- **Semantics:** side effects are **at-least-once** and fire **before** the request commits — keep inline workflows short (a few fast steps) and idempotent-friendly (announcements, not payments); heavy multi-step LLM work belongs on the async beat path.

---

## 10. Live-run visualization

- **SSE endpoint** — `GET /workflows/runs/{run_id}/stream` (routers/workflows.py). *Poll-to-stream*: a fresh short-lived session per ~1s tick (never pins a pool connection), emitting a `snapshot` frame **only when state changes** and `done` on terminal (or a ~15-min cap). No engine coupling / no Redis. `_run_stream_snapshot` = a per-node status map (recorded step status wins; a node holding only a live token shows waiting/running) + live token positions.
- **Frontend overlay** — `runOverlay.ts` (status → 5-state ring), `lib/api/runStream.ts` (`parseSseFrame` + `streamRun`, the fetch+reader SSE pattern), `useRunStream` (SSE-first, **`listRunSteps` polling fallback** on failure), `RunOverlayCanvas` (read-only React Flow feeding the chrome into the existing `NodeChromeContext` status-ring). Wired into `RunMonitor` as a per-run "Show live diagram" toggle. Nodes light up sky=running, amber=waiting/retry, green=done, rose=failed.

---

## 11. The designer (frontend)

A 3-pane React-Flow designer (`components/workflows/designer/*`): a **zustand + zundo** store (`store.ts`) as the single graph source of truth (undo/redo, copy/paste, boundary-cascade delete), a draggable categorized palette with drop-to-place, `isValidConnection` smart-connect, `LabeledEdge`, a ⌘K command palette, and **elkjs `layered` auto-layout** (dynamic-imported).

BPMN visual language via `nodes/BaseNode.tsx` + `nodeMeta.ts` + `glyphs.tsx`:
`EventNode` (circles, ring weight = start/intermediate/end), `GatewayNode`
(diamonds with X/+/O markers), `TaskNode` (corner glyph), `BoundaryEventNode`;
the legacy 5 types refactored onto `BaseNode`. `graphSerde.ts` maps definition ↔
React-Flow (boundary `attached_to` ↔ `parentId/extent`, React-Flow-only keys
stripped on save). Per-type inspectors: gateway routing, decision table, script
transform, retry policy, connector, event/boundary fields.

---

## 12. AI-assistant workflow tools

`services/agent.py` gains a full workflow lifecycle toolset (mirroring the
forms-tools pattern), with the **same permission boundaries as the REST API**:

- **Author/maintain (org-admin):** `list_workflows`, `get_workflow`, `update_workflow`, `save_workflow_definition` (a full BPMN graph, **validated before any write**), `validate_workflow`, `publish_workflow`.
- **Run:** `run_workflow` — gated on the workflow's own `run_permission` via `can_run` (NOT org-admin), replicating the manual-run security (record_id loads server-side; side-effecting graphs refuse fabricated data). `test_workflow` dry-runs with no side effects.
- **Debug/monitor (org-admin):** `list_workflow_runs`, `get_workflow_run` (steps + tokens), `retry_workflow_run`, `complete_workflow_task`.

---

## 13. Security invariants (do not regress)

1. **Exactly-once claim** — `FOR UPDATE SKIP LOCKED` (cross-run) + `pg_try_advisory_xact_lock(run_id)` (intra-run) + running-lease reaper.
2. **Per-tenant RLS** — claim on the privileged role, `_enter_tenant` per unit, never run a handler on the privileged role.
3. **Published-version immutability** — `compat.py` normalizes on read only.
4. **Secret confidentiality** — connection secrets Fernet-encrypted; decrypted only at execute time; never in definition/output/snapshot/logs; REST redacts to `has_secret`.
5. **SSRF** — deny-by-default allow-list + private-IP guard on `send_webhook` and `http_request`.
6. **Manual-run trust** — never run a side-effecting step on client-supplied data without a server-loaded `record_id` (guard inspects both legacy `action` and v2 `task` nodes — a parity fix shipped with the agent tools).
7. **Inbound tokens** — only the SHA-256 hash is stored; a leaked row can't be replayed.

---

## 14. Migrations

- **018** — `workflow_run_tokens` (partitioned + FORCE RLS + partition maintenance); `workflow_runs` variables/step_seq/parent_*/dead_letter; `workflow_run_steps.token_id`.
- **019** — `workflow_connections` (Fernet secret, RLS).
- **020** — `workflow_inbound_endpoints` (hashed token, RLS).
- **024** — `workflows.run_inline_on_change` (boolean, default false) — the inline-dispatch flag.
- **027** — partial index `ix_workflows_inline_entity` on `(org_id, entity_definition_id) WHERE enabled AND run_inline_on_change` — flat-cost EXISTS gate for the per-record-write inline check.

Integration tests use `Base.metadata.create_all` (not migrations), so every model
is registered with the metadata and the new tables are added to the conftest RLS
list.

---

## 15. Testing

- **Unit** (`services/api/tests/unit/`): definition/compat/validation, retry backoff + policy, decision-table hit policies, transform, error-boundary matcher, cron due-check, connector auth headers.
- **Integration** (real Postgres testcontainer, `admin_session`/`session` + `set_tenant`): token engine (linear/routing/fork-join/timer/variables/loop-budget), dual-engine dispatch + walker parity, retry, error boundaries, decision + OR-join, human tasks, call-activity (incl. nested-wait resume), inbound webhooks, timer boundary, connections (RLS isolation + secret non-leak + mocked `http_request`), run-stream snapshot, and the agent workflow tools.
- **Frontend** (vitest): pure logic (nodeMeta, validation, graphSerde, store, isValidConnection, retryPolicy, errorEdge, decisionTable, transform, gatewayRouting, autoLayout, runOverlay, parseSseFrame, userTasks) + component tests; visual components rely on tsc + those tested helpers.

**Test invocation** (uv workspace): run from repo root as
`uv run --no-sync pytest -o consider_namespace_packages=true services/api/tests/...`
after `uv sync --all-packages --all-extras` — a plain `uv run` re-syncs root-only
and drops the `api` editable install.

---

## 16. Deferred (documented, not built)

OAuth2 client-credentials connections, Redis per-connection rate limits, JSON
connector templates, email attachments, non-interrupting boundary events, `.bpmn`
XML import/export, compensation events, and the live-run *timeline scrubber* /
edge-token animation (the node-coloring overlay ships).
