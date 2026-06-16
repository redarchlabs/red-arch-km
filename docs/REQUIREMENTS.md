# Red Arch Knowledge Manager - Requirements

## Functional Requirements

### FR-1: Document Management

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-1.1 | System shall allow users to upload text documents | Must Have |
| FR-1.2 | System shall organize documents in hierarchical folders | Must Have |
| FR-1.3 | System shall support unlimited folder nesting depth | Should Have |
| FR-1.4 | System shall allow drag-and-drop folder reordering | Should Have |
| FR-1.5 | System shall support document tagging | Must Have |
| FR-1.6 | System shall support custom metadata attributes per organization | Should Have |
| FR-1.7 | System shall track document processing status (pending, processing, complete, error) | Must Have |
| FR-1.8 | System shall allow document deletion with cascade to vector/graph stores | Must Have |

### FR-2: Document Processing Pipeline

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-2.1 | System shall chunk documents into semantic segments (~500 tokens) | Must Have |
| FR-2.2 | System shall generate embeddings for each chunk | Must Have |
| FR-2.3 | System shall generate summaries at chunk level | Should Have |
| FR-2.4 | System shall generate hierarchical summaries (block, section, document) | Should Have |
| FR-2.5 | System shall extract entities and relationships for knowledge graph | Should Have |
| FR-2.6 | System shall allow knowledge graph extraction to be toggled per org/document | Should Have |
| FR-2.7 | System shall process documents asynchronously via background workers | Must Have |
| FR-2.8 | System shall report processing errors with details | Must Have |

### FR-3: Conversational AI / RAG

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-3.1 | System shall accept natural language queries from users | Must Have |
| FR-3.2 | System shall retrieve relevant document chunks via vector similarity | Must Have |
| FR-3.3 | System shall optionally retrieve knowledge graph context | Should Have |
| FR-3.4 | System shall generate answers using retrieved context + LLM | Must Have |
| FR-3.5 | System shall cite source documents in responses | Must Have |
| FR-3.6 | System shall stream responses in real-time | Must Have |
| FR-3.7 | System shall preserve conversation history per session | Must Have |
| FR-3.8 | System shall filter retrieval by user's access permissions | Must Have |
| FR-3.9 | System shall allow users to scope queries to specific folders/tags | Should Have |

### FR-4: Access Control (RBAC)

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-4.1 | System shall support multi-dimensional permissions (roles, groups, regions, departments) | Must Have |
| FR-4.2 | System shall enforce folder-level view permissions | Must Have |
| FR-4.3 | System shall enforce folder-level contribute permissions | Must Have |
| FR-4.4 | System shall allow permission inheritance from parent folders | Should Have |
| FR-4.5 | System shall filter search results by user permissions before LLM processing | Must Have |
| FR-4.6 | System shall log permission changes for audit compliance | Should Have |
| FR-4.7 | System shall support org-admin and site-admin privilege levels | Must Have |

### FR-5: Multi-Tenancy

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-5.1 | System shall isolate data between organizations | Must Have |
| FR-5.2 | System shall enforce tenant isolation via PostgreSQL RLS | Must Have |
| FR-5.3 | System shall maintain separate vector collections per tenant | Must Have |
| FR-5.4 | System shall maintain separate graph subsets per tenant | Must Have |
| FR-5.5 | System shall allow users to belong to multiple organizations | Should Have |
| FR-5.6 | System shall support per-org OpenAI API key configuration | Should Have |

### FR-6: Authentication & User Management

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-6.1 | System shall authenticate users via Keycloak (OIDC) | Must Have |
| FR-6.2 | System shall auto-provision user profiles on first login | Must Have |
| FR-6.3 | System shall support JWT token authentication for API access | Must Have |
| FR-6.4 | System shall allow users to switch between organizations | Should Have |
| FR-6.5 | System shall allow org admins to manage memberships | Must Have |
| FR-6.6 | System shall allow site admins to manage all users and orgs | Must Have |

---

## Non-Functional Requirements

### NFR-1: Performance

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-1.1 | API response time for list endpoints | < 500ms p95 |
| NFR-1.2 | API response time for single-item GET | < 200ms p95 |
| NFR-1.3 | Time to first token in streaming chat | < 2s |
| NFR-1.4 | Document ingestion throughput | > 10 docs/minute/worker |
| NFR-1.5 | Vector search latency | < 100ms for top-k=10 |

### NFR-2: Scalability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-2.1 | Support concurrent users per org | 100+ |
| NFR-2.2 | Support documents per org | 100,000+ |
| NFR-2.3 | Support total chunks across all tenants | 10M+ |
| NFR-2.4 | Horizontal scaling of API services | Stateless design |
| NFR-2.5 | Horizontal scaling of workers | Independent worker pool |

### NFR-3: Security

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-3.1 | All API endpoints require authentication | JWT/OIDC middleware |
| NFR-3.2 | Tenant data isolation | PostgreSQL RLS |
| NFR-3.3 | No cross-tenant data leakage in vector search | Access key filtering |
| NFR-3.4 | Secrets management | Environment variables |
| NFR-3.5 | HTTPS in production | TLS termination at load balancer |
| NFR-3.6 | Input validation on all endpoints | Schema validation |

### NFR-4: Reliability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-4.1 | System availability | 99.5% uptime |
| NFR-4.2 | Data durability | PostgreSQL with backups |
| NFR-4.3 | Graceful degradation on brain-api failure | Cascade deletes log and continue |
| NFR-4.4 | Worker task retry on failure | Configurable backoff |

### NFR-5: Maintainability

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-5.1 | Code test coverage | 80% minimum |
| NFR-5.2 | Type safety | Go (statically typed) |
| NFR-5.3 | Structured logging | JSON format with trace IDs |
| NFR-5.4 | Health check endpoints | `/healthz` on all services |
| NFR-5.5 | OpenTelemetry instrumentation | Traces + metrics |

### NFR-6: Deployment

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-6.1 | Container-based deployment | Docker images |
| NFR-6.2 | Local development setup | Docker Compose |
| NFR-6.3 | CI/CD pipeline | GitHub Actions |
| NFR-6.4 | Database migrations | golang-migrate |
| NFR-6.5 | Configuration via environment | 12-factor app |

---

## External Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| PostgreSQL | 16+ | Primary data store |
| Redis | 7+ | Caching, task queue |
| Qdrant | 1.12+ | Vector similarity search |
| Neo4j | 5.25+ | Knowledge graph |
| Keycloak | 26+ | Identity provider |
| OpenAI API | - | Embeddings + completions |

---

## Constraints

| ID | Constraint |
|----|------------|
| C-1 | Backend must be implemented in Go |
| C-2 | Frontend is Next.js 15 with React 18 (preserved from rebuild) |
| C-3 | Must use existing PostgreSQL schema with RLS |
| C-4 | Must integrate with Keycloak for authentication |
| C-5 | Must preserve API contract for frontend compatibility |
