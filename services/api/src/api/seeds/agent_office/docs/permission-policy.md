# Permission Policy

The hard, machine-enforced rules about what any agent may do. This is enforced by the daemon's policy engine ([../../src/policy.ts](../../src/policy.ts)) reading [../../policy.json](../../policy.json) — not by anyone's judgment. No role's autonomy setting (`autoAllow`) can override it.

## The three tiers (precedence: deny > ask > allow)

The policy classifies every tool call into one of three verdicts:

- **deny** — hard-blocked. The action never runs, and the human is *not* asked. Reserved for the truly dangerous (`rm -rf /`, `sudo`, `curl | sh`, force-push).
- **ask** — **always routed to the human, and can never be auto-allowed.** This is the tier for the irreversible / outward-facing actions below. Even an agent set to "full autonomy" hits the human here.
- **allow** — runs without asking. Read-only tools, tests, in-cwd edits, memory writes, and a role's own `autoAllow` list land here.

Unmatched calls fall through to **ask** (the human) by default.

## The human-only actions (the `ask` tier)

These four classes **always** require the human and **no agent can grant them** — not the engineer, not the Solution Architect, not the TPM:

| Class | Example commands |
|-------|------------------|
| Delete files from disk | `rm`, `rmdir`, `git clean`, `git rm` |
| Push to a git remote | `git push` |
| Create a branch | `git branch <name>`, `git checkout -b`, `git switch -c` |
| Create / merge a PR | `gh pr create`, `gh pr merge` |

`git worktree add` is also gated (it creates branches). Read-only git (`status`, `diff`, `log`, `show`, `fetch`, branch *listing*) is auto-allowed.

## Why `ask`, not `deny`, for these

We want the human to be *able* to approve a push or a PR when it's time — so they can't be hard-`deny`'d (that would block even the human). And we never want an agent to do them unattended — so they can't be plain `allow`/`autoAllow` either. The dedicated **`ask` tier** is exactly "always the human, never an agent," which is the guarantee this team is built on.

## How this interacts with agent autonomy

A role-agent's `autoAllow` list widens what's auto-approved **for that agent only**, layered onto this base policy. But `autoAllow` can only add to the **allow** tier, and **ask beats allow**. So even an agent with `autoAllow: ["Bash"]` (which would auto-run arbitrary shell) still hits the human for push / branch / PR / delete. Verified by the policy precedence test.

## Operator setup

The daemon reads its policy from `AGENT_CONTROL_POLICY` or `~/.agent-control/policy.json`. To run the org with these gates, copy this repo's [../../policy.json](../../policy.json) into place:

```bash
cp policy.json ~/.agent-control/policy.json   # or: export AGENT_CONTROL_POLICY=$PWD/policy.json
```

If you change the gates, keep the four human-only classes in the `ask` tier. See also [escalation-protocol.md](./escalation-protocol.md) for the *judgment* permissions (which go through supervisors, not this policy).
