# Workflow Engine

KM2's workflow automation: a durable, token-based execution engine with BPMN
2.0-style semantics, a React Flow visual designer, and an AI-assistant toolset.
This doc is for engineers extending the engine or authoring workflows. Backend
code lives in `services/api/src/api/services/workflow/`; the beat sweep that
drives it lives in `services/worker/`; the designer lives in
`ui/src/components/workflows/`.

## Table of Contents

- [Overview](#overview)
- [Core concepts](#core-concepts)
- [The definition graph](#the-definition-graph)
- [Storage: outbox, runs, run_steps, tokens](#storage-outbox-runs-run_steps-tokens)
- [Triggers and start events](#triggers-and-start-events)
- [Action catalog](#action-catalog)
- [Templating and variables](#templating-and-variables)
- [Conditions and branching](#conditions-and-branching)
- [Connections and inbound webhooks](#connections-and-inbound-webhooks)
- [Run permissions and manual runs](#run-permissions-and-manual-runs)
- [Execution model: beat, outbox, debugging](#execution-model-beat-outbox-debugging)
- [Error handling, timers, sub-processes](#error-handling-timers-sub-processes)
- [The designer](#the-designer)
- [AI-assistant tools](#ai-assistant-tools)
- [API endpoints](#api-endpoints)
- [Migrations](#migrations)
- [Security invariants](#security-invariants)
- [Testing](#testing)

## Overview

A workflow is a graph the frontend and backend agree on, stored as JSON on an
immutable published version. When a record changes, a webhook arrives, a
schedule comes due, or an operator clicks Run, the engine starts a **run** and
advances it node by node, recording each step. The engine is **poll-based**: a
Celery beat process sweeps the `workflow_outbox` and the token queue on a fixed
cadence. **No beat means no automation fires** — a change is recorded in the
outbox but never dispatched until a sweep claims it (interactive paths such as
manual run and inbound webhooks drive the run inline instead, see below).

Two engines coexist. The original **legacy walker** interprets a single-path
graph (`trigger → condition/switch → action …`). The **token engine**
(`services/api/src/api/services/workflow/engine.py`, class `TokenEngine`) adds
the BPMN element set — parallel/inclusive gateways, joins, boundary events,
timers, retries, sub-processes, variables, human tasks. Selection is per
version (`WorkflowDispatchService._use_token_engine` in `dispatcher.py`):
`schema_version >= 2` or any new node type routes to the token engine, else the
walker. Existing published v1 workflows run unchanged. Everything below is
current behavior; the dual-engine split is a compatibility detail, not a knob
authors touch.

## Core concepts

| Term | What it is |
|---|---|
| **Workflow** | A named automation (`workflows` table), optionally bound to an entity. Has an `active_version_id` and a `run_permission`. |
| **Version** | A snapshot of the graph (`workflow_versions`). A published version is immutable (DB trigger `workflow_versions_immutable`). Publishing swaps `active_version_id`. |
| **Definition** | The graph JSON on a version: `{schema_version, nodes, edges}`. |
| **Outbox event** | A recorded record-change (`workflow_outbox`) waiting to be dispatched to matching workflows. |
| **Run** | One execution of one workflow version (`workflow_runs`), with a status and an `input_snapshot`. |
| **Step** | One action execution inside a run (`workflow_run_steps`) — the audit trail. |
| **Token** | A live cursor inside a token-engine run (`workflow_run_tokens`); a run may have many. |

## The definition graph

A version's `definition` is `{schema_version, nodes: [...], edges: [...]}`. A
node is `{id, type, data, position}`; an edge is `{source, target,
source_handle}`. The shared vocabulary lives in
`services/api/src/api/services/workflow/constants.py`; structural validation is
the Pydantic `WorkflowDefinitionModel` (`schemas/workflow_definition.py`) and
semantic validation (reachability, gateway arity, boundary attachment) lives in
`services/api/src/api/services/workflow/validation.py`, mirrored on the
frontend by `ui/src/components/workflows/designer/validation.ts`.

A node's `type` is a BPMN category that drives its on-canvas shape and its
job-vs-wait token semantics; the concrete subtype lives in `data`:

| `type` | Renders as | `data` subtype | Token semantics |
|---|---|---|---|
| `trigger` | start circle | `operations`, `field_filter`, `source`, `schedule`, `inputs` | starts a run |
| `task` | rounded rect | `task_type`: `service` · `send` · `script` · `businessRule` · `user` · `receive` · `call` · `subProcess` · `manual` | service/send/script/businessRule = **sync job**; user/receive/call/subProcess/manual = **wait-state** |
| `gateway` | diamond | `gateway_type`: `exclusive` · `parallel` · `inclusive` · `event_based` | route / fork / join |
| `event` | circle | `position`: `intermediate`·`end`·`boundary` + `event_type`: `timer`·`message`·`signal`·`error`·`escalation`·`terminate`·`none` | catch / throw / park / kill |

The **legacy node types** (`action`, `condition`, `switch`, `delay`, plus
`merge`/`passthrough`) are interpreted, never rewritten — published versions are
immutable, so `services/api/src/api/services/workflow/compat.py` normalizes them
to BPMN categories at read time only.

Edge `source_handle` values route branches: `true`, `false`, `default`,
`error`, `boundary`, `case-<id>`, or `null`. Reserved handle constants are in
`constants.py`.

## Storage: outbox, runs, run_steps, tokens

Introduced by migration **009** (`009_workflow_engine`), the runtime tables are
RANGE-partitioned by `created_at` (with a DEFAULT partition so inserts always
land) and carry FORCE row-level security keyed on `app.current_tenant_id`.
`workflow_ensure_partitions(months_ahead)` pre-creates month partitions; a beat
job calls it daily.

| Table | Purpose | Notable columns |
|---|---|---|
| `workflows` | The automation | `entity_definition_id` (nullable, migration 023), `enabled`, `active_version_id`, `run_permission`, `run_inline_on_change` |
| `workflow_versions` | Immutable graph snapshot | `version_number`, `status` (`draft`/`published`/`archived`), `definition` JSONB |
| `workflow_outbox` | Recorded change events | `operation` (`create`/`update`/`delete`), `source` (`record`/`form`, migration 013), `before_data`/`after_data`, `status` (`pending`/`claimed`/`done`/`skipped`), `seq` |
| `workflow_runs` | One execution | `trigger_operation`, `status`, `input_snapshot`, `conditions_matched`, `resume_at`/`resume_node_id` (migration 014), plus token-engine columns `variables`/`step_seq`/`parent_run_id`/`dead_letter` (migration 018) |
| `workflow_run_steps` | Per-action audit trail | `node_id`, `action_type`, `status`, `input`/`output`, `error`, `attempts`, `token_id` (migration 018) |
| `workflow_run_tokens` | Live token cursors (token engine) | `node_id`, `status` (active→running→waiting/completed/dead), `wait_kind`, `resume_at`, `correlation_key`, `parent_token_id`, `depth`, `lease_owner`/`leased_at`, `data` JSONB (migration 018) |

A DB `CHECK` constraint pins each status/operation vocabulary so a direct SQL
write can't introduce a value the dispatcher won't match. Models are in
`services/api/src/api/models/workflow.py`; repositories in
`services/api/src/api/repositories/workflow.py`.

## Triggers and start events

A workflow starts in one of four ways. The trigger node's `data` declares which.

| Trigger | How it fires | Path |
|---|---|---|
| **Record change** | An entity create/update/delete writes a `workflow_outbox` row; the beat sweep matches it against enabled workflows on that entity | async (beat) or inline (see `run_inline_on_change`) |
| **Manual (none start)** | `data.source = "manual"`, `entity_definition_id` NULL; run on demand with declared `inputs` | inline, `POST /api/workflows/{id}/run` |
| **Inbound webhook** | An external `POST /api/inbound/{token}` seeds a run with the JSON body | **inline** (real-time) |
| **Scheduled** | The trigger `data.schedule` is due | async (beat run-timers sweep) |

**Match logic** (`evaluator.trigger_matches`): a trigger may pin `operations`
(absent = any; explicit `[]` = schedule-only, no change trigger), a
`field_filter` (fires only when a listed field changed on an update), and a
required `source` (`record`/`form`/`any`). A `source: "manual"` trigger is
**never** fired by the outbox — it is on-demand only.

**Manual / none-start inputs** (`manual_inputs.py`): a manual trigger declares
`data.inputs` as `[{key, label, type, required}]`. `manual_inputs` validates and
coerces the submitted payload against that schema, so the run's `inputs` context
is well-typed and can't carry undeclared keys. Inputs are addressable as
`{{ inputs.<key> }}` in actions and `inputs.<key>` in gateway/condition
expressions.

**Inbound webhooks** run the JSON body into the run as
`input_snapshot.after` with `trigger_operation = "webhook"` — so a handler reads
the payload as `{{ after.<field> }}` (`inbound.trigger_from_inbound`).

**Scheduled** triggers carry `data.schedule` as `{cron: "<5-field>"}` (croniter)
or `{every_minutes: N}`; `schedule.is_schedule_due` decides, driven by
`WorkflowDispatchService.run_due_schedules`. A scheduled run has no triggering
record (`trigger_operation = "scheduled"`); cadence is derived from the last
scheduled run's timestamp, so the sweep is safe at any interval finer than the
schedule.

### Inline dispatch on record change (`run_inline_on_change`)

A workflow flagged `run_inline_on_change` (migration **024**; set via
`PATCH /api/workflows/{id}`) fires **synchronously in the record-write request**
(`entity_records._dispatch_inline_workflows`) instead of waiting for the beat
sweep — for latency-sensitive reactions. It keys off the exact outbox row the
request wrote, runs in a `begin_nested()` savepoint under a hard time budget,
and swallows failures so the record write always commits. The real outbox row
stays `pending`, so the later beat sweep **dedups** the inline run (same
`workflow × outbox event`) and still fires any non-inline workflows on the same
change. A partial-indexed EXISTS gate (migration **027**,
`ix_workflows_inline_entity`) keeps the per-write cost flat. Semantics: side
effects are **at-least-once** and fire **before** the request commits — keep
inline workflows short and idempotent-friendly; heavy multi-step LLM work
belongs on the async beat path.

## Action catalog

Every action is a handler registered in `ACTION_REGISTRY`
(`services/api/src/api/services/workflow/actions.py`) keyed by its `type`
string. A `task` node names its action in `data`; the runner resolves it,
renders its config against the run context, and records the output as a step.
`SIDE_EFFECTING_ACTIONS = {send_email, send_webhook, send_form, http_request}`
gates the manual-run trust check (see [Security](#security-invariants)).

| Action `type` | Purpose | Key config inputs |
|---|---|---|
| `update_record_field` | Set ONE field of the triggering record | `field`, `value` |
| `update_record` | Set MULTIPLE fields of a targeted record | `values` (map); optional `target_slug`, `mode` (`latest`/`first`/`by_id`), `record_id`/`filters` — omit `target_slug` to update the trigger record |
| `create_record` | Create a record in any same-org entity | `target_slug`, `values` |
| `get_record` | Read a record's live fields into a variable (read-only) | `target_slug`, `mode`, `record_id`/`filters`, `capture` |
| `send_email` | Send a templated email | `to` (template or `$ref`), `subject`, `body`; no-op when SMTP unconfigured |
| `send_form` | Mint + email an intake-form link bound to the triggering record | `form_id`, `recipient`/`recipient_field` |
| `send_webhook` | POST `{before, after, ...body}` to a URL (SSRF-guarded) | `url`, `body` |
| `http_request` | Authenticated HTTP call via a reusable connection (SSRF-guarded) | `connection`, `method`, `path`/`url`, `headers`, `body`, `capture` |
| `knowledge_search` | Answer from the org knowledge base (hybrid RAG); read-only | `query`, `synthesize` (default true), `use_knowledge_graph`, `folder_tags`/`tags`/`access_keys` scope, `capture` |
| `summarize` | Compress text to one short spoken line via a small LLM | `text`, `question`, `max_words`, `instruction`, `model` |
| `llm_decide` | Constrained-LLM step: pick the robot's next move from an enum vocabulary; returns `{say, gesture, mood, done, reason}` | `question`, `context`, `system`, `gestures`, `moods`, `history`, `model`, `capture` |
| `llm_grade` | Score a free-text answer 0–100 vs a rubric; returns `{score, passed, feedback}` | `answer`, `question`, `rubric`, `pass_threshold` (default 70), `model`, `capture` |
| `grade_quiz` | Deterministically grade an MCQ assessment server-side; returns `{score, passed, correct, total, answered}` | `assessment_id`, `answers` (`{question_id: choice}` preferred, else positional `a1..aN` inputs), `pass_threshold`, `question_slug`/`assessment_ref`/`order_field`, `capture` |
| `llm_respond` | Role-play a persona + coach a learner (training simulator); returns `{reply, coach, done}` | `persona`, `scenario`, `objective`, `grounding`, `history`, `user_message`, `model`, `capture` |
| `log` | No-side-effect breadcrumb (testing/audit) | `message` |

Every handler has a matching `simulate()` that dry-runs without side effects or
network/DB access, powering the test endpoint (`POST /api/workflows/{id}/versions/{version_id}/test`).

The LMS course experience (`grade_quiz`, `llm_grade`, `llm_respond`,
`knowledge_search`) is documented end-to-end in [LMS.md](LMS.md). `grade_quiz`
grades correctly but does **not** by itself make a quiz tamper-proof — its
docstring spells out the trust model; real tamper-resistance needs
entity/field-level access control (a platform feature).

## Templating and variables

Action config is resolved against a context built by `_trigger_context`
(`actions.py`): `{before, after, inputs, vars, now, today}`.

- **`{"$ref": "after.field"}` envelopes** — unwrap to the typed value (preserves
  type). Also `before.`, `inputs.`, `vars.`.
- **`{{ ... }}` string templates** — render to text. Namespaces: `before`,
  `after`, `inputs`, `vars` (each may be dotted, e.g. `{{ vars.kb.answer }}`),
  plus bare clock tokens `{{ now }}` (UTC ISO-8601) and `{{ today }}` (UTC
  `YYYY-MM-DD`). The token regex is enforced — arbitrary attribute access is
  impossible.
- **Deep rendering** — `_render_deep` walks nested dict/list config (e.g. an
  `http_request` JSON body) rendering every string. The `http_request` URL/host
  is deliberately NOT templated — it stays under the connection + SSRF
  allow-list.

**Run variables** are the mechanism for passing data between steps. A task with
`data.capture = "name"` publishes its output under `vars.name`, read downstream
as `{{ vars.name.<field> }}` (`TokenEngine` merges captures into
`workflow_runs.variables` at the DB level so parallel tokens don't clobber each
other). Example chain: `knowledge_search` (capture `kb`) → `summarize`
(`text: "{{ vars.kb.answer }}"`) → `http_request` robot `/say`
(`body: {"text": "{{ vars.answer }}"}`).

`now`/`today` are read once at context-build time; two steps in one run may see
timestamps a few ms apart (same `today`), which is fine for date stamping.

## Conditions and branching

**Legacy walker** (`evaluator.evaluate_graph`): a `condition` node evaluates
`data.expr` (jsonlogic) and follows the `true`/`false` handle; a `switch` node
evaluates ordered `data.cases`, taking the first truthy case's handle else
`default`. jsonlogic (`services/api/src/api/services/workflow/jsonlogic.py`) is
whitelisted ops only — no eval, no attribute access.

**Token engine** (`dispatcher.py` / `engine.py`):

- **Exclusive gateway** — `data.expr` (true/false) or `data.cases` with a
  `default`, jsonlogic-evaluated against the run context.
- **Parallel fork/AND-join** — a fork emits a token on every out-edge; an
  AND-join buffers arriving tokens and fires only when every incoming edge has a
  buffered token.
- **Inclusive OR-join** — dead-path aware: fires once no other live token can
  still reach the join (forward BFS over static edges), so it converges after an
  exclusive split where an AND-join would deadlock.
- **End events** — `none` completes the token; `terminate` kills all live tokens
  and succeeds; `error` fails the run.

Bounded loops (cycles) are legal: `MAX_RUN_STEPS`, `MAX_TOKENS_PER_RUN`, and
`MAX_TOKEN_DEPTH` budgets guarantee termination. A `businessRule` (decision
table, `decision.py`) and a `script`/transform task (`{var: jsonlogic-expr}`
mapping, `expression.py`) derive variables for downstream branching; the
transform is jsonlogic only — deliberately no Turing-complete code.

## Connections and inbound webhooks

**Connections** (migration **019**, `workflow_connections`, FORCE RLS) are
reusable org-scoped credentials for outbound calls. `auth_type` is one of
`none`, `bearer`, `api_key`, `basic` (`CONNECTION_AUTH_TYPES` in
`models/workflow.py`). The secret is **Fernet-encrypted** at rest
(`ORG_ENCRYPTION_KEY`) and decrypted **only at execute time** in the runner
(`_auth_headers` / `ResolvedConnection`) — never in the definition, a step
output, an input snapshot, or a log. REST CRUD redacts the secret to a
`has_secret` boolean. The `http_request` action resolves a connection, injects
its auth header, and calls `base_url + path` (or a literal `url`), behind the
same deny-by-default SSRF guard as `send_webhook` (host must be allow-listed and
not a private IP). `POST /api/workflows/connections/call` exercises a connection
directly (the robot call button).

**Inbound webhooks** (migration **020**, `workflow_inbound_endpoints`) expose a
public `POST /api/inbound/{token}` (`routers/inbound.py`). Only the SHA-256 hash
of the token is stored, so a leaked row can't be replayed. The request runs on a
privileged session to resolve the endpoint across tenants, then **downgrades to
the endpoint's org** before any write, and drives the run inline. When the
endpoint has an HMAC signing secret (migration **022**,
`signing_secret_encrypted`), a valid `X-KM2-Signature` header
(`t=<unix>,v1=<hmac-sha256>`, `webhook_signing.py`) is required; a
missing/invalid/stale signature returns one opaque `401` (no oracle). See
[MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md) for the integration surface
and [AUTHENTICATION.md](AUTHENTICATION.md#6-inbound-webhook-signing) for the
signature scheme.

## Run permissions and manual runs

`POST /api/workflows/{id}/run` executes the workflow's **published** version for
real against submitted inputs, gated by the workflow's `run_permission`
(migration **012**, JSONB, default `{"mode": "org_admin"}`). `permissions.can_run`
(`permissions.py`):

| `mode` | Who may run |
|---|---|
| `org_admin` (default) | Org admins only |
| `any_member` | Any org member |
| `roles` | Members holding a listed role or group (`role_ids` / `group_ids` matched against the caller's membership) |

Org admins always pass. The manual run performs real side effects and records a
`workflow_run` with `input_snapshot.manual = True`. A side-effecting step never
runs on client-supplied data without a server-loaded `record_id` — the trust
guard inspects both legacy `action` and v2 `task` nodes. The dry-run test
endpoint (`.../versions/{version_id}/test`) executes `simulate()` only. See
[RBAC.md](RBAC.md) for org roles and membership.

## Execution model: beat, outbox, debugging

Automation is poll-based. A Celery **beat** process
(`services/worker/src/worker/celery_app.py`) enqueues sweep tasks
(`services/worker/src/worker/tasks/workflow.py`) that POST to internal endpoints
(`routers/internal.py`, gated by `require_internal_api_key`). Each internal
handler claims cross-org on the privileged session, then downgrades to
`app_user` + the tenant GUC **per unit of work**, so every action write is
RLS-enforced for its org.

| Beat task | Internal endpoint | Default cadence (env override) | Does |
|---|---|---|---|
| `sweep_outbox` | `POST /api/internal/workflows/dispatch-batch` | 10s (`WORKFLOW_SWEEP_INTERVAL`) | Claim pending outbox events, match + run workflows |
| `advance_tokens` | `POST /api/internal/workflows/advance-tokens` | 10s (`WORKFLOW_TOKEN_INTERVAL`) | Reactivate parked tokens (timers/retries/crashed leases), drain the active queue |
| `run_timers` | `POST /api/internal/workflows/run-timers` | 30s (`WORKFLOW_TIMER_INTERVAL`) | Resume due delayed runs + fire due scheduled workflows |
| `maintain_partitions` | `POST /api/internal/workflows/maintain-partitions` | 86400s (`WORKFLOW_PARTITION_INTERVAL`) | Pre-create month partitions |

**No beat = no fires.** A record change writes an outbox row, but without a
running beat + worker the sweep never claims it. Most token-engine runs actually
complete synchronously inside dispatch; the token sweep exists to resume parked
waits and make progress after an interruption. Interactive paths (manual run,
inbound webhook, `run_inline_on_change`) bypass the beat and drive inline.

Concurrency is exactly-once: the sweep claims with `FOR UPDATE SKIP LOCKED`
(cross-run), takes `pg_try_advisory_xact_lock(run_id)` before mutating a run
(intra-run), and a lease reaper requeues a `running` token whose lease exceeds
the TTL.

### Debugging: follow outbox → runs → run_steps

1. **Outbox** — is there a `pending` row for the change? If not, the write
   didn't emit an event (check the entity/operation). If it stays `pending`, the
   **beat/worker is down** (check the site-admin beat heartbeat).
2. **Runs** — `GET /api/workflows/{id}/runs` (or `GET /api/workflows/runs/recent`
   for the org-wide feed). A run with `conditions_matched = false` means the
   trigger matched but the graph's conditions routed away; `status = failed`
   carries an `error`.
3. **Run steps** — `GET /api/workflows/runs/{run_id}/steps` shows each action's
   `status`, `input`, `output`, and `error` — the per-node audit trail. Token
   engine runs also carry live token positions (streamed via
   `GET /api/workflows/runs/{run_id}/stream`, SSE).

## Error handling, timers, sub-processes

These are token-engine features; the legacy walker is fail-fast.

- **Retry** (`retry.py`) — opt-in per task via `data.retry = {max_attempts,
  base_delay_seconds, max_delay_seconds}`. A retryable failure parks the token
  `waiting/retry` with a full-jitter exponential `resume_at`; the timer sweep
  re-dispatches the same node. No policy = legacy fail-fast.
- **Error boundary events** — an error boundary attached to a task turns a
  terminal failure into try/catch: the token routes to the boundary's error
  path; the run does not fail. Precedence: error boundary > `continue_on_error`
  > dead-letter fail.
- **Dead-letter + replay** — an exhausted, uncaught failure sets
  `run.dead_letter = True` (a DLQ badge), replayable via the `retry_workflow_run`
  tool.
- **Timers** — an intermediate timer catch parks `waiting/timer` with a
  `resume_at` the sweep resumes; a timer/escalation boundary on a wait task
  parks with an SLA `resume_at` — completion always wins the race against the
  timer.
- **Human tasks** — user/receive/manual tasks park on arrival;
  `signal_token` (via the `complete_workflow_task` tool or
  `POST /api/workflows/runs/{run_id}/complete-task`) merges decision variables,
  stamps a completion marker, and reactivates the token so a downstream gateway
  can route on the outcome.
- **Call activity / sub-process** — a `call`/`subProcess` task starts a **child
  run** (`parent_run_id`/`parent_token_id`/`depth+1`). It drives inline when the
  child completes within the parent's transaction; if the child parks, the
  parent parks `waiting/subprocess` and is reactivated when the child
  terminates. `MAX_TOKEN_DEPTH` bounds recursion.

## The designer

A three-pane React Flow designer (`ui/src/components/workflows/designer/`) with a
`zustand + zundo` store (`store.ts`) as the single graph source of truth
(undo/redo, copy/paste, boundary-cascade delete), a categorized drag-to-place
palette, smart-connect (`isValidConnection`), a ⌘K command palette, and elkjs
`layered` auto-layout. BPMN visuals come from `nodes/BaseNode.tsx` + `nodeMeta.ts`
+ `glyphs.tsx` (`EventNode` circles, `GatewayNode` diamonds, `TaskNode` corner
glyph, `BoundaryEventNode`). `graphSerde.ts` maps the definition ↔ React Flow
(boundary `attached_to` ↔ `parentId/extent`; React-Flow-only keys stripped on
save). Per-type inspectors cover gateway routing, decision table, script
transform, retry policy, connector, and event/boundary fields. Validation
(`validation.ts`) mirrors the backend rule-for-rule. A live-run overlay
(`runOverlay.ts`, `lib/api/runStream.ts`) colors nodes from the SSE snapshot
(running / waiting / done / failed), falling back to `listRunSteps` polling.

## AI-assistant tools

`services/api/src/api/services/agent.py` exposes the workflow lifecycle to the
chat agent with the **same permission boundaries as the REST API**:

- **Author/maintain (org-admin):** `list_workflows`, `get_workflow`,
  `update_workflow`, `save_workflow_definition` (a full BPMN graph, validated
  before any write), `validate_workflow`, `publish_workflow`.
- **Run:** `run_workflow` — gated on the workflow's own `run_permission` via
  `can_run` (NOT org-admin), replicating manual-run security; `test_workflow`
  dry-runs with no side effects.
- **Debug/monitor (org-admin):** `list_workflow_runs`, `get_workflow_run` (steps
  + tokens), `retry_workflow_run`, `complete_workflow_task`.

The same operations are available over MCP via `tools/km2-mcp` (e.g.
`km2_run_workflow`, `km2_save_workflow_definition`); see
[MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md).

## API endpoints

Mounted at `/api/workflows` (`routers/workflows.py`), `/api/inbound`
(`routers/inbound.py`), and `/api/internal` (`routers/internal.py`). Auth
column: **admin** = `require_org_admin`, **member** = `require_org_access`,
**run-perm** = the workflow's `run_permission`, **public** = token/signature,
**internal** = `require_internal_api_key`.

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /api/workflows/` | member | List workflows |
| `POST /api/workflows/` | admin | Create a workflow |
| `GET /api/workflows/{id}` | member | Get a workflow |
| `PATCH /api/workflows/{id}` | admin | Update (enabled, run_permission, run_inline_on_change) |
| `DELETE /api/workflows/{id}` | admin | Delete a workflow |
| `GET /api/workflows/{id}/versions` | member | List versions |
| `POST /api/workflows/{id}/versions` | admin | Create a draft version |
| `POST /api/workflows/{id}/versions/{vid}/publish` | admin | Publish (swaps active version) |
| `POST /api/workflows/{id}/versions/{vid}/test` | member | Dry-run (`simulate`, no side effects) |
| `POST /api/workflows/{id}/run` | run-perm | Run the published version for real |
| `GET /api/workflows/{id}/runs` | admin | Runs for a workflow |
| `GET /api/workflows/runs/recent` | admin | Org-wide recent-run feed |
| `GET /api/workflows/runs/{run_id}/steps` | admin | Step audit trail |
| `POST /api/workflows/runs/{run_id}/complete-task` | member | Complete a parked human task |
| `GET /api/workflows/runs/{run_id}/stream` | member | SSE live-run snapshot |
| `GET/POST /api/workflows/connections` | admin | List / create connections (secret redacted) |
| `PATCH/DELETE /api/workflows/connections/{id}` | admin | Update / delete a connection |
| `POST /api/workflows/connections/call` | member | Invoke a connection directly |
| `GET/POST /api/workflows/inbound-endpoints` | admin | List / create inbound endpoints (token shown once) |
| `DELETE /api/workflows/inbound-endpoints/{id}` | admin | Delete an inbound endpoint |
| `POST /api/inbound/{token}` | public | Receive an inbound webhook (inline run) |
| `POST /api/internal/workflows/{dispatch-batch,advance-tokens,run-timers,maintain-partitions}` | internal | Beat sweeps |

Request/response shapes are in `services/api/src/api/schemas/workflow.py` and
`schemas/workflow_definition.py`. A read-only versioned surface also exists under
`/api/v1/workflows` (`routers/v1/`); see [API.md](API.md).

## Migrations

Alembic, `services/api/alembic/versions/`.

| Migration | Adds |
|---|---|
| `009_workflow_engine` | `workflows`, `workflow_versions`, and partitioned `workflow_outbox`/`workflow_runs`/`workflow_run_steps` (RLS, immutability trigger, partition helper) |
| `012_workflow_run_permission` | `workflows.run_permission` JSONB (default `{"mode": "org_admin"}`) |
| `013_workflow_outbox_source` | `workflow_outbox.source` (`record`/`form`) |
| `014_workflow_run_delay` | `workflow_runs.resume_at`/`resume_node_id` + waiting partial index (legacy `delay`) |
| `018_workflow_token_engine` | `workflow_run_tokens` (partitioned, RLS); `workflow_runs` variables/step_seq/parent_*/dead_letter; `workflow_run_steps.token_id` |
| `019_workflow_connections` | `workflow_connections` (Fernet secret, RLS) |
| `020_workflow_inbound_endpoints` | `workflow_inbound_endpoints` (hashed token, RLS) |
| `022_inbound_signing_secret` | `workflow_inbound_endpoints.signing_secret_encrypted` (HMAC signing) |
| `023_nullable_workflow_entity` | `workflows.entity_definition_id` made nullable (manual/on-demand workflows) |
| `024_workflow_run_inline_on_change` | `workflows.run_inline_on_change` (inline-dispatch flag) |
| `027_workflows_inline_partial_index` | `ix_workflows_inline_entity` — flat-cost EXISTS gate for the inline check |

Integration tests build the schema with `Base.metadata.create_all`, so every
model is registered with the metadata and new tables are added to the conftest
RLS list.

## Security invariants

Do not regress these:

1. **Exactly-once claim** — `FOR UPDATE SKIP LOCKED` (cross-run) +
   `pg_try_advisory_xact_lock(run_id)` (intra-run) + running-lease reaper.
2. **Per-tenant RLS** — claim on the privileged role, `_enter_tenant` per unit;
   a handler never runs on the privileged role.
3. **Published-version immutability** — enforced by the DB trigger;
   `compat.py` normalizes legacy graphs on read only.
4. **Secret confidentiality** — connection secrets are Fernet-encrypted,
   decrypted only at execute time, never in a definition/output/snapshot/log;
   REST redacts to `has_secret`.
5. **SSRF guard** — deny-by-default allow-list + private-IP block on
   `send_webhook` and `http_request`.
6. **Manual-run trust** — never run a side-effecting step
   (`SIDE_EFFECTING_ACTIONS`) on client-supplied data without a server-loaded
   `record_id`; the guard covers both `action` and `task` nodes.
7. **Inbound tokens** — only the SHA-256 hash is stored; a signed endpoint
   additionally requires a valid `X-KM2-Signature`.

## Testing

- **Unit** (`services/api/tests/unit/`): definition/compat/validation, retry
  backoff, decision-table hit policies, transform, error-boundary matcher, cron
  due-check, connector auth headers, action handlers.
- **Integration** (Postgres testcontainer): token engine
  (linear/routing/fork-join/timer/variables/loop-budget), dual-engine parity,
  retry, error boundaries, human tasks, call-activity, inbound webhooks,
  connections (RLS isolation + secret non-leak), run-stream snapshot, agent
  tools.
- **Frontend** (vitest): nodeMeta, validation, graphSerde, store,
  isValidConnection, retryPolicy, decisionTable, transform, gatewayRouting,
  autoLayout, runOverlay, parseSseFrame, userTasks.

Run backend tests from the repo root:
`uv run --no-sync pytest -o consider_namespace_packages=true services/api/tests/...`
after `uv sync --all-packages --all-extras`. See [DEVELOPMENT.md](DEVELOPMENT.md).

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
