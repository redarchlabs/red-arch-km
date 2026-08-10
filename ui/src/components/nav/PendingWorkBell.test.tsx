import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listApprovals = vi.fn();
const listQuestions = vi.fn();
const listNotifications = vi.fn();

vi.mock("@/lib/api/agents", () => ({
  listApprovals: (...a: unknown[]) => listApprovals(...a),
  listQuestions: (...a: unknown[]) => listQuestions(...a),
  listNotifications: (...a: unknown[]) => listNotifications(...a),
}));

vi.mock("next/link", () => ({
  default: ({ children, ...rest }: { children: React.ReactNode }) => <a {...rest}>{children}</a>,
}));

import { pendingWorkChanged } from "@/lib/agents/pendingWork";

import { PendingWorkBell } from "./PendingWorkBell";

const escalation = (over: Record<string, unknown> = {}) => ({
  id: "n-1",
  kind: "escalation",
  run_id: null,
  work_order_id: "wo-1",
  recipient_role: "org_admin",
  title: "“SEO check” is blocked",
  body: null,
  status: "unread",
  created_at: "2026-08-09T20:15:00Z",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  listApprovals.mockResolvedValue([]);
  listQuestions.mockResolvedValue([]);
  listNotifications.mockResolvedValue([]);
});

describe("PendingWorkBell", () => {
  it("counts an escalation — work that has already stopped", async () => {
    listNotifications.mockResolvedValue([escalation()]);

    render(<PendingWorkBell />);

    expect(await screen.findByLabelText("1 waiting for you")).toBeTruthy();
  });

  it("ignores notifications that are only a record of something happening", async () => {
    // A question notification duplicates the question itself, which is already
    // counted; counting both would double every ask and make the badge noise.
    listNotifications.mockResolvedValue([escalation({ kind: "question" })]);

    render(<PendingWorkBell />);

    await waitFor(() => expect(listNotifications).toHaveBeenCalled());
    expect(screen.getByLabelText("Nothing waiting for you")).toBeTruthy();
  });

  it("drops the count as soon as the escalation is resolved elsewhere", async () => {
    // Resolving on the approvals page used to leave the header saying "1 waiting
    // on you" for up to a poll interval, which reads as a click that did nothing.
    listNotifications.mockResolvedValue([escalation()]);
    render(<PendingWorkBell />);
    expect(await screen.findByLabelText("1 waiting for you")).toBeTruthy();

    listNotifications.mockResolvedValue([]);
    pendingWorkChanged();

    expect(await screen.findByLabelText("Nothing waiting for you")).toBeTruthy();
  });

  it("adds escalations to approvals and questions", async () => {
    listApprovals.mockResolvedValue([{ id: "ap-1" }]);
    listQuestions.mockResolvedValue([{ id: "q-1" }]);
    listNotifications.mockResolvedValue([escalation()]);

    render(<PendingWorkBell />);

    expect(await screen.findByLabelText("3 waiting for you")).toBeTruthy();
  });
});
