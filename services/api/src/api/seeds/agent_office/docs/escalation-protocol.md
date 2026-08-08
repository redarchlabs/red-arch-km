# Escalation Protocol

How an agent asks for permission or a decision it isn't authorized to make alone. Read with [charter.md](./charter.md) (the org chart + the UX-change test) and [permission-policy.md](./permission-policy.md) (the hard human-only gates).

## The two kinds of "asking for permission"

There are exactly two, and they are different:

1. **Hard human-only actions** — deleting files, `git push`, creating a branch, creating/merging a PR. You do **not** escalate these. When you try them, the daemon automatically routes the approval to the human. Just attempt the action; the human will see the approval prompt. No agent can grant these.

2. **Judgment calls** — anything else you're not authorized to decide alone. For these you **escalate to your supervisor** with the `escalate` tool.

## When to escalate (engineers)

Escalate to your supervisor (the Solution Architect, for engineers) when:

- The work would **substantially change the user experience, workflow, layout, or behavior** of the application (the charter test) — even if you *could* technically do it.
- The Linear task or the design spec is ambiguous, contradictory, or silent on a decision you must make.
- You've found a problem that changes the plan (the approach won't work, a dependency is missing, the estimate was wrong).
- The change would touch auth, billing, data retention, privacy, or compliance.

When in doubt, escalate. A cheap question up the chain beats an expensive surprise to the human.

### How to escalate

Call `escalate` with:
- `reason` — what you need decided, in 1–2 sentences.
- `context` — options you considered, trade-offs, the Linear task / Work Order id.

You will **block** until it's settled. The result is either your supervisor's decision (proceed accordingly) or the human's answer (if it was bubbled up). Then **record the decision** in the Work Order's activity log and decisions section, and continue.

## When you receive an escalation (supervisors)

You'll get a message identifying the escalation id and the report's question. Adjudicate it:

1. Apply the **UX-change test** from [charter.md](./charter.md).
2. **If it does NOT substantially change UX/workflow/layout/behavior and it's within your scope** → decide it yourself. Call `resolve_escalation` with `decision: "resolved"` and a clear note telling the engineer what to do.
3. **If it DOES, or it's outside your authority/scope** → don't decide it. Call `resolve_escalation` with `decision: "bubble"` and your recommendation. It goes to the next level up (ultimately the human), and their answer flows back to the engineer automatically.
4. If you (a supervisor) can't decide it for your own reasons, bubble it — the same mechanism carries it up your chain.

Always write the decision into the Work Order (decisions section + activity log) so the history is durable.

## Only real tool calls count (anti-fake rule)

Escalating and being reviewed mean **calling the tool and getting a result back** — `mcp__self__escalate` or `request_review`. Writing a `TodoWrite` item, a memory note, or a work-order line that *says* you escalated or were reviewed does **not** count and is treated as if you skipped the step. If you didn't receive a result from the tool, it didn't happen. (This is enforced by independent review — see below — not by trust.)

## Mandatory review checkpoint (engineers)

You are **not done** until your supervisor signs off. End every task by calling **`request_review`** with an honest summary and a list of every decision the product owner didn't specify (each marked with its escalation id, or `NOT ESCALATED`). Your supervisor then **independently inspects your actual diff** (`git -C <your cwd> diff`) — a summary alone won't pass. The supervisor either signs off, sends it back with required changes (fix and call `request_review` again), or bubbles it to the human if it finds an un-escalated decision or a UX/workflow/layout/behavior change. This is the team's defense against an engineer that decided something it should have escalated: the reviewer looks at the real code, so fabrications are caught.

## Adjudicating a review request (supervisors)

A `REVIEW REQUEST` escalation is handled like any other, but you **must** open the engineer's actual diff and verify it yourself rather than trusting the summary. For every change, confirm it was within the engineer's authority *or* genuinely escalated to you earlier. Any un-escalated unspecified decision, or any UX/workflow/layout/behavior change, → **bubble to the human**. Clean and in-scope → sign off. Needs work → resolve with the exact changes required.

## Invariants

- **Nothing material is decided at a level that lacks the authority for it.** A behavior/UX change is never decided by an engineer or, alone, by the SA — it reaches the human.
- **Escalations are not the hard gates.** Never use `escalate` to get a `git push` / branch / PR / delete approved — those go straight to the human as approvals on their own.
- **Blocked ≠ stuck.** While you're blocked on an escalation, that's expected; the supervisor (or human) is deciding. Do not work around a blocked decision.
