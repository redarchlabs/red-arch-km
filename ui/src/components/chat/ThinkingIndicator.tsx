"use client";

interface ThinkingIndicatorProps {
  /** What the assistant is currently doing, e.g. "Searching your documents…". */
  label: string;
}

/**
 * Shown while an assistant reply has been requested but no text has streamed
 * back yet: three walking dots plus what the system is currently doing, so the
 * wait reads as progress rather than a stalled screen.
 */
export function ThinkingIndicator({ label }: ThinkingIndicatorProps) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 py-0.5">
      <span aria-hidden="true" className="flex items-end gap-1">
        {[0, 160, 320].map((delay) => (
          <span
            key={delay}
            className="h-2 w-2 animate-bounce rounded-full bg-primary/70"
            style={{ animationDelay: `${delay}ms`, animationDuration: "1.1s" }}
          />
        ))}
      </span>
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
    </div>
  );
}
