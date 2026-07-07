"use client";

/**
 * ⌘K / Ctrl+K command palette (built on the shared Dialog). Fuzzy-filter the
 * BPMN vocabulary to drop a node at the centre of the view, or run a canvas
 * command (undo / redo / fit view). Arrow keys navigate, Enter runs.
 */
import { useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";

import { Dialog } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import { PALETTE_ITEMS } from "./paletteItems";
import { useDesignerStore } from "./store";

interface Command {
  id: string;
  label: string;
  group: string;
  run: () => void;
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const { screenToFlowPosition, fitView } = useReactFlow();
  const addNode = useDesignerStore((s) => s.addNode);
  const selectNode = useDesignerStore((s) => s.selectNode);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const dropCenter = (type: string, data: Record<string, unknown>) => {
      const position = screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
      const created = addNode(type, position, data);
      selectNode(created.id);
    };
    const nodeCommands: Command[] = PALETTE_ITEMS.map((item) => ({
      id: item.key,
      label: `Add ${item.label}`,
      group: "Add node",
      run: () => dropCenter(item.type, item.makeData()),
    }));
    const actions: Command[] = [
      { id: "undo", label: "Undo", group: "Canvas", run: () => useDesignerStore.temporal.getState().undo() },
      { id: "redo", label: "Redo", group: "Canvas", run: () => useDesignerStore.temporal.getState().redo() },
      { id: "fit", label: "Fit view", group: "Canvas", run: () => fitView({ duration: 200 }) },
    ];
    return [...nodeCommands, ...actions];
  }, [addNode, selectNode, screenToFlowPosition, fitView]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(q));
  }, [commands, query]);

  const run = (cmd: Command | undefined) => {
    if (!cmd) return;
    cmd.run();
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(filtered[active]);
    }
  };

  return (
    <Dialog open={open} onClose={() => setOpen(false)} className="max-w-md p-0">
      <div className="border-b p-3">
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Add a node, or a command…"
          className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>
      <div className="max-h-72 overflow-y-auto p-1">
        {filtered.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-muted-foreground">No matches.</p>
        ) : (
          filtered.map((cmd, i) => (
            <button
              key={cmd.id}
              type="button"
              onMouseEnter={() => setActive(i)}
              onClick={() => run(cmd)}
              className={cn(
                "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm",
                i === active ? "bg-accent" : "hover:bg-accent/60",
              )}
            >
              <span>{cmd.label}</span>
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{cmd.group}</span>
            </button>
          ))
        )}
      </div>
    </Dialog>
  );
}
