"use client";

import { HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useHelp } from "@/context/HelpContext";

/**
 * Header help toggle. Shows/hides the shared {@link HelpDock} — which is docked
 * on the right in desktop mode and an overlay drawer on smaller screens. The
 * panel's content is resolved from the current route, so it's always relevant.
 */
export function HelpButton() {
  const { open, toggle } = useHelp();

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={open ? "Hide help" : "Show help"}
      aria-pressed={open}
    >
      <HelpCircle className="h-4 w-4" />
    </Button>
  );
}
