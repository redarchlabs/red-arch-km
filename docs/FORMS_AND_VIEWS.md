# Forms & Views

KM2's forms and views are a single element-tree platform: one authoring schema, one
renderer, one validator. A **form** binds to an entity and captures/edits a record
(internally or through a public token link); a **view** reuses the exact same tree to
build a screen or dashboard, entity-bound or standalone. This doc is for engineers
working on the designer, the renderer, or anything that authors forms/views (including
the AI agent). It is grounded in `services/api/src/api/schemas/form_elements.py` and
`ui/src/components/forms/FormRenderer.tsx`.

## Table of Contents

- [Core model: the v2 element tree](#core-model-the-v2-element-tree)
- [Element catalog](#element-catalog)
- [record_list in depth](#record_list-in-depth)
- [Templated hrefs & scheme validation](#templated-hrefs--scheme-validation)
- [Conditional visibility & calculated values](#conditional-visibility--calculated-values)
- [Rendering & submission contract](#rendering--submission-contract)
- [Views, dashboards & standalone composition](#views-dashboards--standalone-composition)
- [Backend model: tables & routers](#backend-model-tables--routers)
- [Authoring: UI builder, agent tools & validation](#authoring-ui-builder-agent-tools--validation)
- [Known gaps / TODO](#known-gaps--todo)

## Core model: the v2 element tree

A form's (or view's) `config` is `{ "version": 2, "elements": FormElement[] }` — a
recursive tree of typed elements defined in `schemas/form_elements.py` (`FormConfig`
lives in `schemas/form.py`) and mirrored in TypeScript in `ui/src/lib/api/forms.ts`.
Every element is a Pydantic model with `extra="forbid"`, and the tree is a discriminated
union on the `type` field — an unknown `type` or unknown key is a 422, never silently
stored.

Three invariants drive the design:

- **Field data types are never author-chosen.** Only `field`, `section`, `table`, and
  `block` columns bind entity data (by `slug` / `relationship_id`). The control and
  validation come from the entity field's own `field_type`
  (`repositories/dynamic_entity.py`); the element only tunes presentation and binding.
- **One tree, one catalog.** The render contract sends the authoring tree plus a resolved
  **field catalog** (`FieldMeta` per entity) — not a parallel "public tree" — so a single
  `FormRenderer` walks the tree and looks each leaf up in the catalog.
- **Container depth is bounded.** `MAX_TREE_DEPTH = 8`; `tree_depth`/`iter_elements` walk
  every container. Layout containers never change entity context; data containers
  (`section`/`table`/`block`) hold only leaf children.

A pre-v2 flat `{fields, sections}` layout is upgraded in place by
`upgrade_legacy_form_config` (a `FormConfig` before-validator) so old rows stay listable
and renderable.

## Element catalog

Every element type in the `FormElement` union. Layout containers nest any element
recursively; data containers hold only leaf children. "Standalone" = valid in a
no-entity view.

| `type` | Purpose | Key props | Binds data? |
|---|---|---|---|
| `field` | One entity field, bound by `slug` | `slug`, `label`, `required`, `read_only`, `width`, `display` (dropdown/radio), `help_text`, `placeholder` | yes (root/section/block scope) |
| `label` | Static text / divider | `text`, `variant` (heading/subheading/paragraph/divider), `width` | no |
| `calculated` | Derived value from a sandboxed expression | `expression`, `result_type`, `target_slug` (persist when set, else display-only), `label`, `help_text`, `width` | writes `target_slug` |
| `input` | Standalone unbound input; value lives in form state under `key` | `key`, `control` (text/textarea/number/slider/toggle/select), `default`, `min`/`max`/`step`, `options`, `required` | no (feeds expressions/buttons) — standalone |
| `live_value` | Display-only readout polling an HTTP endpoint from the browser | `url`, `json_pointer` (dot path), `poll_ms`, `units` | no — standalone |
| `progress` | Display-only progress bar | `value` (expression), `max`, `show_percent` | no |
| `slides` | In-app slide deck (prev/next + progress) | `slug` (JSON slide field, wins) **or** inline `slides[]` ({title, body-markdown, image_url, video_url, require_video, notes}) | reads `slug` — standalone |
| `report` | Embed a saved report (chart/KPI/table) | `report_id`, `title`, `height`, `poll_ms` | no — standalone |
| `record_list` | Read-only live "status board" of an entity's records | `entity`, `fields`, `filters`, `sort_by`/`sort_dir`, `limit`, `poll_ms`, row link + row workflow props (below) | no — standalone |
| `chat` | Conversation panel (lists messages, sends → runs an answer workflow) | `conversation_entity`, `message_entity`, `conversation_relationship`, `answer_workflow_id`, `answer_controls`, `filler`, `poll_ms` | no — standalone |
| `button` | Clickable action | `action` (see below), `style` (primary/secondary/danger/ghost) | via action |
| `form_ref` | Embed another form (views only) | `form_id`, `mode` (fill/display) | via embedded form |
| `section` | One 1:1 related record, inline or modal | `relationship_id` (to-one FK on root), `mode`, `elements` (leaf children) | yes (related entity) |
| `table` | 1:M child grid, incl. cross-entity columns | `anchor_relationship_id` (targets root), `columns`, `min_rows`/`max_rows`, `read_only`, `sort_by`/`sort_dir` | yes (child + related) |
| `block` | 1:M child as stacked sub-forms | `anchor_relationship_id`, `elements` (leaf children), `add_label`, `min_items`/`max_items` | yes (child entity) |
| `tab_group` | Tabs | `tabs[]` = `{label, elements}` | container |
| `panel` | Titled region / fieldset | `title`, `collapsible`, `collapsed`, `elements` | container |
| `accordion` | Collapsible panes | `panes[]` = `{label, elements}` | container |
| `columns` | Multi-column row | `columns[]` = `{span, elements}` | container |

**Button actions** (`ButtonAction`, discriminated on `kind`):

| `kind` | Effect | Key props |
|---|---|---|
| `submit` | Submit the enclosing form (default) | — |
| `run_workflow` | Run a published workflow with templated inputs, via `POST /api/workflows/{id}/run` | `workflow_id`, `inputs` (map of expressions over form values), `confirm`, `success_message` |
| `link` | Navigate to a view/URL; `href` supports `{token}` fill | `href`, `new_tab` |
| `call_connection` | POST/GET a saved workflow **Connection** server-side, via `POST /api/workflows/connections/call` | `connection`, `method`, `path`, `body` (templated), `confirm`, `success_message` |

`call_connection` runs server-side so the connection's stored secret and the workflow
SSRF allow-list still apply — the browser never sees the base URL or secret.

**Table columns** (`TableColumn`, discriminated on `kind`): `field` (`AnchorColumn`, a field
on the child), `related` (`RelatedColumn`, one hop across a to-one on the child; `editable`
upserts + links the related record), and `link` (`LinkColumn`, a per-row hyperlink that
binds no data — see below).

## record_list in depth

`RecordListElement` renders a read-only table of an entity's records (frontend
`RecordListNode` in `FormRenderer.tsx`). It reads `entity` newest-first (or by
`sort_by`/`sort_dir`), shows up to `limit` rows with `fields` as columns (all fields when
empty), and re-polls when `poll_ms` is set. Four features make it the backbone of catalogs
and learner boards:

- **Server-side filters.** `filters` is a list of `RecordListFilter`
  (`{field, op, value}`, ops `eq`/`ne`/`gt`/`gte`/`lt`/`lte`/`in`/`contains`/`isnull`),
  ANDed. They map onto the record endpoint's `field:op[:value]` filter
  (`entity_records_helpers.parse_filters`).
- **`@me` learner-binding.** A filter `value` of the sentinel `@me` on a to-one relation
  field is resolved server-side (in the records endpoint) to the caller's own record id —
  matched by email through `resolve_own_record_id` (`services/self_record.py`, identity
  field `email`, case-insensitive). This scopes a board to just the current user's rows
  (their own attempts/certificates) without hard-coding an id, and is the same identity
  rule as a view's `record_id=me`.
- **Per-row Open link.** `row_link_template` is a URL with `{token}` placeholders filled
  from the row (`{id}` = row record id, `{<field_slug>}` = a field value, each
  URL-encoded), labeled by `row_link_label` ("Open" by default). It routes each row to its
  own destination — e.g. `/views/{player_view_slug}/view?record_id={id}`. Validated at
  author time by `_assert_safe_href` (see below).
- **Per-row workflow button.** `row_workflow_id` runs that workflow against the row's
  record (`row_action_label` labels the button). `row_workflow_inputs` is a map of
  expressions evaluated **per row over the row's field values PLUS the enclosing view's
  values** — so `{"var": "id"}` is the row id, `{"var": "<row field>"}` a row value, and
  `{"var": "<view field>"}` a value from the parent scope. The renderer passes the
  enclosing `scope.values` into `RecordListNode` for exactly this. It powers a generic
  catalog Enroll button (passing `course_id` from the row + the caller's `learner_email`
  from the parent scope).

## Templated hrefs & scheme validation

Three call sites render author-supplied URLs — a `LinkColumn`, a `record_list`
`row_link_template`, and a `link` button's `href`. All share one pure helper module
`ui/src/lib/forms/href.ts` so behavior can't drift:

- `safeHref(url)` — rejects any non-`http(s)` scheme (`javascript:`, `data:`, `vbscript:`,
  …) by returning `"#"`; relative URLs and `http(s)` URLs pass through. This is the XSS
  guard at render/navigate time.
- `fillTokens(template, record)` — replaces `{token}` with `record[token]`, URL-encoded
  (absent → empty string), then runs the result through `safeHref`.

The same guard runs at **author time** on the server: `_assert_safe_href` in
`form_elements.py` is a `field_validator` on `LinkColumn.href_template`,
`LinkAction.href`, and `RecordListElement.row_link_template`, raising a `ValueError`
(422) if a stored link carries a disallowed scheme. `{token}` placeholders are
URL-encoded at render, so only the static scheme prefix is constrained. `LinkAction.href`
fills from the bound record's values at click time (`{id}` = bound record id) — e.g.
`/views/{quiz_view_slug}/view?record_id=me`.

## Conditional visibility & calculated values

**`visible_when`** — the base `_Element` carries an optional `visible_when` expression, so
*any* element can be gated. The renderer evaluates it over the enclosing `scope.values`
with the same sandboxed evaluator as `calculated`; the element renders only when the
result is truthy (`None` = always visible). Because hiding an element never suppresses
server-side validation of required persisted fields, gate inputs/buttons — not required
data fields. `form_layout.flatten` and `_leaf_slugs` **declare the field slugs a
`visible_when` reads** so `build_render` fetches them into the record values (otherwise a
gate would evaluate against `undefined` and silently always-hide).

**Calculated expressions** are a whitelisted-operator JsonLogic interpreter,
`services/form_expression.py` — no `eval`/`exec`, no attribute access. Supported ops:
`var`, arithmetic (`+ - * /`), comparison (`== != < <= > >=`), `if`/`and`/`or`/`!`, `cat`,
and date ops `today`/`now`/`date_add`/`date_diff` (UTC, ISO-8601, month-end clamped). A
bad formula degrades to `null` rather than sinking a submission. The client previews
values through a TS port kept in lock-step, `ui/src/lib/forms/jsonLogic.ts` (identical op
set). When a `calculated` element has a `target_slug`, the server **recomputes it
authoritatively** on submit and ignores any client-sent value.

## Rendering & submission contract

Both surfaces resolve to `FormRenderRead` (`schemas/form.py`): the authoring `config`, a
`catalog` (`EntityCatalogEntry` → `FieldMeta` per entity touched), `relationships`
(`RelationshipMeta`, so the client switches entity context descending into a container),
`values` (prefilled root values), `related` (per-`relationship_id` 1:1 `values` / 1:M
`rows`), and the resolved `record_id` (for `record_id=me`, the caller's own id).

- **Backend** — `FormRenderService` (`services/form_service.py`) builds the render payload
  via `build_render` and applies submissions via `apply_submit`: root fields, 1:1
  `section`s, 1:M `table`/`block`s, cross-entity editable `related` columns (upsert + link
  the related record, ownership-checked), and server-authoritative recompute of persisted
  calculated values. `form_layout.flatten` turns the validated tree into the exact data
  bindings it reads/writes (`RootBinding` + `SectionBinding`/`TableBinding`/`BlockBinding`).
- **Frontend** — one `FormRenderer.tsx` walks the tree for all surfaces (public token page,
  authenticated fill, builder preview), with node components `RecordListNode`, `ChatNode`,
  `SlideDeck` (`coerceSlides`), report/live_value/progress renderers, etc.
- **Submit shape** — `FormSubmit` = `{values, related}`; `related` is keyed by
  `relationship_id` (1:1 → `{values}`, 1:M → `{rows}`). Only slugs the tree actually
  exposes are honored; everything else is dropped server-side.

## Views, dashboards & standalone composition

A view (`ViewService`, `services/view_service.py`) reuses the form tree. It is either:

- **Entity-bound** (`entity_definition_id` set) — validates and renders exactly like a
  form by delegating to `FormRenderService.build_render`. The `record_id=me` sentinel (see
  the router) auto-binds the caller's own record.
- **Standalone** (`entity_definition_id` NULL) — a dashboard. `_validate_config` rejects
  any entity-bound element (`field`/`calculated`/`section`/`table`/`block`); only
  presentational/action/layout elements are allowed (`label`, `button`, `form_ref`,
  `record_list`, `report`, `chat`, `input`, `live_value`, `slides`, `progress`, and layout
  containers). It renders with an empty catalog and no values.

There is no view "type" enum — a "dashboard" is just a standalone view composed of
`report`, `record_list`, and `form_ref` elements. An org can designate one view as its
**home/landing** screen via `orgs.home_view_id` (migration `036_org_home_view`), which
surfaces a "Home" nav item. Caps: `MAX_VIEWS_PER_ORG = 200`. The generated-course player
builds learner-bound quiz/scenario views this way (`services/lms_play_views.py` +
`course_generation.create_play_views`) — see [LMS.md](LMS.md).

## Backend model: tables & routers

Two RLS-scoped tenant tables, both using the standard hardened tenant-isolation policy
template (`org_id = current_setting('app.current_tenant_id')`):

| Table | Migration | Notable columns |
|---|---|---|
| `forms` | `011_intake_forms` | `slug` (unique per org), `entity_definition_id` (**NOT NULL** — a form always binds an entity), `config` JSONB, `is_active` |
| `form_links` | `011_intake_forms` | `token_hash` (**globally unique** — the public path resolves the org from the token before any tenant context), `status` (`pending`/`submitted`/`expired`/`revoked`), `target_record_id`, `recipient_email`, `expires_at`, `submitted_at`, `created_by_id` |
| `views` | `021_views` | `slug` (unique per org), `entity_definition_id` (**nullable** — entity-bound or standalone), `config` JSONB, `is_active` |

Both tables later gained `lineage_id` (migration `037_config_lineage`, unique per org) so a
form/view keeps a stable identity across environments during release promotion — see
[CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md). Note that element props like `row_link_template`,
`filters`, and `visible_when` live inside the JSONB `config`, not as table columns.

Routers (mounted in `services/api/src/api/main.py`):

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /api/forms/` | org admin | List forms |
| `POST /api/forms/` | org admin | Create form (validates layout) |
| `GET·PATCH·DELETE /api/forms/{id}` | org admin | Read / update / delete |
| `GET /api/forms/{id}/render?record_id=` | org member | Resolved render payload (editable) |
| `POST /api/forms/{id}/submit` | org member | Apply a submission (`record_id` in body) |
| `GET·POST /api/forms/{id}/links` | org admin | List / mint single-use token links |
| `POST /api/forms/{id}/links/{link_id}/revoke` | org admin | Revoke a link |
| `GET·POST /api/public/forms/{token}` | none (token) | Public render + submit; per-token rate limit; runs on the privileged session to resolve the org, then `PublicFormService` scopes to it |
| `GET /api/views/` | org member | List views |
| `POST /api/views/` | org admin | Create view |
| `GET /api/views/{id}` | org member | Read view |
| `PATCH·DELETE /api/views/{id}` | org admin | Update / delete |
| `GET /api/views/{id}/render?record_id=` | org member | Resolved render; `record_id` may be a UUID or the sentinel `me` |

Routers: `routers/forms.py` (`router` + `public_router`) and `routers/views.py`. Errors
map through the shared `FormError` hierarchy (`FormConflictError`→409,
`FormNotFoundError`→404, `FormValidationError`→400, `FormLinkError`→410). Auth
dependencies are `require_org_admin` / `require_org_access` (see [RBAC.md](RBAC.md)); the
public form path is unauthenticated by token, throttled by a per-token
`SlidingWindowLimiter`.

## Authoring: UI builder, agent tools & validation

**UI builder** — `ui/src/components/forms/builder/` (form builder pages under
`/forms/{id}`; view builder under `/views/{id}`, runtime at `/views/{id}/view`,
authenticated fill at `/forms/{id}/fill`). The builder preview reuses the same
`FormRenderer` with a catalog built from the entity definition
(`ui/src/lib/forms/catalogFromEntities.ts`), so what the author sees is what fillers get.

**AI agent tools** — `services/agent.py` exposes org-admin-gated designer tools that
take/return the v2 `config` tree and run the same validation as the UI, so an agent can
author, edit, and repair forms/views:

- CRUD: `list_forms`/`get_form`/`create_form`/`update_form`/`delete_form` and
  `list_views`/`get_view`/`create_view`/`update_view`/`delete_view`.
- `describe_form_elements` — an on-demand reference for the full element vocabulary (each
  `type` + its required/optional props). Kept out of the system prompt (progressive
  disclosure) so the model pulls it only when authoring.
- `validate_form_layout(config, entity_slug?)` — a dry-run check. Without `entity_slug` it
  is a structural check only; with it, it verifies every field slug and relationship exists
  on the entity, returning located errors. Tool hints steer the agent to dry-run before
  saving. See [AGENT_ORG.md](AGENT_ORG.md) for the chat-agent tooling.

**Validation** — `services/form_layout.py` is the pure, DB-free validator + binding
flattener, reused by `FormService`, `ViewService`, and the agent tools. `validate` checks
every field slug exists on its entity-in-context, relationships sit in legal positions
(`section` = a to-one on the root; `table`/`block` = a to-many targeting the root;
`related` column = a to-one on the child), and bounds nesting depth. `FormService`/
`ViewService` load the entity context from the DB and hand it in as plain maps; the agent's
`validate_form_layout` reaches it through `FormService.validate_layout`. Invalid trees are
rejected with a precise, located message.

## Known gaps / TODO

- Workflow-run and `call_connection` buttons run published workflows/connections; the
  workflow engine, connections, and SSRF allow-list are documented in
  [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).
- The `chat` element defaults its entity/field names to the robot-chat demo
  (`robot_conversation`/`robot_message`); those are configurable per element but ship with
  demo-oriented defaults.

## Related docs

[WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) · [LMS.md](LMS.md) · [RBAC.md](RBAC.md) ·
[API.md](API.md) · [AGENT_ORG.md](AGENT_ORG.md) · [README](../README.md)

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
