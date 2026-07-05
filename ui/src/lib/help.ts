/**
 * Context-sensitive help registry.
 *
 * Each topic is keyed by a route prefix; the help button resolves the current
 * pathname to the MOST specific matching topic. Bodies are Markdown.
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
Resources organizes your knowledge base into a folder tree.

- **Browse**: click a folder to see its documents. Expand/collapse with the ▸ arrows.
- **Right-click** a folder (or use the **⋯** button) for actions:
  - **New subfolder** — create a child folder.
  - **Upload document here** — add a document straight into that folder.
  - **Properties** — rename, describe, and set **who can view or contribute**.
  - **Delete** — removes the folder (documents inside are not deleted).
- **Move** a folder by dragging it onto another folder.
- **Permissions**: a document inherits its folder's viewer/contributor rules at
  upload, but you can override them per document in the document's Properties.

The tree is virtualized, so it stays fast with thousands of folders.
`,
  },
  {
    prefix: "/documents/search",
    title: "Searching documents",
    body: `
Search runs a **semantic** query across every document you can access — enter a
question or a few terms, not just exact keywords.

- Results are ranked by relevance (the % shown), and the matched terms are
  **highlighted** in each snippet.
- Click a result to open the document; use **Read document** there to see it
  with its original formatting.
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

Documents keep their **original formatting**: Markdown, \`.docx\` and text render
with headings/lists/tables; PDFs and images are shown as the original file. Very
large documents load a section at a time as you scroll.
`,
  },
  {
    prefix: "/chat",
    title: "Chat",
    body: `
Ask questions in natural language; answers are grounded **only** in your
organization's documents (retrieval-augmented generation).

- Use the **Scope** selector to limit the answer to specific folders.
- Claims are cited inline with **[n]** markers that link to the source document,
  and each source is listed once below the answer.
- If nothing relevant is found, the assistant says so rather than guessing.
`,
  },
  {
    prefix: "/site-admin",
    title: "Site administration",
    body: `
Site admins manage the whole instance: organizations, global users, and
system-wide settings. Actions here affect **every** organization — proceed
carefully.
`,
  },
  {
    prefix: "/admin",
    title: "Organization administration",
    body: `
Manage this organization: members and their roles, and the permission
dimensions (regions, departments, roles, groups) that folder and document
permissions are built from.
`,
  },
];

const DEFAULT_TOPIC: HelpTopic = {
  prefix: "/",
  title: "Help",
  body: `
Welcome to Red Arch KM. Use the sidebar to move between **Resources**
(your folders and documents), **Search**, and **Chat**. Open help on any page
with the **?** button for guidance specific to what you're looking at.
`,
};

/** Resolve the most specific help topic for a pathname. */
export function helpForPath(pathname: string): HelpTopic {
  const match = TOPICS.filter((t) => pathname.startsWith(t.prefix)).sort(
    (a, b) => b.prefix.length - a.prefix.length,
  )[0];
  return match ?? DEFAULT_TOPIC;
}
