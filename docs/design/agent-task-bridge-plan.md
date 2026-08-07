# Implementation Plan: `agent` Task — the Workflow ↔ Agent Bridge

> **Status (2026-08-07, branch `feat/agent-task-bridge`):** Phases 0 and 1 are
> BUILT and tested (hardening `97317bf`, engine bridge `64a6d0d`, UI `6898046`),
> plus Phase 3's code component (`review_sample_pct` sampling → org-admin review
> notifications). Phase 2 (form-link draft values) and the review
> entity/reports (org config) remain; the interim shadow pattern below needs no
> new mechanism. Full test coverage: 22 bridge unit + 16 bridge integration +
> 15 hardening tests; UI 593 green, tsc/eslint/ruff/mypy clean on touched files.

**Goal.** A BPMN workflow step can be assigned to an agent from the org roster: the step
enqueues an `AgentRun`, the token parks, the existing agent executor drives the run under
its normal authority/approval machinery, and the run's terminal state resumes the token —
with validated output for downstream nodes on success and a boundary-event escalation on
failure, timeout, or deliberate agent escalation.

**Design provenance.** This plan encodes the corrections from the 2026-08-06 five-lens
review (architecture / concurrency / security / data model / product). The two load-bearing
corrections: the precedent is the **call activity** (`_dispatch_call` + `wait_kind="subprocess"`
+ `_signal_parent`), *not* `send_form` (which is fire-and-forget; action handlers cannot park);
and escalation/timeout use **boundary events**, *not* named out-edge handles (task completion
advances all out-edges — handles would fork).

Non-goals for phase 1: coordinator agents (delegation trees per step), shadow mode,
review/metrics entities, per-org concurrency fairness in the agent sweep.

---

## Phase 0 — Agent-runtime hardening (prerequisite, independently shippable)

The workflow engine's crash-safety story must not be weakened by coupling to a less-hardened
lifecycle. All three items below are bugs today even without the bridge.

### 0.1 Conditional terminal transitions (compare-and-set)

`AgentRunRepository.finalize_run` (`services/api/src/api/repositories/agent_run.py:112`) is
last-writer-wins ORM assignment. Replace with a guarded raw UPDATE:

```sql
UPDATE agent_runs SET status = :terminal, finished_at = now(), ...
WHERE id = :id AND status IN ('queued', 'running', 'waiting')
```

Return rows-affected; **0 rows means the run was externally finalized (cancelled/timed out)**
— the caller must skip wire-back and take no further side effects. Audit every terminal call
site and route all of them through this method:
`run_executor.py` done (~line 228), `_mark_error` (~127), agent-missing (~139),
no-provider-key (~143), and `ApprovalService.deny` (`approvals.py` ~82).

### 0.2 Cancellation

`"cancelled"` exists in `AGENT_RUN_STATUSES` (`models/agent_run.py:24`) but nothing sets it.

- `AgentRunRepository.cancel(run_id, *, reason)` — conditional UPDATE
  (`queued|waiting|running → cancelled`), voids pending `AgentApproval` rows.
- `ApprovalService.approve` must refuse to re-queue a cancelled run (approve currently
  re-queues unconditionally, `approvals.py:49-71`).
- **Cooperative check** in `run_agent_loop` (`services/agents/runtime.py`): re-read run
  status before each `_stream_turn` and before phase-2 tool execution; on `cancelled`,
  raise a `RunCancelled` control signal (sibling of `RunParked`). A cancelled run stops
  taking side effects within one turn.
- `_claim` (`run_executor.py:108`) already filters by status; verify cancelled is excluded.

### 0.3 Stale-`running` lease recovery

A worker death (deploy, OOM) leaves a claimed run `running` forever; `_backstop` only
re-bubbles `waiting` runs. Mirror the token engine's lease (`engine.py: LEASE_TTL_SECONDS=300`):

- Heartbeat: bump `last_activity_at` (column exists, `models/agent_run.py:79`) once per loop
  iteration in the executor's `emit` path.
- Sweep (in `advance-runs`): `running` runs with `last_activity_at < now - TTL` → requeue
  once (attempt counter in `input`), then finalize as `error` via 0.1. Choose TTL ≥ the
  longest plausible single LLM turn (suggest 10 min; a heartbeat per iteration keeps
  long multi-turn runs alive).

### 0.4 Tests (phase 0)

- Unit: finalize CAS (concurrent cancel vs done — exactly one wins, loser reports 0 rows);
  approve-after-cancel refused; cooperative cancel stops the loop mid-run without executing
  the gated batch.
- Integration: kill a worker mid-run (simulate by stamping stale `last_activity_at`), sweep
  reclaims; denied approval finalizes error exactly once.

---

## Phase 1 — The bridge

### 1.1 Migration 045 — columns on `agent_runs` (no bridge table)

A new table would silently miss `admin_bypass_all` (the 034/040 relkind trap); `agent_runs`
is a plain table already covered. Add:

| column | type | notes |
|---|---|---|
| `workflow_run_id` | UUID, nullable | **soft reference — no FK** (workflow_runs has composite PK `(id, created_at)`, partition-drop retention) |
| `workflow_run_created_at` | timestamptz, nullable | partition-pruning key; every lookup uses both |
| `workflow_node_id` | String(64), nullable | matches node-id widths in `models/workflow.py` |
| `workflow_token_id` | UUID, nullable | exact-token cancellation propagation |
| `output` | JSONB, nullable | the schema-validated `complete_task` object (do **not** overload `input`) |

Indexes: partial `(workflow_run_id) WHERE workflow_run_id IS NOT NULL` (reverse lookup +
cancellation scan). Code constants: add `"escalated"` to `AGENT_RUN_STATUSES` (String(12)
fits; no DB CHECK exists) and `"workflow"` to `AGENT_RUN_TRIGGERS`.

Timeout ownership (see 1.4) is the workflow timer boundary — **no** `timeout_at` on
`agent_runs`; the lease TTL (0.3) covers agent-side crashes.

### 1.2 Workflow vocabulary

- `constants.py`: `TASK_AGENT = "agent"`; add to `TASK_TYPES` and `WAIT_TASK_TYPES` **only if**
  the generic wait branch is reused — it is not (see 1.3); keep it out of `WAIT_TASK_TYPES`
  and give it its own dispatch branch, but add it to `TASK_TYPES` for validation.
- `models/workflow.py`: add `"agent"` to `TOKEN_WAIT_KINDS` (fits String(24)).
- `engine.py`: **do NOT add `"agent"` to `_SIGNALABLE_WAIT_KINDS`** (line 76). This is the
  anti-spoofing control: `POST /runs/{id}/complete-task` (`require_org_access`, any member)
  must never be able to complete an agent step. Extend the timer sweep's IN-list (line ~314)
  and the `ix_wf_tokens_timer` partial-index predicate to include `"agent"` so an attached
  timer boundary actually fires (the predicate currently covers only timer/boundary/retry).
- Publish-time validation (`validation.py` + designer `validation.ts`):
  - `agent` tasks only in `schema_version >= 2` graphs (v1 walker has no tokens).
  - Referenced agent exists, `enabled`, `kind == "operator"`; reference is resolved to an
    **immutable agent id** at publish (name resolution is mutable — delete/recreate swaps grants).
  - An **error boundary must be attached and wired** — hard error, not warning. An unwired
    escalation turns "agent has no API key" into a workflow that reads as succeeded.
  - Warn (not block) when the workflow is triggerable by inbound webhook or anonymous
    share-link unless the agent opts in (1.7).

### 1.3 Engine dispatch branch (`_dispatch_task`), modeled on `_dispatch_call`

Node config (in the v2 definition JSON):

```json
{ "task_type": "agent",
  "data": { "agent_id": "<uuid>",
             "task": "Triage: {{ after.subject }}\n\n{{ after.body }}",
             "output_schema": { "category": {"type": "string", "enum": ["billing","tech","other"]},
                                 "priority": {"type": "string", "enum": ["p1","p2","p3"]},
                                 "response_draft": {"type": "string", "maxLength": 4000} },
             "capture": "triage" } }
```

**First arrival** (no `_agent_run_id` in `token.data`) — inside the token's transaction, so
enqueue + park commit atomically:

1. Render `task` with the standard template context; wrap interpolated values in a
   delimited data block (see 1.7 spotlighting).
2. Snapshot into `AgentRun.input`: rendered task, `output_schema`, workflow linkage
   `{run_id, run_created_at, token_id, token_created_at, node_id}`. Snapshotting pins the
   schema to the executing version — a mid-flight republish must not change validation.
3. Create `AgentRun(status="queued", trigger="workflow", actor_user_id=None, org_id=<run org>)`
   with the 1.1 columns; **`actor_user_id` is always None** (service identity — never the
   triggering user's OAuth identity).
4. Stamp `token.data["_agent_run_id"]`; park `status="waiting"`, `wait_kind="agent"`,
   `correlation_key=str(agent_run.id)`, `resume_at=None` (timeout is the boundary's job).
   The `_agent_run_id` stamp is the idempotency guard: any re-dispatch without a completion
   marker must **not** enqueue a second run.

**Re-dispatch with `_completion_output` present** → validate against the snapshot schema
(server-side re-validation, defense against forged/hand-rolled resumes), publish to
`vars.<capture>` via the existing wait-task capture path, record the step with the audit
snapshot (1.6), advance normal out-edges.

**Re-dispatch armed by a fired boundary** → existing boundary routing; see 1.4/1.5.

### 1.4 Timeout = attached timer boundary, single owner

Use the engine's existing timer-boundary mechanism (designer + validation + sweep support
already exist) rather than a bespoke `timeout_minutes`. The one addition: **when the timer
boundary fires on an `agent` wait, cancel the linked AgentRun (0.2) in the same transaction
that reroutes the token.** This closes the "workflow escalated to a human, agent keeps
acting, late approval re-executes the step" race at the side-effect level, not just the
token level. Symmetrically, every token-death path — `kill_all` (terminate outcome),
`_kill_token` (dead-letter), `_fail_run` — must cancel linked non-terminal agent runs via
the `workflow_token_id` column.

### 1.5 `complete_task` tool + loop termination

New spec in `services/agents/tools/` registered **only** for runs with workflow linkage
(loader gains run/trigger context — today `load_agent_tools` is per-agent only):

- `always_allowed=True`, exempt from `kind_gate`, not `side_effecting` — it must never be a
  grants foot-gun and never park on approval.
- Handler validates args against the **snapshotted** schema; on mismatch returns the
  validation errors as the tool result so the model retries (the runtime already treats
  tool failures as feedback).
- On success: stash the object on `ToolContext` and raise `RunCompleted` (control-signal
  sibling of `RunParked`, `runtime.py:41`) so termination is deterministic and exactly-once.
  If batched with other calls, execute it **last**; two-phase gating already ordered the batch.
- **Done-without-complete_task** (model just answers in text): append one corrective user
  message and re-loop once; if it still doesn't call the tool, finalize as `escalated`
  with reason `no_structured_output`. `truncated=True` (iteration budget) → `escalated`,
  never `done`.

### 1.6 Wire-back — one choke point, transactional, token-exact

All terminal transitions flow through `finalize_run` (0.1); wire-back lives there (or a thin
service wrapper), keyed on `workflow_run_id IS NOT NULL`:

- Same transaction as the status flip, inside `enter_tenant(org_id)` (RLS backstop), and
  assert `workflow_run.org_id == agent_run.org_id` before signaling.
- Conditional raw UPDATE mirroring `_signal_parent` (`engine.py:1047`), targeted at the
  **exact token**: `WHERE id=:token_id AND created_at=:token_created_at AND status='waiting'
  AND wait_kind='agent' AND correlation_key=:agent_run_id` (created_at → partition-local;
  correlation → a stale run from a previous loop iteration can never complete the current
  park). 0 rows = workflow moved on → log `wire_back_late`, do nothing.
- Mapping: `done` → stamp `_completion_output` + reactivate (token sweep advances);
  `escalated` → reactivate armed for the error boundary with `error_code="escalated"`;
  `error`/`cancelled` → `error_code="failed"`. This is the first real user of the
  code-specific error-boundary catch plumbing (`engine.py:588-600`).
- Step output snapshot (survives retention pruning on either side):
  `{agent_run_id, agent_id, status, error, total_tokens, cost_usd, escalation_reason}`.
- **Reconciliation sweep** (crash backstop, fits the poll architecture): tokens
  `waiting/agent` whose `_agent_run_id` is terminal → reactivate as above. A lost signal
  degrades to latency, never a hang.
- `agent` tasks are exempt from node retry policy (`max_attempts` honored as 1): a re-run
  replays side-effecting work on top of committed effects. All failures route to the
  boundary where a human decides.

### 1.7 Security hardening (in-scope for phase 1, not follow-ups)

1. **Spotlighting**: the rendered task puts author instructions and interpolated record
   data in separate, delimited sections; the workflow-run system prompt states that data
   is not instructions. `after.*` carries member-, webhook-, and share-link-written text.
2. **Egress**: for workflow-triggered runs, deny `web_research` (its query string is
   un-gated egress — `side_effecting=False`) unless the node explicitly opts in.
3. **Agent-side consent** (confused-deputy mirror of `workflow_allowlist`): new
   `Agent.workflow_invocable` (JSONB list of workflow ids, or `"*"`); checked at enqueue.
   Default empty — binding an agent requires consent on the agent, not just the workflow.
4. **Tainted vars**: document that agent-derived `vars.*` feed downstream actions that run
   `privileged=True`; `output_schema` supports enums / numeric ranges / maxLength as the
   first-line constraint; writing agent output to `server_only` fields without a human
   gate is a documented anti-pattern (enforcement = phase 3 provenance tagging).
5. `actor_user_id=None` (1.3); run detail surfaces stay org_admin-gated.

### 1.8 UI (phase-1 scope — the review was explicit that this is not polish)

- **Designer**: `nodeMeta.ts` entry + palette; bespoke `NodeInspector` panel (agent picker
  filtered to enabled operators, task template textarea with `{{ }}` hinting, output-schema
  field editor, capture name); `validation.ts` rules from 1.2; `nodeHelp.ts` topic including
  the untrusted-template warning; backend contract test updated (constants.py header mirror).
- **RunMonitor**: branch on `wait_kind === "agent"` — "Agent working…" panel with a link to
  the agent run, inline transcript via existing `GET /agents/runs/{id}/steps`
  (`routers/agent_console.py:87`; add a step fetcher to `ui/src/lib/api/agents.ts`), and the
  pending `AgentApproval` (approve/deny) surfaced **inside** the workflow run view. No
  approve/reject user-task buttons for agent waits.
- **Approvals page + notifications**: rows for workflow-triggered runs carry a
  `?run=` deep-link to the workflow run. Suppress the `_backstop` reminder for
  workflow-triggered runs — the timer boundary owns the SLA (one queue, not two).
- **Publish preflight** surfaced in the designer: agent enabled, provider key resolvable,
  operator kind, boundary wired.

### 1.9 Tests (phase 1)

- Unit: dispatch branch (first-arrival enqueue-once; re-dispatch idempotency via
  `_agent_run_id`; completion validate + capture; boundary arming); `complete_task`
  (validate/retry, `RunCompleted` exactly-once, done-without-tool → escalated); wire-back
  CAS (late completion after boundary fired = no-op logged; wrong-org assert; loop-back —
  second iteration's park never accepts first iteration's run).
- Integration (uv + `API_SECRET_KEY=test-secret`, per repo convention): end-to-end happy
  path (record trigger → agent run → complete_task → vars → downstream update_record);
  escalation path (agent calls escalate → error boundary → user task); timeout path
  (timer boundary fires → AgentRun cancelled → late approval refused); approval path
  (approval_required tool parks run + token; approve resumes; deny → boundary);
  worker-crash path (stale lease → sweep → boundary). **RLS posture tests**: the enqueue
  INSERT under the token engine's downgraded `app_user` role, and the wire-back UPDATE on
  partitioned `workflow_run_tokens` from the agent worker's session — both under the real
  role, not the privileged test session.

### 1.10 Demo (acceptance)

HRMS org: intake-triage workflow. Operator agent, grants = read KB (folder-scoped) +
`create_record` on the routing entity, `approval_required` on `send_email`. Record trigger
on new ticket → agent triages → completed path writes the routing record; ambiguous ticket
→ escalate → user task with the escalation payload; email draft parks for approval visible
inside the run view.

---

## Phase 2 — Shadow mode (separate feature, separately costed)

Form links have **no draft-values channel** (`mint_form_link(form_id, record_id, email)`),
so "agent pre-fills the human's form" is real work: a values payload on the form link,
renderer support distinguishing draft vs record values, and dual storage for grading.
Interim cheap version: the agent writes its draft into ordinary record fields the form
already displays, and the dual record lands in the phase-3 review entity.
Escalation payload contract from 1.6 (`vars.<capture>_escalation`: partial output, last
assistant message, reason) is the context handoff shadow mode builds on.

### Interim shadow pattern (available today, zero new mechanism)

Author the graph as: `agent task (capture=draft)` → `update_record` mapping
`{{ vars.draft.* }}` into ordinary record fields → `send_form`/user task whose
form displays those fields. The human sees the agent's draft in the form,
edits, submits. Both versions exist (the agent's draft is on the record + the
step output snapshot; the human's edit is the submission).

## Phase 3 — Reviews & the autonomy dial

> **Sampling is BUILT**: agent-task node config `review_sample_pct` (0–100,
> designer field) routes that share of *completed* steps to an org-admin
> `review` notification carrying `{workflow_run_id, node_id, agent_run_id,
> result}`. Deterministic (agent-run-id hash), never fails the step. The
> review *entity* + acceptance-rate reports are org config per the design.

`agent_task_review` entity (snapshots `{agent_run_id, agent_id, status, cost}` — no live
join to prunable tables) + reports: acceptance rate, escalation rate, turnaround
(report queued-vs-executing separately so sweep latency isn't blamed on agents).
Per-step sampling % routes N% of completed steps to a human review queue. Precedence rule,
documented: org autonomy posture (`high_touch`) is the outer bound; the per-step dial can
only be more conservative. Provenance tagging on agent-derived vars → `update_record`
refuses tainted input for protected fields by default.

---

## Order of work

1. Phase 0 (hardening) — PR 1. Independently valuable; fixes real bugs.
2. Migration 045 + vocabulary + dispatch branch + `complete_task` + wire-back — PR 2
   (engine-complete, testable via API without UI).
3. Security items 1.7 — PR 2 or 3 (spotlighting + consent + egress gate before any real org uses it).
4. Designer + RunMonitor + approvals UX — PR 3.
5. Demo build + integration hardening — PR 4.

Known open risks to re-verify during implementation: migration 034/040 partitioned-RLS
status in prod (memory: `km2-missing-km-app-role`), and the serial cross-org agent sweep
(limit 10, one session) as a head-of-line-blocking hazard once workflows park on it —
per-org fairness is deliberately deferred but must be documented in the feature docs.
