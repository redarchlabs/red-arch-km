"use client";

import { Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";

/**
 * A one-time-secret display: shows a value in a read-only code box with a
 * click-to-copy button and a transient "Copied." confirmation. Used wherever a
 * secret is revealed exactly once (API keys, inbound-webhook tokens/secrets).
 */
export function SecretField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="rounded-md border bg-muted/40 p-3">
      <p className="mb-1 text-xs font-medium text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2">
        <code className="flex-1 break-all rounded bg-background px-2 py-1 text-xs">{value}</code>
        <Button
          type="button"
          variant="outline"
          size="icon"
          className="h-7 w-7 shrink-0"
          onClick={() => void copy()}
          aria-label={`Copy ${label}`}
        >
          <Copy className="h-4 w-4" />
        </Button>
      </div>
      {copied ? <p className="mt-1 text-xs text-emerald-600 dark:text-emerald-400">Copied.</p> : null}
    </div>
  );
}
