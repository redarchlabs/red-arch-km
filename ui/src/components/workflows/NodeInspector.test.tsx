import { type Node } from "@xyflow/react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Connection } from "@/lib/api/connections";
import { ACTION_CONFIG_FIELDS, ACTION_TYPES } from "./actionTypes";
import { NodeInspector } from "./NodeInspector";

afterEach(cleanup);

function conditionNode(id: string, expr: unknown): Node {
  return { id, type: "condition", position: { x: 0, y: 0 }, data: { expr } };
}

function actionNode(actionType: string, config: Record<string, unknown> = {}): Node {
  return {
    id: "act1",
    type: "action",
    position: { x: 0, y: 0 },
    data: { action_type: actionType, config },
  };
}

function taskNode(data: Record<string, unknown> = {}): Node {
  return {
    id: "task1",
    type: "task",
    position: { x: 0, y: 0 },
    data: { task_type: "service", action_type: "", config: {}, ...data },
  };
}

function gatewayNode(data: Record<string, unknown> = {}): Node {
  return {
    id: "gw1",
    type: "gateway",
    position: { x: 0, y: 0 },
    data: { gateway_type: "exclusive", ...data },
  };
}

function eventNode(data: Record<string, unknown> = {}): Node {
  return { id: "evt1", type: "event", position: { x: 0, y: 0 }, data };
}

describe("NodeInspector condition-node isolation (HIGH regression)", () => {
  it("does not leak raw/row mode across node selection", () => {
    const onChangeData = vi.fn();
    // Node A has a row-representable expression → simple editor.
    const { rerender } = render(
      <NodeInspector
        node={conditionNode("condA", { "==": [{ var: "after.status" }, "closed"] })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText("Switch to simple editor")).toBeNull(); // A: row mode

    // Selecting node B — whose expr is NOT row-representable — must remount the
    // editor (via key={node.id}) so it opens in raw mode and preserves B's expr
    // rather than reusing A's row-mode state and silently discarding it.
    rerender(
      <NodeInspector
        node={conditionNode("condB", { or: [{ "==": [{ var: "a" }, 1] }] })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );

    // Raw editor is shown for B (the "Switch to simple editor" affordance only
    // exists in raw mode), and its textarea holds B's untouched expression.
    expect(screen.queryByText("Switch to simple editor")).not.toBeNull();
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toContain("or");
    // Merely selecting B must not have rewritten its expression.
    expect(onChangeData).not.toHaveBeenCalled();
  });
});

describe("NodeInspector action config", () => {
  it.each(ACTION_TYPES)("renders every config input for the %s action", (actionType) => {
    render(
      <NodeInspector
        node={actionNode(actionType)}
        entities={[]}
        forms={[]}
        fields={[]}
        onChangeData={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    for (const cfg of ACTION_CONFIG_FIELDS[actionType]) {
      expect(screen.queryByText(cfg.label)).not.toBeNull();
    }
  });

  it("threads an edited config value back through onChangeData", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={actionNode("update_record_field", { field: "status" })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    // The "New value" input carries the placeholder "closed" per actionTypes.
    fireEvent.change(screen.getByPlaceholderText("closed"), { target: { value: "done" } });
    expect(onChangeData).toHaveBeenCalledWith(
      "act1",
      expect.objectContaining({
        action_type: "update_record_field",
        config: { field: "status", value: "done" },
      }),
    );
  });
});

describe("NodeInspector retry policy", () => {
  it("enabling retry writes a default policy through the store update path", () => {
    const onChangeData = vi.fn();
    render(<NodeInspector node={taskNode()} onChangeData={onChangeData} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByLabelText(/Retry on failure/i));
    expect(onChangeData).toHaveBeenCalledWith(
      "task1",
      expect.objectContaining({
        retry: { max_attempts: 3, base_delay_seconds: 1, max_delay_seconds: 300 },
      }),
    );
  });

  it("disabling retry DELETES the retry key (not max_attempts:1)", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={taskNode({ retry: { max_attempts: 3, base_delay_seconds: 1, max_delay_seconds: 300 } })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText(/Retry on failure/i));
    expect(onChangeData).toHaveBeenCalledTimes(1);
    const [, nextData] = onChangeData.mock.calls[0];
    expect(nextData).not.toHaveProperty("retry");
    expect(nextData).toMatchObject({ task_type: "service" });
  });

  it("edits max_attempts through the retry editor", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={taskNode({ retry: { max_attempts: 3, base_delay_seconds: 1, max_delay_seconds: 300 } })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    const input = screen.getByDisplayValue("3") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "6" } });
    expect(onChangeData).toHaveBeenCalledWith(
      "task1",
      expect.objectContaining({ retry: expect.objectContaining({ max_attempts: 6 }) }),
    );
  });

  it("toggles continue_on_error on and off", () => {
    const onChangeData = vi.fn();
    const { rerender } = render(
      <NodeInspector node={taskNode()} onChangeData={onChangeData} onDelete={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText(/Continue the workflow/i));
    expect(onChangeData).toHaveBeenCalledWith(
      "task1",
      expect.objectContaining({ continue_on_error: true }),
    );

    rerender(
      <NodeInspector
        node={taskNode({ continue_on_error: true })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText(/Continue the workflow/i));
    const [, nextData] = onChangeData.mock.calls[onChangeData.mock.calls.length - 1];
    expect(nextData).not.toHaveProperty("continue_on_error");
  });
});

describe("NodeInspector business-rule task (decision table)", () => {
  it("renders the decision-table editor and adds a rule through onChangeData", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={taskNode({ task_type: "businessRule" })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText("Hit policy")).not.toBeNull();
    fireEvent.click(screen.getByText("Add rule"));
    expect(onChangeData).toHaveBeenCalledWith(
      "task1",
      expect.objectContaining({
        decision_table: { hit_policy: "first", rules: [{ when: null, output: {} }] },
      }),
    );
  });
});

describe("NodeInspector script task (transform)", () => {
  it("edits an expression cell and threads the parsed transform back", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={taskNode({ task_type: "script", transform: { total: 5 } })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    // The seeded row shows variable "total" and expression "5".
    fireEvent.change(screen.getByDisplayValue("5"), { target: { value: "10" } });
    expect(onChangeData).toHaveBeenCalledWith(
      "task1",
      expect.objectContaining({ transform: { total: 10 } }),
    );
  });
});

describe("NodeInspector user/manual task", () => {
  it("edits the assignee for a user task", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={taskNode({ task_type: "user" })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText("Label")).not.toBeNull();
    fireEvent.change(screen.getByPlaceholderText("user@example.com or a role"), {
      target: { value: "ops@example.com" },
    });
    expect(onChangeData).toHaveBeenCalledWith(
      "task1",
      expect.objectContaining({ assignee: "ops@example.com" }),
    );
  });
});

describe("NodeInspector gateway routing modes", () => {
  it("switching a condition gateway to multi-way drops expr and seeds cases", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={gatewayNode({ expr: { "==": [{ var: "after.status" }, "open"] } })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText("Branch condition (true / false)")).not.toBeNull();
    fireEvent.change(screen.getByDisplayValue("Two-way (true / false)"), {
      target: { value: "cases" },
    });
    const [, next] = onChangeData.mock.calls[onChangeData.mock.calls.length - 1];
    expect(next).not.toHaveProperty("expr");
    expect(next).toMatchObject({ gateway_type: "exclusive", cases: [] });
  });

  it("switching a multi-way gateway back to condition drops cases", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={gatewayNode({ cases: [{ handle: "c1", label: "A", expr: null }] })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    // Multi-way mode shows the shared cases editor.
    expect(screen.queryByText("Add case")).not.toBeNull();
    fireEvent.change(screen.getByDisplayValue("Multi-way (cases)"), {
      target: { value: "condition" },
    });
    const [, next] = onChangeData.mock.calls[onChangeData.mock.calls.length - 1];
    expect(next).not.toHaveProperty("cases");
    expect(next).toMatchObject({ gateway_type: "exclusive" });
  });

  it("shows a fork/join note (no condition) for a parallel gateway", () => {
    render(
      <NodeInspector
        node={gatewayNode({ gateway_type: "parallel" })}
        onChangeData={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText(/Forks a token/i)).not.toBeNull();
    expect(screen.queryByText("Branch condition (true / false)")).toBeNull();
  });
});

describe("NodeInspector event fields", () => {
  it("edits delay_seconds for a timer intermediate event", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={eventNode({ position: "intermediate", event_type: "timer" })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("60"), { target: { value: "90" } });
    expect(onChangeData).toHaveBeenCalledWith(
      "evt1",
      expect.objectContaining({ delay_seconds: 90 }),
    );
  });

  it("edits error_code for an error end event", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={eventNode({ position: "end", event_type: "error" })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("payment_failed"), {
      target: { value: "boom" },
    });
    expect(onChangeData).toHaveBeenCalledWith(
      "evt1",
      expect.objectContaining({ error_code: "boom" }),
    );
  });

  it("does not show delay_seconds for a plain end event", () => {
    render(
      <NodeInspector
        node={eventNode({ position: "end", event_type: "none" })}
        onChangeData={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText("Delay (seconds)")).toBeNull();
  });
});

describe("NodeInspector http_request connector", () => {
  const CONN: Connection = {
    id: "c1",
    name: "Stripe",
    kind: "http",
    base_url: "https://api.stripe.com",
    auth_type: "bearer",
    config: {},
    has_secret: true,
  };

  it("renders the connector fields for an http_request task", () => {
    render(
      <NodeInspector
        node={taskNode({ action_type: "http_request" })}
        onChangeData={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByText("Connection")).not.toBeNull();
    expect(screen.queryByText("Method")).not.toBeNull();
    expect(screen.queryByText("Capture response as")).not.toBeNull();
  });

  it("prunes an emptied field through the full-replace path (not a shallow merge)", () => {
    const onChangeData = vi.fn();
    render(
      <NodeInspector
        node={taskNode({ action_type: "http_request", config: { connection: "Stripe", method: "GET" } })}
        onChangeData={onChangeData}
        onDelete={vi.fn()}
      />,
    );
    // No connections passed → the connection is a free-text input showing "Stripe".
    fireEvent.change(screen.getByDisplayValue("Stripe"), { target: { value: "" } });
    const [, next] = onChangeData.mock.calls[onChangeData.mock.calls.length - 1];
    // The key is GONE (a shallow {...data, ...patch} merge would have kept it).
    expect(next.config).not.toHaveProperty("connection");
    expect(next.config).toMatchObject({ method: "GET" });
    expect(next).toMatchObject({ task_type: "service", action_type: "http_request" });
  });

  it("offers a saved-connection picker when connections are provided", () => {
    render(
      <NodeInspector
        node={taskNode({ action_type: "http_request" })}
        connections={[CONN]}
        onChangeData={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const option = screen.getByRole("option", { name: /Stripe/ }) as HTMLOptionElement;
    expect(option.value).toBe("Stripe");
  });
});
