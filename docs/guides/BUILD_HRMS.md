# Build a Human Resource Management System on Red Arch KM2

This guide shows how to assemble a full HRMS — recruiting pipeline, employee
directory, onboarding/offboarding automation with access provisioning and IT
lockout, performance-review cycles, and HR dashboards — entirely from KM2's
generic primitives (custom entities, relationships, forms, views/dashboards,
workflows, reports, and RBAC). No HR-specific tables or hard-coded flows are
involved; everything below is ordinary tenant data plus configuration.

Audience: an org admin building the app, or an evaluator wanting to see how a
vertical HR app maps onto the platform.

## Table of Contents

- [What you'll build](#what-youll-build)
- [Prerequisites](#prerequisites)
- [Reference implementation](#reference-implementation)
- [Data model (entities, fields, relationships)](#data-model-entities-fields-relationships)
- [Forms](#forms)
- [Views & dashboards](#views--dashboards)
- [Workflows (automation)](#workflows-automation)
- [Permissions](#permissions)
- [Knowledge & AI (optional)](#knowledge--ai-optional)
- [Extending it](#extending-it)
- [Known gaps / TODO](#known-gaps--todo)

## What you'll build

- A **recruiting pipeline**: open a requisition, track candidates and
  applications through stages, record interviews, extend offers.
- An **employee directory** with department, position, and manager structure.
- **Onboarding automation** — one action provisions a new hire's access grants
  (AD group, VPN, badge, mailing list, SaaS), spawns onboarding tasks, and emails
  IT, the new hire, and the manager.
- **Offboarding automation** — one action revokes all access grants, emails
  IT-security to lock the account out, and spawns exit tasks.
- **Performance-review cycles** (annual and semi-annual) launched on demand.
- An interactive **HR ops console** where HR triggers every workflow from the UI.
- **HR dashboards**: quarterly hires-vs-terminations, active headcount, headcount
  by department, review-completion status, and pipeline distributions.
- **Field-level protection** so salary and SSN are hidden from ordinary members,
  and employees see only their own record and reviews.

## Prerequisites

- An organization and org-admin access (site-admin bootstrap and org setup are in
  [DEPLOYMENT.md](../DEPLOYMENT.md); local dev in [DEVELOPMENT.md](../DEVELOPMENT.md)).
- Familiarity with the primitive reference docs:
  [FORMS_AND_VIEWS.md](../FORMS_AND_VIEWS.md) (entities, forms, views, dashboards,
  `record_list`, the `@me` filter), [WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md)
  (triggers, actions, scheduling), and [RBAC.md](../RBAC.md) (roles, access masks,
  per-entity/field access control).
- Everything here can be created three ways — the in-app builders, the in-app
  assistant agent, or the `km2-mcp` / agent tools (`km2_create_entity`,
  `km2_add_entity_field`, `km2_create_entity_relationship`, `km2_create_form`,
  `km2_create_view`, `km2_create_workflow`, `km2_create_report`). This guide
  describes the **design** in platform terms so it stays valid regardless of how
  you author it. See [docs/guides/README.md](README.md) for the build-methods
  overview.

## Reference implementation

A live reference exists: the **"Human Resource Management"** demo org. It was built
**live via the `km2-mcp` tools**, not from a committed seed script — there is no
`scripts/` provisioner for it (the only script under `scripts/`,
`scripts/provision_company.py`, provisions the unrelated autonomous-company agent
roster). The design below therefore mirrors that live org conceptually rather than
citing a file.

As built, the demo has **13 custom entities**, **17 `many_to_one` relationships**,
**7 published workflows**, **6 forms**, **6 views** (including the interactive
`hr_ops_console`), **14 reports** embedded across **5 dashboards**, and roughly
**90–120 seeded records** across all lifecycle states. This guide reproduces that
shape and adds two coherent entities (`review_cycle`, `time_off`) to cover a
review-cadence record and the time-off request form.

## Data model (entities, fields, relationships)

Model the domain as custom entities. Field types are drawn from the platform's
set — `text`, `long_text`, `integer`, `numeric`, `boolean`, `date`, `timestamptz`,
`picklist` (single-select), `json` (see `FieldType` in
`services/api/src/api/schemas/custom_entity.py`). Foreign keys are **not** a field
type — they are separate relationships (`km2_create_entity_relationship`), so the
tables below list scalar fields and the relationships are listed once at the end.

### Entity overview

| Entity | Purpose | In live demo? |
|---|---|---|
| `department` | Org unit (name, cost center, headcount target) | Yes |
| `position` | A job/title/grade (the role definition, not the person) | Yes |
| `employee` | The hub record: a person employed (or pre-hire/terminated) | Yes |
| `job_requisition` | An approved opening to fill | Yes |
| `candidate` | An applicant (person outside the org) | Yes |
| `application` | A candidate applying to a requisition; carries pipeline `stage` | Yes |
| `interview` | An interview event on an application | Yes |
| `offer` | An offer extended for an application | Yes |
| `onboarding_task` | A checklist item for a new hire | Yes |
| `offboarding_task` | A checklist item for a departing employee | Yes |
| `access_grant` | One provisioned/revoked access (AD group, VPN, badge, DL, SaaS) | Yes |
| `performance_review` | A review of an employee by a reviewer | Yes |
| `employment_event` | A movement log row (hire / termination / transfer / promotion) — powers the hires-vs-terminations report | Yes |
| `review_cycle` | A named review period (annual / semi-annual) reviews attach to | Addition |
| `time_off` | A PTO / leave request | Addition |

### Core entity fields

**`department`**

| Field | Type | Notes |
|---|---|---|
| `name` | text | Required, unique |
| `code` | text | Short cost-center code |
| `headcount_target` | integer | For the headcount-vs-target report |
| `location` | picklist | Office/region |

**`position`**

| Field | Type | Notes |
|---|---|---|
| `title` | text | e.g. "Senior Engineer" |
| `level` | picklist | IC1–IC6 / M1–M4 |
| `employment_type` | picklist | full_time / part_time / contract |
| `is_open` | boolean | Whether the seat is currently vacant |

**`employee`** (the hub — note the two protected fields)

| Field | Type | Notes |
|---|---|---|
| `full_name` | text | Required |
| `work_email` | text | Login/contact email |
| `status` | picklist | pre_hire / active / on_leave / terminated |
| `hire_date` | date | Populated at onboarding |
| `termination_date` | date | Populated at offboarding |
| `employment_type` | picklist | full_time / part_time / contract |
| `salary` | numeric | **`read_access = server_only`** — hidden from ordinary members |
| `national_id` | text | SSN/PII — **`read_access = server_only`** |

**`onboarding_task`**

| Field | Type | Notes |
|---|---|---|
| `title` | text | e.g. "Sign NDA", "Set up workstation" |
| `category` | picklist | it / hr / facilities / manager |
| `status` | picklist | open / in_progress / done |
| `due_date` | date | Relative to `hire_date` |

**`offboarding_task`** — same shape as `onboarding_task` (title, category, `status`, `due_date`);
categories skew to `it` / `security` / `facilities`.

**`access_grant`**

| Field | Type | Notes |
|---|---|---|
| `system` | picklist | ad_group / vpn / building_badge / email_dl / saas |
| `resource` | text | The specific group/app name |
| `status` | picklist | requested / provisioned / suspended / revoked |
| `granted_at` | timestamptz | Set when provisioned |
| `revoked_at` | timestamptz | Set at offboarding |

**`performance_review`**

| Field | Type | Notes |
|---|---|---|
| `period` | text | e.g. "2026 Annual" |
| `status` | picklist | draft / self_assessment / manager_review / finalized |
| `rating` | picklist | exceeds / meets / below (or 1–5) |
| `summary` | long_text | Narrative |
| `due_date` | date | Cycle deadline |

**`time_off`** (addition)

| Field | Type | Notes |
|---|---|---|
| `type` | picklist | vacation / sick / parental / unpaid |
| `start_date` | date | Required |
| `end_date` | date | Required |
| `days` | numeric | Calculated (`date_diff` on start/end) or entered |
| `status` | picklist | requested / approved / denied |
| `note` | long_text | Reason / comments |

The recruiting entities are compact:

| Entity | Key fields |
|---|---|
| `job_requisition` | `title` (text), `status` (picklist: open/on_hold/filled/closed), `openings` (integer), `target_start` (date) |
| `candidate` | `full_name` (text), `email` (text), `phone` (text), `source` (picklist), `resume_note` (long_text) |
| `application` | `stage` (picklist: applied/screen/interview/offer/hired/rejected), `applied_at` (timestamptz) |
| `interview` | `round` (picklist), `scheduled_at` (timestamptz), `outcome` (picklist), `notes` (long_text) |
| `offer` | `status` (picklist: extended/accepted/declined/expired), `base_salary` (numeric, `server_only`), `start_date` (date) |
| `employment_event` | `event_type` (picklist: hire/termination/transfer/promotion), `event_date` (date) |
| `review_cycle` | `name` (text), `kind` (picklist: annual/semi_annual), `opens_on` (date), `closes_on` (date) |

### Relationships

All are `many_to_one` (the FK lives on the "from" entity). The live demo wires 17;
the core set:

| From | To | Meaning |
|---|---|---|
| `employee` | `department` | Which department the person is in |
| `employee` | `position` | The role they hold |
| `employee` | `employee` | Manager (self-referential) |
| `job_requisition` | `department` | Owning department |
| `job_requisition` | `position` | Role being filled |
| `application` | `candidate` | Who applied |
| `application` | `job_requisition` | What they applied to |
| `interview` | `application` | Interview belongs to an application |
| `interview` | `employee` | The interviewer |
| `offer` | `application` | Offer for an application |
| `offer` | `position` | Position offered |
| `onboarding_task` | `employee` | New hire the task is for |
| `offboarding_task` | `employee` | Departing employee |
| `access_grant` | `employee` | Whose access this is |
| `performance_review` | `employee` | Subject of the review |
| `performance_review` | `employee` | Reviewer (self-referential) |
| `performance_review` | `review_cycle` | The cycle it belongs to |
| `employment_event` | `employee` | Person the movement concerns |
| `time_off` | `employee` | Requester |

## Forms

Forms bind an entity's own fields; a 1:1 related record uses a `section`, a 1:M
child grid uses a `table`/`block` (see [FORMS_AND_VIEWS.md](../FORMS_AND_VIEWS.md)).
Build these:

- **New-candidate intake** (on `candidate`). Fields: `full_name`, `email`,
  `phone`, `source`, `resume_note`. Publish it as a **public token link**
  (`GET|POST /api/public/forms/{token}`) so it can live on a careers page; the
  submission creates a `candidate` and (optionally) fires a pipeline workflow.
- **Employee record** (on `employee`). The HR-facing edit form: identity, `status`,
  dates, `employment_type`, plus `salary`/`national_id`. Present the protected
  fields only on this admin form; they never render for non-admins (see
  [Permissions](#permissions)).
- **Performance-review form** (on `performance_review`). `period`, `rating`,
  `summary`, `status` — used by managers to complete a review.
- **Time-off request** (on `time_off`). `type`, `start_date`, `end_date`, `note`;
  a `calculated` element derives `days` from `date_diff(end_date, start_date)`
  (the sandboxed JsonLogic evaluator in `services/form_expression.py` supports
  `date_diff`). Submitting creates a `requested` record that the approval workflow
  or a manager acts on.

> Because a `many_to_one` FK is a relationship (not a scalar field), forms set an
> employee's department/manager through a `section` (inline related record) or the
> cross-entity editable columns in a `table`; bulk parent assignment is commonly
> done at seed/import time or by a workflow `update_record` step.

## Views & dashboards

Views reuse the same element tree as forms. Boards are built from `record_list`
(a live, polling table of an entity's records with server-side `filters` — the
`@me` sentinel scopes rows to the caller), and dashboards embed saved reports via
the `report` element. Build:

- **Employee directory** — a standalone view with a `record_list` over `employee`
  (`full_name`, `status`, `hire_date`, work email), sorted by name. Filter to
  `status = active` for the default roster.
- **Recruiting pipeline** — `record_list` boards over `application` (grouped by
  `stage`) and `job_requisition` (filtered `status = open`), plus an interview
  schedule board over `interview`.
- **Review-status board** — a `record_list` over `performance_review` showing
  `period`, `status`, `rating`, `due_date`; pair it with the review-completion
  report below.
- **`hr_ops_console`** — the interactive control panel. It is a standalone view
  combining `input` elements (e.g. an employee picker / free-text keys) with
  `button` elements of action `run_workflow`, each wired to one of the manual
  workflows below. The button's input map passes console inputs into the workflow
  via `{"var": "<input_key>"}`, so HR triggers onboarding, offboarding, offer
  extension, and review launches straight from the UI. It also embeds `record_list`
  status boards so results (new access grants, spawned tasks) appear as the
  workflow runs.

### Dashboards with reports

A **report** is a saved GROUP BY / metric query over one entity plus a `viz` spec
(`bar`, `grouped_bar`, `line`, `pie`, `donut`, `metric`, `table`, …; see
`services/api/src/api/schemas/report.py`). Build these and drop them onto
dashboards with the `report` element:

| Report | Entity | Viz | Notes |
|---|---|---|---|
| Hires vs Terminations (quarterly) | `employment_event` | `line` | Group by `event_date` bucketed to quarter, `color_by = event_type` for the two-series overlay. A bucketed group-by needs an explicit alias/order to avoid a 400. |
| Active headcount | `employee` | `metric` | KPI count filtered `status = active`. |
| Headcount by department | `employee` | `bar` | Group by the `department` relationship. |
| New hires (recent) | `employee` | `table` | Filter `hire_date >=` a cutoff. |
| Review completion | `performance_review` | `donut` | Group by `status`. |
| Pipeline by stage | `application` | `bar` | Group by `stage`. |
| Access grants by status | `access_grant` | `pie` | provisioned / revoked / suspended. |

Compose ~5 dashboards from these: an **HR command center** (headcount KPI +
hires-vs-terminations + review completion), a **recruiting dashboard** (pipeline
by stage + open reqs), an **onboarding/access dashboard** (access grants by status
+ open onboarding tasks), an **offboarding board**, and a **reviews board**.

## Workflows (automation)

Seven published workflows. Each is a graph of `task` nodes; the record-writing and
notification steps use the engine's real actions — `create_record`, `update_record`,
`get_record`, `send_email` (see the action registry in
`services/api/src/api/services/workflow/actions.py` and
[WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md)). Manual workflows declare input
variables consumed as `{{ inputs.x }}`; each carries a `run_permission`
([RBAC.md](../RBAC.md#workflow-run-permissions)).

1. **Pre-hire · Open Requisition** — *trigger:* manual (inputs: department,
   position, openings). *Steps:* `create_record` a `job_requisition` (status
   `open`); `send_email` to the hiring manager confirming the opening.

2. **Pre-hire · Extend Offer** — *trigger:* manual (inputs: application, base
   salary, start date). *Steps:* `create_record` an `offer` (status `extended`);
   `send_email` to the candidate and hiring manager with the offer details.

3. **Onboarding · Provision New Hire Access** — *trigger:* manual (input:
   employee/new hire). *Steps:* `create_record` five `access_grant` rows
   (`ad_group`, `vpn`, `building_badge`, `email_dl`, `saas`), each linked to the
   employee with status `provisioned`; `create_record` the standard
   `onboarding_task` checklist; `update_record` the employee to `status = active`
   and set `hire_date`; `send_email` to IT (provisioning list), the new hire
   (welcome), and the manager. Optionally `create_record` an `employment_event`
   (`hire`) so the hires report updates.

4. **Offboarding · Deprovision & IT Lockout** — *trigger:* manual (input:
   departing employee). *Steps:* `get_record`/`update_record` every `access_grant`
   for the employee to `status = revoked` (set `revoked_at`); `send_email` to
   `it-security@…` instructing the account **lockout**; `create_record` the
   `offboarding_task` checklist (return badge/laptop, disable accounts, final
   payroll); `update_record` the employee to `status = terminated` with
   `termination_date`; `create_record` an `employment_event` (`termination`);
   notify the manager and HR.

5. **Annual Review Cycle** — *trigger:* manual (input: review cycle / cohort).
   *Steps:* for the target employees, `create_record` a `performance_review`
   linked to the `annual` `review_cycle` (status `draft`, `due_date` = cycle
   close); `send_email` to each employee and manager to begin. (Can be moved to a
   **scheduled** trigger — a cron `schedule` on the trigger, see
   WORKFLOW_ENGINE.md §8 — to auto-launch each period.)

6. **Semi-Annual Review Cycle** — identical shape to (5) against the `semi_annual`
   `review_cycle`; run at the mid-year mark.

7. **Application Outcome Notifications** — *trigger:* record change on
   `application.stage` (on_change). *Steps:* an **exclusive gateway** branches on
   the new `stage` (`offer` → send offer email; `rejected` → send rejection;
   `hired` → hand off to onboarding). In the demo this is left `enabled = false`
   so it can be run manually with a `record_id`; enable it to fire automatically
   on the beat sweep.

The time-off request (from the form above) is a natural eighth workflow: trigger
on `time_off` create, route to the requester's manager as a **user task** (the
human-task inbox, WORKFLOW_ENGINE.md §7), and `update_record` the status to
`approved`/`denied` on their decision.

## Permissions

Three effective roles, enforced with the existing RBAC primitives — see
[RBAC.md](../RBAC.md):

| Role | Sees / does |
|---|---|
| **HR admin** (org admin) | Full CRUD on all HR entities; sees `salary`/`national_id`; runs every workflow. |
| **Manager** (org member, Role dimension) | The directory, their team's reviews and time-off; can run review/offer workflows if granted via `run_permission` mode `specific_roles`. |
| **Employee** (org member) | Their own record, reviews, and time-off only — via `@me`-scoped views. Cannot see others' salary/PII. |

Key controls:

- **Self-scoped views** — build employee-facing views whose `record_list` filters
  use the `@me` sentinel on the `employee` relationship, so "My reviews" and "My
  time off" resolve to the caller's own records
  (`services/api/src/api/services/self_record.py`, `resolve_own_record_id`).
- **Field-level protection** — set `read_access = server_only` on
  `employee.salary`, `employee.national_id`, and `offer.base_salary`. The record
  API then strips those fields for non-privileged members and refuses to
  filter/sort/group on them (closing a filter oracle). Only org admins and the
  workflow engine (privileged) read them. This is migration
  `039_entity_access_control`, enforced in
  `services/api/src/api/repositories/dynamic_entity.py`; see
  [RBAC.md](../RBAC.md) and the same pattern in [LMS.md](../LMS.md).
- **Workflow-only entities** — to keep the audit trail tamper-proof, set
  `write_access = workflow_only` on `access_grant`, `employment_event`, and
  `performance_review` so members cannot fabricate grants, movement events, or
  review ratings directly — only the onboarding/offboarding/review workflows (and
  admins) may write them.
- **Run permissions** — leave the onboarding/offboarding/lockout workflows at
  `run_permission` `org_admin`; open the review-launch and offer workflows to
  managers via `specific_roles`.

## Knowledge & AI (optional)

Give employees a self-service HR assistant grounded in your policy documents.
Upload the employee handbook, PTO policy, benefits guide, and code of conduct into
a knowledge folder, then let members ask questions in chat; the RAG pipeline
retrieves and cites the relevant passages
([KNOWLEDGE_ENGINE.md](../KNOWLEDGE_ENGINE.md)). Two refinements worth adding:

- **Access-scoped answers** — folder/document permission masks flow into vector
  search, so a manager-only policy only surfaces for managers ([RBAC.md](../RBAC.md)).
- **In-workflow knowledge** — the `knowledge_search` workflow action lets an
  automation pull a policy passage (e.g. accrual rules) into a run and use it in a
  templated email or an LLM step (`summarize` / `llm_respond`) when answering a
  time-off or benefits question.

## Extending it

- **Assets/equipment** — add an `asset` entity (laptop, phone, monitor) linked to
  `employee`; have onboarding assign and offboarding reclaim it as tasks.
- **Compensation history** — a `compensation_change` entity (workflow-only,
  `server_only` amount) for raises/promotions feeding the reviews cycle.
- **Scheduled reviews** — move the annual/semi-annual launchers to `cron`
  scheduled triggers so cycles open automatically.
- **Org chart view** — a view built on the self-referential `employee → manager`
  relationship.
- **Headcount planning** — a report comparing `department.headcount_target`
  against active headcount per department.

## Known gaps / TODO

- The live demo built relationships (FKs) via API seeding because forms bind
  scalar fields and related-record elements (`section`/`table`) rather than a
  standalone "pick an existing parent record" control; assess whether a form-level
  relation picker is needed before handing intake forms to non-technical HR staff.
- `record_list` boards render relationship columns as record identifiers, so
  employee-facing boards should surface scalar fields (name, status) and reach
  related data via the report tiles or detail views.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
