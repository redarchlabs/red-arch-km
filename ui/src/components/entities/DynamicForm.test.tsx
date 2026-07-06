import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EntityField } from "@/lib/api/entities";

import { DynamicForm } from "./DynamicForm";

afterEach(cleanup);

function field(partial: Partial<EntityField> & Pick<EntityField, "slug" | "field_type">): EntityField {
  return {
    id: partial.slug,
    name: partial.slug,
    picklist_options: null,
    is_required: false,
    is_unique: false,
    default_value: null,
    order: 0,
    ...partial,
  };
}

describe("DynamicForm", () => {
  it("coerces number and boolean fields on submit", () => {
    const onSubmit = vi.fn();
    render(
      <DynamicForm
        fields={[
          field({ slug: "count", name: "Count", field_type: "integer" }),
          field({ slug: "active", name: "Active", field_type: "boolean" }),
        ]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByLabelText("Count"), { target: { value: "42" } });
    fireEvent.change(screen.getByLabelText("Active"), { target: { value: "true" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).toHaveBeenCalledWith({ count: 42, active: true });
  });

  it("blocks submit when a required field is empty", () => {
    const onSubmit = vi.fn();
    render(
      <DynamicForm
        fields={[field({ slug: "title", name: "Title", field_type: "text", is_required: true })]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByText(/Title is required/)).not.toBeNull();
  });

  it("rejects a non-integer value for an integer field", () => {
    const onSubmit = vi.fn();
    render(
      <DynamicForm
        fields={[field({ slug: "count", name: "Count", field_type: "integer" })]}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByLabelText("Count"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByText(/whole number/)).not.toBeNull();
  });

  it("renders a relationship picker and submits the chosen target id", () => {
    const onSubmit = vi.fn();
    render(
      <DynamicForm
        fields={[field({ slug: "reason", name: "Reason", field_type: "text" })]}
        relationships={[
          {
            id: "rel1",
            slug: "patient",
            name: "Patient",
            is_required: true,
            targetEntityName: "Patient",
            options: [
              { value: "p-1", label: "Alice" },
              { value: "p-2", label: "Bob" },
            ],
          },
        ]}
        onSubmit={onSubmit}
      />,
    );
    // Required relationship blocks submit until selected.
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByText(/Patient is required/)).not.toBeNull();

    fireEvent.change(screen.getByLabelText(/Patient/), { target: { value: "p-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSubmit).toHaveBeenCalledWith({ reason: null, patient: "p-2" });
  });

  it("shows an empty-state hint when a relationship has no target records", () => {
    render(
      <DynamicForm
        fields={[]}
        relationships={[
          {
            id: "rel1",
            slug: "patient",
            name: "Patient",
            is_required: false,
            targetEntityName: "Patient",
            options: [],
          },
        ]}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.queryByText(/No Patient records yet/)).not.toBeNull();
  });

  it("seeds edit values from initial and empties optional fields to null", () => {
    const onSubmit = vi.fn();
    render(
      <DynamicForm
        fields={[field({ slug: "note", name: "Note", field_type: "text" })]}
        initial={{ note: "hello" }}
        submitLabel="Update"
        onSubmit={onSubmit}
      />,
    );
    expect((screen.getByLabelText("Note") as HTMLInputElement).value).toBe("hello");
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Update" }));

    expect(onSubmit).toHaveBeenCalledWith({ note: null });
  });
});
