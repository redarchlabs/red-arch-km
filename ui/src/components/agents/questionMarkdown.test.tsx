import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "@/components/common/Markdown";

/**
 * An agent asking a real question writes a real question — options, trade-offs,
 * what it has already tried. It arrived as one flat line:
 *
 *   "Supervisor decision required for T5: do you approve one of the following?
 *    (reply with the option number(s)) 1) Grant agents the ability to attach…
 *    2) Provision Lighthouse/headless Chrome… 3) Run a full site crawl…"
 *
 * — a wall of text somebody has to parse before they can answer it. The text was
 * Markdown all along; the question panels were rendering it as a plain string.
 */
const QUESTION = [
  "Supervisor decision required for **T5**. Approve one of:",
  "",
  "1. Grant agents the ability to attach artifacts to work orders",
  "2. Provision Lighthouse / headless Chrome in the environment",
  "3. Deny provisioning — proceed with a text-only report",
  "",
  "Context: research-analyst has completed only lightweight fetches.",
].join("\n");

describe("an agent's question, rendered", () => {
  it("lays numbered options out as a list rather than one line", () => {
    render(<Markdown content={QUESTION} stripImages />);

    // Three list items, not a paragraph containing "1) … 2) … 3) …".
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("keeps the emphasis the agent wrote", () => {
    render(<Markdown content={QUESTION} stripImages />);

    expect(screen.getByText("T5").tagName).toBe("STRONG");
  });

  it("drops an image the model was talked into emitting", () => {
    // LLM-authored text: an ![](attacker) from a poisoned document would
    // otherwise make the reader's browser fetch that URL.
    const { container } = render(
      <Markdown
        content="see ![leak](https://attacker.example/x.png)"
        stripImages
      />,
    );

    expect(container.querySelector("img")).toBeNull();
  });

  it("still shows a plain question with no markup in it", () => {
    render(<Markdown content="Which domain should I audit?" stripImages />);

    expect(screen.getByText("Which domain should I audit?")).toBeTruthy();
  });
});
