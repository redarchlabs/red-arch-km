import { describe, expect, it } from "vitest";

import { formatBytes, formatDate } from "@/lib/format";

describe("formatBytes", () => {
  it("formats raw bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("formats kilobytes", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(1536)).toBe("1.5 KB");
  });

  it("formats megabytes", () => {
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("formats gigabytes", () => {
    expect(formatBytes(1024 * 1024 * 1024)).toBe("1.0 GB");
    expect(formatBytes(1.5 * 1024 * 1024 * 1024)).toBe("1.5 GB");
  });
});

describe("formatDate", () => {
  it("returns a string containing the year when given a Date", () => {
    const d = new Date(2024, 0, 15, 10, 30);
    const result = formatDate(d);
    expect(typeof result).toBe("string");
    expect(result).toMatch(/2024/);
  });

  it("returns a string containing the year when given an ISO string", () => {
    const result = formatDate("2025-06-01T12:00:00Z");
    expect(typeof result).toBe("string");
    expect(result).toMatch(/2025/);
  });

  it("accepts both Date and string without throwing", () => {
    expect(() => formatDate(new Date())).not.toThrow();
    expect(() => formatDate(new Date().toISOString())).not.toThrow();
  });
});
