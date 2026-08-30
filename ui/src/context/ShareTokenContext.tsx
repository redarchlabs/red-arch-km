"use client";

import { createContext, useContext, type ReactNode } from "react";

/**
 * The share token of the anonymous page currently being rendered, if any.
 *
 * A view's configuration is written once and rendered in two places: signed in,
 * where the session and `X-Org-ID` identify the org, and behind a share link,
 * where the token in the URL is the only credential. Anything in that config
 * that points at an org-scoped resource therefore has to be resolved
 * differently in each context.
 *
 * Making that the renderer's problem rather than the config's is deliberate. The
 * alternative — a second `public_url` beside every `url` — would mean the author
 * knows in advance how the view will be viewed, and would go stale the moment
 * sharing is turned on for a view that was built private.
 *
 * Empty outside a shared page, which is the signed-in case.
 */
const ShareTokenContext = createContext<string | null>(null);

export function ShareTokenProvider({ token, children }: { token: string | null; children: ReactNode }) {
  return <ShareTokenContext.Provider value={token}>{children}</ShareTokenContext.Provider>;
}

export function useShareToken(): string | null {
  return useContext(ShareTokenContext);
}
