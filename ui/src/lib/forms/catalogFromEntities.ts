/**
 * Build a `FormRender` payload client-side from a form's element tree + the
 * loaded entity definitions/relationships, so the builder can live-preview an
 * unsaved layout through the same `<FormRenderer>` the real surfaces use. This
 * mirrors the backend's catalog + relationship resolution (`form_service` +
 * `form_layout.flatten`) but with empty prefill values.
 */
import type { EntityDefinition, EntityRelationship } from "@/lib/api/entities";
import type {
  EntityCatalogEntry,
  FormConfig,
  FormElement,
  FormRender,
  RelationshipMeta,
} from "@/lib/api/forms";

function catalogEntry(def: EntityDefinition): EntityCatalogEntry {
  return {
    entity_id: def.id,
    name: def.name,
    fields: def.fields.map((f) => ({
      slug: f.slug,
      label: f.name,
      field_type: f.field_type,
      required: f.is_required,
      options: f.picklist_options ?? [],
    })),
  };
}

/** Walk the tree and emit relationship metadata by usage (section→to_one target,
 * table/block anchor→to_many source, related column→to_one target). */
function relationshipMeta(
  config: FormConfig,
  relById: Map<string, EntityRelationship>,
): RelationshipMeta[] {
  const out: RelationshipMeta[] = [];
  const seen = new Set<string>();
  const add = (relId: string, entityId: string, kind: "to_one" | "to_many") => {
    if (seen.has(relId)) return;
    const rel = relById.get(relId);
    if (!rel) return;
    seen.add(relId);
    out.push({ relationship_id: relId, related_entity_id: entityId, kind, name: rel.name });
  };
  const visit = (el: FormElement) => {
    switch (el.type) {
      case "section": {
        const rel = relById.get(el.relationship_id);
        if (rel) add(el.relationship_id, rel.target_definition_id, "to_one");
        break;
      }
      case "block": {
        const rel = relById.get(el.anchor_relationship_id);
        if (rel) add(el.anchor_relationship_id, rel.source_definition_id, "to_many");
        break;
      }
      case "table": {
        const rel = relById.get(el.anchor_relationship_id);
        if (rel) add(el.anchor_relationship_id, rel.source_definition_id, "to_many");
        for (const col of el.columns) {
          if (col.kind === "related") {
            const cr = relById.get(col.relationship_id);
            if (cr) add(col.relationship_id, cr.target_definition_id, "to_one");
          }
        }
        break;
      }
      case "tab_group":
        el.tabs.forEach((t) => t.elements.forEach(visit));
        break;
      case "accordion":
        el.panes.forEach((p) => p.elements.forEach(visit));
        break;
      case "columns":
        el.columns.forEach((c) => c.elements.forEach(visit));
        break;
      case "panel":
        el.elements.forEach(visit);
        break;
      default:
        break;
    }
  };
  config.elements.forEach(visit);
  return out;
}

export function buildRenderFromConfig(
  formName: string,
  rootEntityId: string,
  config: FormConfig,
  entities: EntityDefinition[],
  relationships: EntityRelationship[],
): FormRender {
  const relById = new Map(relationships.map((r) => [r.id, r]));
  return {
    form_id: "preview",
    form_name: formName,
    description: null,
    status: "editable",
    root_entity_id: rootEntityId,
    record_id: null,
    config,
    catalog: entities.map(catalogEntry),
    relationships: relationshipMeta(config, relById),
    values: {},
    related: {},
  };
}
