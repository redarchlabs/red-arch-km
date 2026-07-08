"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { HelpTopic } from "@/lib/help";

interface HelpContextValue {
  /** Whether the help rail/drawer is visible. */
  open: boolean;
  setOpen: (value: boolean) => void;
  toggle: () => void;
  /**
   * Item-level help override. Interactive surfaces (the form/view builder, admin
   * tabs, the entity editor) push the topic for the thing the user is working on;
   * the dock shows it in preference to route/node help. Consumers set it on a
   * user action (focus / tab select) and clear it (`null`) on unmount — there is
   * deliberately NO provider-level auto-clear, so a consumer's mount effect can't
   * race a route-change clear.
   */
  override: HelpTopic | null;
  setOverride: (topic: HelpTopic | null) => void;
}

const HelpContext = createContext<HelpContextValue | null>(null);

/** Stable no-op so `useHelpOverride`'s deps don't churn outside a provider. */
const NOOP = () => {};

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
  const [override, setOverride] = useState<HelpTopic | null>(null);

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
    <HelpContext.Provider
      value={{ open, setOpen, toggle: () => setOpen((o) => !o), override, setOverride }}
    >
      {children}
    </HelpContext.Provider>
  );
}

export function useHelp(): HelpContextValue {
  const ctx = useContext(HelpContext);
  if (!ctx) throw new Error("useHelp must be used within a HelpProvider");
  return ctx;
}

/**
 * Non-throwing accessor for the help-override setter, for interactive surfaces
 * that want to push item-level help. Returns a stable no-op if there is no
 * provider, so components stay usable outside the authenticated layout.
 */
export function useHelpOverride(): (topic: HelpTopic | null) => void {
  return useContext(HelpContext)?.setOverride ?? NOOP;
}
