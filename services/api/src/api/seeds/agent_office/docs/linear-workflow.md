# Linear Workflow

Linear is the canonical system for the **task graph** — what work exists and its status. The Work Order markdown file is canonical for the **narrative/history** (standup notes, discussion, decisions, reports). Keep both in sync. Adapted from the cobalt team's conventions.

## Structure

- A **Work Order** → a Linear **Project** named `WO-YYYY-MM-DD-<slug>`.
- A **feature** → a Linear **issue** (the parent).
- A **task** → a **sub-issue** of its feature (set `parentId`); concrete acceptance criteria in the description. The owning role is named in the title prefix; the **assignee is always the human**.
- A **design spec** → a Linear **document** on the project, mirrored by a repo ADR when a real architectural decision was made.

## At task start — pull context

If a Linear id appears in the task, branch, or recent commits:
1. `mcp__linear__get_issue` for the ticket.
2. `mcp__linear__list_comments` for recent discussion.
Use the acceptance criteria, prior decisions, and blockers to inform the work. If no id is referenced but the work clearly maps to a ticket, confirm which ticket before doing non-trivial work.

## Status moves

- Move a task to **In Progress** when you start it.
- Move a task to **In Review** when implementation + tests are done and it's heading into the review gate.
- **Never move a ticket to Done programmatically.** Done is the human's call. Leave it *In Review* with a comment; the product owner closes it.

## Linking to code

- Reference the Linear id in commit subjects where one applies: `feat(wallet): prepaid top-up (CORE-571)`.
- **PR creation is human-gated** (see [permission-policy.md](./permission-policy.md)). When the human creates the PR, link it both ways: PR body references the Linear id, and a Linear comment links the PR.
- Branch names don't need to encode the id; if they do, follow the existing `<user>/<slug>` form. **Branch creation is human-gated.**

## Creating tickets

- The **TPM** creates the Linear project + issues for an **approved** Work Order. That's the normal path.
- Otherwise: **don't create tickets without confirmation.** If you finish meaningful work with no ticket, flag it in your activity log (`⚠️ Not tracked in Linear: <one-liner>`) and propose a ticket; wait for the human/TPM.
- Don't create spurious tickets — cycle/project hygiene matters.

### Required fields on every issue you create (`mcp__linear__save_issue`)

- **`assignee: "me"`** — ALWAYS. Every issue the team creates is assigned to the human (`"me"` resolves to the human via the shared Linear login). Role-agents are **not** Linear users — never try to assign an issue to a role name.
- **Owning role in the title** — prefix the title with the role that will do the work, e.g. `[backend-engineer] Wallet top-up endpoint`, `[qa-engineer] Top-up E2E coverage`. That keeps the intended worker visible even though the assignee is the human; restate it in the description.
- **`parentId`** — the task graph is a tree, not a flat list. Create the **feature** issue first, then create each **task** as a sub-issue with `parentId` set to that feature's id (e.g. `LIN-123`). Never leave breakdown tasks parentless.
- **`project`** — the Work Order's Linear project, so everything rolls up under `WO-YYYY-MM-DD-<slug>`.
- **`description`** — concrete acceptance criteria the task can be built and tested against.

## Don'ts

- Don't mark Done. Don't merge. Don't create PRs or branches or push. (All human-gated.)
- Don't paste long PR descriptions into Linear comments — a one-line summary + link; the PR body / WO md is the source of truth.
- Don't poll Linear needlessly — pull a ticket once at task start.
