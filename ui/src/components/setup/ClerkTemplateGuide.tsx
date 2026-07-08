"use client";

import { AlertTriangle, KeyRound } from "lucide-react";

/**
 * Install-time guidance shown in the setup wizard: how to configure the Clerk
 * JWT template so session tokens carry `email`/`username`. Without it, users are
 * provisioned with `user_<id>@placeholder.invalid` addresses because Clerk's
 * DEFAULT session token omits those claims (see services/user_provisioning.py).
 */
export function ClerkTemplateGuide() {
  // NEXT_PUBLIC_ vars are inlined at build time, so this reflects what the
  // running UI will actually send — a reliable way to catch a name mismatch.
  const templateName = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;

  const claimsJson = `{
  "email": "{{user.primary_email_address}}",
  "username": "{{user.username}}"
}`;

  return (
    <div className="space-y-3 rounded-md border border-dashed p-4">
      <div className="flex items-start gap-3">
        <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="space-y-1">
          <h3 className="text-sm font-semibold">Configure the Clerk JWT template</h3>
          <p className="text-sm text-muted-foreground">
            So new members show up with their real name and email (not a{" "}
            <code className="rounded bg-muted px-1">@placeholder.invalid</code> address), session
            tokens must carry <code className="rounded bg-muted px-1">email</code> and{" "}
            <code className="rounded bg-muted px-1">username</code> claims. Clerk&apos;s default
            token omits them, so add a JWT template once per installation.
          </p>
        </div>
      </div>

      {templateName ? (
        <p className="text-sm">
          This UI mints tokens via the template named{" "}
          <code className="rounded bg-muted px-1 font-semibold">{templateName}</code> — the template
          you create in Clerk must use <span className="font-medium">exactly</span> this name.
        </p>
      ) : (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/5 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <p>
            <span className="font-medium">No template name is configured.</span> Set{" "}
            <code className="rounded bg-muted px-1">NEXT_PUBLIC_CLERK_JWT_TEMPLATE</code> in{" "}
            <code className="rounded bg-muted px-1">ui/.env.local</code> (e.g.{" "}
            <code className="rounded bg-muted px-1">redarch-km</code>) and restart the UI, or tokens
            will keep provisioning placeholder emails.
          </p>
        </div>
      )}

      <ol className="ml-1 list-inside list-decimal space-y-1.5 text-sm text-muted-foreground">
        <li>
          In the{" "}
          <a
            href="https://dashboard.clerk.com"
            target="_blank"
            rel="noreferrer"
            className="font-medium text-foreground underline underline-offset-2"
          >
            Clerk dashboard
          </a>{" "}
          open <span className="font-medium">Configure → Sessions → JWT templates</span> and click{" "}
          <span className="font-medium">New template</span> (Blank).
        </li>
        <li>
          Name it{" "}
          <code className="rounded bg-muted px-1 text-foreground">{templateName || "redarch-km"}</code>{" "}
          — it must match <code className="rounded bg-muted px-1">NEXT_PUBLIC_CLERK_JWT_TEMPLATE</code>.
        </li>
        <li>Set the Claims to the JSON below, then Save.</li>
        <li>
          Confirm <code className="rounded bg-muted px-1">NEXT_PUBLIC_CLERK_JWT_TEMPLATE</code> is set
          in <code className="rounded bg-muted px-1">ui/.env.local</code> and restart the UI.
        </li>
      </ol>

      <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
        <code>{claimsJson}</code>
      </pre>

      <p className="text-xs text-muted-foreground">
        Existing members with placeholder emails self-heal on their next sign-in — their real values
        re-sync automatically.
      </p>
    </div>
  );
}
