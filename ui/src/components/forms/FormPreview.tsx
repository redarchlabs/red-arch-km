"use client";

/**
 * FormPreview — a read-only rendering of an intake form as its recipient would
 * see it. Resolves each configured field slug against the owning entity
 * definition for its label and control type, applying the form's per-field
 * overrides (label / required / help text / placeholder / width / heading). All
 * controls are disabled: this is a preview, not a real submission surface.
 *
 * Rendered client-side from data the caller already has (the form config plus
 * the loaded entity definitions), so it needs no extra API calls. Fields flow
 * through a two-column grid: "full"-width fields span the row, "half"-width
 * fields pair up, and a field's optional heading starts a new full-width group.
 */
import { Badge } from "@/components/ui/badge";
import type { EntityDefinition, EntityField, FieldType } from "@/lib/api/entities";
import type {
  Form,
  FormFieldConfig,
  FormSectionConfig,
  SectionMode,
} from "@/lib/api/forms";

interface FormPreviewProps {
  form: Form;
  entities: EntityDefinition[];
}

const CONTROL_CLASS =
  "mt-1 w-full truncate rounded-md border bg-muted/40 px-2 py-1.5 text-sm text-muted-foreground";

const MODE_LABELS: Record<SectionMode, string> = {
  inline: "single related record",
  modal: "single related record (modal)",
  table: "multiple related records",
};

/** A disabled control that visually matches the field's data type. */
function FieldControl({
  field,
  placeholder,
}: {
  field: EntityField | undefined;
  placeholder?: string | null;
}) {
  const type: FieldType | undefined = field?.field_type;
  const hint = placeholder || "";
  switch (type) {
    case "boolean":
      return <input type="checkbox" disabled className="mt-1 h-4 w-4 align-middle" />;
    case "long_text":
      return <div className={`${CONTROL_CLASS} h-16`}>{hint}&nbsp;</div>;
    case "picklist":
      return (
        <select disabled className={CONTROL_CLASS}>
          <option>{field?.picklist_options?.[0] || hint || "Select…"}</option>
        </select>
      );
    case "date":
    case "timestamptz":
      return <div className={CONTROL_CLASS}>{hint || "mm / dd / yyyy"}</div>;
    case "integer":
    case "bigint":
    case "numeric":
      return <div className={CONTROL_CLASS}>{hint || "0"}</div>;
    default:
      return <div className={CONTROL_CLASS}>{hint}&nbsp;</div>;
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
  return (
    <div>
      <label className="text-xs font-medium">
        {label}
        {required ? <span className="ml-0.5 text-destructive">*</span> : null}
      </label>
      <FieldControl field={field} placeholder={config.placeholder} />
      {config.help_text ? (
        <p className="mt-1 text-xs text-muted-foreground">{config.help_text}</p>
      ) : null}
    </div>
  );
}

/**
 * A related-records section. The designer does not load relationship metadata,
 * so we render the section's configured field labels rather than resolving the
 * target entity's field types — enough to convey shape and layout.
 */
function SectionBlock({ section }: { section: FormSectionConfig }) {
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
                    {f.label || f.slug}
                    {f.required ? <span className="ml-0.5 text-destructive">*</span> : null}
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
            <FieldBody key={f.slug} config={f} field={undefined} />
          ))}
        </div>
      )}
    </div>
  );
}

export function FormPreview({ form, entities }: FormPreviewProps) {
  const entity = entities.find((e) => e.id === form.entity_definition_id);
  const fieldBySlug = new Map((entity?.fields ?? []).map((f) => [f.slug, f]));
  const fields = form.config.fields ?? [];
  const sections = form.config.sections ?? [];

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
        <SectionBlock key={section.relationship_id} section={section} />
      ))}
    </div>
  );
}
