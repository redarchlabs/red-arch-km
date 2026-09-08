import { describe, expect, it } from "vitest";
import { isSelfEcho } from "./echo";

describe("playback echo policy", () => {
  it("lets deliberate push-to-talk repeat the answer", () => {
    expect(isSelfEcho("repeat these words", "repeat these words", "push_to_talk", 2000, 1000)).toBe(false);
  });
  it("expires even if the last answer has not changed", () => {
    expect(isSelfEcho("repeat these words", "repeat these words", "always_on", 2000, 2001)).toBe(false);
  });
  it("compares Unicode words during playback", () => {
    expect(isSelfEcho("Привет всем сегодня", "Привет всем сегодня!", "always_on", 2000, 1000)).toBe(true);
  });
  it("keeps unrelated speech", () => {
    expect(isSelfEcho("another question", "repeat these words", "always_on", 2000, 1000)).toBe(false);
  });
});
