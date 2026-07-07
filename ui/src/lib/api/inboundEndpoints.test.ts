import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the axios client so these tests assert path/method wiring without a
// backend or Clerk token flow.
const get = vi.fn();
const post = vi.fn();
const del = vi.fn();

vi.mock("./client", () => ({
  default: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
    delete: (...a: unknown[]) => del(...a),
  },
}));

import {
  createInboundEndpoint,
  deleteInboundEndpoint,
  listInboundEndpoints,
  type InboundEndpointCreated,
} from "./inboundEndpoints";

beforeEach(() => {
  [get, post, del].forEach((m) => m.mockReset());
});

const CREATED: InboundEndpointCreated = {
  id: "e1",
  name: "Stripe webhook",
  workflow_id: "w1",
  enabled: true,
  token: "tok_secret",
  url: "https://app.example.com/api/inbound/tok_secret",
};

describe("inbound endpoints API client", () => {
  it("lists endpoints and unwraps data", async () => {
    get.mockResolvedValue({ data: [{ id: "e1", name: "n", workflow_id: "w1", enabled: true }] });
    await expect(listInboundEndpoints()).resolves.toEqual([
      { id: "e1", name: "n", workflow_id: "w1", enabled: true },
    ]);
    expect(get).toHaveBeenCalledWith("/workflows/inbound-endpoints");
  });

  it("creates an endpoint via POST and returns the one-time token + url", async () => {
    post.mockResolvedValue({ data: CREATED });
    const body = { name: "Stripe webhook", workflow_id: "w1" };
    await expect(createInboundEndpoint(body)).resolves.toEqual(CREATED);
    expect(post).toHaveBeenCalledWith("/workflows/inbound-endpoints", body);
  });

  it("deletes an endpoint via DELETE under its id", async () => {
    del.mockResolvedValue({ data: undefined });
    await deleteInboundEndpoint("e1");
    expect(del).toHaveBeenCalledWith("/workflows/inbound-endpoints/e1");
  });

  it("propagates client errors to the caller", async () => {
    get.mockRejectedValue(new Error("Request failed"));
    await expect(listInboundEndpoints()).rejects.toThrow("Request failed");
  });
});
