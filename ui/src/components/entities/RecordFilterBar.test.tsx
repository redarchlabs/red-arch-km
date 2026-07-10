import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityField } from "@/lib/api/entities";
import type { RecordFilter } from "@/lib/api/entityRecords";

import { RecordFilterBar } from "./RecordFilterBar";

afterEach(cleanup);

function field(slug: string, field_type: EntityField["field_type"], picklist: string[] | null = null): EntityField {
  return { id: slug, name: slug, slug, field_type, picklist_options: picklist } as EntityField;
}

const FIELDS: EntityField[] = [
  field("amount", "numeric"),
  field("stage", "picklist", ["won", "lost"]),
  field("name", "text"),
];

describe("RecordFilterBar", () => {
  it("adds a first filter row on 'Add filter'", () => {
    const onChange = vi.fn();
    const { getByText } = render(<RecordFilterBar fields={FIELDS} filters={[]} onChange={onChange} />);
    fireEvent.click(getByText("Add filter"));
    expect(onChange).toHaveBeenCalledWith([{ field: "amount", op: "eq", value: "" }]);
  });

  it("offers range operators for a numeric field and not 'contains'", () => {
    const filters: RecordFilter[] = [{ field: "amount", op: "eq", value: "" }];
    const { getByLabelText } = render(<RecordFilterBar fields={FIELDS} filters={filters} onChange={vi.fn()} />);
    const opSelect = getByLabelText("Filter operator") as HTMLSelectElement;
    const ops = Array.from(opSelect.options).map((o) => o.value);
    expect(ops).toEqual(["eq", "ne", "gt", "gte", "lt", "lte", "isnull"]);
    expect(ops).not.toContain("contains");
  });

  it("offers 'contains' for a text field", () => {
    const filters: RecordFilter[] = [{ field: "name", op: "contains", value: "" }];
    const { getByLabelText } = render(<RecordFilterBar fields={FIELDS} filters={filters} onChange={vi.fn()} />);
    const ops = Array.from((getByLabelText("Filter operator") as HTMLSelectElement).options).map((o) => o.value);
    expect(ops).toContain("contains");
  });

  it("removes a row immutably", () => {
    const onChange = vi.fn();
    const filters: RecordFilter[] = [
      { field: "amount", op: "gte", value: "100" },
      { field: "stage", op: "eq", value: "won" },
    ];
    const { getAllByLabelText } = render(<RecordFilterBar fields={FIELDS} filters={filters} onChange={onChange} />);
    fireEvent.click(getAllByLabelText("Remove filter")[0]);
    expect(onChange).toHaveBeenCalledWith([{ field: "stage", op: "eq", value: "won" }]);
  });
});
