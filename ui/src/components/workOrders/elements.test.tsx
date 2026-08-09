import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkOrder, WorkOrderEntry } from "@/lib/api/workOrders";

const agentRoster = [
  { id: "agent-1", name: "chief-of-staff", enabled: true },
  { id: "agent-2", name: "research-analyst", enabled: true },
];
const getWorkOrder = vi.fn();
const setWorkOrderStatus = vi.fn();
const assignWorkOrder = vi.fn();
const getWorkOrderEntries = vi.fn();
const listWorkOrders = vi.fn();
const createWorkOrder = vi.fn();
const replyToWorkOrder = vi.fn();
const listApprovals = vi.fn();
const approveApproval = vi.fn();
const denyApproval = vi.fn();

vi.mock("@/lib/api/workOrders", () => ({
  getWorkOrderMap: vi.fn(),
  assignWorkOrder: (...a: unknown[]) => assignWorkOrder(...a),
  createWorkOrder: (...a: unknown[]) => createWorkOrder(...a),
  getWorkOrder: (...a: unknown[]) => getWorkOrder(...a),
  setWorkOrderStatus: (...a: unknown[]) => setWorkOrderStatus(...a),
  getWorkOrderEntries: (...a: unknown[]) => getWorkOrderEntries(...a),
  listWorkOrders: (...a: unknown[]) => listWorkOrders(...a),
  replyToWorkOrder: (...a: unknown[]) => replyToWorkOrder(...a),
}));

vi.mock("@/lib/api/agents", () => ({
  listAgentRunSteps: vi.fn(),
  listAgents: () => Promise.resolve(agentRoster),
  listApprovals: (...a: unknown[]) => listApprovals(...a),
  approveApproval: (...a: unknown[]) => approveApproval(...a),
  denyApproval: (...a: unknown[]) => denyApproval(...a),
}));

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (...a: unknown[]) => push(...a) }),
}));

import {
  AgentDiaryNode,
  ApprovalQueueNode,
  WorkOrderActionsNode,
  WorkOrderCreateNode,
  WorkOrderListNode,
} from "./elements";

const entry = (id: string, text: string): WorkOrderEntry => ({
  id,
  agent_id: null,
  agent_run_id: null,
  role: "chief",
  text,
  created_at: "2026-08-09T12:00:00Z",
});

const order = (over: Partial<WorkOrder> = {}): WorkOrder => ({
  id: "wo-1",
  slug: "wo-1",
  title: "SEO check",
  status: "in_progress",
  body: null,
  priority: "normal",
  assigned_agent_id: "agent-1",
  created_by_profile_id: null,
  created_at: "2026-08-09T12:00:00Z",
  updated_at: "2026-08-09T12:00:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("WorkOrderActionsNode", () => {
  it("offers exactly the transitions the server allows", async () => {
    // The buttons come from the server's own state machine. Deriving them on the
    // client would offer moves the server rejects the moment that table changes.
    getWorkOrder.mockResolvedValue(order({ allowed_transitions: ["cancelled", "done"] }));

    render(<WorkOrderActionsNode workOrderId="wo-1" />);

    expect(await screen.findByRole("button", { name: "Mark done" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start work" })).toBeNull();
  });

  it("labels in_progress as starting work, because that is what it does", async () => {
    // On an assigned order this queues the agent's run. "in_progress" describes
    // the resulting status; "Start work" describes the act.
    getWorkOrder.mockResolvedValue(order({ status: "approved", allowed_transitions: ["in_progress"] }));

    render(<WorkOrderActionsNode workOrderId="wo-1" />);

    expect(await screen.findByRole("button", { name: "Start work" })).toBeTruthy();
  });

  it("shows the server's refusal verbatim", async () => {
    // Starting an order whose agent is disabled is refused, and the message names
    // the agent — flattening it to "failed" throws away the only actionable part.
    getWorkOrder.mockResolvedValue(order({ status: "approved", allowed_transitions: ["in_progress"] }));
    setWorkOrderStatus.mockRejectedValue({
      isAxiosError: true,
      response: { data: { detail: "assigned to an agent that cannot run (missing or disabled)" } },
    });

    render(<WorkOrderActionsNode workOrderId="wo-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Start work" }));

    expect(await screen.findByText(/cannot run/)).toBeTruthy();
  });
  it("lets the assignee be chosen where the work is started", async () => {
    // "Start work" on an unassigned order does nothing, so the choice of who does
    // it belongs on the same control rather than only on the filing form.
    getWorkOrder.mockResolvedValue(order({ assigned_agent_id: null, allowed_transitions: [] }));
    assignWorkOrder.mockResolvedValue(order({ assigned_agent_id: "agent-2" }));

    render(<WorkOrderActionsNode workOrderId="wo-1" />);
    const picker = (await screen.findByLabelText("Assigned agent")) as HTMLSelectElement;
    fireEvent.change(picker, { target: { value: "agent-2" } });

    await waitFor(() => expect(assignWorkOrder).toHaveBeenCalledWith("wo-1", "agent-2"));
  });

  it("offers unassigned as a real choice", async () => {
    // An order nobody has picked up is a request, not a misconfiguration.
    getWorkOrder.mockResolvedValue(order({ allowed_transitions: [] }));

    render(<WorkOrderActionsNode workOrderId="wo-1" />);

    expect(await screen.findByRole("option", { name: "Unassigned" })).toBeTruthy();
  });
});

describe("AgentDiaryNode", () => {
  it("does not offer history when there is none", async () => {
    getWorkOrderEntries.mockResolvedValue({ entries: [entry("e1", "only one")], has_more: false });

    render(<AgentDiaryNode workOrderId="wo-1" />);

    expect(await screen.findByText("only one")).toBeTruthy();
    expect(screen.queryByText("Older entries")).toBeNull();
  });

  it("walks backwards with a cursor rather than an offset", async () => {
    // Agents append while a reader scrolls back; an offset would repeat or skip a
    // row each time one lands between requests. The cursor is the oldest entry held.
    getWorkOrderEntries
      .mockResolvedValueOnce({ entries: [entry("e5", "newest")], has_more: true })
      .mockResolvedValueOnce({ entries: [entry("e4", "older")], has_more: false });

    render(<AgentDiaryNode workOrderId="wo-1" pageSize={1} />);
    fireEvent.click(await screen.findByText("Older entries"));

    await waitFor(() => expect(screen.getByText("older")).toBeTruthy());
    expect(getWorkOrderEntries).toHaveBeenLastCalledWith("wo-1", { limit: 1, before: "e5" });
    // Prepended, so history reads upward from what was already on screen.
    expect(screen.getByText("newest")).toBeTruthy();
  });

  it("renders agent Markdown as formatted text", async () => {
    // Entries are written by a model told to explain itself, so they arrive as
    // Markdown; showing the source is a wall of literal asterisks.
    getWorkOrderEntries.mockResolvedValue({ entries: [entry("e1", "**Audit** the site")], has_more: false });

    render(<AgentDiaryNode workOrderId="wo-1" />);

    const strong = await screen.findByText("Audit");
    expect(strong.tagName).toBe("STRONG");
  });
});

describe("ApprovalQueueNode", () => {
  const pending = [
    {
      id: "ap-1",
      run_id: "run-1",
      tool_name: "delegate_task",
      arguments: { agent: "research-analyst" },
      status: "pending",
      decided_at: null,
      created_at: "2026-08-09T12:00:00Z",
      workflow_run_id: null,
      workflow_id: null,
    },
  ];

  it("stays out of the way when nothing is pending", async () => {
    listApprovals.mockResolvedValue([]);

    const { container } = render(<ApprovalQueueNode scope="org" workOrderId={null} />);

    await waitFor(() => expect(listApprovals).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("puts the decision where the stall is visible", async () => {
    listApprovals.mockResolvedValue(pending);
    approveApproval.mockResolvedValue({ ...pending[0], status: "approved" });

    render(<ApprovalQueueNode scope="org" workOrderId={null} />);
    fireEvent.click(await screen.findByRole("button", { name: /Approve/ }));

    await waitFor(() => expect(approveApproval).toHaveBeenCalledWith("ap-1"));
  });

  it("reports a decision someone else already made", async () => {
    // Two open tabs racing is normal, and a 409 means this click changed nothing.
    listApprovals.mockResolvedValue(pending);
    denyApproval.mockRejectedValue(new Error("409"));

    render(<ApprovalQueueNode scope="org" workOrderId={null} />);
    fireEvent.click(await screen.findByRole("button", { name: /Deny/ }));

    expect(await screen.findByText(/already made/)).toBeTruthy();
  });
});

describe("WorkOrderListNode", () => {
  it("does not link rows with nowhere to go", async () => {
    // A read-only wallboard has no detail view; a link to nothing is worse than text.
    listWorkOrders.mockResolvedValue([order()]);

    const { container } = render(<WorkOrderListNode />);

    await screen.findByText("SEO check");
    expect(container.querySelector("a")).toBeNull();
  });

  it("passes the order as the detail view's record", async () => {
    listWorkOrders.mockResolvedValue([order()]);

    const { container } = render(<WorkOrderListNode detailViewId="view-9" />);

    await screen.findByText("SEO check");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/views/view-9/view?record_id=wo-1");
  });

  it("narrows to the statuses asked for", async () => {
    listWorkOrders.mockResolvedValue([order(), order({ id: "wo-2", title: "Done thing", status: "done" })]);

    render(<WorkOrderListNode statuses={["done"]} />);

    expect(await screen.findByText("Done thing")).toBeTruthy();
    expect(screen.queryByText("SEO check")).toBeNull();
  });
});

describe("WorkOrderCreateNode", () => {
  it("renders no form of its own", async () => {
    // Every element renders inside the FormRenderer's <form>, and HTML forbids
    // nesting them: React reports a hydration error and the browser recovers by
    // dropping the inner form's fields, so the control silently stops working.
    const { container } = render(<WorkOrderCreateNode />);
    await screen.findByRole("option", { name: "chief-of-staff" });

    expect(container.querySelector("form")).toBeNull();
  });

  it("files the order from the button", async () => {
    createWorkOrder.mockResolvedValue({ id: "wo-9" });

    render(<WorkOrderCreateNode detailViewId="view-9" />);
    fireEvent.change(screen.getByLabelText("Work order title"), { target: { value: "Audit the site" } });
    fireEvent.click(screen.getByRole("button", { name: "File it" }));

    await waitFor(() => expect(createWorkOrder).toHaveBeenCalled());
    expect(createWorkOrder.mock.calls[0][0].title).toBe("Audit the site");
    // Straight to the order just filed — the next thing anyone wants is to start it.
    await waitFor(() => expect(push).toHaveBeenCalledWith("/views/view-9/view?record_id=wo-9"));
  });

  it("files the order from the keyboard", async () => {
    // Enter used to be the browser's job. Without the hand-wired handler it now
    // reaches the *outer* form and submits that instead.
    createWorkOrder.mockResolvedValue({ id: "wo-9" });

    render(<WorkOrderCreateNode />);
    const title = screen.getByLabelText("Work order title");
    fireEvent.change(title, { target: { value: "Audit the site" } });
    fireEvent.keyDown(title, { key: "Enter" });

    await waitFor(() => expect(createWorkOrder).toHaveBeenCalled());
  });
});

describe("AgentDiaryNode reply", () => {
  it("sends the reply and reloads, so the outcome is visible", async () => {
    // The server decides what a reply does — start a run, or record that it could
    // not be delivered. Either way the answer arrives as a diary entry.
    getWorkOrderEntries
      .mockResolvedValueOnce({ entries: [entry("e1", "Would you like me to do that?")], has_more: false })
      .mockResolvedValueOnce({
        entries: [entry("e1", "Would you like me to do that?"), entry("e2", "Yes please")],
        has_more: false,
      });
    replyToWorkOrder.mockResolvedValue(order());

    render(<AgentDiaryNode workOrderId="wo-1" />);
    await screen.findByText("Would you like me to do that?");
    fireEvent.change(screen.getByLabelText("Reply to the agent"), { target: { value: "Yes please" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(replyToWorkOrder).toHaveBeenCalledWith("wo-1", "Yes please"));
    expect(await screen.findByText("Yes please")).toBeTruthy();
  });

  it("keeps the text when sending fails", async () => {
    // Clearing the box on failure loses what the person wrote, with no copy of it
    // anywhere — the reply was never recorded.
    getWorkOrderEntries.mockResolvedValue({ entries: [entry("e1", "anything")], has_more: false });
    replyToWorkOrder.mockRejectedValue({
      isAxiosError: true,
      response: { data: { detail: "Work order is not in progress" } },
    });

    render(<AgentDiaryNode workOrderId="wo-1" />);
    await screen.findByText("anything");
    fireEvent.change(screen.getByLabelText("Reply to the agent"), { target: { value: "still here" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/not in progress/)).toBeTruthy();
    expect((screen.getByLabelText("Reply to the agent") as HTMLTextAreaElement).value).toBe("still here");
  });

  it("can be turned off by config", async () => {
    getWorkOrderEntries.mockResolvedValue({ entries: [entry("e1", "read only")], has_more: false });

    render(<AgentDiaryNode workOrderId="wo-1" allowReply={false} />);

    await screen.findByText("read only");
    expect(screen.queryByLabelText("Reply to the agent")).toBeNull();
  });
});
