/** Pure helpers behind the `live_value` element: pick a value out of a polled JSON
 * body, and turn it into the text the readout shows. Kept out of the renderer so both
 * steps are testable without mounting a form. */

/** Walk a dot path (`head.pitch`) into a parsed JSON body. A blank pointer means the
 * whole body; a path that runs off the end of the data yields `undefined`. */
export function readJsonPointer(data: unknown, pointer?: string | null): unknown {
  if (!pointer) return data;
  let cur: unknown = data;
  for (const part of pointer.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

/** The raw text for a polled value: `—` when there is nothing there, JSON for an
 * object, and the plain string form of anything else. */
export function formatLiveValue(picked: unknown): string {
  if (picked == null) return "—";
  return typeof picked === "object" ? JSON.stringify(picked) : String(picked);
}

/** Apply the element's optional display map. A polled status flag should read
 * "Thinking…" rather than "true" — what the state means, not how it is encoded.
 * Values the map doesn't name (including the `unreachable` placeholder) pass
 * through unchanged, so a partial map only relabels the cases it lists. */
export function displayLiveValue(text: string, valueMap?: Record<string, string> | null): string {
  return valueMap?.[text] ?? text;
}
