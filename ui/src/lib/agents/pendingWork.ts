/**
 * A one-line signal for "something the bell counts has just been settled".
 *
 * The header bell polls every 20s, which is fine for work that *arrives* on its
 * own — but not for work you just cleared yourself. Resolving an escalation and
 * then watching the header still say "4 waiting on you" reads as a failed click,
 * and people click it again. The mutations announce here, and anything showing a
 * pending-work count re-reads immediately.
 *
 * A module-level listener set rather than a window event: the subscribers are
 * all in the same client bundle, it needs no DOM, and it stays inert on the
 * server during SSR.
 */

type Listener = () => void;

const listeners = new Set<Listener>();

/** Announce that an approval, question, or escalation was just settled. */
export function pendingWorkChanged(): void {
  // Copy first — a listener that unsubscribes itself must not skip the next one.
  for (const listener of [...listeners]) {
    try {
      listener();
    } catch {
      // One bad subscriber must not stop the rest from hearing about it.
    }
  }
}

/** Subscribe to settle events. Returns the unsubscribe. */
export function onPendingWorkChanged(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
