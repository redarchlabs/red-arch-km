"use client";

import { HelpCircle } from "lucide-react";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { Button } from "@/components/ui/button";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { helpForPath } from "@/lib/help";

/**
 * Header help button. Opens a dialog with help for the CURRENT route — the
 * content is resolved from the pathname, so it's always relevant to what the
 * user is looking at.
 */
export function HelpButton() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const topic = helpForPath(pathname ?? "/");

  return (
    <>
      <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Help">
        <HelpCircle className="h-4 w-4" />
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{topic.title}</DialogTitle>
        </DialogHeader>
        <Markdown content={topic.body} />
      </Dialog>
    </>
  );
}
