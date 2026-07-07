"use client";

/**
 * Drop-to-place: converts a palette drag's screen coordinates into flow
 * coordinates with `screenToFlowPosition` and adds the node exactly where it was
 * dropped — killing the old random-placement behaviour. Must be used inside a
 * `<ReactFlowProvider>`.
 */
import { useReactFlow } from "@xyflow/react";
import { useCallback, type DragEvent } from "react";

import { useDesignerStore } from "./store";

export const DRAG_MIME = "application/x-km2-node";

export interface DragPayload {
  type: string;
  data?: Record<string, unknown>;
}

/** Serialise a palette item onto a drag event. */
export function setDragPayload(e: DragEvent, payload: DragPayload): void {
  e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
  e.dataTransfer.effectAllowed = "move";
}

export function useDragAndDrop() {
  const { screenToFlowPosition } = useReactFlow();
  const addNode = useDesignerStore((s) => s.addNode);

  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData(DRAG_MIME);
      if (!raw) return;
      let payload: DragPayload;
      try {
        payload = JSON.parse(raw) as DragPayload;
      } catch {
        return;
      }
      if (!payload?.type) return;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      addNode(payload.type, position, payload.data);
    },
    [screenToFlowPosition, addNode],
  );

  return { onDragOver, onDrop };
}
