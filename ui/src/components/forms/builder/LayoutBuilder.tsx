"use client";

import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useHelpOverride } from "@/context/HelpContext";
import { helpForElement } from "@/lib/builderHelp";
import type { EntityDefinition, EntityField, EntityRelationship } from "@/lib/api/entities";
import { listReports } from "@/lib/api/reports";
import type {
  ButtonElement,
  CalculatedElement,
  FieldElement,
  FormElement,
  LabelElement,
  RecordListElement,
  RecordListFilterConfig,
  SectionElement,
  Slide,
  SlidesElement,
  TableColumn,
  TableElement,
} from "@/lib/api/forms";
import { FILTER_OPERATORS } from "@/lib/api/filterOps";

import {
  KIND_LABELS,
  LAYOUT_KINDS,
  LEAF_KINDS,
  newElement,
  type PaletteKind,
  VIEW_KINDS,
} from "./elementFactory";

/** Resolvers the builder needs to offer valid field/relationship pickers. */
export interface BuilderCtx {
  entitiesById: Map<string, EntityDefinition>;
  toOneOutgoing: (entityId: string) => EntityRelationship[]; // sections + related columns
  incomingToMany: (entityId: string) => EntityRelationship[]; // tables + blocks
  forms?: { id: string; name: string }[]; // for embedded-form (form_ref) pickers in views
}

const box = "rounded-md border bg-card p-3 space-y-2";
const input = "w-full rounded-md border bg-background px-2 py-1 text-sm";
const btn = "inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted";

function fieldsOf(ctx: BuilderCtx, entityId: string): EntityField[] {
  return ctx.entitiesById.get(entityId)?.fields ?? [];
}

interface LayoutBuilderProps {
  elements: FormElement[];
  entityId: string;
  ctx: BuilderCtx;
  onChange: (elements: FormElement[]) => void;
  /** Which palette groups to offer here (leaf-only inside sections/blocks; view =
   *  presentational + embedded forms + layout, no entity-bound leaves). */
  allow?: "all" | "leaf" | "view";
}

export function LayoutBuilder({ elements, entityId, ctx, onChange, allow = "all" }: LayoutBuilderProps) {
  // Focusing anything inside an element's card shows that element's help. Focus
  // capture runs outermost-first, so for nested containers the innermost (most
  // specific) element sets the help last and wins.
  const setHelp = useHelpOverride();
  const updateAt = (i: number, el: FormElement) => onChange(elements.map((e, j) => (j === i ? el : e)));
  const removeAt = (i: number) => onChange(elements.filter((_, j) => j !== i));
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= elements.length) return;
    const next = [...elements];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  const add = (kind: PaletteKind) => onChange([...elements, newElement(kind)]);

  return (
    <div className="space-y-2">
      {elements.map((el, i) => (
        <div key={el.id ?? i} className={box} onFocusCapture={() => setHelp(helpForElement(el.type))}>
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase text-muted-foreground">
              {KIND_LABELS[el.type]}
            </span>
            <div className="flex items-center gap-1">
              <button type="button" className={btn} onClick={() => move(i, -1)} aria-label="Move up">
                <ChevronUp className="h-3 w-3" />
              </button>
              <button type="button" className={btn} onClick={() => move(i, 1)} aria-label="Move down">
                <ChevronDown className="h-3 w-3" />
              </button>
              <button
                type="button"
                className={`${btn} text-destructive`}
                onClick={() => removeAt(i)}
                aria-label="Delete"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          </div>
          <ElementEditor
            el={el}
            entityId={entityId}
            ctx={ctx}
            allow={allow}
            onChange={(e) => updateAt(i, e)}
          />
        </div>
      ))}
      <AddMenu allow={allow} onAdd={add} />
    </div>
  );
}

function AddMenu({ allow, onAdd }: { allow: "all" | "leaf" | "view"; onAdd: (k: PaletteKind) => void }) {
  const kinds: PaletteKind[] =
    allow === "leaf"
      ? LEAF_KINDS
      : allow === "view"
        ? VIEW_KINDS
        : [...LEAF_KINDS, "section", "table", "block", ...LAYOUT_KINDS];
  return (
    <div className="flex items-center gap-2">
      <Plus className="h-3 w-3 text-muted-foreground" />
      <select
        className={input}
        value=""
        onChange={(e) => {
          if (e.target.value) onAdd(e.target.value as PaletteKind);
        }}
      >
        <option value="">Add element…</option>
        {kinds.map((k) => (
          <option key={k} value={k}>
            {KIND_LABELS[k]}
          </option>
        ))}
      </select>
    </div>
  );
}

function ElementEditor({
  el,
  entityId,
  ctx,
  allow,
  onChange,
}: {
  el: FormElement;
  entityId: string;
  ctx: BuilderCtx;
  allow: "all" | "leaf" | "view";
  onChange: (el: FormElement) => void;
}) {
  switch (el.type) {
    case "field":
      return <FieldEditor el={el} fields={fieldsOf(ctx, entityId)} onChange={onChange} />;
    case "label":
      return <LabelEditor el={el} onChange={onChange} />;
    case "calculated":
      return <CalculatedEditor el={el} fields={fieldsOf(ctx, entityId)} onChange={onChange} />;
    case "input":
      return <InputEditor el={el} onChange={onChange} />;
    case "live_value":
      return <LiveValueEditor el={el} onChange={onChange} />;
    case "report":
      return <ReportEditor el={el} onChange={onChange} />;
    case "record_list":
      return <RecordListEditor el={el} ctx={ctx} onChange={onChange} />;
    case "slides":
      return <SlidesEditor el={el} onChange={onChange} />;
    case "chat":
      return <ChatEditor el={el} onChange={onChange} />;
    case "button":
      return <ButtonEditor el={el} onChange={onChange} />;
    case "form_ref":
      return <FormRefEditor el={el} forms={ctx.forms ?? []} onChange={onChange} />;
    case "section":
      return <SectionEditor el={el} entityId={entityId} ctx={ctx} onChange={onChange} />;
    case "block":
      return <BlockEditor el={el} entityId={entityId} ctx={ctx} onChange={onChange} />;
    case "table":
      return <TableEditor el={el} entityId={entityId} ctx={ctx} onChange={onChange} />;
    case "tab_group":
      return <TabGroupEditor el={el} entityId={entityId} ctx={ctx} allow={allow} onChange={onChange} />;
    case "accordion":
      return <AccordionEditor el={el} entityId={entityId} ctx={ctx} allow={allow} onChange={onChange} />;
    case "columns":
      return <ColumnsEditor el={el} entityId={entityId} ctx={ctx} allow={allow} onChange={onChange} />;
    case "panel":
      return <PanelEditor el={el} entityId={entityId} ctx={ctx} allow={allow} onChange={onChange} />;
    default:
      return null;
  }
}

function RecordListEditor({
  el,
  ctx,
  onChange,
}: {
  el: RecordListElement;
  ctx: BuilderCtx;
  onChange: (el: FormElement) => void;
}) {
  const entities = [...ctx.entitiesById.values()];
  const filters = el.filters ?? [];
  // Stable client keys per filter row (parallel to `filters`, never persisted) so
  // deleting a middle row doesn't reassign identities below it — an index key would
  // shift them and jump input focus. Falls back to the index if the two drift (e.g.
  // an external edit adds a filter without going through these handlers).
  const [rowKeys, setRowKeys] = useState<number[]>(() => filters.map((_, i) => i));
  const nextKey = useRef(filters.length);
  const setFilters = (next: RecordListFilterConfig[], keys?: number[]) => {
    if (keys) setRowKeys(keys);
    onChange({ ...el, filters: next });
  };
  const addFilter = () =>
    setFilters([...filters, { field: "", op: "eq", value: "" }], [...rowKeys, nextKey.current++]);
  const removeFilter = (i: number) =>
    setFilters(filters.filter((_, j) => j !== i), rowKeys.filter((_, j) => j !== i));
  const updateFilter = (i: number, patch: Partial<RecordListFilterConfig>) =>
    setFilters(filters.map((f, j) => (j === i ? { ...f, ...patch } : f)));

  return (
    <div className="space-y-1.5">
      <Row label="Label">
        <input
          className={input}
          value={el.label ?? ""}
          onChange={(e) => onChange({ ...el, label: e.target.value || null })}
        />
      </Row>
      <Row label="Entity">
        <select className={input} value={el.entity} onChange={(e) => onChange({ ...el, entity: e.target.value })}>
          <option value="">Select entity…</option>
          {entities.map((ent) => (
            <option key={ent.id} value={ent.slug}>
              {ent.name}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Columns">
        <input
          className={input}
          placeholder="field slugs, comma-separated (blank = all)"
          value={(el.fields ?? []).join(", ")}
          onChange={(e) =>
            onChange({
              ...el,
              fields: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
        />
      </Row>
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-muted-foreground">Filters (all must match)</span>
          <button type="button" className={btn} onClick={addFilter}>
            <Plus className="h-3 w-3" /> Filter
          </button>
        </div>
        {filters.map((f, i) => (
          <div key={rowKeys[i] ?? i} className="flex items-center gap-1.5">
            <input
              className={input}
              placeholder="field / relation slug"
              value={f.field}
              onChange={(e) => updateFilter(i, { field: e.target.value })}
            />
            <select
              className={input}
              value={f.op ?? "eq"}
              onChange={(e) => updateFilter(i, { op: e.target.value as (typeof FILTER_OPERATORS)[number] })}
            >
              {FILTER_OPERATORS.map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
            <input
              className={input}
              placeholder="value ( @me = current user )"
              value={f.value == null ? "" : String(f.value)}
              onChange={(e) => updateFilter(i, { value: e.target.value })}
            />
            <button
              type="button"
              className={btn}
              onClick={() => removeFilter(i)}
              aria-label="Remove filter"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        ))}
        <p className="text-[11px] text-muted-foreground">
          Tip: a value of <code>@me</code> on a relation field (e.g. learner) scopes the list to the current
          user’s own records.
        </p>
      </div>
      <Row label="Sort by">
        <input
          className={input}
          placeholder="field slug (blank = created_at)"
          value={el.sort_by ?? ""}
          onChange={(e) => onChange({ ...el, sort_by: e.target.value || null })}
        />
      </Row>
      <Row label="Sort dir">
        <select
          className={input}
          value={el.sort_dir ?? "desc"}
          onChange={(e) => onChange({ ...el, sort_dir: e.target.value as "asc" | "desc" })}
        >
          <option value="desc">Desc</option>
          <option value="asc">Asc</option>
        </select>
      </Row>
      <Row label="Limit">
        <input
          className={input}
          type="number"
          value={el.limit ?? 20}
          onChange={(e) => onChange({ ...el, limit: Number(e.target.value) || 20 })}
        />
      </Row>
      <Row label="Poll (ms)">
        <input
          className={input}
          type="number"
          placeholder="blank = fetch once"
          value={el.poll_ms ?? ""}
          onChange={(e) => onChange({ ...el, poll_ms: Number(e.target.value) || null })}
        />
      </Row>
      <Row label="Empty text">
        <input
          className={input}
          value={el.empty_text ?? ""}
          onChange={(e) => onChange({ ...el, empty_text: e.target.value || null })}
        />
      </Row>
    </div>
  );
}

function SlidesEditor({
  el,
  onChange,
}: {
  el: SlidesElement;
  onChange: (el: FormElement) => void;
}) {
  const slides = el.slides ?? [];
  const setSlides = (next: Slide[]) => onChange({ ...el, slides: next });
  const updateSlide = (i: number, patch: Partial<Slide>) =>
    setSlides(slides.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  return (
    <div className="space-y-1.5">
      <Row label="Label">
        <input
          className={input}
          value={el.label ?? ""}
          onChange={(e) => onChange({ ...el, label: e.target.value || null })}
        />
      </Row>
      <Row label="Bind to field">
        <input
          className={input}
          placeholder="JSON field slug (e.g. slides) — blank = inline"
          value={el.slug ?? ""}
          onChange={(e) => onChange({ ...el, slug: e.target.value || null })}
        />
      </Row>
      {el.slug ? (
        <p className="text-[11px] text-muted-foreground">
          Bound to <code>{el.slug}</code>: slides come from that record field. Clear it to author inline
          slides here instead.
        </p>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">Inline slides</span>
            <button
              type="button"
              className={btn}
              onClick={() => setSlides([...slides, { title: `Slide ${slides.length + 1}`, body: "" }])}
            >
              <Plus className="h-3 w-3" /> Slide
            </button>
          </div>
          {slides.map((s, i) => (
            <div key={i} className="space-y-1 rounded-md border border-dashed p-2">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium text-muted-foreground">Slide {i + 1}</span>
                <button
                  type="button"
                  className={`${btn} text-destructive`}
                  onClick={() => setSlides(slides.filter((_, j) => j !== i))}
                  aria-label="Remove slide"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
              <input
                className={input}
                placeholder="Title"
                value={s.title ?? ""}
                onChange={(e) => updateSlide(i, { title: e.target.value || null })}
              />
              <textarea
                className={`${input} font-mono`}
                rows={3}
                placeholder="Body (Markdown)"
                value={s.body ?? ""}
                onChange={(e) => updateSlide(i, { body: e.target.value })}
              />
              <input
                className={input}
                placeholder="Image URL (optional)"
                value={s.image_url ?? ""}
                onChange={(e) => updateSlide(i, { image_url: e.target.value || null })}
              />
              <input
                className={input}
                placeholder="Video URL — direct mp4/webm (optional)"
                value={s.video_url ?? ""}
                onChange={(e) => updateSlide(i, { video_url: e.target.value || null })}
              />
              {s.video_url ? (
                <label className="flex items-center gap-1 text-xs">
                  <input
                    type="checkbox"
                    checked={s.require_video !== false}
                    onChange={(e) => updateSlide(i, { require_video: e.target.checked })}
                  />
                  Nudge learner to finish before advancing
                </label>
              ) : null}
            </div>
          ))}
          {slides.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">No slides yet — add one, or bind a field above.</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function FormRefEditor({
  el,
  forms,
  onChange,
}: {
  el: Extract<FormElement, { type: "form_ref" }>;
  forms: { id: string; name: string }[];
  onChange: (el: FormElement) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Row label="Form">
        <select
          className={input}
          value={el.form_id}
          onChange={(e) => onChange({ ...el, form_id: e.target.value })}
        >
          <option value="">Select form…</option>
          {forms.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Label">
        <input
          className={input}
          value={el.label ?? ""}
          onChange={(e) => onChange({ ...el, label: e.target.value || null })}
        />
      </Row>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function FieldEditor({
  el,
  fields,
  onChange,
}: {
  el: FieldElement;
  fields: EntityField[];
  onChange: (el: FieldElement) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Row label="Field">
        <select className={input} value={el.slug} onChange={(e) => onChange({ ...el, slug: e.target.value })}>
          <option value="">Select field…</option>
          {fields.map((f) => (
            <option key={f.slug} value={f.slug}>
              {f.name}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Label">
        <input
          className={input}
          value={el.label ?? ""}
          placeholder="(field name)"
          onChange={(e) => onChange({ ...el, label: e.target.value || null })}
        />
      </Row>
      <Row label="Width">
        <select
          className={input}
          value={el.width ?? "full"}
          onChange={(e) => onChange({ ...el, width: e.target.value as FieldElement["width"] })}
        >
          <option value="full">Full</option>
          <option value="half">Half</option>
          <option value="third">Third</option>
          <option value="quarter">Quarter</option>
        </select>
      </Row>
      <div className="flex gap-4 text-xs">
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={!!el.required}
            onChange={(e) => onChange({ ...el, required: e.target.checked })}
          />
          Required
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={!!el.read_only}
            onChange={(e) => onChange({ ...el, read_only: e.target.checked })}
          />
          Read-only
        </label>
      </div>
    </div>
  );
}

function LabelEditor({ el, onChange }: { el: LabelElement; onChange: (el: LabelElement) => void }) {
  return (
    <div className="space-y-1.5">
      <Row label="Text">
        <input className={input} value={el.text} onChange={(e) => onChange({ ...el, text: e.target.value })} />
      </Row>
      <Row label="Variant">
        <select
          className={input}
          value={el.variant}
          onChange={(e) => onChange({ ...el, variant: e.target.value as LabelElement["variant"] })}
        >
          <option value="heading">Heading</option>
          <option value="subheading">Subheading</option>
          <option value="paragraph">Paragraph</option>
          <option value="divider">Divider</option>
        </select>
      </Row>
    </div>
  );
}

function CalculatedEditor({
  el,
  fields,
  onChange,
}: {
  el: CalculatedElement;
  fields: EntityField[];
  onChange: (el: CalculatedElement) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Row label="Label">
        <input
          className={input}
          value={el.label ?? ""}
          onChange={(e) => onChange({ ...el, label: e.target.value || null })}
        />
      </Row>
      <Row label="Result">
        <select
          className={input}
          value={el.result_type}
          onChange={(e) => onChange({ ...el, result_type: e.target.value as CalculatedElement["result_type"] })}
        >
          {["text", "integer", "numeric", "boolean", "date", "timestamptz"].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Save to">
        <select
          className={input}
          value={el.target_slug ?? ""}
          onChange={(e) => onChange({ ...el, target_slug: e.target.value || null })}
        >
          <option value="">Display only</option>
          {fields.map((f) => (
            <option key={f.slug} value={f.slug}>
              {f.name}
            </option>
          ))}
        </select>
      </Row>
      <label className="block text-xs">
        <span className="text-muted-foreground">Expression (JsonLogic)</span>
        <textarea
          className={`${input} font-mono`}
          rows={3}
          value={JSON.stringify(el.expression)}
          onChange={(e) => {
            try {
              onChange({ ...el, expression: JSON.parse(e.target.value) });
            } catch {
              /* keep last valid value while typing invalid JSON */
            }
          }}
        />
      </label>
    </div>
  );
}

type InputEl = Extract<FormElement, { type: "input" }>;

function InputEditor({ el, onChange }: { el: InputEl; onChange: (el: FormElement) => void }) {
  const numeric = el.control === "number" || el.control === "slider";
  return (
    <div className="space-y-1.5">
      <Row label="Key">
        <input
          className={input}
          value={el.key}
          placeholder="value_key"
          onChange={(e) => onChange({ ...el, key: e.target.value })}
        />
      </Row>
      <Row label="Control">
        <select
          className={input}
          value={el.control}
          onChange={(e) => onChange({ ...el, control: e.target.value as InputEl["control"] })}
        >
          {["text", "textarea", "number", "slider", "toggle", "select"].map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Label">
        <input
          className={input}
          value={el.label ?? ""}
          onChange={(e) => onChange({ ...el, label: e.target.value || null })}
        />
      </Row>
      {numeric ? (
        <Row label="Min / Max / Step">
          <div className="flex gap-1">
            {(["min", "max", "step"] as const).map((k) => (
              <input
                key={k}
                className={input}
                type="number"
                placeholder={k}
                value={el[k] ?? ""}
                onChange={(e) =>
                  onChange({ ...el, [k]: e.target.value === "" ? null : Number(e.target.value) })
                }
              />
            ))}
          </div>
        </Row>
      ) : null}
      {el.control === "select" ? (
        <Row label="Options">
          <input
            className={input}
            placeholder="a, b, c"
            value={(el.options ?? []).map((o) => o.value).join(", ")}
            onChange={(e) =>
              onChange({
                ...el,
                options: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean)
                  .map((v) => ({ value: v })),
              })
            }
          />
        </Row>
      ) : null}
      <Row label="Default">
        <input
          className={input}
          value={el.default == null ? "" : String(el.default)}
          onChange={(e) => {
            const raw = e.target.value;
            const value: string | number | boolean | null =
              raw === ""
                ? null
                : el.control === "toggle"
                  ? raw === "true"
                  : numeric
                    ? Number(raw)
                    : raw;
            onChange({ ...el, default: value });
          }}
        />
      </Row>
    </div>
  );
}

type LiveValueEl = Extract<FormElement, { type: "live_value" }>;

function LiveValueEditor({ el, onChange }: { el: LiveValueEl; onChange: (el: FormElement) => void }) {
  return (
    <div className="space-y-1.5">
      <Row label="Label">
        <input
          className={input}
          value={el.label ?? ""}
          onChange={(e) => onChange({ ...el, label: e.target.value || null })}
        />
      </Row>
      <Row label="URL">
        <input
          className={input}
          placeholder="http://localhost:8080/senses"
          value={el.url}
          onChange={(e) => onChange({ ...el, url: e.target.value })}
        />
      </Row>
      <Row label="JSON path">
        <input
          className={input}
          placeholder="head.pitch"
          value={el.json_pointer ?? ""}
          onChange={(e) => onChange({ ...el, json_pointer: e.target.value || null })}
        />
      </Row>
      <Row label="Poll (ms)">
        <input
          className={input}
          type="number"
          value={el.poll_ms ?? 1000}
          onChange={(e) => onChange({ ...el, poll_ms: Number(e.target.value) || 1000 })}
        />
      </Row>
      <Row label="Units">
        <input
          className={input}
          value={el.units ?? ""}
          onChange={(e) => onChange({ ...el, units: e.target.value || null })}
        />
      </Row>
    </div>
  );
}

type ReportEl = Extract<FormElement, { type: "report" }>;

function ReportEditor({ el, onChange }: { el: ReportEl; onChange: (el: FormElement) => void }) {
  const [reports, setReports] = useState<Array<{ id: string; name: string }>>([]);

  useEffect(() => {
    let alive = true;
    void listReports()
      .then((rows) => {
        if (alive) setReports(rows.map((r) => ({ id: r.id, name: r.name })));
      })
      .catch(() => {
        /* leave the list empty; the id can still be typed */
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="space-y-1.5">
      <Row label="Report">
        <select
          className={input}
          value={el.report_id}
          onChange={(e) => onChange({ ...el, report_id: e.target.value })}
        >
          <option value="">— pick a report —</option>
          {reports.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Title">
        <input
          className={input}
          value={el.title ?? ""}
          onChange={(e) => onChange({ ...el, title: e.target.value || null })}
        />
      </Row>
      <Row label="Height (px)">
        <input
          className={input}
          type="number"
          value={el.height ?? 320}
          onChange={(e) => onChange({ ...el, height: Number(e.target.value) || null })}
        />
      </Row>
      <Row label="Poll (ms)">
        <input
          className={input}
          type="number"
          placeholder="off"
          value={el.poll_ms ?? ""}
          onChange={(e) => onChange({ ...el, poll_ms: Number(e.target.value) || null })}
        />
      </Row>
    </div>
  );
}

type ChatEl = Extract<FormElement, { type: "chat" }>;

function ChatEditor({ el, onChange }: { el: ChatEl; onChange: (el: FormElement) => void }) {
  return (
    <div className="space-y-1.5">
      <Row label="Title">
        <input
          className={input}
          value={el.title ?? ""}
          onChange={(e) => onChange({ ...el, title: e.target.value || null })}
        />
      </Row>
      <Row label="Answer workflow id">
        <input
          className={input}
          placeholder="Robot: Chat Answer workflow id"
          value={el.answer_workflow_id ?? ""}
          onChange={(e) => onChange({ ...el, answer_workflow_id: e.target.value || null })}
        />
      </Row>
      <Row label="Message entity">
        <input
          className={input}
          value={el.message_entity ?? "robot_message"}
          onChange={(e) => onChange({ ...el, message_entity: e.target.value })}
        />
      </Row>
      <Row label="Conversation entity">
        <input
          className={input}
          value={el.conversation_entity ?? "robot_conversation"}
          onChange={(e) => onChange({ ...el, conversation_entity: e.target.value })}
        />
      </Row>
      <Row label="Conversation link slug">
        <input
          className={input}
          value={el.conversation_relationship ?? "conversation"}
          onChange={(e) => onChange({ ...el, conversation_relationship: e.target.value })}
        />
      </Row>
      <Row label="Poll (ms)">
        <input
          className={input}
          type="number"
          value={el.poll_ms ?? 1500}
          onChange={(e) => onChange({ ...el, poll_ms: Number(e.target.value) || 1500 })}
        />
      </Row>
      <Row label="Placeholder">
        <input
          className={input}
          value={el.placeholder ?? ""}
          onChange={(e) => onChange({ ...el, placeholder: e.target.value })}
        />
      </Row>
      <ChatAnswerControlsEditor el={el} onChange={onChange} />
      <ChatFillerEditor el={el} onChange={onChange} />
    </div>
  );
}

/** Builder controls for the "one moment…" filler chatter the chat shows (and optionally
 * speaks) while a slow answer is still being generated. */
function ChatFillerEditor({ el, onChange }: { el: ChatEl; onChange: (el: FormElement) => void }) {
  const f = el.filler ?? {};
  const patch = (next: Partial<NonNullable<ChatEl["filler"]>>) =>
    onChange({ ...el, filler: { ...f, ...next } });
  return (
    <div className="mt-1.5 space-y-1.5 rounded-md border border-dashed p-2">
      <label className="flex items-center gap-2 text-xs font-medium">
        <input type="checkbox" checked={f.show ?? false} onChange={(e) => patch({ show: e.target.checked })} />
        <span>Fill wait with &ldquo;one moment…&rdquo; chatter</span>
      </label>
      {f.show ? (
        <>
          <p className="text-[11px] text-muted-foreground">
            While the answer is still cooking, drip out a randomized line (first after{" "}
            <em>Delay</em>, then every <em>Interval</em>) so a slow reply feels responsive. Set a{" "}
            <em>Speak connection</em> to have the robot say it out loud too.
          </p>
          <Row label="Delay (ms)">
            <input
              className={input}
              type="number"
              value={f.delay_ms ?? 1400}
              onChange={(e) => patch({ delay_ms: Number(e.target.value) || 1400 })}
            />
          </Row>
          <Row label="Interval (ms)">
            <input
              className={input}
              type="number"
              value={f.interval_ms ?? 6000}
              onChange={(e) => patch({ interval_ms: Number(e.target.value) || 6000 })}
            />
          </Row>
          <Row label="Max lines">
            <input
              className={input}
              type="number"
              value={f.max_lines ?? 2}
              onChange={(e) => patch({ max_lines: Number(e.target.value) || 2 })}
            />
          </Row>
          <Row label="Speak connection">
            <input
              className={input}
              placeholder="robot (optional)"
              value={f.speak_connection ?? ""}
              onChange={(e) => patch({ speak_connection: e.target.value || null })}
            />
          </Row>
          <Row label="Phrases">
            <textarea
              className={input}
              rows={3}
              placeholder={"One line each. {q} = the question.\nLeave blank for the built-in set."}
              value={(f.phrases ?? []).join("\n")}
              onChange={(e) =>
                patch({
                  phrases: e.target.value
                    .split("\n")
                    .map((p) => p.trim())
                    .filter(Boolean),
                })
              }
            />
          </Row>
        </>
      ) : null}
    </div>
  );
}

/** Builder controls for the live answer-speed toggle row (Fast mode / Knowledge graph
 * / Concise / Answer model) the chat card can render at runtime. */
function ChatAnswerControlsEditor({ el, onChange }: { el: ChatEl; onChange: (el: FormElement) => void }) {
  const ac = el.answer_controls ?? {};
  const patch = (next: Partial<NonNullable<ChatEl["answer_controls"]>>) =>
    onChange({ ...el, answer_controls: { ...ac, ...next } });
  return (
    <div className="mt-1.5 space-y-1.5 rounded-md border border-dashed p-2">
      <label className="flex items-center gap-2 text-xs font-medium">
        <input
          type="checkbox"
          checked={ac.show ?? false}
          onChange={(e) => patch({ show: e.target.checked })}
        />
        <span>Show answer speed controls</span>
      </label>
      {ac.show ? (
        <>
          <p className="text-[11px] text-muted-foreground">
            Initial state — viewers can flip these per turn. The workflow must read{" "}
            <code>inputs.synthesize</code>, <code>inputs.use_knowledge_graph</code>,{" "}
            <code>inputs.answer_model</code>, <code>inputs.max_words</code>.
          </p>
          <Row label="Fast mode">
            <input
              type="checkbox"
              checked={ac.fast_mode ?? true}
              onChange={(e) => patch({ fast_mode: e.target.checked })}
            />
          </Row>
          <Row label="Knowledge graph">
            <input
              type="checkbox"
              checked={ac.knowledge_graph ?? false}
              onChange={(e) => patch({ knowledge_graph: e.target.checked })}
            />
          </Row>
          <Row label="Concise">
            <input
              type="checkbox"
              checked={ac.concise ?? true}
              onChange={(e) => patch({ concise: e.target.checked })}
            />
          </Row>
          <Row label="Speak aloud">
            <input
              type="checkbox"
              checked={ac.speak ?? true}
              onChange={(e) => patch({ speak: e.target.checked })}
            />
          </Row>
          <Row label="Models">
            <input
              className={input}
              placeholder="gpt-5-nano, gpt-5-mini"
              value={(ac.models ?? []).join(", ")}
              onChange={(e) =>
                patch({
                  models: e.target.value
                    .split(",")
                    .map((m) => m.trim())
                    .filter(Boolean),
                })
              }
            />
          </Row>
          <Row label="Concise words">
            <input
              className={input}
              type="number"
              value={ac.concise_words ?? 20}
              onChange={(e) => patch({ concise_words: Number(e.target.value) || 20 })}
            />
          </Row>
          <Row label="Full words">
            <input
              className={input}
              type="number"
              value={ac.verbose_words ?? 45}
              onChange={(e) => patch({ verbose_words: Number(e.target.value) || 45 })}
            />
          </Row>
        </>
      ) : null}
    </div>
  );
}

function ButtonEditor({ el, onChange }: { el: ButtonElement; onChange: (el: ButtonElement) => void }) {
  return (
    <div className="space-y-1.5">
      <Row label="Label">
        <input className={input} value={el.label} onChange={(e) => onChange({ ...el, label: e.target.value })} />
      </Row>
      <Row label="Style">
        <select
          className={input}
          value={el.style}
          onChange={(e) => onChange({ ...el, style: e.target.value as ButtonElement["style"] })}
        >
          {["primary", "secondary", "danger", "ghost"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </Row>
      <Row label="Action">
        <select
          className={input}
          value={el.action.kind}
          onChange={(e) => {
            const kind = e.target.value;
            if (kind === "submit") onChange({ ...el, action: { kind: "submit" } });
            else if (kind === "run_workflow")
              onChange({ ...el, action: { kind: "run_workflow", workflow_id: "", inputs: {} } });
            else if (kind === "call_connection")
              onChange({ ...el, action: { kind: "call_connection", connection: "", method: "POST", path: "", body: {} } });
            else onChange({ ...el, action: { kind: "link", href: "" } });
          }}
        >
          <option value="submit">Submit form</option>
          <option value="run_workflow">Run workflow</option>
          <option value="call_connection">Call connection</option>
          <option value="link">Link / navigate</option>
        </select>
      </Row>
      {el.action.kind === "run_workflow" ? (
        <Row label="Workflow id">
          <input
            className={input}
            value={el.action.workflow_id}
            onChange={(e) => {
              const action = el.action.kind === "run_workflow" ? el.action : { kind: "run_workflow" as const, workflow_id: "", inputs: {} };
              onChange({ ...el, action: { ...action, workflow_id: e.target.value } });
            }}
          />
        </Row>
      ) : null}
      {el.action.kind === "call_connection" ? <CallConnectionFields el={el} onChange={onChange} /> : null}
      {el.action.kind === "link" ? (
        <Row label="Href">
          <input
            className={input}
            value={el.action.href}
            onChange={(e) => onChange({ ...el, action: { kind: "link", href: e.target.value } })}
          />
        </Row>
      ) : null}
    </div>
  );
}

function CallConnectionFields({
  el,
  onChange,
}: {
  el: ButtonElement;
  onChange: (el: ButtonElement) => void;
}) {
  if (el.action.kind !== "call_connection") return null;
  const action = el.action;
  const set = (patch: Partial<typeof action>) => onChange({ ...el, action: { ...action, ...patch } });
  return (
    <>
      <Row label="Connection">
        <input
          className={input}
          placeholder="robot"
          value={action.connection}
          onChange={(e) => set({ connection: e.target.value })}
        />
      </Row>
      <Row label="Method / Path">
        <div className="flex gap-1">
          <select
            className={input}
            value={action.method ?? "POST"}
            onChange={(e) => set({ method: e.target.value as NonNullable<typeof action.method> })}
          >
            {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <input
            className={input}
            placeholder="/head"
            value={action.path ?? ""}
            onChange={(e) => set({ path: e.target.value })}
          />
        </div>
      </Row>
      <Row label="Body (JSON)">
        <textarea
          className={`${input} font-mono`}
          rows={3}
          placeholder={'{ "yaw": { "var": "body_yaw" } }'}
          defaultValue={JSON.stringify(action.body ?? {}, null, 2)}
          onBlur={(e) => {
            try {
              set({ body: JSON.parse(e.target.value || "{}") });
            } catch {
              /* keep last valid body; invalid JSON is ignored on blur */
            }
          }}
        />
      </Row>
    </>
  );
}

function RelationshipPicker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: EntityRelationship[];
  onChange: (id: string) => void;
}) {
  return (
    <Row label={label}>
      <select className={input} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Select…</option>
        {options.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>
    </Row>
  );
}

function SectionEditor({
  el,
  entityId,
  ctx,
  onChange,
}: {
  el: SectionElement;
  entityId: string;
  ctx: BuilderCtx;
  onChange: (el: SectionElement) => void;
}) {
  const rels = ctx.toOneOutgoing(entityId);
  const rel = rels.find((r) => r.id === el.relationship_id);
  const childEntity = rel?.target_definition_id;
  return (
    <div className="space-y-2">
      <RelationshipPicker
        label="Relationship"
        value={el.relationship_id}
        options={rels}
        onChange={(id) => onChange({ ...el, relationship_id: id })}
      />
      <Row label="Mode">
        <select
          className={input}
          value={el.mode}
          onChange={(e) => onChange({ ...el, mode: e.target.value as SectionElement["mode"] })}
        >
          <option value="inline">Inline</option>
          <option value="modal">Modal</option>
        </select>
      </Row>
      {childEntity ? (
        <div className="border-l-2 pl-2">
          <LayoutBuilder
            elements={el.elements}
            entityId={childEntity}
            ctx={ctx}
            allow="leaf"
            onChange={(els) => onChange({ ...el, elements: els as SectionElement["elements"] })}
          />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Pick a relationship to add fields.</p>
      )}
    </div>
  );
}

function BlockEditor({
  el,
  entityId,
  ctx,
  onChange,
}: {
  el: Extract<FormElement, { type: "block" }>;
  entityId: string;
  ctx: BuilderCtx;
  onChange: (el: FormElement) => void;
}) {
  const rels = ctx.incomingToMany(entityId);
  const rel = rels.find((r) => r.id === el.anchor_relationship_id);
  const childEntity = rel?.source_definition_id;
  return (
    <div className="space-y-2">
      <RelationshipPicker
        label="Collection"
        value={el.anchor_relationship_id}
        options={rels}
        onChange={(id) => onChange({ ...el, anchor_relationship_id: id })}
      />
      {childEntity ? (
        <div className="border-l-2 pl-2">
          <LayoutBuilder
            elements={el.elements}
            entityId={childEntity}
            ctx={ctx}
            allow="leaf"
            onChange={(els) => onChange({ ...el, elements: els as typeof el.elements })}
          />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Pick a collection to add fields.</p>
      )}
    </div>
  );
}

function TableEditor({
  el,
  entityId,
  ctx,
  onChange,
}: {
  el: TableElement;
  entityId: string;
  ctx: BuilderCtx;
  onChange: (el: TableElement) => void;
}) {
  const rels = ctx.incomingToMany(entityId);
  const rel = rels.find((r) => r.id === el.anchor_relationship_id);
  const childEntity = rel?.source_definition_id;
  const childFields = childEntity ? fieldsOf(ctx, childEntity) : [];
  const childRels = childEntity ? ctx.toOneOutgoing(childEntity) : [];

  const setColumns = (columns: TableColumn[]) => onChange({ ...el, columns });
  const addFieldCol = () => setColumns([...el.columns, { kind: "field", slug: "" }]);
  const addRelatedCol = () =>
    setColumns([...el.columns, { kind: "related", relationship_id: "", slug: "", editable: true }]);
  const addLinkCol = () =>
    setColumns([...el.columns, { kind: "link", href_template: "", link_label: "Open" }]);

  return (
    <div className="space-y-2">
      <RelationshipPicker
        label="Collection"
        value={el.anchor_relationship_id}
        options={rels}
        onChange={(id) => onChange({ ...el, anchor_relationship_id: id })}
      />
      {childEntity ? (
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Sort by</span>
            <select
              className={input}
              value={el.sort_by ?? ""}
              onChange={(e) => onChange({ ...el, sort_by: e.target.value || null })}
            >
              <option value="">Default order</option>
              {childFields.map((f) => (
                <option key={f.slug} value={f.slug}>
                  {f.name}
                </option>
              ))}
            </select>
            <select
              className={input}
              value={el.sort_dir ?? "asc"}
              onChange={(e) => onChange({ ...el, sort_dir: e.target.value as "asc" | "desc" })}
              disabled={!el.sort_by}
            >
              <option value="asc">Asc</option>
              <option value="desc">Desc</option>
            </select>
          </div>
          <p className="text-xs font-medium text-muted-foreground">Columns</p>
          {el.columns.map((col, ci) => (
            <div key={ci} className="flex items-center gap-2">
              {col.kind === "field" ? (
                <select
                  className={input}
                  value={col.slug}
                  onChange={(e) =>
                    setColumns(el.columns.map((c, j) => (j === ci ? { ...c, slug: e.target.value } : c)))
                  }
                >
                  <option value="">Field…</option>
                  {childFields.map((f) => (
                    <option key={f.slug} value={f.slug}>
                      {f.name}
                    </option>
                  ))}
                </select>
              ) : col.kind === "related" ? (
                <>
                  <select
                    className={input}
                    value={col.relationship_id}
                    onChange={(e) =>
                      setColumns(
                        el.columns.map((c, j) =>
                          j === ci ? { ...c, relationship_id: e.target.value } : c,
                        ),
                      )
                    }
                  >
                    <option value="">Relation…</option>
                    {childRels.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                  <select
                    className={input}
                    value={col.slug}
                    onChange={(e) =>
                      setColumns(el.columns.map((c, j) => (j === ci ? { ...c, slug: e.target.value } : c)))
                    }
                  >
                    <option value="">Field…</option>
                    {(ctx.entitiesById
                      .get(childRels.find((r) => r.id === col.relationship_id)?.target_definition_id ?? "")
                      ?.fields ?? []
                    ).map((f) => (
                      <option key={f.slug} value={f.slug}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </>
              ) : (
                <>
                  <input
                    className={input}
                    placeholder="/documents/{document_key}"
                    value={col.href_template}
                    onChange={(e) =>
                      setColumns(
                        el.columns.map((c, j) =>
                          j === ci ? { ...c, href_template: e.target.value } : c,
                        ),
                      )
                    }
                  />
                  <input
                    className={input}
                    placeholder="Link text"
                    value={col.link_label ?? ""}
                    onChange={(e) =>
                      setColumns(
                        el.columns.map((c, j) => (j === ci ? { ...c, link_label: e.target.value } : c)),
                      )
                    }
                  />
                </>
              )}
              <button
                type="button"
                className={`${btn} text-destructive`}
                onClick={() => setColumns(el.columns.filter((_, j) => j !== ci))}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))}
          <div className="flex gap-2">
            <button type="button" className={btn} onClick={addFieldCol}>
              <Plus className="h-3 w-3" /> Field column
            </button>
            <button type="button" className={btn} onClick={addRelatedCol}>
              <Plus className="h-3 w-3" /> Related column
            </button>
            <button type="button" className={btn} onClick={addLinkCol}>
              <Plus className="h-3 w-3" /> Link column
            </button>
          </div>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">Pick a collection to add columns.</p>
      )}
    </div>
  );
}

function TabGroupEditor({
  el,
  entityId,
  ctx,
  allow,
  onChange,
}: {
  el: Extract<FormElement, { type: "tab_group" }>;
  entityId: string;
  ctx: BuilderCtx;
  allow: "all" | "leaf" | "view";
  onChange: (el: FormElement) => void;
}) {
  return (
    <div className="space-y-2">
      {el.tabs.map((tab, ti) => (
        <div key={ti} className="rounded-md border p-2">
          <input
            className={`${input} mb-1 font-medium`}
            value={tab.label}
            onChange={(e) =>
              onChange({
                ...el,
                tabs: el.tabs.map((t, j) => (j === ti ? { ...t, label: e.target.value } : t)),
              })
            }
          />
          <LayoutBuilder
            elements={tab.elements}
            entityId={entityId}
            ctx={ctx}
            allow={allow}
            onChange={(els) =>
              onChange({ ...el, tabs: el.tabs.map((t, j) => (j === ti ? { ...t, elements: els } : t)) })
            }
          />
        </div>
      ))}
      <button
        type="button"
        className={btn}
        onClick={() => onChange({ ...el, tabs: [...el.tabs, { label: `Tab ${el.tabs.length + 1}`, elements: [] }] })}
      >
        <Plus className="h-3 w-3" /> Add tab
      </button>
    </div>
  );
}

function AccordionEditor({
  el,
  entityId,
  ctx,
  allow,
  onChange,
}: {
  el: Extract<FormElement, { type: "accordion" }>;
  entityId: string;
  ctx: BuilderCtx;
  allow: "all" | "leaf" | "view";
  onChange: (el: FormElement) => void;
}) {
  return (
    <div className="space-y-2">
      {el.panes.map((pane, pi) => (
        <div key={pi} className="rounded-md border p-2">
          <input
            className={`${input} mb-1 font-medium`}
            value={pane.label}
            onChange={(e) =>
              onChange({
                ...el,
                panes: el.panes.map((p, j) => (j === pi ? { ...p, label: e.target.value } : p)),
              })
            }
          />
          <LayoutBuilder
            elements={pane.elements}
            entityId={entityId}
            ctx={ctx}
            allow={allow}
            onChange={(els) =>
              onChange({ ...el, panes: el.panes.map((p, j) => (j === pi ? { ...p, elements: els } : p)) })
            }
          />
        </div>
      ))}
      <button
        type="button"
        className={btn}
        onClick={() =>
          onChange({ ...el, panes: [...el.panes, { label: `Section ${el.panes.length + 1}`, elements: [] }] })
        }
      >
        <Plus className="h-3 w-3" /> Add pane
      </button>
    </div>
  );
}

function ColumnsEditor({
  el,
  entityId,
  ctx,
  allow,
  onChange,
}: {
  el: Extract<FormElement, { type: "columns" }>;
  entityId: string;
  ctx: BuilderCtx;
  allow: "all" | "leaf" | "view";
  onChange: (el: FormElement) => void;
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {el.columns.map((col, ci) => (
        <div key={ci} className="rounded-md border p-2">
          <LayoutBuilder
            elements={col.elements}
            entityId={entityId}
            ctx={ctx}
            allow={allow}
            onChange={(els) =>
              onChange({ ...el, columns: el.columns.map((c, j) => (j === ci ? { ...c, elements: els } : c)) })
            }
          />
        </div>
      ))}
    </div>
  );
}

function PanelEditor({
  el,
  entityId,
  ctx,
  allow,
  onChange,
}: {
  el: Extract<FormElement, { type: "panel" }>;
  entityId: string;
  ctx: BuilderCtx;
  allow: "all" | "leaf" | "view";
  onChange: (el: FormElement) => void;
}) {
  return (
    <div className="space-y-2">
      <Row label="Title">
        <input
          className={input}
          value={el.title ?? ""}
          onChange={(e) => onChange({ ...el, title: e.target.value || null })}
        />
      </Row>
      <div className="border-l-2 pl-2">
        <LayoutBuilder
          elements={el.elements}
          entityId={entityId}
          ctx={ctx}
          allow={allow}
          onChange={(els) => onChange({ ...el, elements: els })}
        />
      </div>
    </div>
  );
}
