"use client";

import { HelpCircle, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { Button } from "@/components/ui/button";
import { helpForPath } from "@/lib/help";

/**
 * Header help button. Opens a full-height panel on the RIGHT with help for the
 * CURRENT route — content is resolved from the pathname, so it's always
 * relevant to what the user is looking at.
 */
export function HelpButton() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const topic = helpForPath(pathname ?? "/");

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Help">
        <HelpCircle className="h-4 w-4" />
      </Button>

      {open ? (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-50 bg-black/20"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          {/* Right-side panel */}
          <aside
            role="dialog"
            aria-modal="true"
            aria-label={topic.title}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-xl"
          >
            <header className="flex items-center justify-between border-b px-4 py-3">
              <h2 className="text-base font-semibold">{topic.title}</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close help"
                className="rounded-sm p-1 text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <Markdown content={topic.body} />
            </div>
          </aside>
        </>
      ) : null}
    </>
  );
}
