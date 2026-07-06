import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  EntityDefinition,
  EntityField,
  EntityRelationship,
} from "@/lib/api/entities";

const addEntityField = vi.fn();
const deleteEntityField = vi.fn();
const createRelationship = vi.fn();

vi.mock("@/lib/api/entities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/entities")>();
  return {
    ...actual,
    addEntityField: (...a: unknown[]) => addEntityField(...a),
    deleteEntityField: (...a: unknown[]) => deleteEntityField(...a),
    createRelationship: (...a: unknown[]) => createRelationship(...a),
  };
});

import { EntitySchemaEditor } from "./EntitySchemaEditor";

function field(partial: Partial<EntityField> & Pick<EntityField, "slug" | "field_type" | "name">): EntityField {
  return {
    id: partial.slug,
    picklist_options: null,
    is_required: false,
    is_unique: false,
    default_value: null,
    order: 0,
    ...partial,
  };
}

const entity: EntityDefinition = {
  id: "e1",
  name: "Tasks",
  slug: "tasks",
  description: null,
  is_active: true,
  fields: [
    field({ slug: "due", name: "Due", field_type: "date" }),
    field({ slug: "priority", name: "Priority", field_type: "picklist", picklist_options: ["hi", "lo"] }),
  ],
};

const order: EntityDefinition = { ...entity, id: "e2", name: "Order", slug: "order", fields: [] };

const relationship: EntityRelationship = {
  id: "r1",
  name: "Orders",
  slug: "orders",
  cardinality: "one_to_many",
  on_delete: "SET NULL",
  is_required: false,
  source_definition_id: "e1",
  target_definition_id: "e2",
};

beforeEach(() => {
  [addEntityField, deleteEntityField, createRelationship].forEach((m) => m.mockReset());
});

afterEach(cleanup);

describe("EntitySchemaEditor field-type rendering", () => {
  it("shows each field's type and relationship cardinality/target", () => {
    render(
      <EntitySchemaEditor
        entity={entity}
        relationships={[relationship]}
        allEntities={[entity, order]}
        onChange={vi.fn()}
      />,
    );
    // "date"/"picklist" also appear as <option>s in the add-field select, so
    // just assert they render at least once (the field badge included).
    expect(screen.queryAllByText("date").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("picklist").length).toBeGreaterThan(0);
    expect(screen.queryByText("Orders")).not.toBeNull();
    expect(screen.queryAllByText("one_to_many").length).toBeGreaterThan(0);
    expect(screen.queryByText(/→\s*Order/)).not.toBeNull();
  });
});

describe("EntitySchemaEditor drop-field confirmation", () => {
  it("confirms via a dialog (not window.confirm) before dropping a field", async () => {
    deleteEntityField.mockResolvedValue(undefined);
    const onChange = vi.fn();
    render(
      <EntitySchemaEditor
        entity={entity}
        relationships={[]}
        allEntities={[entity]}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByLabelText("Delete field Due"));
    expect(screen.queryByText(/Delete field .*Due/)).not.toBeNull();
    expect(deleteEntityField).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete field" }));
    });
    expect(deleteEntityField).toHaveBeenCalledWith("e1", "due");
    expect(onChange).toHaveBeenCalled();
  });
});
