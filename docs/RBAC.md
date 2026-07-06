# RBAC (Role-Based Access Control)

Red Arch Knowledge Manager implements a fine-grained permission system using 32-bit access masks for efficient folder and document access control.

## Permission Hierarchy

```
┌─────────────────────────────────────────────────────┐
│                     Site Admin                       │
│         (Platform-wide superuser access)             │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────┐
│                      Org Admin                       │
│          (Full access within organization)           │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────┐
│                   Org Member                         │
│      (Access controlled by permission masks)         │
└─────────────────────────────────────────────────────┘
```

## Access Levels

### Site Admin
- `is_site_admin = true` on user_profiles
- Can CRUD all organizations
- Bypasses org-level permission checks
- Bootstrapped via the first-run setup wizard (`/setup` + one-time token from
  the API logs, see [DEPLOYMENT.md](DEPLOYMENT.md)); afterwards existing site
  admins promote/demote others in the Site Admin console
  (`PATCH /api/admin/users/{id}`)
- The last active site admin cannot be demoted or deactivated (409), and
  admins cannot demote/deactivate themselves (400)
- Accounts can be deactivated (`is_active = false`): authentication is refused
  with 403 even when the Clerk JWT is valid

### Org Admin
- `is_org_admin = true` on user_org_memberships
- Full access to all folders/documents in their org
- Can manage org members and permissions
- Set by other org admins or site admins

### Org Member
- Access determined by dimension assignments
- Permission mask calculated from membership
- Can only view/contribute to folders matching their mask

## Permission Dimensions

Five dimensions define access within an organization:

| Dimension | Bits | Max Value | Shift | Example |
|-----------|------|-----------|-------|---------|
| Org | 11 | 2047 | 21 | Acme Corp (slot 1) |
| Region | 5 | 31 | 16 | North America (slot 3) |
| Role | 5 | 31 | 11 | Manager (slot 2) |
| Group | 7 | 127 | 4 | Project Alpha (slot 7) |
| Dept | 4 | 15 | 0 | Engineering (slot 5) |

**Total: 11 + 5 + 5 + 7 + 4 = 32 bits**

### Bit Layout

```
 31                                                       0
 ├─── Org (11) ───┼─ Region (5) ─┼─ Role (5) ─┼─ Group (7) ─┼─ Dept (4) ─┤
     bits 21-31       16-20         11-15         4-10         0-3
```

## Access Mask Encoding

The `access_mask` package encodes/decodes permission masks:

```python
from access_mask import encode, decode, matches

# Encode a user's permissions
user_mask = encode(
    org=1,       # Org slot 1
    region=3,    # Region slot 3 (North America)
    dept=5,      # Dept slot 5 (Engineering)
    role=2,      # Role slot 2 (Manager)
    group=7,     # Group slot 7 (Project Alpha)
)

# Decode for inspection
decoded = decode(user_mask)
# DecodedMask(org=1, region=3, role=2, group=7, dept=5)

# Check if user can access a document
# Document allows any role/group in Engineering, North America
doc_mask = encode(org=1, region=3, dept=5, role=31, group=127)
can_access = matches(user_mask, doc_mask)  # True
```

### Wildcard Values

Setting a dimension to its maximum value acts as a wildcard (matches any user value):

| Dimension | Wildcard Value |
|-----------|----------------|
| Region | 31 |
| Role | 31 |
| Group | 127 |
| Dept | 15 |

**Note:** Org cannot be a wildcard — it must always match exactly.

```python
# Folder accessible to ALL roles in Engineering, North America
folder_mask = encode(
    org=1,
    region=3,      # North America only
    dept=5,        # Engineering only
    role=31,       # ANY role (wildcard)
    group=127,     # ANY group (wildcard)
)
```

### Multiple Masks

Folders can have multiple permission masks for complex access rules:

```python
# Folder accessible to Engineering OR Sales
folder.view_permission_masks = [
    encode(org=1, dept=5, region=31, role=31, group=127),  # Engineering, any region/role/group
    encode(org=1, dept=2, region=31, role=31, group=127),  # Sales, any region/role/group
]
```

A user with a mask matching ANY of the folder's masks gains access.

## User Membership

### Assignment Flow

1. User authenticates via Clerk
2. API creates/updates user_profiles with Clerk sub
3. Admin creates user_org_memberships linking user to org
4. Admin assigns dimensions via junction tables:
   - membership_regions
   - membership_departments
   - membership_roles
   - membership_groups

### Mask Calculation

When evaluating access, the API calculates the user's mask:

```python
from api.services.permission_config import calculate_user_masks_from_membership

# Given a membership with assigned dimensions
masks = calculate_user_masks_from_membership(
    membership,
    org_permission_number=1
)
# Returns list of masks (one per dimension combination)
```

## Folder Permissions

### Configuration Structure

```json
{
  "viewer_permissions_config": [
    {
      "regions": ["North America", "Europe"],
      "departments": ["Engineering"],
      "roles": [],
      "groups": []
    }
  ],
  "contributor_permissions_config": [
    {
      "regions": ["North America"],
      "departments": ["Engineering"],
      "roles": ["Manager", "Senior"],
      "groups": []
    }
  ]
}
```

### Mask Generation

The permission config is compiled into integer masks:

```python
# API calculates masks when folder is created/updated
folder.view_permission_masks = compile_permissions(
    config=folder.viewer_permissions_config,
    org=org,
)
folder.contributor_permission_masks = compile_permissions(
    config=folder.contributor_permissions_config,
    org=org,
)
```

## Document Access

### Per-Document Permission Overrides

Documents can override their folder's permissions:

1. **Folder permissions (default):** If a document has `viewer_permissions_config = null`, its access falls back to its folder's masks
2. **Per-document override:** If a document has a non-null `viewer_permissions_config`, those masks are used instead of the folder's

```json
{
  "viewer_permissions_config": {
    "regions": ["North America"],
    "departments": ["Engineering"],
    "roles": [],
    "groups": []
  },
  "view_permission_masks": [...]
}
```

This allows fine-tuning access on a per-document basis without moving the document or creating nested folders.

### Inheritance

Documents inherit access from their parent folder AT CREATION:

1. Document created in folder
2. Folder's view_permission_masks copied to document's access keys (if no per-document override)
3. Access keys passed to brain-api during ingestion
4. Qdrant stores access keys in point payload

### Query Filtering

When searching/chatting, user's masks filter results:

```python
# Brain API vector search
results = search(
    query="How do I configure...",
    access_keys=[user_mask_1, user_mask_2],  # User's permission masks
)
# Only chunks with matching access keys returned
```

## Workflow Run Permissions

Workflows can be manually executed by users with specified permissions:

### Run Permission Model

The `workflows.run_permission` JSONB column defines who may manually run a workflow via `POST /api/workflows/{id}/run`:

```json
{
  "mode": "org_admin"
}
```

### Run Permission Modes

| Mode | Description |
|------|-------------|
| `org_admin` | Only org admins (default) |
| `any_member` | Any org member |
| `specific_roles` | Only members with specified role_ids; includes `role_ids` list |
| `specific_groups` | Only members of specified group_ids; includes `group_ids` list |

### Examples

```json
{
  "mode": "org_admin"
}
```

```json
{
  "mode": "any_member"
}
```

```json
{
  "mode": "specific_roles",
  "role_ids": ["uuid-1", "uuid-2"]
}
```

```json
{
  "mode": "specific_groups",
  "group_ids": ["uuid-1", "uuid-2"]
}
```

### Enforcement

The `can_run(ctx, run_permission)` function checks membership:

```python
# User must be an org member
# If mode = org_admin: user.is_org_admin must be True
# If mode = any_member: any member passes
# If mode = specific_roles: user's membership must include at least one role_id
# If mode = specific_groups: user's membership must include at least one group_id
```

Org admins **always** have permission to run workflows, regardless of `run_permission` mode.

### Custom Entity Access

Users can CRUD records of custom entities if they have org access (any org member). Workflow automation triggers
on record changes (create/update/delete) regardless of which user changed the record.

## API Authentication

### Clerk Session JWT

All API requests require a valid Clerk session JWT:

```http
Authorization: Bearer <token>
```

The token is validated with these checks:
- Issuer pinned to `CLERK_JWT_ISSUER` (configured from Clerk Dashboard)
- Signature verified against Clerk's JWKS at `{issuer}/.well-known/jwks.json`
- `azp` (authorized party) claim checked against allowlist in `CLERK_ALLOWED_AZP` (comma-separated origins)
- Token lifetime validated (Clerk tokens typically expire in ~60 seconds; SDK auto-refreshes)

### User Context

After validation, the API builds user context:

```python
@dataclass
class CurrentUser:
    keycloak_sub: str  # Clerk subject ID
    username: str
    email: str
    profile_id: uuid.UUID
    is_site_admin: bool

@dataclass
class OrgContext:
    user: CurrentUser
    org_id: uuid.UUID
    membership: UserOrgMembership | None
    is_org_admin: bool
```

## Service-to-Service Auth

### Brain API Key

Internal services authenticate with shared secrets:

```http
X-API-Key: ${BRAIN_API_KEY}
```

### Internal API Key

Worker-to-API callbacks use a separate key:

```http
X-Internal-API-Key: ${INTERNAL_API_KEY}
```

## Security Considerations

1. **RLS Enforcement**: PostgreSQL RLS prevents cross-tenant data access at the database level
2. **Mask Validation**: All mask values validated within range before encoding
3. **Key Rotation**: API keys should be rotated regularly via environment variables
4. **Audit Logging**: Permission changes logged with user context
5. **Token Expiry**: JWT tokens have limited lifetime (~60s); refresh handled automatically by Clerk SDK
6. **Per-org Secret Encryption**: Per-org OpenAI API keys are encrypted at rest (Fernet symmetric encryption) using the `ORG_ENCRYPTION_KEY` configured in the environment. Decryption happens only when the secret is needed (e.g., worker consumption via internal API), and the decrypted value is **never** logged or cached beyond its immediate use.
7. **Workflow Security**:
   - Manual run execution: record ownership validated if `record_id` provided; side-effecting actions (email/webhook/form) rejected on free-form data
   - Webhook delivery: targets validated against `WORKFLOW_WEBHOOK_ALLOWLIST` (SSRF guard)
   - Dispatch: exactly-once claiming via `FOR UPDATE SKIP LOCKED`; pg_advisory_lock for scheduled workflows
8. **Intake Form Security**: Links are single-use (token hashed, status transitions `pending → submitted`); optional expiry; email templating HTML-escaped
