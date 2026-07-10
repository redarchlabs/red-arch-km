import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the axios client so these tests assert path/method wiring without a
// backend or Clerk token flow.
const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const del = vi.fn();

vi.mock("./client", () => ({
  default: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
    patch: (...a: unknown[]) => patch(...a),
    delete: (...a: unknown[]) => del(...a),
  },
}));

import {
  completeTask,
  createWorkflow,
  getWorkflow,
  listRunSteps,
  listRuns,
  listWorkflows,
  publishVersion,
  runWorkflow,
  saveDraft,
  testVersion,
} from "./workflows";

beforeEach(() => {
  [get, post, patch, del].forEach((m) => m.mockReset());
});

describe("workflows API client", () => {
  it("lists workflows and unwraps data", async () => {
    get.mockResolvedValue({ data: [{ id: "w1" }] });
    await expect(listWorkflows()).resolves.toEqual([{ id: "w1" }]);
    expect(get).toHaveBeenCalledWith("/workflows/");
  });

  it("gets a single workflow by id", async () => {
    get.mockResolvedValue({ data: { id: "w1" } });
    await getWorkflow("w1");
    expect(get).toHaveBeenCalledWith("/workflows/w1");
  });

  it("creates a workflow via POST", async () => {
    post.mockResolvedValue({ data: { id: "w1" } });
    const input = { name: "n", entity_definition_id: "e1" };
    await createWorkflow(input);
    expect(post).toHaveBeenCalledWith("/workflows/", input);
  });

  it("saves a draft version wrapping the definition", async () => {
    post.mockResolvedValue({ data: { id: "v1" } });
    const definition = { schema_version: 1, nodes: [], edges: [] };
    await saveDraft("w1", definition);
    expect(post).toHaveBeenCalledWith("/workflows/w1/versions", { definition });
  });

  it("publishes a version", async () => {
    post.mockResolvedValue({ data: { id: "v1" } });
    await publishVersion("w1", "v1");
    expect(post).toHaveBeenCalledWith("/workflows/w1/versions/v1/publish", {});
  });

  it("runs a dry-run test against a version", async () => {
    post.mockResolvedValue({ data: { conditions_matched: true } });
    const input = { operation: "update", after: { status: "closed" } };
    await testVersion("w1", "v1", input);
    expect(post).toHaveBeenCalledWith("/workflows/w1/versions/v1/test", input);
  });

  it("triggers a real run", async () => {
    post.mockResolvedValue({ data: { run_id: "r1" } });
    await runWorkflow("w1", { operation: "update" });
    expect(post).toHaveBeenCalledWith("/workflows/w1/run", { operation: "update" }, {
      timeout: undefined,
    });
  });

  it("passes a per-call timeout override to the run request", async () => {
    post.mockResolvedValue({ data: { run_id: "r1" } });
    await runWorkflow("w1", { inputs: { text: "hi" } }, 120000);
    expect(post).toHaveBeenCalledWith(
      "/workflows/w1/run",
      { inputs: { text: "hi" } },
      { timeout: 120000 },
    );
  });

  it("lists runs with the limit as a query param", async () => {
    get.mockResolvedValue({ data: [] });
    await listRuns("w1", 25);
    expect(get).toHaveBeenCalledWith("/workflows/w1/runs", { params: { limit: 25 } });
  });

  it("lists run steps under the runs path", async () => {
    get.mockResolvedValue({ data: [] });
    await listRunSteps("r1");
    expect(get).toHaveBeenCalledWith("/workflows/runs/r1/steps");
  });

  it("completes a human task via POST with the decision variables", async () => {
    post.mockResolvedValue({ data: { run_id: "r1", status: "running" } });
    await completeTask("r1", { variables: { approved: true } });
    expect(post).toHaveBeenCalledWith("/workflows/runs/r1/complete-task", {
      variables: { approved: true },
    });
  });

  it("completes a task with an empty body when no decision is passed", async () => {
    post.mockResolvedValue({ data: { run_id: "r1", status: "succeeded" } });
    await completeTask("r1");
    expect(post).toHaveBeenCalledWith("/workflows/runs/r1/complete-task", {});
  });

  it("propagates client errors to the caller", async () => {
    get.mockRejectedValue(new Error("Request failed"));
    await expect(listWorkflows()).rejects.toThrow("Request failed");
  });
});
