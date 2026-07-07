/**
 * Intake forms — admin CRUD + link minting (authenticated, via apiClient) and
 * the public form endpoints (unauthenticated, via plain fetch: the external
 * user has no Clerk session, only a token).
 */
import apiClient from "./client";

const PUBLIC_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export type SectionMode = "inline" | "modal" | "table";

// Column width in the form's responsive grid: "full" spans the row, "half"
// shares a row with the adjacent half-width field (two columns).
export type FieldWidth = "full" | "half";

// How a picklist field is rendered — purely presentational (the submitted value
// is one of the field's options either way). Ignored for non-picklist fields.
export type FieldDisplay = "dropdown" | "radio";

export interface FormFieldConfig {
  slug: string;
  label?: string | null;
  required?: boolean | null;
  help_text?: string | null;
  placeholder?: string | null;
  width?: FieldWidth | null;
  heading?: string | null; // group heading rendered above this field
  display?: FieldDisplay | null; // picklist render style (dropdown/radio)
}

export interface FormSectionConfig {
  relationship_id: string;
  mode: SectionMode;
  label?: string | null;
  fields: FormFieldConfig[];
}

export interface FormConfig {
  fields: FormFieldConfig[];
  sections: FormSectionConfig[];
}

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

// ---- admin ----
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

// ---- public (unauthenticated) ----
export interface PublicFormField {
  slug: string;
  label: string;
  field_type: string;
  required: boolean;
  help_text: string | null;
  options: string[];
  placeholder?: string | null;
  width?: FieldWidth | null;
  heading?: string | null;
  display?: FieldDisplay | null;
}

export interface PublicFormSection {
  key: string; // relationship_id — the submission key under PublicSubmit.sections
  label: string;
  mode: SectionMode;
  entity_name: string;
  fields: PublicFormField[];
  rows: Record<string, unknown>[];
  values: Record<string, unknown>;
}

export interface PublicForm {
  form_name: string;
  description: string | null;
  fields: PublicFormField[];
  values: Record<string, unknown>;
  sections: PublicFormSection[];
  status: string;
}

export interface PublicSubmit {
  values: Record<string, unknown>;
  sections?: Record<string, unknown>;
}

async function publicJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export async function getPublicForm(token: string): Promise<PublicForm> {
  const res = await fetch(`${PUBLIC_BASE}/public/forms/${encodeURIComponent(token)}`, {
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
  });
  return publicJson<PublicForm>(res);
}

export async function submitPublicForm(token: string, body: PublicSubmit): Promise<void> {
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
