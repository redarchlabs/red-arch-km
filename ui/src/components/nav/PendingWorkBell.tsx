"use client";

import { Bell } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { listApprovals, listQuestions } from "@/lib/api/agents";
import { cn } from "@/lib/utils";

/** How often to re-check, when the tab is actually being looked at. */
const POLL_MS = 20_000;

/**
 * How many things are waiting on *you* — pending approvals plus questions agents
 * have asked a person.
 *
 * Deliberately not the notification unread count: a notification is a record that
 * something happened, most of which needs nothing from you. A badge that counts
 * those trains people to ignore it, and then the one item that genuinely blocks
 * an agent goes unnoticed — which is exactly what happened with a run parked on
 * an approval nobody knew existed.
 *
 * Polling is gated on visibility: a background tab left open overnight would
 * otherwise make thousands of requests for a number nobody is reading.
 */
export function PendingWorkBell() {
  const [count, setCount] = useState(0);

  const refresh = useCallback(async () => {
    if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
    try {
      const [approvals, questions] = await Promise.all([listApprovals(), listQuestions()]);
      setCount(approvals.length + questions.length);
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

  return (
    <Link
      href="/agents/approvals"
      aria-label={count > 0 ? `${count} items waiting for you` : "Nothing waiting for you"}
      className="relative rounded-sm p-1 text-muted-foreground transition-colors hover:text-foreground"
    >
      <Bell className={cn("h-4 w-4", count > 0 && "text-amber-600")} />
      {count > 0 ? (
        <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-600 px-1 text-[10px] font-medium leading-none text-white">
          {count > 9 ? "9+" : count}
        </span>
      ) : null}
    </Link>
  );
}
