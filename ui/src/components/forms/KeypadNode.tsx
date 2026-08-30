"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";

import type { KeypadElement } from "@/lib/api/forms";

/**
 * A numeric entry pad that runs a workflow with what was typed.
 *
 * A console operated by a finger needs digits big enough to hit without looking,
 * and a value committed deliberately rather than on every keystroke — a course
 * of 045 must not be entered as 0, then 04, then 045. That is a different
 * control from a text input, which is why it is its own element.
 */

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

export function KeypadNode({
  el,
  onRun,
  disabled,
}: {
  el: KeypadElement;
  onRun?: (workflowId: string, inputs: Record<string, unknown>) => Promise<void> | void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  const max = el.max_length ?? 6;
  const press = (k: string) => {
    setValue((v) => {
      if (k === "." && (!el.allow_decimal || v.includes("."))) return v;
      if (k === "-" && (!el.allow_negative || v.length > 0)) return v;
      if (v.replace(/[^0-9]/g, "").length >= max && k !== "." && k !== "-") return v;
      return v + k;
    });
  };

  const submit = async () => {
    if (!onRun || value === "" || busy) return;
    if (el.confirm && !window.confirm(el.confirm)) return;
    setBusy(true);
    try {
      const parsed = Number(value);
      await onRun(el.workflow_id, {
        ...(el.inputs ?? {}),
        [el.input_name ?? "value"]: Number.isFinite(parsed) ? parsed : value,
      });
      setValue("");
    } finally {
      setBusy(false);
    }
  };

  const keyClass =
    "min-h-14 rounded-xl border bg-background text-xl font-medium " +
    "transition-all duration-150 active:scale-[0.97] disabled:opacity-50";

  return (
    <div className="w-full max-w-xs">
      {el.label ? (
        <p className="mb-1 text-sm font-medium text-muted-foreground">{el.label}</p>
      ) : null}
      {/* The readout is the committed value, not a text field: this control is
          for a screen where the keyboard is the pad itself. */}
      <div
        className="mb-2 rounded-lg border bg-background px-3 py-2 text-right font-mono text-2xl"
        aria-live="polite"
      >
        {value || <span className="text-muted-foreground">{el.placeholder ?? "—"}</span>}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {KEYS.map((k) => (
          <button key={k} type="button" className={keyClass} disabled={disabled} onClick={() => press(k)}>
            {k}
          </button>
        ))}
        <button
          type="button"
          className={keyClass}
          disabled={disabled}
          onClick={() => (el.allow_decimal ? press(".") : press("-"))}
        >
          {el.allow_decimal ? "." : el.allow_negative ? "−" : ""}
        </button>
        <button key="0" type="button" className={keyClass} disabled={disabled} onClick={() => press("0")}>
          0
        </button>
        <button
          type="button"
          className={keyClass}
          disabled={disabled}
          onClick={() => setValue((v) => v.slice(0, -1))}
          aria-label="Delete"
        >
          ⌫
        </button>
      </div>
      <button
        type="button"
        className="mt-2 inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-xl bg-primary px-6 text-lg font-medium text-primary-foreground transition-all duration-150 hover:bg-primary/90 active:scale-[0.97] disabled:opacity-60"
        disabled={disabled || busy || value === ""}
        onClick={() => void submit()}
      >
        {busy ? <Loader2 className="h-5 w-5 animate-spin" /> : null}
        {el.submit_label ?? "Enter"}
      </button>
    </div>
  );
}
