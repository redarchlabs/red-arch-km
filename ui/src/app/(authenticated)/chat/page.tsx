"use client";

import { Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ChatMessage, type Message } from "@/components/chat/ChatMessage";
import { SessionList } from "@/components/chat/SessionList";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useOrg } from "@/context/OrgContext";
import {
  createSession,
  getSession,
  listSessions,
  updateSession,
} from "@/lib/api/chat";
import { streamChat } from "@/lib/api/search";
import type { ChatSession } from "@/types";

export default function ChatPage() {
  const { currentOrgId } = useOrg();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadSessions = useCallback(async () => {
    if (!currentOrgId) return;
    try {
      setSessions(await listSessions());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load sessions");
    }
  }, [currentOrgId]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const selectSession = useCallback(async (id: string | null) => {
    setActiveId(id);
    setMessages([]);
    setError(null);
    if (id === null) return;
    try {
      const session = await getSession(id);
      const data = session.chat_data as { messages?: Partial<Message>[] } | null;
      // Backfill stable IDs on persisted messages that predate the id field.
      const hydrated: Message[] = (data?.messages ?? []).map((m, idx) => ({
        id: m.id ?? `${id}-${idx}`,
        role: (m.role as Message["role"]) ?? "assistant",
        content: m.content ?? "",
        sources: m.sources,
      }));
      setMessages(hydrated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load session");
    }
  }, []);

  const persistMessages = useCallback(
    async (sessionId: string, next: Message[]) => {
      try {
        await updateSession(sessionId, { messages: next });
      } catch {
        // Non-fatal: conversation continues in memory even if persist fails
      }
    },
    [],
  );

  const sendMessage = async () => {
    const query = input.trim();
    if (!query || streaming || !currentOrgId) return;

    setError(null);
    setInput("");

    // Ensure we have a session to write to
    let sessionId = activeId;
    if (sessionId === null) {
      try {
        const session = await createSession({});
        sessionId = session.id;
        setActiveId(session.id);
        setSessions((prev) => [session, ...prev]);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to create session");
        return;
      }
    }

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: query,
    };
    const assistantMsg: Message = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      streaming: true,
    };
    const history = messages.map((m) => ({ role: m.role, content: m.content }));

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setStreaming(true);

    let finalMessages: Message[] = [...messages, userMsg, assistantMsg];

    try {
      for await (const event of streamChat(query, { chat_history: history })) {
        if (event.type === "sources" && event.sources) {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = { ...last, sources: event.sources };
            }
            finalMessages = next;
            return next;
          });
        } else if (event.type === "delta" && event.content) {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content + event.content };
            }
            finalMessages = next;
            return next;
          });
        } else if (event.type === "error") {
          setError(event.message ?? "Stream error");
          break;
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Chat request failed");
    } finally {
      setStreaming(false);
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.role === "assistant") {
          next[next.length - 1] = { ...last, streaming: false };
        }
        finalMessages = next;
        return next;
      });

      if (sessionId) {
        void persistMessages(sessionId, finalMessages);
        void loadSessions();
      }

      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  };

  const handleNew = () => {
    setActiveId(null);
    setMessages([]);
    setError(null);
  };

  if (!currentOrgId) {
    return <p className="text-muted-foreground">Select an organization to chat.</p>;
  }

  return (
    <div className="flex h-full">
      <SessionList
        sessions={sessions}
        activeId={activeId}
        onSelect={(id) => void selectSession(id)}
        onNew={handleNew}
      />

      <div className="flex flex-1 flex-col pl-4">
        <div className="mb-4">
          <h1 className="text-2xl font-semibold">Chat</h1>
          <p className="text-sm text-muted-foreground">
            Ask questions about documents in your organization.
          </p>
        </div>

        <div
          ref={scrollRef}
          className="flex-1 space-y-4 overflow-y-auto rounded-lg border bg-muted/20 p-4"
        >
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              <p>Start a conversation by asking a question below.</p>
            </div>
          ) : (
            messages.map((m) => <ChatMessage key={m.id} message={m} />)
          )}
        </div>

        {error ? (
          <p className="mt-2 text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <div className="mt-4 flex gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question… (Shift+Enter for new line)"
            className="min-h-[60px] resize-none"
            disabled={streaming}
          />
          <Button
            onClick={() => void sendMessage()}
            disabled={streaming || !input.trim()}
            size="icon"
            className="h-auto"
            aria-label="Send"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
