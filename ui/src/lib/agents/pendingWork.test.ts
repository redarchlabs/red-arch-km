import { describe, expect, it, vi } from "vitest";

import { onPendingWorkChanged, pendingWorkChanged } from "./pendingWork";

describe("pendingWork signal", () => {
  it("tells every subscriber that something was settled", () => {
    const bell = vi.fn();
    const panel = vi.fn();
    const offBell = onPendingWorkChanged(bell);
    const offPanel = onPendingWorkChanged(panel);

    pendingWorkChanged();

    expect(bell).toHaveBeenCalledTimes(1);
    expect(panel).toHaveBeenCalledTimes(1);
    offBell();
    offPanel();
  });

  it("stops calling a subscriber that unsubscribed", () => {
    const listener = vi.fn();
    const off = onPendingWorkChanged(listener);
    off();

    pendingWorkChanged();

    expect(listener).not.toHaveBeenCalled();
  });

  it("keeps going when one subscriber throws", () => {
    // An unmounting component that blows up must not silently freeze the badge
    // of every other surface listening.
    const survivor = vi.fn();
    const offBad = onPendingWorkChanged(() => {
      throw new Error("boom");
    });
    const offGood = onPendingWorkChanged(survivor);

    expect(() => pendingWorkChanged()).not.toThrow();
    expect(survivor).toHaveBeenCalledTimes(1);
    offBad();
    offGood();
  });
});
