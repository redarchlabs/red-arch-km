import { afterEach, describe, expect, it, vi } from "vitest";

import { streamRunTokens, type RunTokenEvent } from "./runStream";

vi.mock("@/lib/auth/clerk", () => ({ getToken: async () => "test-token" }));

/** A Response whose body streams `chunks` (as the API would write SSE frames). */
function sseResponse(chunks: string[], ok = true): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok, status: ok ? 200 : 500, body } as unknown as Response;
}

async function collect(token = "tok"): Promise<RunTokenEvent[]> {
  const events: RunTokenEvent[] = [];
  for await (const event of streamRunTokens(token)) events.push(event);
  return events;
}

afterEach(() => vi.unstubAllGlobals());

describe("streamRunTokens", () => {
  it("yields each delta then done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: delta\ndata: {"type":"delta","text":"Operation "}\n\n',
          'event: delta\ndata: {"type":"delta","text":"Deep Horizon."}\n\n',
          'event: done\ndata: {"type":"done"}\n\n',
        ]),
      ),
    );

    expect(await collect()).toEqual([
      { type: "delta", text: "Operation " },
      { type: "delta", text: "Deep Horizon." },
      { type: "done" },
    ]);
  });

  it("reassembles a frame split across network chunks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: delta\ndata: {"type":"delta",',
          '"text":"split"}\n\nevent: done\ndata: {"type":"done"}\n\n',
        ]),
      ),
    );

    expect(await collect()).toEqual([{ type: "delta", text: "split" }, { type: "done" }]);
  });

  it("ignores keepalive comments while the run is still thinking", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          ": keepalive\n\n",
          ": keepalive\n\n",
          'event: delta\ndata: {"type":"delta","text":"finally"}\n\n',
          'event: done\ndata: {"type":"done"}\n\n',
        ]),
      ),
    );

    expect(await collect()).toEqual([{ type: "delta", text: "finally" }, { type: "done" }]);
  });

  it("skips malformed frames instead of ending the stream", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          "event: delta\ndata: not json\n\n",
          'event: delta\ndata: {"type":"delta","text":"ok"}\n\n',
          'event: done\ndata: {"type":"done"}\n\n',
        ]),
      ),
    );

    expect(await collect()).toEqual([{ type: "delta", text: "ok" }, { type: "done" }]);
  });

  it("stops at an error frame", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: error\ndata: {"detail":"stream failed"}\n\n',
          'event: delta\ndata: {"type":"delta","text":"never"}\n\n',
        ]),
      ),
    );

    const events = await collect();
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("error");
  });

  it("throws when the stream cannot be opened", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sseResponse([], false)));
    await expect(collect()).rejects.toThrow(/Answer stream failed/);
  });

  it("requests the live endpoint for the given token with auth headers", async () => {
    const fetchMock = vi.fn(async () => sseResponse(['event: done\ndata: {"type":"done"}\n\n']));
    vi.stubGlobal("fetch", fetchMock);

    await collect("abc-123");

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/workflows/runs/live/abc-123");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer test-token");
  });
});
