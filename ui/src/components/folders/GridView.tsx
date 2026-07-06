"use client";

import { type CellComponentProps, Grid } from "react-window";

import { cn } from "@/lib/utils";

import {
  type ExplorerItem,
  LARGE_CELL_H,
  LIST_ROW_H,
  MIN_CELL_W,
  type RowProps,
  SMALL_CELL_H,
  type ViewMode,
} from "./explorerItem";
import { ItemIcon, StatusBadge, useElementSize } from "./explorerUi";
import { useItemMenu } from "./useItemMenu";

interface CellProps extends RowProps {
  columnCount: number;
  view: ViewMode;
}

/** Virtualized grid for the list / small-icon / large-icon views. */
export function GridView({ items, view, onOpen, onAction, onReload }: RowProps & { view: ViewMode }) {
  const { ref, width, height } = useElementSize();
  const kind = view as "list" | "small" | "large";
  const minCol = MIN_CELL_W[kind];
  const rowHeight = view === "list" ? LIST_ROW_H : view === "large" ? LARGE_CELL_H : SMALL_CELL_H;
  // Fit as many min-width columns as the measured width allows (≥ 1).
  const columnCount = Math.max(1, Math.floor((width || minCol) / minCol));
  const rowCount = Math.ceil(items.length / columnCount);
  return (
    <div ref={ref} className="h-full">
      <Grid
        cellComponent={GridCell}
        cellProps={{ items, columnCount, view, onOpen, onAction, onReload }}
        columnCount={columnCount}
        columnWidth={`${100 / columnCount}%`}
        rowCount={rowCount}
        rowHeight={rowHeight}
        style={{ height, width: "100%" }}
      />
    </div>
  );
}

function GridCell({
  rowIndex,
  columnIndex,
  style,
  items,
  columnCount,
  view,
  onOpen,
  onAction,
  onReload,
}: CellComponentProps<CellProps>) {
  const item = items[rowIndex * columnCount + columnIndex];
  if (!item) return <div style={style} />; // trailing slot on the last row
  return (
    <div style={style} className="p-1">
      <IconCell item={item} view={view} onOpen={onOpen} onAction={onAction} onReload={onReload} />
    </div>
  );
}

function IconCell({
  item,
  view,
  onOpen,
  onAction,
  onReload,
}: { item: ExplorerItem; view: ViewMode } & Omit<RowProps, "items">) {
  const { open, menu } = useItemMenu(item, onOpen, onAction, onReload);
  const large = view === "large";
  if (view === "list") {
    return (
      <button
        type="button"
        onDoubleClick={() => onOpen(item)}
        onContextMenu={open}
        className="flex h-full w-full items-center gap-2 rounded px-2 text-left text-sm hover:bg-accent/50"
      >
        <ItemIcon item={item} className="h-4 w-4 shrink-0" />
        <span className="truncate">{item.name}</span>
        {item.status ? <StatusBadge status={item.status} /> : null}
        {menu}
      </button>
    );
  }
  return (
    <button
      type="button"
      onDoubleClick={() => onOpen(item)}
      onContextMenu={open}
      className="flex h-full w-full flex-col items-center gap-1 rounded p-2 text-center hover:bg-accent/50"
    >
      <ItemIcon item={item} className={large ? "h-12 w-12" : "h-8 w-8"} />
      <span className={cn("w-full truncate", large ? "text-sm" : "text-xs")}>{item.name}</span>
      {item.status && item.status !== "SUCCESS" ? <StatusBadge status={item.status} /> : null}
      {menu}
    </button>
  );
}
