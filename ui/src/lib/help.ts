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
    prefix: "/setup",
    title: "First-run setup",
    body: `
Welcome — this instance needs to be initialized.

### What to do
1. Paste the **setup token** printed in the server logs when the instance first
   started. It proves you're the operator bootstrapping the system.
2. This promotes your account to **site admin** and lets you create the first
   organization.

Once setup completes you won't see this page again. If you don't have the token,
check the API server's startup logs (or ask whoever deployed the instance).
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
  const match = TOPICS.filter((t) => pathname.startsWith(t.prefix)).sort(
    (a, b) => b.prefix.length - a.prefix.length,
  )[0];
  return match ?? DEFAULT_TOPIC;
}
