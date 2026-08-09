"use client";

import { useCallback, useState } from "react";

import { uploadDocument } from "@/lib/api/documents";

/**
 * Paste or drop a file into a message box and have it become a KM2 document.
 *
 * Deliberately the same upload route as the Resources explorer: the extension
 * allowlist, the per-file cap and the extraction pipeline (which OCRs images)
 * all live there, and a second upload path is how those three drift apart.
 *
 * Uploading happens on paste, not on send. A screenshot takes a moment to store
 * and OCR, and doing it while someone is still typing means the message goes as
 * soon as they press send.
 */

/** Images per message. Matches the server's cap so the UI refuses before the
 *  model silently ignores the extras. */
export const MAX_ATTACHMENTS = 4;

export interface PendingAttachment {
  /** Local id while uploading; the document id once it lands. */
  key: string;
  name: string;
  documentId?: string;
  /** Object URL for an image, so a chip can show what was pasted. */
  preview?: string;
  error?: string;
  uploading: boolean;
}

function isImage(file: File): boolean {
  return file.type.startsWith("image/");
}

/** A pasted screenshot arrives as "image.png" for every screenshot ever taken.
 *  Stamping it keeps a work order's documents distinguishable a week later. */
function nameFor(file: File): string {
  if (file.name && file.name !== "image.png") return file.name;
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const extension = file.type.split("/")[1] || "png";
  return `pasted-${stamp}.${extension}`;
}

export function usePasteAttach() {
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);

  const add = useCallback(async (files: File[]) => {
    if (files.length === 0) return;
    const accepted: { file: File; item: PendingAttachment }[] = [];
    setAttachments((prev) => {
      const room = MAX_ATTACHMENTS - prev.length;
      for (const file of files.slice(0, Math.max(room, 0))) {
        accepted.push({
          file,
          item: {
            key: `${Date.now()}-${file.name}-${Math.random().toString(36).slice(2, 8)}`,
            name: nameFor(file),
            preview: isImage(file) ? URL.createObjectURL(file) : undefined,
            uploading: true,
          },
        });
      }
      return [...prev, ...accepted.map((a) => a.item)];
    });

    await Promise.all(
      accepted.map(async ({ file, item }) => {
        try {
          const result = await uploadDocument({ file, title: item.name });
          const document = result.documents[0];
          setAttachments((prev) =>
            prev.map((a) =>
              a.key === item.key
                ? { ...a, uploading: false, documentId: document?.id, error: document ? undefined : "Upload failed" }
                : a,
            ),
          );
        } catch {
          // The chip keeps the failure rather than vanishing: a screenshot that
          // silently did not attach is worse than one visibly marked failed.
          setAttachments((prev) =>
            prev.map((a) => (a.key === item.key ? { ...a, uploading: false, error: "Upload failed" } : a)),
          );
        }
      }),
    );
  }, []);

  const onPaste = useCallback(
    (event: React.ClipboardEvent) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length === 0) return;
      // Only when there ARE files: pasting ordinary text must still paste text.
      event.preventDefault();
      void add(files);
    },
    [add],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length === 0) return;
      event.preventDefault();
      void add(files);
    },
    [add],
  );

  const remove = useCallback((key: string) => {
    setAttachments((prev) => {
      const going = prev.find((a) => a.key === key);
      if (going?.preview) URL.revokeObjectURL(going.preview);
      return prev.filter((a) => a.key !== key);
    });
  }, []);

  const clear = useCallback(() => {
    setAttachments((prev) => {
      for (const a of prev) if (a.preview) URL.revokeObjectURL(a.preview);
      return [];
    });
  }, []);

  /** Ids to send. Excludes anything still uploading or failed, so a message never
   *  claims an attachment the server does not have. */
  const documentIds = attachments.filter((a) => a.documentId).map((a) => a.documentId as string);
  const busy = attachments.some((a) => a.uploading);

  return { attachments, documentIds, busy, onPaste, onDrop, remove, clear, add, full: attachments.length >= MAX_ATTACHMENTS };
}
