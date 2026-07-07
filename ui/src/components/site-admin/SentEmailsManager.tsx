"use client";

import { Mail, Paperclip, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchSentEmail,
  fetchSentEmails,
  type EmailAddress,
  type SentEmailDetail,
  type SentEmailList,
  type SentEmailSummary,
} from "@/lib/api/emails";
import { getApiErrorMessage } from "@/lib/api/errors";

// Mailpit's ring buffer defaults to 500 messages; one page covers the common case.
const PAGE_SIZE = 100;

function formatAddress(addr: EmailAddress | null): string {
  if (!addr) return "—";
  return addr.name ? `${addr.name} <${addr.address}>` : addr.address;
}

function formatRecipients(addrs: EmailAddress[]): string {
  if (addrs.length === 0) return "—";
  return addrs.map(formatAddress).join(", ");
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function SentEmailsManager() {
  const [data, setData] = useState<SentEmailList | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setIsLoading(true);
    setError(null);
    try {
      setData(await fetchSentEmails(0, PAGE_SIZE));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load sent emails"));
    } finally {
      if (!silent) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const messages = data?.messages ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold">Sent emails</h2>
              <p className="text-sm text-muted-foreground">
                Messages captured by the Mailpit container (dev/staging). In production the
                app sends via a real SMTP relay and nothing is captured here.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={isLoading}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          {isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : data && !data.available ? (
            <div className="rounded-md border border-dashed p-6 text-center">
              <Mail className="mx-auto h-8 w-8 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">Mailpit isn&apos;t running</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Start the mail container to capture outgoing email
                {data.detail ? <span className="block font-mono">({data.detail})</span> : null}
              </p>
            </div>
          ) : messages.length === 0 ? (
            <p className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
              No emails captured yet.
            </p>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">
                Showing <span className="font-medium text-foreground">{messages.length}</span> of{" "}
                {data?.total ?? messages.length}
              </p>
              <div className="hidden overflow-x-auto rounded-md border md:block">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">Sent</th>
                      <th className="px-3 py-2 font-medium">To</th>
                      <th className="px-3 py-2 font-medium">Subject</th>
                      <th className="px-3 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {messages.map((m) => (
                      <EmailRow key={m.id} message={m} onOpen={() => setOpenId(m.id)} />
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="space-y-2 md:hidden">
                {messages.map((m) => (
                  <li key={m.id}>
                    <button
                      type="button"
                      onClick={() => setOpenId(m.id)}
                      className="w-full rounded-md border p-3 text-left text-sm hover:bg-muted/50"
                    >
                      <p className="font-medium">{m.subject}</p>
                      <p className="mt-1 truncate text-xs text-muted-foreground">
                        To: {formatRecipients(m.to)}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">{formatTimestamp(m.created)}</p>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </CardContent>
      </Card>

      {openId ? <EmailDetailDialog id={openId} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

interface EmailRowProps {
  message: SentEmailSummary;
  onOpen: () => void;
}

function EmailRow({ message, onOpen }: EmailRowProps) {
  return (
    <tr className="cursor-pointer border-t align-top hover:bg-muted/50" onClick={onOpen}>
      <td className="px-3 py-2 whitespace-nowrap text-xs text-muted-foreground">
        {formatTimestamp(message.created)}
      </td>
      <td className="px-3 py-2 text-xs">{formatRecipients(message.to)}</td>
      <td className="px-3 py-2">
        <span className="font-medium">{message.subject}</span>
        {message.snippet ? (
          <span className="ml-2 text-xs text-muted-foreground">— {message.snippet}</span>
        ) : null}
      </td>
      <td className="px-3 py-2 text-right">
        {message.attachments > 0 ? (
          <Badge variant="secondary" className="gap-1">
            <Paperclip className="h-3 w-3" />
            {message.attachments}
          </Badge>
        ) : null}
      </td>
    </tr>
  );
}

interface EmailDetailDialogProps {
  id: string;
  onClose: () => void;
}

/** Modal showing one message's headers + body (HTML sandboxed, text fallback). */
function EmailDetailDialog({ id, onClose }: EmailDetailDialogProps) {
  const [detail, setDetail] = useState<SentEmailDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showText, setShowText] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    fetchSentEmail(id)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(getApiErrorMessage(e, "Failed to load email"));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const hasHtml = Boolean(detail?.html);

  return (
    <Dialog open onClose={onClose} className="max-w-3xl">
      <DialogHeader>
        <DialogTitle>{detail?.subject ?? "Email"}</DialogTitle>
      </DialogHeader>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : detail ? (
        <div className="space-y-3">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            <dt className="text-muted-foreground">From</dt>
            <dd className="font-mono break-all">{formatAddress(detail.from_addr)}</dd>
            <dt className="text-muted-foreground">To</dt>
            <dd className="font-mono break-all">{formatRecipients(detail.to)}</dd>
            {detail.cc.length > 0 ? (
              <>
                <dt className="text-muted-foreground">Cc</dt>
                <dd className="font-mono break-all">{formatRecipients(detail.cc)}</dd>
              </>
            ) : null}
            <dt className="text-muted-foreground">Date</dt>
            <dd>{formatTimestamp(detail.date)}</dd>
            {detail.attachments.length > 0 ? (
              <>
                <dt className="text-muted-foreground">Attachments</dt>
                <dd>{detail.attachments.join(", ")}</dd>
              </>
            ) : null}
          </dl>

          {hasHtml ? (
            <div className="flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => setShowText((v) => !v)}>
                {showText ? "Show HTML" : "Show plain text"}
              </Button>
            </div>
          ) : null}

          {hasHtml && !showText ? (
            // Sandboxed with no permissions — scripts in captured HTML cannot run.
            <iframe
              title="Email preview"
              sandbox=""
              srcDoc={detail.html ?? ""}
              className="h-96 w-full rounded-md border bg-white"
            />
          ) : (
            <pre className="max-h-96 overflow-auto rounded-md border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
              {detail.text || "(no plain-text body)"}
            </pre>
          )}
        </div>
      ) : null}
    </Dialog>
  );
}
