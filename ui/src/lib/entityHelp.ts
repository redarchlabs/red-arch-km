/**
 * Focus-driven help for the entity schema editor. The editor is two add-forms
 * (Fields / Relationships) rather than a select-an-item inspector, so each card
 * pushes its topic when focused and clears it on blur/unmount. Module-level
 * constants for stable references.
 */
import type { HelpTopic } from "@/lib/help";

const topic = (title: string, body: string): HelpTopic => ({ prefix: "", title, body });

export const ENTITY_FIELDS_HELP = topic(
  "Entity fields",
  `
Fields are the **typed columns** of this entity's records. Give a field a name,
pick a **type**, and add it.

### Field types
- **text** — a short single line. **long text** — multi-line prose.
- **integer** / **bigint** — whole numbers (bigint for very large values).
- **numeric** — exact decimals (money, quantities).
- **boolean** — true / false.
- **date** — a calendar day. **timestamptz** — a date *and* time (with zone).
- **uuid** — a unique identifier value.
- **json** — arbitrary structured data.
- **picklist** — a fixed set of options you supply (comma-separated).

### Options
- **Unique** — the database rejects duplicate values for this field.

> Deleting a field drops its column **and all data in it** — it can't be undone.
`,
);

export const ENTITY_RELATIONSHIPS_HELP = topic(
  "Entity relationships",
  `
Relationships **link this entity to another**, which is what powers a form's
tables and related-record sections.

### Cardinality
- **one-to-one** — each record links to at most one on the other side.
- **one-to-many** — this record has many of the target (e.g. Order → Line items).
- **many-to-one** — many of these point at one target (e.g. Line item → Order).
- **many-to-many** — both sides can have many.

### On delete
What happens to related rows when a record is deleted:
- **SET NULL** — clear the link, keep the related record.
- **CASCADE** — delete the related records too.
- **RESTRICT** — block the delete while links exist.

Pick a **target entity** and a name (the name is how the relationship appears in
form pickers).
`,
);
