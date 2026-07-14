/**
 * Per-tab help for the Admin and Site Admin pages. Each tab swaps in a different
 * manager, so the coarse route topic isn't enough — the active tab pushes its own
 * topic to the help dock (and clears it on unmount). Topics are module-level
 * constants for stable references.
 */
import type { HelpTopic } from "@/lib/help";

const topic = (title: string, body: string): HelpTopic => ({ prefix: "", title, body });

type AdminTabKey =
  | "regions"
  | "departments"
  | "roles"
  | "groups"
  | "tags"
  | "attributes"
  | "members"
  | "import_export"
  | "api";
type SiteAdminTabKey = "orgs" | "users" | "memberships" | "system" | "celery" | "emails" | "deployments";

// Regions / Departments / Roles / Groups all use the same DimensionManager, so
// they share one topic that names all four.
const DIMENSION_HELP = topic(
  "Permission dimensions",
  `
**Regions, Departments, Roles, and Groups** are the four **dimensions** that
folder and document permissions are built from.

- Define the values here (e.g. add a region *EMEA*, a department *Finance*).
- Each value carries a **permission number** used when composing viewer /
  contributor rules on folders and documents.
- Changing or deleting a value affects **every** permission rule that references
  it — review the impact before editing.

To grant a *person* access to specific values, use the **Members** tab.
`,
);

export const ADMIN_TAB_HELP: Record<AdminTabKey, HelpTopic> = {
  regions: DIMENSION_HELP,
  departments: DIMENSION_HELP,
  roles: DIMENSION_HELP,
  groups: DIMENSION_HELP,
  tags: topic(
    "Tags",
    `
**Tags** are free-form classification labels for documents. Unlike permission
dimensions they don't grant access — they help you **organize and filter**.

Create, rename, or remove tags here; apply them to documents, then use them to
scope search and chat.
`,
  ),
  attributes: topic(
    "Attributes",
    `
**Attributes** are custom **metadata fields** captured on documents (beyond the
built-in title/description).

- Define an attribute with a **type** — *freeform* text or a *picklist* of
  allowed options — and mark it **required** if every document must set it.
- Documents then carry these values, which you can display and filter on.
`,
  ),
  members: topic(
    "Members",
    `
Manage **who belongs to this organization** and what they can see.

- The left list is your members; **select one** to edit them on the right.
- **Org admin** grants full administrative control of this organization.
- The per-dimension toggles (regions, departments, roles, groups) set **which
  permission values** that member can access — this is what gates the folders
  and documents they can view or contribute to.
`,
  ),
  import_export: topic(
    "Import / Export",
    `
Migrate this organization's **configuration and data** to or from a portable
JSON bundle.

- **Export** selected resources — entities, forms, reports, views, workflows,
  connections, records, documents and more — into one downloadable file.
- **Import** a bundle into this org, choosing how to resolve name/slug
  collisions (skip, overwrite, or rename). Secrets are never exported.
`,
  ),
  api: topic(
    "API & Keys",
    `
Grant **external systems** programmatic access to this organization over the
REST API.

- **Create a key** with a name, a set of **scopes** (what it may do), and an
  optional **expiry**. The secret is shown **once** — copy it immediately.
- A key acts with **organization-wide data access**, limited to its scopes. It
  is *not* tied to a person; treat it like a shared service credential.
- **Revoke** a key at any time to cut off access instantly.

Callers authenticate with \`Authorization: Bearer km2_…\`. Full endpoint
documentation is linked from the **Using the API** panel.
`,
  ),
};

export const SITE_ADMIN_TAB_HELP: Record<SiteAdminTabKey, HelpTopic> = {
  orgs: topic(
    "Organizations",
    `
Create and manage **every organization** on this instance.

Each org is an isolated tenant with its own members, resources, and permission
dimensions. Actions here have instance-wide reach — proceed carefully.
`,
  ),
  users: topic(
    "Users",
    `
Every user account **across all organizations**.

- Search and page through the full user list.
- Toggle **site admin** to grant or revoke instance-wide administration.
- Toggle **active** to enable or deactivate an account (a deactivated user can't
  sign in).
`,
  ),
  memberships: topic(
    "Memberships",
    `
Manage org membership **from the outside** — as a site admin, without being a
member yourself.

**Select an organization** to see its members, then add or remove people and
toggle org-admin. Use this to bootstrap a new org or fix access when no one
inside can.
`,
  ),
  system: topic(
    "System status",
    `
Instance-wide **health and configuration** — service status and system settings
for the whole deployment.
`,
  ),
  celery: topic(
    "Celery",
    `
Monitor the **background task system** (Celery) that runs ingestion, workflow
sweeps, and other async jobs.

Check worker heartbeats, queue depth, and recent task activity here when
something that runs in the background seems stuck or delayed.
`,
  ),
  emails: topic(
    "Sent emails",
    `
Inspect **outgoing email** captured by the dev/staging mail catcher (Mailpit) —
so you can confirm what a workflow or form actually sent.

This proxies the mail container directly and is available in **dev/staging
only**; it has no per-organization scoping, which is why it lives under Site
Admin.
`,
  ),
  deployments: topic(
    "Deployments",
    `
The **change-management deployment log** across every organization — each config
promotion (and rollback), newest first.

Use it to see what was deployed where, by which release, and its outcome. It is a
read-only, cross-org audit view; org admins manage their own releases under the
**Releases** area.
`,
  ),
};
