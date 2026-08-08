# Definition of Done

What "done" means before a task goes to the review gate. A task is **not** done until every box that applies is true.

## For an implementation task (frontend / backend / database engineer)

- [ ] The Linear task's acceptance criteria are all met.
- [ ] The work matches the Solution Architect's design spec (or a deviation was escalated and approved).
- [ ] Tests written **first** and passing: unit for new logic, integration for new endpoints/flows. No new public surface ships untested.
- [ ] No behavior/UX/layout change slipped in that wasn't approved (if it did, it should have been escalated).
- [ ] Build is green (`npm run build` / `npm run build:web` for this repo; the project's equivalent otherwise) and the linter/type-checker passes.
- [ ] No secrets, no hardcoded config, errors handled at boundaries.
- [ ] **The review board signed off** (via `request_review` to the principal engineer): the PE reviewed the real diff, QA confirmed edge cases + negative testing + appropriate unit/e2e tests, and the relevant peer engineer reviewed. You never self-declare done. The daemon **enforces** the QA step — `request_review` is blocked until a qa-engineer review (or a human-approved `request_qa_exception`) is recorded; there is **no** automatic infra carve-out.
- [ ] The WO activity log has an itemized entry for this task (see [reporting.md](./reporting.md)).
- [ ] Nothing was pushed/branched/PR'd/deleted (those are human-gated and happen separately).

## For a design task (solution-architect)

- [ ] A written spec exists (Linear document + repo ADR when a real architectural decision was made).
- [ ] Trade-offs are explicit: options considered, choice, why, cost, what it forecloses.
- [ ] Blast radius is named: which surfaces change (frontend/backend/db/infra/docs).
- [ ] Acceptance criteria for each downstream task are concrete enough for an engineer to build and QA to test against.
- [ ] Any UX/workflow/layout/behavior change is flagged as a decision for the human.

## For a QA review / test task (qa-engineer)

QA is the **test gate** on the principal engineer's review board: no feature/bug fix is done until QA passes it. The daemon enforces this — an implementation agent's `request_review` is blocked until a QA review (or a human-approved `request_qa_exception`) is recorded on the work order. QA is skipped **only** via a human-approved exception; there is no automatic infra carve-out.

- [ ] The spec's **edge cases and failure modes** are actually exercised — not just the happy path. Missing cases are listed as findings.
- [ ] **Unit tests** exist for the new/changed logic (boundaries, empty/null, error paths, invariants).
- [ ] **End-to-end (Playwright) tests** cover the documented user-facing behavior.
- [ ] Tests are deterministic (no reliance on timing/order); flaky tests are quarantined, not ignored; no assertions loosened to force a pass.
- [ ] Artifacts (screenshots/traces) captured for the critical flows.
- [ ] A clear **PASS/FAIL with specific gaps** is delivered. QA may write/extend the unit/e2e tests itself; a product-code fix is escalated to the engineer, not made by QA.
- [ ] The WO activity log has an entry listing what was reviewed/tested and the result.

## For the Work Order overall (technical-project-manager)

- [ ] Every task is either done, or its open items are in section 7 (decisions for the human).
- [ ] The review board cleared for each feature/bug task (principal engineer + QA + relevant peer engineer), and any `/finish-feature` CRITICAL/HIGH findings are resolved.
- [ ] The summary report (WO §9) is compiled.
- [ ] Queued human-gated actions (push/branch/PR/delete) are listed for the human, not done silently.
- [ ] Each role was prompted to record durable lessons in its `memory/`.
