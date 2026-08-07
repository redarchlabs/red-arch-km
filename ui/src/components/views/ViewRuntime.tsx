"use client";

import { ArrowLeft, Maximize, Minimize, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { FormRenderer } from "@/components/forms/FormRenderer";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { getApiErrorMessage } from "@/lib/api/errors";
import type { FormRender, OrgBranding } from "@/lib/api/forms";
import { getPublicViewRender, getViewRender, runPublicViewWorkflow } from "@/lib/api/views";
import { runWorkflow } from "@/lib/api/workflows";

/** Logos are <img> sources, not axios calls, so they need the absolute API base. */
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

interface ViewRuntimeProps {
  /** The view's id. Ignored when `token` is set — a shared page is identified
   * only by its token, and never learns the internal id. */
  id: string;
  /**
   * Present the view as a KIOSK: the app chrome is gone (the authenticated
   * layout drops the nav rail, header and help dock on this route), the card
   * frame is gone, and the element tree gets the whole screen. This is the mode
   * a shared tablet runs in — a crew station, a check-in pad, a wall display —
   * where the person using it is doing one task and every pixel of KM2's
   * authoring UI is a distraction (or a way out of the app).
   */
  kiosk?: boolean;
  /**
   * Anonymous share token. When set, every request goes through the public
   * endpoints instead of the authenticated ones: the render is pinned to the
   * record the link was created for, and workflow runs are limited server-side
   * to the ones this view's own element tree references. Implies kiosk.
   */
  token?: string;
}

/** Runtime viewer: renders a view through the shared `FormRenderer`. Buttons run
 * workflows or navigate; embedded forms render inline. Shared by the normal
 * in-app viewer and the chrome-free kiosk route. */
export function ViewRuntime({ id, kiosk = false, token }: ViewRuntimeProps) {
  // An entity-bound view can target a specific record via `?record_id=` — its
  // fields prefill, and run_workflow buttons run against that record (so an
  // update_record/update_record_field step writes it).
  // A shared page ignores this entirely: its record is pinned server-side, so a
  // visitor cannot point the link at a different row by editing the URL.
  const queryRecordId = useSearchParams().get("record_id") ?? undefined;
  const recordId = token ? undefined : queryRecordId;
  const shared = !!token;
  // Present for a signed-in kiosk; null for an anonymous visitor (the provider
  // lives in the root layout, so this is safe on the shared route too).
  const { currentOrg } = useOrg();
  const fetchRender = useCallback(
    () => (token ? getPublicViewRender(token) : getViewRender(id, recordId)),
    [token, id, recordId],
  );
  const router = useRouter();
  const [render, setRender] = useState<FormRender | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Serialized form of the last render we committed to state. Kiosk pages poll
  // for hours; when a tick returns identical data, skipping setRender keeps the
  // whole element tree from re-rendering for nothing.
  const lastRenderJson = useRef<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchRender();
      lastRenderJson.current = JSON.stringify(next);
      setRender(next);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load view"));
    } finally {
      setLoading(false);
    }
  }, [fetchRender]);

  useEffect(() => {
    void load();
  }, [load]);

  // Live view: a `refresh_ms` on the view's config re-fetches the render on that
  // cadence, so record-bound elements (progress bars, calculated values, a
  // state-driven image, `visible_when` gates, section fields) follow the record as
  // workflows change it — the page-level counterpart to the per-element `poll_ms`.
  // Deliberately silent: it never toggles the loading skeleton, a failed refresh
  // keeps the last good render on screen, and a hidden tab skips the fetch. The
  // renderer preserves values the viewer has edited (see FormRenderer).
  const refreshMs = render?.config.refresh_ms ?? null;
  useEffect(() => {
    if (!refreshMs) return;
    let alive = true;
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      void fetchRender()
        .then((next) => {
          if (!alive) return;
          const json = JSON.stringify(next);
          if (json === lastRenderJson.current) return; // unchanged — no re-render
          lastRenderJson.current = json;
          setRender(next);
        })
        .catch(() => {
          /* keep the last good render */
        });
    }, Math.max(1000, refreshMs));
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [refreshMs, fetchRender]);

  // Return to wherever the user came from — the dashboard/view whose button
  // opened this one, or the view editor if they launched the runtime viewer
  // from there. (Previously this hard-linked to `/views/${id}`, which always
  // dumped end users into this view's *editor*.) Fall back to Home for a direct
  // deep-link with no in-app history to go back to.
  const handleBack = useCallback(() => {
    if (window.history.length > 1) router.back();
    else router.push("/home");
  }, [router]);

  const handleRunWorkflow = async (
    workflowId: string,
    inputs: Record<string, unknown>,
    rowRecordId?: string,
  ) => {
    setNotice(null);
    setError(null);
    if (token) {
      // Shared page: no record id is sent. The server pins the record and rejects
      // any workflow this view doesn't reference, so nothing here widens access.
      try {
        await runPublicViewWorkflow(token, workflowId, { inputs, after: inputs });
        setNotice("Sent.");
      } catch (e: unknown) {
        setError(getApiErrorMessage(e, "Workflow failed to start"));
      }
      return;
    }
    try {
      // Target the row's record (record-list action), else the page's record
      // (`?record_id=`), else no record — an ad-hoc "run now". The run endpoint
      // needs a CRUD operation ("update" is the default); button `inputs` ride
      // along as `after` for workflows that reference them.
      // `me` and `latest` are RENDER-time sentinels (the server binds the view to the
      // caller's own record, or to the newest one); the run endpoint only accepts a
      // UUID, so we never forward either. Instead we use the id the render RESOLVED it
      // to (`render.record_id`) so an entity-triggered button on such a view still runs
      // against the right record; a manual workflow ignores record_id anyway.
      const isSentinel = recordId === "me" || recordId === "latest";
      const pageRecordId = isSentinel ? (render?.record_id ?? null) : recordId;
      const target = rowRecordId ?? pageRecordId ?? null;
      // Pass the button values as BOTH `after` (entity-triggered workflows that
      // reference after.*) and `inputs` (manual-trigger workflows whose declared
      // inputs read inputs.*). The backend routes to the right one by trigger
      // type and drops undeclared keys, so sending both is safe.
      await runWorkflow(workflowId, { operation: "update", record_id: target, after: inputs, inputs });
      setNotice("Workflow started.");
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Workflow failed to start"));
    }
  };

  // Branding source differs by mode. A shared page has no session, so its org
  // identity rides in the render payload (and only when the link opted in); an
  // authenticated kiosk already knows its org, so it reads the context.
  const branding: OrgBranding | null = shared
    ? (render?.branding ?? null)
    : kiosk && currentOrg
      ? {
          org_name: currentOrg.name,
          accent_color: currentOrg.accent_color ?? null,
          has_logo: currentOrg.has_logo ?? false,
        }
      : null;
  const logoSrc = !branding?.has_logo
    ? null
    : shared
      ? `${API_BASE}/public/views/${token}/branding/logo`
      : `${API_BASE}/orgs/${currentOrg?.id}/settings/logo`;

  if (loading) return <Skeleton className={kiosk || shared ? "h-screen w-full" : "h-96 w-full"} />;
  if (!render)
    return (
      <p className="p-6 text-center text-sm text-destructive">
        {error ?? (shared ? "This link is not available." : "View not found.")}
      </p>
    );

  if (kiosk || shared) {
    // Full-bleed: no card, no page padding, no title. The view's own element tree
    // is the entire screen. Errors/notices float rather than reflowing the layout,
    // so a workflow failure never shifts a control out from under a finger.
    return (
      <div
        className="relative min-h-screen w-full bg-background text-foreground"
        // A pinned theme scopes to this wrapper — never <html>, and never written
        // to the visitor's stored preference. A shared link opens in a stranger's
        // browser; inheriting whatever theme it last used is exactly the wrong
        // default for a page whose look was designed.
        data-theme={render.config.theme ?? undefined}
        // The org accent overrides the theme's primary for this subtree only.
        style={
          branding?.accent_color
            ? ({ "--color-primary": branding.accent_color } as React.CSSProperties)
            : undefined
        }
      >
        {/* A shared page gets no way "back into the app" — there is no app for
            an anonymous visitor to return to, and offering one only invites a
            confusing sign-in wall. It keeps the fullscreen toggle. */}
        <KioskControls id={id} recordId={recordId} exitHref={shared ? null : undefined} />
        {branding ? <BrandHeader branding={branding} logoSrc={logoSrc} /> : null}
        {error || notice ? (
          <div className="pointer-events-none fixed inset-x-0 top-0 z-40 flex justify-center p-2">
            <p
              className={`rounded-full px-4 py-1.5 text-sm font-medium shadow ${
                error ? "bg-destructive text-destructive-foreground" : "bg-green-600 text-white"
              }`}
            >
              {error ?? notice}
            </p>
          </div>
        ) : null}
        <div className={KIOSK_PADDING[render.config.padding ?? "none"]}>
          <FormRenderer render={render} mode="fill" viewContext onRunWorkflow={handleRunWorkflow} />
        </div>
      </div>
    );
  }

  return (
    // `max-w`: on an ultrawide monitor an unconstrained view typesets its text
    // and stretches its tables edge to edge, which is what actually hurts.
    <div className="mx-auto max-w-7xl space-y-6">
      {/* No page title here: each view supplies its own heading in its element
          tree, so echoing the internal view name (e.g. "Course Player") above it
          is redundant. Keep only a back affordance. */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleBack}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
        <Link
          href={`/views/${id}/kiosk${recordId ? `?record_id=${encodeURIComponent(recordId)}` : ""}`}
          className="ml-auto inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          title="Present this view full screen, with no navigation — for a tablet or wall display"
        >
          <Maximize className="h-4 w-4" /> Present
        </Link>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {notice ? <p className="text-sm text-success">{notice}</p> : null}
      {/* No outer Card: data elements now carry their own card frames, and a
          card-inside-a-card double border read as clutter. */}
      <FormRenderer render={render} mode="fill" viewContext onRunWorkflow={handleRunWorkflow} />
    </div>
  );
}

/** The slim identity strip a branded kiosk / shared page carries.
 *
 * Deliberately small and quiet: it says whose page this is, and then gets out of
 * the way of the view — which is the actual content, often on a wall display. */
function BrandHeader({ branding, logoSrc }: { branding: OrgBranding; logoSrc: string | null }) {
  return (
    <header className="flex items-center gap-3 border-b bg-background/80 px-4 py-2.5 backdrop-blur">
      {logoSrc ? (
        /* Served from the API (token-scoped on a shared page), so next/image's
           build-time domain allow-list can't cover it. */
        // eslint-disable-next-line @next/next/no-img-element
        <img src={logoSrc} alt="" className="h-7 w-auto max-w-40 object-contain" />
      ) : null}
      <span className="truncate text-sm font-semibold">{branding.org_name}</span>
    </header>
  );
}

/** Inset for the element tree on the chrome-free routes. Full-bleed is the default
 * because a control surface meant to fill a screen (a crew station, a puzzle pad) was
 * designed against the edges; a page of prose needs the opposite, or it is typeset hard
 * against the bezel with nowhere for the eye to rest. `max-w` matters as much as the
 * inset: text running the full width of a wall display is what actually hurts to read. */
/** Insets are per-breakpoint, not flat: the same `px-12` that gives a wall display
 * room to breathe eats a sixth of a phone's width, and these pages are opened on
 * both. Small screens get an inset that still reads as margin and no more. */
const KIOSK_PADDING: Record<"none" | "comfortable" | "spacious", string> = {
  none: "",
  comfortable: "mx-auto max-w-5xl px-4 py-6 sm:px-8 sm:py-10",
  spacious: "mx-auto max-w-4xl px-5 py-8 sm:px-12 sm:py-16",
};

/** The only chrome a kiosk keeps: a way back into the app and a browser-fullscreen
 * toggle. Deliberately faint and in the corner — visible to an adult who goes
 * looking, easy for a child to ignore. */
function KioskControls({
  id,
  recordId,
  exitHref,
}: {
  id: string;
  recordId?: string;
  /** `null` hides the exit affordance entirely (a shared, anonymous page). */
  exitHref?: string | null;
}) {
  const [isFull, setIsFull] = useState(false);
  // The Fullscreen API is absent on iPhone Safari and gated on some tablets; the
  // toggle simply doesn't render there (an "Add to Home Screen" shortcut is the
  // real full-screen story on iPad).
  const [supported, setSupported] = useState(false);

  useEffect(() => {
    setSupported(typeof document !== "undefined" && !!document.documentElement.requestFullscreen);
    const onChange = () => setIsFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFull = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void document.documentElement.requestFullscreen().catch(() => undefined);
  };

  return (
    <div className="absolute right-2 top-2 z-50 flex items-center gap-1 opacity-30 transition-opacity hover:opacity-100 focus-within:opacity-100">
      {supported ? (
        <button
          type="button"
          onClick={toggleFull}
          aria-label={isFull ? "Exit full screen" : "Enter full screen"}
          className="rounded-full p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          {isFull ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
        </button>
      ) : null}
      {exitHref === null ? null : (
        <Link
          href={exitHref ?? `/views/${id}/view${recordId ? `?record_id=${encodeURIComponent(recordId)}` : ""}`}
          aria-label="Leave kiosk mode"
          className="rounded-full p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </Link>
      )}
    </div>
  );
}
