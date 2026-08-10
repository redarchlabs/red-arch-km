import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the axios client so these tests assert wiring without a backend or Clerk.
const get = vi.fn();
const post = vi.fn();

vi.mock("./client", () => ({
  default: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
  },
}));

vi.mock("@/lib/auth/clerk", () => ({ getToken: async () => "tok" }));

import { onPendingWorkChanged } from "@/lib/agents/pendingWork";

import {
  answerQuestion,
  approveApproval,
  declineQuestion,
  denyApproval,
  listApprovals,
  resolveNotification,
} from "./agents";

let settled: ReturnType<typeof vi.fn>;
let unsubscribe: () => void;

beforeEach(() => {
  [get, post].forEach((m) => m.mockReset());
  post.mockResolvedValue({ data: { id: "x" } });
  get.mockResolvedValue({ data: [] });
  settled = vi.fn();
  unsubscribe?.();
  unsubscribe = onPendingWorkChanged(settled);
});

describe("settling work announces it", () => {
  // The header bell counts approvals, questions, and escalations. Every call
  // that clears one has to say so, or the badge keeps the stale number until the
  // next 20s poll and the click looks broken.
  it.each([
    ["approve", () => approveApproval("ap-1")],
    ["deny", () => denyApproval("ap-1")],
    ["resolve an escalation", () => resolveNotification("n-1")],
    ["answer a question", () => answerQuestion("q-1", "yes")],
    ["decline a question", () => declineQuestion("q-1")],
  ])("announces when you %s", async (_label, call) => {
    await call();

    expect(settled).toHaveBeenCalledTimes(1);
  });

  it("stays quiet when the settle call fails", async () => {
    // A denied or conflicting decision changes nothing — dropping the count
    // there would hide work that is still outstanding.
    post.mockRejectedValue(new Error("409"));

    await expect(resolveNotification("n-1")).rejects.toThrow();
    expect(settled).not.toHaveBeenCalled();
  });

  it("stays quiet on a plain read", async () => {
    await listApprovals();

    expect(settled).not.toHaveBeenCalled();
  });
});
