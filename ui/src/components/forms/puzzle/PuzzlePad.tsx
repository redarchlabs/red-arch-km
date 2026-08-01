"use client";

import { Lightbulb, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { PuzzlePadElement } from "@/lib/api/forms";
import { fillSpecTokens, isPuzzleKind, parsePuzzleSpec, type PuzzleKind } from "@/lib/forms/puzzleSpec";

import { ChoicesPad } from "./ChoicesPad";
import { ColorPad } from "./ColorPad";
import { KeypadPad } from "./KeypadPad";
import { SequencePad } from "./SequencePad";
import { SortPad } from "./SortPad";
import { WiresPad } from "./WiresPad";
import type { PadOutcome } from "./types";

/** How long the pad stays locked after a submission, showing that it was sent.
 * Long enough to read, short enough that a second try isn't a wait — the pad
 * does not know whether the workflow will replace the puzzle or leave it up. */
const SENT_LOCK_MS = 1500;

export interface PuzzlePadNodeProps {
  el: PuzzlePadElement;
  /** The enclosing scope's values — where the `*_field` attributes read from. */
  values: Record<string, unknown>;
  disabled: boolean;
  onComplete: (outcome: PadOutcome) => void;
}

/**
 * The shell around every puzzle kind: it resolves what to show, owns the prompt,
 * the hint and the attempt/timing bookkeeping, and hands the outcome back. Each
 * kind component below it worries only about its own interaction.
 */
export function PuzzlePad({ el, values, disabled, onComplete }: PuzzlePadNodeProps) {
  const readField = (field: string | null | undefined): unknown =>
    field ? values[field] : undefined;

  const text = (field: string | null | undefined, inline: string | null | undefined): string => {
    const v = readField(field);
    // A field with a value wins; the inline value is the fallback, so an author
    // can set a default and still let a record override it.
    if (typeof v === "string" && v.trim()) return v.trim();
    if (typeof v === "number") return String(v);
    return (inline ?? "").trim();
  };

  const kindFromField = readField(el.kind_field);
  const kind: PuzzleKind = isPuzzleKind(kindFromField) ? kindFromField : (el.kind ?? "choices");

  // A field wins when it holds something; the inline spec is the fallback. Blank
  // counts as absent, so a record with an empty spec column falls back rather
  // than rendering an unparseable pad.
  const fieldSpec = readField(el.spec_field);
  const hasFieldSpec = typeof fieldSpec === "string" ? fieldSpec.trim().length > 0 : fieldSpec != null;
  const specRaw = hasFieldSpec ? fieldSpec : (el.spec ?? null);
  // Reduce the spec to a STRING before memoising. A JSON record field arrives as
  // a fresh object on every fetch, and a spec that changes identity every render
  // would re-parse, re-shuffle and reset the person's half-finished arrangement.
  const rawJson = typeof specRaw === "string" ? specRaw : JSON.stringify(specRaw ?? null);
  // `{field_slug}` placeholders let one authored spec serve every record it
  // renders against — the labels come from the record, nothing is stored per row.
  const specJson = fillSpecTokens(rawJson, values);
  const parsed = useMemo(() => parsePuzzleSpec(kind, specJson), [kind, specJson]);

  const prompt = text(el.prompt_field, el.prompt);
  const hint = text(el.hint_field, el.hint);
  const submitLabel = el.submit_label?.trim() || "Transmit";

  const [showHint, setShowHint] = useState(false);
  const [sent, setSent] = useState(false);
  const attemptsRef = useRef(0);
  const startedAtRef = useRef(Date.now());
  const lockRef = useRef<number | null>(null);

  // A new puzzle: clear the hint, the lock, and the clock this attempt is timed
  // against. Keyed on the resolved spec + prompt, which is what "a new puzzle"
  // actually means here.
  const puzzleKey = `${kind}|${specJson}|${prompt}`;
  useEffect(() => {
    attemptsRef.current = 0;
    startedAtRef.current = Date.now();
    setShowHint(false);
    setSent(false);
  }, [puzzleKey]);

  useEffect(
    () => () => {
      if (lockRef.current) window.clearTimeout(lockRef.current);
    },
    [],
  );

  const submit = ({ solved, answer }: { solved: boolean | null; answer: string }) => {
    if (disabled || sent) return;
    attemptsRef.current += 1;
    setSent(true);
    lockRef.current = window.setTimeout(() => setSent(false), SENT_LOCK_MS);
    onComplete({
      solved,
      answer,
      attempts: attemptsRef.current,
      elapsed_ms: Date.now() - startedAtRef.current,
    });
  };

  const padProps = { disabled: disabled || sent, submitLabel, submit };

  let body;
  if (!parsed.ok) {
    body = (
      <div className="flex items-center gap-3 rounded-2xl border-2 border-dashed border-destructive/50 bg-destructive/5 p-6 text-destructive">
        <TriangleAlert className="h-6 w-6 shrink-0" />
        <div>
          <p className="font-semibold">This puzzle can&apos;t be shown.</p>
          <p className="text-sm opacity-90">{parsed.error}</p>
        </div>
      </div>
    );
  } else {
    const spec = parsed.spec;
    switch (spec.kind) {
      case "choices":
        body = <ChoicesPad spec={spec} {...padProps} />;
        break;
      case "keypad":
        body = <KeypadPad spec={spec} {...padProps} />;
        break;
      case "sequence":
        body = <SequencePad spec={spec} {...padProps} />;
        break;
      case "wires":
        body = <WiresPad spec={spec} {...padProps} />;
        break;
      case "sort":
        body = <SortPad spec={spec} {...padProps} />;
        break;
      case "color":
        body = <ColorPad spec={spec} {...padProps} />;
        break;
    }
  }

  return (
    <div className="relative space-y-5" style={el.min_height ? { minHeight: `${el.min_height}px` } : undefined}>
      {prompt ? (
        <p className="text-balance text-center text-2xl font-bold leading-tight sm:text-3xl">{prompt}</p>
      ) : null}

      {body}

      {hint && el.show_hint !== false ? (
        <div className="text-center">
          {showHint ? (
            <p className="inline-flex items-start gap-2 rounded-2xl bg-amber-500/10 px-4 py-3 text-left text-base text-amber-700 dark:text-amber-300">
              <Lightbulb className="mt-0.5 h-5 w-5 shrink-0" />
              {hint}
            </p>
          ) : (
            <button
              type="button"
              onClick={() => setShowHint(true)}
              className="inline-flex min-h-12 items-center gap-2 rounded-2xl px-4 text-base font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <Lightbulb className="h-5 w-5" /> Need a hint?
            </button>
          )}
        </div>
      ) : null}

      {sent ? (
        // Confirmation, not a blocker: the pad below is already disabled, and
        // this must never swallow the taps that follow it.
        <div className="pointer-events-none absolute inset-0 flex items-start justify-center">
          <span className="mt-2 rounded-full bg-primary px-5 py-2 text-lg font-semibold text-primary-foreground shadow-lg">
            Sent
          </span>
        </div>
      ) : null}
    </div>
  );
}
