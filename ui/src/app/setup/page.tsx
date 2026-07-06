"use client";

import { CheckCircle2, KeyRound, Loader2, Rocket } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useOrg } from "@/context/OrgContext";
import { getApiErrorMessage, getApiErrorStatus } from "@/lib/api/errors";
import { createOrg } from "@/lib/api/orgs";
import { claimSetup, fetchSetupStatus } from "@/lib/api/setup";

type Step = "checking" | "token" | "org" | "done";

export default function SetupPage() {
  const router = useRouter();
  const { orgs, isSiteAdmin, isLoading: orgLoading, refresh, setCurrentOrgId } = useOrg();

  const [step, setStep] = useState<Step>("checking");
  const [token, setToken] = useState("");
  const [orgName, setOrgName] = useState("");
  const [orgDescription, setOrgDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const decidedRef = useRef(false);

  // Decide the entry step once org context is loaded: fresh instance → token
  // entry; already-initialized → org creation for orgless site admins, home
  // for everyone else.
  useEffect(() => {
    if (orgLoading || decidedRef.current) return;
    decidedRef.current = true;
    void (async () => {
      try {
        const status = await fetchSetupStatus();
        if (status.needs_setup) {
          setStep("token");
        } else if (isSiteAdmin && orgs.length === 0) {
          setStep("org");
        } else {
          router.replace("/documents");
        }
      } catch (e: unknown) {
        setError(getApiErrorMessage(e, "Could not reach the API. Is the backend running?"));
        setStep("token");
      }
    })();
  }, [orgLoading, isSiteAdmin, orgs.length, router]);

  const handleClaim = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed || isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await claimSetup(trimmed);
      // Claim succeeded — advance regardless of what happens next. A failed
      // context refresh must not repaint this as a claim error (the token is
      // already consumed; retrying would 409 and mislead the operator).
      setStep("org");
      await refresh().catch(() => undefined);
      return;
    } catch (err: unknown) {
      const status = getApiErrorStatus(err);
      if (status === 403) {
        setError("Invalid or already-used setup token. Check the API server logs for the current one.");
      } else if (status === 409) {
        setError("This instance is already set up. Ask an existing site admin for access.");
      } else {
        setError(getApiErrorMessage(err, "Claim failed"));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCreateOrg = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = orgName.trim();
    if (!name || isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const org = await createOrg({ name, description: orgDescription.trim() || null });
      // Same success/failure separation as the claim step: the org exists
      // now, so a refresh hiccup must not read as "create failed" (a retry
      // would create a duplicate org).
      setStep("done");
      setCurrentOrgId(org.id);
      await refresh().catch(() => undefined);
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, "Could not create the organization"));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-6 pt-6">
        <div>
          <h1 className="text-2xl font-semibold">Red Arch Knowledge Manager setup</h1>
          <p className="text-sm text-muted-foreground">
            First-run configuration for this installation.
          </p>
        </div>

        {step === "checking" ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Checking setup status…
          </div>
        ) : null}

        {step === "token" ? (
          <form onSubmit={handleClaim} className="space-y-4">
            <div className="flex items-start gap-3">
              <KeyRound className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" />
              <div className="space-y-1">
                <h2 className="text-base font-semibold">Claim global admin</h2>
                <p className="text-sm text-muted-foreground">
                  A one-time setup token was printed to the API server logs at startup.
                  Paste it here to make <span className="font-medium">your account</span> the
                  global administrator.
                </p>
              </div>
            </div>
            <Input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Setup token from the server logs"
              autoFocus
              disabled={isSubmitting}
              aria-label="Setup token"
            />
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <Button type="submit" disabled={isSubmitting || !token.trim()} className="w-full">
              {isSubmitting ? "Claiming…" : "Claim admin access"}
            </Button>
          </form>
        ) : null}

        {step === "org" ? (
          <form onSubmit={handleCreateOrg} className="space-y-4">
            <div className="flex items-start gap-3">
              <Rocket className="mt-1 h-5 w-5 shrink-0 text-muted-foreground" />
              <div className="space-y-1">
                <h2 className="text-base font-semibold">Create your first organization</h2>
                <p className="text-sm text-muted-foreground">
                  Documents, folders, and members all live inside an organization.
                </p>
              </div>
            </div>
            <Input
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              placeholder="Organization name"
              autoFocus
              disabled={isSubmitting}
              aria-label="Organization name"
            />
            <Textarea
              value={orgDescription}
              onChange={(e) => setOrgDescription(e.target.value)}
              placeholder="Description (optional)"
              disabled={isSubmitting}
              aria-label="Organization description"
            />
            {error ? <p className="text-sm text-destructive">{error}</p> : null}
            <Button type="submit" disabled={isSubmitting || !orgName.trim()} className="w-full">
              {isSubmitting ? "Creating…" : "Create organization"}
            </Button>
          </form>
        ) : null}

        {step === "done" ? (
          <div className="space-y-4">
            <div className="flex items-start gap-3">
              <CheckCircle2 className="mt-1 h-5 w-5 shrink-0 text-green-600" />
              <div className="space-y-1">
                <h2 className="text-base font-semibold">You&apos;re all set</h2>
                <p className="text-sm text-muted-foreground">
                  You are the global administrator and your organization is ready.
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <Button className="flex-1" onClick={() => router.push("/site-admin")}>
                Open the admin console
              </Button>
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => router.push("/documents")}
              >
                Go to documents
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
