"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

interface HelpContextValue {
  /** Whether the help rail/drawer is visible. */
  open: boolean;
  setOpen: (value: boolean) => void;
  toggle: () => void;
}

const HelpContext = createContext<HelpContextValue | null>(null);

/** Tailwind's `lg` breakpoint — at/above this we treat the viewport as desktop. */
const DESKTOP_QUERY = "(min-width: 1024px)";

/**
 * Shares the help panel's open state between the header toggle and the docked
 * rail. The rail is DOCKED (always shown) on desktop by default and hidden on
 * smaller viewports, where it opens as an overlay drawer instead.
 */
export function HelpProvider({ children }: { children: ReactNode }) {
  // Start closed so SSR and the mobile-first render agree; an effect opens the
  // dock on desktop after mount to avoid a hydration mismatch.
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia(DESKTOP_QUERY);
    // Dock open by default whenever we're on (or grow to) a desktop width; the
    // user can still collapse it. We only auto-open, never auto-close, so a
    // deliberate collapse isn't undone by a resize.
    if (mq.matches) setOpen(true);
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setOpen(true);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return (
    <HelpContext.Provider value={{ open, setOpen, toggle: () => setOpen((o) => !o) }}>
      {children}
    </HelpContext.Provider>
  );
}

export function useHelp(): HelpContextValue {
  const ctx = useContext(HelpContext);
  if (!ctx) throw new Error("useHelp must be used within a HelpProvider");
  return ctx;
}
