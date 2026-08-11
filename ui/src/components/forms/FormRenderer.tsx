"use client";

import { ChevronRight, Inbox, Loader2, Maximize2, Mic, Plus, Trash2, TriangleAlert } from "lucide-react";
import { Component, Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getFormRender,
  type ButtonElement,
  type CalculatedElement,
  type ChatElement,
  type FormElement,
  type FormRender,
  type FormSubmit,
  type ImageElement,
  type InputElement,
  type InputOption,
  type LiveValueElement,
  type PuzzlePadElement,
  type RecordListElement,
  type ReportElement,
  type RecordListColumn,
  type RecordListRowActionConfig,
  type SectionElement,
  type StatElement,
  type TableElement,
  TEXT_DISPLAYS,
} from "@/lib/api/forms";
import { createRecord, listRecords, type EntityRecord } from "@/lib/api/entityRecords";
import type { FilterOp } from "@/lib/api/filterOps";
import { runReport, type AggregateResult, type Visualization } from "@/lib/api/reports";
import { formatValue } from "@/components/reports/ReportChart";
import { streamRunTokens } from "@/lib/api/runStream";
import { callConnection, runWorkflow } from "@/lib/api/workflows";
import { Markdown } from "@/components/common/Markdown";
import {
  AgentDiaryNode,
  AgentTimelineNode,
  ApprovalQueueNode,
  WorkOrderActionsNode,
  WorkOrderCreateNode,
  WorkOrderListNode,
  WorkOrderTasksNode,
} from "@/components/workOrders/elements";
import { usePasteAttach } from "@/lib/usePasteAttach";
import { AttachmentChips } from "@/components/common/AttachmentChips";
import { LiveActivityNode } from "@/components/workOrders/LiveActivity";
import { WorkOrderDocumentsNode } from "@/components/workOrders/WorkOrderDocuments";
import { ReportChart } from "@/components/reports/ReportChart";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { buildCatalog, fieldMeta, relatedEntityId } from "@/lib/forms/catalog";
import { fillTokens } from "@/lib/forms/href";
import { shareTarget } from "@/lib/forms/shareUrl";
import { evaluate } from "@/lib/forms/jsonLogic";
import { mergeServerValues, sameValue } from "@/lib/forms/mergeValues";
import { displayLiveValue, formatLiveValue, readJsonPointer } from "@/lib/forms/liveValue";
import { useSpeechRecognition } from "@/lib/speech/useSpeechRecognition";

import { CountdownNode } from "./CountdownNode";
import { FieldControl } from "./FieldControl";
import { QrCodeCard } from "./QrCodeCard";
import { PuzzlePad } from "./puzzle/PuzzlePad";
import type { PadOutcome } from "./puzzle/types";
import { SlideDeck, coerceSlides } from "./SlideDeck";

/**
 * The one renderer that walks a `FormRender` element tree — used by the public
 * intake page, the authenticated internal fill page, and the builder preview.
 * It owns the editable state (root values + related sections/tables/blocks),
 * live-evaluates calculated fields, and builds the `FormSubmit` payload.
 */
export interface FormRendererProps {
  render: FormRender;
  mode?: "fill" | "preview";
  onSubmit?: (payload: FormSubmit) => Promise<void> | void;
  onRunWorkflow?: (
    workflowId: string,
    inputs: Record<string, unknown>,
    // Optional record to run the workflow against (an entity-bound view, or a
    // record-list row action). The host page falls back to its own record when omitted.
    recordId?: string,
  ) => Promise<void> | void;
  submitting?: boolean;
  /** When set (fill mode), render a submit button in the footer. */
  defaultSubmitLabel?: string;
  /** Page-controlled error to show above the footer submit button. */
  error?: string | null;
  /** Rendering a VIEW (a display surface) rather than a form. Entity-bound
   * `field` elements default to a read-only value readout — a view shows data;
   * an editable input on a wall display reads as unfinished. A field opts back
   * in with `editable: true` (e.g. a console where edits feed a workflow button). */
  viewContext?: boolean;
}

type Values = Record<string, unknown>;
type RowState = { id?: string; values: Values; related?: Record<string, { id?: string; values: Values }> };
type RelatedState = { id?: string; values?: Values; rows?: RowState[] };

interface Scope {
  entityId: string;
  values: Values;
  setValue: (slug: string, v: unknown) => void;
  keyPrefix: string;
}

const SPAN: Record<string, string> = {
  full: "sm:col-span-12",
  half: "sm:col-span-6",
  third: "sm:col-span-4",
  quarter: "sm:col-span-3",
};

/** Static 1–12 span classes for the columns container. Tailwind only ships
 * classes it can see at build time, so these cannot be computed strings. */
const COLSPAN: Record<number, string> = {
  1: "sm:col-span-1",
  2: "sm:col-span-2",
  3: "sm:col-span-3",
  4: "sm:col-span-4",
  5: "sm:col-span-5",
  6: "sm:col-span-6",
  7: "sm:col-span-7",
  8: "sm:col-span-8",
  9: "sm:col-span-9",
  10: "sm:col-span-10",
  11: "sm:col-span-11",
  12: "sm:col-span-12",
};
function spanClass(width?: string | null): string {
  return SPAN[width ?? "full"] ?? "sm:col-span-12";
}

function nonEmpty(v: Values): boolean {
  return Object.values(v).some((x) => x !== "" && x != null);
}

/** Substitute a link column's `{token}` placeholders from a table row: `{id}` is the
 * row record id, any other token is an anchor field value on the row. Delegates the
 * fill + scheme-check to the shared `fillTokens`. */
function fillHref(template: string, row: RowState): string {
  return fillTokens(template, { ...row.values, id: row.id });
}

/** Collect `input` elements reachable in the root scope (layout containers only, since
 * section/table/block change entity scope and hold their own values). */
function collectInputs(elements: FormElement[]): InputElement[] {
  const out: InputElement[] = [];
  const walk = (els: FormElement[]) => {
    for (const el of els) {
      if (el.type === "input") out.push(el);
      else if (el.type === "columns") el.columns.forEach((c) => walk(c.elements));
      else if (el.type === "panel") walk(el.elements);
      else if (el.type === "tab_group") el.tabs.forEach((t) => walk(t.elements));
      else if (el.type === "accordion") el.panes.forEach((p) => walk(p.elements));
    }
  };
  walk(elements);
  return out;
}

/** Read a dot-path (e.g. `head.pitch`, `items.0.name`) out of a parsed JSON value. */
/** A read-only readout that polls a CORS-reachable endpoint and shows a JSON value.
 * Top-level (owns polling state) so it's not re-created each parent render. */
function LiveValueNode({ el }: { el: LiveValueElement }) {
  const [value, setValue] = useState<string>("…");
  const [ok, setOk] = useState(true);

  useEffect(() => {
    if (!el.url) {
      setValue("(no url)");
      return;
    }
    let alive = true;
    let timer: number | undefined;
    let failures = 0;
    let ctrl: AbortController | null = null;
    const base = Math.max(200, el.poll_ms ?? 1000);

    const tick = async () => {
      // A hidden tab doesn't poll — these can run at 5 Hz against a device on
      // the local network, and nobody is looking.
      if (typeof document !== "undefined" && document.hidden) return;
      ctrl = new AbortController();
      try {
        const res = await fetch(el.url, {
          headers: { Accept: "application/json" },
          signal: ctrl.signal,
        });
        const json: unknown = await res.json();
        const picked = readJsonPointer(json, el.json_pointer);
        if (!alive) return;
        failures = 0;
        setOk(true);
        setValue(formatLiveValue(picked));
      } catch {
        if (!alive) return;
        failures += 1;
        setOk(false);
        setValue("unreachable");
      }
    };

    // Recursive setTimeout: one request finishes before the next is scheduled, so a
    // slow device can't pile up overlapping fetches. Failures back off (capped ~30s)
    // instead of hammering an unreachable endpoint at full speed.
    const loop = async () => {
      await tick();
      if (!alive) return;
      const delay = Math.min(base * 2 ** Math.min(failures, 5), 30_000);
      timer = window.setTimeout(loop, delay);
    };
    void loop();

    const onVisible = () => {
      if (alive && !document.hidden) {
        if (timer) window.clearTimeout(timer);
        void loop();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      alive = false;
      ctrl?.abort();
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [el.url, el.json_pointer, el.poll_ms]);

  return (
    <div>
      {el.label ? <label className="mb-1 block text-sm font-medium">{el.label}</label> : null}
      <div
        className={`rounded-md border bg-muted/40 px-3 py-2 text-sm tabular-nums ${
          ok ? "" : "text-destructive"
        }`}
      >
        {/* Translated at render, not in the poll, so an edited map takes effect on the
            next render instead of only on the next re-poll. */}
        {displayLiveValue(value, el.value_map)}
        {el.units ? <span className="ml-1 text-muted-foreground">{el.units}</span> : null}
      </div>
    </div>
  );
}

/** ISO date / datetime strings as the API emits them — the only string shapes we
 * risk reformatting. Anything else passes through verbatim. */
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$/;

function formatCell(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  if (typeof value === "object") return JSON.stringify(value);
  const s = String(value);
  if (ISO_DATE_RE.test(s)) {
    const d = new Date(s);
    if (!Number.isNaN(d.getTime())) {
      return s.length <= 10
        ? d.toLocaleDateString(undefined, { dateStyle: "medium" })
        : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    }
  }
  return s;
}

/** `employee_number` → `Employee Number` — a raw field slug is developer-speak,
 * and it's what an unconfigured column header would otherwise print. */
function humanizeSlug(slug: string): string {
  return slug.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Chat panel heights. `fill` is for a chat that IS the screen (a kiosk answer
 * station): viewport-relative with a floor so it never collapses. */
const CHAT_HEIGHT: Record<string, string> = {
  sm: "h-72",
  md: "h-96",
  lg: "h-[34rem]",
  fill: "h-[calc(100vh-10rem)] min-h-96",
};

/** Wall-display typesetting for the label element — the same ramp DisplayText
 * gives entity-bound fields, so a standalone dashboard can headline a screen. */
const LABEL_DISPLAY_CLASSES: Record<string, string> = {
  headline: "text-4xl font-semibold leading-tight tracking-tight text-foreground",
  prose: "text-2xl leading-relaxed text-foreground",
  quote: "border-l-4 border-primary/60 pl-6 text-2xl italic leading-relaxed text-foreground",
  caption: "text-xl leading-relaxed text-muted-foreground",
};

/** The one frame data elements (record lists, reports, chat) share, so a dashboard
 * reads as a set of matched cards instead of three ad-hoc borders. */
function ViewCard({
  title,
  actions,
  flush = false,
  children,
}: {
  title?: string | null;
  actions?: ReactNode;
  flush?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-lg border bg-background shadow-sm">
      {title || actions ? (
        <header className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
          {title ? <h3 className="truncate text-sm font-semibold">{title}</h3> : <span />}
          {actions}
        </header>
      ) : null}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

/** Catches a render error in one element so it can't blank the whole page — a
 * kiosk screen with one broken chart should lose the chart, not the mission.
 * Deliberately latching (no auto-reset): under `refresh_ms` polling, a
 * deterministic throw would otherwise re-crash at poll frequency forever. */
class ElementErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <TriangleAlert className="h-4 w-4 shrink-0" />
          This element hit an error. The rest of the page is unaffected.
        </div>
      );
    }
    return this.props.children;
  }
}

/**
 * Whether a record_list filter's `value` is a JsonLogic expression rather than a
 * literal. Filters have always taken plain scalars (and the `@me` sentinel), so a
 * non-null object is unambiguous — no existing layout can be caught by this.
 */
function isExpressionValue(value: unknown): boolean {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Read-only "status board": lists an entity's records (newest-first or by
 * sort_by), optionally re-polling to stay live, with an optional per-row workflow
 * button that runs against that row's record. */
function RecordListNode({
  el,
  onRunWorkflow,
  scopeValues,
}: {
  el: RecordListElement;
  onRunWorkflow?: FormRendererProps["onRunWorkflow"];
  // The enclosing view's values, so a per-row workflow input can reference a parent
  // field (e.g. a learner-bound catalog's `email`) alongside the row's own fields.
  scopeValues?: Record<string, unknown>;
}) {
  const [rows, setRows] = useState<EntityRecord[] | null>(null);
  const [error, setError] = useState(false);
  const [busyRow, setBusyRow] = useState<string | null>(null);
  // Plucked results of the element's auxiliary queries, keyed by lookup key —
  // the `lookups.*` scope per-row visibility expressions evaluate against.
  const [lookups, setLookups] = useState<Record<string, unknown[]>>({});
  // Bumped after a row workflow completes so rows AND lookups refetch — the run
  // almost certainly changed what this list shows (that's why the button exists).
  const [runTick, setRunTick] = useState(0);
  // A filter value may be a literal OR an expression over the enclosing view's
  // values, so a picker on the page can drive the board (`{"var": "week"}` against
  // a lesson dropdown). Resolved here rather than in the fetch effect so the effect
  // re-runs when the *resolved* value changes — picking a different lesson refetches.
  //
  // `pending` is the case that matters: an expression that resolves to nothing means
  // the person has not chosen yet. Dropping the filter would fetch UNFILTERED and
  // show every lesson's rows at once, which reads as a bug and is worse than empty —
  // so the board holds and says what it is waiting for instead.
  const { resolvedFilters, pending } = useMemo(() => {
    const out: { field: string; op: FilterOp; value?: string }[] = [];
    for (const f of el.filters ?? []) {
      const op = f.op ?? "eq";
      const raw = isExpressionValue(f.value) ? evaluate(f.value, scopeValues ?? {}) : f.value;
      if (op !== "isnull" && (raw == null || raw === "")) {
        // `isnull` legitimately carries no value; anything else with nothing to
        // match on is an unanswered question, not an absent filter.
        return { resolvedFilters: [], pending: true };
      }
      out.push({ field: f.field, op, value: raw == null ? undefined : String(raw) });
    }
    return { resolvedFilters: out, pending: false };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(el.filters ?? []), JSON.stringify(scopeValues ?? {})]);

  // Serialize the RESOLVED filters so the fetch effect re-runs when the picker
  // changes, not merely when the author edits the layout.
  const filtersKey = JSON.stringify(resolvedFilters) + String(pending);
  const lookupsKey = JSON.stringify(el.row_lookups ?? []);

  useEffect(() => {
    const declared = el.row_lookups ?? [];
    if (declared.length === 0) return;
    let alive = true;
    void Promise.all(
      declared.map(async (lk) => {
        const res = await listRecords(lk.entity, {
          limit: lk.limit ?? 200,
          filters: (lk.filters ?? []).map((f) => ({
            field: f.field,
            op: f.op ?? "eq",
            value: f.value == null ? undefined : String(f.value),
          })),
        });
        return [lk.key, res.items.map((r) => r[lk.pluck])] as const;
      }),
    )
      .then((entries) => {
        if (alive) setLookups(Object.fromEntries(entries));
      })
      .catch(() => {
        /* keep last known lookups; visibility rules fail open below */
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookupsKey, runTick]);

  useEffect(() => {
    if (!el.entity) {
      setError(true);
      return;
    }
    if (pending) {
      // Nothing picked yet. Clear any rows from a previous selection rather than
      // leaving the last lesson's plan on screen under a blank picker.
      setRows([]);
      setError(false);
      return;
    }
    let alive = true;
    let timer: number | undefined;
    let failures = 0;
    // Serialized last-committed rows: a poll tick that returns identical data
    // skips setRows, so a live board doesn't re-render the table for nothing.
    let lastJson: string | null = null;
    // poll_ms turns the board live; 0 => fetch once.
    const base = el.poll_ms ? Math.max(500, el.poll_ms) : 0;
    // Author-declared server-side filters (ANDed). `@me` resolves to the caller's
    // own record server-side; `isnull` carries no value. A filter value may also be
    // an expression over the enclosing view's values, so a picker elsewhere on the
    // page can drive the board — that is what `resolvedFilters` computed above is.
    const filters = resolvedFilters;

    const fetchOnce = async () => {
      // Pause while the tab is hidden — don't hammer the DB for a board no one sees.
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const res = await listRecords(el.entity, {
          limit: el.limit ?? 20,
          orderBy: el.sort_by ?? undefined,
          orderDir: el.sort_dir ?? "desc",
          filters,
        });
        if (!alive) return;
        failures = 0;
        setError(false);
        const json = JSON.stringify(res.items);
        if (json !== lastJson) {
          lastJson = json;
          setRows(res.items);
        }
      } catch {
        if (!alive) return;
        failures += 1;
        setError(true);
      }
    };

    // Recursive setTimeout (not setInterval): one fetch completes before the next
    // is scheduled, so responses can't overlap or arrive out of order. Consecutive
    // failures back off exponentially (capped ~30s) instead of re-hitting a failing
    // endpoint every poll_ms.
    const loop = async () => {
      await fetchOnce();
      if (!alive || !base) return;
      const delay = Math.min(base * 2 ** Math.min(failures, 5), 30_000);
      timer = window.setTimeout(loop, delay);
    };
    void loop();

    const onVisible = () => {
      if (base && alive && !document.hidden) {
        if (timer) window.clearTimeout(timer);
        void loop();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // `filtersKey` (below) is `el.filters` serialized — a stable stand-in that
    // re-runs the effect when the filters change without the array's churn.
    // `runTick` re-runs it after a row workflow completes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [el.entity, el.limit, el.sort_by, el.sort_dir, el.poll_ms, filtersKey, runTick]);

  // Columns: the explicit field list PLUS any column configured for presentation
  // only (additive, so `columns` never has to restate `fields`), else the field
  // slugs on the first row so an unconfigured board still shows something.
  const configured = el.columns ?? [];
  const declared = [
    ...(el.fields ?? []),
    ...configured.map((c) => c.slug).filter((s) => !(el.fields ?? []).includes(s)),
  ];
  const columns =
    declared.length > 0
      ? declared
      : rows && rows[0]
        ? Object.keys(rows[0]).filter((k) => !["id", "created_at", "updated_at", "org_id"].includes(k))
        : [];
  const colConfig = new Map<string, RecordListColumn>(configured.map((c) => [c.slug, c]));

  // Every per-row button as one list, so the header, the cell and the run handler
  // stop caring which of the two shapes an action was declared in. `row_workflow_id`
  // is drawn first and keeps its exact old behaviour.
  const rowActions: RecordListRowActionConfig[] = [
    ...(el.row_workflow_id
      ? [
          {
            workflow_id: el.row_workflow_id,
            label: el.row_action_label ?? "Run",
            inputs: el.row_workflow_inputs ?? {},
            visible_when: el.row_workflow_visible_when,
            hidden_text: el.row_workflow_hidden_text,
          },
        ]
      : []),
    ...(el.row_actions ?? []),
  ];

  const runRow = async (row: EntityRecord, action: RecordListRowActionConfig) => {
    if (!onRunWorkflow) return;
    const recordId = String(row.id);
    setBusyRow(recordId);
    try {
      // Evaluate each input over the row's values merged onto the parent scope, so
      // `{var: id}` is the row id, `{var: <row field>}` a row value, and
      // `{var: <parent field>}` a value from the enclosing view (e.g. `email`).
      const evalScope = { ...scopeValues, ...row };
      const inputs: Record<string, unknown> = {};
      for (const [k, expr] of Object.entries(action.inputs ?? {})) {
        inputs[k] = evaluate(expr, evalScope);
      }
      await onRunWorkflow(action.workflow_id, inputs, recordId);
      // The run almost certainly changed what this list (or its lookups) shows —
      // refetch both so e.g. an Enroll button flips to its hidden-state text.
      setRunTick((t) => t + 1);
    } finally {
      setBusyRow(null);
    }
  };

  // Per-row visibility for the row button/link: JsonLogic over the row's values
  // merged onto the view scope, plus the lookup arrays. No rule = visible; a
  // rule referencing a lookup that hasn't loaded yet evaluates against [] and
  // corrects itself when the lookup lands.
  const rowScope = (row: EntityRecord) => ({ ...scopeValues, ...row, lookups });
  const rowActionVisible = (row: EntityRecord, action: RecordListRowActionConfig): boolean =>
    action.visible_when == null || Boolean(evaluate(action.visible_when, rowScope(row)));
  const rowLinkVisible = (row: EntityRecord): boolean =>
    el.row_link_visible_when == null || Boolean(evaluate(el.row_link_visible_when, rowScope(row)));

  // Right-align a column when its values are numbers — scanning figures down a
  // ragged left edge is what makes a table look homemade. An explicit `align`
  // on the column config wins.
  const numericCols = new Set(
    columns.filter((c) => (rows ?? []).some((r) => typeof r[c] === "number")),
  );
  const alignOf = (c: string): string => {
    const explicit = colConfig.get(c)?.align;
    if (explicit === "right") return "text-right";
    if (explicit === "center") return "text-center";
    if (explicit === "left") return "text-left";
    return numericCols.has(c) ? "text-right" : "text-left";
  };

  /** Draw one cell per its column config; `auto` keeps the type-driven default. */
  const renderCell = (col: string, raw: unknown): ReactNode => {
    const cfg = colConfig.get(col);
    const fmt = cfg?.format ?? "auto";
    if (fmt === "badge") {
      const text = formatCell(raw);
      if (text === "—") return text;
      const tone = cfg?.badge_map?.[String(raw)] ?? "neutral";
      return (
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
            BADGE_TONES[tone] ?? BADGE_TONES.neutral
          }`}
        >
          {text}
        </span>
      );
    }
    if (fmt === "code") {
      return <span className="font-mono text-xs">{formatCell(raw)}</span>;
    }
    if (fmt === "text") return raw == null ? "—" : String(raw);
    return formatCell(raw);
  };

  return (
    <ViewCard title={el.label} flush>
      {error ? (
        <div className="px-4 py-3 text-sm text-destructive">Unable to load records.</div>
      ) : rows == null ? (
        // Initial load only — background re-polls swap data in place (or not at
        // all, when a tick returns identical rows), never back to this skeleton.
        <div className="space-y-2 p-4">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-2/3" />
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
          <Inbox className="h-8 w-8 text-muted-foreground/50" />
          {/* "Nothing chosen" and "nothing found" are different answers, and the
              second is a lie when the first is true. `empty_text` is the author's
              words for an empty result, so it does not apply to an unmade choice. */}
          <p className="text-sm text-muted-foreground">
            {pending ? "Make a selection to see these." : (el.empty_text ?? "No records yet.")}
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50">
              <tr>
                {columns.map((c) => (
                  <th key={c} className={`px-3 py-2 font-medium ${alignOf(c)}`}>
                    {colConfig.get(c)?.label ?? humanizeSlug(c)}
                  </th>
                ))}
                {el.row_link_template ? <th className="w-24 px-3 py-2" /> : null}
                {rowActions.length > 0 ? <th className="w-24 px-3 py-2" /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={String(row.id)}
                  className="border-b transition-colors last:border-0 hover:bg-muted/40"
                >
                  {columns.map((c) => (
                    <td
                      key={c}
                      className={`px-3 py-2 ${alignOf(c)} ${numericCols.has(c) ? "tabular-nums" : ""}`}
                    >
                      {renderCell(c, row[c])}
                    </td>
                  ))}
                  {el.row_link_template ? (
                    <td className="px-3 py-2 text-right">
                      {rowLinkVisible(row) ? (
                        <a
                          href={fillTokens(el.row_link_template, row)}
                          className="inline-block rounded-md border bg-background px-2 py-1 text-xs font-medium hover:bg-muted"
                        >
                          {el.row_link_label ?? "Open"}
                        </a>
                      ) : null}
                    </td>
                  ) : null}
                  {rowActions.length > 0 ? (
                    <td className="px-3 py-2 text-right">
                      {/* One row is one unit of work: while any of its buttons is
                          running they all disable, because they act on the same record
                          and a second press mid-run is a race, not impatience. */}
                      <div className="flex justify-end gap-1">
                        {rowActions.map((action, i) =>
                          rowActionVisible(row, action) ? (
                            <button
                              key={`${action.workflow_id}-${i}`}
                              type="button"
                              className="rounded-md border bg-background px-2 py-1 text-xs font-medium hover:bg-muted disabled:opacity-60"
                              disabled={busyRow === String(row.id)}
                              onClick={() => void runRow(row, action)}
                            >
                              {action.label ?? "Run"}
                            </button>
                          ) : action.hidden_text ? (
                            <span
                              key={`${action.workflow_id}-${i}`}
                              className="whitespace-nowrap text-xs text-muted-foreground"
                            >
                              {action.hidden_text}
                            </span>
                          ) : null,
                        )}
                      </div>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ViewCard>
  );
}

/** Badge tones for a record-list column rendered as a status pill. Fixed set,
 * from the theme's own tokens — a status board shouldn't invent colors. */
const BADGE_TONES: Record<string, string> = {
  neutral: "bg-muted text-muted-foreground",
  success: "bg-success/12 text-success",
  warning: "bg-warning/12 text-warning",
  destructive: "bg-destructive/12 text-destructive",
  info: "bg-primary/12 text-primary",
};

/** A KPI tile: one big number over a label. Reads a saved report (the same data
 * path as the report element) so there is no second way to compute a metric. */
function StatNode({ el }: { el: StatElement }) {
  const [result, setResult] = useState<AggregateResult | null>(null);
  const [viz, setViz] = useState<Visualization | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!el.report_id) return;
    let alive = true;
    let timer: number | undefined;
    let failures = 0;
    let lastJson: string | null = null;
    const base = el.poll_ms ? Math.max(1000, el.poll_ms) : 0;

    const fetchOnce = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const res = await runReport(el.report_id);
        if (!alive) return;
        failures = 0;
        setFailed(false);
        const json = JSON.stringify(res);
        if (json === lastJson) return;
        lastJson = json;
        const { viz: nextViz, ...rows } = res;
        setResult(rows);
        if (nextViz) setViz(nextViz);
      } catch {
        if (!alive) return;
        failures += 1;
        setFailed(true);
      }
    };
    const loop = async () => {
      await fetchOnce();
      if (!alive || !base) return;
      timer = window.setTimeout(loop, Math.min(base * 2 ** Math.min(failures, 5), 30_000));
    };
    void loop();
    const onVisible = () => {
      if (base && alive && !document.hidden) {
        if (timer) window.clearTimeout(timer);
        void loop();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [el.report_id, el.poll_ms]);

  // Sum the metric across rows, exactly as the report's own metric tile does.
  const metric = viz?.series[0] ?? result?.metrics[0];
  const value =
    result && metric ? result.rows.reduce((sum, r) => sum + (Number(r[metric]) || 0), 0) : null;
  const compareKey = viz?.compare_to;
  const compare =
    result && compareKey
      ? result.rows.reduce((sum, r) => sum + (Number(r[compareKey]) || 0), 0)
      : null;
  const delta = compare != null && compare !== 0 && value != null
      ? ((value - compare) / Math.abs(compare)) * 100
      : null;
  const up = delta != null && delta >= 0;
  // "Good" is a property of the metric, not the direction: headcount rising is
  // not inherently good news, so `neutral` colors the delta like body text.
  const trend = el.trend ?? "up_is_good";
  const deltaTone =
    trend === "neutral"
      ? "text-muted-foreground"
      : (up && trend === "up_is_good") || (!up && trend === "down_is_good")
        ? "text-success"
        : "text-destructive";

  return (
    <div className="rounded-lg border bg-background p-4 shadow-sm">
      {el.label ? (
        <p className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {el.label}
        </p>
      ) : null}
      {failed && value == null ? (
        <p className="mt-1 text-sm text-destructive">Unavailable</p>
      ) : value == null ? (
        <Skeleton className="mt-1.5 h-9 w-24" />
      ) : (
        <p className="mt-1 truncate text-3xl font-bold leading-tight tabular-nums text-foreground">
          {formatValue(value, viz?.number_format ?? "plain", viz?.unit, viz?.precision)}
        </p>
      )}
      {delta != null ? (
        <p className={`mt-1 text-sm ${deltaTone}`}>
          {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}% vs prior
        </p>
      ) : null}
    </div>
  );
}

/** Embeds a saved report: runs it (optionally re-polling) and draws the result
 * with {@link ReportChart} per the report's own visualization spec. */
function ReportNode({ el }: { el: ReportElement }) {
  const [result, setResult] = useState<AggregateResult | null>(null);
  const [viz, setViz] = useState<Visualization | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!el.report_id) {
      setError("No report selected.");
      return;
    }
    let alive = true;
    let timer: number | undefined;
    let failures = 0;
    // A poll tick that returns identical data skips setState entirely, so a live
    // dashboard doesn't redraw its charts for nothing.
    let lastJson: string | null = null;
    const base = el.poll_ms ? Math.max(1000, el.poll_ms) : 0;

    const fetchOnce = async () => {
      // Pause while the tab is hidden — don't recompute aggregates no one sees.
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        // One round trip: the run response carries the rows AND the report's viz
        // spec so the chart knows how to draw.
        const res = await runReport(el.report_id);
        if (!alive) return;
        failures = 0;
        setError(null);
        const json = JSON.stringify(res);
        if (json === lastJson) return;
        lastJson = json;
        const { viz: nextViz, ...rows } = res;
        setResult(rows);
        setViz(nextViz);
      } catch {
        if (!alive) return;
        failures += 1;
        setError("Unable to load report.");
      }
    };

    // Recursive setTimeout (not setInterval): one run completes before the next is
    // scheduled, so slow aggregates can't overlap or land out of order. Consecutive
    // failures back off exponentially (capped ~30s).
    const loop = async () => {
      await fetchOnce();
      if (!alive || !base) return;
      const delay = Math.min(base * 2 ** Math.min(failures, 5), 30_000);
      timer = window.setTimeout(loop, delay);
    };
    void loop();

    const onVisible = () => {
      if (base && alive && !document.hidden) {
        if (timer) window.clearTimeout(timer);
        void loop();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [el.report_id, el.poll_ms]);

  // Wait for BOTH the data and the viz spec so a pie/metric/table report doesn't
  // briefly flash as the bar-chart fallback. Only a fully-loaded report is
  // expandable (clicking a spinner would open an empty modal).
  const body = (height: number) =>
    error ? (
      <div className="px-1 py-2 text-sm text-destructive">{error}</div>
    ) : result && viz ? (
      <ReportChart result={result} viz={viz} height={height} />
    ) : (
      // Initial load only; once data is on screen, quiet re-polls swap it in place.
      <Skeleton className="h-40 w-full" />
    );
  const ready = !error && Boolean(result && viz);

  return (
    <>
      <div
        className={`group relative overflow-hidden rounded-lg border bg-background shadow-sm ${
          ready ? "cursor-zoom-in transition-colors hover:border-primary/50" : ""
        }`}
        role={ready ? "button" : undefined}
        tabIndex={ready ? 0 : undefined}
        aria-label={ready ? `Enlarge ${el.title ?? "report"}` : undefined}
        onClick={ready ? () => setExpanded(true) : undefined}
        onKeyDown={
          ready
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setExpanded(true);
                }
              }
            : undefined
        }
      >
        {el.title ? (
          <div className="border-b px-4 py-2.5 text-sm font-semibold">{el.title}</div>
        ) : null}
        {ready ? (
          <Maximize2 className="pointer-events-none absolute right-3 top-3 h-3.5 w-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
        ) : null}
        <div className="p-4">{body(el.height ?? 320)}</div>
      </div>
      {expanded ? (
        <Dialog open={expanded} onClose={() => setExpanded(false)} className="max-w-5xl">
          {el.title ? (
            <DialogHeader>
              <DialogTitle>{el.title}</DialogTitle>
            </DialogHeader>
          ) : null}
          <div className="max-h-[75vh] overflow-auto">{body(Math.max(el.height ?? 320, 520))}</div>
        </Dialog>
      ) : null}
    </>
  );
}

/** A compact on/off pill for the chat's live answer-speed controls. */
function ChatToggle({
  label,
  on,
  onClick,
  disabled,
  title,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={on}
      title={title}
      className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors disabled:opacity-50 ${
        on ? "border-primary bg-primary text-primary-foreground" : "border-input text-muted-foreground hover:bg-muted"
      }`}
    >
      {label}
    </button>
  );
}

/** Render a chat response latency in ms as a compact human string (e.g. "820ms", "3.4s"). */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** How many prior turns ride along as conversation memory.
 *
 * Generous, because the model server reuses the KV cache for the unchanged
 * prefix (`--cache-reuse`, see run-local-llm-stack.sh): a turn only pays to
 * evaluate the NEW text, so a long history costs roughly what a short one does.
 * The cap that remains is a CONTEXT budget, not a latency one — the prompt still
 * has to fit the server's per-slot window (16k context / 2 slots here), and a
 * conversation that outgrows it would be truncated by the server rather than by
 * us. ~40 turns of chat sits comfortably inside that. */
const MAX_HISTORY_TURNS = 40;

/** Mint a token naming this turn's live answer stream, or "" where the browser
 * can't (non-secure origins have no `crypto.randomUUID`) — the caller then just
 * runs the workflow without a stream, as it always did. */
function newStreamToken(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : "";
}

/** Rough estimate of how long it takes to speak `text` aloud, used by always-on
 * voice to keep the mic paused until the robot has (approximately) finished
 * talking — otherwise the mic transcribes the robot's own reply and loops.
 * ~150 wpm ≈ 400ms/word, plus a base for startup/network.
 *
 * The ceiling has to clear the LONGEST answer the chat can produce, not a typical
 * one. A chat element's `verbose_words` goes up to 200, which is ~80s of speech;
 * the old 20s cap silently truncated anything past ~48 words, so the mic reopened
 * mid-sentence and transcribed the rest of the robot's own answer as a new
 * question. It stays capped only so a freak input can't wedge the mic shut.
 *
 * `TAIL_PAD_MS` covers the gap between the last word and recognition settling —
 * without it the final syllable reliably bleeds into the reopened mic. */
function estimateSpeechMs(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  const TAIL_PAD_MS = 1200;
  return Math.min(180_000, Math.max(1500, 800 + words * 400 + TAIL_PAD_MS));
}

/** Normalize speech text for self-echo comparison: lowercase, strip everything but
 * letters/digits/spaces, collapse whitespace. Lets us tell when a recognized
 * utterance is really the robot's own last answer bleeding back through the mic. */
function normalizeEcho(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** True when `heard` looks like the robot's own `spoken` answer echoing back
 * (one contains the other, on a substantial chunk) — so always-on voice can drop
 * it instead of treating the robot's speech as a new question. */
function isSelfEcho(heard: string, spoken: string): boolean {
  const h = normalizeEcho(heard);
  const s = normalizeEcho(spoken);
  if (h.length < 8 || s.length < 8) return false;
  return s.includes(h) || h.includes(s);
}

/** How long a turn may sit unanswered before the chat gives up on it. Comfortably
 * past the run call's own 120s timeout, so this only catches a run that reported
 * success and then never wrote a reply. */
const ANSWER_TIMEOUT_MS = 180_000;

/** Default "one moment…" chatter shown/spoken while a slow answer is still cooking.
 * `{q}` is swapped for the person's question so some lines restate what was asked
 * (which both reassures the asker and buys the robot time). */
const DEFAULT_FILLER_PHRASES: readonly string[] = [
  "One moment please…",
  "Let me check on that…",
  "Give me a second while I look that up…",
  'Checking my notes on "{q}"…',
  "Hang on, pulling that together…",
  "Just a moment while I find the best answer…",
  'Still working on "{q}" — almost there.',
  "Good question — let me dig into that.",
];

/** Pick a filler phrase at random, avoiding an immediate repeat, and fill in `{q}`.
 * `lastIdx` is a mutable cursor (a ref's value) so successive calls don't repeat. */
function pickFiller(pool: readonly string[], question: string, lastIdx: { current: number }): string {
  let idx = Math.floor(Math.random() * pool.length);
  if (pool.length > 1 && idx === lastIdx.current) idx = (idx + 1) % pool.length;
  lastIdx.current = idx;
  const q = question.length > 48 ? `${question.slice(0, 48)}…` : question;
  return pool[idx].replace(/\{q\}/g, q);
}

/** A conversation panel backed by two entities (a conversation session + its messages).
 * Lists the active conversation's messages as bubbles (polling), and on send creates a
 * `person` message then runs the answer workflow so the robot replies + speaks. TOP-LEVEL
 * so its polling/input state is stable across FormRenderer re-renders. */
function ChatNode({ el, preview }: { el: ChatElement; preview: boolean }) {
  const convEntity = el.conversation_entity ?? "robot_conversation";
  const msgEntity = el.message_entity ?? "robot_message";
  const relSlug = el.conversation_relationship ?? "conversation";
  const roleField = el.role_field ?? "role";
  const textField = el.text_field ?? "text";
  const channelField = el.channel_field ?? "channel";
  // Blank unless the org's message entity actually has a field for it: this
  // element cannot invent a column on someone else's schema.
  const attachmentsField = el.attachments_field ?? null;
  const paste = usePasteAttach();
  const pollMs = Math.max(500, el.poll_ms ?? 1500);

  // Live answer-speed controls (Fast mode / Knowledge graph / Concise / Answer model).
  // When enabled, the chosen values ride along as workflow `inputs` so a viewer can
  // trade quality for speed per turn without touching the workflow itself.
  const controls = el.answer_controls ?? null;
  const controlsEnabled = !!controls?.show;
  const models = controls?.models?.length ? controls.models : [];
  const [fastMode, setFastMode] = useState(controls?.fast_mode ?? true);
  const [useGraph, setUseGraph] = useState(controls?.knowledge_graph ?? false);
  const [concise, setConcise] = useState(controls?.concise ?? true);
  const [speak, setSpeak] = useState(controls?.speak ?? true);
  const [answerModel, setAnswerModel] = useState(models[0] ?? "");

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<EntityRecord[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // `thinking` covers the gap between firing the answer workflow and the robot's
  // reply landing in the polled message list — the run is not awaited, so this is
  // what tells the user the robot is working.
  const [thinking, setThinking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Response-time tracking: `askedAtRef` stamps when a turn is sent, `responseMs`
  // freezes the measured latency keyed by the robot reply's id (so each answered
  // turn keeps its own time), and `elapsedMs` ticks live while the robot thinks.
  const askedAtRef = useRef<number | null>(null);
  const [responseMs, setResponseMs] = useState<Record<string, number>>({});
  const [elapsedMs, setElapsedMs] = useState(0);
  // The answer as it streams out of the run's LLM step, shown until the saved
  // robot_message lands. Preview only — the polled record stays the source of
  // truth, so if the stream fails the chat behaves exactly as it did before.
  const [liveAnswer, setLiveAnswer] = useState("");
  const streamAbortRef = useRef<AbortController | null>(null);
  // The running poll's tick, so a finished answer can be fetched the instant the
  // run says it's done instead of waiting out the rest of the interval.
  const pollNowRef = useRef<(() => Promise<void>) | null>(null);
  // Always-on voice self-echo control: `speakingUntil` is a timestamp the mic
  // stays paused until (≈ how long the robot's reply takes to speak aloud), and
  // `lastRobotSpokenRef` holds the robot's last reply so a bleed-through can be
  // recognized and dropped. `willSpeakRef` mirrors whether the answer is spoken.
  const [speakingUntil, setSpeakingUntil] = useState(0);
  const lastRobotSpokenRef = useRef("");
  const willSpeakRef = useRef(true);
  // Perceived-latency filler: ephemeral "one moment…" lines shown (and optionally
  // spoken) while the robot works. `lastQuestionRef` lets a filler restate the ask;
  // `fillerIdxRef` avoids picking the same phrase twice in a row.
  const filler = el.filler ?? null;
  const fillerEnabled = !!filler?.show;
  const [fillers, setFillers] = useState<string[]>([]);
  const lastQuestionRef = useRef("");
  const fillerIdxRef = useRef(-1);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Whether the transcript is scrolled to (near) the bottom. Auto-scroll only
  // follows new content when this is true, so polling never yanks the reader back
  // down while they're looking at earlier turns.
  const atBottomRef = useRef(true);
  // Guards against setting state from a backgrounded run that resolves/rejects
  // after the node unmounts.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Don't leave an SSE connection open behind a closed view.
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
    };
  }, []);

  // Adopt the most recent conversation on mount (fresh one is created on first send).
  useEffect(() => {
    if (preview) return;
    let alive = true;
    void (async () => {
      try {
        const res = await listRecords(convEntity, { limit: 1 });
        if (alive && res.items[0]) setConversationId(String(res.items[0].id));
      } catch {
        /* no conversation yet — created on first send */
      }
    })();
    return () => {
      alive = false;
    };
  }, [convEntity, preview]);

  // Poll the active conversation's messages (client-side filter: the records list
  // endpoint has no field filter, but a live chat's turns are among the newest rows).
  useEffect(() => {
    if (preview || !conversationId) return;
    let alive = true;
    // Serialized last-committed transcript: an unchanged poll skips the state
    // updates below entirely (the answered-detection only ever transitions when
    // the rows change, so skipping it on identical ticks is safe).
    let lastJson: string | null = null;
    const tick = async () => {
      try {
        const res = await listRecords(msgEntity, { limit: 100 });
        if (!alive) return;
        const rows = res.items
          .filter((r) => String(r[relSlug] ?? "") === conversationId)
          .sort((a, b) => String(a.created_at ?? "").localeCompare(String(b.created_at ?? "")));
        const json = JSON.stringify(rows);
        if (json === lastJson) return;
        lastJson = json;
        setMessages(rows);
        // The robot has answered once the newest turn is no longer the person's;
        // clearing here (rather than on the run promise) makes the reply and the
        // dismissal of the typing indicator land on the same tick.
        const last = rows[rows.length - 1];
        if (last && String(last[roleField] ?? "") !== "person") {
          setThinking(false);
          setFillers([]);
          // The saved reply supersedes the streamed preview of it.
          stopAnswerStream();
          // Freeze the latency for this reply once. `askedAtRef` is nulled after
          // recording, so a still-answered conversation re-polled later won't
          // overwrite the time with the (much larger) idle gap.
          if (askedAtRef.current != null) {
            const elapsed = Date.now() - askedAtRef.current;
            askedAtRef.current = null;
            const id = String(last.id);
            setResponseMs((prev) => (prev[id] != null ? prev : { ...prev, [id]: elapsed }));
            // Self-echo guard for always-on voice: remember what the robot is about
            // to say, and (if it will speak it) hold the mic paused for roughly how
            // long that takes so we don't transcribe the robot's own reply. Fires
            // once per turn (askedAtRef null-guard), so the cooldown isn't re-pushed
            // on every subsequent poll while the robot turn stays newest.
            const replyText = String(last[textField] ?? "");
            lastRobotSpokenRef.current = replyText;
            if (willSpeakRef.current && replyText.trim()) {
              setSpeakingUntil(Date.now() + estimateSpeechMs(replyText));
            }
          }
        }
      } catch {
        /* transient; keep the last good render */
      }
    };
    pollNowRef.current = tick;
    void tick();
    const id = window.setInterval(tick, pollMs);
    return () => {
      alive = false;
      pollNowRef.current = null;
      window.clearInterval(id);
    };
  }, [msgEntity, relSlug, roleField, textField, conversationId, pollMs, preview]);

  // Track the reader's position: pinned to the bottom (follow new turns) vs.
  // scrolled up (leave them where they are). ~48px of slack counts as "bottom".
  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    atBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
  };

  useEffect(() => {
    if (!atBottomRef.current) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, thinking, fillers]);

  // Tick a live counter while the robot is thinking so the user watches the
  // response time climb; the frozen `responseMs` value takes over once it replies.
  useEffect(() => {
    if (!thinking) return;
    const start = askedAtRef.current ?? Date.now();
    setElapsedMs(Date.now() - start);
    const id = window.setInterval(() => setElapsedMs(Date.now() - start), 100);
    return () => window.clearInterval(id);
  }, [thinking]);

  // Backstop: a run that reports success but never writes a reply would otherwise
  // leave the indicator running for the life of the page. The run call itself times
  // out at 120s, so anything still waiting well past that is never arriving.
  useEffect(() => {
    if (!thinking) return;
    const id = window.setTimeout(() => {
      if (mountedRef.current) failTurn("The robot did not reply. Try asking again.");
    }, ANSWER_TIMEOUT_MS);
    return () => window.clearTimeout(id);
  }, [thinking]);

  // While the robot is thinking, drip out filler chatter: the first line after
  // `delay_ms`, then another every `interval_ms`, until the reply lands (which
  // flips `thinking` off and tears this down). Each line is also spoken through
  // `speak_connection` if configured, so the physical robot stalls out loud too.
  useEffect(() => {
    if (!thinking || !fillerEnabled) return;
    const pool = filler?.phrases?.length ? filler.phrases : DEFAULT_FILLER_PHRASES;
    const delay = Math.max(400, filler?.delay_ms ?? 1400);
    const interval = Math.max(2000, filler?.interval_ms ?? 6000);
    // Say a couple of lines, then fall silent (the ticking timer still shows the
    // robot is working) — endless "one moment…" is more annoying than reassuring.
    const maxLines = Math.max(1, filler?.max_lines ?? 2);
    let emitted = 0;
    let intervalId: number | undefined;
    const emit = () => {
      emitted += 1;
      const phrase = pickFiller(pool, lastQuestionRef.current, fillerIdxRef);
      setFillers((prev) => [...prev, phrase]);
      if (filler?.speak_connection) {
        // Fire-and-forget: a filler that fails to speak must never surface an
        // error or block the real answer.
        void callConnection({
          connection: filler.speak_connection,
          method: "POST",
          path: filler.speak_path ?? "/say",
          body: { [filler.speak_field ?? "text"]: phrase },
        }).catch(() => {});
      }
      if (emitted >= maxLines && intervalId !== undefined) {
        window.clearInterval(intervalId);
        intervalId = undefined;
      }
    };
    const timeoutId = window.setTimeout(() => {
      emit();
      if (emitted < maxLines) intervalId = window.setInterval(emit, interval);
    }, delay);
    return () => {
      window.clearTimeout(timeoutId);
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thinking, fillerEnabled]);

  /** Drop any in-flight answer stream and clear what it had painted. */
  const stopAnswerStream = () => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setLiveAnswer("");
  };

  /**
   * End a turn that will never produce a reply: stop the timer, the filler chatter
   * and the stream, and surface why. Every failure path routes through here so none
   * of them can leave the "thinking" indicator running on its own.
   */
  const failTurn = (message: string) => {
    askedAtRef.current = null;
    setThinking(false);
    setFillers([]);
    stopAnswerStream();
    setErr(message);
  };

  /**
   * Consume this turn's token stream in the background. Everything here is
   * best-effort: an unavailable stream (no Redis, older API, dropped connection)
   * simply leaves the typing indicator up until the reply record arrives.
   */
  const watchAnswerStream = (streamToken: string) => {
    const controller = new AbortController();
    streamAbortRef.current = controller;
    setLiveAnswer("");
    void (async () => {
      try {
        for await (const event of streamRunTokens(streamToken, { signal: controller.signal })) {
          if (!mountedRef.current || controller.signal.aborted) return;
          if (event.type === "delta" && event.text) {
            setLiveAnswer((prev) => {
              const next = prev + event.text;
              // The robot speaks each finished clause as it streams, well before the
              // reply record lands — so the self-echo backstop has to track the text
              // being spoken RIGHT NOW. Left until the poll loop set it, this ref
              // still held the PREVIOUS answer for the whole streaming window, which
              // is exactly when a bleed-through has to be recognized.
              lastRobotSpokenRef.current = next;
              return next;
            });
          } else if (event.type === "done" || event.type === "error") {
            // The run has finished writing the reply — go get it now rather than
            // leaving the answer on screen as a preview for another poll cycle.
            void pollNowRef.current?.();
            return;
          }
        }
      } catch {
        /* preview only — the polled reply record is the real answer */
      }
    })();
  };

  const send = async (textOverride?: string) => {
    const text = (textOverride ?? input).trim();
    if (!text || sending || preview) return;
    setSending(true);
    setErr(null);
    setFillers([]);
    lastQuestionRef.current = text;
    // Sending is an explicit "bring me to the latest" gesture — re-pin to bottom
    // even if the reader had scrolled up.
    atBottomRef.current = true;
    try {
      let convId = conversationId;
      if (!convId) {
        const conv = await createRecord(convEntity, { title: text.slice(0, 60), status: "active" });
        convId = String(conv.id);
        setConversationId(convId);
      }
      await createRecord(msgEntity, {
        [roleField]: "person",
        [channelField]: "typed",
        [textField]: text,
        [relSlug]: convId,
        ...(attachmentsField && paste.documentIds.length
          ? { [attachmentsField]: paste.documentIds.join(",") }
          : {}),
      });
      setInput("");
      paste.clear();
      if (el.answer_workflow_id) {
        // Fire the answer workflow but DON'T block the composer on it: the run
        // fans out to RAG + one or more LLM steps and can take many seconds. The
        // robot's reply is written as a robot_message and surfaced by the poll
        // loop, so we show a "thinking" indicator and let the user keep typing.
        // The generous timeout is just a backstop against a hung request.
        const inputs: Record<string, unknown> = { text, conversation_id: convId };
        // Recent-conversation memory: enough prior turns for the workflow to
        // condense a follow-up ("tell me more") into a standalone, context-aware
        // search query. `messages` holds the turns before this one (the just-sent
        // person message hasn't been polled back yet).
        //
        // Capped to keep the prompt inside the model server's per-slot context —
        // see MAX_HISTORY_TURNS. Turn cost stays flat as the chat grows because
        // the server re-uses the cached prefix and only evaluates the new tail.
        const history = messages
          .slice(-MAX_HISTORY_TURNS)
          .map((m) => {
            const who = String(m[roleField] ?? "") === "person" ? "User" : "Robot";
            const line = String(m[textField] ?? "").trim();
            return line ? `${who}: ${line}` : "";
          })
          .filter(Boolean)
          .join("\n");
        if (history) inputs.history = history;
        if (controlsEnabled) {
          // Fast mode = retrieval-only, so synthesize is its inverse.
          inputs.synthesize = !fastMode;
          inputs.use_knowledge_graph = useGraph;
          // Word budget, and therefore how much DETAIL survives. Retrieval hands the
          // summariser a whole document's worth of specifics; too tight a cap forces it
          // to answer at a summary altitude ("crew sizes vary by ship") instead of
          // quoting them. Measured on the same context: 20 words generalises, ~100 gives
          // per-item numbers. These are the fallbacks — a chat element's own
          // concise_words / verbose_words win.
          inputs.max_words = concise ? controls?.concise_words ?? 45 : controls?.verbose_words ?? 200;
          inputs.speak = speak;
          if (answerModel) inputs.answer_model = answerModel;
        }
        askedAtRef.current = Date.now();
        setThinking(true);
        // Subscribe BEFORE firing the run: pub/sub drops anything published
        // while nobody is listening, and the run's first tokens can arrive
        // moments after it starts.
        const streamToken = newStreamToken();
        if (streamToken) {
          inputs.stream_token = streamToken;
          watchAnswerStream(streamToken);
        }
        void runWorkflow(
          el.answer_workflow_id,
          { inputs, ...(streamToken ? { stream_token: streamToken } : {}) },
          120000,
        )
          .then((result) => {
            // A FAILED run still answers HTTP 200 — the failure is in the body, so
            // the .catch below never sees it. Without this the spinner ticks forever
            // waiting for a robot_message the run never wrote: observed at 508s after
            // the LLM connection dropped mid-answer ("peer closed connection without
            // sending complete message body").
            //
            // Only failure is handled here. A succeeded run may still be moments away
            // from its message landing, and the poll is what picks that up.
            if (!mountedRef.current) return;
            if (result.status === "failed" || result.error) {
              failTurn(result.error || "The robot could not answer");
            }
          })
          .catch((e: unknown) => {
            if (!mountedRef.current) return;
            failTurn(e instanceof Error ? e.message : "The robot could not answer");
          });
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to send");
    } finally {
      setSending(false);
    }
  };

  const startNew = () => {
    stopAnswerStream();
    setConversationId(null);
    setMessages([]);
    setThinking(false);
    setErr(null);
    askedAtRef.current = null;
    setResponseMs({});
    setFillers([]);
    fillerIdxRef.current = -1;
    setSpeakingUntil(0);
    lastRobotSpokenRef.current = "";
  };

  // ---- Voice input (browser microphone → speech-to-text) --------------------
  // When enabled, a person can TALK to the robot instead of typing. Recognized
  // speech is fed straight into `send()`, so the robot answers + speaks exactly
  // as it does for a typed turn. Two runtime modes the viewer can switch between:
  //  - "push_to_talk": hold the mic button to speak, release to send.
  //  - "always_on":    the mic stays open; each finished utterance auto-sends.
  const voiceCfg = el.voice ?? null;
  const voiceEnabled = !!voiceCfg?.show;
  const pauseWhileThinking = voiceCfg?.pause_while_thinking ?? true;
  const [voiceMode, setVoiceMode] = useState<"push_to_talk" | "always_on">(voiceCfg?.mode ?? "push_to_talk");
  // Intent flag for always-on: the viewer has clicked the mic to keep listening.
  // Actual capture is paused while the robot answers (turn-taking) then resumed.
  const [alwaysOnEngaged, setAlwaysOnEngaged] = useState(false);
  // Mirror whether the answer is spoken aloud (so the poll loop, whose deps don't
  // track the live controls, can decide if a speaking-cooldown is needed).
  willSpeakRef.current = controlsEnabled ? speak : true;
  const {
    supported: voiceSupported,
    listening: voiceListening,
    interim: voiceInterim,
    start: startVoice,
    stop: stopVoice,
  } = useSpeechRecognition({
    lang: voiceCfg?.lang ?? "en-US",
    onResult: (text) => {
      // Drop the robot's own reply echoing back through the mic (always-on): the
      // speaking-cooldown usually keeps the mic closed while it talks, and this is
      // the backstop if the estimate runs short and the tail bleeds through.
      if (isSelfEcho(text, lastRobotSpokenRef.current)) return;
      void send(text);
    },
    onError: (m) => {
      if (!mountedRef.current) return;
      setErr(m);
      // Drop always-on intent on any surfaced error (e.g. denied mic permission)
      // so the reconcile effect can't re-arm the mic in a loop.
      setAlwaysOnEngaged(false);
    },
  });

  // Keep always-on capture in step with the robot's turn: listen while engaged,
  // but pause while a turn is sending/thinking AND while the robot is still
  // speaking its reply (`speakingUntil`), so the mic never hears the robot itself;
  // resume once idle. Push-to-talk drives the mic directly instead.
  useEffect(() => {
    if (!voiceEnabled || voiceMode !== "always_on") return;
    const now = Date.now();
    const speaking = speakingUntil > now;
    const busy = pauseWhileThinking && (thinking || sending || speaking);
    const shouldListen = alwaysOnEngaged && !busy;
    if (shouldListen && !voiceListening) startVoice(true);
    else if (!shouldListen && voiceListening) stopVoice();
    // If the only thing keeping us paused is the robot still speaking, wake up and
    // resume the instant that window elapses — no other state change would re-run
    // this effect, so the mic would otherwise stay off forever.
    if (alwaysOnEngaged && pauseWhileThinking && speaking && !thinking && !sending) {
      const id = window.setTimeout(() => setSpeakingUntil(0), Math.max(0, speakingUntil - now));
      return () => window.clearTimeout(id);
    }
  }, [
    voiceEnabled,
    voiceMode,
    alwaysOnEngaged,
    pauseWhileThinking,
    thinking,
    sending,
    speakingUntil,
    voiceListening,
    startVoice,
    stopVoice,
  ]);

  // Hold-to-talk press/release. `startVoice`/`stopVoice` are no-ops if the mode
  // or support doesn't allow it, so these stay simple.
  const pressToTalkStart = () => {
    if (preview || voiceMode !== "push_to_talk") return;
    startVoice(false);
  };
  const pressToTalkEnd = () => {
    if (voiceMode !== "push_to_talk") return;
    stopVoice();
  };
  // Switch input modes cleanly: drop any live session + always-on intent first.
  const switchVoiceMode = (mode: "push_to_talk" | "always_on") => {
    if (mode === voiceMode) return;
    setAlwaysOnEngaged(false);
    setSpeakingUntil(0);
    stopVoice();
    setVoiceMode(mode);
  };
  const micActive = voiceListening; // truly capturing right now
  const micArmed = voiceMode === "always_on" && alwaysOnEngaged; // intends to listen

  return (
    <div
      className={`flex flex-col rounded-lg border bg-background shadow-sm ${CHAT_HEIGHT[el.height ?? "md"] ?? CHAT_HEIGHT.md}`}
    >
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-sm font-semibold">{el.title ?? "Chat"}</span>
        <button
          type="button"
          onClick={startNew}
          disabled={preview}
          className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-60"
        >
          New chat
        </button>
      </div>
      {controlsEnabled ? (
        <div className="flex flex-wrap items-center gap-1.5 border-b px-3 py-1.5">
          <ChatToggle label="Fast mode" on={fastMode} onClick={() => setFastMode((v) => !v)} disabled={preview} />
          <ChatToggle
            label="Knowledge graph"
            on={useGraph}
            onClick={() => setUseGraph((v) => !v)}
            disabled={preview || fastMode}
            title={fastMode ? "Fast mode already skips the knowledge graph" : undefined}
          />
          <ChatToggle label="Concise" on={concise} onClick={() => setConcise((v) => !v)} disabled={preview} />
          <ChatToggle
            label={speak ? "🔊 Speak" : "🔇 Speak"}
            on={speak}
            onClick={() => setSpeak((v) => !v)}
            disabled={preview}
            title={speak ? "Robot says the answer aloud" : "Robot answers silently (text only)"}
          />
          {models.length > 0 ? (
            <select
              className="ml-auto rounded-md border bg-background px-2 py-1 text-xs disabled:opacity-60"
              value={answerModel}
              disabled={preview}
              onChange={(e) => setAnswerModel(e.target.value)}
              aria-label="Answer model"
            >
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : null}
        </div>
      ) : null}
      <div ref={scrollRef} onScroll={handleScroll} className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {preview ? "Chat preview — messages appear here at runtime." : "Say hello to the robot…"}
          </p>
        ) : (
          messages.map((m) => {
            const isPerson = String(m[roleField] ?? "") === "person";
            const took = responseMs[String(m.id)];
            return (
              <div key={m.id} className={`flex flex-col ${isPerson ? "items-end" : "items-start"}`}>
                <div
                  className={`max-w-[80%] break-words rounded-2xl px-3 py-2 text-sm ${
                    isPerson ? "whitespace-pre-wrap bg-primary text-primary-foreground" : "bg-muted"
                  }`}
                >
                  {isPerson ? (
                    String(m[textField] ?? "")
                  ) : (
                    // The robot's replies are LLM output — render their markdown
                    // (lists, bold, code) instead of showing raw asterisks. The
                    // shared component sanitizes; images are stripped because this
                    // is model/record data, not authored content.
                    <Markdown content={String(m[textField] ?? "")} stripImages />
                  )}
                </div>
                {!isPerson && took != null ? (
                  <span className="mt-0.5 px-1 text-[11px] tabular-nums text-muted-foreground">
                    responded in {formatDuration(took)}
                  </span>
                ) : null}
              </div>
            );
          })
        )}
        {fillers.map((f, i) => (
          <div key={`filler-${i}`} className="flex justify-start">
            <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl bg-muted/60 px-3 py-2 text-sm italic text-muted-foreground">
              {f}
            </div>
          </div>
        ))}
        {thinking && liveAnswer ? (
          // The answer as it is being written. Replaced by the robot's saved
          // message the moment the run's record lands in the poll.
          <div className="flex flex-col items-start" data-testid="live-answer">
            <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl bg-muted px-3 py-2 text-sm">
              {liveAnswer}
              <span className="ml-0.5 inline-block h-3.5 w-1 animate-pulse rounded-sm bg-current align-text-bottom" />
            </div>
            <span className="mt-0.5 px-1 text-[11px] tabular-nums text-muted-foreground">
              {formatDuration(elapsedMs)}
            </span>
          </div>
        ) : thinking ? (
          <div className="flex justify-start">
            <div className="flex max-w-[80%] items-center gap-2 rounded-2xl bg-muted px-3 py-2 text-sm text-muted-foreground">
              <span className="flex items-center gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current" />
              </span>
              <span className="tabular-nums text-[11px]">{formatDuration(elapsedMs)}</span>
            </div>
          </div>
        ) : null}
      </div>
      {err ? <p className="px-3 text-xs text-destructive">{err}</p> : null}
      {voiceEnabled ? (
        <div className="flex items-center gap-2 border-t px-2 pt-2">
          <span className="text-xs text-muted-foreground">Voice</span>
          <div className="flex rounded-md border p-0.5 text-xs">
            <button
              type="button"
              onClick={() => switchVoiceMode("push_to_talk")}
              disabled={preview}
              className={`rounded px-2 py-0.5 transition-colors disabled:opacity-50 ${
                voiceMode === "push_to_talk" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
              }`}
            >
              Hold to talk
            </button>
            <button
              type="button"
              onClick={() => switchVoiceMode("always_on")}
              disabled={preview}
              className={`rounded px-2 py-0.5 transition-colors disabled:opacity-50 ${
                voiceMode === "always_on" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
              }`}
            >
              Always on
            </button>
          </div>
          {micActive ? (
            <span className="flex items-center gap-1 text-xs font-medium text-red-500">
              <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
              Listening…
            </span>
          ) : micArmed ? (
            <span className="text-xs text-muted-foreground">Paused — robot is answering…</span>
          ) : !voiceSupported ? (
            <span className="text-xs text-muted-foreground">Not supported in this browser</span>
          ) : null}
        </div>
      ) : null}
      <div className="flex items-center gap-2 border-t p-2">
        {voiceEnabled ? (
          <button
            type="button"
            disabled={preview || !voiceSupported}
            aria-pressed={micActive}
            aria-label={voiceMode === "push_to_talk" ? "Hold to talk" : micArmed ? "Stop listening" : "Start listening"}
            title={
              !voiceSupported
                ? "Voice input isn't supported in this browser — try Chrome or Edge."
                : voiceMode === "push_to_talk"
                  ? "Hold to talk"
                  : micArmed
                    ? "Listening — click to stop"
                    : "Click to listen continuously"
            }
            // Push-to-talk: capture only while the button is held.
            onPointerDown={
              voiceMode === "push_to_talk"
                ? (e) => {
                    e.preventDefault();
                    pressToTalkStart();
                  }
                : undefined
            }
            onPointerUp={voiceMode === "push_to_talk" ? () => pressToTalkEnd() : undefined}
            onPointerLeave={voiceMode === "push_to_talk" ? () => pressToTalkEnd() : undefined}
            onPointerCancel={voiceMode === "push_to_talk" ? () => pressToTalkEnd() : undefined}
            // Always-on: click toggles continuous listening.
            onClick={voiceMode === "always_on" ? () => !preview && setAlwaysOnEngaged((v) => !v) : undefined}
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md border transition-colors disabled:opacity-50 ${
              micActive
                ? "animate-pulse border-red-500 bg-red-500 text-white"
                : micArmed
                  ? "border-red-500 text-red-500"
                  : "border-input text-muted-foreground hover:bg-muted"
            }`}
          >
            <Mic className="h-4 w-4" />
          </button>
        ) : null}
        {attachmentsField ? (
          <AttachmentChips attachments={paste.attachments} onRemove={paste.remove} />
        ) : null}
        <Input
          className="w-full"
          placeholder={micActive ? "Listening…" : el.placeholder ?? "Message the robot…"}
          value={micActive && voiceInterim && !isSelfEcho(voiceInterim, lastRobotSpokenRef.current) ? voiceInterim : input}
          disabled={preview || sending || micActive}
          onChange={(e) => setInput(e.target.value)}
          onPaste={attachmentsField ? paste.onPaste : undefined}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button
          type="button"
          onClick={() => void send()}
          disabled={preview || sending || micActive || !input.trim()}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

/** A standalone input (text/textarea/number/slider/toggle/select). TOP-LEVEL and driven
 * by props so its identity is stable across FormRenderer re-renders — otherwise every
 * keystroke would remount the control and drop focus/scroll (the value lives in the
 * parent's form state, so it persists regardless). */
function InputNode({
  el,
  value,
  onChange,
  disabled,
}: {
  el: InputElement;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
}) {
  const base = "w-full rounded-md border bg-background px-3 py-2 text-sm disabled:opacity-60";
  const label = el.label ? (
    <label className="mb-1 block text-sm font-medium">
      {el.label}
      {el.required ? <span className="text-destructive"> *</span> : null}
    </label>
  ) : null;

  // Entity-sourced choices (`options_from`). Fetched here rather than baked into the
  // saved layout so adding a record adds a choice — the whole point of the source.
  // `null` = still loading, which is distinct from `[]` = loaded and genuinely empty:
  // an empty picker should say so rather than look like a picker that has not arrived.
  const [sourced, setSourced] = useState<InputOption[] | null>(null);
  const [sourceFailed, setSourceFailed] = useState(false);
  const source = el.options_from ?? null;
  // Serialize so the effect re-runs on a real config change, not on each render's
  // fresh object identity.
  const sourceKey = JSON.stringify(source);

  useEffect(() => {
    if (!source) return;
    let alive = true;
    setSourceFailed(false);
    void listRecords(source.entity, {
      limit: source.limit ?? 100,
      orderBy: source.sort_by || undefined,
      orderDir: source.sort_dir ?? "asc",
      filters: (source.filters ?? []).map((f) => ({
        field: f.field,
        op: f.op ?? "eq",
        value: f.value == null ? undefined : String(f.value),
      })),
    })
      .then((res) => {
        if (!alive) return;
        const seen = new Set<string>();
        const opts: InputOption[] = [];
        for (const row of res.items) {
          const raw = row[source.value];
          if (raw == null || raw === "") continue; // no value to store = not a choice
          const v = String(raw);
          if (seen.has(v)) continue; // two records, one stored value — offer it once
          seen.add(v);
          const shown = source.label ? row[source.label] : null;
          opts.push({ value: v, label: shown == null ? v : String(shown) });
        }
        setSourced(opts);
      })
      .catch(() => {
        if (!alive) return;
        // Say the choices failed rather than render an empty select, which reads as
        // "there are no lessons" when it means "the list did not load".
        setSourced([]);
        setSourceFailed(true);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey]);

  let control: ReactNode;
  switch (el.control) {
    case "textarea":
      control = (
        <textarea
          className={base}
          rows={3}
          disabled={disabled}
          placeholder={el.placeholder ?? undefined}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
      break;
    case "number":
      control = (
        <input
          type="number"
          className={base}
          disabled={disabled}
          placeholder={el.placeholder ?? undefined}
          min={el.min ?? undefined}
          max={el.max ?? undefined}
          step={el.step ?? undefined}
          value={value == null ? "" : Number(value)}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
      );
      break;
    case "slider":
      control = (
        <div className="flex items-center gap-3">
          <input
            type="range"
            className="flex-1"
            disabled={disabled}
            min={el.min ?? 0}
            max={el.max ?? 100}
            step={el.step ?? 1}
            value={Number(value ?? el.min ?? 0)}
            onChange={(e) => onChange(Number(e.target.value))}
          />
          <span className="w-12 text-right text-sm tabular-nums text-muted-foreground">
            {value == null ? "—" : String(value)}
          </span>
        </div>
      );
      break;
    case "toggle":
      control = (
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(value)}
          disabled={disabled}
          onClick={() => onChange(!value)}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-60 ${
            value ? "bg-primary" : "bg-muted"
          }`}
        >
          <span
            className={`inline-block h-5 w-5 transform rounded-full bg-background shadow transition-transform ${
              value ? "translate-x-5" : "translate-x-0.5"
            }`}
          />
        </button>
      );
      break;
    case "select": {
      // An entity source replaces the static list outright rather than merging:
      // two origins for one dropdown would make a stale typed-out option
      // indistinguishable from a live record.
      const loading = source != null && sourced == null;
      const choices = source ? (sourced ?? []) : (el.options ?? []);
      control = (
        <select
          className={base}
          disabled={disabled || loading}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{loading ? "Loading…" : "—"}</option>
          {choices.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label ?? opt.value}
            </option>
          ))}
        </select>
      );
      break;
    }
    default:
      control = (
        <input
          type="text"
          className={base}
          disabled={disabled}
          placeholder={el.placeholder ?? undefined}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
  return (
    <div>
      {label}
      {control}
      {sourceFailed ? (
        <p className="mt-1 text-xs text-destructive">Could not load the choices for this list.</p>
      ) : null}
      {el.help_text ? <p className="mt-1 text-xs text-muted-foreground">{el.help_text}</p> : null}
    </div>
  );
}

export function FormRenderer({
  render,
  mode = "fill",
  onSubmit,
  onRunWorkflow,
  submitting = false,
  defaultSubmitLabel,
  error,
  viewContext = false,
}: FormRendererProps) {
  const catalog = useMemo(() => buildCatalog(render), [render]);
  const preview = mode === "preview";

  const [values, setValues] = useState<Values>(() => ({ ...render.values }));
  const [related, setRelated] = useState<Record<string, RelatedState>>(() => initRelated(render));
  const [ui, setUi] = useState<Record<string, number | boolean>>({});

  // What the viewer has EDITED. A live refresh (a view's `config.refresh_ms` makes the
  // page re-fetch) re-seeds values from the server, and must never overwrite something
  // half-typed — so every key touched here is skipped on refresh. Refs, not state:
  // dirtiness only gates the merge effect and must never itself trigger a render.
  const dirtyRef = useRef<Set<string>>(new Set());
  const relatedDirtyRef = useRef(false);
  const markRelatedDirty = () => {
    relatedDirtyRef.current = true;
  };

  const setRoot = (slug: string, v: unknown) => {
    dirtyRef.current.add(slug);
    setValues((p) => ({ ...p, [slug]: v }));
  };
  const setSection = (relId: string, slug: string, v: unknown) => {
    markRelatedDirty();
    setRelated((p) => ({ ...p, [relId]: { ...p[relId], values: { ...p[relId]?.values, [slug]: v } } }));
  };
  const rowsOf = (relId: string): RowState[] => related[relId]?.rows ?? [];
  const setRows = (relId: string, rows: RowState[]) => {
    markRelatedDirty();
    setRelated((p) => ({ ...p, [relId]: { ...p[relId], rows } }));
  };
  const setRowValue = (relId: string, idx: number, slug: string, v: unknown) => {
    const rows = [...rowsOf(relId)];
    rows[idx] = { ...rows[idx], values: { ...rows[idx].values, [slug]: v } };
    setRows(relId, rows);
  };
  const setRowRelated = (relId: string, idx: number, colRel: string, slug: string, v: unknown) => {
    const rows = [...rowsOf(relId)];
    const rel = { ...(rows[idx].related ?? {}) };
    rel[colRel] = { ...rel[colRel], values: { ...rel[colRel]?.values, [slug]: v } };
    rows[idx] = { ...rows[idx], related: rel };
    setRows(relId, rows);
  };

  // Seed standalone-input defaults into root state once, so a button's workflow inputs /
  // connection body see the default even if the operator never touched the control.
  useEffect(() => {
    const inputs = collectInputs(render.config.elements);
    setValues((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const el of inputs) {
        if (next[el.key] === undefined && el.default !== undefined && el.default !== null) {
          next[el.key] = el.default;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [render]);

  // Adopt a refreshed render: `values` is seeded once at mount, so without this a
  // re-fetched render would change nothing on screen. Untouched keys follow the
  // server; edited ones (dirtyRef) are left alone, and identical values produce no
  // state change so a quiet poll never re-renders the tree.
  useEffect(() => {
    setValues((prev) => mergeServerValues(prev, render.values, dirtyRef.current));
    // Related state is a nested structure; re-seed it wholesale, but only while the
    // viewer hasn't edited any of it (a status page never does).
    if (!relatedDirtyRef.current) {
      setRelated((prev) => {
        const next = initRelated(render);
        return sameValue(prev, next) ? prev : next;
      });
    }
  }, [render]);

  const buildPayload = (): FormSubmit => {
    const outRelated: FormSubmit["related"] = {};
    for (const [relId, st] of Object.entries(related)) {
      if (st.rows) {
        const rows = st.rows.filter((r) => nonEmpty(r.values) || Object.keys(r.related ?? {}).length);
        if (rows.length) outRelated[relId] = { rows };
      } else if (st.values && nonEmpty(st.values)) {
        outRelated[relId] = { values: st.values };
      }
    }
    return { values, related: outRelated };
  };

  // The id of the button whose action is currently in flight — its own spinner
  // feedback, so pressing "Run Onboarding" visibly does something at the button
  // rather than only in a floating notice somewhere else on the page.
  const [busyButton, setBusyButton] = useState<string | null>(null);

  const runButton = async (btn: ButtonElement) => {
    const busyKey = btn.id ?? btn.label;
    setBusyButton(busyKey);
    try {
      await runButtonAction(btn);
    } finally {
      setBusyButton((prev) => (prev === busyKey ? null : prev));
    }
  };

  const runButtonAction = async (btn: ButtonElement) => {
    if (btn.action.kind === "submit") {
      await onSubmit?.(buildPayload());
    } else if (btn.action.kind === "run_workflow") {
      if (btn.action.confirm && !window.confirm(btn.action.confirm)) return;
      const inputs: Record<string, unknown> = {};
      for (const [k, expr] of Object.entries(btn.action.inputs)) inputs[k] = evaluate(expr, values);
      await onRunWorkflow?.(btn.action.workflow_id, inputs);
    } else if (btn.action.kind === "call_connection") {
      const action = btn.action;
      if (action.confirm && !window.confirm(action.confirm)) return;
      const body: Record<string, unknown> = {};
      for (const [k, expr] of Object.entries(action.body)) body[k] = evaluate(expr, values);
      try {
        const res = await callConnection({
          connection: action.connection,
          method: action.method,
          path: action.path,
          body,
        });
        if (res.ok) toast.success(action.success_message ?? "Done");
        else toast.error(`Request failed (${res.status_code})`);
      } catch (err: unknown) {
        toast.error(err instanceof Error ? err.message : "Connection call failed");
      }
    } else if (btn.action.kind === "link") {
      if (typeof window !== "undefined") {
        // Fill `{token}` placeholders from the record's values (`{id}` = the bound
        // record id) so a button can route per-record, then scheme-check the result.
        const href = fillTokens(btn.action.href, { ...values, id: render.record_id ?? "" });
        // `noopener` so a link out to somewhere else can't reach back through
        // `window.opener` and renavigate the console that opened it.
        if (btn.action.new_tab) window.open(href, "_blank", "noopener,noreferrer");
        else window.location.href = href;
      }
    } else if (btn.action.kind === "copy_link") {
      const action = btn.action;
      // Resolved absolute, not just filled: this link is going somewhere off this
      // browser, so `/views/…` has to become a real address first.
      const target = shareTarget(
        action.href,
        { ...values, id: render.record_id ?? "" },
        typeof window === "undefined" ? "" : window.location.origin,
        action.host,
      );
      if (!target) {
        toast.error("No link configured");
        return;
      }
      try {
        await navigator.clipboard.writeText(target);
        toast.success(action.success_message ?? "Link copied");
      } catch {
        // Clipboard access is refused outside a secure context (plain http on a
        // LAN address — exactly how these consoles run), so say what to do rather
        // than failing silently.
        toast.error(`Couldn't copy automatically — the link is ${target}`);
      }
    }
  };

  /** A puzzle pad finished. Its outcome joins the values the action's `inputs`
   * expressions can read, so a workflow input is written exactly as a button's
   * would be — `{"var": "answer"}`, `{"var": "solved"}`. */
  const completePuzzle = async (el: PuzzlePadElement, scope: Scope, outcome: PadOutcome) => {
    const action = el.on_complete;
    if (!action) return;
    if (action.confirm && !window.confirm(action.confirm)) return;
    // Root values first, so a pad inside a section can still reference the page
    // record; the section's own values shadow them, and the outcome wins over both.
    const context = { ...values, ...scope.values, ...outcome };
    const inputs: Record<string, unknown> = {};
    for (const [k, expr] of Object.entries(action.inputs ?? {})) inputs[k] = evaluate(expr, context);
    await onRunWorkflow?.(action.workflow_id, inputs);
    if (action.success_message) toast.success(action.success_message);
  };

  const rootScope: Scope = {
    entityId: catalog.rootEntityId,
    values,
    setValue: setRoot,
    keyPrefix: "root",
  };

  // Render the list by CALLING ElementNode (and its sub-nodes) as functions rather than
  // mounting them as components. These node fns hold no hooks, so inlining them means a
  // FormRenderer re-render (e.g. a keystroke) DIFFS the DOM in place instead of remounting
  // the whole tree — inputs keep focus/scroll. Only true stateful leaves (FieldControl,
  // InputNode, LiveValueNode, EmbeddedForm) stay real components with stable identity.
  const renderList = (elements: FormElement[], scope: Scope) => (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-12">
      {elements.map((el, i) => (
        <Fragment key={el.id ?? `${scope.keyPrefix}-${i}`}>{ElementNode({ el, scope })}</Fragment>
      ))}
    </div>
  );

  function ElementNode({ el, scope }: { el: FormElement; scope: Scope }): ReactNode {
    // Conditional visibility: an element with a `visible_when` expression renders
    // only when it evaluates truthy against the enclosing scope's values. `null`/
    // absent is always visible. Used to gate flow (e.g. show the quiz only when
    // modules are complete, or an "Enroll" button only when not yet enrolled).
    if (el.visible_when != null && !evaluate(el.visible_when, scope.values)) return null;
    switch (el.type) {
      case "field": {
        const meta = fieldMeta(catalog, scope.entityId, el.slug);
        if (!meta) return null;
        // Display-only presentations bypass the control entirely. A read-only input is
        // still an input — border, background, resize grip — which is the wrong object
        // for a wall screen showing a scripture to a room.
        if (el.display && TEXT_DISPLAYS.has(el.display)) {
          return (
            <div className={spanClass(el.width)}>
              <DisplayText
                display={el.display}
                label={el.label ?? ""}
                value={scope.values[el.slug]}
              />
            </div>
          );
        }
        // On a view, a field is a readout unless the author opted it back into
        // editing: label + value, not a bordered input pretending to be a form.
        if (viewContext && el.editable !== true) {
          const text = formatCell(scope.values[el.slug]);
          return (
            <div className={spanClass(el.width)}>
              {(el.label ?? meta.label) ? (
                <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {el.label ?? meta.label}
                </p>
              ) : null}
              <div className="whitespace-pre-wrap text-sm text-foreground">{text}</div>
              {el.help_text ? <p className="mt-1 text-xs text-muted-foreground">{el.help_text}</p> : null}
            </div>
          );
        }
        return (
          <div className={spanClass(el.width)}>
            <FieldControl
              meta={meta}
              label={el.label ?? meta.label}
              required={el.required ?? meta.required}
              readOnly={el.read_only || preview}
              placeholder={el.placeholder ?? undefined}
              display={el.display}
              value={scope.values[el.slug]}
              onChange={(v) => scope.setValue(el.slug, v)}
              name={`${scope.keyPrefix}-${el.slug}`}
            />
            {el.help_text ? <p className="mt-1 text-xs text-muted-foreground">{el.help_text}</p> : null}
          </div>
        );
      }
      case "label":
        return <div className={spanClass(el.width)}>{LabelNode({ el })}</div>;
      case "calculated":
        return <div className={spanClass(el.width)}>{CalculatedNode({ el, scope })}</div>;
      case "progress":
        return <div className={spanClass(el.width)}>{ProgressNode({ el, scope })}</div>;
      case "countdown":
        return (
          <div className={spanClass(el.width)}>
            <CountdownNode el={el} values={scope.values} />
          </div>
        );
      case "input":
        return (
          <div className={spanClass(el.width)}>
            <InputNode
              el={el}
              value={scope.values[el.key]}
              onChange={(v) => scope.setValue(el.key, v)}
              disabled={preview}
            />
          </div>
        );
      case "live_value":
        return (
          <div className={spanClass(el.width)}>
            <LiveValueNode el={el} />
          </div>
        );
      case "image":
        return <div className={spanClass(el.width)}>{ImageNode({ el, scope })}</div>;
      case "qr_code":
        return (
          <div className={spanClass(el.width)}>
            <QrCodeCard el={el} values={scope.values} recordId={render.record_id} disabled={preview} />
          </div>
        );
      case "puzzle_pad":
        return (
          <div className={spanClass(el.width)}>
            <PuzzlePad
              el={el}
              values={scope.values}
              disabled={preview || submitting}
              onComplete={(outcome) => void completePuzzle(el, scope, outcome)}
            />
          </div>
        );
      case "slides": {
        // `slug` binds to a JSON field on the record (the module's slide list);
        // otherwise inline `slides` are used. Deck is display-only.
        const slides = el.slug ? coerceSlides(scope.values[el.slug]) : (el.slides ?? []);
        return (
          <div className="sm:col-span-12">
            <SlideDeck slides={slides} label={el.label} />
          </div>
        );
      }
      case "report":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <ReportNode el={el} />
            </ElementErrorBoundary>
          </div>
        );
      case "stat":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <StatNode el={el} />
            </ElementErrorBoundary>
          </div>
        );
      case "record_list":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <RecordListNode el={el} onRunWorkflow={onRunWorkflow} scopeValues={scope.values} />
            </ElementErrorBoundary>
          </div>
        );
      case "chat":
        return (
          <div className="sm:col-span-12">
            <ElementErrorBoundary>
              <ChatNode el={el} preview={preview} />
            </ElementErrorBoundary>
          </div>
        );
      // Work-order elements. `work_order_id: null` means "the order this page is
      // about" — the view's resolved record — so one definition serves every
      // order rather than needing a view per order.
      case "agent_timeline":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <AgentTimelineNode
                workOrderId={el.work_order_id ?? render.record_id}
                title={el.title}
                pollMs={el.poll_ms}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "agent_diary":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <AgentDiaryNode
                workOrderId={el.work_order_id ?? render.record_id}
                title={el.title}
                pageSize={el.page_size}
                height={el.height}
                pollMs={el.poll_ms}
                allowReply={el.allow_reply}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "agent_activity":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <LiveActivityNode
                workOrderId={el.work_order_id ?? render.record_id}
                title={el.title}
                height={el.height}
                allowSteer={el.allow_steer}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "work_order_documents":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <WorkOrderDocumentsNode
                workOrderId={el.work_order_id ?? render.record_id}
                title={el.title}
                hideWhenEmpty={el.hide_when_empty}
                allowUpload={el.allow_upload}
                pollMs={el.poll_ms}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "approval_queue":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <ApprovalQueueNode
                scope={el.scope ?? "work_order"}
                workOrderId={el.work_order_id ?? render.record_id}
                title={el.title}
                hideWhenEmpty={el.hide_when_empty}
                includeQuestions={el.include_questions}
                pollMs={el.poll_ms}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "work_order_create":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <WorkOrderCreateNode
                title={el.title}
                submitLabel={el.submit_label}
                defaultPriority={el.default_priority}
                showAssignee={el.show_assignee}
                detailViewId={el.detail_view_id}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "work_order_tasks":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <WorkOrderTasksNode
                workOrderId={el.work_order_id ?? render.record_id}
                title={el.title}
                showProgress={el.show_progress}
                pollMs={el.poll_ms}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "work_order_actions":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <WorkOrderActionsNode
                workOrderId={el.work_order_id ?? render.record_id}
                title={el.title}
                showSummary={el.show_summary}
                showAssignee={el.show_assignee}
                showMode={el.show_mode}
                showReview={el.show_review}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "work_order_list":
        return (
          <div className={spanClass(el.width)}>
            <ElementErrorBoundary>
              <WorkOrderListNode
                title={el.title}
                statuses={el.statuses}
                detailViewId={el.detail_view_id}
                limit={el.limit}
                pollMs={el.poll_ms}
              />
            </ElementErrorBoundary>
          </div>
        );
      case "button":
        return <div className={spanClass(el.width)}>{ButtonNode({ el })}</div>;
      case "form_ref":
        return (
          <div className="sm:col-span-12 space-y-2 border-t pt-4">
            {el.label ? <h2 className="text-lg font-semibold">{el.label}</h2> : null}
            <EmbeddedForm formId={el.form_id} />
          </div>
        );
      case "columns": {
        // Class-based spans, not an inline gridColumn style: an inline style
        // applies at ALL widths, which in the phone-size single-column grid
        // generated implicit columns instead of stacking. `sm:` classes stack
        // below the breakpoint for free. Rounding remainder goes to the last
        // column so ratios that don't divide 12 evenly can't leave a gutter.
        const totalSpan = el.columns.reduce((s, c) => s + Math.max(1, c.span), 0) || 1;
        const spans = el.columns.map((c) =>
          Math.min(12, Math.max(1, Math.round((Math.max(1, c.span) / totalSpan) * 12))),
        );
        const overflow = spans.reduce((a, b) => a + b, 0) - 12;
        const adjusted = spans.map((s, i) =>
          i === spans.length - 1 ? Math.min(12, Math.max(1, s - overflow)) : s,
        );
        return (
          <div className="sm:col-span-12 grid grid-cols-1 gap-4 sm:grid-cols-12">
            {el.columns.map((col, ci) => (
              <div key={ci} className={`min-w-0 ${COLSPAN[adjusted[ci]] ?? "sm:col-span-12"}`}>
                {renderList(col.elements, scope)}
              </div>
            ))}
          </div>
        );
      }
      case "panel": {
        if (!el.collapsible) {
          return (
            <fieldset className="sm:col-span-12 rounded-lg border p-4">
              {el.title ? <legend className="px-1 text-sm font-semibold">{el.title}</legend> : null}
              {renderList(el.elements, scope)}
            </fieldset>
          );
        }
        const pkey = el.id ?? el.title ?? "panel";
        const collapsed = (ui[`panel-${pkey}`] as boolean | undefined) ?? Boolean(el.collapsed);
        return (
          <fieldset className="sm:col-span-12 rounded-lg border p-4">
            <legend className="px-1">
              <button
                type="button"
                onClick={() => setUi((p) => ({ ...p, [`panel-${pkey}`]: !collapsed }))}
                className="flex items-center gap-1 text-sm font-semibold"
                aria-expanded={!collapsed}
              >
                <ChevronRight
                  className={`h-4 w-4 transition-transform ${collapsed ? "" : "rotate-90"}`}
                />
                {el.title ?? "Details"}
              </button>
            </legend>
            {collapsed ? null : renderList(el.elements, scope)}
          </fieldset>
        );
      }
      case "card": {
        const accent: Record<string, string> = {
          none: "",
          primary: "border-t-2 border-t-primary",
          success: "border-t-2 border-t-success",
          warning: "border-t-2 border-t-warning",
          destructive: "border-t-2 border-t-destructive",
        };
        return (
          <section
            className={`sm:col-span-12 overflow-hidden rounded-lg border bg-background shadow-sm ${
              accent[el.accent ?? "none"] ?? ""
            }`}
          >
            {el.title || el.subtitle ? (
              <header className="border-b px-4 py-3">
                {el.title ? <h3 className="text-sm font-semibold">{el.title}</h3> : null}
                {el.subtitle ? (
                  <p className="mt-0.5 text-xs text-muted-foreground">{el.subtitle}</p>
                ) : null}
              </header>
            ) : null}
            <div className="p-4">{renderList(el.elements as FormElement[], scope)}</div>
          </section>
        );
      }
      case "tab_group":
        return TabGroupNode({ el, scope });
      case "accordion":
        return AccordionNode({ el, scope });
      case "section":
        return SectionNode({ el });
      case "table":
        return TableNode({ el });
      case "block":
        return BlockNode({ el });
      default:
        return null;
    }
  }

  function LabelNode({ el }: { el: Extract<FormElement, { type: "label" }> }) {
    if (el.variant === "divider") return <hr className="my-2 border-t" />;
    // Wall-display typesetting — same ramp as the field element's DisplayText,
    // but available to standalone views with no entity to bind.
    if (el.display && LABEL_DISPLAY_CLASSES[el.display])
      return <div className={LABEL_DISPLAY_CLASSES[el.display]}>{el.text}</div>;
    // A page heading has to outrank the card titles beneath it; the old text-lg
    // was card-title size, which flattened the whole hierarchy.
    if (el.variant === "heading")
      return <h2 className="text-2xl font-semibold tracking-tight">{el.text}</h2>;
    if (el.variant === "subheading") return <h3 className="text-lg font-semibold">{el.text}</h3>;
    // Body copy is content, not chrome: foreground color (muted was punishing
    // whole paragraphs), and markdown so authors get bold/links/lists.
    return <Markdown content={el.text} stripImages className="leading-relaxed" />;
  }

  /** A display-only picture. The `url` is token-filled from the enclosing scope's
   * values (`{id}` = the bound record id) and scheme-checked by `fillTokens`, so the
   * artwork can follow record state — e.g. `/sim/ship-{condition}.svg`. */
  function ImageNode({ el, scope }: { el: ImageElement; scope: Scope }) {
    const src = fillTokens(el.url ?? "", { ...scope.values, id: render.record_id ?? "" });
    if (!src) return null;
    return (
      <figure className="space-y-1">
        {/* eslint-disable-next-line @next/next/no-img-element -- author-supplied URL
            (relative or remote); next/image needs build-time-known domains. */}
        <img
          src={src}
          alt={el.alt ?? el.caption ?? ""}
          className="w-full rounded-md object-contain"
          style={el.max_height ? { maxHeight: `${el.max_height}px` } : undefined}
        />
        {el.caption ? (
          <figcaption className="text-center text-xs text-muted-foreground">{el.caption}</figcaption>
        ) : null}
      </figure>
    );
  }

  /** Typeset a field's VALUE for a display screen — no input, no border, no resize grip.
   *
   * Sizes are deliberately large and set in `rem`, not scaled to the viewport: this is read
   * from the back of a room, and the failure mode of a wall slide is text that is too small,
   * never too big. An empty value renders nothing at all rather than an empty frame, so a
   * slide with no scripture simply has no scripture on it. */
  function DisplayText({
    display,
    label,
    value,
  }: {
    display: string;
    label: string;
    value: unknown;
  }) {
    const text = value == null ? "" : String(value);
    if (!text.trim()) return null;
    const body = {
      headline: "text-4xl font-semibold leading-tight tracking-tight text-foreground",
      prose: "text-2xl leading-relaxed text-foreground",
      // A scripture is quoted material and reads as such — the rule carries that, so the
      // words themselves need no quotation marks added by the author.
      quote: "border-l-4 border-primary/60 pl-6 text-2xl italic leading-relaxed text-foreground",
      caption: "text-xl leading-relaxed text-muted-foreground",
      // A transcript grows all lesson; cap it and let it scroll rather than pushing the
      // slide off the screen it is supposed to be supporting.
      log: "max-h-[32vh] overflow-y-auto whitespace-pre-wrap text-base leading-relaxed text-muted-foreground",
    }[display] ?? "text-2xl leading-relaxed text-foreground";
    return (
      <div className="space-y-2">
        {label ? (
          <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">{label}</p>
        ) : null}
        <div className={body}>{text}</div>
      </div>
    );
  }

  function CalculatedNode({ el, scope }: { el: CalculatedElement; scope: Scope }) {
    const result = evaluate(el.expression, scope.values);
    const display = result == null ? "—" : String(result);
    return (
      <div>
        {el.label ? <label className="mb-1 block text-sm font-medium">{el.label}</label> : null}
        <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm">{display}</div>
        {el.help_text ? <p className="mt-1 text-xs text-muted-foreground">{el.help_text}</p> : null}
      </div>
    );
  }

  function ProgressNode({
    el,
    scope,
  }: {
    el: Extract<FormElement, { type: "progress" }>;
    scope: Scope;
  }) {
    const max = el.max && el.max > 0 ? el.max : 100;
    const raw = Number(evaluate(el.value, scope.values));
    const value = Number.isFinite(raw) ? Math.min(Math.max(raw, 0), max) : 0;
    const pct = Math.round((value / max) * 100);
    return (
      <div>
        {el.label ? <label className="mb-1 block text-sm font-medium">{el.label}</label> : null}
        <div
          className="relative h-4 w-full overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${pct}%` }}
          />
          {el.show_percent !== false ? (
            <span className="absolute inset-0 flex items-center justify-center text-xs font-medium">
              {pct}%
            </span>
          ) : null}
        </div>
      </div>
    );
  }

  function ButtonNode({ el }: { el: ButtonElement }) {
    // Each variant darkens one step on hover and a further step while held, following the
    // same /90-then-/80 ramp as the shared buttonVariants (ui/components/ui/button-variants).
    const styles: Record<string, string> = {
      primary: "bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/80",
      secondary: "border bg-background hover:bg-accent hover:text-accent-foreground active:bg-accent/80",
      danger: "bg-destructive text-destructive-foreground hover:bg-destructive/90 active:bg-destructive/80",
      ghost: "hover:bg-muted active:bg-muted/80",
    };
    // The press effect is scale+shadow rather than colour alone, because these buttons are
    // tapped on a touch screen during a mission, where there is no hover state to feel.
    const interaction =
      "transition-all duration-150 hover:shadow-sm active:scale-[0.97] active:shadow-none " +
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
      "disabled:pointer-events-none disabled:opacity-60";
    // Sized for the pointer that will actually hit it: a cursor by default, a
    // finger on a view presented on a tablet or a wall display.
    const sizes: Record<string, string> = {
      default: "rounded-md px-4 py-2 text-sm",
      large: "min-h-14 rounded-xl px-6 py-3 text-lg",
      xl: "min-h-20 rounded-2xl px-8 py-4 text-2xl",
    };
    const busy = busyButton === (el.id ?? el.label);
    const spinner: Record<string, string> = {
      default: "h-4 w-4",
      large: "h-5 w-5",
      xl: "h-6 w-6",
    };
    return (
      <button
        type={el.action.kind === "submit" ? "submit" : "button"}
        disabled={preview || submitting || busy}
        onClick={el.action.kind === "submit" ? undefined : () => void runButton(el)}
        className={`inline-flex items-center justify-center gap-2 font-medium ${sizes[el.size ?? "default"] ?? sizes.default} ${interaction} ${styles[el.style]}`}
      >
        {busy ? (
          <Loader2 className={`${spinner[el.size ?? "default"] ?? spinner.default} animate-spin`} />
        ) : null}
        {el.label}
      </button>
    );
  }

  function TabGroupNode({ el, scope }: { el: Extract<FormElement, { type: "tab_group" }>; scope: Scope }) {
    const key = el.id ?? "tabs";
    const active = (ui[`tab-${key}`] as number) ?? 0;
    return (
      <div className="sm:col-span-12 space-y-3">
        <div className="flex gap-1 border-b">
          {el.tabs.map((tab, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setUi((p) => ({ ...p, [`tab-${key}`]: i }))}
              className={`px-3 py-1.5 text-sm font-medium ${
                i === active ? "border-b-2 border-primary" : "text-muted-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {el.tabs[active] ? renderList(el.tabs[active].elements, scope) : null}
      </div>
    );
  }

  function AccordionNode({ el, scope }: { el: Extract<FormElement, { type: "accordion" }>; scope: Scope }) {
    const key = el.id ?? "acc";
    const open = (ui[`acc-${key}`] as number) ?? 0;
    return (
      <div className="sm:col-span-12 space-y-2">
        {el.panes.map((pane, i) => (
          <div key={i} className="rounded-md border">
            <button
              type="button"
              onClick={() => setUi((p) => ({ ...p, [`acc-${key}`]: i }))}
              className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium"
            >
              {pane.label}
              <span>{i === open ? "−" : "+"}</span>
            </button>
            {i === open ? <div className="border-t p-3">{renderList(pane.elements, scope)}</div> : null}
          </div>
        ))}
      </div>
    );
  }

  function SectionNode({ el }: { el: SectionElement }) {
    const entityId = relatedEntityId(catalog, el.relationship_id);
    if (!entityId) return null;
    const scope: Scope = {
      entityId,
      values: related[el.relationship_id]?.values ?? {},
      setValue: (slug, v) => setSection(el.relationship_id, slug, v),
      keyPrefix: `sec-${el.relationship_id}`,
    };
    const heading = <h2 className="text-lg font-semibold">{el.label ?? "Details"}</h2>;
    const modalKey = `modal-${el.relationship_id}`;

    if (el.mode === "modal") {
      const filled = nonEmpty(scope.values);
      return (
        <div className="sm:col-span-12 space-y-2 border-t pt-4">
          {heading}
          <button
            type="button"
            disabled={preview}
            onClick={() => setUi((p) => ({ ...p, [modalKey]: true }))}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <Plus className="h-4 w-4" /> {filled ? "Edit" : "Add"} {el.label ?? "details"}
          </button>
          {ui[modalKey] ? (
            // The shared Dialog brings the behavior a hand-rolled backdrop lacks:
            // focus trap, Escape-to-close, scroll lock, backdrop click.
            <Dialog
              open
              onClose={() => setUi((p) => ({ ...p, [modalKey]: false }))}
              className="max-w-md"
            >
              <DialogHeader>
                <DialogTitle>{el.label ?? "Details"}</DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                {renderList(el.elements as FormElement[], scope)}
                <button
                  type="button"
                  onClick={() => setUi((p) => ({ ...p, [modalKey]: false }))}
                  className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
                >
                  Done
                </button>
              </div>
            </Dialog>
          ) : null}
        </div>
      );
    }

    return (
      <div className="sm:col-span-12 space-y-3 border-t pt-4">
        {heading}
        {renderList(el.elements as FormElement[], scope)}
      </div>
    );
  }

  function TableNode({ el }: { el: TableElement }) {
    const relId = el.anchor_relationship_id;
    const childEntity = relatedEntityId(catalog, relId);
    if (!childEntity) return null;
    const rows = rowsOf(relId);
    // Table-level lock: whole grid renders read-only in fill mode (no add/remove row, all cells locked).
    const locked = preview || Boolean(el.read_only);
    return (
      <div className="sm:col-span-12 space-y-2 border-t pt-4">
        <h2 className="text-lg font-semibold">{el.label ?? "Items"}</h2>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left">
                {el.columns.map((col, ci) => (
                  <th key={ci} className="px-2 py-1.5 font-medium">
                    {col.label ?? (col.kind === "link" ? "" : col.slug)}
                  </th>
                ))}
                {!locked ? <th className="w-8" /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="border-b align-top">
                  {el.columns.map((col, ci) => {
                    if (col.kind === "link") {
                      const href = fillHref(col.href_template, row);
                      return (
                        <td key={ci} className="px-2 py-1.5">
                          {/* Button-look, not underlined text: these are the
                              primary actions of their rows (open slides, read
                              the source), and inline links read as an
                              afterthought next to the styled inputs around
                              them. Same recipe as the record_list row link. */}
                          <a
                            href={href}
                            target={col.new_tab ? "_blank" : undefined}
                            rel={col.new_tab ? "noopener noreferrer" : undefined}
                            className="inline-block whitespace-nowrap rounded-md border bg-background px-2.5 py-1 text-xs font-medium hover:bg-muted"
                          >
                            {col.link_label ?? "Open"}
                          </a>
                        </td>
                      );
                    }
                    if (col.kind === "field") {
                      const meta = fieldMeta(catalog, childEntity, col.slug);
                      if (!meta) return <td key={ci} />;
                      return (
                        <td key={ci} className="px-2 py-1.5">
                          <FieldControl
                            meta={meta}
                            label=""
                            required={false}
                            readOnly={col.read_only || locked}
                            display={col.display}
                            value={row.values[col.slug]}
                            onChange={(v) => setRowValue(relId, ri, col.slug, v)}
                            name={`tbl-${relId}-${ri}-${col.slug}`}
                          />
                        </td>
                      );
                    }
                    // related column
                    const relatedEntity = relatedEntityId(catalog, col.relationship_id);
                    const meta = relatedEntity ? fieldMeta(catalog, relatedEntity, col.slug) : undefined;
                    if (!meta) return <td key={ci} />;
                    return (
                      <td key={ci} className="px-2 py-1.5">
                        <FieldControl
                          meta={meta}
                          label=""
                          required={false}
                          readOnly={!col.editable || locked}
                          display={col.display}
                          value={row.related?.[col.relationship_id]?.values?.[col.slug]}
                          onChange={(v) => setRowRelated(relId, ri, col.relationship_id, col.slug, v)}
                          name={`tbl-${relId}-${ri}-${col.relationship_id}-${col.slug}`}
                        />
                      </td>
                    );
                  })}
                  {!locked ? (
                    <td className="px-1 py-1.5">
                      <button
                        type="button"
                        onClick={() => setRows(relId, rows.filter((_, i) => i !== ri))}
                        className="text-muted-foreground hover:text-destructive"
                        aria-label="Remove row"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!locked ? (
          <button
            type="button"
            onClick={() => setRows(relId, [...rows, { values: {} }])}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <Plus className="h-4 w-4" /> Add row
          </button>
        ) : null}
      </div>
    );
  }

  function BlockNode({ el }: { el: Extract<FormElement, { type: "block" }> }) {
    const relId = el.anchor_relationship_id;
    const childEntity = relatedEntityId(catalog, relId);
    if (!childEntity) return null;
    const rows = rowsOf(relId);
    return (
      <div className="sm:col-span-12 space-y-3 border-t pt-4">
        <h2 className="text-lg font-semibold">{el.label ?? "Items"}</h2>
        {rows.map((row, ri) => {
          const scope: Scope = {
            entityId: childEntity,
            values: row.values,
            setValue: (slug, v) => setRowValue(relId, ri, slug, v),
            keyPrefix: `blk-${relId}-${ri}`,
          };
          return (
            <div key={ri} className="relative rounded-md border p-3">
              {!preview ? (
                <button
                  type="button"
                  onClick={() => setRows(relId, rows.filter((_, i) => i !== ri))}
                  className="absolute right-2 top-2 text-muted-foreground hover:text-destructive"
                  aria-label="Remove"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              ) : null}
              {renderList(el.elements as FormElement[], scope)}
            </div>
          );
        })}
        {!preview ? (
          <button
            type="button"
            onClick={() => setRows(relId, [...rows, { values: {} }])}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <Plus className="h-4 w-4" /> {el.add_label ?? "Add another"}
          </button>
        ) : null}
      </div>
    );
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!preview) void onSubmit?.(buildPayload());
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {renderList(render.config.elements, rootScope)}
      {!preview && (error || defaultSubmitLabel) ? (
        <div className="space-y-3">
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {defaultSubmitLabel ? (
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-primary px-4 py-2.5 font-medium text-primary-foreground disabled:opacity-60"
            >
              {submitting ? "Submitting…" : defaultSubmitLabel}
            </button>
          ) : null}
        </div>
      ) : null}
    </form>
  );
}

/** Renders a form embedded in a view (`form_ref`) as a read-only preview. Full
 * record-bound embedded fill is a future enhancement. */
function EmbeddedForm({ formId }: { formId: string }) {
  const [render, setRender] = useState<FormRender | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    getFormRender(formId)
      .then((r) => active && setRender(r))
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : "Form unavailable"));
    return () => {
      active = false;
    };
  }, [formId]);
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!render) return <p className="text-sm text-muted-foreground">Loading form…</p>;
  return (
    <div className="rounded-md border p-3">
      <FormRenderer render={render} mode="preview" />
    </div>
  );
}

function initRelated(render: FormRender): Record<string, RelatedState> {
  const out: Record<string, RelatedState> = {};
  for (const [relId, data] of Object.entries(render.related ?? {})) {
    if (data.rows) out[relId] = { rows: data.rows.map((r) => ({ ...(r as RowState) })) };
    else out[relId] = { id: (data as RelatedState).id, values: { ...(data.values ?? {}) } };
  }
  return out;
}
