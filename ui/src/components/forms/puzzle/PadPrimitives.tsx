"use client";

import type { ReactNode } from "react";

/**
 * Shared touch furniture for the puzzle pads.
 *
 * Every size here is set for a finger on a tablet rather than a cursor on a
 * desktop: the smallest interactive target is 64px tall, well above the ~44px
 * platform minimum, because the people using these pads are often children and a
 * mis-tap in the middle of a timed mission reads as the game being broken.
 */

/** The primary action on pads that confirm rather than submit-on-tap. */
export function PadAction({
  label,
  onClick,
  disabled,
  tone = "primary",
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "primary" | "quiet";
}) {
  const styles =
    tone === "primary"
      ? "bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80 shadow-sm"
      : "border bg-background text-muted-foreground hover:bg-muted active:bg-muted/80";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`min-h-16 rounded-2xl px-8 text-xl font-semibold transition-all duration-150 active:scale-[0.97] disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring ${styles}`}
    >
      {label}
    </button>
  );
}

/** A large tappable tile — the shape used for choices, sequence steps and sort
 * items. `state` drives the visual, never the layout, so a tile never resizes
 * under a finger when it becomes selected. */
export function PadTile({
  children,
  onClick,
  disabled,
  state = "idle",
  className = "",
  ...rest
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  state?: "idle" | "selected" | "done" | "muted";
  className?: string;
} & Omit<React.HTMLAttributes<HTMLButtonElement>, "children" | "onClick">) {
  const states: Record<string, string> = {
    idle: "border-2 border-border bg-card hover:border-primary/60 hover:bg-accent",
    selected: "border-2 border-primary bg-primary/10 ring-4 ring-primary/20",
    done: "border-2 border-green-500 bg-green-500/10",
    muted: "border-2 border-dashed border-border bg-muted/40 text-muted-foreground",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`flex min-h-20 items-center justify-center gap-3 rounded-2xl p-4 text-center text-xl font-semibold leading-snug transition-all duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring ${states[state]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

/** The row under every pad: reset on the left, the confirm action on the right. */
export function PadFooter({
  onReset,
  resetDisabled,
  children,
}: {
  onReset?: () => void;
  resetDisabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 pt-2">
      {onReset ? (
        <button
          type="button"
          onClick={onReset}
          disabled={resetDisabled}
          className="mr-auto min-h-12 rounded-xl px-4 text-base font-medium text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
        >
          Start over
        </button>
      ) : null}
      {children}
    </div>
  );
}

/** A numbered badge — the order marker on sequence steps. */
export function OrderBadge({ n }: { n: number }) {
  return (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-base font-bold text-primary-foreground">
      {n}
    </span>
  );
}
