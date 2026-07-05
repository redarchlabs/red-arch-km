"use client";

import DOMPurify from "dompurify";
import { Loader2, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { type SearchHit, searchDocuments } from "@/lib/api/search";

/** How many ranked hits to request. Semantic search is top-K by relevance, so
 * we fetch a generous single page rather than offset-paginating. */
const RESULT_LIMIT = 25;

/** Strip any HTML from result snippets — chunk text may contain stray markup. */
function sanitize(text: string): string {
  return DOMPurify.sanitize(text, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
}

export default function DocumentSearchPage() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  const runSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    try {
      const res = await searchDocuments(q, RESULT_LIMIT);
      setHits(res.hits);
      setSearched(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Search failed");
      setHits([]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Search documents</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Semantic search across your knowledge base — ask a question or enter terms.
        </p>
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void runSearch();
        }}
      >
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Enter a search question, statement, or term…"
          aria-label="Search query"
          autoFocus
        />
        <Button type="submit" disabled={!query.trim() || loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Search
        </Button>
      </form>

      {error ? (
        <Card className="border-destructive">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      {searched && !loading && hits.length === 0 && !error ? (
        <p className="text-sm text-muted-foreground">No matching documents found.</p>
      ) : null}

      <ol className="space-y-3">
        {hits.map((hit, index) => (
          <li key={hit.id}>
            <Link href={`/documents/${hit.document_key}`} className="block">
              <Card className="transition-colors hover:border-primary/50">
                <CardContent className="space-y-1.5 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate font-medium">
                      {index + 1}. {hit.document_title || "Untitled document"}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      {(hit.score * 100).toFixed(1)}% match
                    </span>
                  </div>
                  <p className="line-clamp-3 text-sm text-muted-foreground">
                    {sanitize(hit.text)}
                  </p>
                  <span className="text-xs text-muted-foreground">Section #{hit.chunk_order}</span>
                </CardContent>
              </Card>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}
