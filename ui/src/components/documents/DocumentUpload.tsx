"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  createDocument,
  type TranslationMethod,
  uploadDocument,
} from "@/lib/api/documents";
import { listFolders } from "@/lib/api/folders";
import type { Folder } from "@/types";

type Mode = "text" | "file";

const documentSchema = z.object({
  title: z.string().min(1, "Title is required").max(255),
  description: z.string().max(2000).optional(),
  text: z.string().min(1, "Content is required"),
});

interface DocumentUploadProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  /** Pre-select a folder (e.g. when creating from inside a folder view). */
  defaultFolderId?: string | null;
}

export function DocumentUpload({ open, onClose, onCreated, defaultFolderId }: DocumentUploadProps) {
  const [mode, setMode] = useState<Mode>("text");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [translationMethod, setTranslationMethod] = useState<TranslationMethod>("ocr");
  const [folderId, setFolderId] = useState<string>(defaultFolderId ?? "");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Load the folder list once the dialog opens so the user can file the doc.
  useEffect(() => {
    if (!open) return;
    let active = true;
    listFolders()
      .then((f) => {
        if (active) setFolders(f);
      })
      .catch(() => {
        // Non-fatal: folder picker just stays empty ("No folder").
      });
    return () => {
      active = false;
    };
  }, [open]);

  useEffect(() => {
    setFolderId(defaultFolderId ?? "");
  }, [defaultFolderId, open]);

  const reset = () => {
    setMode("text");
    setTitle("");
    setDescription("");
    setText("");
    setFile(null);
    setTranslationMethod("ocr");
    setFolderId(defaultFolderId ?? "");
    setError(null);
  };

  const handleClose = () => {
    if (!submitting) {
      reset();
      onClose();
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validate per mode: file mode needs a title + a file; text mode needs
    // title + pasted content.
    if (!title.trim()) {
      setError("Title is required");
      return;
    }
    if (mode === "file" && !file) {
      setError("Choose a file to upload");
      return;
    }
    if (mode === "text") {
      const parsed = documentSchema.safeParse({ title, description, text });
      if (!parsed.success) {
        setError(parsed.error.issues[0]?.message ?? "Invalid input");
        return;
      }
    }

    setSubmitting(true);
    try {
      if (mode === "file" && file) {
        await uploadDocument({
          file,
          title: title.trim(),
          description: description || null,
          folder_id: folderId || null,
          translation_method: translationMethod,
        });
        toast.success("File uploaded", {
          description:
            translationMethod === "ai"
              ? "Extracting text with AI vision, then indexing…"
              : "Running OCR, then indexing…",
        });
      } else {
        await createDocument({
          title: title.trim(),
          description: description || null,
          text,
          folder_id: folderId || null,
        });
        toast.success("Document created", {
          description: "It's queued for chunking, embedding, and indexing.",
        });
      }
      reset();
      onCreated();
      onClose();
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "Failed to create document";
      setError(message);
      toast.error(mode === "file" ? "Could not upload file" : "Could not create document", {
        description: message,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} className="max-w-2xl">
      <form onSubmit={handleSubmit}>
        <DialogHeader>
          <DialogTitle>New Document</DialogTitle>
          <DialogDescription>
            Add a document to the knowledge base. It will be chunked, embedded, and indexed.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="inline-flex rounded-md border p-0.5 text-sm">
            <button
              type="button"
              onClick={() => {
                // Clear the other mode's input so a hidden, previously-entered
                // value can't be silently submitted after toggling back.
                setMode("text");
                setFile(null);
                setError(null);
              }}
              disabled={submitting}
              className={`rounded px-3 py-1 ${mode === "text" ? "bg-accent font-medium" : "text-muted-foreground"}`}
            >
              Paste text
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("file");
                setText("");
                setError(null);
              }}
              disabled={submitting}
              className={`rounded px-3 py-1 ${mode === "file" ? "bg-accent font-medium" : "text-muted-foreground"}`}
            >
              Upload file
            </button>
          </div>

          <div>
            <label htmlFor="title" className="mb-1.5 block text-sm font-medium">
              Title
            </label>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Q3 Planning Notes"
              required
              disabled={submitting}
            />
          </div>

          <div>
            <label htmlFor="description" className="mb-1.5 block text-sm font-medium">
              Description <span className="text-muted-foreground">(optional)</span>
            </label>
            <Input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Brief summary"
              disabled={submitting}
            />
          </div>

          <div>
            <label htmlFor="folder" className="mb-1.5 block text-sm font-medium">
              Folder <span className="text-muted-foreground">(optional)</span>
            </label>
            <select
              id="folder"
              value={folderId}
              onChange={(e) => setFolderId(e.target.value)}
              disabled={submitting}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="">No folder (unfiled)</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.dot_path || f.name}
                </option>
              ))}
            </select>
          </div>

          {mode === "text" ? (
            <div>
              <label htmlFor="text" className="mb-1.5 block text-sm font-medium">
                Content
              </label>
              <Textarea
                id="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Paste document content here…"
                className="min-h-[240px]"
                disabled={submitting}
              />
            </div>
          ) : (
            <>
              <div>
                <label htmlFor="file" className="mb-1.5 block text-sm font-medium">
                  File
                </label>
                <input
                  id="file"
                  type="file"
                  accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.gif,.webp,.txt,.md"
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    setFile(f);
                    // Default the title to the filename (minus extension) if empty.
                    if (f && !title.trim()) setTitle(f.name.replace(/\.[^.]+$/, ""));
                  }}
                  disabled={submitting}
                  className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-transparent file:px-3 file:py-1.5 file:text-sm file:font-medium hover:file:bg-accent"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  PDF, images (PNG/JPG/TIFF/…), or text. The original is stored, then
                  chunked, embedded, and indexed.
                </p>
              </div>

              <div>
                <label className="mb-1.5 block text-sm font-medium">Text extraction</label>
                <div className="flex flex-col gap-1.5 text-sm">
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="translation_method"
                      checked={translationMethod === "ocr"}
                      onChange={() => setTranslationMethod("ocr")}
                      disabled={submitting}
                    />
                    <span>
                      <strong>OCR</strong> — free, fast; best for clearly typed documents.
                    </span>
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="translation_method"
                      checked={translationMethod === "ai"}
                      onChange={() => setTranslationMethod("ai")}
                      disabled={submitting}
                    />
                    <span>
                      <strong>AI vision</strong> — paid; handles scanned, handwritten, or
                      complex layouts.
                    </span>
                  </label>
                </div>
              </div>
            </>
          )}

          {error ? (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={handleClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting
              ? mode === "file"
                ? "Uploading…"
                : "Creating…"
              : mode === "file"
                ? "Upload"
                : "Create"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
