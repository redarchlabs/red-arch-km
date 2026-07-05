"use client";

import { MoreVertical, Settings2, SquareArrowOutUpRight, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { DocumentProperties } from "@/components/documents/DocumentProperties";
import { type MenuItem, useRowMenu } from "@/components/common/ActionMenu";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { deleteDocument } from "@/lib/api/documents";
import { formatDate } from "@/lib/format";
import type { Document, Folder } from "@/types";

function statusVariant(status: Document["processing_status"]) {
  if (status === "SUCCESS") return "default";
  if (status === "FAILED") return "destructive";
  return "secondary";
}

interface DocumentRowProps {
  doc: Document;
  folders: Folder[];
  /** Reload the list after a change (rename / move / permissions / delete). */
  onChanged: () => void;
}

export function DocumentRow({ doc, folders, onChanged }: DocumentRowProps) {
  const router = useRouter();
  const [propsOpen, setPropsOpen] = useState(false);

  const items: MenuItem[] = [
    {
      label: "Open",
      icon: <SquareArrowOutUpRight className="h-4 w-4" />,
      onSelect: () => router.push(`/documents/${doc.id}`),
    },
    {
      label: "Properties",
      icon: <Settings2 className="h-4 w-4" />,
      onSelect: () => setPropsOpen(true),
    },
    {
      label: "Delete",
      icon: <Trash2 className="h-4 w-4" />,
      destructive: true,
      onSelect: async () => {
        if (!confirm(`Delete "${doc.title}" permanently?`)) return;
        await deleteDocument(doc.id);
        onChanged();
      },
    },
  ];

  const { open, menu } = useRowMenu(items);

  return (
    <>
      <Link href={`/documents/${doc.id}`} className="block" onContextMenu={open}>
        <Card className="transition-colors hover:bg-accent/30">
          <CardContent className="flex items-center justify-between py-4">
            <div className="min-w-0">
              <h3 className="truncate font-medium">{doc.title}</h3>
              {doc.description ? (
                <p className="mt-0.5 truncate text-sm text-muted-foreground">{doc.description}</p>
              ) : null}
              <p className="mt-1 text-xs text-muted-foreground">{formatDate(doc.created_at)}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <Badge variant={statusVariant(doc.processing_status)}>{doc.processing_status}</Badge>
              <button
                type="button"
                aria-label="Document actions"
                onClick={open}
                className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <MoreVertical className="h-4 w-4" />
              </button>
            </div>
          </CardContent>
        </Card>
      </Link>
      {menu}
      <DocumentProperties
        document={doc}
        folders={folders}
        open={propsOpen}
        onClose={() => setPropsOpen(false)}
        onSaved={onChanged}
      />
    </>
  );
}
