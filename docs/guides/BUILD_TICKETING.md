# Build a Support Ticketing System on Red Arch KM2

This guide walks you through building a support / help-desk application — where requesters
submit tickets, agents work a queue, SLAs escalate automatically, and a dashboard tracks the
backlog — entirely out of KM2's generic primitives. There is no "ticketing module" or
`tickets` table to switch on: you assemble the whole help desk from ordinary custom entities,
forms, views, workflows, and reports in your own tenant.

## Table of Contents

- [What you'll build](#what-youll-build)
- [Prerequisites](#prerequisites)
- [Reference implementation](#reference-implementation)
- [Data model (entities, fields, relationships)](#data-model-entities-fields-relationships)
- [Forms](#forms)
- [Views & dashboards](#views--dashboards)
- [Workflows (automation)](#workflows-automation)
- [Permissions](#permissions)
- [Knowledge & AI](#knowledge--ai)
- [Extending it](#extending-it)

## What you'll build

- A **Submit a ticket** form (internal or a public token link) that opens a ticket.
- A **My tickets** board where each requester sees only their own tickets, via the `@me`
  filter.
- A **Team queue** of all open tickets, sorted by priority and SLA, with per-row actions.
- A **Ticket detail** view: the ticket's fields, its conversation thread, a reply button, and
  one-click status-change buttons.
- A **Support dashboard** of reports — open tickets by priority, tickets by status, tickets
  created per week, and average resolution time.
- **Automation**: acknowledge-and-triage on create, status-change notifications, per-ticket
  **SLA escalation** on a timer, and an agent **reply** workflow that appends a comment and
  emails the requester.
- **Permissions** so requesters see only their own tickets while agents and admins run the
  full queue, and internal notes stay off the requester's view.
- Optional **RAG assist**: draft suggested answers from a help-center knowledge base.

## Prerequisites

- An org where you are an **admin**, and familiarity with the in-app builders (see
  [DEVELOPMENT.md](../DEVELOPMENT.md)).
- The primitive reference docs this guide leans on: [FORMS_AND_VIEWS.md](../FORMS_AND_VIEWS.md)
  (the element tree, `record_list`, the `@me` / `record_id=me` self-binding),
  [WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md) (triggers, actions, gateways, timers,
  `run_workflow` buttons), [RBAC.md](../RBAC.md) (run permissions and per-entity/field access
  control), and [KNOWLEDGE_ENGINE.md](../KNOWLEDGE_ENGINE.md) (RAG). See the
  [build-guides index](README.md) for the full set.

**Three ways to build anything below.** Every entity, form, view, and workflow can be created
with (a) the in-app **builders** (UI), (b) the in-app **assistant agent** by describing what
you want, or (c) the **km2-mcp** / agent tools (`km2_create_entity`, `km2_add_entity_field`,
`km2_create_entity_relationship`, `km2_create_form`, `km2_create_view`, `km2_create_workflow`,
`km2_create_report`, …). The rest of this guide describes the **design** in platform terms,
not clicks, so it stays durable whichever method you pick.

## Reference implementation

This is a **fresh recipe** — there is no committed ticketing seed script or live reference org
in the repo. Every primitive it uses is real platform code, cited throughout, and every field
type, view node, workflow action, and report shape below is one KM2 actually supports. Build
it in any tenant; nothing here is hard-coded.

## Data model (entities, fields, relationships)

Five custom entities. Field types are the ones KM2 exposes
(`services/api/src/api/schemas/custom_entity.py`: `text`, `long_text`, `integer`, `numeric`,
`boolean`, `date`, `timestamptz`, `picklist`, plus relationships). "Stamped by workflow"
fields are written by the automation below, not typed by a user.

### `ticket`

| Field | Type | Notes |
|---|---|---|
| `title` | text · required | Short summary of the issue |
| `description` | long_text · required | Full detail from the requester |
| `status` | picklist · required | `open` · `in_progress` · `waiting` · `resolved` · `closed` (default `open`) |
| `priority` | picklist · required | `low` · `medium` · `high` · `urgent` (default `medium`) |
| `requester_email` | text · required | Denormalized address for notifications + display |
| `category_name` | text | Denormalized category label (stamped on triage) — safe to group in reports |
| `created_at` | timestamptz | Stamped by the triage workflow with `{{now}}` |
| `sla_due` | timestamptz | Stamped on triage = `created_at + category.sla_hours` |
| `resolved_at` | timestamptz | Stamped when `status → resolved` |
| `resolution_hours` | numeric | Stamped on resolve = hours between `created_at` and `resolved_at` (feeds the avg-resolution report) |

Relationships (to-one, so they live as FK columns on `ticket`):

- `ticket.category` → `category` (`many_to_one`, `on_delete SET NULL`)
- `ticket.assignee` → `agent` (`many_to_one`, `on_delete SET NULL`)
- `ticket.requester` → `requester` (`many_to_one`, `on_delete SET NULL`) — **this relation is
  what powers the `@me` board.**

### `ticket_comment`

The conversation thread. One ticket has many comments.

| Field | Type | Notes |
|---|---|---|
| `body` | long_text · required | The reply / note text |
| `author` | text | Who wrote it (agent name or requester email) |
| `internal` | boolean | `true` = agent-only note, hidden from requester-facing boards (default `false`) |
| `created_at` | timestamptz | Stamped on create |

- `ticket_comment.ticket` → `ticket` (`many_to_one`, `on_delete CASCADE`)

### `category`

Drives default priority and the SLA clock.

| Field | Type | Notes |
|---|---|---|
| `name` | text · required · unique | e.g. "Billing", "Access", "Bug" |
| `default_priority` | picklist | `low`/`medium`/`high`/`urgent` — seeds a ticket's priority when unset |
| `sla_hours` | integer · required | Hours-to-resolve target; sets `ticket.sla_due` |

- `category.default_agent` → `agent` (`many_to_one`, optional) — used for auto-assignment.

### `agent`

A support agent. Its `email` field is the identity used by the `@me` "my assigned" board.

| Field | Type | Notes |
|---|---|---|
| `name` | text · required | Display name |
| `email` | text · required · unique | Must match the agent's login email for `@me` to resolve |
| `team` | text | e.g. "Tier 1", "Billing" |
| `active` | boolean | `false` takes them out of auto-assignment (default `true`) |

### `requester`

A lightweight contact keyed by email. Modeling the requester as a **relation to an entity with
an `email` field** is what lets the `@me` filter scope a board to "my tickets" — `@me` resolves
a to-one relation to the caller's own record by matching the target entity's `email` slug to the
caller's login email (`services/api/src/api/services/self_record.py`, `resolve_own_record_id`).

| Field | Type | Notes |
|---|---|---|
| `email` | text · required · unique | Identity field matched by `@me` (must be slug `email`) |
| `name` | text | Optional display name |

The triage workflow upserts a `requester` by email on each new ticket (mirroring the proven
learner-by-email pattern in the LMS), so requesters never manage this entity directly.

## Forms

Forms are v2 element trees over one entity (see [FORMS_AND_VIEWS.md](../FORMS_AND_VIEWS.md)).
The control for each field is derived from its `field_type` — you only choose binding and
layout.

- **Submit a ticket** (on `ticket`) — `field` elements for `title`, `description`,
  `category` (a to-one relation renders as a picker), `priority` (optional; the triage
  workflow fills it from the category if left blank), and `requester_email`. Leave
  `status`/`assignee`/`sla_due`/`created_at` off the form — the triage workflow stamps them.
  Publish it as a **public token link** (`GET|POST /api/public/forms/{token}`) if external
  users file tickets without logging in, or use it internally at `/forms/{id}/fill`.
- **Agent reply** (on `ticket_comment`) — `field` elements for `body` and the `internal`
  toggle, with the parent `ticket` supplied by context (reached from the ticket detail view).
  Submitting creates a comment. (The reply *workflow* below is the alternative that also
  emails the requester in one click.)
- **Edit ticket** (on `ticket`) — the triage form for agents: `status`, `priority`,
  `assignee`, and `category`. Changing `status` here is what fires the status-change
  notification workflow.

## Views & dashboards

Views reuse the same element tree; a **standalone** view (no bound entity) can still host a
`record_list`, `report`, `button`, and layout elements.

- **My tickets** (standalone, requester-facing) — a `record_list` on `ticket` with
  `filters: [{ field: "requester", op: "eq", value: "@me" }]`, columns `title` / `status` /
  `priority` / `created_at`, `sort_by: created_at` desc, and a short `poll_ms`. `@me` resolves
  server-side to the caller's own `requester` record, so each user sees only their tickets
  (empty, not org-wide, if they have none). Add a `button`/`form_ref` linking to **Submit a
  ticket**.
- **Team queue** (standalone, agent-facing) — a `record_list` on `ticket` filtered
  `status:in:[open, in_progress, waiting]`, `sort_by: priority` (then a secondary board sorted
  by `sla_due` asc for "closest to breach"), with a per-row `row_workflow_id` button such as
  **Claim** (assigns the ticket to the acting agent). Add a second board filtered
  `assignee:eq:@me` for each agent's **My assigned** work.
- **Ticket detail** (bound to `ticket`, opened with `?record_id=<id>`) —
  - `field` elements for the ticket (make `created_at` / `sla_due` / `resolution_hours`
    `read_only`);
  - a `table` (1:M child grid) of `ticket_comment` for **this** ticket — columns `author`,
    `body`, `internal`, `created_at` — this is the conversation thread;
  - a **Reply** `button` (`run_workflow` → the reply workflow) fed by an `input` element for
    the reply body;
  - **status-change** `button`s (`run_workflow`) — *Start* (→ `in_progress`), *Waiting*,
    *Resolve*, *Close* — each running a small workflow that sets `status` and notifies.
- **Support dashboard** (standalone) — `report` elements embedding saved reports (see
  [FORMS_AND_VIEWS.md](../FORMS_AND_VIEWS.md) `report` node and `report.py`/`aggregate.py`):

| Report | `viz.type` | `group_by` | `metrics` | Filters |
|---|---|---|---|---|
| Open by priority | `bar` | `priority` | `count` | `status in [open, in_progress, waiting]` |
| Tickets by status | `donut` | `status` | `count` | — |
| Created per week | `line` | `created_at` (bucket `week`) | `count` | last N weeks |
| Avg resolution (hrs) | `metric` | — | `avg` of `resolution_hours` | `status = resolved` |
| Open by category | `bar` | `category_name` | `count` | `status in [open, in_progress, waiting]` |

  A `count` query needs no metric field; `avg`/`sum`/`min`/`max` require a numeric field —
  which is why `resolution_hours` exists as a stamped `numeric` column, and why the reports
  group on the denormalized `category_name` text rather than the `category` relation.

## Workflows (automation)

Built on the token engine ([WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md)). Actions used below are
all real handlers in `services/api/src/api/services/workflow/actions.py`: `create_record`,
`get_record`, `update_record` / `update_record_field`, `send_email`, `send_webhook`,
`knowledge_search`, `summarize`, `llm_respond`, plus **intermediate timer** events and
**exclusive gateways** for routing.

### 1. Acknowledge & triage — trigger `on_change` (create) on `ticket`

Scope the trigger to the **create** operation so the self-updates below don't re-fire it.

1. **Upsert the requester** — `get_record` on `requester` filtered `email = {{after.requester_email}}`;
   if none, `create_record` a `requester` `{ email, name }`. Then `update_record` on the
   triggering ticket to set `requester` to that record id.
2. **Read the category** — `get_record` on `category` for the chosen category; capture
   `default_priority`, `sla_hours`, `default_agent`, and `name`.
3. **Stamp defaults** — `update_record` on the ticket: `status = open` (if unset),
   `created_at = {{now}}`, `category_name = {{vars.category.name}}`, `priority =
   {{vars.category.default_priority}}` when the form left it blank, and `sla_due` computed by a
   **script/transform task** using the sandboxed `date_add(now, sla_hours, "hour")` op.
4. **Auto-assign** — set `assignee = {{vars.category.default_agent}}` (or leave unassigned for
   the Team queue to pick up).
5. **Acknowledge** — `send_email` to `{{after.requester_email}}` with the ticket title and a
   link to the requester's My tickets view.
6. **Arm the SLA timer** — an **intermediate timer** event with `resume_at = {{sla_due}}`.
   When it fires, `get_record` the ticket by id and route on an **exclusive gateway**: if
   `status` is still one of `open`/`in_progress`/`waiting`, escalate — `update_record`
   `priority = urgent` and `send_email` (or `send_webhook`) an on-call manager; if already
   resolved/closed, end. Each ticket thus gets its own escalation run parked on a timer, with
   no polling and no fan-out loop.

> Because step 3–4 write the **same** triggering ticket, keep the trigger create-only: an
> update won't match it, so there's no re-entry. (A timer-based SLA is the loop-free primitive;
> a `scheduled` cron trigger — `{cron: "..."}` or `{every_minutes: N}` — is better reserved
> for a *digest* like "email me every morning's breached tickets", since a single scheduled run
> has no per-record cursor to fan out over.)

### 2. Status-change notifications — trigger `on_change` (update) on `ticket`, `field_filter: [status]`

An **exclusive gateway** on `{{after.status}}` (comparing `{{before.status}}` to detect the
transition):

- **→ resolved** — a script task stamps `resolved_at = {{now}}` and
  `resolution_hours = date_diff(created_at, resolved_at, "hour")` via `update_record`, then
  `send_email` the requester "resolved — please confirm".
- **→ waiting** — `send_email` "we need more information".
- **→ closed** — `send_email` "your ticket is closed".

### 3. Reply to requester — trigger `manual` (member action)

Reached from the Ticket detail **Reply** button. Inputs: `ticket_id`, `body`.

1. `create_record` a `ticket_comment` `{ ticket: {{inputs.ticket_id}}, body: {{inputs.body}},
   author: <acting user>, internal: false, created_at: {{now}} }`.
2. `get_record` the ticket by id → read `requester_email`.
3. `send_email` the requester with the reply body and a link back to the ticket.

Add an **internal-note** variant (same shape, `internal: true`, and **no** email) for the
`internal` toggle path.

### Status-change buttons

The *Start / Waiting / Resolve / Close* buttons on the detail view each run a one-step
`manual` workflow that `update_record`s `status`; workflow #2 then reacts to the change and
notifies. (The button's `run_permission` is what gates who may transition a ticket.)

## Permissions

See [RBAC.md](../RBAC.md) for the full model.

- **Requesters** — ordinary org members. They only ever open the **My tickets** and **Submit a
  ticket** views; the `@me` filter means their board resolves to their own `requester` record
  and returns only their rows. External requesters can file via the public token form without
  an account.
- **Agents / org-admins** — reach the **Team queue**, **Ticket detail**, **Edit ticket**, and
  the reply/status workflows. Gate the reply and status-change workflows with
  `run_permission.mode = specific_roles` (an "Agent" role) or `org_admin`; org admins always
  pass (`can_run`).
- **Controlled writes** — leave `ticket` write open to members so requesters can submit, but
  keep status/SLA transitions flowing through the workflows above. To force *all* structured
  writes through automation, set the entity's `write_access = workflow_only` (only the workflow
  engine + org admins may write it) and drive every change from a form → workflow button.
- **Internal notes** — the `internal` boolean plus a requester-facing comment board filtered
  `internal:eq:false` hides agent notes from requesters at the **view** layer. Note this is a
  view/filter convention, not a hard per-row guarantee: org members can read an entity's records
  through the records API. For a hard boundary, either keep internal notes in a **separate
  entity** requesters get no view or role for, or mark a sensitive field's *value*
  `read_access = server_only` so members can't read it at all. Both are the migration-039
  entity/field access-control levers described in [RBAC.md](../RBAC.md).

## Knowledge & AI

Optional, and a natural fit for a help desk (see [KNOWLEDGE_ENGINE.md](../KNOWLEDGE_ENGINE.md)).

- **Help-center RAG** — ingest your help articles / runbooks into a folder, then add a manual
  **Suggest an answer** workflow: a `knowledge_search` action over that folder using the
  ticket's `title` + `description` as the query, piped into a `summarize` or `llm_respond`
  action to draft a reply. The agent reviews the draft and sends it through the reply workflow —
  the human stays in the loop. Folder access masks ([RBAC.md](../RBAC.md)) keep the RAG scoped
  to what the agent may read.
- **In-app assistant** — the same knowledge base powers the built-in chat assistant, so agents
  can ask "how do we handle a refund past 30 days?" while working a ticket.
- **Auto-triage (advanced)** — an `llm_decide` action in the triage workflow can suggest a
  category/priority from the ticket text; keep it advisory (a suggestion an agent confirms)
  rather than an unattended write.

## Extending it

- **CSAT survey** — a `send_form` on `→ closed` collecting a satisfaction rating into a
  `ticket_survey` entity, charted on the dashboard.
- **Public status page** — a token view with a `record_list` (or `live_value`) of open incidents.
- **Chat-ops escalation** — a `send_webhook` / `http_request` connector task posting urgent
  breaches to Slack/Teams (secrets held in an org `workflow_connection`, SSRF-guarded).
- **Business-hours SLAs** — compute `sla_due` against a working calendar in the script task
  instead of raw hours.
- **Smart routing** — replace the single default-agent rule with a `businessRule`
  decision-table task that routes by category, team, and load.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
