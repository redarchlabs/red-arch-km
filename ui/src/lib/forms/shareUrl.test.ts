import { describe, expect, it } from "vitest";

import { isLoopbackUrl, resolveShareUrl, shareTarget } from "./shareUrl";

describe("resolveShareUrl", () => {
  it("makes a relative view link absolute against the page's own origin", () => {
    expect(resolveShareUrl("/views/abc/kiosk", "http://192.168.1.50:3002")).toBe(
      "http://192.168.1.50:3002/views/abc/kiosk",
    );
  });

  it("keeps the query string, which is what carries the record", () => {
    expect(resolveShareUrl("/views/abc/kiosk?record_id=42", "http://192.168.1.50:3002")).toBe(
      "http://192.168.1.50:3002/views/abc/kiosk?record_id=42",
    );
  });

  it("prefers a configured host over the address the console was opened at", () => {
    // The whole point of `host`: the operator can open the console however they
    // like and the tablet still gets a reachable address.
    expect(resolveShareUrl("/views/abc/kiosk", "http://localhost:3002", "http://192.168.1.50:3002")).toBe(
      "http://192.168.1.50:3002/views/abc/kiosk",
    );
  });

  it("accepts a host written without a scheme", () => {
    expect(resolveShareUrl("/x", "http://localhost:3002", "192.168.1.50:3002")).toBe(
      "http://192.168.1.50:3002/x",
    );
  });

  it("leaves an already-absolute url alone, host or no host", () => {
    expect(resolveShareUrl("https://app.example.com/x", "http://localhost:3002", "http://192.168.1.50:3002")).toBe(
      "https://app.example.com/x",
    );
  });

  it("returns empty for an empty url rather than encoding nothing", () => {
    expect(resolveShareUrl("", "http://localhost:3002")).toBe("");
    expect(resolveShareUrl("   ", "http://localhost:3002")).toBe("");
  });

  it("falls back to the raw url when there is no origin to resolve against", () => {
    expect(resolveShareUrl("/views/abc", "")).toBe("/views/abc");
  });
});

describe("shareTarget", () => {
  it("fills record tokens and then makes the result absolute", () => {
    // The two steps a QR code and a copy_link button both need, in that order:
    // a template is useless absolute, and a filled relative url is useless off-device.
    expect(
      shareTarget("/views/{view_slug}/kiosk?record_id={id}", { view_slug: "lesson", id: "42" }, "http://192.168.1.50:3002"),
    ).toBe("http://192.168.1.50:3002/views/lesson/kiosk?record_id=42");
  });

  it("prefers the configured host, so a console on localhost still copies a reachable link", () => {
    expect(shareTarget("/s/{token}", { token: "abc" }, "http://localhost:3002", "http://192.168.1.50:3002")).toBe(
      "http://192.168.1.50:3002/s/abc",
    );
  });

  it("neutralises a dangerous scheme before it can reach the clipboard", () => {
    expect(shareTarget("javascript:alert(1)", {}, "http://192.168.1.50:3002")).toBe(
      "http://192.168.1.50:3002/#",
    );
  });

  it("returns empty for an empty template, so callers can say 'no link' instead of copying the origin", () => {
    expect(shareTarget("", {}, "http://192.168.1.50:3002")).toBe("");
  });
});

describe("isLoopbackUrl", () => {
  it("flags the addresses that only mean 'this machine'", () => {
    // A QR of one of these scans fine and then fails on the tablet, which is the
    // confusing outcome the warning exists to prevent.
    expect(isLoopbackUrl("http://localhost:3002/x")).toBe(true);
    expect(isLoopbackUrl("http://127.0.0.1:3002/x")).toBe(true);
    expect(isLoopbackUrl("http://0.0.0.0:3002/x")).toBe(true);
  });

  it("passes a real LAN or public address", () => {
    expect(isLoopbackUrl("http://192.168.1.50:3002/x")).toBe(false);
    expect(isLoopbackUrl("https://app.example.com/x")).toBe(false);
  });

  it("does not flag a relative url it cannot parse", () => {
    expect(isLoopbackUrl("/views/abc")).toBe(false);
  });
});
