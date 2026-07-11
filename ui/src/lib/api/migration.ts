/**
 * Org portability API: inspect what's exportable (manifest), export a selected
 * subset to a JSON bundle, and import a bundle (optionally a subset) back into
 * the current org. See services/api/src/api/routers/migration.py.
 */

import apiClient from "./client";

/** How the importer resolves a name/slug collision in the target org. */
export type CollisionStrategy = "skip" | "overwrite" | "rename";

/** A selection maps a resource type to the ids (record entity-slugs for records)
 * to include. A type omitted from the object means "all of that type". */
export type Selection = Record<string, string[]>;

/** Lightweight index of every selectable object in the org (no bodies). */
export interface Manifest {
  tags: { id: string; name: string }[];
  entities: { id: string; name: string; slug: string }[];
  connections: { id: string; name: string }[];
  folders: { id: string; name: string; dot_path: string }[];
  workflows: { id: string; name: string }[];
  inbound_endpoints: { id: string; name: string }[];
  forms: { id: string; name: string; slug: string }[];
  reports: { id: string; name: string; slug: string }[];
  views: { id: string; name: string; slug: string }[];
  records: { entity_slug: string; name: string; count: number }[];
  documents: { id: string; title: string }[];
}

/** Per-resource-kind tally returned by an import run. */
export interface ResourceOutcome {
  created: number;
  overwritten: number;
  renamed: number;
  skipped: number;
  failed: number;
}

/** A credential the import minted fresh (shown once) so callers can be reconfigured. */
export interface GeneratedSecret {
  kind: string;
  name: string;
  token: string;
  url: string;
  signing_secret: string;
  signature_header: string;
}

export interface ImportSummary {
  strategy: CollisionStrategy;
  dry_run: boolean;
  resources: Record<string, ResourceOutcome>;
  warnings: string[];
  errors: string[];
  generated_secrets: GeneratedSecret[];
}

/** Fetch the org's exportable-object index for the selection UI. */
export async function fetchManifest(): Promise<Manifest> {
  return (await apiClient.get<Manifest>("/migration/manifest")).data;
}

export interface ExportOptions {
  selection: Selection;
}

/** Download a (possibly narrowed) org export bundle as a Blob. */
export async function exportOrg(options: ExportOptions): Promise<{ blob: Blob; filename: string }> {
  const response = await apiClient.post(
    "/migration/export",
    { selection: options.selection },
    { responseType: "blob" },
  );
  const disposition = String(response.headers["content-disposition"] ?? "");
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match?.[1] ?? "km2-export.json";
  return { blob: response.data as Blob, filename };
}

export interface ImportOptions {
  file: File;
  strategy: CollisionStrategy;
  dryRun: boolean;
  selection: Selection;
}

export async function importOrg(options: ImportOptions): Promise<ImportSummary> {
  const form = new FormData();
  form.append("file", options.file);
  form.append("selection", JSON.stringify(options.selection));
  // Passing undefined suppresses the client's JSON default so the browser sets
  // the multipart Content-Type with its boundary (see documents.ts).
  const response = await apiClient.post<ImportSummary>("/migration/import", form, {
    params: { strategy: options.strategy, dry_run: options.dryRun },
    headers: { "Content-Type": undefined },
    // A large bundle (records + documents) can take a while to rebuild.
    timeout: 300000,
  });
  return response.data;
}
