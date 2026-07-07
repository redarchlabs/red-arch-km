"use client";

/**
 * Canvas keyboard shortcuts, wired to the designer store:
 *   Delete/Backspace  delete selected nodes (boundary children cascade) + edges
 *   ⌘/Ctrl+C / V / D  copy · paste · duplicate (clipboard remaps ids + handles)
 *   ⌘/Ctrl+Z / ⇧Z / Y undo · redo
 * Multi-select (shift-drag / shift-click) is handled natively by React Flow.
 * The trigger node is never deleted here — a workflow must keep its start.
 */
import { useEffect } from "react";

import { useDesignerStore } from "./store";

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

export function useDesignerKeymap({ disabled = false }: { disabled?: boolean } = {}): void {
  useEffect(() => {
    if (disabled) return;

    const handler = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target)) return;
      const s = useDesignerStore.getState();
      const temporal = useDesignerStore.temporal.getState();
      const mod = e.metaKey || e.ctrlKey;
      const key = e.key.toLowerCase();

      if (!mod && (e.key === "Delete" || e.key === "Backspace")) {
        const nodeIds = s.nodes.filter((n) => n.selected && n.type !== "trigger").map((n) => n.id);
        const edgeIds = s.edges.filter((ed) => ed.selected).map((ed) => ed.id);
        if (nodeIds.length === 0 && edgeIds.length === 0) return;
        e.preventDefault();
        if (nodeIds.length > 0) s.deleteNodes(nodeIds);
        if (edgeIds.length > 0) s.deleteEdges(edgeIds);
        return;
      }

      if (!mod) return;

      if (key === "c") {
        s.copySelection();
      } else if (key === "v") {
        e.preventDefault();
        s.paste();
      } else if (key === "d") {
        e.preventDefault();
        s.duplicateSelection();
      } else if (key === "z") {
        e.preventDefault();
        if (e.shiftKey) temporal.redo();
        else temporal.undo();
      } else if (key === "y") {
        e.preventDefault();
        temporal.redo();
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [disabled]);
}
