import type { PuzzleSpec } from "@/lib/forms/puzzleSpec";

/** What a pad hands back when the person finishes.
 *
 * `solved` is deliberately nullable: `choices` and `keypad` are never told the
 * answer, so the pad genuinely does not know — reporting `false` there would be
 * a lie a workflow might act on. The locally-graded kinds report a real verdict.
 */
export interface PadOutcome {
  solved: boolean | null;
  /** Short human-readable record of what was chosen/keyed/arranged. */
  answer: string;
  attempts: number;
  elapsed_ms: number;
}

/** The contract every kind-specific pad implements. The shell owns the prompt,
 * the hint, attempt counting and the trip back to the workflow; a pad owns only
 * its own interaction and says when the person is done. */
export interface PadProps<S extends PuzzleSpec = PuzzleSpec> {
  spec: S;
  disabled: boolean;
  /** Label for the confirm control on pads that have one. */
  submitLabel: string;
  submit: (result: { solved: boolean | null; answer: string }) => void;
}
