# Build Guides

Step-by-step recipes for building complete business applications on top of the Red Arch
Knowledge Management Platform (KM2). KM2 ships no hard-coded "ticketing module" or "HR
module" — instead you compose generic primitives (custom entities, relationships, forms,
views/dashboards, workflows, reports, RBAC, and RAG knowledge) into whatever vertical app
you need. These guides show how.

## How to build

Everything in these guides can be created three ways — pick whichever you prefer:

1. **In-app builders** — the entity, form, view, and workflow designers in the UI.
2. **The in-app assistant agent** — describe what you want in plain language and let it
   author the entities/forms/views/workflows for you.
3. **Programmatic tools** — the [`km2-mcp`](../MCP_AND_INTEGRATIONS.md) tools / agent tools
   (`km2_create_entity`, `km2_add_entity_field`, `km2_create_entity_relationship`,
   `km2_create_form`, `km2_create_view`, `km2_create_workflow`, `km2_create_report`, …).

The guides describe the **design** (entities, fields, workflows) in platform terms rather
than click-by-click, so they stay valid across UI changes.

## Available guides

| Guide | Build | Reference org |
|-------|-------|---------------|
| [BUILD_TICKETING.md](BUILD_TICKETING.md) | A support / help-desk ticketing system — queues, SLAs, assignment, escalation, agent replies, and a support dashboard. | Fresh recipe |
| [BUILD_HRMS.md](BUILD_HRMS.md) | A Human Resource Management System — employee directory, onboarding/offboarding automation, performance-review cycles, and HR dashboards. | "Human Resource Management" demo org |
| [BUILD_LMS.md](BUILD_LMS.md) | A Learning Management System — course catalog, enrollment, module player, server-graded quizzes, LLM-graded scenarios, and certificates. | "Corporate Training" demo org |

## Primitives these guides build on

Before (or while) following a guide, skim the reference docs for the primitives it uses:

- [FORMS_AND_VIEWS.md](../FORMS_AND_VIEWS.md) — entities, records, forms, views, dashboards,
  `record_list`, learner/requester-bound `@me` filters.
- [WORKFLOW_ENGINE.md](../WORKFLOW_ENGINE.md) — triggers, actions (email, HTTP, LLM grading,
  knowledge search), scheduling, and webhooks.
- [RBAC.md](../RBAC.md) — roles, access masks, and per-entity/field access control.
- [KNOWLEDGE_ENGINE.md](../KNOWLEDGE_ENGINE.md) — RAG over documents for in-app assistance.
- [AGENT_ORG.md](../AGENT_ORG.md) — AI agents, including course auto-generation.

Want a guide for an app not listed here (CRM, project tracker, inventory, an autonomous
company)? The same recipe shape applies — model the entities, compose the views, automate
with workflows, and lock it down with RBAC.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
