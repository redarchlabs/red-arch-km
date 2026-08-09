/**
 * Flexible form designer — types + clients.
 *
 * A form's layout is a recursive **element tree** (mirrors the backend
 * `schemas/form_elements.py`). Admin CRUD goes through `apiClient` (authenticated);
 * the public token page uses plain `fetch` (the external user has no session).
 * The same render/submit contract (`FormRender` / `FormSubmit`) drives both the
 * public token page and the authenticated internal fill surface, and is walked by
 * the shared `<FormRenderer>`.
 */
import apiClient from "./client";
import type { FilterOp } from "./filterOps";

const PUBLIC_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

// ---- shared presentational vocabulary ----
export type FieldWidth = "full" | "half" | "third" | "quarter";
/** `dropdown`/`radio` pick a picklist's input widget. The rest are DISPLAY-ONLY: they drop
 * the input and typeset the value, for a screen read from across a room where a read-only
 * textarea is still a bordered control wrapped around a sentence nobody may edit. */
export type FieldDisplay = "dropdown" | "radio" | "headline" | "prose" | "quote" | "caption" | "log";

/** The display-only subset — rendered as text, never as an input. */
export const TEXT_DISPLAYS: ReadonlySet<string> = new Set([
  "headline",
  "prose",
  "quote",
  "caption",
  "log",
]);
export type SectionMode = "inline" | "modal";
export type ResultType = "text" | "integer" | "numeric" | "boolean" | "date" | "timestamptz";

/** A JsonLogic expression (object/array) or a literal (string/number/bool/null). */
export type Expression = unknown;

// ---- element tree ----
interface ElementBase {
  id?: string | null;
  /** Optional conditional visibility: a JsonLogic expression over the enclosing
   * scope's values (same evaluator as `calculated`). The element renders only
   * when truthy; `null`/absent is always visible. Mirrors backend `_Element`. */
  visible_when?: Expression | null;
}

export interface FieldElement extends ElementBase {
  type: "field";
  slug: string;
  label?: string | null;
  required?: boolean | null;
  read_only?: boolean;
  /** Views render fields as read-only readouts by default; `true` opts this
   * field back into an input there (edits can feed workflow-button inputs).
   * Ignored on forms. */
  editable?: boolean | null;
  help_text?: string | null;
  placeholder?: string | null;
  width?: FieldWidth | null;
  display?: FieldDisplay | null;
}

export interface LabelElement extends ElementBase {
  type: "label";
  text: string;
  variant: "heading" | "subheading" | "paragraph" | "divider";
  /** Wall-display typesetting; overrides `variant` when set. */
  display?: "headline" | "prose" | "quote" | "caption" | null;
  width?: FieldWidth | null;
}

export interface CalculatedElement extends ElementBase {
  type: "calculated";
  label?: string | null;
  expression: Expression;
  result_type: ResultType;
  target_slug?: string | null;
  help_text?: string | null;
  width?: FieldWidth | null;
}

/** Widget an `input` renders. Presentational; value coercion follows the control. */
export type InputControl = "text" | "textarea" | "number" | "slider" | "toggle" | "select";

export interface InputOption {
  value: string;
  label?: string | null;
}

/**
 * A standalone (unbound) input — its value lives in form state under `key`, NOT tied to
 * an entity field. Reference it from a button's `inputs` or a `calculated` expression as
 * `{ var: "<key>" }`. Enables sliders/toggles/free-text in a standalone view that feed a
 * workflow run without a backing record.
 */
export interface InputElement extends ElementBase {
  type: "input";
  key: string;
  control: InputControl;
  label?: string | null;
  placeholder?: string | null;
  help_text?: string | null;
  default?: string | number | boolean | null;
  required?: boolean;
  width?: FieldWidth | null;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  options?: InputOption[];
}

/** Display-only readout that polls a CORS-reachable endpoint and shows a JSON value. */
export interface LiveValueElement extends ElementBase {
  type: "live_value";
  label?: string | null;
  url: string;
  json_pointer?: string | null;
  poll_ms?: number;
  units?: string | null;
  /** Display translation keyed by the stringified value (`{"true": "Thinking…"}`);
   * unlisted values pass through unchanged. */
  value_map?: Record<string, string> | null;
  width?: FieldWidth | null;
}

/** A display-only picture. `url` may carry `{token}` placeholders filled from the
 * enclosing scope's values (`{id}` = the bound record id, `{<field_slug>}` = a field
 * value), so the artwork can follow record state — e.g. `/sim/ship-{condition}.svg`.
 * Relative or http(s) URLs only. */
export interface ImageElement extends ElementBase {
  type: "image";
  url: string;
  alt?: string | null;
  caption?: string | null;
  /** px cap on rendered height; the image always scales down to the column width. */
  max_height?: number | null;
  width?: FieldWidth | null;
}

/** A display-only progress bar. `value` is a JsonLogic expression over the form's
 * values (or a literal) yielding a number; the bar fills `value / max`, clamped to
 * `[0, max]`. `show_percent` draws the computed percentage on the bar. */
/** A QR code for a URL — how a screen hands a link to a phone or tablet without
 * anyone typing an address. A relative `url` resolves against `host` if set, else
 * against the address the page was opened at. */
export interface QrCodeElement extends ElementBase {
  type: "qr_code";
  url: string;
  label?: string | null;
  caption?: string | null;
  display?: "button" | "inline";
  host?: string | null;
  size?: number;
  width?: FieldWidth | null;
}

export interface ProgressElement extends ElementBase {
  type: "progress";
  label?: string | null;
  value?: Expression;
  max?: number;
  show_percent?: boolean;
  width?: FieldWidth | null;
}

/** A live "time left" clock, counting down to a deadline that lives on the record.
 * The deadline is either absolute (`until_field`) or a start plus a duration
 * (`from_field` + `seconds`/`seconds_field`). Display-only: it reads the record and
 * ticks in the browser, and nothing about it decides when time is actually up —
 * that is the workflow's business. See the Python schema for the clock-skew note. */
export interface CountdownElement extends ElementBase {
  type: "countdown";
  label?: string | null;
  until_field?: string | null;
  from_field?: string | null;
  seconds?: number | null;
  seconds_field?: string | null;
  done_text?: string | null;
  show_bar?: boolean;
  width?: FieldWidth | null;
}

/** One slide in a deck: optional title, Markdown `body`, optional image, optional
 * video. A direct `video_url` (mp4/webm) with `require_video` (default true) blocks
 * advancing past the slide until the learner watches it through. */
export interface Slide {
  title?: string | null;
  body?: string;
  image_url?: string | null;
  video_url?: string | null;
  require_video?: boolean;
  notes?: string | null;
}

/** An in-app slide deck — module content as a navigable presentation (prev/next +
 * progress) instead of a wall of text. `slug` binds to a JSON entity field holding
 * the slide array (the common case); `slides` provides them inline. Display-only. */
export interface SlidesElement extends ElementBase {
  type: "slides";
  label?: string | null;
  slug?: string | null;
  slides?: Slide[];
  width?: FieldWidth | null;
}

/** Embeds a saved report on a dashboard — renders its chart / KPI tile / table per
 * the report's own visualization spec. Not entity-bound, so valid in standalone views. */
export interface ReportElement extends ElementBase {
  type: "report";
  report_id: string;
  title?: string | null;
  height?: number | null;
  poll_ms?: number | null;
  width?: FieldWidth | null;
}

/** A KPI tile backed by a saved report (same data path as `report`). */
export interface StatElement extends ElementBase {
  type: "stat";
  report_id: string;
  label?: string | null;
  icon?: string | null;
  trend?: "up_is_good" | "down_is_good" | "neutral";
  poll_ms?: number | null;
  width?: FieldWidth | null;
}

export type ColumnFormat = "auto" | "text" | "number" | "date" | "datetime" | "badge" | "code";
export type BadgeTone = "neutral" | "success" | "warning" | "destructive" | "info";

/** Presentation for one `record_list` column (mirrors backend `RecordListColumn`). */
export interface RecordListColumn {
  slug: string;
  label?: string | null;
  align?: "left" | "right" | "center" | null;
  format?: ColumnFormat;
  badge_map?: Record<string, BadgeTone>;
}

/** One server-side filter on a `record_list` (mirrors backend `RecordListFilter`).
 * A `value` of `"@me"` on a relation field scopes the board to the caller's own
 * records (resolved server-side, like `record_id=me`). */
export interface RecordListFilterConfig {
  field: string;
  op?: FilterOp;
  value?: string | number | boolean | null;
}

/** Read-only "status board": lists existing records of an entity (newest-first or by
 * sort_by), optionally re-polling to stay live, with an optional per-row workflow button. */
export interface RecordListElement extends ElementBase {
  type: "record_list";
  entity: string;
  label?: string | null;
  fields?: string[];
  /** Optional per-column presentation; additive to `fields`. */
  columns?: RecordListColumn[];
  /** Server-side row filters, ANDed. `value: "@me"` on a relation → caller's own rows. */
  filters?: RecordListFilterConfig[];
  sort_by?: string | null;
  sort_dir?: "asc" | "desc";
  limit?: number;
  poll_ms?: number | null;
  empty_text?: string | null;
  row_workflow_id?: string | null;
  row_action_label?: string | null;
  /** Inputs for `row_workflow_id`, evaluated per row over the row's values + the
   * enclosing view's values (`{ var: "id" }` = row id) — e.g. a course board's Enroll
   * passing `course_id` + the caller's `learner_email`. */
  row_workflow_inputs?: Record<string, Expression>;
  /** Optional per-row hyperlink with `{token}` placeholders filled from the row
   * (`{id}` = row id, `{<field>}` = a field value) — e.g. `/views/{player_view_slug}/view`. */
  row_link_template?: string | null;
  row_link_label?: string | null;
  /** Auxiliary one-shot queries whose plucked values row expressions can test
   * against as `lookups.<key>` — e.g. the caller's enrollments plucked to course
   * ids, so the per-row Enroll hides for already-enrolled rows. */
  row_lookups?: RecordListLookupConfig[];
  /** Per-row visibility for the workflow button: JsonLogic over
   * `{...viewScope, ...row, lookups}`; absent = always visible. */
  row_workflow_visible_when?: Expression;
  /** Muted text shown in place of a hidden row button — e.g. "Enrolled ✓". */
  row_workflow_hidden_text?: string | null;
  /** Per-row visibility for the row link, same scope as the button rule. */
  row_link_visible_when?: Expression;
  width?: FieldWidth | null;
}

/** One auxiliary `record_list` query (mirrors backend `RecordListLookup`): fetch
 * `entity` narrowed by `filters` (incl. `@me`), pluck one field/relation from each
 * record, expose the array to row expressions as `lookups.<key>`. */
export interface RecordListLookupConfig {
  key: string;
  entity: string;
  filters?: RecordListFilterConfig[];
  pluck: string;
  limit?: number;
}

/** Live, per-turn controls for how the answer workflow retrieves/generates. When
 * `show` is set, the chat renders a compact toggle row and forwards the chosen values
 * as workflow `inputs` (synthesize / use_knowledge_graph / answer_model / max_words),
 * so a viewer can trade quality for speed without rebuilding the workflow. The other
 * fields are the *initial* state of each control. */
export interface ChatAnswerControls {
  show?: boolean;
  /** Fast mode = retrieval-only (`synthesize:false`): one LLM call, no graph hop. */
  fast_mode?: boolean;
  /** Include the knowledge-graph hop (only affects the non-fast/synthesis path). */
  knowledge_graph?: boolean;
  /** Concise = cap the spoken reply to `concise_words`; otherwise `verbose_words`. */
  concise?: boolean;
  /** Speak = have the robot say the answer aloud (forwarded as `inputs.speak`). */
  speak?: boolean;
  /** Selectable answer models (first entry is the default). e.g. ["gpt-5-nano","gpt-5-mini"]. */
  models?: string[];
  concise_words?: number;
  verbose_words?: number;
}

/** Perceived-latency filler: while the answer workflow runs, the chat shows (and,
 * if `speak_connection` is set, speaks) short randomized "one moment…" lines so a
 * slow answer still feels responsive. Fillers are ephemeral — nothing is persisted,
 * and they clear the instant the real reply lands. */
export interface ChatFiller {
  show?: boolean;
  /** Wait this long into a turn before the first filler (ms). */
  delay_ms?: number;
  /** Gap between successive fillers while the robot is still working (ms). */
  interval_ms?: number;
  /** Max filler lines to emit per turn before falling silent (default 2). */
  max_lines?: number;
  /** Override the phrase pool. `{q}` is replaced with the person's question. */
  phrases?: string[];
  /** Saved connection slug to verbalize the filler through (e.g. "robot"). */
  speak_connection?: string | null;
  /** Connection path that makes the robot talk (default "/say"). */
  speak_path?: string;
  /** Request-body field carrying the phrase text (default "text"). */
  speak_field?: string;
}

/** Voice input for the chat: the browser microphone drives speech-to-text
 * (Web Speech API) so a person can talk to the robot instead of typing. When
 * `show` is set, the composer renders a mic control with two modes the viewer
 * can switch between at runtime:
 *  - "push_to_talk" — hold the mic button to speak, release to send.
 *  - "always_on"    — the mic stays open and each spoken utterance auto-sends.
 * `mode` is only the initial default. Recognized speech flows through the exact
 * same send path as typed text, so the robot answers (and speaks) identically. */
export interface ChatVoice {
  show?: boolean;
  /** Initial input mode the viewer sees (they can flip it live). Default "push_to_talk". */
  mode?: "push_to_talk" | "always_on";
  /** BCP-47 recognition language (default "en-US"). */
  lang?: string;
  /** Always-on: pause listening while the robot answers so it doesn't hear
   * itself / the tail of the prior turn, then resume (turn-taking). Default true. */
  pause_while_thinking?: boolean;
}

/** A conversation panel over two entities (a conversation session + its messages).
 * Lists the active conversation's messages as bubbles (polling) and, on send, creates
 * a `person` message then runs `answer_workflow_id` so the robot answers + speaks. */
export interface ChatElement extends ElementBase {
  type: "chat";
  title?: string | null;
  conversation_entity?: string;
  message_entity?: string;
  conversation_relationship?: string;
  role_field?: string;
  text_field?: string;
  channel_field?: string;
  answer_workflow_id?: string | null;
  answer_controls?: ChatAnswerControls | null;
  filler?: ChatFiller | null;
  voice?: ChatVoice | null;
  poll_ms?: number;
  placeholder?: string;
  /** Panel height; `fill` sizes to the viewport for a chat that IS the screen. */
  height?: "sm" | "md" | "lg" | "fill";
  width?: FieldWidth | null;
}

export type SubmitAction = { kind: "submit" };
export type RunWorkflowAction = {
  kind: "run_workflow";
  workflow_id: string;
  inputs: Record<string, Expression>;
  confirm?: string | null;
  success_message?: string | null;
};
export type LinkAction = { kind: "link"; href: string; new_tab?: boolean };
/** Copy the link rather than follow it. A relative `href` is resolved absolute
 * against `host` (else the page's origin) first — a pasted `/views/…` is useless
 * anywhere but this browser. Same `{token}` fill as `LinkAction`. */
export type CopyLinkAction = {
  kind: "copy_link";
  href: string;
  host?: string | null;
  success_message?: string | null;
};
/** POST/GET to a saved Connection server-side; `body` templated from form values. */
export type CallConnectionAction = {
  kind: "call_connection";
  connection: string;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path?: string;
  body: Record<string, Expression>;
  confirm?: string | null;
  success_message?: string | null;
};
export type ButtonAction =
  | SubmitAction
  | RunWorkflowAction
  | LinkAction
  | CopyLinkAction
  | CallConnectionAction;

export interface ButtonElement extends ElementBase {
  type: "button";
  label: string;
  action: ButtonAction;
  style: "primary" | "secondary" | "danger" | "ghost";
  /** Touch target size. `large`/`xl` are for a view presented on a tablet or a
   * wall display, where a finger is the pointer. */
  size?: "default" | "large" | "xl";
  width?: FieldWidth | null;
}

/** A hands-on interactive surface — big tap targets, a number pad, ordering,
 * drag-to-connect wires, drag-to-sort bins, or tap-to-paint colouring. The
 * interaction runs entirely in the browser; only the outcome reaches a workflow.
 * See `services/api/src/api/schemas/form_elements.py` for the spec shapes and
 * for which kinds are graded on the client vs. the server. */
export interface PuzzlePadElement extends ElementBase {
  type: "puzzle_pad";
  kind: "choices" | "keypad" | "sequence" | "wires" | "sort" | "color";
  kind_field?: string | null;
  spec?: Record<string, unknown> | null;
  spec_field?: string | null;
  prompt?: string | null;
  prompt_field?: string | null;
  hint?: string | null;
  hint_field?: string | null;
  /** Record field holding the correct value, read only to REVEAL it afterwards.
   * Empty while the question is live; whatever writes the record fills it once
   * answering has closed. See the Python schema for why that ordering matters. */
  answer_field?: string | null;
  /** Keep the pad locked after one submission, until the puzzle itself changes. */
  lock_after_submit?: boolean;
  on_complete?: RunWorkflowAction | null;
  submit_label?: string;
  show_hint?: boolean;
  min_height?: number | null;
  width?: FieldWidth | null;
}

export interface FormRefElement extends ElementBase {
  type: "form_ref";
  form_id: string;
  mode: "fill" | "display";
  label?: string | null;
}

export type AnchorColumn = {
  kind: "field";
  slug: string;
  label?: string | null;
  read_only?: boolean;
  width?: FieldWidth | null;
  display?: FieldDisplay | null;
};
export type RelatedColumn = {
  kind: "related";
  relationship_id: string;
  slug: string;
  label?: string | null;
  editable?: boolean;
  width?: FieldWidth | null;
  display?: FieldDisplay | null;
};
/** A non-data column rendering a per-row hyperlink. `href_template` is a URL with
 * `{token}` placeholders: `{id}` = the row record id, `{<field_slug>}` = an anchor
 * field value on the row (each URL-encoded). e.g. `/documents/{document_key}`. */
export type LinkColumn = {
  kind: "link";
  href_template: string;
  link_label?: string;
  label?: string | null;
  new_tab?: boolean;
  width?: FieldWidth | null;
};
export type TableColumn = AnchorColumn | RelatedColumn | LinkColumn;

export interface TableElement extends ElementBase {
  type: "table";
  anchor_relationship_id: string;
  label?: string | null;
  columns: TableColumn[];
  min_rows?: number;
  max_rows?: number | null;
  read_only?: boolean;
  /** Anchor field slug to order rows by (server-side); omit for insertion order. */
  sort_by?: string | null;
  sort_dir?: "asc" | "desc";
}

/** Leaf elements that may appear inside a section/block. */
export type SectionChild = FieldElement | CalculatedElement | LabelElement;

export interface SectionElement extends ElementBase {
  type: "section";
  relationship_id: string;
  mode: SectionMode;
  label?: string | null;
  elements: SectionChild[];
}

export interface BlockElement extends ElementBase {
  type: "block";
  anchor_relationship_id: string;
  label?: string | null;
  add_label?: string | null;
  min_items?: number;
  max_items?: number | null;
  elements: SectionChild[];
}

export interface Tab {
  label: string;
  elements: FormElement[];
}
export interface TabGroupElement extends ElementBase {
  type: "tab_group";
  tabs: Tab[];
}

export interface PanelElement extends ElementBase {
  type: "panel";
  title?: string | null;
  collapsible?: boolean;
  collapsed?: boolean;
  elements: FormElement[];
}

/** A dashboard tile: a titled, bordered surface grouping its children. Same
 * entity scope as its parent (pure layout, like `panel`). */
export interface CardElement extends ElementBase {
  type: "card";
  title?: string | null;
  subtitle?: string | null;
  accent?: "none" | "primary" | "success" | "warning" | "destructive";
  elements: FormElement[];
}

export interface AccordionPane {
  label: string;
  elements: FormElement[];
}
export interface AccordionElement extends ElementBase {
  type: "accordion";
  panes: AccordionPane[];
}

export interface ColumnDef {
  span: number;
  elements: FormElement[];
}
export interface ColumnsElement extends ElementBase {
  type: "columns";
  columns: ColumnDef[];
}

export type FormElement =
  | FieldElement
  | LabelElement
  | CalculatedElement
  | InputElement
  | LiveValueElement
  | ImageElement
  | QrCodeElement
  | ProgressElement
  | CountdownElement
  | SlidesElement
  | ReportElement
  | StatElement
  | RecordListElement
  | ChatElement
  | ButtonElement
  | PuzzlePadElement
  | FormRefElement
  | TableElement
  | SectionElement
  | BlockElement
  | TabGroupElement
  | PanelElement
  | CardElement
  | AccordionElement
  | ColumnsElement;

export interface FormConfig {
  version: number;
  elements: FormElement[];
  /** Live refresh cadence (ms) for the runtime *view* viewer: it re-fetches the render
   * on this cadence so record-bound elements follow the record as workflows change it.
   * Edited values are preserved across a refresh. Null/absent = fetch once. */
  refresh_ms?: number | null;
  /** Breathing room around the element tree on the chrome-free routes (kiosk / public
   * share), which are otherwise full-bleed. Absent = "none", so the edge-to-edge
   * kiosks built before this option keep their layout. */
  padding?: "none" | "comfortable" | "spacious" | null;
  /** Pin the palette this view renders in instead of following the viewer's
   * theme. Applied to the view's own wrapper, never to `<html>`. */
  theme?: "light" | "dark" | "redarch" | null;
}

/** Org identity for a chrome-free page. Present only on an anonymous share
 * render whose link opted into branding. */
export interface OrgBranding {
  org_name: string;
  accent_color?: string | null;
  has_logo?: boolean;
}

// ---- form entity + CRUD DTOs ----
export interface Form {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  entity_definition_id: string;
  config: FormConfig;
  is_active: boolean;
}

export interface FormCreateInput {
  name: string;
  slug: string;
  entity_definition_id: string;
  description?: string | null;
  config?: FormConfig;
}

export interface FormUpdateInput {
  name?: string;
  description?: string | null;
  config?: FormConfig;
  is_active?: boolean;
}

export interface FormLink {
  id: string;
  form_id: string;
  status: string;
  recipient_email: string | null;
  expires_at: string | null;
  submitted_at: string | null;
}
export interface FormLinkCreated extends FormLink {
  token: string;
  url: string;
  email_sent: boolean;
}
export interface GenerateLinkInput {
  target_record_id: string;
  recipient_email?: string | null;
  expires_in_days?: number | null;
}

// ---- resolved render/submit contract (shared) ----
export interface FieldMeta {
  slug: string;
  label: string;
  field_type: string;
  required: boolean;
  options: string[];
}
export interface EntityCatalogEntry {
  entity_id: string;
  name: string;
  fields: FieldMeta[];
}
export interface RelationshipMeta {
  relationship_id: string;
  related_entity_id: string;
  kind: "to_one" | "to_many";
  name: string;
}
export interface FormRender {
  form_id: string;
  form_name: string;
  description: string | null;
  status: string;
  root_entity_id: string | null;
  /** The record this render is actually bound to, once resolved. For a `record_id=me`
   * view it's the caller's own record id (or null if unresolved) — use it to target a
   * workflow button at the right record instead of re-sending the `me` sentinel. */
  record_id: string | null;
  config: FormConfig;
  catalog: EntityCatalogEntry[];
  relationships: RelationshipMeta[];
  values: Record<string, unknown>;
  related: Record<string, { values?: Record<string, unknown>; rows?: Record<string, unknown>[] }>;
  /** Set only on an anonymous share render whose link opted into branding. */
  branding?: OrgBranding | null;
}
export interface FormSubmit {
  values: Record<string, unknown>;
  related: Record<string, { values?: Record<string, unknown>; rows?: Record<string, unknown>[] }>;
}

const EMPTY_CONFIG: FormConfig = { version: 2, elements: [] };
export function emptyConfig(): FormConfig {
  return { ...EMPTY_CONFIG, elements: [] };
}

// ---- admin (authenticated) ----
export async function listForms(): Promise<Form[]> {
  const res = await apiClient.get<Form[]>("/forms/");
  return res.data;
}
export async function getForm(id: string): Promise<Form> {
  const res = await apiClient.get<Form>(`/forms/${id}`);
  return res.data;
}
export async function createForm(input: FormCreateInput): Promise<Form> {
  const res = await apiClient.post<Form>("/forms/", input);
  return res.data;
}
export async function updateForm(id: string, input: FormUpdateInput): Promise<Form> {
  const res = await apiClient.patch<Form>(`/forms/${id}`, input);
  return res.data;
}
export async function deleteForm(id: string): Promise<void> {
  await apiClient.delete(`/forms/${id}`);
}
export async function listFormLinks(id: string): Promise<FormLink[]> {
  const res = await apiClient.get<FormLink[]>(`/forms/${id}/links`);
  return res.data;
}
export async function generateFormLink(id: string, input: GenerateLinkInput): Promise<FormLinkCreated> {
  const res = await apiClient.post<FormLinkCreated>(`/forms/${id}/links`, input);
  return res.data;
}
export async function revokeFormLink(id: string, linkId: string): Promise<FormLink> {
  const res = await apiClient.post<FormLink>(`/forms/${id}/links/${linkId}/revoke`);
  return res.data;
}

// ---- authenticated internal fill surface ----
export async function getFormRender(id: string, recordId?: string): Promise<FormRender> {
  const res = await apiClient.get<FormRender>(`/forms/${id}/render`, {
    params: recordId ? { record_id: recordId } : undefined,
  });
  return res.data;
}
export async function submitForm(id: string, recordId: string, body: FormSubmit): Promise<void> {
  await apiClient.post(`/forms/${id}/submit`, { record_id: recordId, ...body });
}

// ---- public (unauthenticated, token) ----
async function publicJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `Request failed (${res.status})`);
  }
  return res.json();
}
export async function getPublicForm(token: string): Promise<FormRender> {
  const res = await fetch(`${PUBLIC_BASE}/public/forms/${encodeURIComponent(token)}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
  });
  return publicJson<FormRender>(res);
}
export async function submitPublicForm(token: string, body: FormSubmit): Promise<void> {
  const res = await fetch(`${PUBLIC_BASE}/public/forms/${encodeURIComponent(token)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `Submission failed (${res.status})`);
  }
}
