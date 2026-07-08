/**
 * Context-sensitive help registry.
 *
 * Each topic is keyed by a route prefix; the help panel resolves the current
 * pathname to the MOST specific matching topic. Bodies are Markdown and render
 * through the shared Markdown component (headings, lists, tables, code).
 *
 * Keep entries task-oriented: what the page is for, the actions available, how
 * permissions apply, and a few tips — enough to be genuinely useful without a
 * separate manual.
 */
export interface HelpTopic {
  /** Route prefix this topic applies to (most specific match wins). */
  prefix: string;
  title: string;
  body: string;
  /**
   * Optional pattern for routes a prefix can't isolate (e.g. an id in the
   * middle: `/workflows/<id>/runs`). A `match` topic wins over prefix matches.
   * Give such a topic a non-colliding `prefix` so it never wins by prefix.
   */
  match?: RegExp;
}

const TOPICS: HelpTopic[] = [
  {
    prefix: "/folders",
    title: "Resources & folders",
    body: `
**Resources** is your knowledge base, organized as a folder tree on the left
with the selected folder's contents on the right.

### Browsing
- **Click** a folder to select it — its subfolders and documents appear in the
  right pane. You don't have to drill in to see what's inside.
- Expand or collapse a branch with the **▸ / ▾** arrows.
- The right pane is sortable by **Name, Type, Modified, or Size** — click a
  column header (click again to reverse). Switch between **Details, List,
  Small icons, and Large icons** with the view buttons.

### Actions
Right-click a folder (or use the **⋯** button / the toolbar) for:
- **New subfolder** — create a child folder.
- **Upload document here** — add a document straight into that folder.
- **Properties** — rename, describe, and set **who can view or contribute**.
- **Delete** — removes the folder. Documents inside are *not* deleted.

**Move** a folder by dragging it onto another folder.

### Permissions
A document **inherits** its folder's viewer/contributor rules at upload time,
and you can override them per-document in that document's **Properties**.
Permissions are built from your organization's dimensions (regions,
departments, roles, groups) — an admin manages those under **Admin**.

> The tree and contents are virtualized, so they stay fast with thousands of
> folders and documents.
`,
  },
  {
    prefix: "/documents/search",
    title: "Searching documents",
    body: `
Search runs a **semantic** query across every document you're allowed to see —
enter a question or a few concepts, not just exact keywords. "How do we handle
refunds?" works as well as \`refund policy\`.

### Reading results
- Results are ranked by **relevance** (the % shown). Higher means a closer
  match to your query's meaning.
- The terms you searched are **highlighted** in each snippet so you can see why
  a result matched.
- Click a result to open the document, then choose **Read document** to see it
  with its original formatting and section summaries.

### Tips
- Searching only returns content from documents your permissions grant you —
  results are always scoped to what you can access.
- Longer, more specific queries usually beat single keywords.
- Need a synthesized answer instead of a list of snippets? Use **Chat**, which
  reads across sources and cites them.
`,
  },
  {
    prefix: "/documents/",
    title: "Reading a document",
    body: `
This is a single document. Choose **Read document** for the full-screen reader.

### View modes
- **Side-by-side** — section summaries on the left, full text on the right. The
  panes **scroll together**, so each summary stays aligned with the text it
  describes.
- **Embedded** — each section's summary shown inline, just above its text.

### Original formatting
Documents keep their **original formatting**:
- **Markdown, \`.docx\`, and plain text** render with headings, lists, and
  tables.
- **PDFs and images** show the extracted text (scroll-synced with the
  summaries); the **Original** button opens the untouched file in a new tab for
  pixel-perfect fidelity.

Very large documents — even a whole book — load a **section at a time** as you
scroll, so the reader stays responsive.

### Permissions
Use **Properties** to adjust who can view or contribute to *this* document,
independent of its folder.
`,
  },
  {
    prefix: "/documents",
    title: "Documents & the reader",
    body: `
Open a document and choose **Read document** for the full-screen reader:

- **Side-by-side** — section summaries on the left, the full text on the right.
  The panes scroll together so each summary stays aligned with its text.
- **Embedded** — each section's summary shown inline above its text.

Documents keep their **original formatting**: Markdown, \`.docx\`, and text
render with headings, lists, and tables; PDFs and images are shown as the
original file. Very large documents load a section at a time as you scroll.

To add documents, go to **Resources**, pick a folder, and choose **Upload
document here** — new documents inherit that folder's permissions.
`,
  },
  {
    prefix: "/chat",
    title: "Chat",
    body: `
Ask questions in natural language. Answers are grounded **only** in your
organization's documents (retrieval-augmented generation) — the assistant
retrieves relevant passages and answers from them, not from general knowledge.

### Working with answers
- Claims are **cited inline** with **[n]** markers that link to the source
  document. Each source is listed once below the answer, even if several
  passages came from it.
- Use the **Scope** selector to limit an answer to specific folders — handy for
  focusing on one project or excluding drafts.
- If nothing relevant is found, the assistant **says so** rather than guessing.

### Tips
- Ask follow-ups — the conversation keeps context.
- If an answer seems thin, broaden the scope or rephrase; if it's off-topic,
  narrow the scope to the right folders.
- Chat only ever draws on documents your permissions allow you to read.
`,
  },
  {
    prefix: "/workflows",
    title: "Workflows",
    body: `
**Workflows** automate what happens when a record changes — send an email,
update a field, call an API, route work to a person, and more.

### The list
- **Create** a workflow, then open it to build it in the designer.
- A workflow only runs once it's **published** — drafts are for editing.
- Each workflow starts from a single **trigger** (a record create / update /
  delete on a watched entity).

### The designer
- **Drag** a node from the palette onto the canvas, or click to place it, then
  **drag between handles** to connect steps.
- **Select any node** to configure it on the right — and this help panel will
  show a detailed explanation of that node type.
- **⌘K** opens the command palette; **Auto-layout** tidies the diagram.
- **Save draft** as you go; **Publish** when you're ready for it to run.

> Tip: use the **Test (dry run)** panel to simulate a record change against the
> saved version without writing any data.
`,
  },
  {
    prefix: "/workflows/connections",
    title: "Connections",
    body: `
**Connections** are reusable, centrally-stored API credentials — a base URL plus
its authentication — that workflow **"Call a connected API"** actions use, so
secrets never live inside a workflow.

### Creating one
- Give it a name, a **base URL**, and an **auth type**: none, **API key** (a
  header name + secret), **basic** (username + password), or **bearer** (a
  token).
- Edit or delete a connection here; a workflow's HTTP action just picks it by
  name.

> Because credentials live here, you can rotate a secret in one place and every
> workflow that uses the connection picks it up.
`,
  },
  {
    prefix: "/workflows/webhooks",
    title: "Inbound webhooks",
    body: `
**Webhooks** let an external system **start a workflow** by POSTing to a
generated URL — the inbound counterpart to the outbound webhook action.

### Setting one up
1. Create an endpoint: give it a name and pick the **workflow it triggers**.
2. You're shown a **URL and a signing secret** — copy them now; the **secret is
   shown only once**.

### How requests are handled
- Incoming requests must be **HMAC-signed** (\`X-KM2-Signature\`) with that
  secret and are verified before anything runs.
- A verified request executes its workflow **inline** (no polling delay), so the
  caller gets an immediate result.

Use these to wire external events — a form service, a device, another app —
straight into your automations.
`,
  },
  {
    prefix: "/workflows/inbox",
    title: "Task inbox",
    body: `
The **inbox** lists workflow runs paused on a **human step** — a *user* or
*manual* task — that are waiting for someone to act.

- Each item shows which workflow it's from and what it's waiting on.
- **Approve / reject** (or otherwise complete) the task to **release the run**
  so it continues down the next path.

This is the human-in-the-loop side of automation: a run parks at a user task and
sits here until a person resolves it, then the engine picks up where it left off.
`,
  },
  {
    prefix: "/entities",
    title: "Entities & data model",
    body: `
**Entities** are your structured data model — custom record types (e.g. *Task*,
*Contact*, *Asset*) with typed fields and relationships. They're what forms
capture into, what views display, and what workflow triggers watch.

### The list
Create, open, or delete an entity. Opening one shows its **schema** and its
**records**.

### Schema editor
- **Fields** — add a field with a **type**: text / long text, integer / bigint /
  numeric, boolean, date, timestamp, uuid, json, or **picklist** (a fixed set of
  options). Mark a field **unique** to enforce no duplicates.
- **Relationships** — link entities with a **cardinality** (one-to-one,
  one-to-many, many-to-one, many-to-many), a target entity, and an **on-delete**
  rule (set null, cascade, or restrict) that decides what happens to related
  records when one is deleted.

### Records
The records table lets you add, edit, and delete individual records directly —
the same data your forms write and your views read.
`,
  },
  {
    prefix: "/forms",
    title: "Forms",
    body: `
**Forms** capture structured input **into an entity**. The list lets you create,
open, or delete a form (each is bound to one entity).

### Building a form
Compose it from an **element tree** — add and arrange:
- **Fields** (bound to the entity's fields), **labels/text**, and **calculated**
  values (derived with an expression).
- **Buttons** that submit, run a workflow, or link out.
- **Tables** and **related-record** sections for one-to-many / one-to-one links.
- **Layout containers** — sections, tabs, accordions, columns, panels — to
  structure the page.

Each element has its own settings (width, required, read-only, and so on).
**Preview** to see exactly what a recipient gets.

### Sharing & filling
- **Send a link** generates a single-use link to email the form for a specific
  record.
- The **Fill** view is internal data entry against the same form. Submitting can
  **trigger a workflow** via a button.
`,
  },
  {
    prefix: "/views",
    title: "Views",
    body: `
**Views** are read/display layouts built with the **same element-tree builder as
forms**, but for *showing* records rather than capturing them. A view can be
**bound to an entity** or **standalone**.

### Building a view
Compose it from labels/text, **buttons** (which run workflows or link out),
**embedded forms**, **tables** of related records, and **layout containers**
(sections, tabs, columns, panels). Entity-bound fields are available when the
view is tied to an entity.

### Using it
Open the **runtime viewer** to see the view rendered with real data; its buttons
run workflows in place. Use views for dashboards, record detail pages, and
action launchers.
`,
  },
  {
    prefix: "/site-admin",
    title: "Site administration",
    body: `
Site admins manage the **whole instance**, across every organization.

### What you can do here
- **Organizations** — create, view, and manage the orgs on this instance.
- **Global users** — see all users, activate or deactivate accounts, and grant
  or revoke site-admin access.
- **System settings** — instance-wide configuration.

> Actions here affect **every** organization. Deactivating a user or changing
> an org's status has broad reach — proceed carefully, and prefer the least
> change that solves the problem.

For settings that affect only one organization (its members, roles, and
permission dimensions), use **Admin** instead.
`,
  },
  {
    prefix: "/admin",
    title: "Organization administration",
    body: `
Manage **this organization**.

### Members & roles
Invite or remove members and set each member's role. Roles determine what a
person can do within the org.

### Permission dimensions
Folder and document permissions are built from your org's **dimensions**:
- **Regions, departments, roles, groups** — define the values here, then use
  them when setting a folder's or document's viewer/contributor rules.
- Changing a dimension affects every permission rule that references it, so
  review the impact before editing or deleting values.

Scope is limited to this organization. Instance-wide concerns (other orgs,
global users) live under **Site Admin**.
`,
  },
  {
    // The id in the middle of the path can't be isolated by a prefix, so this
    // topic matches by regex; the prefix is a sentinel that never prefix-matches.
    prefix: "/workflows/runs",
    match: /^\/workflows\/[^/]+\/runs(\/|$)/,
    title: "Run monitor",
    body: `
Every **execution of this workflow**, newest first. Use it to see what a
workflow actually did — and to debug when it didn't do what you expected.

- **Click a run** to expand its **steps** and see what happened at each node,
  including inputs, outputs, and any error.
- **Statuses**: *running*, *waiting* (parked on a human task, timer, or event),
  *succeeded*, *failed*, and *dead-lettered* (retries exhausted with no catcher
  — needs a manual replay).
- Deep-link a specific run with \`?run=<id>\`.
`,
  },
];

const DEFAULT_TOPIC: HelpTopic = {
  prefix: "/",
  title: "Getting started",
  body: `
Welcome to **Red Arch Knowledge Manager** — a knowledge base your team can search and ask
questions of.

### The essentials
- **Resources** — your folders and documents. Browse the tree, upload files,
  and set who can see what.
- **Search** — find passages across everything you can access, with the matched
  terms highlighted.
- **Chat** — ask questions and get answers grounded in your documents, with
  inline citations back to the source.
- **Admin / Site Admin** — manage members, roles, permission dimensions, and
  (for site admins) organizations and global users.

### Getting help
This panel is **context-sensitive**: it updates to match whatever page you're
on. On desktop it stays docked on the right; on smaller screens it opens as a
drawer. Toggle it any time with the **?** button in the header.
`,
};

/** Resolve the most specific help topic for a pathname. */
export function helpForPath(pathname: string): HelpTopic {
  // A regex `match` topic wins over prefix matching (for routes with an id in
  // the middle that no prefix can isolate).
  const byPattern = TOPICS.find((t) => t.match?.test(pathname));
  if (byPattern) return byPattern;
  const byPrefix = TOPICS.filter((t) => pathname.startsWith(t.prefix)).sort(
    (a, b) => b.prefix.length - a.prefix.length,
  )[0];
  return byPrefix ?? DEFAULT_TOPIC;
}
