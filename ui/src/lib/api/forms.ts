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

const PUBLIC_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

// ---- shared presentational vocabulary ----
export type FieldWidth = "full" | "half" | "third" | "quarter";
export type FieldDisplay = "dropdown" | "radio";
export type SectionMode = "inline" | "modal";
export type ResultType = "text" | "integer" | "numeric" | "boolean" | "date" | "timestamptz";

/** A JsonLogic expression (object/array) or a literal (string/number/bool/null). */
export type Expression = unknown;

// ---- element tree ----
interface ElementBase {
  id?: string | null;
}

export interface FieldElement extends ElementBase {
  type: "field";
  slug: string;
  label?: string | null;
  required?: boolean | null;
  read_only?: boolean;
  help_text?: string | null;
  placeholder?: string | null;
  width?: FieldWidth | null;
  display?: FieldDisplay | null;
}

export interface LabelElement extends ElementBase {
  type: "label";
  text: string;
  variant: "heading" | "subheading" | "paragraph" | "divider";
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

export type SubmitAction = { kind: "submit" };
export type RunWorkflowAction = {
  kind: "run_workflow";
  workflow_id: string;
  inputs: Record<string, Expression>;
  confirm?: string | null;
  success_message?: string | null;
};
export type LinkAction = { kind: "link"; href: string; new_tab?: boolean };
export type ButtonAction = SubmitAction | RunWorkflowAction | LinkAction;

export interface ButtonElement extends ElementBase {
  type: "button";
  label: string;
  action: ButtonAction;
  style: "primary" | "secondary" | "danger" | "ghost";
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
export type TableColumn = AnchorColumn | RelatedColumn;

export interface TableElement extends ElementBase {
  type: "table";
  anchor_relationship_id: string;
  label?: string | null;
  columns: TableColumn[];
  min_rows?: number;
  max_rows?: number | null;
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
  | ButtonElement
  | FormRefElement
  | TableElement
  | SectionElement
  | BlockElement
  | TabGroupElement
  | PanelElement
  | AccordionElement
  | ColumnsElement;

export interface FormConfig {
  version: number;
  elements: FormElement[];
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
  config: FormConfig;
  catalog: EntityCatalogEntry[];
  relationships: RelationshipMeta[];
  values: Record<string, unknown>;
  related: Record<string, { values?: Record<string, unknown>; rows?: Record<string, unknown>[] }>;
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
