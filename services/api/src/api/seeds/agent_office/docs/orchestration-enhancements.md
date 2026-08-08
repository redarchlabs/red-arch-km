# Agent Orchestration — Recent Enhancements

> A retrospective of the recent changes to the agent-orchestration system: the concrete
> problem each one solved, the root cause, and how it was fixed. For the authoritative,
> current specification see [interaction-policy.md](./interaction-policy.md),
> [charter.md](./charter.md), and the roster in [README.md](./README.md).

## Context

The system is a roster of autonomous role-agents (a daemon runs each as its own Claude
session) that plan, build, and review software, with the human approving direction and
holding the only keys to the git remote. Running it at scale surfaced a cluster of
governance, routing, review, and reliability gaps. The enhancements below turn rules that
**used to live only in prose** into things the system **structurally guarantees**, and fix
the reliability bug that was silently stalling agents.

**A note on enforcement.** Each fix is enforced at one of two layers:
- **[code]** — the daemon physically enforces it (a tool call is denied, a turn is injected). Cannot be ignored.
- **[persona]** — a behavioral rule in the agent's prompt. Strong guidance, but advisory.

The theme of this work was **moving the load-bearing rules from [persona] to [code]**.

## At a glance

| # | Problem | Root cause | Fix | Layer |
|---|---------|-----------|-----|-------|
| 1 | A coordinator edited & committed product code | persona said "don't," nothing stopped it | `kind` classification + `kindGate` deny layer | [code] |
| 2 | Agents couldn't reach the right colleague | flat roster; only direct-report hand-off existed | real hierarchy + cross-tree **soft asks** | [code] |
| 3 | "Take a look" and "go build this" used one channel | a single overloaded hand-off tool | `consult_peer` (soft) vs `delegate_task` (hard) | [code] |
| 4 | One generalist dev; unclear when to specialize | a single `fullstack-dev` duplicating the lead | specialist roster + PE do-it-yourself-vs-delegate rule | [persona] |
| 5 | Work could ship unreviewed; QA's role was vague | no required reviewer; "done" was self-declared | the **review board** + QA test-gate + infra carve-out | [persona] |
| 6 | Status reports lied about who owed the next action | trusted GitHub's sticky PR label | evidence-based TPM status discipline | [persona] |
| 7 | The TPM didn't forward Architect → engineering | stale personas from the old 2-tier org | corrected routing personas + per-agent "Team rules" | [persona] |
| 8 | Agents stalled mid-task instead of continuing | checkpoint nudge told the agent to *wait*; no re-drive | reworded checkpoint + a continuation turn | [code] |
| 9 | Personas lacked depth on *how* to do the work | personas described *what*, not *how well* | a craft-best-practices section per role | [persona] |
| 10 | Approving an action took two clicks | a confirm-approve step nobody needed | single-click Approve | [code/UI] |

---

## 1. Coordinators could do the work themselves  **[code]**

**Problem.** The Technical Project Manager — whose job is to *plan and delegate* — was
editing and committing product code directly (`git add`, `git commit`), and even running
build/lint checks itself.

**Root cause.** Its persona said "never write product code," but persona text is advice,
not a wall. Nothing at the tool layer stopped it.

**Solution.** Every agent now has a **`kind`** — `implementation`, `advisory`, or
`coordinator` — and a deny layer (`kindGate`) runs on every tool call:
- **coordinator** — blocked from `Edit`/`Write` (except its own memory) and from all Bash
  except read-only status/inspection (read-only `git`/`gh`, `ls`/`cat`/`grep`). It cannot
  edit code, commit, *or even run build/test/make* — that work goes to an implementation
  agent via `delegate_task`.
- **advisory** — may write only under test/spec/docs paths; never product code; never mutating git.
- **implementation** — builds product code (push/branch/PR/delete stay human-gated).

*Where:* `src/config.ts`, `src/store.ts`, `src/kind-gate.ts`, `src/agent.ts`; spec in
[interaction-policy.md](./interaction-policy.md) §2–3.

## 2. Agents couldn't reach the right colleague  **[code]**

**Problem.** The TPM recognized the lint-enforcer needed to run but had no way to invoke it.

**Root cause.** Most agents had **no supervisor at all** (the roster was nearly flat), and
the only hand-off tool, `delegate_task`, is restricted to *direct reports*. There was no
channel to ask another agent across the org.

**Solution.** Every agent now has a supervisor, giving the three flows lines to run on:
work **delegates down**, blockers **escalate up**, finished work has a **defined reviewer**.
The engineering pod sits under a new **principal-engineer**; the review/audit roles under
the **solution-architect**; both leads under the TPM. On top of that, a cross-tree **soft-ask**
channel (see §3) lets any agent pull in an advisory colleague directly.

*Where:* roster (DB + `agents.example.json`); org chart in [charter.md](./charter.md) and
[interaction-policy.md](./interaction-policy.md) §1.

## 3. Soft asks vs hard asks  **[code]**

**Problem.** A cheap "take a look at this" and a binding "go build this" went through the
same channel, so every question looked like a work order — and a non-supervisor could hand
work straight to a developer with no review.

**Solution.** Two purpose-built channels:
- **Hard ask — `delegate_task`:** assigns *implementation work*, only down your own chain;
  the result returns via `request_review`. *(TPM → principal-engineer → engineer; never TPM → engineer.)*
- **Soft ask — `consult_peer` / `reply_to_peer`:** requests *analysis, review, or critique*
  from any **advisory** agent, across the org, async and non-blocking, with no review gate.
  It **cannot** assign work.

This is what lets the solution-architect get a devil's-advocate read, or any engineer pull
in QA/lint/security, *directly* — expertise flows to where it's needed without a manager
brokering it, while authority over *who owns and reviews* the work stays clear.

*Where:* `src/self-tools.ts`, `src/peer-routing.ts`, `src/web-channel.ts`, `src/daemon.ts`,
`src/types.ts`; spec in [interaction-policy.md](./interaction-policy.md) §4.

## 4. Specialist engineering roster  **[persona]**

**Problem.** Implementation ran through a single `fullstack-dev`, redundant with the
principal-engineer (itself a full-stack developer), and there was no rule for *when* to
split work across specialists.

**Solution.** Retired `fullstack-dev`; implementation now routes to **frontend**,
**backend**, and **database** specialists under the principal-engineer. The PE's operating
rule: **implement small or tightly-coupled work directly** (cheaper, keeps the
frontend/backend/data contract coherent in one head); **delegate to a specialist only when
the work is large or splits cleanly across domains** and can run in parallel. Either way,
whatever ships gets a reviewer who is not its author. Idle specialists cost nothing — the
cost is only paid when work is actually fanned out.

*Where:* roster + personas; rule in [interaction-policy.md](./interaction-policy.md) §5.

## 5. The review board — nothing ships unreviewed  **[persona]**

**Problem.** With a single hand-off channel and no notion of *who must review this*, a
developer could be handed a hard-core change with no guarantee the right reviewer saw it —
and "done" was self-declared. QA's specific role was undefined.

**Solution.** A feature/bug fix is **not done** when the code is written; it is done when
the **principal engineer signs off after a review board clears.** The engineer calls
`request_review` (never self-declares done), and the PE convenes:
1. Its **own review** of the real diff — correct AND in-scope.
2. **QA (`qa-engineer`) — the test gate:** confirm the spec's **edge cases and failure modes
   are actually exercised** and that **appropriate unit *and* e2e (Playwright) tests are
   included.** QA returns PASS/FAIL with the gaps and may write/extend the tests itself; a
   product-code fix is sent back to the engineer.
3. **The relevant peer engineer** (frontend/backend/database) for the domain touched.
4. **solution-architect** for architecture-level calls; SA ⇄ PE also peer-review each other.

**Carve-out:** a pure **CI / Nix / build-config** fix that adds no product code and changes
no behavior **skips QA and the board** — the PE's own review is enough.

*Where:* personas; spec in [interaction-policy.md](./interaction-policy.md) §5 and
[definition-of-done.md](./definition-of-done.md).

## 6. Trustworthy status reporting  **[persona]**

**Problem.** Reports trusted GitHub's PR label, but "changes requested" is **sticky** — it
stays even after the developer pushes fixes, until a reviewer re-reviews. Reports said
"developer still owes work" when the ball was actually in the reviewer's court.

**Solution.** The TPM (a read-only coordinator with `git`/`gh` + Linear) follows a fixed
discipline instead of eyeballing labels:
- **Canonical status model:** `drafting → in review → changes-requested → awaiting re-review → awaiting human merge → merged`.
- **Derive status from evidence:** read CI (`gh pr checks`), review threads, and the commit
  timeline vs. the last review. Commits after a "changes requested" review → **awaiting
  re-review** (reviewer's court), not "developer to address".
- **Sweep all open PRs** read-only each cycle; **cross-reference each to its Linear issue**
  so the rollup is by deliverable; maintain a **living digest**; report in a fixed format
  (*Recently shipped · In flight · Blocked + on whom · Risks*).

*Where:* TPM persona; spec in [interaction-policy.md](./interaction-policy.md) §6.

## 7. Routing personas corrected  **[persona]**

**Problem.** After the org grew, the TPM stopped forwarding the Solution Architect's
requests to the principal-engineer — work bubbled to the human instead of moving along the
chain.

**Root cause.** Personas still described the **old 2-tier org** (TPM → SA → fullstack-dev),
so agents routed to roles that no longer owned the work (e.g. the SA being asked to commit).

**Solution.** Every persona got a standardized **"Team rules"** block derived from its
`kind`, supervisor, and reports — stating exactly what it can do and where to route
(escalate up, delegate down, consult advisory peers). Stale supervisor/ownership references
were corrected across the roster.

*Where:* all personas (DB + `agents.example.json`).

## 8. Agents stalled instead of compacting and continuing  **[code]**

**Problem.** Long-running agents would hit a context-pressure checkpoint, write a summary to
their work-order diary, and then **stop** at "✅ Done — reply here to continue" instead of
shrinking context and carrying on. Auto-compaction appeared not to happen.

**Root cause.** Two reinforcing bugs: (a) the checkpoint message told the agent *"I'll
compact your window, then continue"* — so it **waited** for a signal that never came; and
(b) the manager sent the checkpoint guidance and a best-effort `/compact` (a no-op as plain
text) but **no continuation turn**, so the input loop drained and idled. The real context
shrink is the SDK's own auto-compaction, which fires at the true limit but emits no
"now continue" cue.

**Solution.** Reworded the checkpoint/force messages so the agent **continues immediately**
(its window compacts automatically as it fills), and added a `continueAfterCheckpointMessage`
**re-drive turn** in `enforceCompaction` so the session resumes from its checkpoint instead
of parking. Covered by tests.

*Where:* `src/compaction-policy.ts`, `src/session-manager.ts`,
`src/__tests__/compaction-policy.test.ts`.

## 9. Craft best-practices in every persona  **[persona]**

**Problem.** Personas described *what* a role does but gave little guidance on *how to do it
well*, so output quality leaned entirely on the base model.

**Solution.** Every role gained a **"Craft best practices"** section with concrete,
senior-level, domain-specific guidance — e.g. threat-model-first for security, traceability
matrices for the requirements auditor, resilient-locator/auto-wait discipline for Playwright,
WCAG-AA + design-token checks for dark mode, and the full PR/status-tracking playbook for the
TPM.

*Where:* all personas (DB + `agents.example.json`).

## 10. Single-click Approve  **[code/UI]**

**Problem.** Approving a gated action required an extra "Confirm approve" click that added
friction without value.

**Solution.** Approve now sends immediately; Deny / Deny-with-reason are unchanged.

*Where:* `web/components/ApprovalCard.tsx`.

---

## Net effect

The human still approves direction and remains the only one who can push, branch, open/merge
PRs, or delete files. But the team's own guardrails — not just good intentions in a prompt —
now keep **coordinators coordinating**, **work reviewed by the right people**, **status
honest**, and **long-running sessions making progress instead of stalling**. The biggest
wins came from moving load-bearing rules out of prose and into the daemon, and from giving
agents a fast, non-binding way to consult colleagues they don't supervise.
