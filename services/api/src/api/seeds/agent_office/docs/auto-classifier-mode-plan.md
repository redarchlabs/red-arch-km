# Implementation Plan: wire work-order `auto` mode → Claude Code classifier "Auto mode"

**Status:** IMPLEMENTED on `feat/auto-classifier-mode` (awaiting live test) · **Date:** 2026-06-21 · **Area:** permission core (safety-critical)

## Implementation outcome (what actually shipped)

Done in one branch (not phased — user opted to build it all). All 357 unit tests + typecheck green.

- **SDK 0.1.77 → 0.3.185.** Typecheck/tests passed with **zero product-code changes** for the
  bump itself. One surprise not in the migration notes: 0.3.185 **peer-requires `zod@^4`** — bumped
  `zod ^3.23.8 → ^4.0.0` (resolved 4.4.3); all usage was v4-compatible, no code changes.
- **Must-ask mechanism = (B), empirically confirmed (probe 3):** a `PreToolUse` hook returning
  `permissionDecision:"ask"` routes straight to the existing `canUseTool` → `channel.requestApproval`.
  So the human-approval flow is reused **unchanged** — no hook-timeout risk, no reimplementation.
- **`policy.ts`:** added `AutoVerdict` + `evaluatePolicyForAuto`. Fall-through → `defer`; deny/ask/allow
  preserved. Conservative refinement: a *recognized* command in an *unsafe* shell construct
  (`git log > /etc/cron.d/evil`) stays `ask`, never `defer` — only genuinely unrecognized commands defer.
- **`session-mode.ts`:** `toPermissionMode("auto") → "auto"` (+ docs).
- **`agent.ts`:** `autoPolicyHook` (deny→block / allow / ask→canUseTool / defer→classifier),
  `resolveSdkMode` (downgrades auto→default when classifier off or model unsupported),
  `modelSupportsAuto` (Haiku excluded), `AGENT_AUTO_CLASSIFIER` kill-switch (default ON),
  and a **HIGH-IMPACT DECISIONS** block in `SUPERVISOR_PROMPT`.
- **Project-impact requirement (user):** the classifier judges *safety*, not *project impact*, so the
  prompt addendum requires agents to `ask_human` before major UI / architecture / schema / API /
  dependency decisions even when no permission prompt fires. The must-ask permission tier is also intact.
- **`.env.example`:** documented `AGENT_AUTO_CLASSIFIER`.
- Dropped from the original plan (not needed): TodoWrite→Task migration (`CLAUDE_CODE_ENABLE_TASKS=0`),
  MCP non-blocking workaround (the `human` server is in-process), and the env-replace fix (no `env:` used).

---

### Original plan (for reference)

**Status:** proposed (awaiting review) · **Date:** 2026-06-21 · **Area:** permission core (safety-critical)

## Goal

Make a work order set to `mode: "auto"` actually use Claude Code's **classifier-based Auto
mode** (the SDK `permissionMode: "auto"`) so the agent stops asking the human to approve the
large bucket of *safe-but-unlisted* actions — while **preserving the supervisor-policy
invariant** that a hard-deny (and the irreversible/outward "must-ask" tier) can never be
auto-run. See [permission-policy.md](./permission-policy.md) and the `SessionMode` contract in
`src/session-mode.ts`.

## Why this is not a one-line `toPermissionMode` change (empirical findings)

Probed against the live subscription with SDK 0.3.185 (sandbox in `/tmp/sdk-probe-run`):

1. **`canUseTool` is bypassed under `permissionMode: "auto"`.** A host `canUseTool` set to
   hard-deny `Bash` **never fired**; the classifier ran `Bash` + `Read` itself. The classifier
   only routes its *"ask"* decisions to the host (confirmed by `SDKPermissionDeniedMessage`
   docs: the "ask path surfaces via a `can_use_tool` control_request"). → A naive switch to
   `auto` would **silently void the entire supervisor policy** (`agent.ts` `canUseTool`).
2. **`PreToolUse` hooks DO gate under `auto`.** A `PreToolUse` hook fired for **every** tool and
   its `permissionDecision: "deny"` on `Bash` **blocked execution** (tool_result was the deny
   message, not the command output). Hooks run *before* the classifier in the permission
   pipeline (Hooks → deny rules → permission mode/classifier → allow rules → `canUseTool`).
3. **Auto mode is reachable from the SDK on this (Max) subscription** — the probe executed
   successfully. (A secondary source claiming Max is excluded was wrong; verified empirically.)

**Conclusion:** to keep the classifier *and* the hard-deny invariant, the supervisor policy
must move (for `auto` mode only) from `canUseTool` into a **`PreToolUse` hook**.

## Design

### Mode → SDK mapping (new)

| Work-order mode | SDK `permissionMode` | Gate that enforces policy |
|---|---|---|
| `auto`  | `"auto"` *(was `"default"`)* | **`PreToolUse` policy hook** (classifier handles `defer`) |
| `ask`   | `"default"` | `canUseTool` (unchanged) |
| `edit`  | `"default"` | `canUseTool` (unchanged) |
| `plan`  | `"plan"`    | `canUseTool` (unchanged) |

### New policy verdict: `defer`

`evaluatePolicy` currently collapses *explicit must-ask* and *unmatched fall-through* into the
same `"ask"` (`policy.ts:170,198,211`). The payoff of auto mode is precisely to send the
**fall-through** bucket to the classifier instead of the human. Add a sibling:

```
evaluatePolicyForAuto(policy, tool, input, cwd): "deny" | "allow" | "ask" | "defer"
  - deny rule match            → "deny"   (hard block — invariant)
  - explicit ask rule match    → "ask"    (must-ask tier → human; never auto-allowed)
  - allow rule match (all segs)→ "allow"
  - nothing matched            → "defer"  (let the classifier decide)   ← the win
  Bash segment algebra: deny(any) > ask(any) > defer(any) > allow(all)
```

Pure function, reuses `ruleMatches`/`splitSegments`/`shellSafeForAllow`; fully unit-testable.

### The `PreToolUse` policy hook (`auto` mode only)

Built inside `runAgent` so it closes over the same context `canUseTool` uses (`opts.kind`,
`opts.extraAllowWriteDir`, `policy`, `channel`, `hooks`, `currentMode`):

```
hook(input):
  if currentMode !== "auto"            → return {}            // no-op; canUseTool path handles non-auto
  if HUMAN_TOOL_PREFIX / memory-dir / kindGate ... (mirror canUseTool's pre-checks)
  v = evaluatePolicyForAuto(policy, tool, input, cwd)
  edit re-gate via editVerdict for WRITE_TOOLS (unchanged semantics)
  switch v:
    "deny"  → { permissionDecision: "deny",  reason: "supervisor policy" }   // hard-deny survives
    "allow" → { permissionDecision: "allow" }
    "defer" → { permissionDecision: "defer" }                                // classifier decides
    "ask"   → human approval (see decision below)
  + hooks.onDecision(...) for audit (deny/ask/allow), same as canUseTool today
```

Keep `canUseTool` installed as today (it's simply ignored while in `auto`), so a **live
`setMode` switch** in/out of `auto` works without re-spawning. `response.setPermissionMode("auto")`
is supported (`"auto"` is a valid `PermissionMode`); extend `toPermissionMode` + the `setMode`
handler accordingly.

### Must-ask tier under auto — DECISION PENDING a 1-call spike (Phase 2, task 0)

The "ask" tier (git push, branch/PR, rm, installs, secrets) must still reach the **human**.
Two candidate mechanisms — pick after the spike:

- **(A) Hook awaits `channel.requestApproval` and returns `allow`/`deny`.** Reuses the exact
  current approval flow. **Risk:** `PreToolUse` hooks may have a timeout; human approval can
  take minutes. Must confirm the hook can block indefinitely (set/disable `timeout` on the
  hook matcher) — otherwise a pending approval could be killed.
- **(B) Hook returns `permissionDecision: "ask"`** and lets the SDK surface a `can_use_tool`
  control_request to the existing `canUseTool` (which already blocks indefinitely). Cleaner if
  it works under `auto` — **needs probe 3 to confirm** hook-`ask` routes to the host under auto.

Recommend **probe 3 first**; prefer (B) if confirmed, else (A) with an explicit no-timeout hook.

### Model gating

Auto mode requires **Opus 4.6+/Sonnet 4.6+**; the classifier runs Sonnet 4.6. Roles routed to
**Haiku** (`opts.model`) are **incompatible**. When `mode === "auto"` and the resolved model is
unsupported: either (a) fall back to `"default"` + `canUseTool` for that session (log it), or
(b) force a supported classifier-capable model. Default to (a) — never silently run auto on an
unsupported model.

## SDK migration 0.1.77 → 0.3.185

**Confirmed no impact (verified against the unpacked 0.3.185 types/exports):**
- No `env:` option used in the spawn path → the `env` *replace* breaking change doesn't apply.
- No v2 session API (`unstable_v2_*`) → removal doesn't apply; harness already uses `query()` + `resume`.
- `settingSources` already explicit `["user","project","local"]` → behavior preserved.
- All options used (`maxThinkingTokens`, `disallowedTools`, `systemPrompt`, `resume`, `maxTurns`,
  `mcpServers`, `canUseTool`, `permissionMode`) and exports (`query`, `tool`, `createSdkMcpServer`)
  exist with the same shapes in 0.3.185.

**Needs verification / small action:**
- **MCP non-blocking connect (0.3.142+):** servers now connect in the background. The `human`
  MCP server provides the approval tools + human-reply path — an early tool call could race its
  connection. Action: set `MCP_CONNECTION_NONBLOCKING=0` for spawns (or `alwaysLoad`/equivalent
  on the human server) and verify approval works on the first tool call after spawn.
- **`TodoWrite` → Task tools:** `TodoWrite` is deprecated in favour of `TaskCreate/Update/Get/List`.
  Add the Task tools to `command-safety.ts` `READ_ONLY_TOOLS` and to the `allow` tier in
  `policy.json` (keep `TodoWrite` for back-compat).
- **`systemPrompt` preset shape:** harness passes `{ type: "preset", preset: "claude_code", append }`.
  Confirm the preset object form is still accepted (0.3.185 also documents string/array forms).
- **Hook input agent context:** confirm `PreToolUseHookInput` carries the subagent/agentID so
  role-aware `kindGate`/approval still works for Task-spawned subagents (today `canUseTool` gets
  `callOpts.agentID`).
- **`tool()` / `createSdkMcpServer()` signatures** used by `self-tools.ts`, `janitor-tools.ts`,
  `agent.ts`, `diary-summary.ts` — re-typecheck under 0.3.185.

## File-by-file changes

1. `package.json` — bump `@anthropic-ai/claude-agent-sdk` `^0.1.0` → `^0.3.185`.
2. `src/policy.ts` — add `evaluatePolicyForAuto` (+ a pure `autoHookDecision(verdict, mode, isWrite)` mapper for testing).
3. `src/session-mode.ts` — `toPermissionMode("auto") → "auto"`; update the module doc; add `usesClassifier(mode)` helper.
4. `src/agent.ts` — build + wire the `PreToolUse` policy hook; model gate for auto; extend `setMode`/`toPermissionMode`; keep `canUseTool`; audit wiring (incl. surfacing classifier `permission_denied`).
5. `src/command-safety.ts` + `policy.json` — add Task tools to read-only/allow.
6. Spawn env — `MCP_CONNECTION_NONBLOCKING=0` (or server `alwaysLoad`).
7. Docs — update `permission-policy.md` + `session-mode.ts` doc to describe auto-mode-as-classifier.

## Test plan (TDD — write tests first)

- `policy.test.ts` (new/extend): `evaluatePolicyForAuto` → `defer` on unmatched; `deny`/`ask`/`allow`
  on matched; Bash segment combinations (deny>ask>defer>allow); secrets→ask; unparseable→defer-or-ask per design.
- `session-mode.test.ts`: `toPermissionMode("auto") === "auto"`; others unchanged.
- `autoHookDecision` mapper: verdict → hook output incl. edit re-gate + hard-deny precedence.
- Integration (probe-style, gated/manual): under `auto`, a deny-tier action is blocked by the hook;
  a fall-through action is auto-handled; an explicit-ask action reaches the approval channel.
- Full `vitest` green before merge.

## Rollout (phased — safest for the permission core)

- **Phase 1 — SDK bump only.** 0.1.77→0.3.185, fix breaking-change items, **behavior identical**
  (`auto` still maps to `"default"`+`canUseTool`). `vitest` green. Ship/merge independently.
- **Phase 2 — classifier auto, behind a flag (default OFF).** probe 3 → pick must-ask mechanism;
  add `evaluatePolicyForAuto` + the hook + the mapping; model gate. Verify on a throwaway WO.
- **Phase 3 — enable by default.** Flip the flag, update docs, note the per-tool classifier
  cost (extra Sonnet 4.6 call per `defer`) for fleet cost awareness.

## Risks & open questions

- **Hook-`ask` timeout vs. indefinite human approval** (the load-bearing unknown — Phase-2 task 0).
- **MCP non-blocking** racing the human approval server on first tool.
- **Cost/latency:** a classifier call per non-trivial tool decision, ×N agents.
- **Subagent agentID** availability in hook input (role-aware gating for Task-spawned children).
- **Live `setMode`** across the auto boundary — confirm `setPermissionMode("auto")` mid-session.

## Effort estimate

Phase 1 ≈ 0.5–1 day (audit + typecheck + test). Phase 2 ≈ 1–2 days (spike + hook + tests).
Phase 3 ≈ 0.5 day (docs + enable). Risk: **medium** — safety-critical path, well-contained by the
phased flag and the existing test suite.
