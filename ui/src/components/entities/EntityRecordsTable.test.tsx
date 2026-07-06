import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EntityDefinition, EntityField } from "@/lib/api/entities";
import type { RecordListResult } from "@/lib/api/entityRecords";

const listRecords = vi.fn();
const createRecord = vi.fn();
const updateRecord = vi.fn();
const deleteRecord = vi.fn();
const listEntities = vi.fn();
const listRelationships = vi.fn();

vi.mock("@/lib/api/entityRecords", () => ({
  listRecords: (...a: unknown[]) => listRecords(...a),
  createRecord: (...a: unknown[]) => createRecord(...a),
  updateRecord: (...a: unknown[]) => updateRecord(...a),
  deleteRecord: (...a: unknown[]) => deleteRecord(...a),
}));

vi.mock("@/lib/api/entities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/entities")>();
  return {
    ...actual,
    listEntities: (...a: unknown[]) => listEntities(...a),
    listRelationships: (...a: unknown[]) => listRelationships(...a),
  };
});

import { EntityRecordsTable } from "./EntityRecordsTable";

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
  name: "Widgets",
  slug: "widgets",
  description: null,
  is_active: true,
  fields: [field({ slug: "name", name: "Name", field_type: "text" })],
};

function page(items: Array<Record<string, unknown>>): RecordListResult {
  return { items: items as RecordListResult["items"], next_cursor: null, limit: 50 };
}

beforeEach(() => {
  vi.useFakeTimers();
  [listRecords, createRecord, updateRecord, deleteRecord, listEntities, listRelationships].forEach((m) =>
    m.mockReset(),
  );
  listEntities.mockResolvedValue([]);
  listRelationships.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("EntityRecordsTable search race (HIGH regression)", () => {
  it("ignores a slow earlier search response that resolves after a newer one", async () => {
    const deferreds: Array<(v: RecordListResult) => void> = [];
    listRecords.mockImplementation(() => new Promise<RecordListResult>((resolve) => deferreds.push(resolve)));

    await act(async () => {
      render(<EntityRecordsTable entity={entity} />);
    });
    expect(deferreds).toHaveLength(1); // mount load (empty search) — still pending

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/Search Widgets/), { target: { value: "ab" } });
      await vi.advanceTimersByTimeAsync(300); // debounce → second load
    });
    expect(deferreds).toHaveLength(2);

    // Resolve the newer request first, then the stale one.
    await act(async () => {
      deferreds[1](page([{ id: "2", name: "Banana" }]));
    });
    await act(async () => {
      deferreds[0](page([{ id: "1", name: "Apple" }]));
    });

    expect(screen.queryAllByText("Banana").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("Apple")).toHaveLength(0); // stale response discarded
  });
});

describe("EntityRecordsTable delete confirmation", () => {
  it("confirms via a dialog (not window.confirm) before deleting", async () => {
    listRecords.mockResolvedValue(page([{ id: "1", name: "Apple" }]));
    deleteRecord.mockResolvedValue(undefined);

    await act(async () => {
      render(<EntityRecordsTable entity={entity} />);
    });
    expect(screen.queryAllByText("Apple").length).toBeGreaterThan(0);

    // Trash button opens a confirmation dialog rather than deleting immediately.
    fireEvent.click(screen.getAllByLabelText("Delete record")[0]);
    expect(screen.queryByText("Delete record?")).not.toBeNull();
    expect(deleteRecord).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    });
    expect(deleteRecord).toHaveBeenCalledWith("widgets", "1");
  });
});

describe("EntityRecordsTable cell rendering", () => {
  it("formats boolean, null and object cell values", async () => {
    const typed: EntityDefinition = {
      ...entity,
      fields: [
        field({ slug: "active", name: "Active", field_type: "boolean" }),
        field({ slug: "notes", name: "Notes", field_type: "text" }),
      ],
    };
    listRecords.mockResolvedValue(page([{ id: "1", active: true, notes: null }]));

    await act(async () => {
      render(<EntityRecordsTable entity={typed} />);
    });
    // boolean → "Yes"; null → "—" (rendered in both desktop + mobile views).
    expect(screen.queryAllByText("Yes").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("—").length).toBeGreaterThan(0);
  });
});
