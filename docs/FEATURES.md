# Red Arch Knowledge Manager - Features & Capabilities

## Overview

Red Arch Knowledge Manager is an enterprise-grade, AI-powered knowledge management platform that enables organizations to centralize, search, and intelligently query their documents using natural language. The platform combines semantic search, knowledge graphs, and large language models to deliver accurate, citation-backed answers while enforcing fine-grained access control.

---

## Core Features

### 1. Conversational AI Q&A with Citations

**What it does:** Users ask natural language questions and receive intelligent answers backed by source document citations.

**How it works:**
- Query is converted to embeddings and matched against document chunks
- Relevant context is assembled from vector search + knowledge graph
- LLM generates response using retrieved context
- Source documents are cited inline for verification

**User experience:**
- Chat interface with streaming responses
- Click-through to source documents
- Conversation history preserved per session
- Filter chat context by folders, tags, or specific documents

---

### 2. Document Management

**What it does:** Organize, upload, and manage documents in a hierarchical folder structure.

**Capabilities:**
- **Folder hierarchy** - Unlimited nesting with drag-and-drop reordering
- **Document upload** - Support for text content with automatic processing
- **Tagging** - Flexible tagging system for cross-cutting categorization
- **Custom attributes** - Define organization-specific metadata fields (freeform text, picklists)
- **Search & filter** - Find documents by title, tags, folder, or full-text content

**Document processing:**
- Automatic chunking into semantic segments
- Embedding generation for similarity search
- Multi-level summarization (chunk → section → document)
- Knowledge graph entity/relationship extraction

---

### 3. Role-Based Access Control (RBAC)

**What it does:** Control who can view, contribute to, or manage documents at multiple levels.

**Permission dimensions:**
- **Roles** - Job functions (e.g., Manager, Analyst, Executive)
- **Groups** - Teams or project groups
- **Regions** - Geographic or jurisdictional divisions
- **Departments** - Organizational units

**Access levels:**
- **View** - Read documents and query via chat
- **Contribute** - Add/edit documents in folders
- **Manage** - Full administrative access

**Key features:**
- **Folder-level permissions** - Inherit or override at any level
- **Permission-aware chat** - RAG results filtered by user entitlements before LLM sees them
- **Audit logging** - Permission changes tracked for compliance
- **Multi-tenancy** - Complete data isolation between organizations via Row-Level Security

---

### 4. Semantic Vector Search

**What it does:** Find relevant documents based on meaning, not just keyword matching.

**How it works:**
- Documents chunked into ~500 token segments
- Each chunk embedded using OpenAI embeddings (ada-002/text-embedding-3-small)
- Embeddings stored in Qdrant vector database
- Similarity search returns top-k relevant chunks

**Features:**
- Configurable chunk overlap for context preservation
- Access-key filtering enforced at search time
- Metadata filtering (tags, folders, document attributes)

---

### 5. Knowledge Graph Extraction

**What it does:** Extract entities, relationships, and events from documents into a structured graph for enhanced retrieval.

**Graph elements:**
- **Entities** - People, organizations, locations, concepts
- **Relationships** - Connections between entities (e.g., "works at", "located in")
- **Events** - Time-bound occurrences involving entities

**Benefits:**
- Answer relationship queries ("Who reports to the CEO?")
- Provide structured context alongside vector search results
- Enable graph-based exploration of document interconnections

**Configuration:**
- Enable/disable per organization
- Enable/disable per document
- Stored in Neo4j graph database

---

### 6. Multi-Level Summarization

**What it does:** Generate hierarchical summaries for quick comprehension at any granularity.

**Summary levels:**
1. **Chunk summaries** - Brief synopsis of each ~500 token segment
2. **Block summaries** - Aggregate of related chunks
3. **Section summaries** - Higher-level topic summaries
4. **Document summaries** - Executive overview of entire document

**Use cases:**
- Quick document previews
- Enhanced RAG context (include summaries in prompt)
- Document overview without reading full content

---

### 7. Multi-Tenant Organization Management

**What it does:** Support multiple isolated organizations on a single platform instance.

**Capabilities:**
- **Organization creation** - Site admins create new orgs
- **User provisioning** - Auto-create profiles on first login via OIDC
- **Membership management** - Assign users to orgs with admin/member roles
- **Org settings** - Per-org OpenAI API key, knowledge graph toggle

**Isolation:**
- PostgreSQL Row-Level Security (RLS) enforces tenant boundaries
- Separate Qdrant collections per tenant
- Separate Neo4j subgraph per tenant

---

### 8. Authentication & Identity

**What it does:** Secure authentication via enterprise identity provider integration.

**Supported methods:**
- **Clerk (OIDC/OAuth2)** - Modern SaaS authentication
- **JWT tokens** - Stateless API authentication

**Features:**
- Auto-provision user profiles on first login
- Session management
- Organization context switching (user can belong to multiple orgs)

---

### 9. Reporting & Data Aggregation

**What it does:** Build named GROUP BY queries over custom entities and visualize the results.

**Features:**
- **Aggregation engine** — group by fields/relationships/base columns with date bucketing
  (day/week/month/quarter/year); metrics `count / count_distinct / sum / avg / min / max`; filter, HAVING,
  order, and limit. Runs server-side under tenant RLS (`POST /api/entities/{slug}/aggregate`).
- **Saved reports** — a stored aggregate query + chart/KPI/table visualization
  (bar/line/area/pie/donut/scatter/table/metric), managed on the Reports page with a live preview builder.
- **Dashboards** — drop a `report` element onto any view to embed a live chart/KPI tile; reports travel in
  the org import/export bundle.
- **Server-side record filtering** — filter record lists by field with `eq/ne/gt/gte/lt/lte/in/contains/
  isnull`; keyset pagination works under any sort, index-backed for scale.

---

## User Interface Features

### Chat Interface
- Real-time streaming responses
- Conversation session management
- Source document citations with links
- Context scope selection (folders, tags, files)

### Documents Page
- List view with search/filter
- Upload new documents
- View document details and metadata
- See processing status (pending, processing, complete, error)

### Folders Page
- Hierarchical tree view
- Drag-and-drop reordering
- Create/rename/delete folders
- Permission configuration per folder

### Admin Panel
- User membership management
- Permission dimension configuration (roles, groups, regions, departments)
- Document attribute schema management
- Tag management

---

## Technical Capabilities

### API Features
- RESTful JSON API
- Paginated list endpoints
- PATCH-style partial updates
- Streaming responses for chat (Server-Sent Events)
- Health check endpoints

### Observability
- OpenTelemetry instrumentation
- Structured JSON logging
- Request tracing with correlation IDs
- Health checks for all dependencies

### Deployment
- Docker Compose orchestration
- Horizontal scalability (stateless API services)
- Background worker processing (Celery/async)
- Database connection pooling

---

## Integration Points

| Integration | Purpose |
|-------------|---------|
| **OpenAI API** | Embeddings + chat completions |
| **Qdrant** | Vector similarity search |
| **Neo4j** | Knowledge graph storage |
| **PostgreSQL** | Primary data store with RLS |
| **Redis** | Caching + task queue broker |
| **Clerk** | Identity provider (OIDC) |

---

## Feature Matrix by Role

| Feature | User | Org Admin | Site Admin |
|---------|------|-----------|------------|
| Chat with documents | Based on folder permissions | Full org access | All orgs |
| Upload documents | Contribute permission | Full access | Full access |
| Manage folders | Manage permission | Full access | Full access |
| Configure permissions | - | Full access | Full access |
| Manage users | - | Org members | All users |
| Create organizations | - | - | Yes |
| System configuration | - | - | Yes |
