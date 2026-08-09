import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OrgSwitcher } from "./OrgSwitcher";

const push = vi.fn();
const setCurrentOrgId = vi.fn();
const orgState = {
  orgs: [
    { id: "org-a", name: "Alpha", is_admin: true },
    { id: "org-b", name: "Beta", is_admin: true },
  ],
  currentOrg: { id: "org-a", name: "Alpha", is_admin: true },
  setCurrentOrgId,
  isSiteAdmin: false,
  isLoading: false,
  error: null,
  refresh: vi.fn(),
};

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/context/OrgContext", () => ({
  useOrg: () => orgState,
}));

describe("OrgSwitcher", () => {
  it("navigates to home when a different org is selected", () => {
    // Whatever page is open belongs to the OLD org — a record detail, a course
    // player, a filtered board. Every one of those renders as an error (404 or
    // empty) under the new org's scoping, which reads as a broken app rather
    // than a completed switch. Home is the only page valid in every org.
    render(<OrgSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "Alpha" }));
    fireEvent.click(screen.getByRole("button", { name: "Beta" }));

    expect(setCurrentOrgId).toHaveBeenCalledWith("org-b");
    expect(push).toHaveBeenCalledWith("/home");
  });

  it("does not navigate when re-selecting the current org", () => {
    push.mockClear();
    setCurrentOrgId.mockClear();
    render(<OrgSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "Alpha" }));
    // The open menu marks the current org; clicking it again is a no-op switch.
    fireEvent.click(screen.getAllByRole("button", { name: "Alpha" })[1]!);

    expect(push).not.toHaveBeenCalled();
  });
});
