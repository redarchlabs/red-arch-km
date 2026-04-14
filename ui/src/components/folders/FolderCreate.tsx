"use client";

import { useState } from "react";
import { z } from "zod";

import {
  PermissionConfigEditor,
  type PermissionEntry,
} from "@/components/folders/PermissionConfigEditor";
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
import { createFolder } from "@/lib/api/folders";

const schema = z.object({
  name: z.string().min(1, "Name is required").max(255),
  description: z.string().max(2000).optional(),
});

interface FolderCreateProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export function FolderCreate({ open, onClose, onCreated }: FolderCreateProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [viewers, setViewers] = useState<PermissionEntry[]>([]);
  const [contributors, setContributors] = useState<PermissionEntry[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName("");
    setDescription("");
    setViewers([]);
    setContributors([]);
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
    const parsed = schema.safeParse({ name, description });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid input");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createFolder({
        name: parsed.data.name,
        description: parsed.data.description || null,
        viewer_permissions_config: viewers.length > 0 ? viewers : null,
        contributor_permissions_config: contributors.length > 0 ? contributors : null,
      });
      reset();
      onCreated();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} className="max-w-3xl">
      <form onSubmit={handleSubmit} className="space-y-4">
        <DialogHeader>
          <DialogTitle>New Folder</DialogTitle>
          <DialogDescription>
            Create a folder and optionally restrict who can view or contribute to its documents.
          </DialogDescription>
        </DialogHeader>

        <div>
          <label htmlFor="folder-name" className="mb-1.5 block text-sm font-medium">
            Name
          </label>
          <Input
            id="folder-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Engineering Docs"
            required
            disabled={submitting}
          />
        </div>

        <div>
          <label htmlFor="folder-description" className="mb-1.5 block text-sm font-medium">
            Description
          </label>
          <Textarea
            id="folder-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={submitting}
          />
        </div>

        <PermissionConfigEditor value={viewers} onChange={setViewers} label="Viewer permissions" />
        <PermissionConfigEditor
          value={contributors}
          onChange={setContributors}
          label="Contributor permissions"
        />

        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={handleClose} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
