"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BYPASS_USER, setBypassSecret } from "@/lib/auth/bypass";

/**
 * Offline sign-in: the operator types the API's shared secret, which is held in
 * `sessionStorage` for this tab only.
 *
 * This form is the reason the secret is not an env var. A `NEXT_PUBLIC_*` secret would be
 * inlined into the bundle every browser downloads — including the visitor-facing kiosk —
 * so it is typed in here instead and never reaches a device that doesn't need it. See
 * lib/auth/bypass.ts.
 */
export function BypassSignIn() {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = secret.trim();
    if (!trimmed) {
      setError("Enter the offline access key.");
      return;
    }
    setBusy(true);
    setError(null);

    if (!setBypassSecret(trimmed)) {
      setError("This browser is blocking session storage, so the key cannot be held.");
      setBusy(false);
      return;
    }

    // Verify before navigating. Storing a wrong key and redirecting would land the
    // operator on a dashboard that 401s every request and bounces straight back here with
    // nothing explaining why — the worst thing to debug with an audience waiting.
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
      const response = await fetch(`${baseUrl}/users/me`, {
        headers: { "X-Test-User": BYPASS_USER, "X-Test-Secret": trimmed },
      });
      if (!response.ok) {
        setError(
          response.status === 401
            ? "That key was rejected. Check API_E2E_TEST_SECRET on the API."
            : `The API answered ${response.status}. Is it running?`,
        );
        setBusy(false);
        return;
      }
    } catch {
      setError("Could not reach the API. Check that it is running and on this network.");
      setBusy(false);
      return;
    }

    // Full load, not router.push: useAuth() reads the secret in a mount effect, so the
    // tree has to be built afresh for the app to see the new session.
    window.location.href = "/documents";
  }

  return (
    <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold tracking-tight">Offline sign-in</h2>
        <p className="text-sm text-muted-foreground">
          This build runs without internet access, so the usual sign-in is unavailable.
          Enter the offline access key to continue as <code>{BYPASS_USER}</code>.
        </p>
      </div>

      <div className="space-y-2">
        <label htmlFor="bypass-secret" className="text-sm font-medium">
          Offline access key
        </label>
        <Input
          id="bypass-secret"
          type="password"
          autoComplete="off"
          autoFocus
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          placeholder="Shared secret"
          aria-describedby={error ? "bypass-error" : undefined}
        />
        {error ? (
          <p id="bypass-error" role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
      </div>

      <Button type="submit" disabled={busy} className="w-full">
        {busy ? "Checking…" : "Continue"}
      </Button>

      <p className="text-xs text-muted-foreground">
        The key is kept for this browser tab only and is forgotten when the tab closes.
      </p>
    </form>
  );
}
