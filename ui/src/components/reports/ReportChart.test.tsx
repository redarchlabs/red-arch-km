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

  it("caps a repeating decimal at 2 fraction digits by default (plain)", () => {
    // The bug: an average like 630/11 used to render as 57.27272727272727.
    expect(formatValue(630 / 11, "plain", "%")).toBe("57.27%");
    // Integers stay exact — no trailing zeros, no thousands grouping.
    expect(formatValue(1500, "plain")).toBe("1500");
  });

  it("honors a caller-supplied precision across formats", () => {
    expect(formatValue(630 / 11, "plain", "%", 1)).toBe("57.3%");
    expect(formatValue(630 / 11, "plain", "%", 0)).toBe("57%");
    expect(formatValue(2 / 3, "percent", null, 1)).toBe("66.7%");
    // Out-of-range precision is clamped to 0–6.
    expect(formatValue(1 / 3, "plain", null, 9)).toBe((1 / 3).toFixed(6));
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
