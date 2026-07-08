"use client";

import { Input } from "@/components/ui/input";
import type { TriggerInput } from "@/lib/api/workflows";

/**
 * The value-entry form for a manual (on-demand) workflow's declared inputs.
 * Shared by the dry-run Test panel and the real Run panel so both stay in sync.
 */
export function ManualRunInputs({
  inputs,
  values,
  onChange,
}: {
  inputs: TriggerInput[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  if (inputs.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        This trigger declares no inputs — it just runs. Add input variables on the trigger to collect
        values here.
      </p>
    );
  }
  return (
    <div className="space-y-3">
      {inputs.map((spec) => (
        <div key={spec.key} className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            {spec.label}
            {spec.required ? <span className="text-destructive"> *</span> : null}
          </label>
          {spec.type === "boolean" ? (
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={Boolean(values[spec.key])}
                onChange={(e) => onChange(spec.key, e.target.checked)}
              />
              {spec.key}
            </label>
          ) : (
            <Input
              type={spec.type === "number" ? "number" : "text"}
              value={(values[spec.key] as string | number | undefined) ?? ""}
              onChange={(e) => {
                if (spec.type === "number") {
                  // An empty number field is "unset", not NaN.
                  onChange(spec.key, e.target.value === "" ? undefined : e.target.valueAsNumber);
                } else {
                  onChange(spec.key, e.target.value);
                }
              }}
              placeholder={spec.key}
            />
          )}
        </div>
      ))}
    </div>
  );
}

/** True if any required input is still empty (disables the run/test button). */
export function missingRequiredInputs(inputs: TriggerInput[], values: Record<string, unknown>): boolean {
  return inputs.some((i) => i.required && (values[i.key] === undefined || values[i.key] === ""));
}

/** Only the declared keys the user actually set — what gets POSTed as `inputs`. */
export function collectDeclaredInputs(
  inputs: TriggerInput[],
  values: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const spec of inputs) if (values[spec.key] !== undefined) out[spec.key] = values[spec.key];
  return out;
}
