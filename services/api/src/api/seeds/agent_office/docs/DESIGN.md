# Self-Organizing Agent Org — System & Process Design

**Status:** Draft for human approval
**Owner:** Human (product owner) + Technical Project Manager (TPM) agent
**Builds on:** `agent-control` daemon (role-agents, scheduler, approval bridge, policy, Linear sync). See [architecture.md](../architecture.md).

> **Note (current state):** this is the original design rationale and keeps its first-cut
> examples. The org has since evolved — the engineering pod now sits under a
> **principal-engineer** with **frontend / backend / database** specialists (the single
> `fullstack-dev` was retired as redundant with the PE), and a **review board**
> (principal engineer + QA test-gate + the relevant peer engineer) gates "done". For the
> authoritative, current roster and policy see [README.md](./README.md),
> [charter.md](./charter.md), and [interaction-policy.md](./interaction-policy.md).

---

## 1. Goal

A team of agents that **self-organize** to deliver software with minimal human supervision. A **Technical Project Manager (TPM)** plans and assigns; a **Solution Architect (SA)** designs every piece of work before it's built; **engineers** implement off Linear tasks + design specs; a **QA engineer** validates behavior with Playwright. The team writes down what it learns so it gets better over time.

The human stays in the loop only for: **(a) plan approval, (b) anything that substantially changes UX/workflow/layout/behavior, (c) deleting files from disk, (d) any git remote action** (push, branch creation, PR create/merge). Everything else is delegated down the org.

---

## 2. Org chart & reporting lines

```
                         ┌──────────────┐
                         │    HUMAN     │  product owner — final approver
                         └──────┬───────┘
                                │ approves plans, UX changes, all git remote ops, deletes
                         ┌──────┴───────┐
                         │     TPM      │  📋 plans, assigns, sizes team, runs standup,
                         │  (planner)   │     owns Master Work Order + reporting
                         └──────┬───────┘
                                │ escalation terminus before human
                         ┌──────┴───────┐
                         │  Solution    │  🏛  designs every feature before build,
                         │  Architect   │     technical supervisor of engineers
                         └──────┬───────┘
              ┌─────────────────┼──────────────────┐
       ┌──────┴──────┐   ┌──────┴──────┐    ┌──────┴──────┐
       │ Full-Stack  │   │     QA       │   │  (optional   │
       │  Developer  │   │  Engineer    │   │  specialists)│
       │   💻        │   │   🧪 PW tests │   │  FE/BE/DB    │
       └─────────────┘   └──────────────┘   └─────────────┘

   Cross-cutting reviewers (invoked by the review gate, not in the chain):
   bug-finder · code-reviewer · security-reviewer · requirements-auditor · doc-updater
```

**Escalation chain:** Engineer/QA → Solution Architect → TPM → Human.
Each link only passes upward what it cannot resolve at its own level.

---

## 3. Roles (role-agents)

Each role is a first-class `agent-control` role-agent with a **persona** (behavior/control file) and a **memory dir** (history that grows). Existing global agents are reused where they fit.

| Role | Slug | Based on | Charter (one line) |
|------|------|----------|--------------------|
| Technical Project Manager | `tpm` | `planner` | Runs standup, drafts work orders, assigns work, sizes the team, owns reporting, last stop before the human. |
| Solution Architect | `solution-architect` | spice-rack `solution-architect` | Designs every feature before it's built; technical supervisor + first escalation target. |
| Full-Stack Developer | `fullstack-dev` | new | Implements Linear tasks against the SA's spec, TDD-first. |
| QA Engineer | `qa-engineer` | `e2e-runner` | Writes Playwright tests that validate documented behavior. |
| *(optional)* Frontend / Backend / DB | `frontend-dev` / `backend-dev` / `db-dev` | new + language reviewers | Specialized implementers when a task is clearly single-surface. |
| Reviewers (gate only) | `bug-finder`, `code-reviewer`, `security-reviewer`, `requirements-auditor`, `doc-updater` | existing | Independent role-shift review before any commit. |

Roster lives in `agents.json` (seed) and is editable in the web **Agents** tab. Each role's `supervisor` is recorded in its persona + the org charter.

---

## 4. The Work Order — the unit of work

A **Work Order (WO)** is the master container for a day's (or initiative's) work. It is the human-facing plan, the standup record, the decision log, and the final report — all in one markdown file, mirrored to Linear for task tracking.

### 4.1 Canonical home

- **Source of truth:** `docs/work-orders/WO-YYYY-MM-DD-<slug>.md` (repo markdown — matches "all info in md files").
- **Execution mirror:** a Linear **Project** named `WO-YYYY-MM-DD-<slug>`; each feature/task is a Linear **issue** under it; SA design specs are Linear **documents** + repo ADRs.
- **Two-way link:** the WO md references the Linear project + issue IDs; the Linear project description references the WO md path.

### 4.2 Work Order structure (template)

```
# WO-2026-06-08-billing-wallet

## 1. Objective            — what & why, tied to human goals / backlog
## 2. Plan                 — features, sequencing, dependencies, risks
## 3. Team & sizing        — roles needed, how many agents, est. effort
## 4. Task breakdown       — table: task → role → Linear ID → acceptance criteria
## 5. Standup notes        — daily team notes, what each role reported, blockers
## 6. Discussion           — design debates, options weighed (SA-led)
## 7. Decisions for human  — open questions requiring approval (the human reads THIS first)
## 8. Activity log         — per-agent itemized record of work done (appended live)
## 9. Summary report       — TPM-compiled: per-agent summary + outcomes + open items
```

Sections 5–7 are written **before** the human approves (the standup output). Sections 8–9 fill in **during/after** execution.

---

## 5. Lifecycle (daily loop)

```
  [cron 08:00]                                                          [human]
      │                                                                    │
      ▼                                                                    │
  1. STANDUP (TPM) ── reviews backlog + open WOs + human goals             │
      │              drafts WO §1–§7 (plan, team, standup notes,           │
      │              discussion, decisions-for-human)                      │
      ▼                                                                    │
  2. PRESENT ───────────────────────────────────────────────────────────► │
      │                                                approve / edit / reject
      ▼ ◄──────────────────────────────────────────────────────────────── ┘
  3. INITIATE (TPM) ── creates Linear Project + issues, assigns roles
      │
      ▼
  4. DESIGN (SA) ── per feature: design spec / ADR before any code
      │
      ▼
  5. IMPLEMENT (Engineer) ── TDD off the Linear task + SA spec
      │   ├─ hits a judgment call ──► ask Solution Architect (§7 escalation)
      │   └─ writes itemized activity-log entry
      ▼
  6. QA (QA Engineer) ── Playwright tests validating documented behavior
      │
      ▼
  7. REVIEW GATE (finish-feature fan-out) ── bug-finder, security, SA,
      │   tdd-guide, requirements-auditor, code-reviewer, doc-updater
      │   → punch list; CRITICAL/HIGH fixed before any commit
      ▼
  8. REPORT (TPM) ── compiles WO §9 summary; posts Linear status update
      │
      ▼
  (commit / branch / push / PR  ── ALL human-gated, see §6)
```

The TPM's standup is a **scheduled** role-agent run (`0 8 * * 1-5`). Scheduled runs escalate every non-auto-approved tool, so nothing unattended slips past the human.

---

## 6. Permission & escalation model

Two **distinct** kinds of permission. Do not conflate them.

### 6.1 Hard human-gated actions (machine-enforced, no agent can grant)

Enforced by `policy.json` (deny → ask-human), **not** by supervisor judgment. These always stop at the human:

- **Delete files from disk** (`rm`, `git clean`, destructive `mv`, `Bash(rm…)`, etc.)
- **git push** to any remote
- **git branch creation** (`git branch`, `git checkout -b`, `git switch -c`)
- **PR create or merge** (`gh pr create`, `gh pr merge`, `git push` of a PR branch)

> No role-agent — not even the TPM — can approve these. They route to the human via the approval bridge. This is the strongest guardrail and is enforced in code, not in prompts.

### 6.2 Soft / judgment actions (supervisor-adjudicated)

For everything else, an engineer asks its **supervisor** (the SA). The supervisor applies one test:

> **Does this substantially change the user experience, workflow, layout, or behavior of the application?**

- **No** → supervisor grants; work continues. (Logged in the activity log.)
- **Yes** → **work STOPS.** The decision is written to WO **§7 (Decisions for human)** and escalated: SA → TPM → Human. The TPM presents it in the next standup or as an immediate inbox item if blocking.

If the SA cannot answer (out of scope, conflicting requirements, missing spec), it escalates to the TPM. If the TPM cannot decide, it goes to the human. **Nothing material is decided unilaterally at a level that lacks the authority for it.**

### 6.3 What counts as "substantially changes UX/workflow/layout/behavior"

The SA uses this as the trigger list (kept in the org charter, refined over time):

- New or changed user-facing screens, flows, or navigation
- Changing what an existing feature does (behavior change)
- Layout/visual redesigns beyond cosmetic spacing
- New external dependencies or data the user must provide
- Anything touching auth, billing, data retention, or compliance posture
- Removing or disabling existing functionality

### 6.4 Escalation mechanism — **built**

First-class routing was built directly (not deferred). It works as follows:

- The engineer calls the `mcp__self__escalate(reason, context)` tool and **blocks** on a pending-escalation registry promise (sibling to approvals/questions in `WebChannel`).
- The daemon's escalation router (`buildEscalationRouter` in `daemon.ts`) routes it to the engineer's configured `supervisor` role-agent: it **steers** an already-live supervisor session, or **spawns** one (with `bypassCap` so the spawn can't queue behind the blocked engineer's concurrency slot). If the supervisor role doesn't exist, is the engineer's own role (cycle), or fails to start, it **bubbles** straight to the human.
- The supervisor adjudicates with the UX-change test and calls `mcp__self__resolve_escalation(escalationId, decision, note)`: `resolved` unblocks the engineer with the note; `bubble` reuses the human `askQuestion` path and the human's answer flows back to the engineer.
- An authorizer ensures an agent can only settle escalations addressed to its own role; the human (authenticated IPC) and the daemon itself bypass it. Lifecycle edges are handled: a second settle during a bubble is rejected; ending the engineer or supervisor session settles/bubbles the in-flight escalation; an optional `ESCALATION_TIMEOUT_MS` auto-bubbles an unanswered escalation.
- Human-gated classes (§6.1) never touch this channel — they hit the approval bridge directly via policy.

Implemented across `src/types.ts`, `src/web-channel.ts`, `src/self-tools.ts`, `src/session-manager.ts`, `src/daemon.ts`, and the web inbox (`EventsProvider`, `EscalationCard`, `CanvasInbox`, `OfficeFloor`).

---

## 7. Memory model (md files — the team gets better over time)

Every role has two md layers, matching the tool's existing `memoryDir` (CLAUDE.md + memory/*.md):

1. **Behavior / control file** — `roles/<slug>/CLAUDE.md` (the persona): the role's charter, decision rules, escalation triggers, definition of done. *How this role works.* Changing it changes behavior.
2. **Memory / history** — `roles/<slug>/memory/*.md`: one fact per file — lessons learned, "how we do X here," past decisions, recurring pitfalls. *What this role has learned.* Grows over time → compounding competence.

Plus **org-level shared memory** all roles read (`docs/agent-org/`):

- `charter.md` — org chart, roles, reporting lines, the UX-change trigger list
- `escalation-protocol.md` — §6 in operational detail
- `permission-policy.md` — the human-gated list + how it maps to `policy.json`
- `work-order-template.md` — §4.2 template
- `definition-of-done.md` — what "done" means before the review gate
- `reporting.md` — the activity-log + summary-report format

**Learning loop:** at end of each WO, the TPM prompts each role to append durable lessons to its `memory/`. Over time the standup, design, and review get sharper because the history is read back at the start of every session.

---

## 8. Reporting

- **Activity log (WO §8):** each agent, on finishing a task, appends an itemized entry:
  ```
  ### fullstack-dev — CORE-571 (wallet top-up endpoint)
  - Added POST /api/wallet/topup with idempotency key  (app/backend/wallet.go)
  - Wrote 6 unit tests + 1 integration test (all green)
  - Escalated: per-minute rounding rule → SA approved 2-decimal floor
  - Did NOT push/branch/PR (human-gated)
  ```
- **Summary report (WO §9):** the TPM compiles a per-agent summary + detailed itemized list + outcomes + open items + which human-gated actions are queued and waiting. Mirrored to a Linear project **status update**.
- **Audit substrate:** the daemon already records every tool decision (tool, input, allow/deny, actor, timestamp) and full session transcripts — the reports are human-readable rollups of that ground truth.

---

## 9. Linear conventions (adopted from spice-rack `linear-workflow`)

- Every task traces to a Linear issue; pull the issue at task start.
- **Never mark a Linear issue Done programmatically** — agents move to *In Review* + comment; the human closes.
- PR ↔ Linear linked both ways at PR open — but **PR creation itself is human-gated** (§6.1).
- Surface untracked work; don't create tickets without confirmation.

---

## 10. Maps to existing capabilities

| Need | Already exists | To build |
|------|----------------|----------|
| Role-agents w/ persona + memory | ✅ registry + memoryDir | personas for tpm/sa/fullstack/qa |
| Daily standup | ✅ scheduler (cron) | TPM standup schedule + prompt |
| Human approvals | ✅ approval bridge + inbox | — |
| Hard git/delete gates | ✅ policy engine | `policy.json` deny→ask rules (§6.1) |
| Work order ↔ tasks | ✅ Linear Task Map sync | WO md template + Linear project mapping |
| Activity / audit | ✅ decisions table + transcripts | report-compile prompt for TPM |
| Thorough pre-commit review | ✅ spice-rack `finish-feature` | adapt for this repo (TS/Next) |
| Supervisor escalation | ✅ `escalate` / `resolve_escalation` tools + daemon router (built) | — |

---

## 11. Phased rollout

- **Phase 0 — Design (this doc):** approve org, lifecycle, permission model. ✅
- **Phase 1 — Org bootstrap (built):** org docs (§7), the 4 core personas, `policy.json` hard gates (§6.1) with the deny/ask/allow tiers, the TPM standup cron, the first-class `escalate` channel (§6.4), and the adapted `finish-feature`. Run one real WO end-to-end with a human watching every step.
- **Phase 2 — Hardening:** escalation timeout + resume semantics, richer reporting roll-up from the decisions log, a committed test suite (`vitest`).
- **Phase 3 — Trust expansion:** as the memory/history matures and the team proves reliable, widen `autoAllow` for low-risk tools and reduce human touchpoints — never relaxing the §6.1 hard gates.

---

## 12. Decisions made with the human

1. **Escalation mechanism** — built the first-class daemon `escalate` channel (not sub-agent adjudication).
2. **Engineer roster granularity** — single full-stack developer to start.
3. **Work order home** — Linear-primary for the task graph; the WO md file holds the details/history (standup notes, decisions, activity log, report).
4. **Build scope** — design docs + Phase 1 executable artifacts, then a human-watched dry-run.
