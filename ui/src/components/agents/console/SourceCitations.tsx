import { ExternalLink, Globe } from "lucide-react";

import { hostnameOf, type WebResearchSource } from "@/lib/agents/tool-results";
import { cn } from "@/lib/utils";

interface SourceCitationsProps {
  sources: WebResearchSource[];
  className?: string;
}

/**
 * A numbered, accessible list of grounded citations for a `web_research` result.
 * Each source with a safe URL renders as a whole-card external link; unsafe or
 * missing URLs degrade gracefully to static text (see `safeExternalHref`).
 */
export function SourceCitations({ sources, className }: SourceCitationsProps) {
  if (sources.length === 0) return null;
  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        <Globe className="h-3 w-3" aria-hidden />
        <span>
          {sources.length} source{sources.length === 1 ? "" : "s"}
        </span>
      </div>
      <ol className="space-y-1.5">
        {sources.map((source, index) => (
          <SourceItem key={index} index={index + 1} source={source} />
        ))}
      </ol>
    </div>
  );
}

const ITEM_CLASS = "flex gap-2 rounded-md border bg-background/60 p-2 text-xs";

function SourceItem({ index, source }: { index: number; source: WebResearchSource }) {
  const host = source.url ? hostnameOf(source.url) : null;
  const label = source.title ?? host ?? source.url ?? "Source";

  const body = (
    <>
      <span
        aria-hidden
        className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded bg-muted text-[10px] font-semibold tabular-nums text-muted-foreground"
      >
        {index}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1 font-medium text-foreground">
          <span className="truncate">{label}</span>
          {source.url ? <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden /> : null}
        </span>
        {host ? <span className="block truncate text-[11px] text-muted-foreground">{host}</span> : null}
        {source.snippet ? (
          <span className="mt-0.5 block text-muted-foreground/90 line-clamp-2">{source.snippet}</span>
        ) : null}
      </span>
    </>
  );

  if (source.url) {
    return (
      <li>
        <a
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open source ${index}: ${label} (opens in a new tab)`}
          className={cn(
            ITEM_CLASS,
            "transition-colors hover:border-primary/50 hover:bg-muted/60",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          {body}
        </a>
      </li>
    );
  }

  return <li className={ITEM_CLASS}>{body}</li>;
}
