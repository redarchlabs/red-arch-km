"use client";

/**
 * In-app slide deck — renders a module's content as a navigable presentation
 * (prev/next, clickable progress dots) instead of a wall of scrolling text.
 * A real stateful component (not an inline node fn) so the current-slide index
 * survives FormRenderer re-renders. Slide bodies are Markdown.
 *
 * A slide may carry a `video_url` (a direct mp4/webm). When `require_video` is
 * set (the default), the deck GATES forward navigation until the learner has
 * watched that video through — the "Next"/forward controls stay disabled, and
 * seeking ahead of the furthest-played point is prevented so it can't be skipped.
 */
import { ChevronLeft, ChevronRight, Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import type { Slide } from "@/lib/api/forms";

/** True when a slide has a video that must be watched before advancing. */
function gatesOn(slide: Slide | undefined): boolean {
  return Boolean(slide?.video_url) && slide?.require_video !== false;
}

export function SlideDeck({ slides, label }: { slides: Slide[]; label?: string | null }) {
  const [index, setIndex] = useState(0);
  // Indices whose required video has been watched through (gate satisfied).
  const [watched, setWatched] = useState<Set<number>>(() => new Set());
  // Furthest playback point reached on the CURRENT slide's video — used to block
  // seeking ahead (skipping). Reset whenever the visible slide changes.
  const maxTimeRef = useRef(0);

  const count = slides.length;
  const i = count ? Math.min(Math.max(index, 0), count - 1) : 0;

  useEffect(() => {
    maxTimeRef.current = 0;
  }, [i]);

  if (!count) {
    return (
      <div className="rounded-lg border p-6 text-sm text-muted-foreground">
        No slides yet.
      </div>
    );
  }

  const slide = slides[i];
  const gated = gatesOn(slide) && !watched.has(i);

  const markWatched = () =>
    setWatched((prev) => {
      if (prev.has(i)) return prev;
      const next = new Set(prev);
      next.add(i);
      return next;
    });

  const go = (target: number) => {
    const clamped = Math.min(Math.max(target, 0), count - 1);
    // Backward is always free; forward is blocked while the current video gates.
    if (clamped > i && gated) return;
    setIndex(clamped);
  };

  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      {label ? <div className="border-b px-4 py-2 text-sm font-medium">{label}</div> : null}

      <div className="flex min-h-[20rem] flex-col gap-4 p-6">
        {slide.title ? <h2 className="text-xl font-semibold">{slide.title}</h2> : null}

        {slide.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- slide images are author/agent-supplied URLs or data URIs, not build-time assets
          <img
            src={slide.image_url}
            alt={slide.title ?? ""}
            className="max-h-64 w-auto self-center rounded-md object-contain"
          />
        ) : null}

        {slide.video_url ? (
          <div className="space-y-1">
            <video
              key={`v-${i}`}
              src={slide.video_url}
              controls
              controlsList="nodownload"
              className="max-h-72 w-full rounded-md bg-black"
              onEnded={markWatched}
              // A broken/unplayable source shouldn't trap the learner forever.
              onError={markWatched}
              onTimeUpdate={(e) => {
                const t = e.currentTarget.currentTime;
                if (t > maxTimeRef.current) maxTimeRef.current = t;
              }}
              onSeeking={(e) => {
                // No-skip: while the gate is active, snap any forward seek back to
                // the furthest point actually watched (a small epsilon avoids loops).
                if (!gatesOn(slide) || watched.has(i)) return;
                const v = e.currentTarget;
                if (v.currentTime > maxTimeRef.current + 0.5) v.currentTime = maxTimeRef.current;
              }}
            />
            {gated ? (
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Lock className="h-3 w-3" /> Finish the video to continue.
              </p>
            ) : null}
          </div>
        ) : null}

        {slide.body ? (
          <div className="max-w-none text-sm leading-relaxed">
            <Markdown content={slide.body} />
          </div>
        ) : null}
      </div>

      <div className="flex items-center justify-between border-t px-4 py-2">
        <button
          type="button"
          onClick={() => go(i - 1)}
          disabled={i === 0}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          <ChevronLeft className="h-4 w-4" /> Prev
        </button>

        <div className="flex items-center gap-1.5">
          {slides.map((_, di) => {
            // A forward dot is locked while the current slide's video gates.
            const locked = di > i && gated;
            return (
              <button
                key={di}
                type="button"
                aria-label={`Go to slide ${di + 1}`}
                aria-current={di === i}
                disabled={locked}
                onClick={() => go(di)}
                className={`h-2 w-2 rounded-full transition-colors disabled:cursor-not-allowed ${
                  di === i
                    ? "bg-primary"
                    : locked
                      ? "bg-muted-foreground/20"
                      : "bg-muted-foreground/30 hover:bg-muted-foreground/60"
                }`}
              />
            );
          })}
          <span className="ml-2 text-xs tabular-nums text-muted-foreground">
            {i + 1} / {count}
          </span>
        </div>

        <button
          type="button"
          onClick={() => go(i + 1)}
          disabled={i === count - 1 || gated}
          title={gated ? "Finish the video to continue" : undefined}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          {gated ? <Lock className="h-4 w-4" /> : null}
          Next <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

/** Coerce a bound JSON field value (array, JSON string, or null) into a Slide[]. */
export function coerceSlides(raw: unknown): Slide[] {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return [];
    }
  }
  if (!Array.isArray(value)) return [];
  return value.filter((s): s is Slide => typeof s === "object" && s !== null);
}
