"use client";

/**
 * Categorized, draggable palette. Drag an item onto the canvas to drop it
 * exactly where you release (see {@link useDragAndDrop}); or click it to drop at
 * the centre of the current view. Every item carries its concrete subtype.
 */
import { useReactFlow } from "@xyflow/react";
import { type DragEvent } from "react";

import { cn } from "@/lib/utils";

import { PALETTE_GROUPS, type PaletteItem } from "./paletteItems";
import { useDesignerStore } from "./store";
import { setDragPayload } from "./useDragAndDrop";

export function NodePalette({ className }: { className?: string }) {
  const addNode = useDesignerStore((s) => s.addNode);
  const selectNode = useDesignerStore((s) => s.selectNode);
  const { screenToFlowPosition } = useReactFlow();

  const onDragStart = (e: DragEvent, item: PaletteItem) => {
    setDragPayload(e, { type: item.type, data: item.makeData() });
  };

  const onClick = (item: PaletteItem) => {
    // Fallback for non-drag input: drop near the middle of the viewport.
    const position = screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
    const created = addNode(item.type, position, item.makeData());
    selectNode(created.id);
  };

  return (
    <div className={cn("space-y-4 overflow-y-auto rounded-lg border bg-card p-3", className)}>
      <p className="text-xs text-muted-foreground">Drag onto the canvas, or click to place.</p>
      {PALETTE_GROUPS.map((group) => (
        <div key={group.category} className="space-y-1.5">
          <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{group.label}</h4>
          <div className="grid grid-cols-1 gap-1.5">
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.key}
                  type="button"
                  draggable
                  onDragStart={(e) => onDragStart(e, item)}
                  onClick={() => onClick(item)}
                  title={item.hint ?? item.label}
                  className="flex cursor-grab items-center gap-2 rounded-md border bg-background px-2 py-1.5 text-left text-sm shadow-sm transition-colors hover:border-primary/60 hover:bg-accent active:cursor-grabbing"
                >
                  <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="truncate">{item.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
