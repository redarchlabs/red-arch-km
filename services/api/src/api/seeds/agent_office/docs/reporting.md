# Reporting

Every agent records what it did; the TPM rolls it up. Reports are human-readable summaries of ground truth that the daemon already captures (every tool decision and the full session transcript are persisted), so keep them concise — they're an index, not a re-transcription.

> **How to record:** call the **`log_work_order(slug, entry)`** tool — entries are stored centrally and shown in the **Work Orders** tab under your role, alongside your full session transcripts. Do **not** write to a `docs/work-orders/*.md` file (your worktree is isolated; the file would be lost). The TPM writes the order body and report with `create_work_order` / `update_work_order`.

## Per-agent activity-log entry

When you finish a task, append one diary entry with `log_work_order`. Format:

```markdown
### <role> — <Linear ID> (<short task title>)
- <itemized thing you did>  (file/path touched)
- <itemized thing you did>
- Tests: <what you wrote / ran and the result>
- Escalated: <decision> → <who decided> : <outcome>   (omit if none)
- Open / handed off: <anything left, who has it>        (omit if none)
- Human-gated & queued: <push/branch/PR/delete not done> (omit if none)
```

Rules:
- One entry per task, appended (never rewrite earlier entries).
- Itemize concretely — "added POST /api/wallet/topup with idempotency key", not "did backend work".
- Always note what you did **not** do because it's human-gated, so the TPM can queue it.

## TPM summary report (Work Order §9)

At the end of the WO, the TPM compiles:

```markdown
## 9. Summary report

### Outcome
<1–3 sentences: what shipped to "ready", what's blocked, what's deferred.>

### By agent
- **solution-architect** — <summary> · specs: <ids> · escalations adjudicated: N
- **principal-engineer** — <summary> · tasks: <ids> · delegated/reviewed: <ids>
- **frontend-engineer / backend-engineer / database-architect** — <summary> · tasks: <ids> · tests: <counts>
- **qa-engineer** — <summary> · edge cases + unit/e2e reviewed: <ids/files>

### Itemized work
<the §8 activity log, grouped by task — the detailed record.>

### Decisions made
<resolved items from §7, each with the answer and who decided.>

### Open items
<unresolved §7 decisions still needing the human.>

### Queued human-gated actions
<every push / branch / PR / delete waiting on the human, with the exact command/intent.>
```

This same summary is also posted to the Linear project as a **status update**, with a link back to the WO md (the md is the source of truth for the narrative).

## The learning loop

After the report, the TPM prompts each role to write durable lessons into its `memory/` (one fact per markdown file): conventions discovered, pitfalls hit, "how we do X here." Those notes are inlined into the role's prompt at the start of every future session — which is how the team compounds competence over time.
