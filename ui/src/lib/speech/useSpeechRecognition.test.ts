import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSpeechRecognition } from "./useSpeechRecognition";

/** A controllable stand-in for the browser SpeechRecognition, so tests can drive
 * results/errors/end deterministically. Instances register themselves so the
 * test can reach the live one the hook created. */
class FakeRecognition {
  static instances: FakeRecognition[] = [];
  lang = "";
  continuous = false;
  interimResults = false;
  maxAlternatives = 1;
  started = 0;
  stopped = 0;
  aborted = 0;
  onresult: ((e: unknown) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onend: (() => void) | null = null;
  onstart: (() => void) | null = null;

  constructor() {
    FakeRecognition.instances.push(this);
  }
  start() {
    this.started += 1;
  }
  stop() {
    this.stopped += 1;
  }
  abort() {
    this.aborted += 1;
  }

  // Test helpers -----------------------------------------------------------
  emitFinal(transcript: string) {
    this.onresult?.({
      resultIndex: 0,
      results: { length: 1, 0: { isFinal: true, length: 1, 0: { transcript } } },
    });
  }
  emitInterim(transcript: string) {
    this.onresult?.({
      resultIndex: 0,
      results: { length: 1, 0: { isFinal: false, length: 1, 0: { transcript } } },
    });
  }
  emitError(error: string) {
    this.onerror?.({ error, message: error });
  }
  emitEnd() {
    this.onend?.();
  }
}

describe("useSpeechRecognition", () => {
  beforeEach(() => {
    FakeRecognition.instances = [];
    (window as unknown as Record<string, unknown>).SpeechRecognition = FakeRecognition;
    delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
  });
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).SpeechRecognition;
  });

  it("reports supported when the API is present", () => {
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn() }));
    expect(result.current.supported).toBe(true);
  });

  it("reports unsupported when the API is absent", () => {
    delete (window as unknown as Record<string, unknown>).SpeechRecognition;
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn() }));
    expect(result.current.supported).toBe(false);
    act(() => result.current.start(false));
    expect(result.current.listening).toBe(false);
  });

  it("delivers a trimmed final transcript via onResult", () => {
    const onResult = vi.fn();
    const { result } = renderHook(() => useSpeechRecognition({ onResult }));
    act(() => result.current.start(false));
    const rec = FakeRecognition.instances[0];
    expect(rec.started).toBe(1);
    expect(result.current.listening).toBe(true);
    act(() => rec.emitFinal("  hello robot  "));
    expect(onResult).toHaveBeenCalledWith("hello robot");
  });

  it("ignores an empty/whitespace final result", () => {
    const onResult = vi.fn();
    const { result } = renderHook(() => useSpeechRecognition({ onResult }));
    act(() => result.current.start(false));
    act(() => FakeRecognition.instances[0].emitFinal("   "));
    expect(onResult).not.toHaveBeenCalled();
  });

  it("exposes the live interim transcript and clears it on end", () => {
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn() }));
    act(() => result.current.start(false));
    act(() => FakeRecognition.instances[0].emitInterim("partial words"));
    expect(result.current.interim).toBe("partial words");
    act(() => FakeRecognition.instances[0].emitEnd());
    expect(result.current.interim).toBe("");
  });

  it("auto-restarts in continuous (always-on) mode when the engine ends", () => {
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn() }));
    act(() => result.current.start(true));
    const rec = FakeRecognition.instances[0];
    expect(rec.continuous).toBe(true);
    expect(rec.started).toBe(1);
    // Chrome ends recognition after a silence gap — hook should reopen the mic.
    act(() => rec.emitEnd());
    expect(rec.started).toBe(2);
    expect(result.current.listening).toBe(true);
  });

  it("does NOT restart after stop() in continuous mode", () => {
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn() }));
    act(() => result.current.start(true));
    const rec = FakeRecognition.instances[0];
    act(() => result.current.stop());
    expect(rec.stopped).toBe(1);
    act(() => rec.emitEnd());
    expect(rec.started).toBe(1); // no restart
    expect(result.current.listening).toBe(false);
  });

  it("does not restart in push-to-talk (non-continuous) mode", () => {
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn() }));
    act(() => result.current.start(false));
    const rec = FakeRecognition.instances[0];
    act(() => rec.emitEnd());
    expect(rec.started).toBe(1);
    expect(result.current.listening).toBe(false);
  });

  it("surfaces a permission error and stops retrying", () => {
    const onError = vi.fn();
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn(), onError }));
    act(() => result.current.start(true));
    const rec = FakeRecognition.instances[0];
    act(() => rec.emitError("not-allowed"));
    expect(onError).toHaveBeenCalledTimes(1);
    // Even though it was continuous, a fatal error must break the restart loop.
    act(() => rec.emitEnd());
    expect(rec.started).toBe(1);
  });

  it("swallows benign no-speech errors without notifying", () => {
    const onError = vi.fn();
    const { result } = renderHook(() => useSpeechRecognition({ onResult: vi.fn(), onError }));
    act(() => result.current.start(true));
    act(() => FakeRecognition.instances[0].emitError("no-speech"));
    expect(onError).not.toHaveBeenCalled();
  });
});
