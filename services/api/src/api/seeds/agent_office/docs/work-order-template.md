# Work Order Template

A **Work Order (WO)** is the master container for a body of work (typically a day's plan, sometimes a multi-day initiative). It is the human-facing plan, the standup record, the decision log, and the final report.

## Where it lives

- **Linear is canonical for the task graph.** Each WO is a Linear **Project** named `WO-YYYY-MM-DD-<slug>`; each feature/task is a Linear **issue** under it, with status owned by Linear.
- **The Work Order record is stored centrally by the daemon** (browsable in the **Work Orders** tab in the web UI), NOT as a file in the repo — agents run in isolated git worktrees, so a `docs/work-orders/*.md` file an agent writes would be stranded on its worktree branch. Use the tools instead:
  - **`create_work_order(slug, title, body, status?)`** — the TPM creates the order; `body` is the markdown (objective, plan, decisions-for-human, …).
  - **`update_work_order(slug, status?, body?)`** — update status or write the final report into the body.
  - **`log_work_order(slug, entry)`** — any agent appends an itemized **diary** entry under its own role.
- The **per-agent diary** in the Work Orders tab is the union of those `log_work_order` entries **and** the full transcripts of every session linked to the order (a session links automatically the first time it calls one of these tools).
- **Two-way link:** put the Linear project URL in the WO body; the Linear project references the WO slug.

The TPM creates and owns the order; engineers, QA, and the SA append diary entries and decisions as they work.

## File structure

Copy this skeleton for a new WO. Sections 1–7 are written at standup (before human approval). Sections 8–9 fill in during/after execution.

```markdown
# WO-YYYY-MM-DD-<slug>

**Linear project:** <url or id>
**Status:** draft | awaiting-approval | approved | in-progress | done
**TPM:** technical-project-manager   **Date:** YYYY-MM-DD

## 1. Objective
What we're doing and why, tied to the human's goals or the backlog.

## 2. Plan
Features in scope, sequencing, dependencies, risks. What is explicitly OUT of scope.

## 3. Team & sizing
Which roles are needed, how many agents, rough effort per feature.

## 4. Task breakdown
| Task | Role | Linear ID | Acceptance criteria |
|------|------|-----------|---------------------|
| …    | backend-engineer | CORE-### | … |

## 5. Standup notes
The team's notes for today: what each role reported (progress, what's next),
blockers, anything carried over from the last WO.

## 6. Discussion
Design debates and options weighed (SA-led). Why we chose what we chose.

## 7. Decisions for the human   ← the human reads THIS first
Open questions that require human approval before or during execution. Each as:
- [ ] <decision needed> — context, options, the team's recommendation.
Resolved ones move to "(resolved: <answer> — <who/when>)".

## 8. Activity log
Appended live by each agent as it finishes a task. See reporting.md for the format.

## 9. Summary report
TPM-compiled at the end: per-agent summary + itemized work + outcomes + open items
+ any human-gated actions queued and waiting (push/branch/PR/delete).
```

## Rules

- **Section 7 is the human's entry point.** Put every decision that needs the human there, plainly, with a recommendation. Don't bury a UX change in the discussion.
- **Never mark a Linear issue Done programmatically** (see [linear-workflow.md](./linear-workflow.md)). The WO `Status` field is for the md file; Linear issue status moves to *In Review*, and the human closes.
- **The activity log is append-only.** Don't rewrite history; add entries.
- One WO per standup. If new work appears mid-day that changes the plan, it's a decision for the human (section 7), not a silent edit.
