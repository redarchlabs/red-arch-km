import apiClient from "./client";

/** ---- Aggregation query contract (mirrors api/schemas/aggregate.py) ---- */
export type DateBucket = "hour" | "day" | "week" | "month" | "quarter" | "year";
export type AggOp = "count" | "count_distinct" | "sum" | "avg" | "min" | "max";
export type FilterOp = "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in" | "contains" | "isnull";
export type CompareOp = "eq" | "ne" | "gt" | "gte" | "lt" | "lte";

export interface GroupBy {
  field: string;
  bucket?: DateBucket | null;
  alias?: string | null;
}
export interface Metric {
  op: AggOp;
  field?: string | null;
  alias?: string | null;
}
export interface FilterSpec {
  field: string;
  op?: FilterOp;
  value?: unknown;
}
export interface HavingSpec {
  metric: string;
  op?: CompareOp;
  value: number;
}
export interface OrderSpec {
  key: string;
  dir?: "asc" | "desc";
}
export interface AggregateQuery {
  group_by?: GroupBy[];
  metrics?: Metric[];
  filters?: FilterSpec[];
  having?: HavingSpec[];
  order_by?: OrderSpec[];
  limit?: number;
}

/** A resolved aggregation: rows keyed by group + metric names, plus the ordered
 * column names so a chart can map its axes without re-parsing the query. */
export interface AggregateResult {
  group_by: string[];
  metrics: string[];
  rows: Array<Record<string, unknown>>;
  row_count: number;
}

/** ---- Visualization spec (mirrors api/schemas/report.py Visualization) ---- */
export type ChartType =
  | "bar"
  | "stacked_bar"
  | "grouped_bar"
  | "line"
  | "area"
  | "stacked_area"
  | "pie"
  | "donut"
  | "scatter"
  | "table"
  | "metric";
export type NumberFormat = "plain" | "comma" | "currency" | "percent" | "compact" | "bytes";

export interface Visualization {
  type: ChartType;
  x?: string | null;
  series: string[];
  color_by?: string | null;
  stacked?: boolean;
  compare_to?: string | null;
  unit?: string | null;
  number_format?: NumberFormat;
  options?: Record<string, unknown>;
}

/** ---- Report entity ---- */
export interface Report {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  entity_definition_id: string;
  query: AggregateQuery;
  viz: Visualization;
  is_active: boolean;
}

export interface ReportCreate {
  name: string;
  slug: string;
  description?: string | null;
  entity_definition_id: string;
  query: AggregateQuery;
  viz: Visualization;
}

export type ReportUpdate = Partial<
  Pick<Report, "name" | "description" | "query" | "viz" | "is_active">
>;

export async function listReports(): Promise<Report[]> {
  const res = await apiClient.get<Report[]>("/reports/");
  return res.data;
}

export async function getReport(reportId: string): Promise<Report> {
  const res = await apiClient.get<Report>(`/reports/${reportId}`);
  return res.data;
}

export async function createReport(body: ReportCreate): Promise<Report> {
  const res = await apiClient.post<Report>("/reports/", body);
  return res.data;
}

export async function updateReport(reportId: string, body: ReportUpdate): Promise<Report> {
  const res = await apiClient.patch<Report>(`/reports/${reportId}`, body);
  return res.data;
}

export async function deleteReport(reportId: string): Promise<void> {
  await apiClient.delete(`/reports/${reportId}`);
}

/** Run a saved report, optionally with dashboard filter/limit overrides. */
export async function runReport(
  reportId: string,
  overrides?: { extra_filters?: FilterSpec[]; limit?: number },
): Promise<AggregateResult> {
  const res = await apiClient.post<AggregateResult>(`/reports/${reportId}/run`, overrides ?? {});
  return res.data;
}

/** Run an unsaved aggregation — the report builder's live preview. */
export async function runAdhoc(
  entityDefinitionId: string,
  query: AggregateQuery,
): Promise<AggregateResult> {
  const res = await apiClient.post<AggregateResult>("/reports/run", {
    entity_definition_id: entityDefinitionId,
    query,
  });
  return res.data;
}

/** Ad-hoc aggregate directly against an entity by slug (no saved report). */
export async function aggregateEntity(
  slug: string,
  query: AggregateQuery,
): Promise<AggregateResult> {
  const res = await apiClient.post<AggregateResult>(`/entities/${slug}/aggregate`, query);
  return res.data;
}
