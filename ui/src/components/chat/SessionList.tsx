"use client";

import { MessageCircle, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ChatSession } from "@/types";

interface SessionListProps {
  sessions: ChatSession[];
  activeId: string | null;
  onSelect: (id: string | null) => void;
  onNew: () => void;
}

function previewFromSession(session: ChatSession): string {
  const data = session.chat_data as { messages?: Array<{ content: string }> } | null;
  const first = data?.messages?.[0];
  return first?.content?.slice(0, 60) ?? "New conversation";
}

export function SessionList({ sessions, activeId, onSelect, onNew }: SessionListProps) {
  return (
    <aside className="flex w-64 flex-col border-r">
      <div className="p-3">
        <Button onClick={onNew} className="w-full justify-start" variant="outline">
          <Plus className="h-4 w-4" />
          New chat
        </Button>
      </div>
      <nav className="flex-1 overflow-y-auto p-2">
        {sessions.length === 0 ? (
          <p className="px-2 text-sm text-muted-foreground">No past conversations.</p>
        ) : (
          <ul className="space-y-1">
            {sessions.map((session) => {
              const active = session.id === activeId;
              return (
                <li key={session.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(session.id)}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors",
                      active
                        ? "bg-accent text-accent-foreground"
                        : "hover:bg-accent/50 text-muted-foreground",
                    )}
                  >
                    <MessageCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-foreground">
                        {previewFromSession(session)}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {formatDate(session.updated_at)}
                      </p>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>
    </aside>
  );
}
