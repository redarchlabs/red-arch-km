/** Only suppress recent playback leaking into always-on capture. */
export function isSelfEcho(
  heard: string,
  spoken: string,
  mode: "push_to_talk" | "always_on",
  playbackUntil: number,
  now = Date.now(),
): boolean {
  if (mode !== "always_on" || now > playbackUntil) return false;
  const normalize = (text: string) => text.toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, "").replace(/\s+/g, " ").trim();
  const h = normalize(heard);
  const s = normalize(spoken);
  return h.length >= 8 && s.length >= 8 && (s.includes(h) || h.includes(s));
}
