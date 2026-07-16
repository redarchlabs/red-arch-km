# Knowledge Engine

The knowledge engine is the retrieval-augmented layer behind KM2's chat and
document search. It runs as the `brain_api` service (port **8020**) backed by
Qdrant (vectors) and Neo4j (graph), and is driven by the shared
`packages/brain_sdk` library. This doc is for engineers working on ingest,
retrieval, or the agentic fact store. For how documents get *into* the system
and how permissions are enforced, see [DATABASE.md](DATABASE.md) and
[RBAC.md](RBAC.md); for the service topology see [ARCHITECTURE.md](ARCHITECTURE.md).

## Table of Contents

- [Overview](#overview)
- [Architecture and stores](#architecture-and-stores)
- [The fact model](#the-fact-model)
- [Ingest pipeline](#ingest-pipeline)
- [Vectors and passages](#vectors-and-passages)
- [RAG retrieval and access-key filtering](#rag-retrieval-and-access-key-filtering)
- [Citations](#citations)
- [The agentic query engine](#the-agentic-query-engine)
- [Digest and knowledge gaps](#digest-and-knowledge-gaps)
- [The USE_FACT_ENGINE flag and current state](#the-use_fact_engine-flag-and-current-state)
- [Endpoints](#endpoints)
- [Operational notes](#operational-notes)
- [Known gaps / TODO](#known-gaps--todo)

## Overview

The engine ships **two ingest-and-query paths** that share the same vector
store, gated by the `USE_FACT_ENGINE` flag on `brain_api`:

| Path | Ingest produces | Query | Status |
|---|---|---|---|
| **Legacy top-K RAG** | flat `(subject, predicate, object)` triplets in Neo4j (`brain_sdk.graph_store`) + passage vectors | single vector search + graph "sprinkle", one LLM synthesis (`SearchService`) | fallback path; used when the flag is off |
| **Agentic fact engine** | canonical entities + **reified claims** in Neo4j (`brain_sdk.facts`) + passage vectors | iterative tool-using ReAct loop over the fact store (`FactAgent`) | the intended path; used when the flag is on |

The fact engine's premise is **"facts as searchable truth, not top-K."** A
top-K similarity query cannot give deterministic recall, deduplicate surface
forms ("IBM" / "I.B.M."), carry per-fact provenance, answer aggregative
questions ("list all X where Y"), or reconcile conflicting/updated documents.
The fact store provides these by storing each statement as a **reified claim
node** — not a graph edge — with provenance, bi-temporal validity, a lifecycle
status, and a confidence score. Vector search does not go away; it becomes one
of several *tools* an agent uses for passage grounding.

Both paths keep the passage vectors identical, so passage-level citations and
document search behave the same regardless of the flag.

## Architecture and stores

`brain_api` is a FastAPI service (`services/brain_api/src/brain_api/main.py`).
It authenticates callers with a single shared `X-API-Key` (`BRAIN_API_KEY`) and
**trusts the caller-supplied `tenant_id` and `access_keys`** — the app backend
(`services/api`) is responsible for scoping those to the authenticated user
before calling. That trust boundary is documented on `AskRequest` in
`services/brain_api/src/brain_api/routers/rag.py`: the key must never reach a
browser.

The engine uses three stores; **no fact data lives in PostgreSQL**:

| Store | Holds | Client |
|---|---|---|
| **Qdrant** | passage (`chunks`) and document (`documents`) vectors, one collection pair per tenant | `brain_sdk.vector_store.qdrant_store.QdrantVectorStore` |
| **Neo4j** | the fact store — canonical `:Entity`, reified `:Claim`, `:Chunk`/`:Document` provenance, `:Community` digests, `:Gap` records; plus the legacy flat triplet graph | `brain_sdk.facts.neo4j_fact_store.Neo4jFactStore` and `brain_sdk.graph_store.neo4j_store.Neo4jGraphStore` |
| **OpenAI (or configured provider)** | embeddings, chunk/doc summaries, claim extraction, agent LLM | `brain_sdk.embedding`, `brain_sdk.summarization`, `brain_sdk.llm` |

Every connected client is a lazily-initialized, lock-guarded singleton on the
`Stores` container (`services/brain_api/src/brain_api/stores.py`). PostgreSQL is
the system of record for documents, orgs, membership, and processing status —
see [DATABASE.md](DATABASE.md).

## The fact model

The model lives in `packages/brain_sdk/src/brain_sdk/facts/`. All model types
are frozen, slotted dataclasses (`models.py`); ids are deterministic hashes so
re-ingesting the same content converges instead of duplicating.

### Entities

An `Entity` is a canonical node that many surface forms collapse onto. Its
`entity_id` is `sha256(tenant_id ⨝ type ⨝ normalized_name)`
(`compute_entity_id`), so the id embeds the tenant — a single global uniqueness
constraint on `:Entity(entity_id)` is safe across tenants, and the same
canonical entity re-resolves to the same node on re-ingest. Entities carry
`canonical_name`, `type` (from a small vocabulary — `PERSON`, `ORG`,
`LOCATION`, …, in `extraction.py`), `aliases`, and an `embedding`.

### Claims

A `Claim` is a reified subject–predicate–object statement. The object is either
another entity (`object_type == ENTITY` → `object_id`, stored as an `OBJECT`
edge) or a literal (`object_value` with `object_type` in `text`/`number`/
`date`/`boolean`). Key fields:

- `valid_from` / `valid_to` — **world** time (when the fact is true).
- `recorded_at` — **ingest** time (when we learned it). This bi-temporal split
  is what earns the word "truth".
- `status` — `active` | `superseded` | `contradicted` | `retracted`
  (`ClaimStatus`).
- `confidence`, `access_keys`, `tags`, and a tuple of `provenance`.
- `dedup_key` = `sha256(tenant_id ⨝ subject_id ⨝ predicate ⨝ object_key)` and
  `claim_id == dedup_key` — one stored node per distinct
  (subject, predicate, object). Supersession retires prior nodes and links
  history rather than mutating in place. A `corroborations` counter is
  maintained on the Neo4j node (not on the Python dataclass).

### Provenance

Each supporting source is a `Provenance` record (`document_key`, `chunk_id`,
`text_span` — the supporting sentence — `extractor_model`, `extracted_at`,
`confidence`). In Neo4j this materializes as
`(:Claim)-[:SOURCED_FROM]->(:Chunk)-[:PART_OF]->(:Document)`, so every claim
traces back to the exact passages that support it.

### Predicates and cardinality

Free-form extraction yields `reports_to` / `is managed by` / `works under` as
three relationships. `predicates.py` resolves every raw predicate to a canonical
key and declares a **cardinality** that drives reconciliation:

- `FUNCTIONAL` — one current value per subject (`headquartered_in`,
  `date_of_birth`); a differing new value *supersedes* the old.
- `MULTI` — many values allowed (`authored`, `mentions`); new values are
  additive and repeats *corroborate*.

The seed vocabulary (`DEFAULT_PREDICATES`) is intentionally small; unknown
predicates are accepted (open-domain) and default to `MULTI` so a value is never
wrongly discarded.

### Reconciliation

`reconciliation.reconcile` is a **pure function** (no I/O, exhaustively
unit-tested independent of Cypher) that decides one of four actions per incoming
claim, given its cardinality and the store's current state:

| Situation | Action |
|---|---|
| identical `dedup_key` already exists | **corroborate** (add provenance, raise confidence; no new node) |
| `MULTI` predicate, new object | **create** (additive) |
| `FUNCTIONAL` predicate, new value strictly newer than all conflicts | **supersede** (retire old, link `:SUPERSEDES`) |
| `FUNCTIONAL` predicate, a conflict that cannot be temporally ordered | **contradict** (keep both, mark `contradicted`, link `:CONTRADICTS`) |

`Neo4jFactStore.insert_claims` reads the relevant existing state, calls
`reconcile`, and executes the returned actions.

### Tenant isolation

Every node also carries a dynamic `:Tenant_<org_id>` label (`_tenant_label`).
**Every** read and write injects that label server-side; the LLM never supplies
it. The agent's query tools are fixed, parameterized Cypher (there is no
text-to-Cypher path), so an LLM cannot broaden the tenant filter. This mirrors
the org-scoping model in [RBAC.md](RBAC.md).

## Ingest pipeline

Ingestion is asynchronous and idempotent. It is orchestrated by
`IngestService.ingest_document` (`services/brain_api/src/brain_api/services/
ingest_service.py`), fed by the worker's ingest tasks
(`services/worker/src/worker/tasks/_ingest_common.py`).

### Async submit + poll

`POST /api/ingest-document` returns **202 Accepted immediately** and runs the
pipeline in a background task
(`services/brain_api/src/brain_api/routers/ingest.py`). Ingesting a large
document (chunk → embed → summarize → extract over thousands of chunks) can
exceed any reasonable HTTP timeout, which is what previously made big documents
fail. The caller polls `GET /api/ingest-status/{tenant}/{document_key}`, which
reports `running` | `done` | `failed` | `unknown`, plus a coarse `phase` and a
`0..1` `progress` fraction. `unknown` means brain-api lost the in-flight job
(e.g. a restart) — the worker treats a persistent `unknown` as failure and
re-dispatches (`IngestJobRegistry` in `services/ingest_jobs.py`).

Concurrent submits are idempotent: if a job for the same document is already
running, a second submit is ignored rather than racing and duplicating vectors.

### Purge-first idempotency

Re-ingest **replaces** rather than appends. `IngestService.remove_document`
purges the document's existing vectors (Qdrant), legacy triplets, and — when the
fact engine is on — its claims/provenance (`FactStore.delete_by_document_key`)
**before** writing the new index, right before the write so an earlier failure
leaves the existing index intact. This fixes the historical
append-duplicates behavior where each run inserted fresh point ids and never
deleted the old set. `delete_by_document_key` drops provenance from the
document, then removes claims and entities left unsupported.

### Stages

1. **Chunk** — `create_sectioned_chunks` (`brain_sdk.chunking.chunker`),
   ~500 tokens with 20-token overlap. Each chunk keeps its Markdown **heading
   path** (`section`) so retrieval can cite the specific passage.
2. **Embed + summarize** (run concurrently) — batch chunk embeddings, per-chunk
   summaries, and a hierarchical document summary. The doc-level vector is the
   summary embedding, or the **centroid** of chunk embeddings on fallback (never
   just the first chunk).
3. **Upsert vectors** — chunk records into the `chunks` collection (with `text`,
   `summary`, `section`, `chunk_order`, `document_key`, `tags`, `access_keys`),
   and one document record into `documents`.
4. **Extract knowledge** — the long pole; a per-chunk LLM pass over the whole
   document. **Which extractor runs depends on the flag:**
   - Fact engine **on** → `FactIngestPipeline` (see below).
   - Fact engine **off** → parallel triplet extraction into the legacy graph
     (`_extract_and_store_triplets`).

### The fact pipeline

When the flag is on, `_extract_and_store_facts` runs
`FactIngestPipeline.ingest_document` (`facts/pipeline.py`) over the chunks:

1. **Profile the document** — `DocumentProfiler` (`facts/doc_profiles.py`)
   classifies the document type (metadata/title heuristic, LLM fallback) and
   writes a short brief of its central entities and key points, so a structured
   doc (a directory, a contract) is extracted for the claims that make it
   queryable instead of being treated as prose. Best-effort: a profiling failure
   falls back to the generic profile.
2. **Extract candidates** — `ClaimExtractor` (`facts/extraction.py`), a
   schema-guided LLM call shown the canonical predicate vocabulary and entity
   types, plus a **deterministic** structured pass (`facts/structure.py`) for
   tables/key-value rows. Each candidate carries its supporting `text_span`.
3. **Resolve** — `EntityResolver` (`facts/resolution.py`) maps subject/object
   mentions to canonical entities (embed → ANN blocking → cosine banding →
   LLM adjudication for the ambiguous middle band, auto-merge above 0.90, create
   below the floor). `PredicateResolver` maps raw predicates to canonical keys
   by *meaning* (embedding similarity), not a hand-maintained alias list.
4. **Reconcile + store** — build `Claim`s, `insert_claims` applies the pure
   reconciliation policy, and provenance edges are wired to each chunk.

Failure handling is granular: one bad chunk's extraction error is swallowed but
counted; a **total** wipeout (every chunk failed) raises `FactIngestError` so an
LLM outage is not recorded as a false "0 claims" success. Per-chunk progress is
reported back through the ingest status so the progress bar keeps moving during
the otherwise-opaque extraction loop.

## Vectors and passages

Qdrant holds two collections per tenant (`QdrantVectorStore`): `chunks`
(passage vectors) and `documents` (doc-level vectors). Each chunk payload keeps
its raw `text`, `summary`, `section` heading path, `chunk_order`, document
identifiers, `tags`, and `access_keys` (defaulting to `[0]` = public). The
document payload additionally stores the hierarchical `summary_tree` so the UI
can render an expandable summary; `GET /api/documents/{tenant}/{key}/summary`
returns it, and `GET /api/documents/{tenant}/{key}/chunks` pages through chunks.

## RAG retrieval and access-key filtering

The legacy top-K path is `SearchService`
(`services/brain_api/src/brain_api/services/search_service.py`):

1. Embed the query and vector-search the `chunks` collection (`vector_search`).
2. Optionally add graph context via `fuzzy_relationship_search`.
3. Build one **numbered source per retrieved passage** and a numbered context
   block, then synthesize an answer whose system prompt requires answering only
   from context and citing the exact passage number.

**Access-key filtering is the retrieval-time enforcement point for
per-document RBAC.** The caller (`services/api`) resolves the requester's masks
with `resolve_user_access_keys` (`services/api/src/api/services/
search_access.py`) — admins get `None` (unrestricted); everyone else gets a list
of integer masks — and passes them to brain-api. Both stores filter on them:

- **Qdrant**: `QdrantVectorStore.search(..., access_keys=...)` — only chunks
  whose `access_keys` intersect the caller's masks are returned.
- **Neo4j fact store**: `query_claims` / `neighborhood` add
  `size(c.access_keys) = 0 OR any(k IN c.access_keys WHERE k IN $keys)` — a claim
  is visible if it is public (no keys) or shares a key with the caller.

Folder scoping rides the same call: `folder:<id>` tags are ORed
(`folder_tags`), so retrieval can be narrowed to a set of folders without
excluding docs that match only one. See [RBAC.md](RBAC.md) §"Query Filtering"
for how masks are computed from membership and folder configuration.

## Citations

**Passage-level citations (RAG path).** `SearchService._passage_sources` turns
each retrieved chunk into its own numbered source (`number`, `document_key`,
`document_title`, `section`, `chunk_order`, and a trimmed `snippet`, ~240
chars). Retrieval returns distinct chunks in rank order, so the source `number`
equals the passage's position in the context — the answer's inline `[n]` points
at the *exact passage*, and two passages from the same document get different
numbers. The `section` (heading path) and `chunk_order` let the UI show where in
the document the citation came from and deep-link to that chunk. Streaming emits
a `sources` event up front, then `delta` text fragments.

**Evidence citations (agent path).** The agent labels every tool observation
`[E<n>]` and the answer cites those evidence ids; see below.

## The agentic query engine

When the fact engine is on, the query surface is `FactAgent`
(`packages/brain_sdk/src/brain_sdk/facts/agent.py`) — a JSON-action ReAct loop,
not a single retrieval call. It is exposed at `/api/v1/agent/ask` and
`/api/v1/agent/ask/stream`.

Design choices baked into the shipped loop:

- **Provider-agnostic.** The loop uses a strict JSON action protocol over
  `LLMClient.complete(..., json_object=True)` rather than any one provider's
  native tool-call format, so it runs identically on OpenAI (default), Claude,
  or Gemini (`brain_sdk.llm.factory.make_llm_client`, `LLM_PROVIDER`).
- **Tenant isolation is server-side.** Every tool injects `tenant_id` and
  `access_keys` from the trusted `AgentContext`; the model never supplies them.
- **Grounding + verification.** Each observation is numbered evidence; the final
  answer must cite evidence ids, and the engine flags any citation that does not
  refer to gathered evidence (`unsupported_citations`).
- **Bounded.** A hard iteration budget (`AGENT_MAX_ITERATIONS`, default 6) caps
  latency and cost; on exhaustion it forces a best-effort answer from the
  evidence already gathered.

### Tools

Each tool is tenant-scoped server-side (`FactAgent._exec_tool`):

| Tool | Store | Purpose |
|---|---|---|
| `claim_query {subject?, predicate?, object?, as_of?}` | Neo4j | structured + aggregative fact lookup; `as_of` gives historical "truth as of a date" |
| `entity_lookup {name}` | Neo4j | resolve a mention → canonical entity and everything known about it |
| `neighborhood {name}` | Neo4j | entities directly connected to a named entity (one hop; multi-hop by chaining) |
| `search_passages {query, limit?}` | Qdrant | semantic passage search — used to ground/quote wording |
| `corpus_overview {}` | Neo4j (digest) | high-level community summaries for broad/thematic questions |

The system prompt makes the fact store's sparseness explicit: if a fact tool
returns nothing, the agent **must** try `search_passages` before concluding "no
information available" — many true facts live only in the raw text.

### Streaming events

`FactAgent.stream` yields `thought` | `tool_call` | `tool_result` | `final` |
`error`, forwarded verbatim as SSE. `FactAgent.run` drains the same stream into
an `AgentResult` (answer, citations, unsupported citations, evidence,
iterations). The legacy RAG stream, by contrast, emits `sources` | `graph` |
`delta` | `done` | `error`.

## Digest and knowledge gaps

**Digest (GraphRAG-style).** `DigestBuilder` (`facts/digest.py`) finds
communities via union-find over the entity→entity graph and writes an LLM
summary per multi-entity community as a `:Community` node. It is fully derived
and regenerable (no schema risk), rebuilt on demand via
`POST /api/v1/agent/digest/rebuild`, and surfaced to the agent through the
`corpus_overview` tool as a cheap orientation layer for thematic questions.

**Knowledge-gap loop.** `facts/gaps.py` turns failed fact queries into
re-extraction targets. When the agent runs a fact tool and gets zero rows, that
is a precise signal a wanted fact is missing; the gap is recorded (best-effort,
never surfaced to the user) as a `:Gap` node keyed so recurrences bump an
`occurrences` counter. Operators review the highest-value gaps via
`GET /api/v1/agent/gaps`, and `POST /api/v1/agent/gaps/re-extract` runs a
passage search over the gap's question to suggest which documents to re-ingest —
re-running them through the (type-/structure-aware) fact pipeline converts the
buried passage into queryable claims. `POST /api/v1/agent/gaps/status` resolves
or dismisses a gap.

An evaluation harness (`facts/evaluation.py`: `FactEngineEvaluator`, `EvalCase`,
`EvalReport`, `score_case`) exists for groundedness/answer scoring but is a
library, not an endpoint.

## The USE_FACT_ENGINE flag and current state

`USE_FACT_ENGINE` (`services/brain_api/src/brain_api/config.py`,
`BrainAPISettings.use_fact_engine`) gates the reified-claim path. **The code
default is `False`**; the reified-claim ingest and the agentic endpoints become
the active path when it is set true. When enabled:

- **Ingest** runs `FactIngestPipeline` (claims) instead of the legacy triplet
  extractor. In the running KM2 deployment the flag is enabled, so live ingest
  currently produces reified claims, not triplets.
- **Startup** eagerly runs `Stores.ensure_fact_schema()` (Neo4j constraints +
  entity vector/full-text indexes) so the agentic path is ready on first request
  (`main.py` lifespan).
- The legacy top-K RAG path (`/api/v1/ask`, `/api/vector-chat`) stays available
  regardless of the flag; the fact engine is additive, not a replacement of the
  passage-retrieval surface.

Note the overloaded `triplets` key: `IngestService` reports fact-engine output
under the same `triplets` field the legacy path uses
(`triplet_count = facts.get("claims_extracted", 0)`), so `processing_details.triplets`,
the `triplets_ingested` metric, and the ingest-status `triplets` count all mean
**claims extracted** when the fact engine is on. Renaming this field is deferred
to avoid churning the worker/API status contract.

The legacy `brain_sdk.graph_store` (flat triplets) and
`brain_sdk.extraction.triplet_extractor` remain in the tree for the flag-off
path; they are intended for removal once the fact engine is the sole path.

## Endpoints

All brain-api endpoints require the shared `X-API-Key` and take `tenant_id` in
the body/path. `services/api` proxies them via
`BrainAPIClient` (`services/api/src/api/services/brain_client.py`) after
resolving the user's `access_keys`. See [API.md](API.md) §"Brain API Endpoints"
for the app-facing chat/search routes.

| Method + path | Purpose |
|---|---|
| `GET /healthz` | liveness probe (no auth) |
| `POST /api/ingest-document` | accept a document; **202** + background processing |
| `GET /api/ingest-status/{tenant}/{key}` | poll a background ingest job |
| `POST /api/remove-document` | purge one document from all stores |
| `POST /api/update-document-metadata` | update tags/access_keys/title in place |
| `POST /api/init-tenant` / `POST /api/remove-tenant` | create/delete a tenant's collections + graph |
| `GET /api/documents/{tenant}/{key}/chunks` | paged chunk text |
| `GET /api/documents/{tenant}/{key}/summary` | doc summary + hierarchical tree |
| `POST /api/vector-search` | semantic passage search |
| `POST /api/vector-chat` | non-streaming hybrid RAG chat |
| `POST /api/v1/ask` / `POST /api/v1/ask/stream` | RAG chat (non-streaming / SSE) |
| `POST /api/v1/agent/ask` / `POST /api/v1/agent/ask/stream` | agentic fact-engine query |
| `POST /api/v1/agent/digest/rebuild` | (re)build community-summary digest |
| `GET /api/v1/agent/gaps` | list open knowledge gaps |
| `POST /api/v1/agent/gaps/status` | resolve/dismiss a gap |
| `POST /api/v1/agent/gaps/re-extract` | suggest documents to re-ingest for a gap |

## Operational notes

- **Re-ingest is purge-first.** Any re-run of a document
  (`POST /api/documents/{id}/reprocess` in `services/api`, or a worker
  redelivery) purges the prior vectors/graph/facts before writing. Use reprocess
  to pick up pipeline improvements (new chunking/citation metadata) or to
  recover a `FAILED`/`CANCELLED` document without re-uploading; it re-reads the
  original from object storage, so it works for PDFs/images/Word too.
- **Recovering FAILED documents.** A document whose ingest failed (LLM outage →
  `FactIngestError`, a lost job, or repeated poll errors) is marked FAILED by the
  worker. Re-dispatch it via reprocess; because ingest is purge-first, a partial
  prior run is replaced, not compounded.
- **Stopping a runaway ingest / controlling spend.** Fact extraction is the cost
  driver (a per-chunk LLM pass, plus resolution embeddings/adjudication). Ingest
  is cancellable between polls; a stuck or expensive job should be cancelled at
  the `services/api` document level (which also purges the partial index) rather
  than left polling. Neo4j and Qdrant deletes are best-effort and idempotent, so
  a cancelled/partial ingest can always be re-run cleanly.
- **Digest freshness.** Community summaries are derived; rebuild them after a
  large ingest with `POST /api/v1/agent/digest/rebuild` so `corpus_overview`
  reflects the new corpus.
- **Warm-up.** On startup brain-api exercises the read path once against a
  synthetic tenant (`SearchService.warm_up`) so the first real user query does
  not absorb the ~20s cold connection/TLS cost.

## Known gaps / TODO

- **No content-hash short-circuit.** Re-ingest always purges and re-extracts,
  even when the document content is unchanged. Skipping unchanged documents via
  a content hash is a possible future optimization to save LLM spend on no-op
  re-ingests (noted in `facts/pipeline.py`).
- **`triplets` naming is overloaded** (see above) — the field means "claims"
  when the fact engine is on. A rename is deferred.
- **Multi-hop neighborhood is agent-composed**, not a native quantified-path
  expansion (`Neo4jFactStore.neighborhood` returns one hop; the agent chains
  calls). A native N-hop query is a follow-up.
- **Legacy triplet path still ships** (`brain_sdk.graph_store`,
  `extraction.triplet_extractor`) for the flag-off case and is intended for
  removal once the fact engine is the sole path.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
