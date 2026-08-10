"use client";

import { Bell } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listApprovals, listNotifications, listQuestions } from "@/lib/api/agents";

/** How often to re-check, when the tab is actually being looked at. */
const POLL_MS = 20_000;

/**
 * How many things are waiting on *you* — pending approvals, questions agents have
 * asked a person, and unresolved escalations.
 *
 * Still not the notification unread count: most notifications are a record that
 * something happened and need nothing from you, and a badge that counts those
 * trains people to ignore it. Escalations are the exception and are counted —
 * every one of them means work has already stopped. Leaving them out is how a
 * work order sat blocked, with an escalation written and unread, under a bell
 * that said nothing was waiting.
 *
 * Polling is gated on visibility: a background tab left open overnight would
 * otherwise make thousands of requests for a number nobody is reading.
 */
export function PendingWorkBell() {
  const [count, setCount] = useState(0);

  const refresh = useCallback(async () => {
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
    try {
      const [approvals, questions, notifications] = await Promise.all([
        listApprovals(),
        listQuestions(),
        listNotifications(true),
      ]);
      setCount(
        approvals.length + questions.length + notifications.filter((n) => n.kind === "escalation").length,
      );
    } catch {
      // A member without agent-admin rights gets 403 here, and an offline tab
      // gets a network error. Neither is worth a banner: the badge simply does
      // not update, and the rest of the header keeps working.
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    const onVisible = () => void refresh();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  // A bare dot on a bell is easy to miss, and the thing it marks is an agent
  // stopped dead waiting for a person — so when there IS something, say so in
  // words. With nothing pending it stays a quiet icon.
  if (count === 0) {
    return (
      <Link
        href="/agents/approvals"
        aria-label="Nothing waiting for you"
        className="rounded-sm p-1 text-muted-foreground transition-colors hover:text-foreground"
      >
        <Bell className="h-4 w-4" />
      </Link>
    );
  }

  return (
    <Link
      href="/agents/approvals"
      aria-label={`${count} waiting for you`}
      className="flex items-center gap-1.5 rounded-full bg-amber-100 px-2 py-1 text-xs font-medium text-amber-900 transition-colors hover:bg-amber-200 dark:bg-amber-900/40 dark:text-amber-100 dark:hover:bg-amber-900/60"
    >
      <Bell className="h-3.5 w-3.5" />
      {count === 1 ? "1 waiting on you" : `${count} waiting on you`}
    </Link>
  );
}
