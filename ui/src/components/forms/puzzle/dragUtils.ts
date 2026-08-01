/** Shared pointer-drag helpers for the puzzle pads.
 *
 * Pointer events, not HTML5 drag-and-drop: the latter does not fire on touch at
 * all, so a tablet — the device these pads are built for — would silently have
 * no drag. Everything here works the same for a finger, a stylus and a mouse.
 */

export interface Point {
  x: number;
  y: number;
}

/** How far a pointer must travel before it counts as a drag rather than a tap.
 * Below this, a press-and-release is treated as a tap so the pads can offer
 * tap-then-tap as an equal alternative to dragging. */
export const DRAG_SLOP = 8;

export function distance(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * The nearest ancestor of whatever is under the pointer that matches `selector`.
 *
 * Used to find the drop target on release. Any element that follows the pointer
 * (a drag ghost) MUST be `pointer-events: none`, or it sits under the pointer
 * and every drop lands on the thing being dragged.
 */
export function hitTest(x: number, y: number, selector: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const el = document.elementFromPoint(x, y);
  return el instanceof Element ? (el.closest(selector) as HTMLElement | null) : null;
}

/** Read a numeric `data-` attribute off a hit-tested element; -1 when absent or
 * malformed, which every caller already treats as "no target". */
export function dataIndex(el: HTMLElement | null, attr: string): number {
  const raw = el?.dataset[attr];
  const n = raw == null ? NaN : Number(raw);
  return Number.isInteger(n) ? n : -1;
}

/** Centre of `el` in the coordinate space of `container`. */
export function centerIn(el: HTMLElement, container: HTMLElement): Point {
  const box = el.getBoundingClientRect();
  const base = container.getBoundingClientRect();
  return { x: box.left - base.left + box.width / 2, y: box.top - base.top + box.height / 2 };
}

/** True when two point lists differ. Measurement runs in a layout effect and
 * writes state; without this guard the write would schedule another measure and
 * the component would never settle. */
export function pointsDiffer(a: Point[], b: Point[]): boolean {
  if (a.length !== b.length) return true;
  return a.some((p, i) => Math.abs(p.x - b[i].x) > 0.5 || Math.abs(p.y - b[i].y) > 0.5);
}
