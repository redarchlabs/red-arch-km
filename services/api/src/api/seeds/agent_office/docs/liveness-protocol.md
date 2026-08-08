# Liveness & Boot-Death Protocol

**Status:** active · **Area:** preventing false "boot-death" declarations + the duplicate work they cause.

## The problem this prevents

A coordinator/peer declared a delegated session "boot-died silently" and re-delegated its task — but
the original had **actually completed**. It was a long live-QA run inside an **isolated Docker compose
project on a non-default port** (e.g. `cobalt-qa-s820` / `:55440`), invisible to a liveness check that
only looked at the default project/port. Result: duplicate QA runs (s820→s823, s821→s826), and the
"dead" session's detached background tasks later created orphan scratch DBs.

The harness's own activity-liveness watchdog is **not** the culprit — it reads per-turn SDK-callback
heartbeats, and a long isolated run that polls `TaskOutput` keeps that heartbeat fresh. The false
positives come from **agents' own** boot-death heuristics. This protocol governs those.

## Rule 1 — Runners post a durability marker BEFORE a long isolated run

Any role about to start a multi-minute, isolated job (live-QA gates, full builds/migrations, load
tests) MUST first write a pre-run marker to the work-order diary via `mcp__self__log_work_order`:

```
[[QA-CHECKPOINT]] LIVE — s<id> starting <gate> on <commit>; isolated project <project>, port <port>; ETA <minutes>m
```

A recent pre-run marker is strong positive evidence the session is alive. Heartbeat the diary again at
phase boundaries on very long runs — a single `go test -race` or migration can run many minutes with
zero diary writes, and silence is NOT death.

## Rule 2 — Declaring a peer boot-died requires MULTIPLE signals, not one

Before declaring a delegated/peer session dead and re-delegating, ALL of the following must hold —
any single one alone is insufficient:

1. **No isolated container/process** — check the runner's OWN project/port, not just the default:
   `docker ps -a --filter name=<runner-project>` (e.g. `cobalt-qa-s<id>`), and look for its process.
2. **Genuinely stale transcript** — and remember a long synchronous tool legitimately stalls the
   transcript; combine with (3).
3. **No fresh diary marker** — no `[[QA-CHECKPOINT]] LIVE` / phase heartbeat within the expected window.

If the runner posted a recent marker, or its isolated project/port is up, assume **alive** — wait or
ask, do not re-delegate.

## Rule 3 — Re-delegate at most once, and isolate every run

- Per-run **unique** resource names keyed to the session (`cobalt-qa-s<id>`, DB `cobalt_<variant>_s<id>`)
  and **verify-empty before migrate**. A detached task from a wrongly-declared-dead session can
  `createdb` *after* a liveness snapshot — unique names + verify-empty prevent silent false-passes and
  collisions.
- Re-delegate a task at most once on a death signal; if the "dead" runner resurfaces with a verdict,
  reconcile (two PASSes on the same tip is corroboration, not a conflict) and correct the board.

## Rule 4 — Always tear down your isolated stack

On finish (pass OR fail), tear down the isolated compose project and drop per-run DBs. Leaked stacks
(`docker ps` showing day-old `cobalt-qa-s*`) are real over-subscription pressure and are reclaimed by
the janitor (`mcp__janitor__scan_reclaimable` → human-gated `apply_reclaim`) — but cleaning up after
yourself is the first line of defense.

The janitor's leaked-stack reclaim is deliberately conservative because **this host runs more than
one agent-control daemon, and they reuse the same `s<id>` session namespace** (see the cross-daemon
hazard in the memory). Two guards bound the blast radius:

1. **Owned-prefix scoping** — the scan only sees containers whose name starts with a prefix the
   operator declared in `RECLAIM_CONTAINER_PREFIXES`. Unset (default) = the destructive scan is
   dormant. Each daemon must be configured with the prefixes ITS stacks use, and those prefixes must
   not overlap another daemon's.
2. **Store membership + terminal status** — only a stack whose session is known to THIS daemon's
   store and is `done`/`failed` is reclaimable. An id this store has never seen (possibly another
   daemon's live session) is never touched.

Attribution is still by **name**, and a stack may hold a stateful volume — the approval card says so;
verify ownership before approving. The robust long-term fix is for stack-creating agents to stamp a
harness-owned docker label (e.g. `--label agent-control.worktrees-dir=$WORKTREES_DIR`) at
`docker compose up` time and have the janitor filter on that label instead of on the name. That work
lives in the stack-spin-up tooling (outside this repo), so the prefix+store guards are the in-repo
stopgap until it lands.

## Related
- `escalation-protocol.md`, `interaction-policy.md`
- Harness boot-death reconciliation (DB-`running` zombies with no checkpoint): `src/session-manager.ts`
  `reapBootDiedSessions` (RUNTIME_BOOTDEATH_REAP_MINUTES).
- Leaked-stack reclaim guards: `src/docker.ts` `listSessionContainers` (owned-prefix scoping) +
  `src/reclaim.ts` leaked-container scan (store-membership + terminal status).
