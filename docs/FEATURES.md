# Features Overview

A catalog of what the Red Arch Knowledge Management Platform v2 (KM2) does, grouped
by capability area. Each entry is a short summary and a link to the deep doc that
owns the detail — this file is the **map to the rest of `docs/`**, not a duplicate of
it. For engineers and technical evaluators surveying the platform.

## Table of Contents

- [At a glance](#at-a-glance)
- [Knowledge & RAG](#knowledge--rag)
- [Data modeling & UI](#data-modeling--ui)
- [Automation](#automation)
- [AI agents](#ai-agents)
- [Security & tenancy](#security--tenancy)
- [Enterprise & admin](#enterprise--admin)
- [Integrations](#integrations)
- [Reference applications](#reference-applications)
- [Feature matrix by role](#feature-matrix-by-role)
- [Integration points](#integration-points)
- [Documentation map](#documentation-map)

## At a glance

KM2 is a multi-tenant platform that pairs a RAG knowledge brain (document ingest,
vector search, knowledge graph, cited chat) with a no-code data + UI layer (custom
entities, forms, views, dashboards, reports), a BPMN workflow engine, an AI agent
org, and enterprise controls (fine-grained RBAC, org API keys, release promotion,
site admin). It runs as four authoritative Python/TypeScript services — `api`
(port 8000), `brain_api` (port 8020), `worker` (Celery), and `ui` (Next.js, port
3000). A Go rewrite of the three backend services is in progress and not yet
authoritative; see [ARCHITECTURE.md](ARCHITECTURE.md) §"Go Migration Status".

## Knowledge & RAG

Owned by the `brain_api` service (port 8020) and the `worker`. Deep doc:
[KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md); data flows in
[ARCHITECTURE.md](ARCHITECTURE.md) §5.

- **Document management.** Upload documents into a nested folder hierarchy with tags
  and org-defined custom attributes; the `worker` ingests each document
  asynchronously (chunk → embed → summarize → graph) and reports processing status.
  See [ARCHITECTURE.md](ARCHITECTURE.md) §5 and the `documents`/`folders` routers.
- **Semantic vector search.** Chunks are embedded and stored in **Qdrant** (one
  collection scope per tenant); similarity search returns top-k passages, filtered by
  the caller's access keys before any LLM sees them. See
  [ARCHITECTURE.md](ARCHITECTURE.md) §5 and [RBAC.md](RBAC.md).
- **Knowledge graph.** Entities, relationships, and events are extracted into
  **Neo4j** for relationship-style queries, toggleable per org and per document.
  KNOWLEDGE_ENGINE.md documents the in-build reified-claim fact-store direction.
- **RAG chat with passage-level citations.** Natural-language Q&A assembles context
  from vector + graph retrieval, streams an LLM answer over SSE, and cites the exact
  source passages inline. Chat context can be scoped by folder, tag, or document.
- **Multi-level summarization.** Chunk / section / document summaries are generated
  at ingest for previews and richer RAG context.

## Data modeling & UI

The no-code layer: define your own data shapes, then design the screens that read and
write them.

- **Custom entities & records.** Org admins define entities (fields, types,
  relationships) and store records against them — no schema migration required. Backed
  by the `entity_definitions` and `entity_records` routers. See
  [ARCHITECTURE.md](ARCHITECTURE.md) §1 and [DATABASE.md](DATABASE.md).
- **Flexible forms & views.** One recursive element-tree schema and a single renderer
  drive every surface: data-entry forms (internal or via public token links),
  dashboards, `record_list` tables (with per-row links and workflow inputs), chat
  panels, and slide/video decks. Buttons wire to workflows. Deep doc:
  [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md).
- **Reports & aggregations.** Named GROUP BY queries over any entity (metrics
  `count/count_distinct/sum/avg/min/max`, date bucketing, filter/HAVING/order/limit),
  saved as reports with a chart/KPI/table visualization and embeddable as `report`
  elements on any dashboard. Runs server-side under tenant RLS via the `reports`
  router (`POST /api/entities/{slug}/aggregate`). Visualization detail in
  [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md).

## Automation

- **Workflow engine (BPMN 2.0.2).** A durable, token-based execution engine with a
  React-Flow designer, an SSE live-run overlay, manual/inbound/change triggers, and a
  library of actions (record CRUD, `knowledge_search` RAG, LLM `summarize`/`respond`/
  `grade`, `send_email`, `http_request`, `send_form`, branching). Runs are swept from
  a durable outbox by the `worker` beat. Deep doc:
  [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).

## AI agents

- **Agent org (multi-agent company).** A first-class, multi-tenant org chart of AI
  agents that plan, delegate, research, and act on the org's own data — governed by a
  `deny > ask > allow` authority engine that funnels every outbound action to a single
  human approval inbox. It is the substrate for the autonomous-company blueprint.
  Deep doc: [AGENT_ORG.md](AGENT_ORG.md).
- **Model tiers & autonomy governance.** Per-org autonomy policy (migration 033) and
  admin bypass policies (migration 034) bound what agents may do without approval; the
  agent console and approval inbox surface pending actions. See
  [AGENT_ORG.md](AGENT_ORG.md) and [SITE_ADMIN.md](SITE_ADMIN.md).
- **In-app assistant agent.** A chat-driven assistant (the `chat`/`agent` routers)
  answers questions from the knowledge base and, for admins, authors platform
  objects — documents, folders, forms, views, and full workflow lifecycles — using a
  progressive-disclosure, caller-scoped toolset. The same toolset backs the workflow
  designer's AI assistance; see [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).

## Security & tenancy

- **Fine-grained RBAC.** 32-bit access masks control View / Contribute / Manage across
  four permission dimensions (roles, groups, regions, departments); folder permissions
  inherit or override per level, and RAG results are filtered by entitlement before the
  LLM sees them. Deep doc: [RBAC.md](RBAC.md).
- **Entity & field access control.** Per-entity write policy and per-field read policy
  (`server_only` / `workflow_only`) make records tamper-proof — e.g. a quiz answer key
  is unreadable to learners and a certificate is writable only by a workflow. Enforced
  by a `privileged` flag on the entity repository (migration 039). See
  [RBAC.md](RBAC.md).
- **Multi-tenancy / org isolation.** Every request sets `SET LOCAL ROLE app_user` so
  PostgreSQL Row-Level Security enforces tenant boundaries; Qdrant collections and
  Neo4j subgraphs are scoped per tenant. See [ARCHITECTURE.md](ARCHITECTURE.md) §6 and
  [DATABASE.md](DATABASE.md).
- **Authentication (Clerk).** Clerk (cloud OIDC) issues JWTs (template `redarch-km`)
  verified per request; profiles auto-provision on first login and users can switch org
  context. Org API keys (`km2_…`, SHA-256 hashed, migration 028) authenticate the
  public API. Deep doc: [AUTHENTICATION.md](AUTHENTICATION.md).

## Enterprise & admin

- **Enterprise API.** A versioned, org-API-key-authenticated `/api/v1` surface
  (entities, records, reports, workflows, search, knowledge, config) with scopes and
  per-key + per-IP Redis rate limits, alongside the Clerk-authenticated main API. Deep
  docs: [API.md](API.md) §"Enterprise API Authentication", [AUTHENTICATION.md](AUTHENTICATION.md).
- **Change management (release promotion) + import/export.** Cut a frozen release of an
  org's configuration, move it through a review/approval gate, promote it to another org
  or a remote KM2 instance, and roll it back — plus lineage-aware config diffing and a
  full org import/export bundle. Migrations 037–038; deep doc:
  [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).
- **Site admin console.** Instance-wide operator surface for managing every
  organization, user account, deployment log, and platform setting — distinct from the
  per-org admin area. Deep doc: [SITE_ADMIN.md](SITE_ADMIN.md).

## Integrations

Deep doc: [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md).

- **MCP developer tool.** `tools/km2-mcp` drives the live API via a Clerk browser
  session, exposing KM2 objects as MCP tools for agentic automation.
- **Connect (outbound MCP OAuth).** An OAuth flow (migration 032) lets agents reach
  external MCP servers on behalf of an org.
- **Inbound webhooks.** HMAC-signed inbound endpoints (`X-KM2-Signature`) start
  workflow runs inline; payloads are templated into workflow variables.
- **Outbound connections.** SSRF-guarded HTTP connections let workflows call
  third-party APIs; the same transport carries cross-instance config promotion.

## Reference applications

These are not bespoke subsystems — each is an ordinary tenant assembled from the
generic primitives above (entities, forms/views, workflows, access control, RAG).

- **LMS (Learning Management).** The "Corporate Training" org: courses, modules,
  server-graded quizzes, LLM-graded scenarios, certificates, a generic course player
  and self-serve catalog, and an admin course generator. Deep doc: [LMS.md](LMS.md);
  build guide: [guides/BUILD_LMS.md](guides/BUILD_LMS.md).
- **HRMS.** The "Human Resource Management" org: pre-hire → onboarding →
  offboarding and review workflows, an interactive HR ops console, and embedded
  reports. Built entirely from platform primitives. Build guide:
  [guides/BUILD_HRMS.md](guides/BUILD_HRMS.md).
- **Autonomous company.** A full traditional org staffed by agents and run by one
  human, using the agent org as its substrate. See [AGENT_ORG.md](AGENT_ORG.md).
- **Ticketing / work orders.** Record-driven request tracking via the `work_orders`
  router and custom entities. Build guide:
  [guides/BUILD_TICKETING.md](guides/BUILD_TICKETING.md).

Step-by-step recipes for building these (and similar apps) live in
[guides/](guides/README.md).

## Feature matrix by role

| Feature | Member | Org Admin | Site Admin |
|---|---|---|---|
| Chat with documents | Per folder permissions | Full org access | All orgs |
| Upload / manage documents | Contribute / Manage permission | Full org access | Full access |
| Manage folders & RBAC | Manage permission | Full org access | Full access |
| Custom entities, forms, views, reports | View/use per permission | Author | Author (any org) |
| Run workflows | Per workflow `run_permission` | Full org access | Full access |
| Agent org & autonomy policy | Use per policy | Configure | Configure (any org) |
| In-app authoring assistant | Q&A only | Authoring tools | Authoring tools |
| Org API keys | - | Issue / revoke | Full access |
| Change management / promotion | - | Cut & promote releases | All orgs |
| Manage users | - | Org members | All users |
| Create organizations | - | - | Yes |
| Instance / system configuration | - | - | Yes |

## Integration points

| Integration | Purpose |
|---|---|
| **OpenAI** | Chat completions, grading, and embeddings (per-org API key) |
| **Qdrant** | Vector similarity search (per-tenant collections) |
| **Neo4j** | Knowledge graph storage (per-tenant subgraph) |
| **PostgreSQL 18** | Primary data store with Row-Level Security |
| **Redis** | Rate limits + Celery broker |
| **MinIO** | Object storage (dev/prod) |
| **Clerk** | Identity provider (OIDC), JWT template `redarch-km` |
| **MCP servers** | Inbound (`km2-mcp`) and outbound (Connect OAuth) agent tooling |

## Documentation map

| Area | Doc |
|---|---|
| System design, services, tenancy, Go migration | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Product requirements | [REQUIREMENTS.md](REQUIREMENTS.md) |
| Knowledge brain / RAG / fact engine | [KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md) |
| RBAC + entity/field access control | [RBAC.md](RBAC.md) |
| Forms, views, dashboards, reports | [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md) |
| Workflow engine | [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) |
| Agent org & autonomy | [AGENT_ORG.md](AGENT_ORG.md) |
| Change management / release promotion | [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md) |
| Main + enterprise API reference | [API.md](API.md) |
| Authentication (Clerk, org API keys) | [AUTHENTICATION.md](AUTHENTICATION.md) |
| MCP & integrations | [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md) |
| Site admin console | [SITE_ADMIN.md](SITE_ADMIN.md) |
| LMS reference application | [LMS.md](LMS.md) |
| Build guides (ticketing, HRMS, LMS) | [guides/README.md](guides/README.md) |
| Database & schema | [DATABASE.md](DATABASE.md) |
| Deployment | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Local development | [DEVELOPMENT.md](DEVELOPMENT.md) |
| Project overview | [README](../README.md) |

## Known gaps / TODO

- Only LMS has a dedicated deep doc ([LMS.md](LMS.md)); HRMS and ticketing are covered
  by their build guides in [guides/](guides/README.md) and grounded in the
  `work_orders` router and the seeded orgs.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
