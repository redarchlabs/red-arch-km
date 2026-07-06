# Knowledge Engine — Design Spec

> **Status:** in build (greenfield — no corpus ingested yet).
> **Scope:** replaces the current top-K "sprinkle" knowledge graph with an
> agentic **fact engine**: a reified-claim, bi-temporal, provenance-tracked
> knowledge store plus an agentic tool-loop chat that reasons its way to
> grounded, cited answers instead of running a single vector query.
>
> **Out of scope / owned elsewhere:** the Workflow Engine + Custom Entities
> feature (`cryptic-zephyr`) is built by a separate agent. This engine is kept
> self-contained so the two can be integrated later (the agent's tool surface
> is the natural seam).

---

## 1. Goals

The product requirement is **"facts as searchable truth, not top-K."** That
decomposes into five properties top-K similarity retrieval structurally cannot
provide:

| Property | Why top-K fails | How we provide it |
|---|---|---|
| **Deterministic recall** | the one chunk with the fact may miss the top-K neighborhood | structured claim lookup by (subject, predicate) |
| **Canonical / deduplicated** | "IBM" / "I.B.M." look distinct; a fact stated 50× looks like 50 facts | entity + predicate resolution at ingest; claim dedup by key |
| **Provenance** | a passage has no per-fact source trail | every claim carries `SOURCED_FROM` edges to chunks/documents |
| **Structurally queryable** | "list all X where Y", counts, joins are impossible on embeddings | Cypher over reified claims |
| **Temporally coherent** | conflicting/updated docs both surface, unranked | bi-temporal validity + supersession + contradiction status |

Plus: **agentic retrieval** — the chat backend is a tool-using loop, not one
retrieval call.

## 2. Architectural inversion

Today the graph is a decoration bolted onto vector RAG. Here the **canonical
fact store is the spine**; vector search becomes one *tool* the agent uses for
passage grounding and provenance.

```
                 ┌────────────────── Ingest (manufacture truth) ──────────────────┐
  document ──▶ chunk ──▶ embed ──▶ Qdrant (passages)
                     │
                     └▶ extract claims ──▶ resolve entities+predicates ──▶ reconcile ──▶ Neo4j
                        (schema-guided)     (blocking+ANN+LLM)             (bi-temporal)  (fact store)

                 ┌────────────────── Query (agentic) ─────────────────────────────┐
  question ──▶ router ──▶ ReAct loop over tools ──▶ verify (ground every claim) ──▶ compose + cite
                          {vector_search, entity_lookup, claim_query,
                           neighborhood, read_source}   ── iteration budget ──┘
                                        │
                                        └── SSE trace to UI (searching… verifying…)
```

### Stores (KM2 already runs all three)
- **Postgres** — system of record for documents, orgs, membership, flags. No
  fact tables here; the graph lives entirely in Neo4j.
- **Qdrant** — passage + document vectors (unchanged; still the grounding tool).
- **Neo4j** — the **fact store**: canonical entities, reified claims,
  provenance, temporal state. Also holds an entity **vector index** (native in
  Neo4j 5) for resolution + semantic entry, and a **full-text index** for
  lexical entity lookup.

## 3. The knowledge model

The single most important, hardest-to-change decision: **claims are reified as
nodes, not edges.** A plain `(s)-[:REL]->(o)` edge can't carry multi-source
provenance, be versioned, be contradicted, or be timestamped. A `:Claim` node
can.

```
(s:Entity)-[:SUBJECT]->(c:Claim)-[:OBJECT]->(o:Entity)      # entity-valued object
(c:Claim {object_value})                                     # literal-valued object
(c:Claim)-[:SOURCED_FROM]->(:Chunk)-[:PART_OF]->(:Document)  # provenance
(c1:Claim)-[:SUPERSEDES]->(c2:Claim)                         # temporal update
(c1:Claim)-[:CONTRADICTS]->(c2:Claim)                        # unresolved conflict
```

- **Entity** — `entity_id` (globally unique hash of tenant+type+canonical name),
  `canonical_name`, `type` (from a type vocabulary), `aliases[]`, `embedding`,
  tenant label. Surface forms collapse to one node via resolution.
- **Claim** — `claim_id`, `predicate` (canonical), `object_value`/`object_type`
  (literal) *or* an `OBJECT` edge (entity), `valid_from`/`valid_to` (world time),
  `recorded_at` (ingest time), `status` ∈ {active, superseded, contradicted,
  retracted}, `confidence`, `corroborations`, `dedup_key`, `access_keys[]`,
  `tags[]`, tenant label.
- **Provenance** — a `SOURCED_FROM` edge per supporting source, carrying
  `text_span` (the sentence), `extractor_model`, `extracted_at`, `confidence`.

**Bi-temporal from day one.** `valid_from/valid_to` = when the fact is true in
the world; `recorded_at` = when we learned it; `status` tracks lifecycle. This
is what earns the word "truth" and is a painful retrofit — free on an empty DB.

### Predicate ontology
Free-form extraction produces `reports_to` / `is managed by` / `works under` as
three relationships. We resolve predicates against a controlled vocabulary
(`facts/predicates.py`). Each predicate declares a **cardinality**:
- `functional` — one current value per subject (e.g. `date_of_birth`,
  `headquartered_in`). A new differing value **supersedes** the old.
- `multi` — many values allowed (e.g. `authored`, `mentions`). New values are
  additive; repeats **corroborate** (bump confidence + provenance).

### Tenant isolation
Every node carries `:Tenant_<org_id>` (matching the existing convention).
**All** query tools inject the tenant label server-side — never trust an LLM to
add the filter (see [[km2-org-scoping-two-layers]]). Text-to-Cypher, if used, is
constrained: read-only, tenant-scoped, parameterized, timeout-bounded.

## 4. Ingest — manufacturing truth

Idempotent, keyed on `document_key + content_hash` (fixes the current
append-duplicates behaviour, [[km2-ingest-not-idempotent]]): re-ingesting a doc
first purges its prior claims/provenance, so re-processing converges instead of
piling up.

1. **Chunk + embed** (existing).
2. **Extract claims** — schema-guided LLM call constrained to the predicate
   vocabulary; each candidate carries its supporting `text_span` + chunk id.
3. **Resolve** — subject/object mentions → canonical entities (embed → ANN
   blocking → threshold, LLM adjudicates the ambiguous band); predicates →
   canonical vocabulary.
4. **Reconcile** — compute `dedup_key`; MERGE claim. New → create; repeat →
   corroborate; functional-predicate conflict → supersede prior (set
   `valid_to`, status) and link `SUPERSEDES`; simultaneous conflict → mark both
   `contradicted` + `CONTRADICTS`. This decision logic is **pure**
   (`facts/reconciliation.py`) and unit-tested independently of Cypher.
5. **Wire provenance** — `SOURCED_FROM` edge to the chunk.

## 5. Query — the agentic engine

The chat backend becomes a **tool-using ReAct loop**, adaptive by question
complexity.

### Tools (each tenant-scoped server-side)
| Tool | Store | For |
|---|---|---|
| `vector_search(q, filters)` | Qdrant | passage-grounded "what does the doc say…" |
| `entity_lookup(name)` | Neo4j | resolve mention → canonical entity + its claims |
| `claim_query(subject?, predicate?, object?, as_of?)` | Neo4j | exact + **aggregative** ("list all X where Y", counts) |
| `neighborhood(entity, hops≤2)` | Neo4j | relational / multi-hop |
| `read_source(chunk_id)` | Qdrant/PG | fetch full source to verify + quote |

### Loop
1. **Router** classifies the question (lookup / relational / aggregative /
   thematic) and sizes the effort — trivial lookups short-circuit to one tool
   call; complex questions get the full loop. Every loop is bounded by a
   **max-iteration budget** (predictable latency + cost).
2. **Plan / decompose** into sub-questions.
3. **Act / observe** — pick tools, inspect results, detect gaps/contradictions.
4. **Refine** — reformulate, try another tool, expand a hop, until confidence or
   budget.
5. **Verify** — for each claim in the draft answer, confirm a retrieved
   fact/chunk supports it; drop or flag unsupported claims. *This is what makes
   it truth, not confident guessing.*
6. **Compose** — synthesize with inline provenance; shape output (tables for
   aggregative answers).

### Streaming
Emit the agent trace over the existing SSE channel
(`plan` / `tool_call` / `tool_result` / `verify` / `delta` / `done`) so the
multi-second loop reads as thoroughness and hands the user a provenance trail.

## 6. Truth & trust mechanisms
- **Provenance everywhere** — every claim traces to source spans; answers cite.
- **Contradiction surfacing** — conflicts are first-class (`contradicted`
  status + `CONTRADICTS`), shown to the user rather than silently resolved.
- **Temporal `as_of`** — claim queries accept a point in time; supersession
  keeps history queryable.
- **Confidence + corroboration** — claims rank by source count and extractor
  confidence; low-confidence facts are flagged.

## 7. Digest layer (GraphRAG-style, later)
The direct analog of Claude's `MEMORY.md`: a cheap, always-available orientation
layer — per-entity summary cards + community summaries — the agent loads first
before diving into retrieval. Derived/regenerable (no schema lock-in), so it is
built last.

## 8. Build order (vertical slices)
1. **Fact-store foundation** — models, predicate registry, reconciliation logic,
   `FactStore` protocol, Neo4j schema (constraints + vector/full-text indexes),
   reified-claim Neo4j store. ← *this slice*
2. **Resolution** — entity + predicate resolver.
3. **Extraction** — schema-guided claim extractor.
4. **Ingest wiring** — idempotent, content-hash keyed, into `IngestService`.
5. **Agentic query engine** — tools, router, loop, verification.
6. **API/SSE** — `brain_api` routers + `services/api` gateway + trace events.
7. **Digest layer.**
8. **UI** — trace, citations, contradiction surfacing.
9. **Eval harness** — groundedness, retrieval quality, answer correctness.

## 9. Security & multi-tenancy invariants (must hold throughout)
- Tenant label injected server-side on **every** tool/query; LLM never supplies it.
- Fact-store queries are read-only from the agent's perspective; writes happen
  only through the ingest path.
- `access_keys[]` on claims enforce per-document RBAC in retrieval, mirroring the
  vector store.
- No secrets in payloads; per-org LLM keys resolved server-side.
```

The old `brain_sdk/graph_store/` (flat triplets) stays until the new engine is
wired end-to-end, then is removed.
