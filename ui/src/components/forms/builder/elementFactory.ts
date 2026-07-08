/**
 * Factories for new form elements, with sensible defaults and a stable client id
 * (used for React keys and granular agent edits). One place so the palette, the
 * builder, and any programmatic insertion all produce well-formed elements.
 */
import type { FormElement } from "@/lib/api/forms";

function genId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `el-${Math.floor(Math.random() * 1e9).toString(36)}`;
}

export type PaletteKind = FormElement["type"];

export const LEAF_KINDS: PaletteKind[] = ["field", "label", "calculated", "input", "live_value", "button"];
export const DATA_KINDS: PaletteKind[] = ["section", "table", "block"];
export const LAYOUT_KINDS: PaletteKind[] = ["tab_group", "panel", "accordion", "columns"];
// Palette for the view builder: no entity-bound leaves, plus embedded forms. `input` and
// `live_value` are unbound, so they're valid in standalone views too.
export const VIEW_KINDS: PaletteKind[] = [
  "label",
  "input",
  "live_value",
  "button",
  "form_ref",
  ...LAYOUT_KINDS,
];

export const KIND_LABELS: Record<PaletteKind, string> = {
  field: "Field",
  label: "Label / text",
  calculated: "Calculated",
  input: "Input (slider / toggle / text)",
  live_value: "Live value",
  button: "Button",
  form_ref: "Embedded form",
  section: "Related record (1:1)",
  table: "Table (1:M)",
  block: "Repeating block (1:M)",
  tab_group: "Tabs",
  panel: "Panel",
  accordion: "Accordion",
  columns: "Columns",
};

export function newElement(kind: PaletteKind): FormElement {
  const id = genId();
  switch (kind) {
    case "field":
      return { id, type: "field", slug: "", width: "full" };
    case "label":
      return { id, type: "label", text: "Text", variant: "paragraph" };
    case "calculated":
      return {
        id,
        type: "calculated",
        label: "Calculated",
        expression: { today: [] },
        result_type: "text",
        target_slug: null,
      };
    case "input":
      return { id, type: "input", key: "", control: "text", label: "Input" };
    case "live_value":
      return { id, type: "live_value", label: "Live value", url: "", poll_ms: 1000 };
    case "button":
      return { id, type: "button", label: "Submit", action: { kind: "submit" }, style: "primary" };
    case "form_ref":
      return { id, type: "form_ref", form_id: "", mode: "display" };
    case "section":
      return { id, type: "section", relationship_id: "", mode: "inline", elements: [] };
    case "table":
      return { id, type: "table", anchor_relationship_id: "", columns: [] };
    case "block":
      return { id, type: "block", anchor_relationship_id: "", elements: [] };
    case "tab_group":
      return { id, type: "tab_group", tabs: [{ label: "Tab 1", elements: [] }] };
    case "panel":
      return { id, type: "panel", title: "Panel", elements: [] };
    case "accordion":
      return { id, type: "accordion", panes: [{ label: "Section 1", elements: [] }] };
    case "columns":
      return {
        id,
        type: "columns",
        columns: [
          { span: 1, elements: [] },
          { span: 1, elements: [] },
        ],
      };
  }
}
