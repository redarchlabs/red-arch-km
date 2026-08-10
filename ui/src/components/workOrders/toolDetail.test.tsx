import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolDetail, formatPayload } from "./toolDetail";

describe("ToolDetail", () => {
  it("shows what the agent actually asked for", () => {
    // The panel used to render "fetch_web_page done" and nothing else, so a run
    // that had fetched six pages looked the same as one that had done nothing.
    render(
      <ToolDetail
        name="fetch_web_page"
        args={{ url: "https://redarchlabs.com/robots.txt" }}
        result={{ status: 404 }}
      />,
    );

    expect(screen.getByText(/redarchlabs.com\/robots.txt/)).toBeTruthy();
    expect(screen.getByText(/404/)).toBeTruthy();
  });

  it("says a call is still running before its result arrives", () => {
    render(<ToolDetail name="fetch_web_page" args={{ url: "https://x/" }} />);

    expect(screen.getByText("running…")).toBeTruthy();
    // No empty "result" heading while there is nothing to put under it.
    expect(screen.queryByText("result")).toBeNull();
  });

  it("marks a call done once it comes back", () => {
    render(
      <ToolDetail name="get_record" args={{ id: 1 }} result={{ ok: true }} />,
    );

    expect(screen.getByText("done")).toBeTruthy();
  });

  it("names the agent when several are working the order", () => {
    render(
      <ToolDetail name="delegate_task" args={{}} agent="chief-of-staff" />,
    );

    expect(screen.getByText("chief-of-staff")).toBeTruthy();
  });

  it("cuts a huge payload rather than burying every step after it", () => {
    render(<ToolDetail name="read_file" args={{}} result={"x".repeat(9000)} />);

    expect(screen.getByText(/more characters/)).toBeTruthy();
  });

  it("renders nothing for an empty payload", () => {
    render(<ToolDetail name="list_records" args={{}} result={null} />);

    expect(screen.queryByText("arguments")).toBeNull();
    expect(screen.queryByText("result")).toBeNull();
  });
});

describe("formatPayload", () => {
  it("passes a string through unquoted", () => {
    expect(formatPayload("plain text")).toBe("plain text");
  });

  it("pretty-prints an object so it can be read down the page", () => {
    expect(formatPayload({ a: 1 })).toBe('{\n  "a": 1\n}');
  });

  it("survives something that will not serialise", () => {
    const loop: Record<string, unknown> = {};
    loop.self = loop;

    // Better to show its shape than to drop the step's only evidence.
    expect(formatPayload(loop)).toContain("object");
  });

  it("treats null and undefined as nothing to show", () => {
    expect(formatPayload(null)).toBe("");
    expect(formatPayload(undefined)).toBe("");
  });
});

describe("a call the authority gate stopped", () => {
  it("says it is waiting for approval rather than running", () => {
    // The runtime emits tool_call and then approval_required for the same call, so
    // a gated delegation used to render twice, both saying "running…" — against an
    // agent that had in fact stopped and was waiting on a person.
    render(
      <ToolDetail
        name="delegate_task"
        args={{ agent: "research-analyst" }}
        awaitingApproval
      />,
    );

    expect(screen.getByText("waiting for your approval")).toBeTruthy();
    expect(screen.queryByText("running…")).toBeNull();
  });

  it("goes back to plain done once it is approved and returns", () => {
    render(
      <ToolDetail
        name="delegate_task"
        args={{}}
        result={{ status: "queued" }}
        awaitingApproval
      />,
    );

    expect(screen.getByText("done")).toBeTruthy();
  });
});
