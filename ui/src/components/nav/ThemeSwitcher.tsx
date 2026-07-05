"use client";

import { Check, Palette } from "lucide-react";
import { useRef, useState } from "react";

import { useTheme } from "@/context/ThemeContext";
import { THEME_LABELS, THEMES, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const close = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const choose = (t: Theme) => {
    setTheme(t);
    close();
  };

  return (
    // Plain popover of buttons (not ARIA menu semantics — those would demand
    // full roving-focus keyboard behavior). Escape closes and restores focus.
    <div
      className="relative"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          close();
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Change theme"
        aria-expanded={open}
        className="inline-flex h-9 w-9 items-center justify-center rounded-md hover:bg-accent hover:text-accent-foreground"
      >
        <Palette className="h-4 w-4" />
      </button>
      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={close} aria-hidden />
          <div className="absolute right-0 z-20 mt-1 w-40 overflow-hidden rounded-md border bg-background shadow-md">
            {THEMES.map((t, index) => (
              <button
                key={t}
                type="button"
                autoFocus={index === 0}
                aria-pressed={t === theme}
                onClick={() => choose(t)}
                className={cn(
                  "flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-accent",
                  t === theme && "bg-accent font-medium",
                )}
              >
                {THEME_LABELS[t]}
                {t === theme ? <Check className="h-4 w-4" /> : null}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
