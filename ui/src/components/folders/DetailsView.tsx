"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import { List, type RowComponentProps } from "react-window";

import { formatBytes, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

import { DETAILS_COLS, DETAILS_ROW_H, type RowProps, type SortDir, type SortKey } from "./explorerItem";
import { ItemIcon, StatusBadge, useElementSize } from "./explorerUi";
import { useItemMenu } from "./useItemMenu";

export function DetailsView({
  items,
  sortKey,
  sortDir,
  onSort,
  onOpen,
  onAction,
  onReload,
}: RowProps & { sortKey: SortKey; sortDir: SortDir; onSort: (k: SortKey) => void }) {
  const { ref, height } = useElementSize();
  const header = (key: SortKey, label: string, alignRight?: boolean) => (
    <button
      type="button"
      onClick={() => onSort(key)}
      className={cn(
        "flex items-center gap-1 px-3 py-1.5 font-medium hover:text-foreground",
        alignRight ? "justify-end" : "text-left",
      )}
    >
      {label}
      {sortKey === key ? (
        sortDir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />
      ) : null}
    </button>
  );
  return (
    <div className="flex h-full flex-col text-sm">
      {/* Fixed header (the rows below scroll under it). */}
      <div
        className="grid shrink-0 items-center border-b bg-background text-xs text-muted-foreground"
        style={{ gridTemplateColumns: DETAILS_COLS }}
      >
        {header("name", "Name")}
        <div className="px-3 py-1.5 font-medium">Status</div>
        {header("type", "Type")}
        {header("modified", "Modified")}
        {header("size", "Size", true)}
      </div>
      <div ref={ref} className="min-h-0 flex-1">
        <List
          rowComponent={DetailsRow}
          rowCount={items.length}
          rowHeight={DETAILS_ROW_H}
          rowProps={{ items, onOpen, onAction, onReload }}
          style={{ height, width: "100%" }}
        />
      </div>
    </div>
  );
}

function DetailsRow({
  index,
  style,
  ariaAttributes,
  items,
  onOpen,
  onAction,
  onReload,
}: RowComponentProps<RowProps>) {
  const item = items[index]!;
  const { open, menu } = useItemMenu(item, onOpen, onAction, onReload);
  return (
    <div
      {...ariaAttributes}
      style={{ ...style, gridTemplateColumns: DETAILS_COLS }}
      onDoubleClick={() => onOpen(item)}
      onContextMenu={open}
      className="grid cursor-default items-center border-b border-border/50 text-sm hover:bg-accent/50"
    >
      <span className="flex min-w-0 items-center gap-2 px-3">
        <ItemIcon item={item} className="h-4 w-4 shrink-0" />
        <span className="truncate">{item.name}</span>
        {menu}
      </span>
      <span className="px-3">{item.status ? <StatusBadge status={item.status} /> : null}</span>
      <span className="truncate px-3 text-muted-foreground">{item.type}</span>
      <span className="truncate px-3 text-muted-foreground">
        {item.modified ? formatDate(item.modified) : "—"}
      </span>
      <span className="px-3 text-right text-muted-foreground">
        {item.size != null ? formatBytes(item.size) : "—"}
      </span>
    </div>
  );
}
