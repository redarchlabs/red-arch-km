/**
 * Lookup helpers over a `FormRender` payload: the renderer walks the authoring
 * tree and resolves each leaf's entity-field metadata (type, options, label) and
 * relationship targets from these maps, switching entity context as it descends
 * into a section/table/block.
 */
import type { FieldMeta, FormRender, RelationshipMeta } from "@/lib/api/forms";

export interface Catalog {
  rootEntityId: string;
  fieldsByEntity: Map<string, Map<string, FieldMeta>>;
  relsById: Map<string, RelationshipMeta>;
}

export function buildCatalog(render: FormRender): Catalog {
  const fieldsByEntity = new Map<string, Map<string, FieldMeta>>();
  for (const entry of render.catalog) {
    const byslug = new Map<string, FieldMeta>();
    for (const f of entry.fields) byslug.set(f.slug, f);
    fieldsByEntity.set(entry.entity_id, byslug);
  }
  const relsById = new Map<string, RelationshipMeta>();
  for (const r of render.relationships) relsById.set(r.relationship_id, r);
  return { rootEntityId: render.root_entity_id ?? "", fieldsByEntity, relsById };
}

export function fieldMeta(catalog: Catalog, entityId: string, slug: string): FieldMeta | undefined {
  return catalog.fieldsByEntity.get(entityId)?.get(slug);
}

/** The entity a section/table/block's children bind to (its relationship target). */
export function relatedEntityId(catalog: Catalog, relationshipId: string): string | undefined {
  return catalog.relsById.get(relationshipId)?.related_entity_id;
}
