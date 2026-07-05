"use client";

import { useEffect, useState } from "react";

import { ParentFolderSelect } from "@/components/folders/ParentFolderSelect";
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
import { updateDocument } from "@/lib/api/documents";
import type { Document, Folder } from "@/types";

interface DocumentPropertiesProps {
  document: Document;
  folders: Folder[];
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

/**
 * Edit a document's title, description, folder, and its OWN viewer/contributor
 * permissions (independent of the folder; they default from the folder at
 * creation but can be overridden here).
 */
export function DocumentProperties({
  document,
  folders,
  open,
  onClose,
  onSaved,
}: DocumentPropertiesProps) {
  const [title, setTitle] = useState(document.title);
  const [description, setDescription] = useState(document.description ?? "");
  const [folderId, setFolderId] = useState<string | null>(document.folder_id);
  const [viewers, setViewers] = useState<PermissionEntry[]>(
    document.viewer_permissions_config ?? [],
  );
  const [contributors, setContributors] = useState<PermissionEntry[]>(
    document.contributor_permissions_config ?? [],
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTitle(document.title);
    setDescription(document.description ?? "");
    setFolderId(document.folder_id);
    setViewers(document.viewer_permissions_config ?? []);
    setContributors(document.contributor_permissions_config ?? []);
    setError(null);
  }, [document]);

  const handleClose = () => {
    if (!submitting) onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) {
      setError("Title is required");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await updateDocument(document.id, {
        title: title.trim(),
        description,
        folder_id: folderId,
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
          <DialogTitle>Document properties</DialogTitle>
          <DialogDescription>
            Edit details and control who can view or contribute to this document. Permissions
            default from the folder but can be set per-document here.
          </DialogDescription>
        </DialogHeader>

        <div>
          <label htmlFor="dp-title" className="mb-1.5 block text-sm font-medium">
            Title
          </label>
          <Input
            id="dp-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            disabled={submitting}
          />
        </div>

        <div>
          <label htmlFor="dp-folder" className="mb-1.5 block text-sm font-medium">
            Folder
          </label>
          <ParentFolderSelect
            id="dp-folder"
            value={folderId}
            onChange={setFolderId}
            folders={folders}
            disabled={submitting}
          />
        </div>

        <div>
          <label htmlFor="dp-description" className="mb-1.5 block text-sm font-medium">
            Description
          </label>
          <Textarea
            id="dp-description"
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
