# API Reference

Red Arch KM exposes two REST APIs: the main API (port 8000) and Brain API (port 8020).

## Authentication

### User Authentication (Main API)

All main API endpoints require a Clerk session JWT:

```http
Authorization: Bearer <clerk_session_token>
```

### Service Authentication (Brain API)

Brain API endpoints require an API key:

```http
X-API-Key: ${BRAIN_API_KEY}
```

## Main API Endpoints

Base URL: `http://localhost:8000`

### Health

#### GET /healthz
Health check endpoint.

**Response:**
```json
{
  "status": "ok"
}
```

### Authentication

#### GET /auth/me
Get current authenticated user.

**Response:**
```json
{
  "keycloak_sub": "abc123",
  "username": "jsmith",
  "email": "jsmith@example.com",
  "profile_id": "uuid",
  "is_site_admin": false
}
```

### Organizations

#### GET /orgs
List organizations (paginated).

**Query Parameters:**
- `page` (int, default: 1) — Page number
- `page_size` (int, default: 50) — Items per page

**Response:**
```json
{
  "items": [
    {
      "id": "uuid",
      "name": "Acme Corp",
      "description": "Example organization",
      "use_knowledge_graph": true,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50,
  "pages": 1
}
```

#### POST /orgs
Create organization (site admin only).

**Request:**
```json
{
  "name": "Acme Corp",
  "description": "Example organization",
  "use_knowledge_graph": true
}
```

**Response:** `201 Created` with org object

#### GET /orgs/{org_id}
Get organization details.

**Response:** Org object

#### PATCH /orgs/{org_id}
Update organization (site admin only).

**Request:**
```json
{
  "name": "Updated Name",
  "description": "Updated description"
}
```

#### DELETE /orgs/{org_id}
Delete organization (site admin only). Cascades to all tenant data.

**Response:** `204 No Content`

### Documents

All document endpoints are scoped to an organization via header:

```http
X-Org-Id: <org_uuid>
```

#### GET /documents
List documents the user can view.

**Query Parameters:**
- `page` (int) — Page number
- `page_size` (int) — Items per page

**Response:**
```json
{
  "items": [
    {
      "id": "uuid",
      "title": "Getting Started Guide",
      "description": "Introduction to the platform",
      "document_key": "unique-key",
      "processing_status": "COMPLETE",
      "folder_id": "uuid",
      "created_at": "2024-01-01T00:00:00Z"
    }
  ],
  "total": 10,
  "page": 1,
  "page_size": 50,
  "pages": 1
}
```

#### POST /documents
Create and ingest a document.

**Request:**
```json
{
  "title": "New Document",
  "text": "Document content...",
  "description": "Optional description",
  "folder_id": "uuid",
  "tag_ids": ["uuid1", "uuid2"],
  "use_knowledge_graph": true,
  "metadata": {
    "author": "John Smith",
    "department": "Engineering"
  }
}
```

**Response:** `201 Created` with document object

#### GET /documents/{document_id}
Get document details.

#### PATCH /documents/{document_id}
Update document metadata.

**Request:**
```json
{
  "title": "Updated Title",
  "description": "Updated description",
  "tag_ids": ["uuid1"]
}
```

#### DELETE /documents/{document_id}
Delete document. Cascades to vector/graph stores.

**Response:** `204 No Content`

#### GET /documents/{document_id}/chunks
Get indexed chunks for a document.

**Response:**
```json
{
  "document_key": "unique-key",
  "chunks": [
    {
      "id": "uuid",
      "text": "Chunk content...",
      "chunk_order": 0
    }
  ]
}
```

### Folders

#### GET /folders
List folders the user can view.

**Response:**
```json
{
  "items": [
    {
      "id": "uuid",
      "name": "Engineering Docs",
      "description": "Technical documentation",
      "parent_id": null,
      "order": 0
    }
  ],
  "total": 5,
  "page": 1,
  "page_size": 50,
  "pages": 1
}
```

#### POST /folders
Create folder with permissions.

**Request:**
```json
{
  "name": "New Folder",
  "description": "Folder description",
  "parent_id": null,
  "viewer_permissions_config": [
    {
      "regions": ["North America"],
      "departments": ["Engineering"],
      "roles": [],
      "groups": []
    }
  ],
  "contributor_permissions_config": []
}
```

#### GET /folders/{folder_id}
Get folder details.

#### PATCH /folders/{folder_id}
Update folder.

#### DELETE /folders/{folder_id}
Delete folder. Documents moved to root.

#### POST /folders/{folder_id}/move
Move folder to new parent.

**Request:**
```json
{
  "new_parent_id": "uuid"
}
```

### Users

#### GET /users
List users in the current organization.

#### GET /users/me
Get the current user along with their accessible orgs.

#### PATCH /users/me
Update the current user's own profile (description only). Username and
email are sourced from Clerk and cannot be changed here.

### Memberships

#### GET /memberships/by-user/{user_id}
Get a user's membership in the current org (or `null` if none exists). Org admin only.

#### POST /memberships
Create membership (org admin only).

**Request:**
```json
{
  "profile_id": "uuid",
  "is_org_admin": false,
  "region_ids": ["uuid"],
  "department_ids": ["uuid"],
  "role_ids": ["uuid"],
  "group_ids": ["uuid"]
}
```

#### PATCH /memberships/{membership_id}
Update membership.

#### DELETE /memberships/{membership_id}
Remove a user from the current org (org admin only). Returns `204`. Guards:
an org admin cannot remove their own membership (`400`, site admins exempt),
and the org's last admin membership cannot be removed (`409`).

### Setup (first-run bootstrap)

#### GET /setup/status
Public (no auth). Returns `{"needs_setup": true}` while no active site admin
exists.

#### POST /setup/claim
Authenticated. Exchanges the one-time setup token from the API server logs
for global admin on the calling account.

**Request:** `{"token": "<token from logs>"}` — **Response:** `{"claimed": true}`

Errors: `403` invalid/used token, `409` a site admin already exists.

### Admin (site admin only)

All routes require `is_site_admin`.

#### GET /admin/users
Paginated list of all users across the instance. Query params: `page`,
`page_size`, `q` (case-insensitive substring match on username/email).

#### PATCH /admin/users/{profile_id}
Update global flags: `{"is_site_admin"?: bool, "is_active"?: bool}`.
Guards: self-demotion/self-deactivation → `400`; removing the last active
site admin → `409`; unknown user → `404`.

#### GET /admin/users/{profile_id}/memberships
All org memberships of one user across every org:
`[{"membership_id", "org_id", "org_name", "is_org_admin"}]`.

#### GET /admin/system
Platform health for the console's System tab:

```json
{
  "version": "2.0.0",
  "components": {
    "database":     {"status": "ok", "latency_ms": 0.8,  "detail": null},
    "redis":        {"status": "ok", "latency_ms": 0.3,  "detail": null},
    "brain_api":    {"status": "ok", "latency_ms": 53.2, "detail": null},
    "worker_queue": {"status": "ok", "latency_ms": null, "detail": "depth=3"}
  }
}
```

### Tags

#### GET /tags
List tags in organization.

#### POST /tags
Create tag.

**Request:**
```json
{
  "name": "Important"
}
```

#### DELETE /tags/{tag_id}
Delete tag.

### Dimensions

#### GET /dimensions/regions
List regions.

#### POST /dimensions/regions
Create region.

#### GET /dimensions/departments
List departments.

#### POST /dimensions/departments
Create department.

#### GET /dimensions/roles
List roles.

#### POST /dimensions/roles
Create role.

#### GET /dimensions/groups
List groups.

#### POST /dimensions/groups
Create group.

### Chat

#### POST /chat/sessions
Create chat session.

**Response:**
```json
{
  "id": "uuid",
  "chat_data": []
}
```

#### GET /chat/sessions
List user's chat sessions.

#### GET /chat/sessions/{session_id}
Get chat session with history.

#### POST /chat/sessions/{session_id}/ask
Ask a question in a chat session and stream the RAG response.

**Request:**
```json
{
  "query": "How do I configure the system?",
  "context_filters": {
    "folder_ids": ["uuid"],
    "tag_ids": ["uuid"],
    "document_keys": ["string"]
  }
}
```

`context_filters` is optional. The knowledge graph is always consulted for
this endpoint.

**Response (streaming):** Server-Sent Events (`sources`, `graph`, `delta`, `done`, `error` event types)

#### DELETE /chat/sessions/{session_id}
Delete chat session.

### Search

#### POST /search
Search documents.

**Request:**
```json
{
  "query": "configuration guide",
  "limit": 10
}
```

**Response:**
```json
{
  "results": [
    {
      "document_id": "uuid",
      "title": "Configuration Guide",
      "score": 0.95,
      "chunk_text": "To configure the system..."
    }
  ]
}
```

### Attributes

#### GET /attributes
List custom attribute definitions.

#### POST /attributes
Create attribute definition.

**Request:**
```json
{
  "name": "Department",
  "slug": "department",
  "attribute_type": "picklist",
  "picklist_options": ["Engineering", "Sales", "Marketing"],
  "required": false
}
```

## Brain API Endpoints

Base URL: `http://localhost:8020`

### Health

#### GET /healthz
Health check.

**Response:**
```json
{
  "status": "ok",
  "qdrant": "connected",
  "neo4j": "connected"
}
```

### Ingestion

#### POST /ingest-document
Ingest document into vector/graph stores.

**Request:**
```json
{
  "tenant_id": "org-uuid",
  "document_key": "unique-key",
  "title": "Document Title",
  "text": "Full document text...",
  "tags": ["engineering", "guide"],
  "access_keys": [12345, 67890],
  "use_knowledge_graph": true,
  "metadata": {}
}
```

**Response:**
```json
{
  "status": "ingested",
  "chunk_count": 15,
  "entity_count": 8
}
```

#### POST /remove-document
Remove document from stores.

**Request:**
```json
{
  "tenant_id": "org-uuid",
  "document_key": "unique-key"
}
```

#### POST /update-document-metadata
Update document metadata in stores.

**Request:**
```json
{
  "tenant_id": "org-uuid",
  "document_key": "unique-key",
  "title": "New Title",
  "new_tags": ["updated-tag"],
  "new_access_keys": [11111]
}
```

#### GET /documents/{tenant_id}/{document_key}/chunks
Get chunks for a document.

### Search

#### POST /vector-search
Semantic search.

**Request:**
```json
{
  "tenant_id": "org-uuid",
  "query": "How do I configure...",
  "limit": 5,
  "access_keys": [12345],
  "tags": []
}
```

**Response:**
```json
{
  "results": [
    {
      "document_key": "key",
      "chunk_text": "...",
      "score": 0.92
    }
  ]
}
```

#### POST /vector-chat
Search with chat context.

**Request:**
```json
{
  "tenant_id": "org-uuid",
  "query": "Follow up question",
  "chat_history": [
    {"role": "user", "content": "Previous question"},
    {"role": "assistant", "content": "Previous answer"}
  ],
  "access_keys": [12345],
  "use_knowledge_graph": true,
  "chunk_limit": 5
}
```

### RAG

#### POST /ask
Non-streaming RAG query.

**Request:**
```json
{
  "tenant_id": "org-uuid",
  "query": "What is the deployment process?",
  "chat_history": [],
  "access_keys": [12345],
  "tags": [],
  "use_knowledge_graph": true
}
```

**Response:**
```json
{
  "answer": "The deployment process involves...",
  "sources": [
    {
      "document_key": "deploy-guide",
      "title": "Deployment Guide",
      "chunk_text": "..."
    }
  ],
  "graph_context": []
}
```

#### POST /ask/stream
Streaming RAG query via SSE.

**Request:** Same as /ask

**Response:** Server-Sent Events stream

Event types:
- `sources` — Document references retrieved
- `graph` — Graph triplets used as context
- `delta` — Incremental answer text
- `done` — Terminal marker
- `error` — Error marker

### Tenant Management

#### POST /init-tenant
Initialize tenant collections.

**Request:**
```json
{
  "tenant_id": "org-uuid"
}
```

#### POST /remove-tenant
Delete all tenant data from stores.

**Request:**
```json
{
  "tenant_id": "org-uuid"
}
```

## Error Responses

All endpoints return errors in a consistent format:

```json
{
  "detail": "Error message"
}
```

### Common Status Codes

| Code | Meaning |
|------|---------|
| 400 | Bad Request — Invalid input |
| 401 | Unauthorized — Missing or invalid auth |
| 403 | Forbidden — Insufficient permissions |
| 404 | Not Found — Resource doesn't exist |
| 409 | Conflict — Resource already exists |
| 422 | Unprocessable Entity — Validation error |
| 500 | Internal Server Error |
| 502 | Bad Gateway — Upstream service error |

## Rate Limiting

The API enforces rate limits per user:

- Default: 60 requests/minute
- Configurable via `API_RATE_LIMIT_PER_MINUTE`

Rate limit headers:
```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 55
X-RateLimit-Reset: 1704067200
```

## Pagination

List endpoints support pagination:

**Query Parameters:**
- `page` (int, default: 1) — Page number (1-indexed)
- `page_size` (int, default: 50, max: 250) — Items per page

**Response:**
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 50,
  "pages": 2
}
```
