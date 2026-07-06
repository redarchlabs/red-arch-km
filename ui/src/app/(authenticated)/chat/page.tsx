"use client";

import { Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AssistantPanel } from "@/components/chat/AssistantPanel";
import { ChatMessage, type Message } from "@/components/chat/ChatMessage";
import { ScopeSelector } from "@/components/chat/ScopeSelector";
import { SessionList } from "@/components/chat/SessionList";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useOrg } from "@/context/OrgContext";
import {
  createSession,
  deleteSession,
  getSession,
  listSessions,
  updateSession,
} from "@/lib/api/chat";
import { listFolders } from "@/lib/api/folders";
import {
  emptyAgentState,
  reduceAgentEvent,
  streamAgentChat,
  streamChat,
} from "@/lib/api/search";
import { listTags } from "@/lib/api/tags";
import type { ChatSession, Folder, Tag } from "@/types";

type ChatMode = "standard" | "agentic" | "assistant";

export default function ChatPage() {
  const { currentOrgId } = useOrg();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [scopeFolderIds, setScopeFolderIds] = useState<string[]>([]);
  const [scopeTagIds, setScopeTagIds] = useState<string[]>([]);
  const [mode, setMode] = useState<ChatMode>("standard");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Load folders + tags so the user can scope the chat's retrieval to any
  // combination of them (context switching). Reset the selected scope on org
  // change — an id from the previous org must never ride along under the new
  // org's X-Org-ID.
  useEffect(() => {
    setScopeFolderIds([]);
    setScopeTagIds([]);
    if (!currentOrgId) {
      setFolders([]);
      setTags([]);
      return;
    }
    let active = true;
    listFolders()
      .then((f) => {
        if (active) setFolders(f);
      })
      .catch(() => {
        // Non-fatal: the scope selector just omits folders.
      });
    listTags()
      .then((page) => {
        if (active) setTags(page.items);
      })
      .catch(() => {
        // Non-fatal: the scope selector just omits tags.
      });
    return () => {
      active = false;
    };
  }, [currentOrgId]);

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

    // Cancel any previous in-flight stream before starting a new one. The
    // ref also lets handleNew / unmount abort the underlying fetch so
    // brain-api doesn't keep generating tokens the user can't see.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    let finalMessages: Message[] = [...messages, userMsg, assistantMsg];

    // brain-api filters by tag *name*, not id — map the selected ids across.
    const scopeTagNames = tags
      .filter((t) => scopeTagIds.includes(t.id))
      .map((t) => t.name);

    try {
      if (mode === "agentic") {
        // Agentic mode: consume the fact-engine trace stream. The answer only
        // arrives on the terminal `final` event; until then we surface the live
        // reasoning trace so the multi-step loop reads as progress.
        let agentState = emptyAgentState();
        for await (const event of streamAgentChat(query, {
          chat_history: history,
          folder_ids: scopeFolderIds,
          tags: scopeTagNames,
          signal: controller.signal,
        })) {
          agentState = reduceAgentEvent(agentState, event);
          if (event.type === "error") setError(agentState.error ?? "Stream error");
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = {
                ...last,
                content: agentState.answer || last.content,
                agentTrace: agentState.trace,
                unsupportedCitations: agentState.unsupportedCitations,
              };
            }
            finalMessages = next;
            return next;
          });
        }
      } else {
        for await (const event of streamChat(query, {
          chat_history: history,
          folder_ids: scopeFolderIds,
          tags: scopeTagNames,
          signal: controller.signal,
        })) {
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
      }
    } catch (e: unknown) {
      // AbortError is an expected control-flow signal (user clicked New,
      // navigated away, or sent a new message mid-stream) — not a failure.
      if (e instanceof DOMException && e.name === "AbortError") {
        // no-op
      } else {
        setError(e instanceof Error ? e.message : "Chat request failed");
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
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
    abortRef.current?.abort();
    setActiveId(null);
    setMessages([]);
    setError(null);
  };

  const handleDelete = useCallback(
    async (id: string) => {
      // Optimistically drop the row; restore it if the request fails so the
      // list never silently loses a conversation the server still holds.
      const previous = sessions;
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (id === activeId) {
        abortRef.current?.abort();
        setActiveId(null);
        setMessages([]);
      }
      try {
        await deleteSession(id);
      } catch (e: unknown) {
        setSessions(previous);
        setError(e instanceof Error ? e.message : "Failed to delete conversation");
      }
    },
    [sessions, activeId],
  );

  // Cancel any in-flight stream when the user navigates away from /chat
  // so brain-api stops generating immediately.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

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
        onDelete={(id) => void handleDelete(id)}
      />

      <div className="flex flex-1 flex-col pl-4">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Chat</h1>
            <p className="text-sm text-muted-foreground">
              Ask questions about documents in your organization.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div
              className="flex overflow-hidden rounded-md border text-xs"
              role="group"
              aria-label="Chat mode"
            >
              {(["standard", "agentic", "assistant"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  disabled={streaming}
                  aria-pressed={mode === m}
                  className={
                    mode === m
                      ? "bg-primary px-2.5 py-1 font-medium text-primary-foreground"
                      : "px-2.5 py-1 text-muted-foreground hover:bg-muted"
                  }
                >
                  {m === "standard" ? "Standard" : m === "agentic" ? "Agentic" : "Assistant"}
                </button>
              ))}
            </div>
            {mode !== "assistant" ? (
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">Scope</span>
                <ScopeSelector
                  folders={folders}
                  tags={tags}
                  selectedFolderIds={scopeFolderIds}
                  selectedTagIds={scopeTagIds}
                  onChangeFolders={setScopeFolderIds}
                  onChangeTags={setScopeTagIds}
                  disabled={streaming}
                />
              </div>
            ) : null}
          </div>
        </div>

        {mode === "assistant" ? (
          <AssistantPanel />
        ) : (
          <>
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
          </>
        )}
      </div>
    </div>
  );
}
