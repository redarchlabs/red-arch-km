import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SourceCitations } from "./SourceCitations";

afterEach(cleanup);

describe("SourceCitations", () => {
  it("renders a safe source as a hardened external link", () => {
    render(
      <SourceCitations
        sources={[{ title: "EV Battery News", url: "https://www.example.com/ev", snippet: "big news" }]}
      />,
    );
    const link = screen.getByRole("link", { name: /Open source 1/i });
    expect(link.getAttribute("href")).toBe("https://www.example.com/ev");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.textContent).toContain("EV Battery News");
    expect(link.textContent).toContain("example.com"); // host with www. stripped
    expect(link.textContent).toContain("big news");
  });

  it("renders a source without a url as static text, not a link", () => {
    render(<SourceCitations sources={[{ title: "No link here" }]} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("No link here")).toBeTruthy();
  });

  it("renders nothing when there are no sources", () => {
    const { container } = render(<SourceCitations sources={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("summarizes the source count", () => {
    render(<SourceCitations sources={[{ url: "https://a.com" }, { url: "https://b.com" }]} />);
    expect(screen.getByText(/2 sources/)).toBeTruthy();
  });
});
