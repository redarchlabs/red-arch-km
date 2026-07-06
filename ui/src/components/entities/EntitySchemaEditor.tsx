"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  addEntityField,
  createRelationship,
  type Cardinality,
  type EntityDefinition,
  type EntityRelationship,
  type FieldType,
  type OnDelete,
} from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";

const FIELD_TYPES: FieldType[] = [
  "text",
  "long_text",
  "integer",
  "bigint",
  "numeric",
  "boolean",
  "date",
  "timestamptz",
  "uuid",
  "json",
  "picklist",
];

const CARDINALITIES: Cardinality[] = ["one_to_one", "one_to_many", "many_to_one", "many_to_many"];
const ON_DELETE: OnDelete[] = ["SET NULL", "CASCADE", "RESTRICT"];

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

function slugify(name: string): string {
  const s = name.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
  return /^[a-z]/.test(s) ? s : `f_${s}`;
}

interface EntitySchemaEditorProps {
  entity: EntityDefinition;
  relationships: EntityRelationship[];
  allEntities: EntityDefinition[];
  onChange: () => void;
}

export function EntitySchemaEditor({
  entity,
  relationships,
  allEntities,
  onChange,
}: EntitySchemaEditorProps) {
  const [error, setError] = useState<string | null>(null);

  // Add-field form state.
  const [fName, setFName] = useState("");
  const [fType, setFType] = useState<FieldType>("text");
  const [fOptions, setFOptions] = useState("");
  const [fUnique, setFUnique] = useState(false);

  // Add-relationship form state.
  const [rName, setRName] = useState("");
  const [rCard, setRCard] = useState<Cardinality>("many_to_one");
  const [rTarget, setRTarget] = useState("");
  const [rOnDelete, setROnDelete] = useState<OnDelete>("SET NULL");

  const targetName = (id: string) => allEntities.find((e) => e.id === id)?.name ?? "—";

  const handleAddField = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = fName.trim();
    if (!name) return;
    setError(null);
    try {
      await addEntityField(entity.id, {
        name,
        slug: slugify(name),
        field_type: fType,
        picklist_options:
          fType === "picklist"
            ? fOptions.split(",").map((o) => o.trim()).filter(Boolean)
            : undefined,
        is_unique: fUnique,
      });
      setFName("");
      setFOptions("");
      setFUnique(false);
      onChange();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to add field"));
    }
  };

  const handleAddRelationship = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = rName.trim();
    if (!name || !rTarget) {
      setError("Relationship needs a name and a target entity");
      return;
    }
    setError(null);
    try {
      await createRelationship(entity.id, {
        name,
        slug: slugify(name),
        cardinality: rCard,
        target_definition_id: rTarget,
        on_delete: rOnDelete,
      });
      setRName("");
      setRTarget("");
      onChange();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to add relationship"));
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <h2 className="text-lg font-semibold">Fields</h2>
          {entity.fields.length > 0 ? (
            <ul className="divide-y rounded-md border">
              {entity.fields.map((field) => (
                <li key={field.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                  <span className="flex-1">{field.name}</span>
                  <Badge variant="outline">{field.field_type}</Badge>
                  {field.is_required ? <Badge variant="outline">required</Badge> : null}
                  {field.is_unique ? <Badge variant="outline">unique</Badge> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No fields yet.</p>
          )}

          <form onSubmit={handleAddField} className="space-y-2 border-t pt-4">
            <div className="flex gap-2">
              <Input
                value={fName}
                onChange={(e) => setFName(e.target.value)}
                placeholder="Field name…"
              />
              <select
                value={fType}
                onChange={(e) => setFType(e.target.value as FieldType)}
                className={selectClass}
              >
                {FIELD_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
            {fType === "picklist" ? (
              <Input
                value={fOptions}
                onChange={(e) => setFOptions(e.target.value)}
                placeholder="Options, comma-separated (e.g. open, closed)"
              />
            ) : null}
            <label className="flex items-center gap-2 text-sm text-muted-foreground">
              <input type="checkbox" checked={fUnique} onChange={(e) => setFUnique(e.target.checked)} />
              Unique
            </label>
            <Button type="submit" disabled={!fName.trim()}>
              <Plus className="h-4 w-4" />
              Add field
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <h2 className="text-lg font-semibold">Relationships</h2>
          {relationships.length > 0 ? (
            <ul className="divide-y rounded-md border">
              {relationships.map((rel) => (
                <li key={rel.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                  <span className="flex-1">{rel.name}</span>
                  <Badge variant="outline">{rel.cardinality}</Badge>
                  <span className="text-xs text-muted-foreground">
                    → {targetName(rel.target_definition_id)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No relationships yet.</p>
          )}

          <form onSubmit={handleAddRelationship} className="space-y-2 border-t pt-4">
            <Input
              value={rName}
              onChange={(e) => setRName(e.target.value)}
              placeholder="Relationship name (e.g. Orders)…"
            />
            <div className="flex flex-wrap gap-2">
              <select
                value={rCard}
                onChange={(e) => setRCard(e.target.value as Cardinality)}
                className={selectClass}
              >
                {CARDINALITIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <select
                value={rTarget}
                onChange={(e) => setRTarget(e.target.value)}
                className={selectClass}
              >
                <option value="">Target entity…</option>
                {allEntities.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))}
              </select>
              <select
                value={rOnDelete}
                onChange={(e) => setROnDelete(e.target.value as OnDelete)}
                className={selectClass}
              >
                {ON_DELETE.map((d) => (
                  <option key={d} value={d}>
                    on delete: {d}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" disabled={!rName.trim() || !rTarget}>
              <Plus className="h-4 w-4" />
              Add relationship
            </Button>
          </form>
        </CardContent>
      </Card>

      {error ? <p className="text-sm text-destructive lg:col-span-2">{error}</p> : null}
    </div>
  );
}
