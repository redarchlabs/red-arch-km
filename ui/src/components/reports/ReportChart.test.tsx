import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AggregateResult } from "@/lib/api/reports";

import { ReportChart, formatValue } from "./ReportChart";

afterEach(cleanup);

describe("formatValue", () => {
  it("returns empty for null/blank", () => {
    expect(formatValue(null)).toBe("");
    expect(formatValue("")).toBe("");
  });

  it("passes through non-numeric strings", () => {
    expect(formatValue("won")).toBe("won");
  });

  it("formats comma / currency / percent / compact / bytes", () => {
    expect(formatValue(1234, "comma")).toBe((1234).toLocaleString());
    expect(formatValue(0.5, "percent")).toBe("50%");
    expect(formatValue(1024, "bytes")).toBe("1.0 KB");
    expect(formatValue(0, "bytes")).toBe("0 B");
    expect(formatValue(5, "plain", "kg")).toBe("5kg");
  });
});

const RESULT: AggregateResult = {
  group_by: ["stage"],
  metrics: ["total"],
  rows: [
    { stage: "won", total: 600 },
    { stage: "lost", total: -50 }, // negative → exercises the signed domain
    { stage: "open", total: 100 },
  ],
  row_count: 3,
};

describe("ReportChart", () => {
  it("renders an svg for bar/line/pie", () => {
    for (const type of ["bar", "line", "pie"] as const) {
      const { container } = render(<ReportChart result={RESULT} viz={{ type, x: "stage", series: ["total"] }} />);
      expect(container.querySelector("svg")).not.toBeNull();
      cleanup();
    }
  });

  it("renders a table for type=table", () => {
    const { container } = render(<ReportChart result={RESULT} viz={{ type: "table", series: ["total"] }} />);
    expect(container.querySelector("table")).not.toBeNull();
  });

  it("renders the summed value for type=metric", () => {
    const { getByText } = render(<ReportChart result={RESULT} viz={{ type: "metric", series: ["total"] }} />);
    // 600 + (-50) + 100 = 650
    expect(getByText("650")).toBeTruthy();
  });

  it("shows 'No data' for an empty result", () => {
    const empty: AggregateResult = { group_by: [], metrics: ["total"], rows: [], row_count: 0 };
    const { getByText } = render(<ReportChart result={empty} viz={{ type: "bar", series: ["total"] }} />);
    expect(getByText("No data")).toBeTruthy();
  });
});
