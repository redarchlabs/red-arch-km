"use client";

import { Plus, Trash2 } from "lucide-react";

import { FormFieldsEditor } from "@/components/forms/FormFieldsEditor";
import { Input } from "@/components/ui/input";
import type { EntityDefinition, EntityRelationship } from "@/lib/api/entities";
import type { FormFieldConfig, FormSectionConfig, SectionMode } from "@/lib/api/forms";

interface Props {
  allEntities: EntityDefinition[];
  outgoing: EntityRelationship[]; // root's own to-one rels → 1:1 sections
  incoming: EntityRelationship[]; // rels targeting root → 1:M table sections
  sections: FormSectionConfig[];
  onChange: (sections: FormSectionConfig[]) => void;
}

interface Candidate {
  rel: EntityRelationship;
  relatedEntityId: string;
  isTable: boolean; // incoming (1:M) → table; outgoing → 1:1
}

export function FormSectionsEditor({ allEntities, outgoing, incoming, sections, onChange }: Props) {
  const entityById = new Map(allEntities.map((e) => [e.id, e]));

  const candidates: Candidate[] = [
    ...outgoing.map((rel) => ({ rel, relatedEntityId: rel.target_definition_id, isTable: false })),
    ...incoming.map((rel) => ({ rel, relatedEntityId: rel.source_definition_id, isTable: true })),
  ];

  const sectionFor = (relId: string) => sections.find((s) => s.relationship_id === relId);

  const addSection = (c: Candidate) => {
    const entity = entityById.get(c.relatedEntityId);
    const fields = (entity?.fields ?? []).map((f) => ({ slug: f.slug }));
    onChange([
      ...sections,
      { relationship_id: c.rel.id, mode: c.isTable ? "table" : "inline", fields },
    ]);
  };

  const removeSection = (relId: string) =>
    onChange(sections.filter((s) => s.relationship_id !== relId));

  const patchSection = (relId: string, patch: Partial<FormSectionConfig>) =>
    onChange(sections.map((s) => (s.relationship_id === relId ? { ...s, ...patch } : s)));

  const setSectionFields = (relId: string, fields: FormFieldConfig[]) =>
    patchSection(relId, { fields });

  if (candidates.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No relationships on this entity yet. Add relationships in the entity schema to collect linked or
        child records here.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Add related entities to the form: a linked record (1:1, shown inline or in a popup) or a table of
        child records (1:M).
      </p>
      {candidates.map((c) => {
        const entity = entityById.get(c.relatedEntityId);
        const section = sectionFor(c.rel.id);
        return (
          <div key={c.rel.id} className="rounded-md border p-3">
            <div className="flex items-center gap-2">
              <span className="flex-1 text-sm font-medium">
                {c.rel.name} <span className="text-xs text-muted-foreground">→ {entity?.name ?? "—"}</span>
              </span>
              <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                {c.isTable ? "1:M table" : "1:1"}
              </span>
              {section ? (
                <button
                  type="button"
                  onClick={() => removeSection(c.rel.id)}
                  className="text-muted-foreground hover:text-destructive"
                  aria-label="Remove section"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => addSection(c)}
                  className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted"
                >
                  <Plus className="h-3.5 w-3.5" /> Add
                </button>
              )}
            </div>

            {section ? (
              <div className="mt-3 space-y-3 border-t pt-3">
                <label className="block space-y-1 text-xs text-muted-foreground">
                  Section header
                  <Input
                    value={section.label ?? ""}
                    onChange={(e) =>
                      patchSection(c.rel.id, { label: e.target.value || null })
                    }
                    placeholder={entity?.name ?? "Related records"}
                    className="h-8 text-sm"
                  />
                </label>
                {!c.isTable ? (
                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-muted-foreground">Show as</span>
                    {(["inline", "modal"] as SectionMode[]).map((m) => (
                      <label key={m} className="flex items-center gap-1">
                        <input
                          type="radio"
                          checked={section.mode === m}
                          onChange={() => patchSection(c.rel.id, { mode: m })}
                        />
                        {m === "inline" ? "on the form" : "popup"}
                      </label>
                    ))}
                  </div>
                ) : null}
                <FormFieldsEditor
                  entityFields={entity?.fields ?? []}
                  fields={section.fields}
                  onChange={(fields) => setSectionFields(c.rel.id, fields)}
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
