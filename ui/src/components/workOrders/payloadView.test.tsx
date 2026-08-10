import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PayloadView, humanise } from "./payloadView";

/** The real result of a fetch_web_page call, which is what sent this back for a
 *  second pass: readable as JSON, but the facts were outnumbered by punctuation. */
const PAGE = {
  status: 404,
  final_url: "https://redarchlabs.com/sitemap.xml",
  title: "Page not found · GitHub Pages",
  meta_description: null,
  canonical: null,
  redirects: [],
  h1: ["404"],
  link_count: 5,
  images_without_alt: 2,
};

describe("PayloadView", () => {
  it("labels each fact instead of quoting it", () => {
    render(<PayloadView label="result" value={PAGE} />);

    // Keys read as words; values stand on their own.
    expect(screen.getByText("meta description")).toBeTruthy();
    expect(screen.getByText("final url")).toBeTruthy();
    // Twice over: the status code and the page's own H1.
    expect(screen.getAllByText("404").length).toBe(2);
    expect(screen.getByText("Page not found · GitHub Pages")).toBeTruthy();
  });

  it("says none rather than null", () => {
    render(<PayloadView label="result" value={{ canonical: null }} />);

    expect(screen.getByText("none")).toBeTruthy();
    expect(screen.queryByText("null")).toBeNull();
  });

  it("says none for an empty list", () => {
    render(<PayloadView label="result" value={{ redirects: [] }} />);

    expect(screen.getByText("none")).toBeTruthy();
  });

  it("reads a list of plain values as a sentence", () => {
    render(
      <PayloadView label="result" value={{ h1: ["Red Arch", "Pricing"] }} />,
    );

    expect(screen.getByText("Red Arch, Pricing")).toBeTruthy();
  });

  it("says yes and no rather than true and false", () => {
    render(<PayloadView label="result" value={{ mobile_friendly: true }} />);

    expect(screen.getByText("yes")).toBeTruthy();
  });

  it("gives a long value the full width instead of a narrow column", () => {
    // A delegation brief squeezed into a two-column grid becomes a ribbon.
    const brief = "Proceed with the live crawl. ".repeat(20);
    render(<PayloadView label="arguments" value={{ task: brief }} />);

    expect(screen.getByText(/Proceed with the live crawl/)).toBeTruthy();
  });

  it("shows a plain string as prose with no braces at all", () => {
    render(<PayloadView label="result" value="404 Not Found" />);

    expect(screen.getByText("404 Not Found")).toBeTruthy();
    // Nothing to switch: a string has no structure to lay out.
    expect(screen.queryByRole("button", { name: "raw" })).toBeNull();
  });

  it("keeps the exact text one click away", () => {
    // The reader who wants the bytes should not have to trust the renderer.
    render(<PayloadView label="result" value={PAGE} />);

    fireEvent.click(screen.getByRole("button", { name: "raw" }));

    expect(screen.getByText(/"final_url"/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "formatted" })).toBeTruthy();
  });

  it("falls back to JSON only at the depth where nesting starts", () => {
    render(
      <PayloadView
        label="result"
        value={{ outer: { middle: { inner: { deep: 1 } } } }}
      />,
    );

    // The outer keys are still laid out…
    expect(screen.getByText("outer")).toBeTruthy();
    // …and the part that is genuinely a structure is shown as one.
    expect(screen.getByText(/"deep"/)).toBeTruthy();
  });

  it("renders nothing when there is nothing to say", () => {
    const { container } = render(<PayloadView label="arguments" value={{}} />);

    expect(container.textContent).toBe("");
  });
});

describe("humanise", () => {
  it("turns a key into words without renaming it", () => {
    expect(humanise("meta_description")).toBe("meta description");
    expect(humanise("images_without_alt")).toBe("images without alt");
    expect(humanise("url")).toBe("url");
  });
});
