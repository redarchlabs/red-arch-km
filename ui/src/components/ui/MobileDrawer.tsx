"use client";

import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/utils";

interface MobileDrawerProps {
  /** Whether the drawer is open. Renders nothing when false. */
  open: boolean;
  onClose: () => void;
  /** Edge the drawer slides in from. Defaults to "left". */
  side?: "left" | "right";
  /** Accessible label for the dialog. */
  label: string;
  /** Width / sizing override, e.g. "w-72 max-w-[80%]". */
  className?: string;
  children: ReactNode;
}

/**
 * Reusable mobile slide-over: a full-height overlay panel with a dimmed
 * backdrop. Centralizes the backdrop / z-index / Escape-to-close / body scroll
 * lock behavior that {@link HelpDock} originally hand-rolled, so every mobile
 * drawer (nav sidebar, chat sessions, folder tree) behaves identically.
 *
 * Callers pair this with a `hidden lg:flex` (or md) docked panel for desktop
 * and render this only below that breakpoint.
 */
export function MobileDrawer({
  open,
  onClose,
  side = "left",
  label,
  className,
  children,
}: MobileDrawerProps) {
  // Escape closes the drawer; body scroll is locked while it is open so the
  // page behind the backdrop doesn't scroll on touch.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 bg-black/20"
        onClick={onClose}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className={cn(
          "fixed inset-y-0 z-50 flex flex-col bg-background shadow-xl",
          side === "left" ? "left-0 border-r" : "right-0 border-l",
          className ?? "w-72 max-w-[80%]",
        )}
      >
        {children}
      </aside>
    </>
  );
}
