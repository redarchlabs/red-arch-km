"use client";

import { Plus, Trash2, X } from "lucide-react";
import { Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getFormRender,
  type ButtonElement,
  type CalculatedElement,
  type ChatElement,
  type FormElement,
  type FormRender,
  type FormSubmit,
  type InputElement,
  type LiveValueElement,
  type RecordListElement,
  type SectionElement,
  type TableElement,
} from "@/lib/api/forms";
import { createRecord, listRecords, type EntityRecord } from "@/lib/api/entityRecords";
import { callConnection, runWorkflow } from "@/lib/api/workflows";
import { buildCatalog, type Catalog, fieldMeta, relatedEntityId } from "@/lib/forms/catalog";
import { evaluate } from "@/lib/forms/jsonLogic";

import { FieldControl } from "./FieldControl";

/**
 * The one renderer that walks a `FormRender` element tree — used by the public
 * intake page, the authenticated internal fill page, and the builder preview.
 * It owns the editable state (root values + related sections/tables/blocks),
 * live-evaluates calculated fields, and builds the `FormSubmit` payload.
 */
export interface FormRendererProps {
  render: FormRender;
  mode?: "fill" | "preview";
  onSubmit?: (payload: FormSubmit) => Promise<void> | void;
  onRunWorkflow?: (
    workflowId: string,
    inputs: Record<string, unknown>,
    // Optional record to run the workflow against (an entity-bound view, or a
    // record-list row action). The host page falls back to its own record when omitted.
    recordId?: string,
  ) => Promise<void> | void;
  submitting?: boolean;
  /** When set (fill mode), render a submit button in the footer. */
  defaultSubmitLabel?: string;
  /** Page-controlled error to show above the footer submit button. */
  error?: string | null;
}

type Values = Record<string, unknown>;
type RowState = { id?: string; values: Values; related?: Record<string, { id?: string; values: Values }> };
type RelatedState = { id?: string; values?: Values; rows?: RowState[] };

interface Scope {
  entityId: string;
  values: Values;
  setValue: (slug: string, v: unknown) => void;
  keyPrefix: string;
}

const SPAN: Record<string, string> = {
  full: "sm:col-span-12",
  half: "sm:col-span-6",
  third: "sm:col-span-4",
  quarter: "sm:col-span-3",
};
function spanClass(width?: string | null): string {
  return SPAN[width ?? "full"] ?? "sm:col-span-12";
}

function nonEmpty(v: Values): boolean {
  return Object.values(v).some((x) => x !== "" && x != null);
}

/** Collect `input` elements reachable in the root scope (layout containers only, since
 * section/table/block change entity scope and hold their own values). */
function collectInputs(elements: FormElement[]): InputElement[] {
  const out: InputElement[] = [];
  const walk = (els: FormElement[]) => {
    for (const el of els) {
      if (el.type === "input") out.push(el);
      else if (el.type === "columns") el.columns.forEach((c) => walk(c.elements));
      else if (el.type === "panel") walk(el.elements);
      else if (el.type === "tab_group") el.tabs.forEach((t) => walk(t.elements));
      else if (el.type === "accordion") el.panes.forEach((p) => walk(p.elements));
    }
  };
  walk(elements);
  return out;
}

/** Read a dot-path (e.g. `head.pitch`, `items.0.name`) out of a parsed JSON value. */
function readJsonPointer(data: unknown, pointer?: string | null): unknown {
  if (!pointer) return data;
  let cur: unknown = data;
  for (const part of pointer.split(".")) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

/** A read-only readout that polls a CORS-reachable endpoint and shows a JSON value.
 * Top-level (owns polling state) so it's not re-created each parent render. */
function LiveValueNode({ el }: { el: LiveValueElement }) {
  const [value, setValue] = useState<string>("…");
  const [ok, setOk] = useState(true);

  useEffect(() => {
    if (!el.url) {
      setValue("(no url)");
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const res = await fetch(el.url, { headers: { Accept: "application/json" } });
        const json: unknown = await res.json();
        const picked = readJsonPointer(json, el.json_pointer);
        if (!alive) return;
        setOk(true);
        setValue(picked == null ? "—" : typeof picked === "object" ? JSON.stringify(picked) : String(picked));
      } catch {
        if (!alive) return;
        setOk(false);
        setValue("unreachable");
      }
    };
    void tick();
    const id = window.setInterval(tick, Math.max(200, el.poll_ms ?? 1000));
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [el.url, el.json_pointer, el.poll_ms]);

  return (
    <div>
      {el.label ? <label className="mb-1 block text-sm font-medium">{el.label}</label> : null}
      <div
        className={`rounded-md border bg-muted/40 px-3 py-2 text-sm tabular-nums ${
          ok ? "" : "text-destructive"
        }`}
      >
        {value}
        {el.units ? <span className="ml-1 text-muted-foreground">{el.units}</span> : null}
      </div>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Read-only "status board": lists an entity's records (newest-first or by
 * sort_by), optionally re-polling to stay live, with an optional per-row workflow
 * button that runs against that row's record. */
function RecordListNode({
  el,
  onRunWorkflow,
}: {
  el: RecordListElement;
  onRunWorkflow?: FormRendererProps["onRunWorkflow"];
}) {
  const [rows, setRows] = useState<EntityRecord[] | null>(null);
  const [error, setError] = useState(false);
  const [busyRow, setBusyRow] = useState<string | null>(null);

  useEffect(() => {
    if (!el.entity) {
      setError(true);
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const res = await listRecords(el.entity, {
          limit: el.limit ?? 20,
          orderBy: el.sort_by ?? undefined,
          orderDir: el.sort_dir ?? "desc",
        });
        if (!alive) return;
        setError(false);
        setRows(res.items);
      } catch {
        if (!alive) return;
        setError(true);
      }
    };
    void tick();
    // poll_ms turns the board live; otherwise fetch once.
    const interval = el.poll_ms ? window.setInterval(tick, Math.max(500, el.poll_ms)) : undefined;
    return () => {
      alive = false;
      if (interval) window.clearInterval(interval);
    };
  }, [el.entity, el.limit, el.sort_by, el.sort_dir, el.poll_ms]);

  // Columns: the explicit field list, else the field slugs on the first row
  // (excluding base columns) so an unconfigured board still shows something.
  const columns =
    el.fields && el.fields.length > 0
      ? el.fields
      : rows && rows[0]
        ? Object.keys(rows[0]).filter((k) => !["id", "created_at", "updated_at", "org_id"].includes(k))
        : [];

  const runRow = async (recordId: string) => {
    if (!el.row_workflow_id || !onRunWorkflow) return;
    setBusyRow(recordId);
    try {
      await onRunWorkflow(el.row_workflow_id, {}, recordId);
    } finally {
      setBusyRow(null);
    }
  };

  return (
    <div>
      {el.label ? <label className="mb-1 block text-sm font-medium">{el.label}</label> : null}
      {error ? (
        <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm text-destructive">
          Unable to load records.
        </div>
      ) : rows && rows.length === 0 ? (
        <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
          {el.empty_text ?? "No records yet."}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                {columns.map((c) => (
                  <th key={c} className="px-3 py-2 text-left font-medium">
                    {c}
                  </th>
                ))}
                {el.row_workflow_id ? <th className="px-3 py-2" /> : null}
              </tr>
            </thead>
            <tbody>
              {(rows ?? []).map((row) => (
                <tr key={String(row.id)} className="border-t">
                  {columns.map((c) => (
                    <td key={c} className="px-3 py-2 tabular-nums">
                      {formatCell(row[c])}
                    </td>
                  ))}
                  {el.row_workflow_id ? (
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        className="rounded border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
                        disabled={busyRow === String(row.id)}
                        onClick={() => void runRow(String(row.id))}
                      >
                        {el.row_action_label ?? "Run"}
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** A compact on/off pill for the chat's live answer-speed controls. */
function ChatToggle({
  label,
  on,
  onClick,
  disabled,
  title,
}: {
  label: string;
  on: boolean;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={on}
      title={title}
      className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors disabled:opacity-50 ${
        on ? "border-primary bg-primary text-primary-foreground" : "border-input text-muted-foreground hover:bg-muted"
      }`}
    >
      {label}
    </button>
  );
}

/** Render a chat response latency in ms as a compact human string (e.g. "820ms", "3.4s"). */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Default "one moment…" chatter shown/spoken while a slow answer is still cooking.
 * `{q}` is swapped for the person's question so some lines restate what was asked
 * (which both reassures the asker and buys the robot time). */
const DEFAULT_FILLER_PHRASES: readonly string[] = [
  "One moment please…",
  "Let me check on that…",
  "Give me a second while I look that up…",
  'Checking my notes on "{q}"…',
  "Hang on, pulling that together…",
  "Just a moment while I find the best answer…",
  'Still working on "{q}" — almost there.',
  "Good question — let me dig into that.",
];

/** Pick a filler phrase at random, avoiding an immediate repeat, and fill in `{q}`.
 * `lastIdx` is a mutable cursor (a ref's value) so successive calls don't repeat. */
function pickFiller(pool: readonly string[], question: string, lastIdx: { current: number }): string {
  let idx = Math.floor(Math.random() * pool.length);
  if (pool.length > 1 && idx === lastIdx.current) idx = (idx + 1) % pool.length;
  lastIdx.current = idx;
  const q = question.length > 48 ? `${question.slice(0, 48)}…` : question;
  return pool[idx].replace(/\{q\}/g, q);
}

/** A conversation panel backed by two entities (a conversation session + its messages).
 * Lists the active conversation's messages as bubbles (polling), and on send creates a
 * `person` message then runs the answer workflow so the robot replies + speaks. TOP-LEVEL
 * so its polling/input state is stable across FormRenderer re-renders. */
function ChatNode({ el, preview }: { el: ChatElement; preview: boolean }) {
  const convEntity = el.conversation_entity ?? "robot_conversation";
  const msgEntity = el.message_entity ?? "robot_message";
  const relSlug = el.conversation_relationship ?? "conversation";
  const roleField = el.role_field ?? "role";
  const textField = el.text_field ?? "text";
  const channelField = el.channel_field ?? "channel";
  const pollMs = Math.max(500, el.poll_ms ?? 1500);

  // Live answer-speed controls (Fast mode / Knowledge graph / Concise / Answer model).
  // When enabled, the chosen values ride along as workflow `inputs` so a viewer can
  // trade quality for speed per turn without touching the workflow itself.
  const controls = el.answer_controls ?? null;
  const controlsEnabled = !!controls?.show;
  const models = controls?.models?.length ? controls.models : [];
  const [fastMode, setFastMode] = useState(controls?.fast_mode ?? true);
  const [useGraph, setUseGraph] = useState(controls?.knowledge_graph ?? false);
  const [concise, setConcise] = useState(controls?.concise ?? true);
  const [speak, setSpeak] = useState(controls?.speak ?? true);
  const [answerModel, setAnswerModel] = useState(models[0] ?? "");

  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<EntityRecord[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // `thinking` covers the gap between firing the answer workflow and the robot's
  // reply landing in the polled message list — the run is not awaited, so this is
  // what tells the user the robot is working.
  const [thinking, setThinking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Response-time tracking: `askedAtRef` stamps when a turn is sent, `responseMs`
  // freezes the measured latency keyed by the robot reply's id (so each answered
  // turn keeps its own time), and `elapsedMs` ticks live while the robot thinks.
  const askedAtRef = useRef<number | null>(null);
  const [responseMs, setResponseMs] = useState<Record<string, number>>({});
  const [elapsedMs, setElapsedMs] = useState(0);
  // Perceived-latency filler: ephemeral "one moment…" lines shown (and optionally
  // spoken) while the robot works. `lastQuestionRef` lets a filler restate the ask;
  // `fillerIdxRef` avoids picking the same phrase twice in a row.
  const filler = el.filler ?? null;
  const fillerEnabled = !!filler?.show;
  const [fillers, setFillers] = useState<string[]>([]);
  const lastQuestionRef = useRef("");
  const fillerIdxRef = useRef(-1);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Whether the transcript is scrolled to (near) the bottom. Auto-scroll only
  // follows new content when this is true, so polling never yanks the reader back
  // down while they're looking at earlier turns.
  const atBottomRef = useRef(true);
  // Guards against setting state from a backgrounded run that resolves/rejects
  // after the node unmounts.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Adopt the most recent conversation on mount (fresh one is created on first send).
  useEffect(() => {
    if (preview) return;
    let alive = true;
    void (async () => {
      try {
        const res = await listRecords(convEntity, { limit: 1 });
        if (alive && res.items[0]) setConversationId(String(res.items[0].id));
      } catch {
        /* no conversation yet — created on first send */
      }
    })();
    return () => {
      alive = false;
    };
  }, [convEntity, preview]);

  // Poll the active conversation's messages (client-side filter: the records list
  // endpoint has no field filter, but a live chat's turns are among the newest rows).
  useEffect(() => {
    if (preview || !conversationId) return;
    let alive = true;
    const tick = async () => {
      try {
        const res = await listRecords(msgEntity, { limit: 100 });
        if (!alive) return;
        const rows = res.items
          .filter((r) => String(r[relSlug] ?? "") === conversationId)
          .sort((a, b) => String(a.created_at ?? "").localeCompare(String(b.created_at ?? "")));
        setMessages(rows);
        // The robot has answered once the newest turn is no longer the person's;
        // clearing here (rather than on the run promise) makes the reply and the
        // dismissal of the typing indicator land on the same tick.
        const last = rows[rows.length - 1];
        if (last && String(last[roleField] ?? "") !== "person") {
          setThinking(false);
          setFillers([]);
          // Freeze the latency for this reply once. `askedAtRef` is nulled after
          // recording, so a still-answered conversation re-polled later won't
          // overwrite the time with the (much larger) idle gap.
          if (askedAtRef.current != null) {
            const elapsed = Date.now() - askedAtRef.current;
            askedAtRef.current = null;
            const id = String(last.id);
            setResponseMs((prev) => (prev[id] != null ? prev : { ...prev, [id]: elapsed }));
          }
        }
      } catch {
        /* transient; keep the last good render */
      }
    };
    void tick();
    const id = window.setInterval(tick, pollMs);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [msgEntity, relSlug, roleField, conversationId, pollMs, preview]);

  // Track the reader's position: pinned to the bottom (follow new turns) vs.
  // scrolled up (leave them where they are). ~48px of slack counts as "bottom".
  const handleScroll = () => {
    const node = scrollRef.current;
    if (!node) return;
    atBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
  };

  useEffect(() => {
    if (!atBottomRef.current) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, thinking, fillers]);

  // Tick a live counter while the robot is thinking so the user watches the
  // response time climb; the frozen `responseMs` value takes over once it replies.
  useEffect(() => {
    if (!thinking) return;
    const start = askedAtRef.current ?? Date.now();
    setElapsedMs(Date.now() - start);
    const id = window.setInterval(() => setElapsedMs(Date.now() - start), 100);
    return () => window.clearInterval(id);
  }, [thinking]);

  // While the robot is thinking, drip out filler chatter: the first line after
  // `delay_ms`, then another every `interval_ms`, until the reply lands (which
  // flips `thinking` off and tears this down). Each line is also spoken through
  // `speak_connection` if configured, so the physical robot stalls out loud too.
  useEffect(() => {
    if (!thinking || !fillerEnabled) return;
    const pool = filler?.phrases?.length ? filler.phrases : DEFAULT_FILLER_PHRASES;
    const delay = Math.max(400, filler?.delay_ms ?? 1400);
    const interval = Math.max(2000, filler?.interval_ms ?? 6000);
    // Say a couple of lines, then fall silent (the ticking timer still shows the
    // robot is working) — endless "one moment…" is more annoying than reassuring.
    const maxLines = Math.max(1, filler?.max_lines ?? 2);
    let emitted = 0;
    let intervalId: number | undefined;
    const emit = () => {
      emitted += 1;
      const phrase = pickFiller(pool, lastQuestionRef.current, fillerIdxRef);
      setFillers((prev) => [...prev, phrase]);
      if (filler?.speak_connection) {
        // Fire-and-forget: a filler that fails to speak must never surface an
        // error or block the real answer.
        void callConnection({
          connection: filler.speak_connection,
          method: "POST",
          path: filler.speak_path ?? "/say",
          body: { [filler.speak_field ?? "text"]: phrase },
        }).catch(() => {});
      }
      if (emitted >= maxLines && intervalId !== undefined) {
        window.clearInterval(intervalId);
        intervalId = undefined;
      }
    };
    const timeoutId = window.setTimeout(() => {
      emit();
      if (emitted < maxLines) intervalId = window.setInterval(emit, interval);
    }, delay);
    return () => {
      window.clearTimeout(timeoutId);
      if (intervalId !== undefined) window.clearInterval(intervalId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thinking, fillerEnabled]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending || preview) return;
    setSending(true);
    setErr(null);
    setFillers([]);
    lastQuestionRef.current = text;
    // Sending is an explicit "bring me to the latest" gesture — re-pin to bottom
    // even if the reader had scrolled up.
    atBottomRef.current = true;
    try {
      let convId = conversationId;
      if (!convId) {
        const conv = await createRecord(convEntity, { title: text.slice(0, 60), status: "active" });
        convId = String(conv.id);
        setConversationId(convId);
      }
      await createRecord(msgEntity, {
        [roleField]: "person",
        [channelField]: "typed",
        [textField]: text,
        [relSlug]: convId,
      });
      setInput("");
      if (el.answer_workflow_id) {
        // Fire the answer workflow but DON'T block the composer on it: the run
        // fans out to RAG + one or more LLM steps and can take many seconds. The
        // robot's reply is written as a robot_message and surfaced by the poll
        // loop, so we show a "thinking" indicator and let the user keep typing.
        // The generous timeout is just a backstop against a hung request.
        const inputs: Record<string, unknown> = { text, conversation_id: convId };
        // Whole-conversation memory: pass every prior turn so the workflow can
        // condense a follow-up ("tell me more") into a standalone, context-aware
        // search query. `messages` holds the turns before this one (the just-sent
        // person message hasn't been polled back yet), which is exactly the history.
        const history = messages
          .map((m) => {
            const who = String(m[roleField] ?? "") === "person" ? "User" : "Robot";
            const line = String(m[textField] ?? "").trim();
            return line ? `${who}: ${line}` : "";
          })
          .filter(Boolean)
          .join("\n");
        if (history) inputs.history = history;
        if (controlsEnabled) {
          // Fast mode = retrieval-only, so synthesize is its inverse.
          inputs.synthesize = !fastMode;
          inputs.use_knowledge_graph = useGraph;
          inputs.max_words = concise ? controls?.concise_words ?? 20 : controls?.verbose_words ?? 45;
          inputs.speak = speak;
          if (answerModel) inputs.answer_model = answerModel;
        }
        askedAtRef.current = Date.now();
        setThinking(true);
        void runWorkflow(el.answer_workflow_id, { inputs }, 120000).catch((e: unknown) => {
          if (!mountedRef.current) return;
          askedAtRef.current = null;
          setThinking(false);
          setFillers([]);
          setErr(e instanceof Error ? e.message : "The robot could not answer");
        });
      }
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to send");
    } finally {
      setSending(false);
    }
  };

  const startNew = () => {
    setConversationId(null);
    setMessages([]);
    setThinking(false);
    setErr(null);
    askedAtRef.current = null;
    setResponseMs({});
    setFillers([]);
    fillerIdxRef.current = -1;
  };

  return (
    <div className="flex h-96 flex-col rounded-lg border bg-background">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-sm font-semibold">{el.title ?? "Chat"}</span>
        <button
          type="button"
          onClick={startNew}
          disabled={preview}
          className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-60"
        >
          New chat
        </button>
      </div>
      {controlsEnabled ? (
        <div className="flex flex-wrap items-center gap-1.5 border-b px-3 py-1.5">
          <ChatToggle label="Fast mode" on={fastMode} onClick={() => setFastMode((v) => !v)} disabled={preview} />
          <ChatToggle
            label="Knowledge graph"
            on={useGraph}
            onClick={() => setUseGraph((v) => !v)}
            disabled={preview || fastMode}
            title={fastMode ? "Fast mode already skips the knowledge graph" : undefined}
          />
          <ChatToggle label="Concise" on={concise} onClick={() => setConcise((v) => !v)} disabled={preview} />
          <ChatToggle
            label={speak ? "🔊 Speak" : "🔇 Speak"}
            on={speak}
            onClick={() => setSpeak((v) => !v)}
            disabled={preview}
            title={speak ? "Robot says the answer aloud" : "Robot answers silently (text only)"}
          />
          {models.length > 0 ? (
            <select
              className="ml-auto rounded-md border bg-background px-2 py-1 text-xs disabled:opacity-60"
              value={answerModel}
              disabled={preview}
              onChange={(e) => setAnswerModel(e.target.value)}
              aria-label="Answer model"
            >
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : null}
        </div>
      ) : null}
      <div ref={scrollRef} onScroll={handleScroll} className="flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {messages.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {preview ? "Chat preview — messages appear here at runtime." : "Say hello to the robot…"}
          </p>
        ) : (
          messages.map((m) => {
            const isPerson = String(m[roleField] ?? "") === "person";
            const took = responseMs[String(m.id)];
            return (
              <div key={m.id} className={`flex flex-col ${isPerson ? "items-end" : "items-start"}`}>
                <div
                  className={`max-w-[80%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-sm ${
                    isPerson ? "bg-primary text-primary-foreground" : "bg-muted"
                  }`}
                >
                  {String(m[textField] ?? "")}
                </div>
                {!isPerson && took != null ? (
                  <span className="mt-0.5 px-1 text-[11px] tabular-nums text-muted-foreground">
                    responded in {formatDuration(took)}
                  </span>
                ) : null}
              </div>
            );
          })
        )}
        {fillers.map((f, i) => (
          <div key={`filler-${i}`} className="flex justify-start">
            <div className="max-w-[80%] whitespace-pre-wrap break-words rounded-2xl bg-muted/60 px-3 py-2 text-sm italic text-muted-foreground">
              {f}
            </div>
          </div>
        ))}
        {thinking ? (
          <div className="flex justify-start">
            <div className="flex max-w-[80%] items-center gap-2 rounded-2xl bg-muted px-3 py-2 text-sm text-muted-foreground">
              <span className="flex items-center gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current" />
              </span>
              <span className="tabular-nums text-[11px]">{formatDuration(elapsedMs)}</span>
            </div>
          </div>
        ) : null}
      </div>
      {err ? <p className="px-3 text-xs text-destructive">{err}</p> : null}
      <div className="flex items-center gap-2 border-t p-2">
        <input
          className="w-full rounded-md border bg-background px-3 py-2 text-sm disabled:opacity-60"
          placeholder={el.placeholder ?? "Message the robot…"}
          value={input}
          disabled={preview || sending}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button
          type="button"
          onClick={() => void send()}
          disabled={preview || sending || !input.trim()}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

/** A standalone input (text/textarea/number/slider/toggle/select). TOP-LEVEL and driven
 * by props so its identity is stable across FormRenderer re-renders — otherwise every
 * keystroke would remount the control and drop focus/scroll (the value lives in the
 * parent's form state, so it persists regardless). */
function InputNode({
  el,
  value,
  onChange,
  disabled,
}: {
  el: InputElement;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
}) {
  const base = "w-full rounded-md border bg-background px-3 py-2 text-sm disabled:opacity-60";
  const label = el.label ? (
    <label className="mb-1 block text-sm font-medium">
      {el.label}
      {el.required ? <span className="text-destructive"> *</span> : null}
    </label>
  ) : null;

  let control: ReactNode;
  switch (el.control) {
    case "textarea":
      control = (
        <textarea
          className={base}
          rows={3}
          disabled={disabled}
          placeholder={el.placeholder ?? undefined}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
      break;
    case "number":
      control = (
        <input
          type="number"
          className={base}
          disabled={disabled}
          placeholder={el.placeholder ?? undefined}
          min={el.min ?? undefined}
          max={el.max ?? undefined}
          step={el.step ?? undefined}
          value={value == null ? "" : Number(value)}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
      );
      break;
    case "slider":
      control = (
        <div className="flex items-center gap-3">
          <input
            type="range"
            className="flex-1"
            disabled={disabled}
            min={el.min ?? 0}
            max={el.max ?? 100}
            step={el.step ?? 1}
            value={Number(value ?? el.min ?? 0)}
            onChange={(e) => onChange(Number(e.target.value))}
          />
          <span className="w-12 text-right text-sm tabular-nums text-muted-foreground">
            {value == null ? "—" : String(value)}
          </span>
        </div>
      );
      break;
    case "toggle":
      control = (
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(value)}
          disabled={disabled}
          onClick={() => onChange(!value)}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-60 ${
            value ? "bg-primary" : "bg-muted"
          }`}
        >
          <span
            className={`inline-block h-5 w-5 transform rounded-full bg-background shadow transition-transform ${
              value ? "translate-x-5" : "translate-x-0.5"
            }`}
          />
        </button>
      );
      break;
    case "select":
      control = (
        <select
          className={base}
          disabled={disabled}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">—</option>
          {(el.options ?? []).map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label ?? opt.value}
            </option>
          ))}
        </select>
      );
      break;
    default:
      control = (
        <input
          type="text"
          className={base}
          disabled={disabled}
          placeholder={el.placeholder ?? undefined}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
  return (
    <div>
      {label}
      {control}
      {el.help_text ? <p className="mt-1 text-xs text-muted-foreground">{el.help_text}</p> : null}
    </div>
  );
}

export function FormRenderer({
  render,
  mode = "fill",
  onSubmit,
  onRunWorkflow,
  submitting = false,
  defaultSubmitLabel,
  error,
}: FormRendererProps) {
  const catalog = useMemo(() => buildCatalog(render), [render]);
  const preview = mode === "preview";

  const [values, setValues] = useState<Values>(() => ({ ...render.values }));
  const [related, setRelated] = useState<Record<string, RelatedState>>(() => initRelated(render));
  const [ui, setUi] = useState<Record<string, number | boolean>>({});

  const setRoot = (slug: string, v: unknown) => setValues((p) => ({ ...p, [slug]: v }));
  const setSection = (relId: string, slug: string, v: unknown) =>
    setRelated((p) => ({ ...p, [relId]: { ...p[relId], values: { ...p[relId]?.values, [slug]: v } } }));
  const rowsOf = (relId: string): RowState[] => related[relId]?.rows ?? [];
  const setRows = (relId: string, rows: RowState[]) =>
    setRelated((p) => ({ ...p, [relId]: { ...p[relId], rows } }));
  const setRowValue = (relId: string, idx: number, slug: string, v: unknown) => {
    const rows = [...rowsOf(relId)];
    rows[idx] = { ...rows[idx], values: { ...rows[idx].values, [slug]: v } };
    setRows(relId, rows);
  };
  const setRowRelated = (relId: string, idx: number, colRel: string, slug: string, v: unknown) => {
    const rows = [...rowsOf(relId)];
    const rel = { ...(rows[idx].related ?? {}) };
    rel[colRel] = { ...rel[colRel], values: { ...rel[colRel]?.values, [slug]: v } };
    rows[idx] = { ...rows[idx], related: rel };
    setRows(relId, rows);
  };

  // Seed standalone-input defaults into root state once, so a button's workflow inputs /
  // connection body see the default even if the operator never touched the control.
  useEffect(() => {
    const inputs = collectInputs(render.config.elements);
    setValues((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const el of inputs) {
        if (next[el.key] === undefined && el.default !== undefined && el.default !== null) {
          next[el.key] = el.default;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [render]);

  const buildPayload = (): FormSubmit => {
    const outRelated: FormSubmit["related"] = {};
    for (const [relId, st] of Object.entries(related)) {
      if (st.rows) {
        const rows = st.rows.filter((r) => nonEmpty(r.values) || Object.keys(r.related ?? {}).length);
        if (rows.length) outRelated[relId] = { rows };
      } else if (st.values && nonEmpty(st.values)) {
        outRelated[relId] = { values: st.values };
      }
    }
    return { values, related: outRelated };
  };

  const runButton = async (btn: ButtonElement) => {
    if (btn.action.kind === "submit") {
      await onSubmit?.(buildPayload());
    } else if (btn.action.kind === "run_workflow") {
      if (btn.action.confirm && !window.confirm(btn.action.confirm)) return;
      const inputs: Record<string, unknown> = {};
      for (const [k, expr] of Object.entries(btn.action.inputs)) inputs[k] = evaluate(expr, values);
      await onRunWorkflow?.(btn.action.workflow_id, inputs);
    } else if (btn.action.kind === "call_connection") {
      const action = btn.action;
      if (action.confirm && !window.confirm(action.confirm)) return;
      const body: Record<string, unknown> = {};
      for (const [k, expr] of Object.entries(action.body)) body[k] = evaluate(expr, values);
      try {
        const res = await callConnection({
          connection: action.connection,
          method: action.method,
          path: action.path,
          body,
        });
        if (res.ok) toast.success(action.success_message ?? "Done");
        else toast.error(`Request failed (${res.status_code})`);
      } catch (err: unknown) {
        toast.error(err instanceof Error ? err.message : "Connection call failed");
      }
    } else if (btn.action.kind === "link") {
      if (typeof window !== "undefined") {
        if (btn.action.new_tab) window.open(btn.action.href, "_blank");
        else window.location.href = btn.action.href;
      }
    }
  };

  const rootScope: Scope = {
    entityId: catalog.rootEntityId,
    values,
    setValue: setRoot,
    keyPrefix: "root",
  };

  // Render the list by CALLING ElementNode (and its sub-nodes) as functions rather than
  // mounting them as components. These node fns hold no hooks, so inlining them means a
  // FormRenderer re-render (e.g. a keystroke) DIFFS the DOM in place instead of remounting
  // the whole tree — inputs keep focus/scroll. Only true stateful leaves (FieldControl,
  // InputNode, LiveValueNode, EmbeddedForm) stay real components with stable identity.
  const renderList = (elements: FormElement[], scope: Scope) => (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-12">
      {elements.map((el, i) => (
        <Fragment key={el.id ?? `${scope.keyPrefix}-${i}`}>{ElementNode({ el, scope })}</Fragment>
      ))}
    </div>
  );

  function ElementNode({ el, scope }: { el: FormElement; scope: Scope }): ReactNode {
    switch (el.type) {
      case "field": {
        const meta = fieldMeta(catalog, scope.entityId, el.slug);
        if (!meta) return null;
        return (
          <div className={spanClass(el.width)}>
            <FieldControl
              meta={meta}
              label={el.label ?? meta.label}
              required={el.required ?? meta.required}
              readOnly={el.read_only || preview}
              placeholder={el.placeholder ?? undefined}
              display={el.display}
              value={scope.values[el.slug]}
              onChange={(v) => scope.setValue(el.slug, v)}
              name={`${scope.keyPrefix}-${el.slug}`}
            />
            {el.help_text ? <p className="mt-1 text-xs text-muted-foreground">{el.help_text}</p> : null}
          </div>
        );
      }
      case "label":
        return <div className={spanClass(el.width)}>{LabelNode({ el })}</div>;
      case "calculated":
        return <div className={spanClass(el.width)}>{CalculatedNode({ el, scope })}</div>;
      case "input":
        return (
          <div className={spanClass(el.width)}>
            <InputNode
              el={el}
              value={scope.values[el.key]}
              onChange={(v) => scope.setValue(el.key, v)}
              disabled={preview}
            />
          </div>
        );
      case "live_value":
        return (
          <div className={spanClass(el.width)}>
            <LiveValueNode el={el} />
          </div>
        );
      case "record_list":
        return (
          <div className="sm:col-span-12">
            <RecordListNode el={el} onRunWorkflow={onRunWorkflow} />
          </div>
        );
      case "chat":
        return (
          <div className="sm:col-span-12">
            <ChatNode el={el} preview={preview} />
          </div>
        );
      case "button":
        return <div className={spanClass(el.width)}>{ButtonNode({ el })}</div>;
      case "form_ref":
        return (
          <div className="sm:col-span-12 space-y-2 border-t pt-4">
            {el.label ? <h2 className="text-lg font-semibold">{el.label}</h2> : null}
            <EmbeddedForm formId={el.form_id} />
          </div>
        );
      case "columns": {
        const totalSpan = el.columns.reduce((s, c) => s + Math.max(1, c.span), 0) || 1;
        return (
          <div className="sm:col-span-12 grid grid-cols-1 gap-4" style={{ gridTemplateColumns: undefined }}>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-12">
              {el.columns.map((col, ci) => (
                <div
                  key={ci}
                  className="sm:col-auto"
                  style={{ gridColumn: `span ${Math.round((Math.max(1, col.span) / totalSpan) * 12)} / span ${Math.round((Math.max(1, col.span) / totalSpan) * 12)}` }}
                >
                  {renderList(col.elements, scope)}
                </div>
              ))}
            </div>
          </div>
        );
      }
      case "panel":
        return (
          <fieldset className="sm:col-span-12 rounded-lg border p-4">
            {el.title ? <legend className="px-1 text-sm font-semibold">{el.title}</legend> : null}
            {renderList(el.elements, scope)}
          </fieldset>
        );
      case "tab_group":
        return TabGroupNode({ el, scope });
      case "accordion":
        return AccordionNode({ el, scope });
      case "section":
        return SectionNode({ el });
      case "table":
        return TableNode({ el });
      case "block":
        return BlockNode({ el });
      default:
        return null;
    }
  }

  function LabelNode({ el }: { el: Extract<FormElement, { type: "label" }> }) {
    if (el.variant === "divider") return <hr className="my-2 border-t" />;
    if (el.variant === "heading")
      return <h2 className="border-b pb-1 text-lg font-semibold">{el.text}</h2>;
    if (el.variant === "subheading") return <h3 className="text-base font-semibold">{el.text}</h3>;
    return <p className="text-sm text-muted-foreground">{el.text}</p>;
  }

  function CalculatedNode({ el, scope }: { el: CalculatedElement; scope: Scope }) {
    const result = evaluate(el.expression, scope.values);
    const display = result == null ? "—" : String(result);
    return (
      <div>
        {el.label ? <label className="mb-1 block text-sm font-medium">{el.label}</label> : null}
        <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm">{display}</div>
        {el.help_text ? <p className="mt-1 text-xs text-muted-foreground">{el.help_text}</p> : null}
      </div>
    );
  }

  function ButtonNode({ el }: { el: ButtonElement }) {
    const styles: Record<string, string> = {
      primary: "bg-primary text-primary-foreground",
      secondary: "border bg-background",
      danger: "bg-destructive text-destructive-foreground",
      ghost: "hover:bg-muted",
    };
    return (
      <button
        type={el.action.kind === "submit" ? "submit" : "button"}
        disabled={preview || submitting}
        onClick={el.action.kind === "submit" ? undefined : () => void runButton(el)}
        className={`rounded-md px-4 py-2 text-sm font-medium disabled:opacity-60 ${styles[el.style]}`}
      >
        {el.label}
      </button>
    );
  }

  function TabGroupNode({ el, scope }: { el: Extract<FormElement, { type: "tab_group" }>; scope: Scope }) {
    const key = el.id ?? "tabs";
    const active = (ui[`tab-${key}`] as number) ?? 0;
    return (
      <div className="sm:col-span-12 space-y-3">
        <div className="flex gap-1 border-b">
          {el.tabs.map((tab, i) => (
            <button
              key={i}
              type="button"
              onClick={() => setUi((p) => ({ ...p, [`tab-${key}`]: i }))}
              className={`px-3 py-1.5 text-sm font-medium ${
                i === active ? "border-b-2 border-primary" : "text-muted-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {el.tabs[active] ? renderList(el.tabs[active].elements, scope) : null}
      </div>
    );
  }

  function AccordionNode({ el, scope }: { el: Extract<FormElement, { type: "accordion" }>; scope: Scope }) {
    const key = el.id ?? "acc";
    const open = (ui[`acc-${key}`] as number) ?? 0;
    return (
      <div className="sm:col-span-12 space-y-2">
        {el.panes.map((pane, i) => (
          <div key={i} className="rounded-md border">
            <button
              type="button"
              onClick={() => setUi((p) => ({ ...p, [`acc-${key}`]: i }))}
              className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium"
            >
              {pane.label}
              <span>{i === open ? "−" : "+"}</span>
            </button>
            {i === open ? <div className="border-t p-3">{renderList(pane.elements, scope)}</div> : null}
          </div>
        ))}
      </div>
    );
  }

  function SectionNode({ el }: { el: SectionElement }) {
    const entityId = relatedEntityId(catalog, el.relationship_id);
    if (!entityId) return null;
    const scope: Scope = {
      entityId,
      values: related[el.relationship_id]?.values ?? {},
      setValue: (slug, v) => setSection(el.relationship_id, slug, v),
      keyPrefix: `sec-${el.relationship_id}`,
    };
    const heading = <h2 className="text-lg font-semibold">{el.label ?? "Details"}</h2>;
    const modalKey = `modal-${el.relationship_id}`;

    if (el.mode === "modal") {
      const filled = nonEmpty(scope.values);
      return (
        <div className="sm:col-span-12 space-y-2 border-t pt-4">
          {heading}
          <button
            type="button"
            disabled={preview}
            onClick={() => setUi((p) => ({ ...p, [modalKey]: true }))}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <Plus className="h-4 w-4" /> {filled ? "Edit" : "Add"} {el.label ?? "details"}
          </button>
          {ui[modalKey] ? (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
              <div className="w-full max-w-md space-y-4 rounded-lg border bg-card p-6 shadow-lg">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold">{el.label ?? "Details"}</h3>
                  <button type="button" onClick={() => setUi((p) => ({ ...p, [modalKey]: false }))}>
                    <X className="h-5 w-5" />
                  </button>
                </div>
                {renderList(el.elements as FormElement[], scope)}
                <button
                  type="button"
                  onClick={() => setUi((p) => ({ ...p, [modalKey]: false }))}
                  className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
                >
                  Done
                </button>
              </div>
            </div>
          ) : null}
        </div>
      );
    }

    return (
      <div className="sm:col-span-12 space-y-3 border-t pt-4">
        {heading}
        {renderList(el.elements as FormElement[], scope)}
      </div>
    );
  }

  function TableNode({ el }: { el: TableElement }) {
    const relId = el.anchor_relationship_id;
    const childEntity = relatedEntityId(catalog, relId);
    if (!childEntity) return null;
    const rows = rowsOf(relId);
    return (
      <div className="sm:col-span-12 space-y-2 border-t pt-4">
        <h2 className="text-lg font-semibold">{el.label ?? "Items"}</h2>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b text-left">
                {el.columns.map((col, ci) => (
                  <th key={ci} className="px-2 py-1.5 font-medium">
                    {col.label ?? col.slug}
                  </th>
                ))}
                {!preview ? <th className="w-8" /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="border-b align-top">
                  {el.columns.map((col, ci) => {
                    if (col.kind === "field") {
                      const meta = fieldMeta(catalog, childEntity, col.slug);
                      if (!meta) return <td key={ci} />;
                      return (
                        <td key={ci} className="px-2 py-1.5">
                          <FieldControl
                            meta={meta}
                            label=""
                            required={false}
                            readOnly={col.read_only || preview}
                            display={col.display}
                            value={row.values[col.slug]}
                            onChange={(v) => setRowValue(relId, ri, col.slug, v)}
                            name={`tbl-${relId}-${ri}-${col.slug}`}
                          />
                        </td>
                      );
                    }
                    // related column
                    const relatedEntity = relatedEntityId(catalog, col.relationship_id);
                    const meta = relatedEntity ? fieldMeta(catalog, relatedEntity, col.slug) : undefined;
                    if (!meta) return <td key={ci} />;
                    return (
                      <td key={ci} className="px-2 py-1.5">
                        <FieldControl
                          meta={meta}
                          label=""
                          required={false}
                          readOnly={!col.editable || preview}
                          display={col.display}
                          value={row.related?.[col.relationship_id]?.values?.[col.slug]}
                          onChange={(v) => setRowRelated(relId, ri, col.relationship_id, col.slug, v)}
                          name={`tbl-${relId}-${ri}-${col.relationship_id}-${col.slug}`}
                        />
                      </td>
                    );
                  })}
                  {!preview ? (
                    <td className="px-1 py-1.5">
                      <button
                        type="button"
                        onClick={() => setRows(relId, rows.filter((_, i) => i !== ri))}
                        className="text-muted-foreground hover:text-destructive"
                        aria-label="Remove row"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!preview ? (
          <button
            type="button"
            onClick={() => setRows(relId, [...rows, { values: {} }])}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <Plus className="h-4 w-4" /> Add row
          </button>
        ) : null}
      </div>
    );
  }

  function BlockNode({ el }: { el: Extract<FormElement, { type: "block" }> }) {
    const relId = el.anchor_relationship_id;
    const childEntity = relatedEntityId(catalog, relId);
    if (!childEntity) return null;
    const rows = rowsOf(relId);
    return (
      <div className="sm:col-span-12 space-y-3 border-t pt-4">
        <h2 className="text-lg font-semibold">{el.label ?? "Items"}</h2>
        {rows.map((row, ri) => {
          const scope: Scope = {
            entityId: childEntity,
            values: row.values,
            setValue: (slug, v) => setRowValue(relId, ri, slug, v),
            keyPrefix: `blk-${relId}-${ri}`,
          };
          return (
            <div key={ri} className="relative rounded-md border p-3">
              {!preview ? (
                <button
                  type="button"
                  onClick={() => setRows(relId, rows.filter((_, i) => i !== ri))}
                  className="absolute right-2 top-2 text-muted-foreground hover:text-destructive"
                  aria-label="Remove"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              ) : null}
              {renderList(el.elements as FormElement[], scope)}
            </div>
          );
        })}
        {!preview ? (
          <button
            type="button"
            onClick={() => setRows(relId, [...rows, { values: {} }])}
            className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
          >
            <Plus className="h-4 w-4" /> {el.add_label ?? "Add another"}
          </button>
        ) : null}
      </div>
    );
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!preview) void onSubmit?.(buildPayload());
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {renderList(render.config.elements, rootScope)}
      {!preview && (error || defaultSubmitLabel) ? (
        <div className="space-y-3">
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {defaultSubmitLabel ? (
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-primary px-4 py-2.5 font-medium text-primary-foreground disabled:opacity-60"
            >
              {submitting ? "Submitting…" : defaultSubmitLabel}
            </button>
          ) : null}
        </div>
      ) : null}
    </form>
  );
}

/** Renders a form embedded in a view (`form_ref`) as a read-only preview. Full
 * record-bound embedded fill is a future enhancement. */
function EmbeddedForm({ formId }: { formId: string }) {
  const [render, setRender] = useState<FormRender | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    getFormRender(formId)
      .then((r) => active && setRender(r))
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : "Form unavailable"));
    return () => {
      active = false;
    };
  }, [formId]);
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!render) return <p className="text-sm text-muted-foreground">Loading form…</p>;
  return (
    <div className="rounded-md border p-3">
      <FormRenderer render={render} mode="preview" />
    </div>
  );
}

function initRelated(render: FormRender): Record<string, RelatedState> {
  const out: Record<string, RelatedState> = {};
  for (const [relId, data] of Object.entries(render.related ?? {})) {
    if (data.rows) out[relId] = { rows: data.rows.map((r) => ({ ...(r as RowState) })) };
    else out[relId] = { id: (data as RelatedState).id, values: { ...(data.values ?? {}) } };
  }
  return out;
}
