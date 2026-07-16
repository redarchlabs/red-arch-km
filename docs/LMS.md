# LMS (Learning Management)

KM2's learning management "module" is not a bespoke subsystem — it is a **reference
application assembled from the generic platform primitives** (custom entities, forms &
views, the workflow engine, entity access control, and RAG chat) plus a small number of
LMS-shaped platform additions and an admin course generator. The live example is the
**"Corporate Training" org**, whose courses, quizzes, scenarios, and certificates are all
ordinary records and views in that tenant. This doc explains the learner journey, the
reusable platform primitives that make it work, and the course generator — and is explicit
about what is platform code versus org-specific seed data.

Audience: engineers and technical evaluators who want to understand how an LMS is built on
KM2 without any LMS-specific tables or hardcoded flows.

## Table of Contents

- [Overview: reference application, not a module](#overview-reference-application-not-a-module)
- [The learner journey](#the-learner-journey)
- [Platform primitives that enable it](#platform-primitives-that-enable-it)
  - [Learner-bound views and the `@me` record-list filter](#learner-bound-views-and-the-me-record-list-filter)
  - [Per-row links and per-row workflow inputs](#per-row-links-and-per-row-workflow-inputs)
  - [Templated button/link hrefs and the scheme validator](#templated-buttonlink-hrefs-and-the-scheme-validator)
  - [Server-graded quiz and LLM-graded scenario](#server-graded-quiz-and-llm-graded-scenario)
  - [Tamper-proofing via entity access control](#tamper-proofing-via-entity-access-control)
- [Course generation](#course-generation)
- [Data model (reference org)](#data-model-reference-org)
- [Cross-references](#cross-references)
- [Known gaps / TODO](#known-gaps--todo)

## Overview: reference application, not a module

There is **no `courses` table, `enrollments` table, or LMS router** in the codebase. The
Corporate Training org's `course`, `module`, `question`, `scenario`, `enrollment`,
`assessment`, `certification`, and attempt records are instances of **custom entities**
defined at the tenant level, stored in the shared dynamic-entity storage and served by the
same entity-records API every org uses. The learner UI is built from **views** (saved
`FormConfig` element trees rendered by the one shared `FormRenderer`), and grading/
certification runs through the **workflow engine**.

What *is* platform code are the reusable pieces the LMS leans on:

| Capability | Platform code | Reusable / LMS-agnostic? |
|---|---|---|
| Learner-bound views + `@me` filter | `services/api/src/api/services/self_record.py`, `entity_records_helpers.py`, `view_service.py` | Yes — any per-user board |
| Per-row Open link + per-row Enroll | `RecordListElement` in `ui/src/lib/api/forms.ts`, `FormRenderer.tsx` | Yes |
| Templated hrefs + scheme check | `ui/src/lib/forms/href.ts` | Yes |
| Server-graded MCQ (`grade_quiz`) | `services/api/src/api/services/workflow/actions.py` | Yes (entity/field slugs configurable) |
| LLM grading (`llm_grade`) | `services/api/src/api/services/llm_grade.py`, `workflow/actions.py` | Yes |
| Tamper-proofing (`server_only`/`workflow_only`) | migration `039_entity_access_control`, `repositories/dynamic_entity.py` | Yes |
| Course generation | `services/api/src/api/services/{llm_generate_course,course_generation,lms_play_views}.py`, `agent.py` | LMS-specific, but writes only generic records/views |

The grading workflows ("Quiz: Grade", "Scenario: Grade & Certify") and the entity
definitions themselves are **org-specific seed data** — they live in the Corporate Training
tenant, not in the repo.

## The learner journey

The self-serve flow, and the platform primitive each step maps to:

| Step | What the learner does | Platform primitive |
|---|---|---|
| **Catalog** | Browse published courses; per row an **Open** link and an **Enroll** button | A view holding a `record_list` of `course` with `row_link_template` (Open) + `row_workflow_inputs` (Enroll) |
| **Enroll** | Click Enroll → idempotent self-enroll | Per-row `run_workflow` firing the org's self-enroll workflow with `course_id` + the caller's `learner_email` |
| **Course player** | Open the course; read module slide decks; reach quiz/scenario | A course-bound view: a `module` `record_list` + templated link buttons to the play views + a tutor `chat` (RAG) |
| **Quiz gate** | Answer MCQs, submit | A learner-bound quiz view submits answers to the **"Quiz: Grade"** workflow (`grade_quiz` action, server-graded) |
| **Scenario gate** | Write a roleplay response, submit | A learner-bound scenario view submits to **"Scenario: Grade & Certify"** (`llm_grade` action) — the final gate |
| **Certificate** | See the issued certificate appear | The grading workflow writes a `certification` record (a `workflow_only` entity); a `@me`-filtered `record_list` polls it into view |

The gate is real: the scenario workflow certifies **only when the learner has also passed
the quiz** (see [Server-graded quiz and LLM-graded scenario](#server-graded-quiz-and-llm-graded-scenario)).
All views are opened with `record_id=me`, so the learner never picks "who they are" — the
platform binds the view to their own record.

## Platform primitives that enable it

### Learner-bound views and the `@me` record-list filter

Two "it's me" features share **one identity rule** so they can't drift, both implemented in
`services/api/src/api/services/self_record.py` via `resolve_own_record_id(...)`. It matches
the caller's email (case-insensitive, field slug `email` by default) to a record in a target
entity, scoping to the org's RLS tenant first so a session with no tenant fails **closed**
(zero rows) rather than leaking.

- **`record_id=me` view binding** — `ViewService.render` (in `view_service.py`) detects the
  `me` sentinel (signalled by `current_user_email`) and auto-binds an entity-bound view to
  the caller's own record. No match / no `email` field → an unbound render, not an error.
  This is why the generated quiz/scenario play views carry a hidden, read-only `email` field
  (`_email_field()` in `lms_play_views.py`, `visible_when: false`): it pulls the learner's
  email into scope for `{var: email}` without displaying it.
- **`@me` record-list filter** — `resolve_me_filters(...)` in `entity_records_helpers.py`
  substitutes `@me` on a **to-one relation** filter (e.g. `learner:eq:@me`) with the caller's
  own record id. `@me` on a non-relation field is a 400. An unresolvable caller resolves to a
  no-match id (`uuid.UUID(int=0)`), so a "my rows" board is empty rather than org-wide. This
  drives every "Your result" / "Your certificate" board in the play views.

Both rules are also exposed in the `record_list` config: a filter `value` of `"@me"` on a
relation field (`RecordListFilterConfig` in `ui/src/lib/api/forms.ts`) scopes the board to
the caller's own records.

### Per-row links and per-row workflow inputs

The catalog's generic Open + Enroll come from two fields on `RecordListElement`
(`ui/src/lib/api/forms.ts`, rendered in `RecordListNode` inside
`ui/src/components/forms/FormRenderer.tsx`):

- **`row_link_template`** — a per-row hyperlink whose `{token}` placeholders are filled from
  the row: `{id}` is the row id, `{<field>}` a field value (e.g.
  `/views/{player_view_slug}/view` or `/views/course_play/view?record_id={id}`). Fill +
  scheme-check delegate to `fillTokens` (below). `row_link_label` names the link ("Open").
- **`row_workflow_inputs`** — inputs for a per-row `row_workflow_id` button, each an
  expression evaluated over the row's values **merged onto the enclosing view's scope**
  (`const evalScope = { ...scopeValues, ...row }` in `runRow`). So `{var: id}` is the row id,
  `{var: <row field>}` a row value, and `{var: <parent field>}` a value from the outer view
  (e.g. the learner's `email`). The catalog's Enroll passes `course_id = {var: id}` and
  `learner_email = {var: email}` to the self-enroll workflow. This replaced the previous
  hardcoded per-course Enroll panels, making one catalog board enroll any course.

### Templated button/link hrefs and the scheme validator

`ui/src/lib/forms/href.ts` holds two pure, dependency-free helpers shared by all three link
call sites (link columns, per-row `record_list` links, and templated button hrefs) so their
behaviour can't diverge:

- **`fillTokens(template, record)`** — replaces `{token}` placeholders from a flat
  `{slug: value}` map (`{id}` and each `{<field>}`), URL-encoding each value; an absent token
  becomes empty string. It then passes the result through `safeHref`.
- **`safeHref(url)`** — rejects any non-`http(s)` scheme (`javascript:`, `data:`,
  `vbscript:`, …) by returning `#`, so a stored, author-supplied link can never become an XSS
  vector when rendered or navigated. Relative and http(s) URLs pass through unchanged.

A `LinkAction` button (`{ kind: "link"; href: string; new_tab?: boolean }`) fills its href
from the bound record's values at click time and scheme-checks via `fillTokens` before
`window.location.href`/`window.open` (see the `btn.action.kind === "link"` branch in
`FormRenderer.tsx`). Scheme validation therefore happens at fill/navigate time through
`safeHref`, not via a stored allowlist.

### Server-graded quiz and LLM-graded scenario

Two workflow actions in `services/api/src/api/services/workflow/actions.py` do the grading;
both are LMS-agnostic and configurable.

- **`grade_quiz` (`GradeQuiz`)** — deterministically grades an MCQ **server-side**. It loads
  an assessment's `question` rows (paging the cursor so every question counts) and compares
  each answer to the stored `correct_answer`, returning
  `{score, passed, correct, total, answered}` where `score` is integer percent and
  `passed = score >= pass_threshold` (default 70). Answers may be **id-keyed**
  (`{question_id: choice}`, order-independent, preferred) or **positional** (`a1..aN`). Entity
  and field slugs (`question_slug`, `assessment_ref`, `order_field`) are configurable, so the
  action is not LMS-specific. Its own docstring flags the trust model: grading is correct, but
  the action alone does not make the quiz tamper-proof — that needs entity access control
  (next section).
- **`llm_grade` (`LlmGrade`)** — grades a free-text answer with the org's LLM via
  `grade_answer` in `llm_grade.py`, which constrains the model to `{score: 0..100, feedback}`
  with a strict JSON schema and a system prompt that ignores instructions embedded in the
  answer (prompt-injection guard). The **LLM only judges**; the workflow owns the pass
  policy — `passed = score >= pass_threshold` is applied in the action.

**Gating and cert-on-pass**: the quiz view submits to the org's **"Quiz: Grade"** workflow
(records an `assessment_attempt`); the scenario view submits to **"Scenario: Grade &
Certify"**. A downstream exclusive gateway branches on `{{vars.quiz.passed}}` /
`{{vars.grade.passed}}`. Certification is issued only when the scenario passes **and** the
learner already passed the quiz — verified in the reference org (a passing scenario with no
quiz pass yields no certificate). The scenario play view's copy states this explicitly ("if
you pass (having also passed the quiz) you're certified").

### Tamper-proofing via entity access control

The gates would be theatre if a learner could read the answer key or fabricate a passing
certificate through the ordinary records API. Migration
`services/api/alembic/versions/039_entity_access_control.py` adds two catalog policies,
enforced in `services/api/src/api/repositories/dynamic_entity.py`:

- **`entity_fields.read_access = server_only`** — the field's values are stripped from the
  record API for regular members and cannot be filtered/sorted/grouped on (to close a filter
  oracle). Used for `question.correct_answer` and the scenario `rubric`.
- **`entity_definitions.write_access = workflow_only`** — only the workflow engine and org
  admins may create/update/delete; direct member writes are 403'd. Used for `certification`,
  `assessment_attempt`, and `simulation_attempt`.

Both default to the pre-existing fully-open behaviour, so existing entities are unaffected
until an admin opts in. Enforcement hinges on a **`privileged`** flag on
`DynamicEntityRepository`: the workflow engine and org admins run privileged (bypass the
policy), regular members and the public `/api/v1` key surface do not. This is why the course
generator writes privileged (it must persist the `server_only` answer key), and why the
grading workflows can write `workflow_only` attempt/certification records that the learner
cannot. See [RBAC.md](RBAC.md) for the full access model.

## Course generation

An admin agent tool authors and persists a complete course from a topic. It is the only
LMS-specific server code, but it writes exclusively generic records and views.

1. **Authoring (schema-bounded blueprint)** — `generate_course_blueprint` in
   `services/api/src/api/services/llm_generate_course.py` calls the org's LLM with a strict
   `json_schema` response format bounding it to one course blueprint: title/description,
   modules (each 3–5 slides), an MCQ quiz (4–5 questions, each 4 options with
   `correct_answer` copied verbatim from the options), and one roleplay scenario. The module
   estimate and thresholds are re-clamped after parsing, and post-parse cleaners drop
   unusable modules/questions (e.g. a `correct_answer` not present in `options`). This module
   is side-effect-free — it only authors.
2. **Persistence (privileged writes)** — `CourseGenerationService.create_from_blueprint` in
   `services/api/src/api/services/course_generation.py` resolves the five LMS entities **by
   slug** and creates the linked graph (course → modules → assessment → questions → scenario)
   through `build_record_repo(..., privileged=True)`. Privileged is required so the
   `server_only` `question.correct_answer` survives the write (a non-privileged write would
   silently drop it) and so it can write `workflow_only` entities. The caller owns the
   transaction (no commit here). The course `code` is unique by construction
   (`CAT-<8 hex>`), and the course is created `status: published`.
3. **Wiring into the generic player** — `create_play_views` builds two learner-bound views
   from `services/api/src/api/services/lms_play_views.py`: a quiz view (`build_quiz_view_config`
   — one `select` per question, submitting to "Quiz: Grade") and a scenario view
   (`build_scenario_view_config` — a response textarea submitting to "Scenario: Grade &
   Certify"), both bound to the `learner` entity and opened with `record_id=me`. It then
   patches the course's **`quiz_view_slug` / `scenario_view_slug`** fields (only if the org's
   `course` entity has them), so the generic catalog/player routes to the generated course's
   own quiz/scenario with no per-course view edits. This is best-effort: if the org lacks the
   grading workflows or a `learner` entity, the records still stand (browsable) and the slugs
   return `None` (not yet playable).
4. **The agent tool** — `generate_course` is registered in
   `services/api/src/api/services/agent.py` (handler `_tool_generate_course`), **admin-only**,
   taking `topic`, `category` (privacy|security|role|compliance|onboarding), optional
   `audience`, and `num_modules` (2–5). A real `gpt-5-mini` run produced a valid published
   **"Fire Safety"** course (slides + MCQ + scenario) that appears in the catalog and plays
   through the same generic player as the hand-built courses. See [AGENT_ORG.md](AGENT_ORG.md)
   for the agent-tool surface.

## Data model (reference org)

These are **custom entities** in the Corporate Training tenant, not platform tables — the
generator and views resolve them by slug. Relationships are the ordinary entity
relationships; the notable access policies are called out.

| Entity (slug) | Purpose | Notable fields / policy |
|---|---|---|
| `course` | A published course | `title`, `description`, `category`, `estimated_minutes`, `code`, `status`; `quiz_view_slug` / `scenario_view_slug` point the generic player at its play views |
| `module` | An ordered unit with a slide deck | `title`, `slides` (JSON deck), `sort_order`, `content_type`, `estimated_minutes`, `course` |
| `assessment` | A quiz for a course | `title`, `passing_threshold`, `course` |
| `question` | One MCQ | `prompt`, `type`, `options`, `correct_answer` (**`server_only`**), `explanation`, `points`, `sort_order`, `assessment` |
| `scenario` | A roleplay assessment | `title`, `prompt`, `persona`, `rubric` (**`server_only`**), `learning_objective`, `skill_area`, `difficulty`, `pass_threshold`, `mode`, `max_score`, `category`, `course` |
| `learner` | The person taking courses | Bound target of the play views; matched by `email` for `record_id=me` / `@me` |
| `enrollment` | A learner's enrollment in a course | Written by the self-enroll workflow (idempotent) |
| `assessment_attempt` | A graded quiz attempt | **`workflow_only`**; `passed`, `score`, `learner`, `assessment` |
| `simulation_attempt` | A graded scenario attempt | **`workflow_only`**; `passed`, `score`, `feedback`, `learner` |
| `certification` | An issued certificate | **`workflow_only`**; `certificate_no`, `issued_date`, `status`, `learner`, `course` |

Field slugs above are those the platform code reads/writes (`course_generation.py`,
`lms_play_views.py`); the exact entity set and any additional fields are org seed data and
may vary.

## Cross-references

- [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md) — the view/`FormConfig` element tree, `record_list`
  boards, `record_id=me` binding, and the shared `FormRenderer`.
- [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) — the token engine, actions (`grade_quiz`,
  `llm_grade`), gateways, and `run_workflow` view buttons.
- [RBAC.md](RBAC.md) — access model, custom entity access, and the `write_access` /
  `read_access` policies that make records tamper-proof.
- [KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md) — the RAG chat the course player's tutor uses.
- [AGENT_ORG.md](AGENT_ORG.md) — the agent tool surface, including `generate_course`.

## Known gaps / TODO

- **Grading workflows and entity definitions are org seed data**, not in the repo. This doc
  describes them by the names the platform code resolves ("Quiz: Grade", "Scenario: Grade &
  Certify") and by observed behaviour in the Corporate Training org; the exact node graphs are
  not verifiable from source here.
- **The self-enroll workflow** is likewise org-defined. The catalog wires an Enroll passing
  `course_id` + `learner_email`; idempotency and any `module_progress` seeding are properties
  of that org workflow, not platform code.
- **The `learner` entity's identity field** is assumed to be `email` (the default
  `IDENTITY_FIELD_SLUG`); an org could configure a different `field_slug`.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
