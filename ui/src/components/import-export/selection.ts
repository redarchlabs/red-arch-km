/**
 * Shared selection model for the import/export selectors. Adapts both the export
 * manifest and an uploaded bundle into a uniform group/item tree the
 * ResourceSelector can render, and derives a default (all-selected) Selection.
 */

import type { Manifest, Selection } from "@/lib/api/migration";

export interface SelectableItem {
  id: string;
  label: string;
  sublabel?: string;
}

export interface SelectableGroup {
  type: string;
  label: string;
  items: SelectableItem[];
}

/** Display order + human labels for each resource type. */
const GROUPS: ReadonlyArray<{ type: string; label: string }> = [
  { type: "entities", label: "Entities" },
  { type: "forms", label: "Forms" },
  { type: "views", label: "Views" },
  { type: "workflows", label: "Workflows" },
  { type: "connections", label: "Connections" },
  { type: "inbound_endpoints", label: "Webhook endpoints" },
  { type: "folders", label: "Folders" },
  { type: "tags", label: "Tags" },
  { type: "records", label: "Records (by entity)" },
  { type: "documents", label: "Documents" },
];

function nameSlug(item: { id: string; name: string; slug?: string }): SelectableItem {
  return { id: item.id, label: item.name, sublabel: item.slug };
}

/** Build selector groups from the export manifest. */
export function manifestToGroups(manifest: Manifest): SelectableGroup[] {
  const byType: Record<string, SelectableItem[]> = {
    entities: manifest.entities.map(nameSlug),
    forms: manifest.forms.map(nameSlug),
    views: manifest.views.map(nameSlug),
    workflows: manifest.workflows.map((w) => ({ id: w.id, label: w.name })),
    connections: manifest.connections.map((c) => ({ id: c.id, label: c.name })),
    inbound_endpoints: manifest.inbound_endpoints.map((e) => ({ id: e.id, label: e.name })),
    folders: manifest.folders.map((f) => ({ id: f.id, label: f.name, sublabel: f.dot_path })),
    tags: manifest.tags.map((t) => ({ id: t.id, label: t.name })),
    records: manifest.records.map((r) => ({
      id: r.entity_slug,
      label: r.name,
      sublabel: `${r.count} record${r.count === 1 ? "" : "s"}`,
    })),
    documents: manifest.documents.map((d) => ({ id: d.id, label: d.title })),
  };
  return toGroups(byType);
}

/** The resource shapes inside an exported bundle (a superset of the manifest). */
interface BundleResources {
  entities?: { id: string; name: string; slug: string }[];
  forms?: { id: string; name: string; slug: string }[];
  views?: { id: string; name: string; slug: string }[];
  workflows?: { id: string; name: string }[];
  connections?: { id: string; name: string }[];
  inbound_endpoints?: { id: string; name: string }[];
  folders?: { id: string; name: string; dot_path?: string }[];
  tags?: { id: string; name: string }[];
  records?: { entity_slug: string; records: unknown[] }[];
  documents?: { id: string; title: string }[];
}

/** Build selector groups from a parsed bundle's `resources`. */
export function bundleToGroups(resources: BundleResources): SelectableGroup[] {
  const byType: Record<string, SelectableItem[]> = {
    entities: (resources.entities ?? []).map(nameSlug),
    forms: (resources.forms ?? []).map(nameSlug),
    views: (resources.views ?? []).map(nameSlug),
    workflows: (resources.workflows ?? []).map((w) => ({ id: w.id, label: w.name })),
    connections: (resources.connections ?? []).map((c) => ({ id: c.id, label: c.name })),
    inbound_endpoints: (resources.inbound_endpoints ?? []).map((e) => ({ id: e.id, label: e.name })),
    folders: (resources.folders ?? []).map((f) => ({ id: f.id, label: f.name, sublabel: f.dot_path })),
    tags: (resources.tags ?? []).map((t) => ({ id: t.id, label: t.name })),
    records: (resources.records ?? []).map((r) => ({
      id: r.entity_slug,
      label: r.entity_slug,
      sublabel: `${r.records.length} record${r.records.length === 1 ? "" : "s"}`,
    })),
    documents: (resources.documents ?? []).map((d) => ({ id: d.id, label: d.title })),
  };
  return toGroups(byType);
}

function toGroups(byType: Record<string, SelectableItem[]>): SelectableGroup[] {
  return GROUPS.filter((g) => (byType[g.type] ?? []).length > 0).map((g) => ({
    type: g.type,
    label: g.label,
    items: byType[g.type] ?? [],
  }));
}

/** Every id in every group selected — the default before the user narrows it. */
export function selectAll(groups: SelectableGroup[]): Selection {
  const selection: Selection = {};
  for (const group of groups) {
    selection[group.type] = group.items.map((i) => i.id);
  }
  return selection;
}

/** Total number of selected items across all groups. */
export function countSelected(selection: Selection): number {
  return Object.values(selection).reduce((sum, ids) => sum + ids.length, 0);
}
