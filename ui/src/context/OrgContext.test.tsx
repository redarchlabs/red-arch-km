import { renderHook, waitFor } from "@testing-library/react";
import { act } from "react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrgProvider, useOrg } from "./OrgContext";

const fetchMe = vi.fn();
const authState = { isAuthenticated: true, isInitializing: false };

vi.mock("@/lib/api/users", () => ({
  fetchMe: () => fetchMe(),
}));

vi.mock("./AuthContext", () => ({
  useAuth: () => authState,
}));

function wrapper({ children }: { children: ReactNode }) {
  return <OrgProvider>{children}</OrgProvider>;
}

function me(orgs: Array<{ id: string; name: string }>, isSiteAdmin = false) {
  return {
    id: "u1",
    username: "jeremy",
    email: "jeremy@example.com",
    is_site_admin: isSiteAdmin,
    orgs: orgs.map((o) => ({ ...o, is_admin: isSiteAdmin })),
  };
}

beforeEach(() => {
  fetchMe.mockReset();
  authState.isAuthenticated = true;
  authState.isInitializing = false;
  localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("OrgProvider", () => {
  it("loads orgs and pins the resolved one to storage", async () => {
    fetchMe.mockResolvedValue(me([{ id: "o1", name: "Robots" }], true));

    const { result } = renderHook(() => useOrg(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.orgs).toHaveLength(1);
    expect(result.current.isSiteAdmin).toBe(true);
    expect(result.current.error).toBeNull();
    expect(localStorage.getItem("redarch:currentOrgId")).toBe("o1");
  });

  it("surfaces a failed load instead of looking like an empty org list", async () => {
    // A rejected /users/me used to be swallowed by a bare try/finally, leaving
    // the switcher reading "No organizations" forever with no way to retry.
    fetchMe.mockRejectedValue(new Error("Timed out waiting for a sign-in token"));

    const { result } = renderHook(() => useOrg(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toMatch(/timed out/i);
    expect(result.current.orgs).toEqual([]);
  });

  it("recovers when refresh() is retried after a failure", async () => {
    fetchMe.mockRejectedValueOnce(new Error("boom"));
    const { result } = renderHook(() => useOrg(), { wrapper });
    await waitFor(() => expect(result.current.error).not.toBeNull());

    fetchMe.mockResolvedValue(me([{ id: "o1", name: "Robots" }]));
    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.orgs).toHaveLength(1);
  });

  it("keeps the known orgs when a later refresh fails", async () => {
    fetchMe.mockResolvedValue(me([{ id: "o1", name: "Robots" }]));
    const { result } = renderHook(() => useOrg(), { wrapper });
    await waitFor(() => expect(result.current.orgs).toHaveLength(1));

    fetchMe.mockRejectedValue(new Error("network down"));
    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.error).toMatch(/network down/i);
    expect(result.current.orgs).toHaveLength(1);
    expect(result.current.currentOrgId).toBe("o1");
  });

  it("clears org state on sign-out", async () => {
    fetchMe.mockResolvedValue(me([{ id: "o1", name: "Robots" }], true));
    const { result, rerender } = renderHook(() => useOrg(), { wrapper });
    await waitFor(() => expect(result.current.orgs).toHaveLength(1));

    authState.isAuthenticated = false;
    rerender();

    await waitFor(() => expect(result.current.orgs).toEqual([]));
    expect(result.current.isSiteAdmin).toBe(false);
    expect(result.current.error).toBeNull();
  });
});
