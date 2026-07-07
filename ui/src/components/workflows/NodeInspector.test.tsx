import { type Node } from "@xyflow/react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
