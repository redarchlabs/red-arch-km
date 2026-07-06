"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  icon?: React.ReactNode;
  destructive?: boolean;
}

interface Anchor {
  x: number;
  y: number;
}

/**
 * Shared row-actions menu that opens at the pointer for BOTH a right-click on a
 * row and a click on a "⋯" button — call the returned `open` from either.
 * `menu` is a portal-rendered popover; drop it anywhere inside the row.
 *
 *   const { open, menu } = useRowMenu(items);
 *   <div onContextMenu={open}>
 *     <button onClick={open} aria-label="Actions"><MoreVertical /></button>
 *     {menu}
 *   </div>
 */
export function useRowMenu(items: MenuItem[]): {
  open: (e: React.MouseEvent) => void;
  menu: React.ReactNode;
} {
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const open = (e: React.MouseEvent) => {
    // Suppress the native context menu and stop the row's own click (e.g. a
    // navigation Link wrapping the row) from firing.
    e.preventDefault();
    e.stopPropagation();
    setAnchor({ x: e.clientX, y: e.clientY });
  };

  const menu = anchor ? (
    <MenuPopover anchor={anchor} items={items} onClose={() => setAnchor(null)} />
  ) : null;

  return { open, menu };
}

interface MenuPopoverProps {
  anchor: Anchor;
  items: MenuItem[];
  onClose: () => void;
}

const MENU_WIDTH = 180;

function MenuPopover({ anchor, items, onClose }: MenuPopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    // Capture phase so a click elsewhere closes before it does anything else.
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onClose);
    window.addEventListener("scroll", onClose, true);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose]);

  if (!mounted) return null;

  // Keep the menu inside the viewport (flip left / clamp bottom near edges).
  const left =
    anchor.x + MENU_WIDTH > window.innerWidth ? Math.max(8, anchor.x - MENU_WIDTH) : anchor.x;
  const maxTop = window.innerHeight - 8 - items.length * 36;
  const top = Math.min(anchor.y, Math.max(8, maxTop));

  return createPortal(
    <div
      ref={ref}
      role="menu"
      style={{ position: "fixed", top, left, width: MENU_WIDTH }}
      className="z-[60] overflow-hidden rounded-md border bg-background p-1 shadow-md"
      onClick={(e) => e.stopPropagation()}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          onClick={() => {
            onClose();
            item.onSelect();
          }}
          className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent ${
            item.destructive ? "text-destructive" : "text-foreground"
          }`}
        >
          {item.icon}
          {item.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}
