"use client";

import { UserProfile, useUser } from "@clerk/nextjs";
import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { fetchMe, type CurrentUser } from "@/lib/api/users";
import { getApiErrorMessage } from "@/lib/api/errors";

/** A newly-registered user with no JWT template lands with this email suffix. */
const PLACEHOLDER_EMAIL_SUFFIX = "@placeholder.invalid";

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
      <span className="w-40 shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="min-w-0 break-words text-sm">{value || "—"}</span>
    </div>
  );
}

export default function ProfilePage() {
  const { user, isLoaded } = useUser();
  const { orgs, isSiteAdmin } = useOrg();
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [meError, setMeError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setMe(await fetchMe());
      } catch (e: unknown) {
        setMeError(getApiErrorMessage(e, "Could not load your Red Arch account details"));
      }
    })();
  }, []);

  const usesPlaceholderEmail = me?.email.endsWith(PLACEHOLDER_EMAIL_SUFFIX) ?? false;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Your profile</h1>
        <p className="text-sm text-muted-foreground">
          Manage your name, email, and password in Clerk. Your Red Arch account syncs from these
          values the next time you sign in.
        </p>
      </div>

      {usesPlaceholderEmail ? (
        <Card className="border-amber-500/50 bg-amber-500/5">
          <CardContent className="flex items-start gap-3 pt-6">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
            <div className="space-y-1 text-sm">
              <p className="font-medium">Your Red Arch email is a placeholder.</p>
              <p className="text-muted-foreground">
                Session tokens aren&apos;t carrying your email/username yet, so your account shows{" "}
                <code className="rounded bg-muted px-1">{me?.email}</code>. Once an administrator
                configures the Clerk JWT template (see Site Admin → setup), your real details sync on
                your next sign-in.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">Red Arch account</h2>
            {isSiteAdmin ? <Badge>site admin</Badge> : null}
          </div>
          {meError ? (
            <p className="text-sm text-destructive">{meError}</p>
          ) : me === null ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <div className="space-y-2">
              <DetailRow label="Username" value={me.username} />
              <DetailRow label="Email" value={me.email} />
              <DetailRow
                label="Organizations"
                value={
                  orgs.length > 0 ? (
                    <span className="flex flex-wrap gap-1.5">
                      {orgs.map((org) => (
                        <Badge key={org.id} variant="outline">
                          {org.name}
                          {org.is_admin ? " · admin" : ""}
                        </Badge>
                      ))}
                    </span>
                  ) : (
                    "No organizations — ask an administrator to add you to one."
                  )
                }
              />
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <h2 className="text-lg font-semibold">Clerk identity</h2>
          {!isLoaded || !user ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <div className="space-y-2">
              <DetailRow label="Clerk user ID" value={<code className="text-xs">{user.id}</code>} />
              <DetailRow label="Username" value={user.username} />
              <DetailRow label="Full name" value={user.fullName} />
              <DetailRow
                label="Primary email"
                value={user.primaryEmailAddress?.emailAddress}
              />
            </div>
          )}
        </CardContent>
      </Card>

      <div>
        <h2 className="mb-3 text-lg font-semibold">Account settings</h2>
        {/* Hash routing keeps Clerk's editor on this single page — no catch-all
            route needed. This is the source of truth for name/email/password. */}
        <UserProfile routing="hash" />
      </div>
    </div>
  );
}
