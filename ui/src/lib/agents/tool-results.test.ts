import { describe, expect, it } from "vitest";

import {
  batchStatus,
  extractSources,
  hostnameOf,
  resultAnswer,
  resultError,
  safeExternalHref,
} from "@/lib/agents/tool-results";

describe("safeExternalHref", () => {
  it("accepts http and https URLs", () => {
    expect(safeExternalHref("https://example.com/a?b=1")).toBe("https://example.com/a?b=1");
    expect(safeExternalHref("http://example.com")).toBe("http://example.com/");
  });

  it("trims surrounding whitespace", () => {
    expect(safeExternalHref("  https://example.com  ")).toBe("https://example.com/");
  });

  it("rejects dangerous and non-http(s) schemes", () => {
    expect(safeExternalHref("javascript:alert(1)")).toBeNull();
    expect(safeExternalHref("data:text/html,<script>")).toBeNull();
    expect(safeExternalHref("file:///etc/passwd")).toBeNull();
    expect(safeExternalHref("ftp://example.com")).toBeNull();
  });

  it("rejects malformed, empty, and non-string values", () => {
    expect(safeExternalHref("not a url")).toBeNull();
    expect(safeExternalHref("")).toBeNull();
    expect(safeExternalHref("   ")).toBeNull();
    expect(safeExternalHref(undefined)).toBeNull();
    expect(safeExternalHref(42)).toBeNull();
    expect(safeExternalHref(null)).toBeNull();
  });
});

describe("hostnameOf", () => {
  it("returns the host without a leading www.", () => {
    expect(hostnameOf("https://www.example.com/path")).toBe("example.com");
    expect(hostnameOf("https://news.example.com")).toBe("news.example.com");
  });

  it("returns null for unparseable input", () => {
    expect(hostnameOf("nope")).toBeNull();
  });
});

describe("extractSources", () => {
  it("keeps well-formed sources and sanitizes urls", () => {
    const sources = extractSources({
      sources: [
        { title: "A", url: "https://a.com", snippet: "hello" },
        { title: "B", url: "javascript:alert(1)" }, // unsafe url dropped, title kept
        { snippet: "only a snippet" },
      ],
    });
    expect(sources).toEqual([
      { title: "A", url: "https://a.com/", snippet: "hello" },
      { title: "B" },
      { snippet: "only a snippet" },
    ]);
  });

  it("drops empty sources and non-object entries", () => {
    expect(
      extractSources({ sources: [{}, { title: "  " }, null, "x", 5] }),
    ).toEqual([]);
  });

  it("returns an empty array when sources is missing or not an array", () => {
    expect(extractSources({})).toEqual([]);
    expect(extractSources({ sources: "nope" })).toEqual([]);
  });
});

describe("resultAnswer / resultError", () => {
  it("reads non-empty answer and error strings", () => {
    expect(resultAnswer({ answer: "the sun is big" })).toBe("the sun is big");
    expect(resultError({ error: "quota exhausted" })).toBe("quota exhausted");
  });

  it("returns null for missing or blank fields", () => {
    expect(resultAnswer({})).toBeNull();
    expect(resultAnswer({ answer: "   " })).toBeNull();
    expect(resultError({ error: 123 })).toBeNull();
  });
});

describe("batchStatus", () => {
  it("reads a completed batch", () => {
    expect(batchStatus("batch_generate", { status: "done", text: "draft" })).toEqual({
      kind: "done",
      text: "draft",
    });
  });

  it("reads a processing batch with its id", () => {
    expect(batchStatus("check_batch", { status: "processing", batch_id: "msgbatch_1" })).toEqual({
      kind: "processing",
      batchId: "msgbatch_1",
    });
  });

  it("returns null for non-batch tools or unknown statuses", () => {
    expect(batchStatus("web_research", { status: "done" })).toBeNull();
    expect(batchStatus("batch_generate", { status: "queued" })).toBeNull();
  });
});
