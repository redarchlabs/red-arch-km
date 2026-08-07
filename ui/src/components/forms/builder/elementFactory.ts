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

export const LEAF_KINDS: PaletteKind[] = [
  "field",
  "label",
  "image",
  "calculated",
  "progress",
  "countdown",
  "input",
  "live_value",
  "button",
];
export const DATA_KINDS: PaletteKind[] = ["section", "table", "block"];
export const LAYOUT_KINDS: PaletteKind[] = ["tab_group", "panel", "card", "accordion", "columns"];
// Palette for the view builder: no entity-bound leaves, plus embedded forms. `input`,
// `live_value`, `progress` and `record_list` are unbound, so they're valid in standalone views too.
export const VIEW_KINDS: PaletteKind[] = [
  "label",
  "image",
  "qr_code",
  "input",
  "live_value",
  "progress",
  "countdown",
  "slides",
  "stat",
  "report",
  "record_list",
  "chat",
  "button",
  "puzzle_pad",
  "form_ref",
  ...LAYOUT_KINDS,
];

export const KIND_LABELS: Record<PaletteKind, string> = {
  field: "Field",
  label: "Label / text",
  image: "Image / picture",
  qr_code: "QR code (open on a phone / tablet)",
  calculated: "Calculated",
  input: "Input (slider / toggle / text)",
  live_value: "Live value",
  progress: "Progress bar",
  countdown: "Countdown / time left",
  slides: "Slide deck",
  stat: "Stat tile (KPI number)",
  report: "Report / chart",
  record_list: "Record list / status board",
  chat: "Chat",
  button: "Button",
  puzzle_pad: "Puzzle pad (tap / drag / colour)",
  form_ref: "Embedded form",
  section: "Related record (1:1)",
  table: "Table (1:M)",
  block: "Repeating block (1:M)",
  tab_group: "Tabs",
  panel: "Panel",
  card: "Card (dashboard tile)",
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
    case "image":
      return { id, type: "image", url: "", alt: "", caption: null, max_height: 320 };
    case "qr_code":
      return {
        id,
        type: "qr_code",
        url: "",
        label: "Show QR code",
        caption: "Point a phone or tablet camera at this.",
        display: "button",
        host: null,
        size: 320,
      };
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
    case "progress":
      return { id, type: "progress", label: "Progress", value: 0, max: 100, show_percent: true };
    case "countdown":
      return {
        id,
        type: "countdown",
        label: "Time left",
        until_field: null,
        from_field: null,
        seconds: 20,
        seconds_field: null,
        done_text: "Time's up",
        show_bar: true,
      };
    case "slides":
      return {
        id,
        type: "slides",
        label: "Slides",
        slides: [{ title: "Slide 1", body: "Slide content (Markdown)." }],
      };
    case "stat":
      return { id, type: "stat", report_id: "", label: "Metric", trend: "up_is_good", width: "quarter" };
    case "report":
      return { id, type: "report", report_id: "", title: "Report", height: 320 };
    case "record_list":
      return { id, type: "record_list", label: "Records", entity: "", fields: [], filters: [], sort_dir: "desc", limit: 20 };
    case "chat":
      return {
        id,
        type: "chat",
        title: "Chat",
        conversation_entity: "robot_conversation",
        message_entity: "robot_message",
        conversation_relationship: "conversation",
        role_field: "role",
        text_field: "text",
        channel_field: "channel",
        answer_workflow_id: null,
        answer_controls: {
          show: false,
          fast_mode: true,
          knowledge_graph: false,
          concise: true,
          models: ["gpt-5-nano", "gpt-5-mini"],
          concise_words: 20,
          verbose_words: 45,
        },
        voice: {
          show: false,
          mode: "push_to_talk",
          lang: "en-US",
          pause_while_thinking: true,
        },
        poll_ms: 1500,
        placeholder: "Message the robot…",
      };
    case "button":
      return { id, type: "button", label: "Submit", action: { kind: "submit" }, style: "primary", size: "default" };
    case "puzzle_pad":
      // Ships with a playable example rather than an empty shell: a pad with no
      // spec can't render anything, so a blank default would drop a broken
      // element on the canvas and leave the author guessing at the JSON shape.
      return {
        id,
        type: "puzzle_pad",
        kind: "choices",
        prompt: "Which one is it?",
        spec: {
          options: [
            { value: "A", label: "First answer" },
            { value: "B", label: "Second answer" },
            { value: "C", label: "Third answer" },
            { value: "D", label: "Fourth answer" },
          ],
          columns: 2,
        },
        hint: "",
        on_complete: null,
        submit_label: "Transmit",
        show_hint: true,
      };
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
    case "card":
      return { id, type: "card", title: "Card", subtitle: null, accent: "none", elements: [] };
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
