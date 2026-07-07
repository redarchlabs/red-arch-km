/**
 * Value logic for a `businessRule` task's `data.decision_table`, mirroring the
 * backend shape `{ hit_policy, rules: [{ when, output }] }` (see the workflow
 * services' decision-table evaluator). A rule's `when` is a JsonLogic condition
 * (null = an always-matching "else" row); `output` is a flat map of the
 * variables the rule sets when it matches.
 *
 * Pure functions only (no React, no store) so they are trivially unit-testable;
 * the inspector's DecisionTableEditor wires them to the node-data update path.
 */
import type { JsonLogic } from "./conditionExpr";

/** How many matching rules contribute their output. */
export const HIT_POLICIES = ["first", "collect"] as const;
export type HitPolicy = (typeof HIT_POLICIES)[number];

export interface DecisionRule {
  /** JsonLogic condition; null matches unconditionally (the "else" row). */
  when: JsonLogic;
  /** Variables this rule sets when it matches. */
  output: Record<string, unknown>;
}

export interface DecisionTable {
  hit_policy: HitPolicy;
  rules: DecisionRule[];
}

/** Coerce any value into a valid hit policy (unknown → "first"). */
export function normalizeHitPolicy(value: unknown): HitPolicy {
  return value === "collect" ? "collect" : "first";
}

/** Narrow an editor value (ConditionEditor emits `unknown`) to JsonLogic. */
export function asJsonLogic(value: unknown): JsonLogic {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function normalizeRule(raw: unknown): DecisionRule {
  const obj = asRecord(raw);
  return { when: asJsonLogic(obj.when), output: asRecord(obj.output) };
}

/** A blank rule (matches unconditionally, sets nothing) for the "add" action. */
export function emptyRule(): DecisionRule {
  return { when: null, output: {} };
}

/**
 * Read a normalized decision table out of a node's `data`. An absent, malformed,
 * or partial `decision_table` degrades to `{ hit_policy: "first", rules: [] }`.
 */
export function readDecisionTable(data: Record<string, unknown>): DecisionTable {
  const obj = asRecord(data.decision_table);
  const rules = Array.isArray(obj.rules) ? obj.rules.map(normalizeRule) : [];
  return { hit_policy: normalizeHitPolicy(obj.hit_policy), rules };
}

/**
 * Serialise a decision table back to the plain object stored on `data`. Rules
 * are reduced to their persisted `{ when, output }` shape (any editor-only
 * bookkeeping the caller layered on is dropped). The input is never mutated.
 */
export function writeDecisionTable(table: DecisionTable): Record<string, unknown> {
  return {
    hit_policy: normalizeHitPolicy(table.hit_policy),
    rules: table.rules.map((r) => ({ when: r.when, output: r.output })),
  };
}
