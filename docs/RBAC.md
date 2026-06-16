# RBAC (Role-Based Access Control)

Red Arch KM implements a fine-grained permission system using 32-bit access masks for efficient folder and document access control.

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
- Cannot be set via API (manual DB operation)

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

1. User authenticates via Keycloak
2. API creates/updates user_profiles with Keycloak sub
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

### Inheritance

Documents inherit access from their parent folder:

1. Document created in folder
2. Folder's view_permission_masks copied to document's access keys
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

## API Authentication

### Keycloak JWT

All API requests require a valid Keycloak JWT:

```http
Authorization: Bearer <token>
```

The token is validated against Keycloak's JWKS endpoint.

### User Context

After validation, the API builds user context:

```python
@dataclass
class CurrentUser:
    keycloak_sub: str
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
5. **Token Expiry**: JWT tokens have limited lifetime; refresh handled by Keycloak
