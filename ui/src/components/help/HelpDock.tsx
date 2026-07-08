"use client";

import { HelpCircle, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { Markdown } from "@/components/common/Markdown";
import { useDesignerStore } from "@/components/workflows/designer/store";
import { useHelp } from "@/context/HelpContext";
import { helpForPath } from "@/lib/help";
import { helpForNode } from "@/lib/nodeHelp";

/** Workflow designer route (`/workflows/<id>/design`) — where node help applies. */
const DESIGN_ROUTE = /^\/workflows\/[^/]+\/design(\/|$)/;

/**
 * Context-sensitive help panel. It resolves the CURRENT route to the most
 * specific topic, so its content always matches what the user is looking at.
 *
 * Two presentations from one open state (shared via {@link useHelp}):
 *  - **Desktop (lg+)**: a DOCKED rail on the right that is part of the flex
 *    layout — always shown by default, collapsible with the header ? button.
 *  - **Mobile**: an overlay drawer with a backdrop; Escape or the backdrop
 *    closes it.
 */
export function HelpDock() {
  const { open, setOpen, override } = useHelp();
  const pathname = usePathname() ?? "/";
  // The designer store is a global singleton (empty + reset off-route), so this
  // is safe to read on every page; we only consult it on the design route.
  const selectedNode = useDesignerStore((s) => s.nodes.find((n) => n.selected) ?? null);
  const nodeTopic = DESIGN_ROUTE.test(pathname) ? helpForNode(selectedNode) : null;
  // Precedence: an explicit item override (builder element, admin tab, …) wins,
  // then designer node help, then route help.
  const itemTopic = override ?? nodeTopic;
  const topic = itemTopic ?? helpForPath(pathname);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  if (!open) return null;

  const body = (
    <>
      <header className="flex items-center gap-2 border-b px-4 py-3">
        <HelpCircle className="h-4 w-4 shrink-0 text-muted-foreground" />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold" title={topic.title}>
          {topic.title}
        </h2>
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
        <p className="mt-6 border-t pt-3 text-xs text-muted-foreground">
          {itemTopic ? (
            <>
              Showing help for what you have selected. Pick another item — or
              click empty space — to change it.
            </>
          ) : (
            <>
              Help updates as you move between pages. Reopen it any time with the{" "}
              <span className="font-medium">?</span> button.
            </>
          )}
        </p>
      </div>
    </>
  );

  return (
    <>
      {/* Desktop: docked rail — part of the layout, pushes content left. */}
      <aside
        aria-label={topic.title}
        className="hidden w-80 shrink-0 flex-col border-l bg-background lg:flex"
      >
        {body}
      </aside>

      {/* Mobile / tablet: overlay drawer with backdrop. */}
      <div className="lg:hidden">
        <div
          className="fixed inset-0 z-50 bg-black/20"
          onClick={() => setOpen(false)}
          aria-hidden
        />
        <aside
          role="dialog"
          aria-modal="true"
          aria-label={topic.title}
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-xl"
        >
          {body}
        </aside>
      </div>
    </>
  );
}
