"use client";

import { useEffect, useState } from "react";

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
import { updateFolder } from "@/lib/api/folders";
import type { Folder } from "@/types";

interface FolderPropertiesProps {
  folder: Folder;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/** Edit a folder's name, description, and viewer/contributor permissions. */
export function FolderProperties({ folder, open, onClose, onSaved }: FolderPropertiesProps) {
  const [name, setName] = useState(folder.name);
  const [description, setDescription] = useState(folder.description ?? "");
  const [viewers, setViewers] = useState<PermissionEntry[]>(folder.viewer_permissions_config ?? []);
  const [contributors, setContributors] = useState<PermissionEntry[]>(
    folder.contributor_permissions_config ?? [],
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-seed the form whenever a different folder is opened.
  useEffect(() => {
    setName(folder.name);
    setDescription(folder.description ?? "");
    setViewers(folder.viewer_permissions_config ?? []);
    setContributors(folder.contributor_permissions_config ?? []);
    setError(null);
  }, [folder]);

  const handleClose = () => {
    if (!submitting) onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    if (name.includes(".")) {
      setError("Folder names cannot contain '.'");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await updateFolder(folder.id, {
        name: name.trim(),
        description,
        viewer_permissions_config: viewers.length > 0 ? viewers : null,
        contributor_permissions_config: contributors.length > 0 ? contributors : null,
      });
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} className="max-w-3xl">
      <form onSubmit={handleSubmit} className="space-y-4">
        <DialogHeader>
          <DialogTitle>Folder properties</DialogTitle>
          <DialogDescription>
            Rename the folder and control who can view or contribute to its documents.
          </DialogDescription>
        </DialogHeader>

        <div>
          <label htmlFor="fp-name" className="mb-1.5 block text-sm font-medium">
            Name
          </label>
          <Input
            id="fp-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            disabled={submitting}
          />
        </div>

        <div>
          <label htmlFor="fp-description" className="mb-1.5 block text-sm font-medium">
            Description
          </label>
          <Textarea
            id="fp-description"
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
            {submitting ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
