"use client";

import { Plus, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  getFormRender,
  type ButtonElement,
  type CalculatedElement,
  type FormElement,
  type FormRender,
  type FormSubmit,
  type SectionElement,
  type TableElement,
} from "@/lib/api/forms";
import { buildCatalog, type Catalog, fieldMeta, relatedEntityId } from "@/lib/forms/catalog";
import { evaluate } from "@/lib/forms/jsonLogic";

import { FieldControl } from "./FieldControl";

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
  onRunWorkflow?: (workflowId: string, inputs: Record<string, unknown>) => Promise<void> | void;
  submitting?: boolean;
  /** When set (fill mode), render a submit button in the footer. */
  defaultSubmitLabel?: string;
  /** Page-controlled error to show above the footer submit button. */
  error?: string | null;
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
function spanClass(width?: string | null): string {
  return SPAN[width ?? "full"] ?? "sm:col-span-12";
}

function nonEmpty(v: Values): boolean {
  return Object.values(v).some((x) => x !== "" && x != null);
}

export function FormRenderer({
  render,
  mode = "fill",
  onSubmit,
  onRunWorkflow,
  submitting = false,
  defaultSubmitLabel,
  error,
}: FormRendererProps) {
  const catalog = useMemo(() => buildCatalog(render), [render]);
  const preview = mode === "preview";

  const [values, setValues] = useState<Values>(() => ({ ...render.values }));
  const [related, setRelated] = useState<Record<string, RelatedState>>(() => initRelated(render));
  const [ui, setUi] = useState<Record<string, number | boolean>>({});

  const setRoot = (slug: string, v: unknown) => setValues((p) => ({ ...p, [slug]: v }));
  const setSection = (relId: string, slug: string, v: unknown) =>
    setRelated((p) => ({ ...p, [relId]: { ...p[relId], values: { ...p[relId]?.values, [slug]: v } } }));
  const rowsOf = (relId: string): RowState[] => related[relId]?.rows ?? [];
  const setRows = (relId: string, rows: RowState[]) =>
    setRelated((p) => ({ ...p, [relId]: { ...p[relId], rows } }));
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

  const runButton = async (btn: ButtonElement) => {
    if (btn.action.kind === "submit") {
      await onSubmit?.(buildPayload());
    } else if (btn.action.kind === "run_workflow") {
      if (btn.action.confirm && !window.confirm(btn.action.confirm)) return;
      const inputs: Record<string, unknown> = {};
      for (const [k, expr] of Object.entries(btn.action.inputs)) inputs[k] = evaluate(expr, values);
      await onRunWorkflow?.(btn.action.workflow_id, inputs);
    } else if (btn.action.kind === "link") {
      if (typeof window !== "undefined") {
        if (btn.action.new_tab) window.open(btn.action.href, "_blank");
        else window.location.href = btn.action.href;
      }
    }
  };

  const rootScope: Scope = {
    entityId: catalog.rootEntityId,
    values,
    setValue: setRoot,
    keyPrefix: "root",
  };

  const renderList = (elements: FormElement[], scope: Scope) => (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-12">
      {elements.map((el, i) => (
        <ElementNode key={el.id ?? `${scope.keyPrefix}-${i}`} el={el} scope={scope} />
      ))}
    </div>
  );

  function ElementNode({ el, scope }: { el: FormElement; scope: Scope }) {
    switch (el.type) {
      case "field": {
        const meta = fieldMeta(catalog, scope.entityId, el.slug);
        if (!meta) return null;
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
        return (
          <div className={spanClass(el.width)}>
            <LabelNode el={el} />
          </div>
        );
      case "calculated":
        return (
          <div className={spanClass(el.width)}>
            <CalculatedNode el={el} scope={scope} />
          </div>
        );
      case "button":
        return (
          <div className={spanClass(el.width)}>
            <ButtonNode el={el} />
          </div>
        );
      case "form_ref":
        return (
          <div className="sm:col-span-12 space-y-2 border-t pt-4">
            {el.label ? <h2 className="text-lg font-semibold">{el.label}</h2> : null}
            <EmbeddedForm formId={el.form_id} />
          </div>
        );
      case "columns": {
        const totalSpan = el.columns.reduce((s, c) => s + Math.max(1, c.span), 0) || 1;
        return (
          <div className="sm:col-span-12 grid grid-cols-1 gap-4" style={{ gridTemplateColumns: undefined }}>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-12">
              {el.columns.map((col, ci) => (
                <div
                  key={ci}
                  className="sm:col-auto"
                  style={{ gridColumn: `span ${Math.round((Math.max(1, col.span) / totalSpan) * 12)} / span ${Math.round((Math.max(1, col.span) / totalSpan) * 12)}` }}
                >
                  {renderList(col.elements, scope)}
                </div>
              ))}
            </div>
          </div>
        );
      }
      case "panel":
        return (
          <fieldset className="sm:col-span-12 rounded-lg border p-4">
            {el.title ? <legend className="px-1 text-sm font-semibold">{el.title}</legend> : null}
            {renderList(el.elements, scope)}
          </fieldset>
        );
      case "tab_group":
        return <TabGroupNode el={el} scope={scope} />;
      case "accordion":
        return <AccordionNode el={el} scope={scope} />;
      case "section":
        return <SectionNode el={el} />;
      case "table":
        return <TableNode el={el} />;
      case "block":
        return <BlockNode el={el} />;
      default:
        return null;
    }
  }

  function LabelNode({ el }: { el: Extract<FormElement, { type: "label" }> }) {
    if (el.variant === "divider") return <hr className="my-2 border-t" />;
    if (el.variant === "heading")
      return <h2 className="border-b pb-1 text-lg font-semibold">{el.text}</h2>;
    if (el.variant === "subheading") return <h3 className="text-base font-semibold">{el.text}</h3>;
    return <p className="text-sm text-muted-foreground">{el.text}</p>;
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

  function ButtonNode({ el }: { el: ButtonElement }) {
    const styles: Record<string, string> = {
      primary: "bg-primary text-primary-foreground",
      secondary: "border bg-background",
      danger: "bg-destructive text-destructive-foreground",
      ghost: "hover:bg-muted",
    };
    return (
      <button
        type={el.action.kind === "submit" ? "submit" : "button"}
        disabled={preview || submitting}
        onClick={el.action.kind === "submit" ? undefined : () => void runButton(el)}
        className={`rounded-md px-4 py-2 text-sm font-medium disabled:opacity-60 ${styles[el.style]}`}
      >
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
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
              <div className="w-full max-w-md space-y-4 rounded-lg border bg-card p-6 shadow-lg">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold">{el.label ?? "Details"}</h3>
                  <button type="button" onClick={() => setUi((p) => ({ ...p, [modalKey]: false }))}>
                    <X className="h-5 w-5" />
                  </button>
                </div>
                {renderList(el.elements as FormElement[], scope)}
                <button
                  type="button"
                  onClick={() => setUi((p) => ({ ...p, [modalKey]: false }))}
                  className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
                >
                  Done
                </button>
              </div>
            </div>
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
    return (
      <div className="sm:col-span-12 space-y-2 border-t pt-4">
        <h2 className="text-lg font-semibold">{el.label ?? "Items"}</h2>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left">
                {el.columns.map((col, ci) => (
                  <th key={ci} className="px-2 py-1.5 font-medium">
                    {col.label ?? col.slug}
                  </th>
                ))}
                {!preview ? <th className="w-8" /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="border-b align-top">
                  {el.columns.map((col, ci) => {
                    if (col.kind === "field") {
                      const meta = fieldMeta(catalog, childEntity, col.slug);
                      if (!meta) return <td key={ci} />;
                      return (
                        <td key={ci} className="px-2 py-1.5">
                          <FieldControl
                            meta={meta}
                            label=""
                            required={false}
                            readOnly={col.read_only || preview}
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
                          readOnly={!col.editable || preview}
                          display={col.display}
                          value={row.related?.[col.relationship_id]?.values?.[col.slug]}
                          onChange={(v) => setRowRelated(relId, ri, col.relationship_id, col.slug, v)}
                          name={`tbl-${relId}-${ri}-${col.relationship_id}-${col.slug}`}
                        />
                      </td>
                    );
                  })}
                  {!preview ? (
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
        {!preview ? (
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
