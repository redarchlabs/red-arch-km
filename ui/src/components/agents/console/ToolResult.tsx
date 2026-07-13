import { AlertCircle, Clock } from "lucide-react";

import { batchStatus, extractSources, resultAnswer, resultError } from "@/lib/agents/tool-results";

import { SourceCitations } from "./SourceCitations";

interface ToolResultProps {
  name: string;
  result: Record<string, unknown>;
}

/**
 * Render a completed tool result. Known shapes get first-class, human-readable
 * surfaces — an error banner, `web_research` answer + cited sources, or a batch
 * status. Every other tool falls back to the raw JSON it always showed, so no
 * existing tool loses information.
 */
export function ToolResult({ name, result }: ToolResultProps) {
  const error = resultError(result);
  if (error) {
    return (
      <div className="mt-1 flex items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{error}</span>
      </div>
    );
  }

  if (name === "web_research") {
    const answer = resultAnswer(result);
    const sources = extractSources(result);
    return (
      <div className="mt-1 space-y-2">
        {answer ? <p className="whitespace-pre-wrap text-xs text-foreground">{answer}</p> : null}
        <SourceCitations sources={sources} />
      </div>
    );
  }

  const batch = batchStatus(name, result);
  if (batch?.kind === "done") {
    return (
      <p className="mt-1 whitespace-pre-wrap text-xs text-foreground">{batch.text || "(no output)"}</p>
    );
  }
  if (batch?.kind === "processing") {
    return (
      <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Clock className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>Batch still processing…</span>
        {batch.batchId ? (
          <code className="rounded bg-muted px-1 py-0.5 text-[11px]">{batch.batchId}</code>
        ) : null}
      </div>
    );
  }

  return (
    <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify(result, null, 2)}</pre>
  );
}
