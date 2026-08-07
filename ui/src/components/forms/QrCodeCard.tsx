"use client";

import { Check, Copy, QrCode, TriangleAlert } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";

import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { QrCodeElement } from "@/lib/api/forms";
import { isLoopbackUrl, shareTarget } from "@/lib/forms/shareUrl";

interface Props {
  el: QrCodeElement;
  /** Enclosing scope values, for `{token}` substitution in the url. */
  values: Record<string, unknown>;
  recordId?: string | null;
  disabled?: boolean;
}

/**
 * A QR code for a link, so a screen can hand a URL to a phone or tablet without
 * anyone typing an IP address.
 *
 * Rendered as a PNG data URL rather than injected SVG markup: the encoder's
 * output goes straight into an `<img>` with no HTML parsing in between.
 */
export function QrCodeCard({ el, values, recordId, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [png, setPng] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [copied, setCopied] = useState(false);

  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const target = shareTarget(el.url ?? "", { ...values, id: recordId ?? "" }, origin, el.host);
  const unreachable = !!target && isLoopbackUrl(target);
  const inline = el.display === "inline";
  const size = el.size ?? 320;

  // Encode only when the code is actually on screen. A console left open all day
  // shouldn't re-encode a QR nobody is looking at every time the view refreshes.
  const shouldRender = inline || open;
  useEffect(() => {
    if (!shouldRender || !target) return;
    let alive = true;
    QRCode.toDataURL(target, { width: size * 2, margin: 2, errorCorrectionLevel: "M" })
      .then((data) => {
        if (alive) {
          setPng(data);
          setFailed(false);
        }
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [shouldRender, target, size]);

  const copy = () => {
    void navigator.clipboard?.writeText(target).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const code = (
    <div className="flex flex-col items-center gap-3">
      {!target ? (
        <p className="text-sm text-muted-foreground">No link configured.</p>
      ) : failed ? (
        <p className="text-sm text-destructive">This link couldn&apos;t be turned into a QR code.</p>
      ) : png ? (
        /* A generated data URL; next/image optimises fetched assets and cannot help here. */
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={png}
          alt={`QR code for ${target}`}
          width={size}
          height={size}
          className="rounded-lg bg-white p-3 shadow-sm"
          style={{ width: size, height: size, maxWidth: "100%" }}
        />
      ) : (
        <div className="animate-pulse rounded-lg bg-muted" style={{ width: size, height: size }} />
      )}

      {el.caption ? <p className="text-center text-sm text-muted-foreground">{el.caption}</p> : null}

      {target ? (
        <button
          type="button"
          onClick={copy}
          title="Copy the link"
          className="inline-flex max-w-full items-center gap-2 rounded-md border px-3 py-1.5 font-mono text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {copied ? <Check className="h-3.5 w-3.5 shrink-0" /> : <Copy className="h-3.5 w-3.5 shrink-0" />}
          <span className="truncate">{target}</span>
        </button>
      ) : null}

      {unreachable ? (
        // The failure this component exists to prevent: scanning succeeds, and
        // then the tablet tries to reach ITSELF and shows a connection error.
        <p className="flex items-start gap-2 rounded-md bg-warning/10 px-3 py-2 text-left text-xs text-warning">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This address only works on this computer. Reopen this page at the machine&apos;s network
            address (or set a host on the QR element) so the code points somewhere the tablet can reach.
          </span>
        </p>
      ) : null}
    </div>
  );

  if (inline) {
    return (
      <div className="space-y-2">
        {el.label ? <p className="text-sm font-medium">{el.label}</p> : null}
        {code}
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className="inline-flex min-h-10 items-center gap-2 rounded-md border bg-background px-4 py-2 text-sm font-medium transition-all duration-150 hover:bg-accent hover:text-accent-foreground active:scale-[0.97] disabled:pointer-events-none disabled:opacity-60"
      >
        <QrCode className="h-4 w-4" />
        {el.label ?? "Show QR code"}
      </button>
      {open ? (
        <Dialog open={open} onClose={() => setOpen(false)} className="max-w-md">
          <DialogHeader>
            <DialogTitle>{el.label ?? "Scan to open"}</DialogTitle>
          </DialogHeader>
          <div className="p-4">{code}</div>
        </Dialog>
      ) : null}
    </>
  );
}
