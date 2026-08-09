"use client";

import { Loader2, Paperclip, X } from "lucide-react";

import type { PendingAttachment } from "@/lib/usePasteAttach";
import { cn } from "@/lib/utils";

/**
 * What you pasted, before you send it.
 *
 * Shows a thumbnail for an image, because the whole point of pasting a screenshot
 * is that a filename does not tell you which screenshot it was. A failed upload
 * keeps its chip rather than disappearing — an attachment that silently did not
 * attach is worse than one visibly marked failed.
 */
export function AttachmentChips({
  attachments,
  onRemove,
}: {
  attachments: PendingAttachment[];
  onRemove: (key: string) => void;
}) {
  if (attachments.length === 0) return null;

  return (
    <ul className="flex flex-wrap gap-2">
      {attachments.map((attachment) => (
        <li
          key={attachment.key}
          className={cn(
            "flex items-center gap-1.5 rounded-md border bg-muted/30 py-1 pl-1 pr-1.5 text-xs",
            attachment.error && "border-destructive/60",
          )}
        >
          {attachment.preview ? (
            // eslint-disable-next-line @next/next/no-img-element -- a local object URL, not a remote asset
            <img src={attachment.preview} alt="" className="h-8 w-8 rounded object-cover" />
          ) : (
            <Paperclip className="mx-1 h-3.5 w-3.5 text-muted-foreground" />
          )}
          <span className="max-w-40 truncate" title={attachment.name}>
            {attachment.name}
          </span>
          {attachment.uploading ? <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" /> : null}
          {attachment.error ? <span className="text-destructive">{attachment.error}</span> : null}
          <button
            type="button"
            onClick={() => onRemove(attachment.key)}
            aria-label={`Remove ${attachment.name}`}
            className="rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        </li>
      ))}
    </ul>
  );
}
