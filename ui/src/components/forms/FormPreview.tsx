"use client";

/**
 * FormPreview — a read-only rendering of an intake form as its recipient would
 * see it. Resolves each configured field slug against the owning entity
 * definition for its label and control type, then renders the *same* bound
 * control the public intake page uses (dropdown/radio picklist, date picker,
 * datetime, number, checkbox, textarea, text) — only disabled, since this is a
 * preview and not a submission surface. The rendered control is derived from the
 * entity field type; the form config only tunes presentation (label / required /
 * width / placeholder / help / heading, plus picklist dropdown-vs-radio).
 *
 * Rendered client-side from data the caller already has (the form config plus
 * the loaded entity definitions). Root fields always resolve their control type.
 * Section fields resolve theirs too when the caller passes `relationships`
 * (the forms editor does); without it (e.g. the workflow designer panel, which
 * doesn't load relationship metadata) sections fall back to generic controls.
 */
import { Badge } from "@/components/ui/badge";
import type {
  EntityDefinition,
  EntityField,
  EntityRelationship,
  FieldType,
} from "@/lib/api/entities";
import type {
  Form,
  FormFieldConfig,
  FormSectionConfig,
  SectionMode,
} from "@/lib/api/forms";

interface FormPreviewProps {
  form: Form;
  entities: EntityDefinition[];
  /** Root's outgoing (1:1) + incoming (1:M) relationships. When supplied,
   *  section fields resolve their real control types; otherwise they render as
   *  generic text controls. */
  relationships?: EntityRelationship[];
}

const CONTROL_CLASS =
  "mt-1 w-full rounded-md border bg-muted/40 px-2 py-1.5 text-sm text-muted-foreground disabled:cursor-default";

const MODE_LABELS: Record<SectionMode, string> = {
  inline: "single related record",
  modal: "single related record (modal)",
  table: "multiple related records",
};

/** A disabled control that matches the bound control the recipient will get. */
function FieldControl({
  field,
  config,
}: {
  field: EntityField | undefined;
  config: FormFieldConfig;
}) {
  const type: FieldType | undefined = field?.field_type;
  const hint = config.placeholder || undefined;
  switch (type) {
    case "boolean":
      return <input type="checkbox" disabled className="mt-1 h-4 w-4 align-middle" />;
    case "long_text":
      return <textarea disabled rows={3} placeholder={hint} className={CONTROL_CLASS} />;
    case "picklist": {
      const options = field?.picklist_options ?? [];
      if (config.display === "radio") {
        return (
          <div className="mt-1 space-y-1">
            {(options.length ? options : ["Option one", "Option two"]).map((o) => (
              <label key={o} className="flex items-center gap-2 text-sm text-muted-foreground">
                <input type="radio" disabled className="h-3.5 w-3.5" />
                {o}
              </label>
            ))}
          </div>
        );
      }
      return (
        <select disabled className={CONTROL_CLASS}>
          <option>{options[0] || hint || "Select…"}</option>
        </select>
      );
    }
    case "date":
      return <input type="date" disabled className={CONTROL_CLASS} />;
    case "timestamptz":
      return <input type="datetime-local" disabled className={CONTROL_CLASS} />;
    case "integer":
    case "bigint":
    case "numeric":
      return <input type="number" disabled placeholder={hint || "0"} className={CONTROL_CLASS} />;
    default:
      return <input type="text" disabled placeholder={hint} className={CONTROL_CLASS} />;
  }
}

/** One labelled field: resolved label, required marker, control, help text. */
function FieldBody({
  config,
  field,
}: {
  config: FormFieldConfig;
  field: EntityField | undefined;
}) {
  const label = config.label || field?.name || config.slug;
  const required = config.required ?? field?.is_required ?? false;
  const labelText = (
    <>
      {label}
      {required ? <span className="ml-0.5 text-destructive">*</span> : null}
    </>
  );

  // Booleans read best as a label + checkbox on one line (matches the public page).
  if (field?.field_type === "boolean") {
    return (
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">{labelText}</span>
        <FieldControl field={field} config={config} />
      </div>
    );
  }
  return (
    <div>
      <label className="text-xs font-medium">{labelText}</label>
      <FieldControl field={field} config={config} />
      {config.help_text ? (
        <p className="mt-1 text-xs text-muted-foreground">{config.help_text}</p>
      ) : null}
    </div>
  );
}

/**
 * A related-records section. When `fieldBySlug` resolves (caller passed
 * relationships) each field renders its real bound control; otherwise fields
 * fall back to generic controls but still honour their configured labels.
 */
function SectionBlock({
  section,
  fieldBySlug,
}: {
  section: FormSectionConfig;
  fieldBySlug: Map<string, EntityField>;
}) {
  const title = section.label || "Related records";
  return (
    <div className="rounded-md border p-3">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-medium">{title}</h3>
        <Badge variant="secondary" className="text-[10px]">
          {MODE_LABELS[section.mode]}
        </Badge>
      </div>
      {section.fields.length === 0 ? (
        <p className="text-xs text-muted-foreground">No fields configured.</p>
      ) : section.mode === "table" ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                {section.fields.map((f) => (
                  <th key={f.slug} className="border-b px-2 py-1 font-medium">
                    {f.label || fieldBySlug.get(f.slug)?.name || f.slug}
                    {(f.required ?? fieldBySlug.get(f.slug)?.is_required) ? (
                      <span className="ml-0.5 text-destructive">*</span>
                    ) : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                {section.fields.map((f) => (
                  <td key={f.slug} className="border-b px-2 py-2 text-muted-foreground">
                    &nbsp;
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div className="space-y-3">
          {section.fields.map((f) => (
            <FieldBody key={f.slug} config={f} field={fieldBySlug.get(f.slug)} />
          ))}
        </div>
      )}
    </div>
  );
}

/** The related entity a section draws its fields from: for a 1:M table the FK
 *  lives on the child (rel.source); for a 1:1 the related entity is the target. */
function sectionEntityId(rel: EntityRelationship, mode: SectionMode): string {
  return mode === "table" ? rel.source_definition_id : rel.target_definition_id;
}

export function FormPreview({ form, entities, relationships }: FormPreviewProps) {
  const entityById = new Map(entities.map((e) => [e.id, e]));
  const relById = new Map((relationships ?? []).map((r) => [r.id, r]));
  const entity = entityById.get(form.entity_definition_id);
  const fieldBySlug = new Map((entity?.fields ?? []).map((f) => [f.slug, f]));
  const fields = form.config.fields ?? [];
  const sections = form.config.sections ?? [];

  const sectionFieldMap = (section: FormSectionConfig): Map<string, EntityField> => {
    const rel = relById.get(section.relationship_id);
    if (!rel) return new Map();
    const related = entityById.get(sectionEntityId(rel, section.mode));
    return new Map((related?.fields ?? []).map((f) => [f.slug, f]));
  };

  if (fields.length === 0 && sections.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">This form has no fields yet.</p>
    );
  }

  return (
    <div className="space-y-4">
      {fields.length > 0 ? (
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          {fields.map((config) => {
            const span = config.width === "half" ? "col-span-1" : "col-span-2";
            return (
              <div key={config.slug} className="contents">
                {config.heading ? (
                  <h3 className="col-span-2 mt-1 border-b pb-1 text-sm font-semibold">
                    {config.heading}
                  </h3>
                ) : null}
                <div className={span}>
                  <FieldBody config={config} field={fieldBySlug.get(config.slug)} />
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {sections.map((section) => (
        <SectionBlock
          key={section.relationship_id}
          section={section}
          fieldBySlug={sectionFieldMap(section)}
        />
      ))}
    </div>
  );
}
