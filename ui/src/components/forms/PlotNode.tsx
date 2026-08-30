"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { PlotElement, PlotSeries } from "@/lib/api/forms";
import { listRecords, type EntityRecord } from "@/lib/api/entityRecords";

/**
 * Records plotted on a coordinate space.
 *
 * A table tells you a contact is at bearing 045, 4,280km away. A plot tells you
 * it is close, off the starboard bow, and closing on the two behind it — which
 * is the question an operator actually asks, and the reason this is an element
 * rather than a styling option on `record_list`.
 *
 * Drawn on a 2D canvas at device pixel ratio. Colours are read from the CSS
 * custom properties in scope, so a plot inherits the view's theme and its
 * state-driven repainting for free rather than carrying its own palette.
 */

const DEG = Math.PI / 180;

interface Point {
  x: number;
  y: number;
  label: string;
  color: string;
}

function num(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Resolve a themed colour by reading the custom property off the live element,
 * so the plot follows the view's palette (including an alert repaint). */
function themed(el: HTMLElement | null, prop: string, fallback: string): string {
  if (!el) return fallback;
  const v = getComputedStyle(el).getPropertyValue(prop).trim();
  return v || fallback;
}

export function PlotNode({ el }: { el: PlotElement }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [rows, setRows] = useState<Record<number, EntityRecord[]>>({});
  const [failed, setFailed] = useState(false);

  const series = useMemo(() => el.series ?? [], [el.series]);
  const seriesKey = JSON.stringify(series);
  const pollMs = el.poll_ms ?? null;

  // Fetch each series. A failure is held rather than thrown: these pages are
  // wall displays, and a blank panel that says so beats an error boundary
  // swallowing the whole station.
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const results = await Promise.all(
          series.map((s: PlotSeries) =>
            listRecords(s.entity, {
              limit: s.limit ?? 50,
              filters: (s.filters ?? []).map((f) => ({
                field: f.field,
                op: f.op ?? "eq",
                value: f.value == null ? undefined : String(f.value),
              })),
            })
          )
        );
        if (!alive) return;
        const next: Record<number, EntityRecord[]> = {};
        results.forEach((r, i) => {
          next[i] = r.items ?? [];
        });
        setRows(next);
        setFailed(false);
      } catch {
        if (alive) setFailed(true);
      }
    };
    void load();
    if (!pollMs) return () => {
      alive = false;
    };
    const timer = setInterval(() => {
      // A hidden tab should not keep polling; the next paint refetches.
      if (typeof document === "undefined" || !document.hidden) void load();
    }, pollMs);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesKey, pollMs]);

  // Project every row into plot space once per data change, so the animation
  // loop below only paints.
  const points = useMemo(() => {
    const out: Point[] = [];
    series.forEach((s, i) => {
      for (const row of rows[i] ?? []) {
        const label = s.point_label ? String(row[s.point_label] ?? "") : "";
        const category = s.category ? String(row[s.category] ?? "") : "";
        const color = (s.colors ?? {})[category] || s.color || "";
        if (el.mode === "cartesian") {
          const x = num(row[s.x ?? ""]);
          const y = num(row[s.y ?? ""]);
          if (x == null || y == null) continue;
          out.push({ x, y, label, color });
        } else if (s.x && s.y) {
          // Polar scope fed cartesian offsets. Bearing-and-distance is the
          // natural spelling for a scope, but plenty of data carries relative
          // x/y instead and converting it in the source would mean storing a
          // derived column purely so a plot could read it.
          const x = num(row[s.x]);
          const y = num(row[s.y]);
          if (x == null || y == null) continue;
          out.push({ x, y: -y, label, color });
        } else {
          const a = num(row[s.angle ?? ""]);
          const r = num(row[s.radius ?? ""]);
          if (a == null || r == null) continue;
          // Bearing is clockwise from up; canvas angles run counter-clockwise
          // from +x, hence the offset and the sign on y.
          out.push({
            x: r * Math.sin(a * DEG),
            y: -r * Math.cos(a * DEG),
            label,
            color,
          });
        }
      }
    });
    return out;
  }, [rows, series, el.mode]);

  const extent = useMemo(() => {
    if (el.mode === "cartesian") {
      const xs = points.map((p) => p.x);
      const ys = points.map((p) => p.y);
      return {
        xMin: el.x_min ?? (xs.length ? Math.min(...xs) : -1),
        xMax: el.x_max ?? (xs.length ? Math.max(...xs) : 1),
        yMin: el.y_min ?? (ys.length ? Math.min(...ys) : -1),
        yMax: el.y_max ?? (ys.length ? Math.max(...ys) : 1),
      };
    }
    const far = points.length ? Math.max(...points.map((p) => Math.hypot(p.x, p.y))) : 1;
    const r = el.max_radius ?? (far > 0 ? far * 1.08 : 1);
    return { xMin: -r, xMax: r, yMin: -r, yMax: r };
  }, [points, el]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    let raf = 0;
    const start = performance.now();

    const draw = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const w = host.clientWidth || 300;
      const h = el.height ?? 320;
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const ink = themed(host, "--color-border", "#334155");
      const fg = themed(host, "--color-muted-foreground", "#94a3b8");
      const accent = themed(host, "--color-primary", "#22d3ee");

      const pad = 12;
      const cx = w / 2;
      const cy = h / 2;
      const spanX = extent.xMax - extent.xMin || 1;
      const spanY = extent.yMax - extent.yMin || 1;
      const scale = Math.min((w - pad * 2) / spanX, (h - pad * 2) / spanY);
      const toPx = (p: { x: number; y: number }) => ({
        px: cx + (p.x - (extent.xMin + extent.xMax) / 2) * scale,
        py: cy + (p.y - (extent.yMin + extent.yMax) / 2) * scale,
      });

      ctx.lineWidth = 1;
      ctx.strokeStyle = ink;

      if (el.mode === "polar") {
        const outer = Math.min(w, h) / 2 - pad;
        const ringCount = el.rings ?? 4;
        for (let i = 1; i <= ringCount; i++) {
          ctx.beginPath();
          ctx.arc(cx, cy, (outer * i) / ringCount, 0, Math.PI * 2);
          ctx.stroke();
        }
        // Bearing spokes every 45°, which is how a bearing is actually called.
        for (let a = 0; a < 360; a += 45) {
          ctx.beginPath();
          ctx.moveTo(cx, cy);
          ctx.lineTo(cx + outer * Math.sin(a * DEG), cy - outer * Math.cos(a * DEG));
          ctx.stroke();
        }
        const sweep = el.sweep_seconds ?? 0;
        if (sweep > 0) {
          const theta = (((performance.now() - start) / (sweep * 1000)) % 1) * Math.PI * 2;
          const grad = ctx.createConicGradient?.(theta - Math.PI / 2, cx, cy);
          if (grad) {
            grad.addColorStop(0, `${accent}00`);
            grad.addColorStop(0.08, `${accent}55`);
            grad.addColorStop(0.12, `${accent}00`);
            ctx.fillStyle = grad;
            ctx.beginPath();
            ctx.arc(cx, cy, outer, 0, Math.PI * 2);
            ctx.fill();
          } else {
            // No conic gradient (older engines): a plain radial line still reads
            // as a sweep and costs nothing.
            ctx.strokeStyle = accent;
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(cx + outer * Math.cos(theta), cy + outer * Math.sin(theta));
            ctx.stroke();
          }
        }
      } else {
        // Cartesian: axes through the origin if it is in view, else a border.
        ctx.beginPath();
        ctx.rect(pad, pad, w - pad * 2, h - pad * 2);
        ctx.stroke();
      }

      ctx.font = "11px system-ui, sans-serif";
      for (const p of points) {
        const { px, py } = toPx(p);
        ctx.fillStyle = p.color || accent;
        ctx.beginPath();
        ctx.arc(px, py, 4, 0, Math.PI * 2);
        ctx.fill();
        if (el.show_labels !== false && p.label) {
          ctx.fillStyle = fg;
          ctx.fillText(p.label, px + 7, py + 3);
        }
      }

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [points, extent, el]);

  return (
    <div ref={hostRef} className="w-full">
      {el.label ? (
        <p className="mb-1 text-sm font-medium text-muted-foreground">{el.label}</p>
      ) : null}
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: el.height ?? 320 }}
        role="img"
        aria-label={el.label ?? "plot"}
      />
      {failed || points.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {failed ? "Could not read the plot's data." : (el.empty_text ?? "No contacts.")}
        </p>
      ) : null}
    </div>
  );
}
