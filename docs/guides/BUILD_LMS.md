# Build a Learning Management System on Red Arch KM2

This is the "build it yourself" companion to [LMS.md](../LMS.md). The reference doc explains
how the shipped LMS reference app is architected; this guide walks you through **creating**
one from KM2's generic primitives — custom entities, forms and views, the workflow engine,
entity access control, and RAG chat. There is no LMS module or `courses` table to enable:
you assemble the whole learner journey (catalog → enroll → course player → server-graded
quiz gate → LLM-graded scenario gate → certificate) out of ordinary records, views, and
workflows in your own tenant. The live example is the **"Corporate Training" org**.

## Table of Contents

- [What you'll build](#what-youll-build)
- [Prerequisites](#prerequisites)
- [Reference implementation](#reference-implementation)
- [Data model (entities, fields, relationships)](#data-model-entities-fields-relationships)
- [Forms](#forms)
- [Views & dashboards](#views--dashboards)
- [Workflows (automation)](#workflows-automation)
- [Gating & tamper-proofing](#gating--tamper-proofing)
- [Knowledge & AI](#knowledge--ai)
- [Auto-generating a course](#auto-generating-a-course)
- [Extending it](#extending-it)

## What you'll build

- A **catalog** of published courses, each row offering an **Open** link and a one-click
  **Enroll** button — one generic board that enrolls any course.
- A **course player** that lists the course's modules, plays their slide decks, and links to
  the quiz and scenario, plus a **tutor chat** that answers questions from the course material.
- A **server-graded quiz gate**: multiple-choice questions graded deterministically on the
  server, with a pass threshold.
- An **LLM-graded scenario gate**: the learner writes a free-text roleplay response, an LLM
  scores it against a hidden rubric, and this is the final gate.
- A **certificate** issued automatically — but only when the learner has passed *both* the
  quiz and the scenario.
- **Tamper-proofing** so a learner cannot read the answer key or fabricate a certificate
  through the ordinary records API.

## Prerequisites

- An org where you are an **admin**, and familiarity with the in-app builders (see
  [DEVELOPMENT.md](../DEVELOPMENT.md)).
- The primitive reference docs you'll lean on: [FORMS_AND_VIEWS.md](../FORMS_AND_VIEWS.md)
  (the element tree, `record_list`, `record_id=me` binding), [WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md)
  (the token engine, actions, gateways, `run_workflow` buttons), [RBAC.md](../RBAC.md)
  (custom entity access control), and [KNOWLEDGE_ENGINE.md](../KNOWLEDGE_ENGINE.md) (RAG chat).

**Three ways to build anything below.** Every entity, form, view, and workflow can be created
with (a) the in-app **builders** (UI), (b) the in-app **assistant agent** by describing what
you want, or (c) the **km2-mcp** / agent tools (`km2_create_entity`, `km2_add_entity_field`,
`km2_create_entity_relationship`, `km2_create_view`, `km2_create_workflow`, …). The rest of
this guide describes the **design** in platform terms, not clicks, so it stays durable
whichever method you pick.

## Reference implementation

The design here mirrors the **"Corporate Training" org**, the live reference tenant. Its
courses, quizzes, scenarios, and certificates are ordinary records in that tenant, not repo
seed data — there is **no full seed script** in the repo (the three hand-built courses were
authored live). The reusable pieces the LMS leans on *are* platform code and are cited
throughout; the entity definitions and grading workflows are per-tenant. The only
LMS-specific server code is the **course generator** (see
[Auto-generating a course](#auto-generating-a-course)), which writes exclusively generic
records and views: `services/api/src/api/services/course_generation.py`,
`services/api/src/api/services/lms_play_views.py`,
`services/api/src/api/services/llm_generate_course.py`, wired as the `generate_course` tool in
`services/api/src/api/services/agent.py`.

## Data model (entities, fields, relationships)

Create ten **custom entities** in your tenant. The platform code (the generator and the play
views) resolves them **by slug**, so keep these slugs. Field types below are the ordinary KM2
field types; the two access policies (`server_only`, `workflow_only`) are the load-bearing
detail — see [Gating & tamper-proofing](#gating--tamper-proofing) for why.

### `course`

| Field | Type | Notes |
|---|---|---|
| `title` | text | Course name. |
| `description` | long text | Catalog blurb. |
| `category` | text | `privacy` \| `security` \| `role` \| `compliance` \| `onboarding`. |
| `estimated_minutes` | number | Optional duration. |
| `code` | text | Unique course code (generator emits `CAT-<8 hex>`). |
| `status` | text | `draft` \| `published`; only `published` shows in the catalog. |
| `quiz_view_slug` | text | Slug of this course's quiz play view — points the generic player at it. |
| `scenario_view_slug` | text | Slug of this course's scenario play view. |

### `module`

| Field | Type | Notes |
|---|---|---|
| `title` | text | Module name. |
| `slides` | JSON | The slide/deck content played in the course player. |
| `sort_order` | number | Ordering within the course. |
| `content_type` | text | e.g. `reading`. |
| `estimated_minutes` | number | Optional. |
| `course` | relation → `course` | Owning course (many modules per course). |

### `assessment` (the quiz)

| Field | Type | Notes |
|---|---|---|
| `title` | text | e.g. "<Course> — Quiz". |
| `passing_threshold` | number | Percent to pass (defaults to 70 in the grader if unset). |
| `course` | relation → `course` | Owning course. |

### `question`

| Field | Type | Notes |
|---|---|---|
| `prompt` | text | The question. |
| `type` | text | `mcq`. |
| `options` | JSON | Array of choice strings. |
| `correct_answer` | text | **`read_access = server_only`** — the answer key. Must equal one of `options`. |
| `explanation` | text | Shown after grading. |
| `points` | number | Usually 1. |
| `sort_order` | number | Ordering. |
| `assessment` | relation → `assessment` | Owning quiz. |

### `scenario`

| Field | Type | Notes |
|---|---|---|
| `title` | text | Scenario name. |
| `prompt` | long text | The situation the learner responds to. |
| `persona` | text | Optional roleplay persona. |
| `rubric` | text | **`read_access = server_only`** — grading criteria; hidden so a learner can't game it. |
| `learning_objective` | text | Optional. |
| `skill_area` | text | Optional. |
| `difficulty` | text | e.g. `medium`. |
| `pass_threshold` | number | Score (0–100) needed to pass (defaults to 70). |
| `mode` | text | `roleplay`. |
| `max_score` | number | Usually 100. |
| `category` | text | Mirrors the course category. |
| `course` | relation → `course` | Owning course. |

### `learner`

| Field | Type | Notes |
|---|---|---|
| `email` | text/email | **Identity field** — matched to the caller's email for `record_id=me` / `@me`. |
| `name` | text | Display name. |

The `learner` entity is the **bound target** of the quiz and scenario play views. A view opened
with `record_id=me` binds to the caller's own `learner` record by email (see
[Views & dashboards](#views--dashboards)).

### `enrollment`

| Field | Type | Notes |
|---|---|---|
| `course` | relation → `course` | Enrolled course. |
| `learner_email` | text | The learner (denormalized email; written by the self-enroll workflow). |
| `category` | text | Denormalized course category (drives catalog filtering / dashboards). |
| `module_progress` | JSON | Per-module completion state. |

### `assessment_attempt`

| Field | Type | Notes |
|---|---|---|
| `passed` | boolean | **Written by the grading workflow.** |
| `score` | number | Integer percent from `grade_quiz`. |
| `learner` | relation → `learner` | Who attempted. |
| `assessment` | relation → `assessment` | Which quiz. |

**`write_access = workflow_only`** — only workflows and admins may create/update these.

### `simulation_attempt`

| Field | Type | Notes |
|---|---|---|
| `passed` | boolean | From the scenario grader. |
| `score` | number | 0–100 from `llm_grade`. |
| `feedback` | text | LLM feedback shown to the learner. |
| `learner` | relation → `learner` | Who attempted. |

**`write_access = workflow_only`**.

### `certification`

| Field | Type | Notes |
|---|---|---|
| `certificate_no` | text | Certificate number. |
| `issued_date` | date/datetime | When issued (the "issued_at"). |
| `status` | text | e.g. `active`. |
| `learner` | relation → `learner` | Certified learner. |
| `course` | relation → `course` | Certified course. |

**`write_access = workflow_only`** — a learner cannot fabricate a certificate through the API.

### Relationships summary

| From → To | Kind |
|---|---|
| `module` → `course` | many-to-one |
| `assessment` → `course` | many-to-one |
| `question` → `assessment` | many-to-one (question → quiz → course) |
| `scenario` → `course` | many-to-one |
| `enrollment` → `course` | many-to-one |
| `assessment_attempt` → `learner`, `assessment` | many-to-one each |
| `simulation_attempt` → `learner` | many-to-one |
| `certification` → `course`, `learner` | many-to-one each |

## Forms

Authoring is ordinary CRUD. Build simple intake/edit forms for the **authoring** entities you
manage by hand — `course`, `module`, `assessment`, `question`, `scenario` — with their fields
laid out top to bottom. The generator can produce these records for you (see below), so hand
forms are only needed if you author courses manually.

You do **not** build forms for `enrollment`, `assessment_attempt`, `simulation_attempt`, or
`certification`: those are written by workflows, never by hand. The learner "forms" are the
quiz and scenario **views** in the next section (they are element trees rendered by the shared
`FormRenderer`, submitting to workflows rather than writing records directly).

## Views & dashboards

Four views make up the learner journey. See
[record_list in depth](../FORMS_AND_VIEWS.md#record_list-in-depth) and
[Templated hrefs & scheme validation](../FORMS_AND_VIEWS.md#templated-hrefs--scheme-validation)
for the primitives.

### 1. Catalog (`catalog`)

A view holding a **`record_list` of `course`** (filtered to `status = published`) with two
per-row affordances — one board that opens and enrolls any course:

- **`row_link_template`** — a per-row Open link whose `{token}` placeholders fill from the row,
  e.g. `/views/course_play/view?record_id={id}` with `row_link_label: "Open"`. Tokens are
  filled and scheme-checked by `fillTokens`/`safeHref` in `ui/src/lib/forms/href.ts`.
- **`row_workflow_inputs`** — inputs for a per-row **Enroll** button (`row_workflow_id` = your
  self-enroll workflow). Each input is an expression evaluated over the row's values merged
  onto the enclosing view's scope: pass `course_id = {var: id}` (the row's course id) and
  `learner_email = {var: email}` (the caller's email, pulled in from the outer view). This
  replaces any hardcoded per-course Enroll panels.

### 2. Course player (`course_play`)

A **course-bound** view (opened `?record_id={course_id}` from the catalog Open link) that
contains:

- A **`record_list` of `module`** filtered to `course = <this course>`, ordered by
  `sort_order`, with a per-row link into the module's slide deck.
- Two **templated link buttons** (a `LinkAction` button whose `href` fills from the bound
  course record at click time): "Take the quiz" → the course's `quiz_view_slug`, and "Do the
  scenario" → its `scenario_view_slug`. Because the href is filled from the course's own
  fields, one generic player routes to every course's play views.
- A **tutor `chat` node** (see [Knowledge & AI](#knowledge--ai)).

### 3. Quiz play view (learner-bound)

Bind this view to the **`learner`** entity and open it with **`record_id=me`**. That binding is
what makes it "yours": `ViewService.render` detects the `me` sentinel and binds the view to the
caller's own `learner` record by matching email (`resolve_own_record_id` in
`services/api/src/api/services/self_record.py`). The view carries a **hidden, read-only `email`
field** (`visible_when: false`) purely to pull the learner's email into scope for `{var: email}`
— never displayed. Its shape (see `build_quiz_view_config` in `lms_play_views.py`):

- One `select` **input** per question (positional keys `a1..aN` — the order the grader reads).
- A **Submit** button with a `run_workflow` action targeting your **"Quiz: Grade"** workflow,
  passing `{a1..aN, assessment_id: <this quiz's id>, learner_email: {var: email}}`.
- A **"Your result" `record_list`** of `assessment_attempt`, filtered
  `learner = @me` **and** `assessment = <this quiz's id>`, with `poll_ms: 2500` so the graded
  score lands without a manual refresh.

The `@me` filter substitutes the caller's own `learner` id server-side
(`resolve_me_filters` in `entity_records_helpers.py`); an unresolvable caller yields an empty
board, never org-wide rows.

### 4. Scenario play view (learner-bound)

Also bound to `learner`, opened `record_id=me`, with the same hidden `email` field (see
`build_scenario_view_config`):

- The scenario `prompt` shown in a `panel`, then a **`textarea`** input keyed `response`.
- A **Submit for grading** button (`run_workflow`) targeting **"Scenario: Grade & Certify"**,
  passing `{response: {var: response}, scenario_id: <this scenario's id>, learner_email: {var: email}}`.
- A **"Your result" `record_list`** of `simulation_attempt` filtered `learner = @me`, and a
  **"Your certificate" `record_list`** of `certification` filtered `learner = @me` **and**
  `course = <this course's id>` — both polling every 2500 ms.

Optionally add a learner **dashboard** composing "My enrollments" (`enrollment` filtered
`@me`), "My certificates" (`certification` filtered `@me`), and the catalog into one page.

## Workflows (automation)

Three workflows drive enrollment and grading. See [WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md)
for the token engine, actions, and gateways; the grading actions are LMS-agnostic and
parameterised by entity/field slugs, so the same workflows serve every course.

### Self-enroll (idempotent)

- **Trigger:** manual (`run_workflow` from the catalog's per-row Enroll button).
- **Inputs:** `course_id`, `learner_email`.
- **Steps:** look up any existing `enrollment` for `(course_id, learner_email)`; if none,
  create one — denormalizing `category` from the course and seeding `module_progress`.
  Idempotency means clicking Enroll twice yields one enrollment.

### Quiz: Grade (server-graded MCQ gate)

- **Trigger:** manual (submitted from the quiz play view).
- **Inputs:** the answers (`a1..aN`), `assessment_id`, `learner_email`.
- **Steps → actions:**
  1. Resolve the `learner` from `learner_email`.
  2. **`grade_quiz`** (`GradeQuiz` in `services/api/src/api/services/workflow/actions.py`):
     loads the assessment's `question` rows (paging so every question counts), compares each
     answer to the stored `correct_answer`, and returns `{score, passed, correct, total,
     answered}` where `score` is integer percent and `passed = score >= pass_threshold`
     (default 70). Grading is **server-side and deterministic** — the browser never sees the
     answer key.
  3. Write an `assessment_attempt` (`workflow_only`) recording `passed`, `score`, `learner`,
     `assessment`.

### Scenario: Grade & Certify (LLM gate + cert-on-pass)

- **Trigger:** manual (submitted from the scenario play view).
- **Inputs:** `response`, `scenario_id`, `learner_email`.
- **Steps → actions:**
  1. Resolve the `learner`; load the `scenario` (including its `server_only` `rubric`, which
     the workflow can read because it runs privileged).
  2. **`llm_grade`** (`LlmGrade` in `actions.py`): grades the free-text `response` against the
     rubric via the org LLM, constrained to `{score: 0..100, feedback}` with a prompt-injection
     guard. The LLM only judges; the workflow owns the policy — `passed = score >= pass_threshold`.
  3. Write a `simulation_attempt` (`workflow_only`) with `passed`, `score`, `feedback`.
  4. **Gate:** an exclusive gateway branches on `{{vars.grade.passed}}`. On the pass branch,
     check that the learner **also** has a passing `assessment_attempt` for the course's quiz;
     only if **both** hold, write a `certification` (`workflow_only`) with `certificate_no`,
     `issued_date`, `status`, `learner`, `course`.

The two gates are real and ordered: a passing scenario with **no** quiz pass yields **no**
certificate (verified in the reference org).

## Gating & tamper-proofing

The gates above would be theatre if a learner could read the answer key or POST their own
certificate through the ordinary records API. Two access-control policies close that, both
introduced by migration `services/api/alembic/versions/039_entity_access_control.py` and
enforced in `services/api/src/api/repositories/dynamic_entity.py`. See
[Entity & field access control](../RBAC.md#entity--field-access-control) in [RBAC.md](../RBAC.md) and the
[tamper-proofing section](../LMS.md#tamper-proofing-via-entity-access-control) of [LMS.md](../LMS.md).

- **`entity_fields.read_access = server_only`** on `question.correct_answer` and
  `scenario.rubric`: these values are stripped from the record API for regular members and
  can't be filtered/sorted/grouped on (closing a filter oracle). The learner literally cannot
  fetch the answer key.
- **`entity_definitions.write_access = workflow_only`** on `certification`,
  `assessment_attempt`, and `simulation_attempt`: only the workflow engine and org admins may
  create/update/delete these; direct member writes are 403'd.

Enforcement hinges on a **`privileged`** flag on `DynamicEntityRepository`. The workflow engine
and admins run privileged (bypass the policy); regular members and the public `/api/v1` key
surface do not. Both policies default to the pre-existing fully-open behaviour, so you opt in
per field/entity — set them only on the four above and everything else is unaffected. This is
also why course authoring must run privileged (below): a non-privileged write would silently
drop the `server_only` `correct_answer`.

## Knowledge & AI

The course player's **tutor chat** is a `chat` node backed by RAG. Ingest the course's source
material (upload documents into a course folder) so the knowledge engine chunks and embeds it;
the chat then answers learner questions with `knowledge_search` grounded in that material,
scoped to the caller's tenant. See [KNOWLEDGE_ENGINE.md](../KNOWLEDGE_ENGINE.md) for ingest,
retrieval, and citations. The scenario grader's `llm_grade` is the other AI touchpoint — see
[Workflows](#workflows-automation).

## Auto-generating a course

Rather than authoring every record by hand, an admin can generate a full published course from
a topic. This is the only LMS-specific server code, and it writes only generic records/views.

1. **Author** — `generate_course_blueprint` in
   `services/api/src/api/services/llm_generate_course.py` calls the org LLM with a strict
   `json_schema` bounding it to one blueprint: title/description, modules (each 3–5 slides), an
   MCQ quiz (4–5 questions, each with 4 options and a `correct_answer` copied verbatim from the
   options), and one roleplay scenario. Post-parse cleaners drop unusable modules/questions
   (e.g. a `correct_answer` not in `options`).
2. **Persist (privileged)** — `CourseGenerationService.create_from_blueprint` in
   `course_generation.py` resolves the five LMS entities by slug and creates the linked graph
   (course → modules → assessment → questions → scenario) through
   `build_record_repo(..., privileged=True)`, so the `server_only` answer key survives the
   write. The course `code` is unique (`CAT-<8 hex>`) and it's created `status: published`.
3. **Wire into the generic player** — `create_play_views` builds the two learner-bound views
   from `lms_play_views.py` (`build_quiz_view_config`, `build_scenario_view_config`), bound to
   `learner` and opened `record_id=me`, then patches the course's `quiz_view_slug` /
   `scenario_view_slug`. It reuses the org's existing **"Quiz: Grade"** and **"Scenario: Grade
   & Certify"** workflows. Best-effort: if the org lacks those workflows or a `learner` entity,
   the records still stand (browsable) but the slugs return `None` (not yet playable).
4. **The tool** — `generate_course` (registered in `agent.py`, handler `_tool_generate_course`)
   is **admin-only** and takes `topic`, `category` (privacy|security|role|compliance|onboarding),
   optional `audience`, and `num_modules` (2–5). A real `gpt-5-mini` run produced a valid
   published **"Fire Safety"** course (slides + MCQ + scenario) that plays through the same
   generic catalog/player as the hand-built courses. See [AGENT_ORG.md](../AGENT_ORG.md) for the
   agent-tool surface and [LMS.md](../LMS.md#course-generation) for the full walkthrough.

## Extending it

- **Progress tracking** — have the course player update `enrollment.module_progress` as slide
  decks are completed, and gate the quiz on all modules being read.
- **Multiple scenarios / retakes** — add more `scenario` records per course and cap attempts by
  counting `simulation_attempt` rows per learner in the grading workflow.
- **Reporting** — build reports over `assessment_attempt` / `certification` (pass rates,
  certificates issued per quarter) and embed them in an admin dashboard.
- **Expiry & recert** — add an `expires_date` to `certification` and a scheduled workflow that
  flips `status` and re-enrolls learners whose certification has lapsed.
- **More course generation** — extend the blueprint schema (e.g. video modules) in
  `llm_generate_course.py` and the persistence in `course_generation.py`.

For the full architecture of the shipped reference app, see [LMS.md](../LMS.md). For other
build guides, see [docs/guides/README.md](README.md).

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
