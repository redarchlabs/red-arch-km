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
const setWorkOrderMode = vi.fn();
const listApprovals = vi.fn();
const listQuestions = vi.fn();
const answerQuestion = vi.fn();
const declineQuestion = vi.fn();
const approveApproval = vi.fn();
const denyApproval = vi.fn();
const getWorkOrderMap = vi.fn();
const listAgentRunSteps = vi.fn();

vi.mock("@/lib/api/workOrders", () => ({
  getWorkOrderMap: (...a: unknown[]) => getWorkOrderMap(...a),
  assignWorkOrder: (...a: unknown[]) => assignWorkOrder(...a),
  createWorkOrder: (...a: unknown[]) => createWorkOrder(...a),
  getWorkOrder: (...a: unknown[]) => getWorkOrder(...a),
  setWorkOrderStatus: (...a: unknown[]) => setWorkOrderStatus(...a),
  getWorkOrderEntries: (...a: unknown[]) => getWorkOrderEntries(...a),
  listWorkOrders: (...a: unknown[]) => listWorkOrders(...a),
  replyToWorkOrder: (...a: unknown[]) => replyToWorkOrder(...a),
  setWorkOrderMode: (...a: unknown[]) => setWorkOrderMode(...a),
}));

vi.mock("@/lib/api/agents", () => ({
  listAgentRunSteps: (...a: unknown[]) => listAgentRunSteps(...a),
  listAgents: () => Promise.resolve(agentRoster),
  listApprovals: (...a: unknown[]) => listApprovals(...a),
  listQuestions: (...a: unknown[]) => listQuestions(...a),
  answerQuestion: (...a: unknown[]) => answerQuestion(...a),
  declineQuestion: (...a: unknown[]) => declineQuestion(...a),
  approveApproval: (...a: unknown[]) => approveApproval(...a),
  denyApproval: (...a: unknown[]) => denyApproval(...a),
}));

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (...a: unknown[]) => push(...a) }),
}));

import {
  AgentDiaryNode,
  AgentTimelineNode,
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
  mode: "manual",
  review_level: "standard",
  assigned_agent_id: "agent-1",
  created_by_profile_id: null,
  created_at: "2026-08-09T12:00:00Z",
  updated_at: "2026-08-09T12:00:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  // The approval queue now also asks for questions; nothing pending is the
  // default for every test that is not about them.
  listQuestions.mockResolvedValue([]);
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

describe("AgentTimelineNode run detail", () => {
  // The reader's half of the compaction bargain. The runtime shortens what the
  // MODEL re-reads and keeps every result whole on the step, so this panel has to
  // be able to open the rest — otherwise the detail is retained and unreachable,
  // which is indistinguishable from having thrown it away.
  const laneMap = {
    lanes: [{ key: "chief", label: "chief-of-staff", avatar: null, agent_kind: "coordinator", status: "done" }],
    events: [
      {
        id: "ev-1",
        lane: "chief",
        kind: "finished" as const,
        at: "2026-08-09T10:00:00Z",
        title: "Read the handbook",
        detail: null,
        target_lane: null,
        run_id: "run-1",
      },
    ],
  };

  const runStep = (id: string, kind: string, content: Record<string, unknown>, name: string | null = null) => ({
    id,
    run_id: "run-1",
    seq: 1,
    kind,
    name,
    content,
    tokens: null,
    created_at: "2026-08-09T10:00:00Z",
  });

  // The marker sits at the END, past the on-screen preview's cut, so finding it
  // proves the detail was opened rather than that the preview happened to show it.
  const body = (marker: string) => `${"L".repeat(3000)}${marker}`;
  const shows = (marker: string) => (document.body.textContent ?? "").includes(marker);

  async function openTheRun() {
    getWorkOrderMap.mockResolvedValue(laneMap);
    render(<AgentTimelineNode workOrderId="wo-1" />);
    fireEvent.click(await screen.findByText("Read the handbook"));
    return screen.findByText("What this agent did");
  }

  it("opens the full result a preview stands in for", async () => {
    listAgentRunSteps.mockResolvedValue([
      runStep("s1", "tool_result", { result: { output: body("THE-END") } }, "read_file"),
    ]);
    await openTheRun();

    const toggle = await screen.findByRole("button", { name: "Show full detail" });
    // Nothing past the preview is unrolled until it is asked for.
    expect(shows("THE-END")).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() => expect(shows("THE-END")).toBe(true));
  });

  it("closes it again", async () => {
    listAgentRunSteps.mockResolvedValue([
      runStep("s1", "tool_result", { result: { output: body("THE-END") } }, "read_file"),
    ]);
    await openTheRun();

    fireEvent.click(await screen.findByRole("button", { name: "Show full detail" }));
    await waitFor(() => expect(shows("THE-END")).toBe(true));
    fireEvent.click(await screen.findByRole("button", { name: "Hide full detail" }));

    await waitFor(() => expect(shows("THE-END")).toBe(false));
  });

  it("opens one step without opening the rest", async () => {
    // One switch for the whole panel would produce the JSON dump this view exists
    // to avoid.
    listAgentRunSteps.mockResolvedValue([
      runStep("s1", "tool_result", { result: { output: body("FIRST-END") } }, "read_file"),
      runStep("s2", "tool_result", { result: { output: body("SECOND-END") } }, "read_file"),
    ]);
    await openTheRun();

    const toggles = await screen.findAllByRole("button", { name: "Show full detail" });
    fireEvent.click(toggles[0]);

    await waitFor(() => expect(shows("FIRST-END")).toBe(true));
    expect(shows("SECOND-END")).toBe(false);
  });

  it("offers the record for a result it has no readable prose for", async () => {
    listAgentRunSteps.mockResolvedValue([runStep("s1", "tool_result", { result: { rows: [1, 2] } }, "list_records")]);
    await openTheRun();

    fireEvent.click(await screen.findByRole("button", { name: "Show full detail" }));

    expect(await screen.findByText(/"rows"/)).toBeTruthy();
  });

  it("leaves a short result with nothing to expand", async () => {
    listAgentRunSteps.mockResolvedValue([runStep("s1", "tool_result", { result: { output: "12 days" } }, "get_record")]);
    await openTheRun();

    expect(await screen.findByText("12 days")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Show full detail" })).toBeNull();
  });

  it("explains a fold rather than leaving a gap", async () => {
    listAgentRunSteps.mockResolvedValue([
      runStep("s1", "compaction", {
        summary: "Searched the handbook and found the leave policy.",
        folded: 8,
        before_chars: 64000,
        after_chars: 9000,
      }),
    ]);
    await openTheRun();

    expect(await screen.findByText("Summarised earlier steps")).toBeTruthy();
    expect(screen.getByText("Searched the handbook and found the leave policy.")).toBeTruthy();
    expect(screen.getByText("64,000 → 9,000 chars")).toBeTruthy();
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

    // No attachments pasted, so an empty list — never undefined, which the server
    // would read as a missing field.
    await waitFor(() => expect(replyToWorkOrder).toHaveBeenCalledWith("wo-1", "Yes please", []));
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

describe("WorkOrderActionsNode mode", () => {
  it("says what the chosen mode actually means", async () => {
    // "Automatic" reads as a convenience setting until you know it means nobody
    // is asked before an agent acts.
    getWorkOrder.mockResolvedValue(order({ mode: "automatic", allowed_transitions: [] }));

    render(<WorkOrderActionsNode workOrderId="wo-1" />);

    expect(await screen.findByText(/approves its own actions/)).toBeTruthy();
  });

  it("changes the mode on the server", async () => {
    getWorkOrder.mockResolvedValue(order({ mode: "manual", allowed_transitions: [] }));
    setWorkOrderMode.mockResolvedValue(order({ mode: "plan" }));

    render(<WorkOrderActionsNode workOrderId="wo-1" />);
    fireEvent.change(await screen.findByLabelText("Agent mode"), { target: { value: "plan" } });

    await waitFor(() => expect(setWorkOrderMode).toHaveBeenCalledWith("wo-1", "plan"));
    expect(await screen.findByText(/Cannot change anything/)).toBeTruthy();
  });

  it("can be hidden by config", async () => {
    getWorkOrder.mockResolvedValue(order({ allowed_transitions: [] }));

    render(<WorkOrderActionsNode workOrderId="wo-1" showMode={false} />);

    await screen.findByLabelText("Assigned agent");
    expect(screen.queryByLabelText("Agent mode")).toBeNull();
  });
});

describe("ApprovalQueueNode questions", () => {
  const question = (over: Record<string, unknown> = {}) => ({
    id: "q-1",
    run_id: "run-1",
    audience: "human",
    question: "Which pages should I include?",
    context: null,
    answer: null,
    status: "pending",
    answered_at: null,
    created_at: "2026-08-09T12:00:00Z",
    asked_by: "research-analyst",
    target_agent: null,
    ...over,
  });

  it("shows a question where the block is visible, and who is asking", async () => {
    // An agent parked on a question looks identical to an idle one. This is the
    // whole reason the work order read as "nothing is happening".
    listApprovals.mockResolvedValue([]);
    listQuestions.mockResolvedValue([question()]);

    render(<ApprovalQueueNode scope="org" workOrderId={null} />);

    expect(await screen.findByText(/research-analyst is asking you/)).toBeTruthy();
    expect(screen.getByText("Which pages should I include?")).toBeTruthy();
  });

  it("answers the question and reloads", async () => {
    listApprovals.mockResolvedValue([]);
    listQuestions.mockResolvedValueOnce([question()]).mockResolvedValueOnce([]);
    answerQuestion.mockResolvedValue({ resumed: true });

    render(<ApprovalQueueNode scope="org" workOrderId={null} />);
    fireEvent.change(await screen.findByLabelText("Answer research-analyst"), {
      target: { value: "Public pages only." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));

    await waitFor(() => expect(answerQuestion).toHaveBeenCalledWith("q-1", "Public pages only."));
  });

  it("can unblock the agent without answering", async () => {
    // Declining is not ignoring: the agent is told to use its own judgement,
    // which beats leaving it parked forever.
    listApprovals.mockResolvedValue([]);
    listQuestions.mockResolvedValue([question()]);
    declineQuestion.mockResolvedValue({ resumed: true });

    render(<ApprovalQueueNode scope="org" workOrderId={null} />);
    fireEvent.click(await screen.findByRole("button", { name: "Let it decide" }));

    await waitFor(() => expect(declineQuestion).toHaveBeenCalledWith("q-1"));
  });

  it("never offers a peer consult for a human to answer", async () => {
    // Another agent is already on the hook for those; answering one here would
    // leave the consulted agent's run going with nobody listening.
    listApprovals.mockResolvedValue([]);
    listQuestions.mockResolvedValue([question({ audience: "agent" })]);

    const { container } = render(<ApprovalQueueNode scope="org" workOrderId={null} />);

    await waitFor(() => expect(listQuestions).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("narrows to this work order's runs", async () => {
    listApprovals.mockResolvedValue([]);
    listQuestions.mockResolvedValue([question(), question({ id: "q-2", run_id: "other" })]);

    render(<ApprovalQueueNode scope="work_order" workOrderId="wo-1" runIds={new Set(["run-1"])} />);

    await screen.findByLabelText("Answer research-analyst");
    expect(screen.queryAllByRole("button", { name: "Answer" })).toHaveLength(1);
  });
});
