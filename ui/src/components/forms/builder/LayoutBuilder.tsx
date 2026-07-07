"use client";

import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";

import type { EntityDefinition, EntityField, EntityRelationship } from "@/lib/api/entities";
import type {
  ButtonElement,
  CalculatedElement,
  FieldElement,
  FormElement,
  LabelElement,
  SectionElement,
  TableColumn,
  TableElement,
} from "@/lib/api/forms";

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
        <div key={el.id ?? i} className={box}>
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
            else onChange({ ...el, action: { kind: "link", href: "" } });
          }}
        >
          <option value="submit">Submit form</option>
          <option value="run_workflow">Run workflow</option>
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
              ) : (
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
