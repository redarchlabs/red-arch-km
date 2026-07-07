"use client";

import { useEffect, useMemo, useState } from "react";

import type { NodeChrome } from "@/components/workflows/nodes/BaseNode";
import { chromeFromNodeStatuses, nodeStatusesFromSteps } from "@/components/workflows/runOverlay";
import { listRunSteps } from "@/lib/api/workflows";
import { streamRun } from "@/lib/api/runStream";

const POLL_MS = 2500;

/**
 * Live per-node status for the run overlay. Prefers the SSE stream
 * (GET /workflows/runs/{runId}/stream); on any stream failure it falls back to
 * polling run steps every {@link POLL_MS}ms (the same cadence as RunMonitor).
 * Returns chrome keyed by node id (feeds NodeChromeContext) + the run status and
 * whether the live stream is connected.
 */
export function useRunStream(
  runId: string | null,
  active = true,
): { chrome: Record<string, NodeChrome>; runStatus: string | null; live: boolean } {
  const [nodes, setNodes] = useState<Record<string, string>>({});
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    if (!active || !runId) return;
    let cancelled = false;
    const controller = new AbortController();
    let pollTimer: ReturnType<typeof setInterval> | null = null;

    const startPolling = () => {
      setLive(false);
      if (pollTimer) return;
      const tick = async () => {
        try {
          const steps = await listRunSteps(runId);
          if (cancelled) return;
          setNodes(nodeStatusesFromSteps(steps.map((s) => ({ node_id: s.node_id, status: s.status })), []));
        } catch {
          // Transient; the next tick retries.
        }
      };
      void tick();
      pollTimer = setInterval(() => void tick(), POLL_MS);
    };

    const runStream = async () => {
      try {
        for await (const ev of streamRun(runId, { signal: controller.signal })) {
          if (cancelled) return;
          if (ev.type === "snapshot") {
            setLive(true);
            setNodes(ev.snapshot.nodes);
            setRunStatus(ev.snapshot.run.status);
          } else if (ev.type === "done") {
            setLive(false);
            return;
          } else if (ev.type === "error") {
            startPolling();
            return;
          }
        }
        if (!cancelled) startPolling(); // stream closed without a terminal event
      } catch {
        if (!cancelled) startPolling();
      }
    };

    void runStream();
    return () => {
      cancelled = true;
      controller.abort();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [runId, active]);

  const chrome = useMemo(() => chromeFromNodeStatuses(nodes), [nodes]);
  return { chrome, runStatus, live };
}
