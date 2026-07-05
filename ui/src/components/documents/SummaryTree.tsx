"use client";

import DOMPurify from "dompurify";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import type { SummaryTreeNode } from "@/lib/api/documents";

/** Strip any HTML from summary text — source docs could contain accidental markup. */
function sanitize(text: string): string {
  return DOMPurify.sanitize(text, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
}

interface SummaryTreeItemProps {
  node: SummaryTreeNode;
  /** Depth in the tree; the root is 0. Deeper nodes start collapsed. */
  depth: number;
}

function SummaryTreeItem({ node, depth }: SummaryTreeItemProps) {
  const hasChildren = node.children.length > 0;
  // Root and its immediate children are expanded by default; deeper levels
  // start collapsed to keep large documents scannable.
  const [expanded, setExpanded] = useState(depth < 1);

  return (
    <li>
      <div className="flex items-start gap-1.5">
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse" : "Expand"}
            className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </button>
        ) : (
          <span className="mt-0.5 inline-block h-4 w-4 shrink-0" aria-hidden />
        )}
        <div className="whitespace-pre-wrap text-sm">{sanitize(node.summary)}</div>
      </div>
      {hasChildren && expanded ? (
        <ul className="ml-3 mt-2 space-y-2 border-l pl-3">
          {node.children.map((child, idx) => (
            <SummaryTreeItem key={idx} node={child} depth={depth + 1} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

interface SummaryTreeProps {
  root: SummaryTreeNode;
}

/** Recursive, collapsible view of a document's hierarchical summary tree. */
export function SummaryTree({ root }: SummaryTreeProps) {
  return (
    <ul className="space-y-2">
      <SummaryTreeItem node={root} depth={0} />
    </ul>
  );
}
