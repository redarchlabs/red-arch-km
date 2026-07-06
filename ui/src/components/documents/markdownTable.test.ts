import { describe, expect, it } from "vitest";

import {
  addColumn,
  addRow,
  deleteColumn,
  deleteRow,
  findTableAt,
  serializeTable,
  setAlign,
} from "./markdownTable";

const DOC = [
  "Some intro text",
  "",
  "| Name | Age |",
  "| --- | --- |",
  "| Alice | 30 |",
  "| Bob | 25 |",
  "",
  "Trailing paragraph.",
].join("\n");

const at = (needle: string, offset = 1) => DOC.indexOf(needle) + offset;

describe("findTableAt", () => {
  it("returns null when the cursor is outside any table", () => {
    expect(findTableAt(DOC, at("intro"))).toBeNull();
    expect(findTableAt(DOC, at("Trailing"))).toBeNull();
  });

  it("parses header, alignments and body rows", () => {
    const t = findTableAt(DOC, at("Alice"));
    expect(t).not.toBeNull();
    expect(t!.header).toEqual(["Name", "Age"]);
    expect(t!.aligns).toEqual(["none", "none"]);
    expect(t!.rows).toEqual([
      ["Alice", "30"],
      ["Bob", "25"],
    ]);
  });

  it("locates the cursor's row and column", () => {
    expect(findTableAt(DOC, at("Age"))).toMatchObject({ cursorRow: -1, cursorCol: 1 });
    expect(findTableAt(DOC, at("Alice"))).toMatchObject({ cursorRow: 0, cursorCol: 0 });
    expect(findTableAt(DOC, at("25"))).toMatchObject({ cursorRow: 1, cursorCol: 1 });
  });
});

describe("column operations", () => {
  it("adds a blank column after the cursor column", () => {
    const t = addColumn(findTableAt(DOC, at("Name"))!, 0);
    expect(t.header).toEqual(["Name", "", "Age"]);
    expect(t.aligns).toEqual(["none", "none", "none"]);
    expect(t.rows).toEqual([
      ["Alice", "", "30"],
      ["Bob", "", "25"],
    ]);
  });

  it("deletes a column but never the last one", () => {
    const t = deleteColumn(findTableAt(DOC, at("Age"))!, 1);
    expect(t.header).toEqual(["Name"]);
    expect(t.rows).toEqual([["Alice"], ["Bob"]]);

    const single = deleteColumn(t, 0);
    expect(single.header).toEqual(["Name"]); // refused: one column must remain
  });
});

describe("row operations", () => {
  it("adds a blank row after the cursor row", () => {
    const t = addRow(findTableAt(DOC, at("Alice"))!, 0);
    expect(t.rows).toEqual([
      ["Alice", "30"],
      ["", ""],
      ["Bob", "25"],
    ]);
  });

  it("adds a row at the top when the cursor is in the header", () => {
    const t = addRow(findTableAt(DOC, at("Name"))!, -1);
    expect(t.rows[0]).toEqual(["", ""]);
  });

  it("deletes the cursor's body row", () => {
    const t = deleteRow(findTableAt(DOC, at("Alice"))!, 0);
    expect(t.rows).toEqual([["Bob", "25"]]);
  });
});

describe("serializeTable", () => {
  it("round-trips a parsed table to normalized Markdown", () => {
    const t = findTableAt(DOC, at("Alice"))!;
    expect(serializeTable(t)).toBe(
      ["| Name | Age |", "| --- | --- |", "| Alice | 30 |", "| Bob | 25 |"].join("\n"),
    );
  });

  it("encodes alignment in the delimiter row", () => {
    const t = setAlign(findTableAt(DOC, at("Age"))!, 1, "center");
    expect(serializeTable(t)).toContain("| --- | :---: |");
  });

  it("escapes pipes inside cell content", () => {
    const t = findTableAt(DOC, at("Alice"))!;
    const withPipe = { ...t, rows: [["A|B", "30"], ["Bob", "25"]] };
    expect(serializeTable(withPipe)).toContain("| A\\|B | 30 |");
  });
});
