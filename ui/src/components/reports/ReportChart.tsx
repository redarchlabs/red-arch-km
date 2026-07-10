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
import { useMemo } from "react";

import type { AggregateResult, NumberFormat, Visualization } from "@/lib/api/reports";
import { cn } from "@/lib/utils";

const PALETTE = [
  "#2563eb", "#16a34a", "#ea580c", "#9333ea", "#dc2626", "#0891b2",
  "#ca8a04", "#db2777", "#4f46e5", "#059669", "#e11d48", "#7c3aed",
];

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

export function formatValue(v: unknown, fmt: NumberFormat = "plain", unit?: string | null): string {
  if (v == null || v === "") return "";
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  let s: string;
  switch (fmt) {
    case "comma":
      s = n.toLocaleString();
      break;
    case "currency":
      s = n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
      break;
    case "percent":
      s = `${(n * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
      break;
    case "compact":
      s = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
      break;
    case "bytes":
      s = formatBytes(n);
      break;
    default:
      s = String(n);
  }
  return unit ? `${s}${unit}` : s;
}

/** Trim a date-trunc ISO timestamp to a readable label; pass other values through. */
function catLabel(v: unknown): string {
  if (v == null) return "—";
  const s = String(v);
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})T/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : s;
}

function uniq(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of values) {
    if (!seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
}

interface Derived {
  categories: string[];
  series: Series[];
}

function derive(result: AggregateResult, viz: Visualization): Derived {
  const rows = result.rows;
  const xKey = viz.x ?? result.group_by[0] ?? null;
  const metricKeys = viz.series.length ? viz.series : result.metrics;

  if (viz.color_by && metricKeys.length >= 1 && xKey) {
    const metric = metricKeys[0];
    const cats = uniq(rows.map((r) => catLabel(r[xKey])));
    const groups = uniq(rows.map((r) => String(r[viz.color_by as string])));
    const series = groups.map((g, i) => ({
      name: g,
      color: PALETTE[i % PALETTE.length],
      values: cats.map((c) => {
        const row = rows.find(
          (r) => catLabel(r[xKey]) === c && String(r[viz.color_by as string]) === g,
        );
        return row ? toNum(row[metric]) : 0;
      }),
    }));
    return { categories: cats, series };
  }

  const cats = xKey ? uniq(rows.map((r) => catLabel(r[xKey]))) : [""];
  const series = metricKeys.map((m, i) => ({
    name: m,
    color: PALETTE[i % PALETTE.length],
    values: xKey
      ? cats.map((c) => {
          const row = rows.find((r) => catLabel(r[xKey]) === c);
          return row ? toNum(row[m]) : 0;
        })
      : [toNum(rows[0]?.[m])],
  }));
  return { categories: cats, series };
}

const W = 680;
const PAD = { top: 16, right: 16, bottom: 44, left: 52 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

interface ChartProps {
  result: AggregateResult;
  viz: Visualization;
  height?: number;
}

function Legend({ series }: { series: Series[] }) {
  if (series.length <= 1) return null;
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

/** Shared cartesian frame (axes + horizontal gridlines + x labels). Text/gridlines
 * use `currentColor`, so the wrapping `text-muted-foreground` themes them. */
function Axes({
  h,
  max,
  categories,
  fmt,
  unit,
}: {
  h: number;
  max: number;
  categories: string[];
  fmt: NumberFormat;
  unit?: string | null;
}) {
  const plotH = h - PAD.top - PAD.bottom;
  const ticks = 4;
  return (
    <g>
      {Array.from({ length: ticks + 1 }, (_, i) => {
        const y = PAD.top + (plotH * i) / ticks;
        const val = max - (max * i) / ticks;
        return (
          <g key={i}>
            <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y} stroke="currentColor" strokeOpacity={0.15} />
            <text x={PAD.left - 6} y={y + 4} textAnchor="end" fontSize={10} fill="currentColor">
              {formatValue(val, fmt, unit)}
            </text>
          </g>
        );
      })}
      {categories.map((c, i) => {
        const band = (W - PAD.left - PAD.right) / categories.length;
        const x = PAD.left + band * i + band / 2;
        return (
          <text key={`${c}-${i}`} x={x} y={h - PAD.bottom + 16} textAnchor="middle" fontSize={10} fill="currentColor">
            {c.length > 12 ? `${c.slice(0, 11)}…` : c}
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
  const colTotals = categories.map((_, ci) => series.reduce((sum, s) => sum + Math.max(0, s.values[ci] ?? 0), 0));
  const max = niceMax(stacked ? Math.max(1, ...colTotals) : Math.max(1, ...series.flatMap((s) => s.values)));
  const y = (v: number) => PAD.top + plotH - (Math.max(0, v) / max) * plotH;

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" className="text-muted-foreground" role="img" aria-label="bar chart">
        <Axes h={height} max={max} categories={categories} fmt={fmt} unit={viz.unit} />
        {categories.map((_, ci) => {
          const x0 = PAD.left + band * ci + band * 0.15;
          const inner = band * 0.7;
          if (stacked) {
            let acc = 0;
            return series.map((s) => {
              const v = Math.max(0, s.values[ci] ?? 0);
              const yTop = y(acc + v);
              const hgt = y(acc) - y(acc + v);
              acc += v;
              return <rect key={s.name} x={x0} y={yTop} width={inner} height={Math.max(0, hgt)} fill={s.color} />;
            });
          }
          const bw = inner / Math.max(series.length, 1);
          return series.map((s, si) => {
            const v = Math.max(0, s.values[ci] ?? 0);
            return (
              <rect
                key={s.name}
                x={x0 + bw * si}
                y={y(v)}
                width={Math.max(1, bw - 2)}
                height={Math.max(0, PAD.top + plotH - y(v))}
                fill={s.color}
              />
            );
          });
        })}
      </svg>
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
  const max = niceMax(Math.max(1, ...series.flatMap((s) => s.values)));
  const x = (i: number) => PAD.left + step * i;
  const y = (v: number) => PAD.top + plotH - (Math.max(0, v) / max) * plotH;

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${height}`} width="100%" className="text-muted-foreground" role="img" aria-label="line chart">
        <Axes h={height} max={max} categories={categories} fmt={fmt} unit={viz.unit} />
        {series.map((s) => {
          const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          return (
            <g key={s.name}>
              {area && (
                <polygon
                  points={`${x(0)},${y(0)} ${pts} ${x(s.values.length - 1)},${y(0)}`}
                  fill={s.color}
                  fillOpacity={0.15}
                />
              )}
              <polyline points={pts} fill="none" stroke={s.color} strokeWidth={2} />
              {s.values.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r={2.5} fill={s.color} />
              ))}
            </g>
          );
        })}
      </svg>
      <Legend series={series} />
    </div>
  );
}

function PieChart({ result, viz, height = 320 }: ChartProps) {
  const { categories, series } = useMemo(() => derive(result, viz), [result, viz]);
  const fmt = viz.number_format ?? "plain";
  const first = series[0];
  const values = first ? first.values : [];
  const total = values.reduce((a, b) => a + Math.max(0, b), 0) || 1;
  const r = Math.min(height, 260) / 2 - 8;
  const cx = height / 2;
  const cy = height / 2;
  const inner = viz.type === "donut" ? r * 0.55 : 0;
  let angle = -Math.PI / 2;

  return (
    <div className="flex flex-wrap items-center gap-4">
      <svg viewBox={`0 0 ${height} ${height}`} width={height} height={height} role="img" aria-label="pie chart">
        {values.map((v, i) => {
          const frac = Math.max(0, v) / total;
          const a1 = angle;
          const a2 = angle + frac * Math.PI * 2;
          angle = a2;
          const large = a2 - a1 > Math.PI ? 1 : 0;
          const p = (rr: number, a: number) => `${cx + rr * Math.cos(a)},${cy + rr * Math.sin(a)}`;
          const d = inner
            ? `M ${p(r, a1)} A ${r} ${r} 0 ${large} 1 ${p(r, a2)} L ${p(inner, a2)} A ${inner} ${inner} 0 ${large} 0 ${p(inner, a1)} Z`
            : `M ${cx} ${cy} L ${p(r, a1)} A ${r} ${r} 0 ${large} 1 ${p(r, a2)} Z`;
          return <path key={i} d={d} fill={PALETTE[i % PALETTE.length]} />;
        })}
      </svg>
      <div className="flex flex-col gap-1 text-xs text-muted-foreground">
        {categories.map((c, i) => (
          <span key={`${c}-${i}`} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: PALETTE[i % PALETTE.length] }} />
            {c} — {formatValue(values[i], fmt, viz.unit)}
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
    <div className="px-1 py-2">
      <div className="text-3xl font-bold leading-tight tabular-nums text-foreground">
        {formatValue(value, viz.number_format ?? "plain", viz.unit)}
      </div>
      {delta != null && (
        <div className={cn("mt-1 text-sm", up ? "text-emerald-600 dark:text-emerald-500" : "text-red-600 dark:text-red-500")}>
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
                    {isMetric ? formatValue(row[c], fmt, viz.unit) : catLabel(row[c])}
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
