import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FormRender } from "@/lib/api/forms";
import { FormRenderer } from "./FormRenderer";

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), run: vi.fn() }));
vi.mock("@/lib/api/entityRecords", async (original) => ({
  ...await original<typeof import("@/lib/api/entityRecords")>(),
  listRecords: mocks.list, createRecord: mocks.create,
}));
vi.mock("@/lib/api/workflows", async (original) => ({
  ...await original<typeof import("@/lib/api/workflows")>(), runWorkflow: mocks.run,
}));
vi.mock("@/lib/api/runStream", () => ({ streamRunTokens: async function* () {} }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const form = {
  form_id: "chat", form_name: "Chat", status: "editable", catalog: [], relationships: [], values: {}, related: {},
  config: { version: 2, elements: [{ id: "chat", type: "chat", answer_workflow_id: "answer", poll_ms: 500 }] },
} as unknown as FormRender;
function mount() { render(<FormRenderer render={form} mode="fill" viewContext />); }
async function send(text: string) {
  fireEvent.change(screen.getByPlaceholderText("Message the robot…"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(mocks.run).toHaveBeenCalled());
}

describe("chat turn ownership", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue({ items: [] });
    let id = 0;
    mocks.create.mockImplementation(async (entity: string) => ({ id: entity === "robot_conversation" ? `c${++id}` : `q${id}` }));
    mocks.run.mockImplementation(() => new Promise(() => {}));
    HTMLElement.prototype.scrollTo = vi.fn();
  });

  it("allows drafting but prevents a second workflow while the first is answering", async () => {
    mount();
    await send("first");
    const input = screen.getByPlaceholderText("Message the robot…");
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "second" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.run).toHaveBeenCalledTimes(1);
  });

  it("ignores failure from a workflow belonging to an abandoned conversation", async () => {
    const old = deferred<{ status: string }>();
    mocks.run.mockReturnValueOnce(old.promise);
    mount();
    await send("first");
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    await send("second");
    await waitFor(() => expect(mocks.run).toHaveBeenCalledTimes(2));
    await act(async () => old.reject(new Error("old failure")));
    expect(screen.queryByText("old failure")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Message the robot…"), { target: { value: "third" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("requires the current question's saved reply and workflow completion", async () => {
    const run = deferred<{ status: string }>();
    mocks.run.mockReturnValueOnce(run.promise);
    let rows: object[] = [];
    mocks.list.mockImplementation(async (entity: string) => ({ items: entity === "robot_message" ? rows : [] }));
    mount();
    await send("first");
    rows = [{ id: "old", conversation: "c1", role: "robot", text: "old answer", created_at: "1" }];
    await act(async () => run.resolve({ status: "succeeded" }));
    fireEvent.change(screen.getByPlaceholderText("Message the robot…"), { target: { value: "second" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    rows = [
      ...rows,
      { id: "q1", conversation: "c1", role: "person", text: "first", created_at: "2" },
      { id: "reply", conversation: "c1", role: "robot", text: "new answer", created_at: "3" },
    ];
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeEnabled());
  });
});
