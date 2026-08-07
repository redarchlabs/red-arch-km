"use client";

/**
 * Dependency-free SVG renderer for an aggregate result + visualization spec.
 *
 * Supports bar (grouped / stacked), line, area (stacked), pie / donut, scatter,
 * a plain table, and a single-KPI "metric" tile. Kept self-contained (no charting
 * dependency) so the app builds without extra packages. Chrome (legend, table,
 * tile) uses the shadcn design tokens so it themes with the rest of the app; only
 * the series colors — which are data-driven and can't be static classes — are
 * applied inline from a fixed, light/dark-readable palette.
 */
import { useMemo, useRef, useState } from "react";

import type { AggregateResult, NumberFormat, Visualization } from "@/lib/api/reports";
import { cn } from "@/lib/utils";

/** Series colors come from the theme's chart tokens (globals.css defines
 * `--color-chart-1..8` per theme), so the redarch brand theme gets brand-tuned
 * charts instead of a hard-coded blue/green. */
const PALETTE = Array.from({ length: 8 }, (_, i) => `var(--color-chart-${i + 1})`);

/** Deterministic category→color assignment: hash the category KEY, so a category
 * keeps its color across refreshes, filter changes and re-sorts (positional
 * assignment recolors everything whenever the visible set shifts). Collisions
 * within one chart probe to the next free slot while free slots remain. */
function assignColors(keys: string[]): Map<string, string> {
  const used = new Set<number>();
  const out = new Map<string, string>();
  for (const key of keys) {
    let h = 2166136261;
    for (let i = 0; i < key.length; i += 1) {
      h ^= key.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    let slot = (h >>> 0) % PALETTE.length;
    for (let tries = 0; tries < PALETTE.length && used.has(slot); tries += 1) {
      slot = (slot + 1) % PALETTE.length;
    }
    used.add(slot);
    out.set(key, PALETTE[slot]);
  }
  return out;
}

// Sentinel raw-key for a NULL group value (so distinct rows never collapse).
const NULL_KEY = "\u0000null";

interface Series {
  name: string;
  color: string;
  values: number[];
}

function toNum(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

function formatBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = n;
  while (Math.abs(v) >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatValue(
  v: unknown,
  fmt: NumberFormat = "plain",
  unit?: string | null,
  precision?: number | null,
): string {
  if (v == null || v === "") return "";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  // A caller-supplied precision (from the report's viz) overrides each format's
  // default fraction-digit cap, so a dashboard can pin e.g. "57.3%". Clamped to
  // the schema's 0–6 range; `null` keeps the per-format default below.
  const p = precision == null ? null : Math.max(0, Math.min(6, Math.trunc(precision)));
  let s: string;
  switch (fmt) {
    case "comma":
      s = n.toLocaleString(undefined, { maximumFractionDigits: p ?? 3 });
      break;
    case "currency": {
      // Large figures render compact ("$3.9M") so KPI tiles and axis labels stay
      // narrow; smaller amounts keep full cents.
      const big = Math.abs(n) >= 100_000;
      s = n.toLocaleString(undefined, {
        style: "currency",
        currency: "USD",
        notation: big ? "compact" : "standard",
        maximumFractionDigits: p ?? (big ? 1 : 2),
      });
      break;
    }
    case "percent":
      s = `${(n * 100).toLocaleString(undefined, { maximumFractionDigits: p ?? 1 })}%`;
      break;
    case "compact":
      s = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: p ?? 1 }).format(n);
      break;
    case "bytes":
      s = formatBytes(n);
      break;
    default:
      // Cap fraction digits so an aggregate like an average doesn't dump a
      // repeating decimal (e.g. 57.27272727272727); plain keeps no thousands
      // grouping. Integers stay exact since trailing zeros are dropped.
      s = n.toLocaleString(undefined, { maximumFractionDigits: p ?? 2, useGrouping: false });
  }
  return unit ? `${s}${unit}` : s;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Compact an axis label: date buckets render as "Apr '24" (or "Apr 15 '24" when
 * the day carries information); anything else is ellipsized past 12 chars. */
function shortLabel(c: string): string {
  const m = c.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?$/);
  if (m) {
    const mon = MONTHS[Number(m[2]) - 1] ?? m[2];
    const day = m[3] && m[3] !== "01" ? ` ${Number(m[3])}` : "";
    return `${mon}${day} '${m[1].slice(2)}`;
  }
  return c.length > 12 ? `${c.slice(0, 11)}…` : c;
}

/** Trim a date-trunc ISO timestamp to a readable label; pass other values through. */
function catLabel(v: unknown): string {
  if (v == null) return "—";
  const s = String(v);
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})T/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : s;
}

interface Derived {
  categories: string[]; // display labels, one per distinct raw category key
  series: Series[];
}

/** Build categories + series from the aggregate rows.
 *
 * Categories are keyed by the RAW group value (not the display label), so two
 * rows that render to the same label — e.g. an unbucketed timestamp truncated to
 * the same day — stay distinct instead of collapsing and dropping data. Rows are
 * indexed into a Map once (O(rows)) rather than re-scanned per cell.
 */
function derive(result: AggregateResult, viz: Visualization): Derived {
  const rows = result.rows;
  const xKey = viz.x ?? result.group_by[0] ?? null;
  const metricKeys = viz.series.length ? viz.series : result.metrics;
  const rawKey = (r: Record<string, unknown>): string => {
    if (!xKey) return "";
    const v = r[xKey];
    return v == null ? NULL_KEY : String(v);
  };

  const catKeys: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    const k = rawKey(r);
    if (!seen.has(k)) {
      seen.add(k);
      catKeys.push(k);
    }
  }
  const categories = catKeys.map((k) => (xKey ? catLabel(k === NULL_KEY ? null : k) : ""));

  if (viz.color_by && metricKeys.length >= 1 && xKey) {
    const metric = metricKeys[0];
    const groups: string[] = [];
    const gseen = new Set<string>();
    const byCatGroup = new Map<string, number>();
    for (const r of rows) {
      const g = String(r[viz.color_by as string]);
      if (!gseen.has(g)) {
        gseen.add(g);
        groups.push(g);
      }
      byCatGroup.set(`${rawKey(r)}${g}`, toNum(r[metric]));
    }
    // color_by groups are data values — hash-assign so a group keeps its color
    // as the visible set shifts. Metric series below stay positional: their
    // order is authored, not data-driven.
    const colorFor = assignColors(groups);
    const series = groups.map((g) => ({
      name: g,
      color: colorFor.get(g) ?? PALETTE[0],
      values: catKeys.map((k) => byCatGroup.get(`${k}${g}`) ?? 0),
    }));
    return { categories, series };
  }

  const byCat = new Map<string, Record<string, unknown>>();
  for (const r of rows) {
    const k = rawKey(r);
    if (!byCat.has(k)) byCat.set(k, r);
  }
  const series = metricKeys.map((m, i) => ({
    name: m,
    color: PALETTE[i % PALETTE.length],
    values: xKey ? catKeys.map((k) => toNum(byCat.get(k)?.[m])) : [toNum(rows[0]?.[m])],
  }));
  return { categories, series };
}

const W = 680;
const PAD = { top: 16, right: 16, bottom: 44, left: 52 };

/** Round a magnitude up to a "nice" 1/2/5×10ⁿ number for axis bounds. */
function niceNum(v: number): number {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

/** A value domain that always includes 0 and expands for negatives, so negative
 * aggregates (profit, balance) render below the zero baseline instead of being
 * clamped to it. */
function domainOf(values: number[]): { min: number; max: number } {
  const hi = Math.max(0, ...values);
  const lo = Math.min(0, ...values);
  return { min: lo < 0 ? -niceNum(-lo) : 0, max: niceNum(hi || 1) };
}

interface ChartProps {
  result: AggregateResult;
  viz: Visualization;
  height?: number;
}

function Legend({ series }: { series: Series[] }) {
  // Always shown — for a single series it is the only place the metric's name
  // appears, which is the y-axis title this chart would otherwise lack.
  return (
    <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
      {series.map((s) => (
        <span key={s.name} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

/** Category-band hover for the cartesian charts, dependency-free. Snap-to-band:
 * state changes when the pointer crosses into another band, not per pixel, so
 * hovering never re-renders the chart at pointer frequency. A tap PINS the
 * tooltip (touch kiosks have no hover); tapping the same band again unpins. */
function useBandHover(count: number, pointMode = false) {
  const [hover, setHover] = useState<{ ci: number; pinned: boolean } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const ciFromEvent = (e: React.PointerEvent): number | null => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || count === 0) return null;
    const xIn = ((e.clientX - rect.left) / rect.width) * W;
    const plotW = W - PAD.left - PAD.right;
    const ci = pointMode
      ? Math.round((xIn - PAD.left) / (plotW / Math.max(count - 1, 1)))
      : Math.floor((xIn - PAD.left) / (plotW / count));
    return ci >= 0 && ci < count ? ci : null;
  };

  const handlers = {
    onPointerMove: (e: React.PointerEvent) => {
      const ci = ciFromEvent(e);
      setHover((prev) => {
        if (prev?.pinned) return prev;
        if (ci == null) return prev == null ? prev : null;
        return prev?.ci === ci ? prev : { ci, pinned: false };
      });
    },
    onPointerLeave: () => setHover((prev) => (prev?.pinned ? prev : null)),
    onPointerDown: (e: React.PointerEvent) => {
      const ci = ciFromEvent(e);
      setHover((prev) =>
        ci == null ? null : prev?.pinned && prev.ci === ci ? null : { ci, pinned: true },
      );
    },
  };
  return { hover, svgRef, handlers };
}

/** The floating readout for a hovered/pinned category: label + every series
 * value, anchored over the band inside the chart's relative wrapper. */
function ChartTooltip({
  ci,
  categories,
  series,
  fmt,
  unit,
  precision,
}: {
  ci: number;
  categories: string[];
  series: Series[];
  fmt: NumberFormat;
  unit?: string | null;
  precision?: number | null;
}) {
  const band = (W - PAD.left - PAD.right) / Math.max(categories.length, 1);
  const centerPct = ((PAD.left + band * ci + band / 2) / W) * 100;
  // Clamp so a tooltip on an edge band stays inside the chart.
  const leftPct = Math.min(86, Math.max(14, centerPct));
  return (
    <div
      className="pointer-events-none absolute top-2 z-10 -translate-x-1/2 rounded-md border bg-background px-2.5 py-1.5 text-xs shadow-md"
      style={{ left: `${leftPct}%` }}
    >
      <div className="mb-0.5 font-medium text-foreground">{categories[ci]}</div>
      {series.map((s) => (
        <div key={s.name} className="flex items-center gap-1.5 text-muted-foreground">
          <span className="inline-block h-2 w-2 shrink-0 rounded-sm" style={{ background: s.color }} />
          <span>{s.name}</span>
          <span className="ml-auto pl-2 tabular-nums text-foreground">
            {formatValue(s.values[ci], fmt, unit, precision)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Shared cartesian frame (axes + horizontal gridlines over [min,max] + x labels).
 * Text/gridlines use `currentColor`, so the wrapping `text-muted-foreground` themes them. */
function Axes({
  h,
  min,
  max,
  categories,
  fmt,
  unit,
  precision,
}: {
  h: number;
  min: number;
  max: number;
  categories: string[];
  fmt: NumberFormat;
  unit?: string | null;
  precision?: number | null;
}) {
  const plotH = h - PAD.top - PAD.bottom;
  const ticks = 4;
  return (
    <g>
      {Array.from({ length: ticks + 1 }, (_, i) => {
        const y = PAD.top + (plotH * i) / ticks;
        const val = max - ((max - min) * i) / ticks;
        return (
          <g key={i}>
            <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y} stroke="currentColor" strokeOpacity={0.15} />
            <text x={PAD.left - 6} y={y + 4} textAnchor="end" fontSize={10} fill="currentColor">
              {formatValue(val, fmt, unit, precision)}
            </text>
          </g>
        );
      })}
      {categories.map((c, i) => {
        // Cap the label count (~8): a quarterly series over seven years printed
        // every tick, and the labels smeared into one unreadable string.
        const every = Math.ceil(categories.length / 8);
        if (i % every !== 0) return null;
        const band = (W - PAD.left - PAD.right) / categories.length;
        const x = PAD.left + band * i + band / 2;
        return (
          <text key={`${c}-${i}`} x={x} y={h - PAD.bottom + 16} textAnchor="middle" fontSize={10} fill="currentColor">
            {shortLabel(c)}
          </text>
        );
      })}
    </g>
  );
}

function BarChart({ result, viz, height = 320 }: ChartProps) {
  const { categories, series } = useMemo(() => derive(result, viz), [result, viz]);
  const stacked = viz.type === "stacked_bar" || viz.stacked;
  const fmt = viz.number_format ?? "plain";
  const plotH = height - PAD.top - PAD.bottom;
  const band = (W - PAD.left - PAD.right) / Math.max(categories.length, 1);
  // Stacked sums only the positive parts (negative stacking is ambiguous); simple
  // bars use the real per-bar values so negatives get a proper domain + baseline.
  const colTotals = categories.map((_, ci) => series.reduce((sum, s) => sum + Math.max(0, s.values[ci] ?? 0), 0));
  const { min, max } = stacked ? domainOf(colTotals) : domainOf(series.flatMap((s) => s.values));
  const span = max - min || 1;
  const y = (v: number) => PAD.top + plotH - ((v - min) / span) * plotH;
  const yZero = y(0);
  const { hover, svgRef, handlers } = useBandHover(categories.length);
  // Value labels only when there's room for them to read as labels, not noise.
  const showValues = !stacked && categories.length * series.length <= 12;

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        {...handlers}
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        className="text-muted-foreground"
        role="img"
        aria-label="bar chart"
      >
        <Axes h={height} min={min} max={max} categories={categories} fmt={fmt} unit={viz.unit} precision={viz.precision} />
        {hover ? (
          <rect
            x={PAD.left + band * hover.ci}
            y={PAD.top}
            width={band}
            height={plotH}
            fill="currentColor"
            opacity={0.07}
          />
        ) : null}
        {categories.map((_, ci) => {
          const x0 = PAD.left + band * ci + band * 0.15;
          const inner = band * 0.7;
          if (stacked) {
            let acc = 0;
            return series.map((s) => {
              const v = Math.max(0, s.values[ci] ?? 0);
              const rect = <rect key={s.name} x={x0} y={y(acc + v)} width={inner} height={Math.max(0, y(acc) - y(acc + v))} fill={s.color} />;
              acc += v;
              return rect;
            });
          }
          const bw = inner / Math.max(series.length, 1);
          return series.map((s, si) => {
            const v = s.values[ci] ?? 0;
            const yv = y(v);
            return (
              <g key={s.name}>
                <rect
                  x={x0 + bw * si}
                  y={Math.min(yv, yZero)}
                  width={Math.max(1, bw - 2)}
                  height={Math.max(0, Math.abs(yv - yZero))}
                  fill={s.color}
                />
                {showValues ? (
                  <text
                    x={x0 + bw * si + (bw - 2) / 2}
                    y={Math.min(yv, yZero) - 4}
                    textAnchor="middle"
                    fontSize={10}
                    fill="currentColor"
                  >
                    {formatValue(v, fmt, viz.unit, viz.precision)}
                  </text>
                ) : null}
              </g>
            );
          });
        })}
      </svg>
      {hover ? (
        <ChartTooltip
          ci={hover.ci}
          categories={categories}
          series={series}
          fmt={fmt}
          unit={viz.unit}
          precision={viz.precision}
        />
      ) : null}
      <Legend series={series} />
    </div>
  );
}

function LineChart({ result, viz, height = 320 }: ChartProps) {
  const { categories, series } = useMemo(() => derive(result, viz), [result, viz]);
  const area = viz.type === "area" || viz.type === "stacked_area";
  const fmt = viz.number_format ?? "plain";
  const plotH = height - PAD.top - PAD.bottom;
  const plotW = W - PAD.left - PAD.right;
  const step = plotW / Math.max(categories.length - 1, 1);
  const { min, max } = domainOf(series.flatMap((s) => s.values));
  const span = max - min || 1;
  const x = (i: number) => PAD.left + step * i;
  const y = (v: number) => PAD.top + plotH - ((v - min) / span) * plotH;
  const yZero = y(0);
  const { hover, svgRef, handlers } = useBandHover(categories.length, true);

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        {...handlers}
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        className="text-muted-foreground"
        role="img"
        aria-label="line chart"
      >
        <Axes h={height} min={min} max={max} categories={categories} fmt={fmt} unit={viz.unit} precision={viz.precision} />
        {hover ? (
          <line
            x1={x(hover.ci)}
            y1={PAD.top}
            x2={x(hover.ci)}
            y2={PAD.top + plotH}
            stroke="currentColor"
            strokeOpacity={0.35}
            strokeDasharray="3 3"
          />
        ) : null}
        {series.map((s) => {
          const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          return (
            <g key={s.name}>
              {area && (
                <polygon
                  points={`${x(0)},${yZero} ${pts} ${x(s.values.length - 1)},${yZero}`}
                  fill={s.color}
                  fillOpacity={0.15}
                />
              )}
              <polyline points={pts} fill="none" stroke={s.color} strokeWidth={2} />
              {s.values.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r={hover?.ci === i ? 4 : 2.5} fill={s.color} />
              ))}
            </g>
          );
        })}
      </svg>
      {hover ? (
        <ChartTooltip
          ci={hover.ci}
          categories={categories}
          series={series}
          fmt={fmt}
          unit={viz.unit}
          precision={viz.precision}
        />
      ) : null}
      <Legend series={series} />
    </div>
  );
}

function PieChart({ result, viz, height = 320 }: ChartProps) {
  const { categories, series } = useMemo(() => derive(result, viz), [result, viz]);
  const fmt = viz.number_format ?? "plain";
  const first = series[0];
  const values = first ? first.values : [];
  // Slice colors keyed by category name, not index — a slice keeps its color
  // when the category set changes between refreshes.
  const colorFor = useMemo(() => assignColors(categories), [categories]);
  // A pie can only represent non-negative magnitudes; negatives are clamped to 0.
  const total = values.reduce((a, b) => a + Math.max(0, b), 0) || 1;
  const r = Math.min(height, 260) / 2 - 8;
  const cx = height / 2;
  const cy = height / 2;
  const inner = viz.type === "donut" ? r * 0.55 : 0;
  let angle = -Math.PI / 2;

  return (
    <div className="flex flex-wrap items-center gap-4">
      {/* viewBox + fluid width, like the bar/line charts: the pie renders at its
          designed size when there's room and scales down instead of overflowing
          a phone or a half-width dashboard slot. */}
      <svg
        viewBox={`0 0 ${height} ${height}`}
        width="100%"
        // A square aspect ratio + a max width: the pie keeps its designed size
        // when there is room and scales down instead of overflowing a phone or a
        // half-width slot. (Sizing it purely with `h-auto w-full` collapsed it to
        // nothing as a flex item — the legend showed and the chart did not.)
        style={{ maxWidth: height, aspectRatio: "1 / 1" }}
        role="img"
        aria-label="pie chart"
      >
        {values.map((v, i) => {
          const frac = Math.max(0, v) / total;
          const fill = colorFor.get(categories[i]) ?? PALETTE[0];
          // A slice covering the whole circle has identical start and end points,
          // and an SVG arc between two identical points draws NOTHING — which is
          // why a single-category pie rendered as an empty box with a legend.
          // Draw the full circle (or ring) directly instead.
          if (frac >= 0.9999) {
            return inner ? (
              <circle
                key={i}
                cx={cx}
                cy={cy}
                r={(r + inner) / 2}
                fill="none"
                stroke={fill}
                strokeWidth={r - inner}
              />
            ) : (
              <circle key={i} cx={cx} cy={cy} r={r} fill={fill} />
            );
          }
          const a1 = angle;
          const a2 = angle + frac * Math.PI * 2;
          angle = a2;
          const large = a2 - a1 > Math.PI ? 1 : 0;
          const p = (rr: number, a: number) => `${cx + rr * Math.cos(a)},${cy + rr * Math.sin(a)}`;
          const d = inner
            ? `M ${p(r, a1)} A ${r} ${r} 0 ${large} 1 ${p(r, a2)} L ${p(inner, a2)} A ${inner} ${inner} 0 ${large} 0 ${p(inner, a1)} Z`
            : `M ${cx} ${cy} L ${p(r, a1)} A ${r} ${r} 0 ${large} 1 ${p(r, a2)} Z`;
          return <path key={i} d={d} fill={fill} />;
        })}
      </svg>
      <div className="flex flex-col gap-1 text-xs text-muted-foreground">
        {categories.map((c, i) => (
          <span key={`${c}-${i}`} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: colorFor.get(c) ?? PALETTE[0] }}
            />
            {c} — {formatValue(values[i], fmt, viz.unit, viz.precision)}
          </span>
        ))}
      </div>
    </div>
  );
}

function MetricTile({ result, viz }: ChartProps) {
  const rows = result.rows;
  const metric = viz.series[0] ?? result.metrics[0];
  const value = rows.reduce((sum, r) => sum + toNum(r[metric]), 0);
  const compare = viz.compare_to ? rows.reduce((sum, r) => sum + toNum(r[viz.compare_to as string]), 0) : null;
  const delta = compare != null && compare !== 0 ? ((value - compare) / Math.abs(compare)) * 100 : null;
  const up = delta != null && delta >= 0;
  return (
    <div className="min-w-0 overflow-hidden px-1 py-2">
      <div className="truncate text-3xl font-bold leading-tight tabular-nums text-foreground">
        {formatValue(value, viz.number_format ?? "plain", viz.unit, viz.precision)}
      </div>
      {delta != null && (
        <div className={cn("mt-1 text-sm", up ? "text-success" : "text-destructive")}>
          {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}% vs prior
        </div>
      )}
    </div>
  );
}

function ReportTable({ result, viz }: ChartProps) {
  const cols = [...result.group_by, ...result.metrics];
  const fmt = viz.number_format ?? "plain";
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} className="border-b px-2.5 py-1.5 text-left font-medium text-muted-foreground">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, ri) => (
            <tr key={ri}>
              {cols.map((c) => {
                const isMetric = result.metrics.includes(c);
                return (
                  <td key={c} className="border-b px-2.5 py-1.5 tabular-nums text-foreground">
                    {isMetric ? formatValue(row[c], fmt, viz.unit, viz.precision) : catLabel(row[c])}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReportChart({ result, viz, height = 320 }: ChartProps) {
  if (!result.rows.length) {
    return <div className="p-4 text-sm text-muted-foreground">No data</div>;
  }
  switch (viz.type) {
    case "metric":
      return <MetricTile result={result} viz={viz} height={height} />;
    case "table":
      return <ReportTable result={result} viz={viz} height={height} />;
    case "pie":
    case "donut":
      return <PieChart result={result} viz={viz} height={height} />;
    case "line":
    case "area":
    case "stacked_area":
    case "scatter":
      return <LineChart result={result} viz={viz} height={height} />;
    case "bar":
    case "stacked_bar":
    case "grouped_bar":
    default:
      return <BarChart result={result} viz={viz} height={height} />;
  }
}
