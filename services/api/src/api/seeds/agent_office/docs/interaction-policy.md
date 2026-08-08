# Interaction & Review Policy

> This is the spec for how role-agents are classified, what each may do, how they
> ask each other for things, and how work is reviewed before it lands. It extends
> [charter.md](./charter.md) (org chart + the one human-gating test) and
> [permission-policy.md](./permission-policy.md) (the global tool policy).
>
> **Status:** implemented. Items are tagged **[code]** (enforced by the daemon)
> or **[persona]** (a behavioral rule in the agent's prompt). See §8 for build-out status.

## Background: the problem this solves

The team is a roster of autonomous role-agents that plan, build, and review software
with the human only approving direction. Running it surfaced four concrete failures —
all symptoms of missing governance:

- **A coordinator did the work itself.** The Technical Project Manager — whose job is to
  *plan and delegate* — was editing and committing product code directly (`git add`,
  `git commit`). Its persona says "never write product code," but persona text is advice,
  not a wall, and nothing at the tool layer stopped it.
- **Agents couldn't reach the right colleague.** The TPM recognized the lint-enforcer
  needed to run but had no way to invoke it: most agents had **no supervisor at all**
  (the roster was nearly flat), and the only hand-off tool — `delegate_task` — is
  restricted to *direct reports*. There was no channel for one agent to ask another
  across the org.
- **Work could skip review.** With a single hand-off channel and no notion of *who must
  review this*, a developer could be handed a hard-core change with no guarantee the
  right senior reviewer ever saw it.
- **Status didn't tell the truth.** Reports trusted GitHub's PR label, but "changes
  requested" is *sticky* — it stays even after the developer pushes fixes, until a
  reviewer re-reviews. So reports said "developer still owes work" when the ball was
  actually in the reviewer's court.

The root cause behind all four: **policy lived only in prose**, the **hierarchy was
incomplete**, and **one overloaded channel** conflated a cheap "take a look at this"
with a binding "go build this." This document fixes that by making the rules
**structural and enforced**:

| Symptom | Root cause | Fix (section) |
|---|---|---|
| TPM edits/commits code directly | persona is advice, not enforced | `kind` + tool gating — a coordinator *cannot* edit/commit (§2–3) |
| Can't invoke a needed agent (e.g. lint) | flat roster; only direct-report hand-off | real hierarchy (§1) + cross-tree **soft asks** (§4) |
| Cheap reviews treated like work orders | one channel for everything | **soft** (`consult_peer`) vs **hard** (`delegate_task`) asks (§4) |
| Hard-core change ships unreviewed | no required reviewer | SA ⇄ PE peer review + the PE's **review board** (PE + QA + peer engineer), pre-commit scope check (§5) |
| Status misreports who owes the next action | trusts GitHub's sticky label | TPM reads PR comments/commits (§6) |

## How it's solved

Four moves, each turning a prose guideline into something the system *guarantees*:

1. **Classify every agent and enforce it at the tool layer (`kind`).** Each agent is
   `implementation`, `advisory`, or `coordinator`, and the daemon's permission gate reads
   that kind on every tool call. A **coordinator** is *physically blocked* from editing
   files or running mutating git — if the TPM tries to `git commit`, the call is denied
   and it must `delegate_task` instead. **advisory** agents may write only tests/specs/docs,
   never product code. Enforcement no longer depends on the agent choosing to obey its
   persona.
2. **Give the team a real hierarchy.** Every agent now has a supervisor, so the three
   flows that were broken have lines to run on: work **delegates down**, blockers
   **escalate up**, and finished work has a **defined reviewer**. The engineering pod sits
   under a new principal-engineer; the review/audit roles under the solution-architect;
   both leads under the TPM.
3. **Split asks into soft and hard.** Two purpose-built channels replace the one
   overloaded one:
   - **Hard ask** (`delegate_task`) assigns *implementation work* — only down your own
     chain, and the result is reviewed by the supervisor. A non-supervisor can't hand work
     straight to a developer.
   - **Soft ask** (`consult_peer`) requests *analysis, review, or critique* — from any
     advisory agent, across the org, with no review gate. Now the solution-architect can
     get a devil's-advocate read, or anyone can ask lint/QA to look, without it
     masquerading as a work order.
4. **Make review and reporting trustworthy.** Hard-core changes require sign-off from the
   solution-architect or principal-engineer; SA and PE peer-review each other; before any
   commit a reviewer confirms the change stayed in scope. And the TPM reports status from
   the real PR conversation — "awaiting re-review" vs "developer to address" — instead of
   GitHub's sticky label.

The net effect: the human still approves direction, but the team's own guardrails — not
just good intentions in a prompt — keep coordinators coordinating, work reviewed, and
status honest. The rest of this document is the precise specification of each move.

## Why this matters

Two of these moves deserve to be called out, because they're what turns a rigid
reporting tree into a team that actually produces good work.

**Peer review catches what a single chain can't.** Work reviewed only by the person who
assigned it inherits that person's blind spots. Pairing the solution-architect and
principal-engineer to review *each other*, and requiring a senior sign-off before any
hard-core change ships, means every significant change gets a second, independent set of
eyes from someone with the standing to push back. The pre-commit scope check adds a
cheap, specific question — *"did this change only what it was supposed to?"* — that
catches scope creep and stray edits before they reach a branch, not after. Review stops
being a rubber stamp and becomes a real quality gate.

**Letting agents talk to colleagues they don't supervise is how quality work actually
happens.** In a strict tree, the only way to get input from another part of the org is to
route a request up to a common boss and back down — slow, noisy, and it makes every
question look like a work order. The soft-ask channel (`consult_peer`) removes that
friction: a backend-engineer can ask the database-architect about a schema, the
solution-architect can have the devils-advocate stress-test a design, anyone can pull in
lint or QA for a quick read — *directly, immediately, with no manager brokering it.*
Because these are **non-binding consultations**, they never bypass the review chain or
hand anyone work; expertise simply flows to where it's needed. The payoff is the best of
both worlds: clear lines of authority for *who owns and reviews the work*, and open lines
of communication for *getting the work right*.

## 1. Org chart (current)

```
HUMAN (product owner)
└── technical-project-manager                 (root — last stop before the human)
    ├── solution-architect                     ←─ peer-reviews ─→ principal-engineer
    │   ├── security-analyst
    │   ├── requirements-auditor
    │   └── compliance-reviewer
    ├── principal-engineer                      (full-stack engineering lead)
    │   ├── frontend-engineer
    │   ├── backend-engineer
    │   ├── database-architect
    │   ├── qa-engineer
    │   ├── lint-enforcer
    │   ├── playwright-runner
    │   ├── mobile-checker
    │   ├── dark-mode-checker
    │   └── bug-finder
    ├── devils-advocate
    ├── repo-janitor
    ├── documentation-training-specialist
    └── personal-secretary
```

## 2. Agent kinds  **[code: `kind` field]**

Every agent has a `kind` that governs what it may change and how others may call it.

| kind | meaning | may mutate? |
|---|---|---|
| **implementation** | builds product code | yes — product code (push/PR/branch/delete still human-gated; work is reviewed) |
| **advisory** | analysis / review / tests | only under **test / spec / docs** paths; never product code; never mutating git |
| **coordinator** | plans & delegates | nothing in the repo — own memory only; must `delegate_task` |

**Classification:**
- **implementation:** principal-engineer, frontend-engineer, backend-engineer, database-architect
- **advisory:** solution-architect, qa-engineer, lint-enforcer, bug-finder, devils-advocate, security-analyst, requirements-auditor, compliance-reviewer, playwright-runner, mobile-checker, dark-mode-checker, documentation-training-specialist
- **coordinator:** technical-project-manager, repo-janitor, personal-secretary

## 3. Tool gating  **[code: `kindGate` in `canUseTool`]**

A role-aware deny layer sits on top of the global policy (which keeps human-gating
push/branch/PR/delete for everyone). The gate runs *after* the own-memory-dir write
allowance, so every agent can still write its own memory notes.

- **coordinator** — DENY `Edit`/`Write`/`NotebookEdit` (except own memory dir) **and all
  Bash except read-only status/inspection**. *Allowed* Bash: read-only `git`
  (status/diff/log/show/branch/rev-parse/fetch/…) and read-only `gh`
  (pr/issue/run view/list/checks/diff), plus `ls`/`cat`/`grep`/`jq` and similar. *Denied*
  Bash: mutating git, **build / test / make / package-manager / script commands**, and any
  file-writing redirect. `mcp__self__*` (coordinate + `delegate_task`), `mcp__linear__*`
  (Linear), and Read/Grep/Glob stay allowed. *A coordinator pulls status and delegates — it
  cannot edit code, commit, or even run the build/test/lint checks; those go to an
  implementation agent via `delegate_task`. (This closes the gap that let the TPM commit
  directly AND run `make fmt-check`/`make lint-check` itself.)*
- **advisory** — DENY product-code edits and mutating git, but ALLOW writes under
  **test / spec / docs** paths (`**/*_test.*`, `**/*.test.*`, `**/*.spec.*`,
  `**/test/**`, `**/tests/**`, `**/spec/**`, `docs/**`). *e.g. the QA engineer edits
  unit-test code; the doc specialist edits `docs/`.*
- **implementation** — unchanged. May edit product code; push/branch/PR/delete remain
  human-gated by the global policy.

## 4. Soft asks vs hard asks

Two distinct channels for one agent to get something from another:

### Hard ask — `delegate_task`  **[code, exists]**
Assign *implementation work* (mutating, "hard-core app changes"). Direct reports only
(`target.supervisor === you`). The report builds it and submits back up via
`request_review`. A non-supervisor cannot hand implementation work to a developer —
it routes through the developer's supervisor, who reviews. *(TPM → principal-engineer
→ developer; never TPM → developer.)*

### Soft ask — `consult_peer`  **[code: new, Phase 2]**
Request *analysis, code review, QA, lint, or critique* — non-mutating, advisory. Any
agent may issue one, **cross-tree**, to any **advisory** target. Async/non-blocking
(the reply lands back in the asker's session). No review gate. **Cannot** be used to
assign implementation work — that's what `delegate_task` is for.
*e.g. solution-architect → devils-advocate; principal-engineer → security-analyst.*

## 5. Review & sign-off

### Do-it-yourself vs. delegate  **[persona]**
The principal-engineer implements **small or tightly-coupled** changes directly — cheaper,
and it keeps the frontend/backend/data contract coherent in one head. It **delegates to a
specialist** (frontend-engineer, backend-engineer, database-architect) only when the work
is **large or splits cleanly across domains** and can run in parallel. Either way, whatever
ships gets a reviewer who is **not** its author. (`fullstack-dev` was retired — the PE is
itself a full-stack developer, so a separate generalist was redundant.)

### Who reviews whom  **[persona; reports already via `request_review` → supervisor]**
- **solution-architect ⇄ principal-engineer** — peer-review each other's work (they're
  peers under the TPM, so this uses the soft-ask channel, not the supervisor chain).
- **principal-engineer** reviews its reports: frontend-engineer, backend-engineer,
  database-architect, qa-engineer, lint-enforcer, playwright-runner, mobile-checker,
  dark-mode-checker, bug-finder.
- **solution-architect** reviews its reports: security-analyst, requirements-auditor,
  compliance-reviewer.
- Hard-core app changes specifically require sign-off from **solution-architect or
  principal-engineer**.

### The review board — required before "done"  **[persona]**
A feature or bug fix is **not done** when the code is written; it is done when the
**principal engineer signs off after a review board clears.** The implementing engineer
calls `request_review`; it **never self-declares done.** The principal engineer convenes:

1. **Its own review** of the real diff — correct AND in-scope (only what the task and role allow).
2. **QA (`qa-engineer`) — mandatory and specific:** confirm the spec's **edge cases and
   failure modes are actually exercised**, and that **appropriate unit *and* e2e (Playwright)
   tests are included.** QA returns PASS/FAIL with the precise gaps and may write/extend the
   unit/e2e tests itself (it can edit test files); a product-code fix is escalated back to
   the engineer, not made by QA.
3. **The relevant peer engineer** (frontend/backend/database) whose domain the change
   touches — convened by the PE via `delegate_task` (peer engineers are implementation kind,
   so they're reached down-chain, not via the advisory soft-ask channel).
4. **solution-architect** via `consult_peer` for architecture-level calls.

Engineers are encouraged to **consult QA early** (`consult_peer`, since QA is advisory)
*before* `request_review`, so test gaps surface sooner. QA's review covers **edge cases AND
negative testing** (invalid/malformed input, unauthorized access, boundary violations,
fail-safe error handling), not just the happy path.

**Enforced — not just persona [code].** The daemon blocks an `implementation`-kind agent's
`request_review` from reaching sign-off until a real **qa-engineer** review (or a
human-approved exception) is recorded on the work order; a missing review auto-routes to
qa-engineer. So an engineer cannot skip QA by reporting "done" — the path the
CORE-1146 fix took before this gate existed.

**Exception — human-approved only.** There is **no** automatic infra carve-out. If QA
genuinely doesn't apply (a pure CI / Nix / build-config / tooling change with no product
code or behavior change), the engineer calls **`request_qa_exception`** with a
justification; a **human** must approve it (it surfaces as an approval, recorded on the work
order). Neither the engineer nor the TPM can self-certify the skip.

### Pre-commit / pre-push scope review  **[persona now; optional stateful [code] gate later]**
Before any commit or push, a peer/reviewer reviews the diff and confirms the agent
**only changed what its role is allowed to change** (in-scope, no overreach). Push,
branch creation, PR create/merge, and file deletion remain human-only regardless.

## 6. Status reporting — the TPM  **[persona]**

The TPM is a coordinator: it has **read-only** `git`/`gh` plus Linear, so it can observe
everything and report without being able to mutate anything. To be an *educated* manager it
follows a fixed discipline rather than eyeballing labels.

**Canonical status model.** Drive every PR through one stage vocabulary, used everywhere:
`drafting → in review → changes-requested → awaiting re-review → awaiting human merge → merged`.
No ad-hoc labels.

**Derive status from evidence, never from GitHub's sticky label.** GitHub keeps a PR marked
**"changes requested"** even after the developer pushes fixes — it only clears when a
reviewer re-reviews. For each PR the TPM reads the **review threads + comments**
(`gh pr view <n> --json reviews,comments`), the **CI checks** (`gh pr checks <n>`), and the
**commit timeline** vs. the last review (`gh pr view <n> --json commits,reviews`):
- Commits pushed **after** the last "changes requested" review → ball is in the
  **reviewer's** court: **"awaiting re-review"**, not "developer still owes work".
- **No** new commits since the review → ball is in the **developer's** court:
  **"changes requested — developer to address"**.
- Approved / merged / draft / CI-failing: say so explicitly.

**Sweep all open PRs every cycle, read-only.** `gh pr list --state open`, then
`gh pr view` / `gh pr checks` / read commits & comments per PR. **Cross-reference each PR to
its Linear issue/milestone** so the rollup is by *deliverable*, not by branch.

**Maintain a living digest** in the TPM's memory — stage, who-owes-next, CI state, and idle
time per PR — refreshed each sweep, so it can report instantly without re-deriving.

**Report in a fixed format:** **Recently shipped** (merged PRs + notable commits since last
report) · **In flight** (open PRs grouped by stage) · **Blocked + on whom** · **Risks**.
Track cycle time and WIP; flag stalls (a PR idle N days, or parked on a human merge gate).

Tell the full story — *who owes the next action and why* — never just the GitHub label.

## 7. Human-only actions (unchanged)

Deleting files, `git push`, creating branches, and creating/merging PRs always require
the human and are enforced in the global policy — no agent, not even the TPM, can grant
them. See [permission-policy.md](./permission-policy.md).

## 8. Build-out status

- [x] Org tree + specialist engineers — frontend/backend/database under principal-engineer; `fullstack-dev` retired (DB + `agents.example.json`)
- [x] `kind` field — schema/migration, `normalizeAgent`, types, roster/UI read; all agents classified **[code]**
- [x] `kindGate(kind, tool, input)` pure function + tests; wired into `canUseTool` **[code]**
- [x] `consult_peer` soft-ask channel — tool, types, web-channel, `buildPeerRouter`, `pickPeerTarget` + tests **[code]**
- [x] Review-board + pre-commit scope-review in the personas — PE convenes; QA test-gate (edge cases + negative testing + unit/e2e); human-approved exceptions only **[persona]**
- [x] TPM status-reporting discipline — canonical model, derive-from-evidence, sweep + Linear cross-ref + living digest **[persona]**
- [x] Craft best-practice section added to every role's persona **[persona]**
- [x] Enforced QA gate — an `implementation` agent's `request_review` is blocked until a real qa-engineer review (or a human-approved `request_qa_exception`) is recorded on the work order; a missing review auto-routes to qa-engineer **[code]**
