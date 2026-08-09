import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getWorkOrderMap = vi.fn();
const listAgentRunSteps = vi.fn();
const mintLiveTicket = vi.fn();

vi.mock("@/lib/api/workOrders", () => ({ getWorkOrderMap: (...a: unknown[]) => getWorkOrderMap(...a) }));
vi.mock("@/lib/api/agents", () => ({ listAgentRunSteps: (...a: unknown[]) => listAgentRunSteps(...a) }));
vi.mock("@/lib/api/agentsLive", () => ({
  mintLiveTicket: (...a: unknown[]) => mintLiveTicket(...a),
  liveSocketUrl: () => "ws://test/ws",
}));
vi.mock("@/lib/usePasteAttach", () => ({
  usePasteAttach: () => ({
    attachments: [], documentIds: [], busy: false, onPaste: vi.fn(), onDrop: vi.fn(),
    remove: vi.fn(), clear: vi.fn(), add: vi.fn(), full: false,
  }),
}));

import { LiveActivityNode } from "./LiveActivity";

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = 1;
  send = vi.fn();
  close = vi.fn();
}

beforeEach(() => {
  vi.clearAllMocks();
  mintLiveTicket.mockResolvedValue({ ticket: "t", expires_in: 60 });
  globalThis.WebSocket = FakeSocket as never;
  listAgentRunSteps.mockResolvedValue([]);
});

const lane = (status: string) => ({ key: "a", label: "agent", avatar: null, agent_kind: null, status });

describe("LiveActivityNode history", () => {
  it("replays what already happened instead of starting blank", async () => {
    // The socket only carries what happens while someone is watching. Without
    // this, an order that ran an hour ago showed an empty panel forever.
    getWorkOrderMap.mockResolvedValue({
      lanes: [lane("done")],
      events: [{ id: "e1", lane: "a", kind: "started", at: "", title: "", detail: null, target_lane: null, run_id: "run-1" }],
    });
    listAgentRunSteps.mockResolvedValue([
      { id: "s1", run_id: "run-1", seq: 0, kind: "tool_call", name: "search_knowledge", content: {}, tokens: null, created_at: "" },
    ]);

    render(<LiveActivityNode workOrderId="wo-1" />);

    expect(await screen.findByText("search_knowledge")).toBeTruthy();
    expect(await screen.findByText(/recorded history/)).toBeTruthy();
  });

  it("says nothing is running rather than pretending to wait", async () => {
    // "Waiting for an agent to do something…" on an order that finished hours ago
    // is how this panel used to look permanently stuck.
    getWorkOrderMap.mockResolvedValue({ lanes: [lane("done")], events: [] });

    render(<LiveActivityNode workOrderId="wo-1" />);

    expect(await screen.findByText(/Nothing is running/)).toBeTruthy();
  });

  it("still waits when an agent really is working", async () => {
    getWorkOrderMap.mockResolvedValue({ lanes: [lane("running")], events: [] });

    render(<LiveActivityNode workOrderId="wo-1" />);

    expect(await screen.findByText(/Waiting for an agent/)).toBeTruthy();
  });

  it("connects even if the history load fails", async () => {
    // History is a nicety; the live feed is the feature.
    getWorkOrderMap.mockRejectedValue(new Error("boom"));

    render(<LiveActivityNode workOrderId="wo-1" />);

    await waitFor(() => expect(mintLiveTicket).toHaveBeenCalled());
  });

  it("does not replay an order's whole life", async () => {
    getWorkOrderMap.mockResolvedValue({
      lanes: [lane("done")],
      events: Array.from({ length: 9 }, (_, i) => ({
        id: `e${i}`, lane: "a", kind: "started", at: "", title: "", detail: null, target_lane: null, run_id: `run-${i}`,
      })),
    });

    render(<LiveActivityNode workOrderId="wo-1" />);

    await waitFor(() => expect(listAgentRunSteps).toHaveBeenCalled());
    expect(listAgentRunSteps.mock.calls.length).toBeLessThanOrEqual(3);
  });
});
