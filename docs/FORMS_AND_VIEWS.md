# Flexible Forms & Views

A composable, tree-based system for designing data-entry forms and screens
("views"), filling them internally or via public token links, and wiring buttons
to workflows. One schema, one renderer, drives every surface.

## Core model: the v2 element tree

A form/view `config` is `{ version: 2, elements: FormElement[] }` — a recursive
tree of typed elements (`schemas/form_elements.py`, mirrored in
`ui/src/lib/api/forms.ts`). Every element has a `type` discriminator:

| Element | Purpose |
|---|---|
| `field` | Bind an entity field by `slug` (+ `read_only`, `required`, `width`, `display`) |
| `label` | Static text (heading/subheading/paragraph/divider) |
| `calculated` | Derived value from a sandboxed JsonLogic `expression`; `target_slug` persists it |
| `button` | Action: `submit` / `run_workflow` (workflow_id + input map) / `link` |
| `form_ref` | Embed another form by id (views) |
| `input` | Standalone unbound input (text/number/slider/toggle/select) into form state; feeds calculated fields + workflow buttons. Valid in standalone views |
| `live_value` | Display-only readout polling an HTTP endpoint for live external state (`url`, `json_pointer`, `poll_ms`). Standalone-valid |
| `record_list` | Read-only live "status board" of an entity's records (`entity`, `fields`, `sort_by`, `poll_ms`, optional per-row `row_workflow_id`). Standalone-valid |
| `report` | Embed a saved report by `report_id`; renders its chart/KPI/table per the report's `viz` (`title`, `height`, `poll_ms`). Standalone-valid |
| `section` | A 1:1 related record, inline or modal |
| `table` | A 1:M child grid; columns from the child entity **and related entities** (cross-join, editable) |
| `block` | A repeatable group of leaf elements (1:M, stacked) |
| `tab_group`, `panel`, `accordion`, `columns` | Nesting layout containers |

Field **types are never author-chosen** — the control and validation derive from
the entity field's own `field_type` (`repositories/dynamic_entity.py`). The tree
only tunes presentation and binding.

## Rendering & submission

- **Backend** (`services/form_service.py`): `FormRenderService` resolves the tree
  into a render payload — the authoring tree plus a **field catalog** (resolved
  `FieldMeta` per entity) and relationship metadata — and applies submissions:
  root fields, 1:1 sections, 1:M tables/blocks, **cross-entity editable columns**
  (upsert + link the related record, ownership-checked), and **server-authoritative
  recompute** of persisted calculated values (client-sent calc values are ignored).
- **Frontend** (`ui/src/components/forms/FormRenderer.tsx`): one component walks
  the tree for all three surfaces (public token page, authenticated fill page,
  builder preview). Calculated values preview live via a TS port of the evaluator
  (`ui/src/lib/forms/jsonLogic.ts`, kept in lock-step with the Python evaluator).

## Surfaces & endpoints

- Public, unauthenticated (token): `GET|POST /api/public/forms/{token}` — resolves
  the org from the token on the privileged session, then RLS-scopes to it.
- Authenticated internal fill: `GET /api/forms/{id}/render?record_id=` +
  `POST /api/forms/{id}/submit` (member-gated). UI: `/forms/{id}/fill`.
- Admin CRUD + links: `/api/forms/*` (org-admin). Builder UI: `/forms/{id}`.
- Views: `/api/views/*` + `GET /api/views/{id}/render`. UI: `/views`, `/views/{id}`
  (builder), `/views/{id}/view` (runtime). A view reuses the same tree; standalone
  (no entity) views allow only label/button/form_ref/layout elements.

## Calculated expressions (sandboxed)

`services/form_expression.py` is a whitelisted-op JsonLogic interpreter — no
`eval`/`exec`, no attribute access. Ops: `var`, arithmetic, comparison,
`if`/`and`/`or`/`!`, `cat`, and date ops `today`/`now`/`date_add`/`date_diff`
(UTC, ISO-8601, month-end clamped). A bad formula degrades to `null`.

## AI agent tools

`services/agent.py` exposes org-admin-gated tools to fully manage the designer:
`list_forms`/`get_form`/`create_form`/`update_form`/`delete_form` and
`list_views`/`get_view`/`create_view`/`update_view`/`delete_view`. All take/return
the v2 `config` tree and run through the same validation the UI does, so an agent
can author, edit, and repair forms/views from feedback.

## Validation

`services/form_layout.py` is a pure (DB-free) validator + binding flattener,
reused by `FormService`, `ViewService`, and the agent tools. It checks every field
slug exists on its entity-in-context, relationships are used in legal positions
(section = root's to-one; table/block = a to-many targeting root; related column =
a to-one on the child), and bounds nesting depth. Invalid trees are rejected with
a precise message.
