import { describe, expect, it } from "vitest";

import { liveSocketUrl } from "./agentsLive";

describe("liveSocketUrl", () => {
  it("upgrades the api scheme to a websocket scheme", () => {
    // A ws:// URL against an https origin is blocked by the browser as mixed
    // content, so this has to follow the API's scheme rather than be hardcoded.
    const url = new URL(liveSocketUrl("tkt", { work_order_id: "wo-1" }));

    expect(["ws:", "wss:"]).toContain(url.protocol);
    expect(url.pathname.endsWith("/agents/live/ws")).toBe(true);
  });

  it("carries the ticket and the scope", () => {
    const url = new URL(liveSocketUrl("tkt", { work_order_id: "wo-1" }));

    expect(url.searchParams.get("ticket")).toBe("tkt");
    expect(url.searchParams.get("work_order_id")).toBe("wo-1");
  });

  it("omits an empty scope rather than sending a blank one", () => {
    // The server treats a non-UUID scope as a refusal; sending "" would turn a
    // run-scoped socket into a rejected connection instead of a work-order one.
    const url = new URL(liveSocketUrl("tkt", { work_order_id: "wo-1", run_id: "" }));

    expect(url.searchParams.has("run_id")).toBe(false);
  });
});
