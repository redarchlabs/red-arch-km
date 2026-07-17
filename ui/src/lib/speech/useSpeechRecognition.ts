import { useCallback, useEffect, useRef, useState } from "react";

// The Web Speech API (SpeechRecognition) is shipped by Chromium/Safari but is
// NOT part of the standard TypeScript DOM lib, so we declare the slice we use.
// It is behind a vendor prefix (`webkitSpeechRecognition`) everywhere except
// very recent Chrome, hence the union lookup in `getSpeechRecognitionCtor`.

interface SpeechRecognitionAlternative {
  readonly transcript: string;
}
interface SpeechRecognitionResult {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: SpeechRecognitionAlternative;
}
interface SpeechRecognitionResultList {
  readonly length: number;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEvent extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEvent extends Event {
  readonly error: string;
  readonly message: string;
}
interface SpeechRecognitionInstance {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionInstance;

/** Resolve the (vendor-prefixed) SpeechRecognition constructor, or null when the
 * browser has no support. Kept as a function so tests can stub `window` first. */
function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface UseSpeechRecognitionOptions {
  /** BCP-47 language tag for recognition (default "en-US"). */
  lang?: string;
  /** Fired once per finalized utterance (already trimmed, never empty). */
  onResult: (text: string) => void;
  /** Fired on a recognition error with a human-readable message. */
  onError?: (message: string) => void;
}

export interface SpeechRecognitionControls {
  /** Whether this browser exposes the Web Speech API at all. */
  supported: boolean;
  /** True while the mic is open. */
  listening: boolean;
  /** Live, not-yet-final transcript for on-screen feedback ("" when idle). */
  interim: string;
  /** Open the mic. `continuous` keeps it open across utterances (always-on);
   * false captures a single utterance then ends (push-to-talk). */
  start: (continuous: boolean) => void;
  /** Close the mic. Any in-flight final result still fires first. */
  stop: () => void;
}

/** Map a raw SpeechRecognition error code to a friendly, actionable message. */
function describeError(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access was blocked. Allow it in your browser to talk to the robot.";
    case "no-speech":
      return "Didn't catch that — try speaking again.";
    case "audio-capture":
      return "No microphone was found.";
    case "network":
      return "Speech recognition network error.";
    default:
      return `Speech recognition error: ${code}`;
  }
}

/**
 * Thin React wrapper over the browser Web Speech API for speech-to-text.
 *
 * Two usage modes, chosen per `start(continuous)` call:
 *  - `start(true)`  — always-on: the mic stays open and every finalized
 *    utterance fires `onResult`. Chrome still ends recognition after a silence
 *    gap even with `continuous:true`, so we transparently restart it until the
 *    caller invokes `stop()`.
 *  - `start(false)` — push-to-talk: one utterance, then recognition ends on its
 *    own (or when the caller releases and calls `stop()`).
 *
 * Callbacks are read through refs so a parent re-render never tears down the
 * live recognition session (the instance is created once and reused).
 */
export function useSpeechRecognition(options: UseSpeechRecognitionOptions): SpeechRecognitionControls {
  const { lang = "en-US" } = options;

  const [supported] = useState(() => getSpeechRecognitionCtor() != null);
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  // Whether the caller currently wants the mic open. Drives the auto-restart in
  // continuous mode and guards against restarting after an explicit stop.
  const activeRef = useRef(false);
  // Whether the active session was opened in always-on (continuous) mode.
  const continuousRef = useRef(false);

  // Latest callbacks, so the recognition event handlers (bound once) always see
  // current values without re-creating the recognition instance.
  const onResultRef = useRef(options.onResult);
  const onErrorRef = useRef(options.onError);
  onResultRef.current = options.onResult;
  onErrorRef.current = options.onError;

  // Lazily build the single recognition instance and bind its handlers.
  const ensureRecognition = useCallback((): SpeechRecognitionInstance | null => {
    if (recognitionRef.current) return recognitionRef.current;
    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) return null;
    const rec = new Ctor();
    rec.maxAlternatives = 1;
    rec.onresult = (event) => {
      let interimText = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) {
          const finalText = transcript.trim();
          if (finalText) onResultRef.current(finalText);
        } else {
          interimText += transcript;
        }
      }
      setInterim(interimText);
    };
    rec.onerror = (event) => {
      // A fatal permission error must not be retried in a hot loop.
      if (event.error === "not-allowed" || event.error === "service-not-allowed" || event.error === "audio-capture") {
        activeRef.current = false;
      }
      // "no-speech"/"aborted" are benign in continuous mode — onend restarts us.
      if (event.error !== "no-speech" && event.error !== "aborted") {
        onErrorRef.current?.(describeError(event.error));
      }
    };
    rec.onend = () => {
      setInterim("");
      // In always-on mode the engine stops itself after a pause; reopen the mic
      // until the caller explicitly stops. Otherwise the session is done.
      if (activeRef.current && continuousRef.current) {
        try {
          rec.start();
          return;
        } catch {
          activeRef.current = false;
        }
      }
      setListening(false);
    };
    recognitionRef.current = rec;
    return rec;
  }, []);

  const start = useCallback(
    (continuous: boolean) => {
      const rec = ensureRecognition();
      if (!rec || activeRef.current) return;
      activeRef.current = true;
      continuousRef.current = continuous;
      rec.lang = lang;
      rec.continuous = continuous;
      rec.interimResults = true;
      try {
        rec.start();
        setListening(true);
      } catch {
        // start() throws if a prior session hasn't fully ended; treat as no-op.
        activeRef.current = false;
      }
    },
    [ensureRecognition, lang],
  );

  const stop = useCallback(() => {
    activeRef.current = false;
    continuousRef.current = false;
    setInterim("");
    const rec = recognitionRef.current;
    if (rec) {
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    }
  }, []);

  // Tear down on unmount so a backgrounded mic never outlives the view.
  useEffect(() => {
    return () => {
      activeRef.current = false;
      continuousRef.current = false;
      const rec = recognitionRef.current;
      if (rec) {
        rec.onresult = null;
        rec.onerror = null;
        rec.onend = null;
        try {
          rec.abort();
        } catch {
          /* ignore */
        }
      }
    };
  }, []);

  return { supported, listening, interim, start, stop };
}
