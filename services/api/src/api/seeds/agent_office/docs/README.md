# Self-Organizing Agent Org

A team of agents that plan, design, build, and test software with minimal human supervision — built on the `agent-control` daemon. The human approves plans, decides anything that changes the product's feel, and is the only one who can push, branch, open/merge PRs, or delete files.

## Read these (all role-agents do)

- [charter.md](./charter.md) — org chart, roles, reporting lines, and the UX-change test that decides when work goes to the human.
- [interaction-policy.md](./interaction-policy.md) — how agents are classified, what each may do, soft vs hard asks, and the review board.
- [escalation-protocol.md](./escalation-protocol.md) — how to `escalate` to your supervisor and how to `resolve_escalation` when you're the supervisor.
- [permission-policy.md](./permission-policy.md) — the hard, machine-enforced human-only gates (delete / push / branch / PR).
- [work-order-template.md](./work-order-template.md) — the Work Order structure.
- [definition-of-done.md](./definition-of-done.md) — what "done" means per role before review.
- [reporting.md](./reporting.md) — activity-log + summary-report formats, and the learning loop.
- [linear-workflow.md](./linear-workflow.md) — Linear conventions (never mark Done programmatically).

## Background

- [DESIGN.md](./DESIGN.md) — the full system/process design and the rationale.
- [orchestration-enhancements.md](./orchestration-enhancements.md) — retrospective of recent changes: the core problems and how each was solved.
- Work Order records live in [../work-orders/](../work-orders/).

## The roles

| Role | Slug | Kind | Supervisor | Job |
|------|------|------|-----------|-----|
| Technical Project Manager | `technical-project-manager` | coordinator | (human) | Standup, Work Orders, assignments, PR-status tracking, reporting |
| Solution Architect | `solution-architect` | advisory | `technical-project-manager` | Design every feature; supervise the review/audit roles; ⇄ peer-review the principal engineer |
| Principal Engineer | `principal-engineer` | implementation | `technical-project-manager` | Full-stack engineering lead; implement or delegate; convene the review board |
| Frontend Engineer | `frontend-engineer` | implementation | `principal-engineer` | Build UI / product code, test-first |
| Backend Engineer | `backend-engineer` | implementation | `principal-engineer` | Build server / API / service code, test-first |
| Database Architect | `database-architect` | implementation | `principal-engineer` | Schemas, migrations, query performance |
| QA Engineer | `qa-engineer` | advisory | `principal-engineer` | Test gate: edge cases + appropriate unit/e2e tests |
| Lint Enforcer | `lint-enforcer` | advisory | `principal-engineer` | Static analysis / style (findings only) |
| Playwright Runner | `playwright-runner` | advisory | `principal-engineer` | Run e2e suites (findings only) |
| Mobile Checker | `mobile-checker` | advisory | `principal-engineer` | Mobile / responsive verification |
| Dark-Mode Checker | `dark-mode-checker` | advisory | `principal-engineer` | Dark-mode / theming verification |
| Bug Finder | `bug-finder` | advisory | `principal-engineer` | Defect hunting (findings only) |
| Security Analyst | `security-analyst` | advisory | `solution-architect` | Security review |
| Requirements Auditor | `requirements-auditor` | advisory | `solution-architect` | Requirements traceability |
| Compliance Reviewer | `compliance-reviewer` | advisory | `solution-architect` | Regulatory / compliance review |
| Devil's Advocate | `devils-advocate` | advisory | `technical-project-manager` | Adversarial critique |
| Repo Janitor | `repo-janitor` | coordinator | `technical-project-manager` | Repo hygiene / dead-code cleanup |
| Documentation & Training | `documentation-training-specialist` | advisory | `technical-project-manager` | Docs & enablement |
| Personal Secretary | `personal-secretary` | coordinator | `technical-project-manager` | Comms triage, scheduling, follow-through |
