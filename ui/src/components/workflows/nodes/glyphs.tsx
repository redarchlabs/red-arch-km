/**
 * Inline SVG BPMN markers drawn on the canvas — the small symbol inside a
 * gateway diamond or an event circle. They use `currentColor`, so the parent's
 * accent text colour drives the stroke/fill. Palette and task-corner icons keep
 * using lucide (see nodeMeta `icon` / `TASK_ICONS`); these cover the shapes
 * lucide doesn't express as cleanly (gateway X/+/O/pentagon, event-type marks).
 */
import type { EventType, GatewayType } from "./nodeMeta";

interface GlyphProps {
  className?: string;
}

const SVG = 24;

function Svg({ className, children }: GlyphProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox={`0 0 ${SVG} ${SVG}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

// --------------------------------------------------------------------------- //
// Gateway markers
// --------------------------------------------------------------------------- //
function ExclusiveGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <path d="M7 7l10 10M17 7L7 17" />
    </Svg>
  );
}

function ParallelGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  );
}

function InclusiveGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <circle cx="12" cy="12" r="6" />
    </Svg>
  );
}

function EventBasedGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 6l4.7 3.4-1.8 5.5H9.1L7.3 9.4z" />
    </Svg>
  );
}

const GATEWAY_GLYPHS: Record<GatewayType, (p: GlyphProps) => React.ReactElement> = {
  exclusive: ExclusiveGlyph,
  parallel: ParallelGlyph,
  inclusive: InclusiveGlyph,
  event_based: EventBasedGlyph,
};

export function GatewayGlyph({ type, className }: { type: GatewayType } & GlyphProps) {
  const Glyph = GATEWAY_GLYPHS[type] ?? ExclusiveGlyph;
  return <Glyph className={className} />;
}

// --------------------------------------------------------------------------- //
// Event markers (by event_type)
// --------------------------------------------------------------------------- //
function TimerGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4l2.5 2.5" />
    </Svg>
  );
}

function MessageGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <rect x="5" y="7" width="14" height="10" rx="1" />
      <path d="M5 8l7 5 7-5" />
    </Svg>
  );
}

function SignalGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <path d="M12 5l7 12H5z" />
    </Svg>
  );
}

function ErrorGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <path d="M6 18l3.5-9 3 5 2-6L18 6" />
    </Svg>
  );
}

function EscalationGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <path d="M12 6l5 11-5-4-5 4z" />
    </Svg>
  );
}

function TerminateGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <circle cx="12" cy="12" r="7" fill="currentColor" stroke="none" />
    </Svg>
  );
}

function NoneGlyph({ className }: GlyphProps) {
  return (
    <Svg className={className}>
      <circle cx="12" cy="12" r="7" />
    </Svg>
  );
}

const EVENT_GLYPHS: Record<EventType, (p: GlyphProps) => React.ReactElement> = {
  timer: TimerGlyph,
  message: MessageGlyph,
  signal: SignalGlyph,
  error: ErrorGlyph,
  escalation: EscalationGlyph,
  terminate: TerminateGlyph,
  none: NoneGlyph,
};

export function EventGlyph({ type, className }: { type: EventType } & GlyphProps) {
  const Glyph = EVENT_GLYPHS[type] ?? NoneGlyph;
  return <Glyph className={className} />;
}
