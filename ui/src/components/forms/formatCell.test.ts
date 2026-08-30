import { describe, expect, it } from "vitest";

import { formatCell } from "./FormRenderer";

describe("formatCell", () => {
  it("caps the decimals on a numeric column, which arrives as a string", () => {
    // What Postgres hands back for a `numeric` written from float arithmetic.
    expect(formatCell("11836.97117999999940707311907317489385560485839843750")).toBe("11,836.97");
    expect(formatCell("127.27926000000001")).toBe("127.28");
    expect(formatCell("100.00")).toBe("100");
  });

  it("leaves integer-looking strings completely alone", () => {
    // These are as likely to be identifiers or codes as quantities; separating or
    // trimming them would corrupt a reference the reader needs verbatim.
    expect(formatCell("0012")).toBe("0012");
    expect(formatCell("12345678901234567890")).toBe("12345678901234567890");
    expect(formatCell("TB-10400")).toBe("TB-10400");
  });

  it("still handles real numbers, booleans and blanks", () => {
    expect(formatCell(1234)).toBe("1,234");
    expect(formatCell(1.23456)).toBe("1.23");
    expect(formatCell(true)).toBe("Yes");
    expect(formatCell(null)).toBe("—");
  });
});
