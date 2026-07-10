/**
 * Per-element help for the Forms / Views builder (the element-tree `LayoutBuilder`).
 *
 * The builder has no single "selected element" — each element is an inline,
 * expandable card — so help is driven by FOCUS: when the user focuses a control
 * inside an element's card, that element's topic is pushed to the help dock
 * (innermost element wins, via focus capture). Topics are module-level constants
 * so re-focusing the same element hands the dock a stable reference (no churn).
 */
import type { PaletteKind } from "@/components/forms/builder/elementFactory";
import type { HelpTopic } from "@/lib/help";

const topic = (title: string, body: string): HelpTopic => ({ prefix: "", title, body });

export const BUILDER_HELP: Record<PaletteKind, HelpTopic> = {
  field: topic(
    "Field element",
    `
Binds an input to **one of the entity's fields** — this is how a form captures a
value into a record.

- **Field** — which entity field this reads and writes.
- **Label** — an optional override; blank uses the field's own name.
- **Width** — full, half, third, or quarter, so fields can sit side by side.
- **Required** — must be filled before the form submits.
- **Read-only** — shown but not editable (useful in views or for computed data).
`,
  ),
  label: topic(
    "Label / text element",
    `
Static text, **not tied to any data** — for instructions, section titles, or a
rule between groups.

Pick a **variant**:
- **Heading** / **Subheading** — titles that structure the page.
- **Paragraph** — a line of explanatory text.
- **Divider** — a horizontal rule with no text.
`,
  ),
  calculated: topic(
    "Calculated element",
    `
A value **derived by a JsonLogic expression** from the record's other fields —
evaluated live as the form is filled.

- **Label** — what the value is called on screen.
- **Result** — its type (text, integer, numeric, boolean, date, timestamp).
- **Save to** — optionally persist the result into a field; leave as *Display
  only* to show it without storing.
- **Expression** — JsonLogic over the record's fields (e.g. concatenate names,
  sum line items).
`,
  ),
  input: topic(
    "Input element",
    `
A **standalone input** whose value is *not* tied to an entity field — it lives in
the screen's state under a **key** you choose. Use it to gather ad-hoc values that
feed a button's workflow inputs or a calculated expression (reference it as
\`{ "var": "<key>" }\`).

- **Key** — the name the value is stored/referenced under.
- **Control** — text, textarea, number, **slider**, **toggle**, or select.
- **Min / Max / Step** — shape the number and slider controls.
- **Options** — the choices for a select.
- **Default** — the starting value.
`,
  ),
  live_value: topic(
    "Live value element",
    `
A **read-only readout** that polls an HTTP endpoint from the browser and shows a
value pulled from the JSON response — a generic way to display live external state
(a device reading, a queue depth, a status).

- **URL** — a CORS-reachable endpoint to poll.
- **JSON pointer** — dot path into the response body (e.g. \`head.pitch\`); blank
  shows the whole body.
- **Poll (ms)** — how often to refresh.
- **Units** — an optional suffix shown after the value.
`,
  ),
  chat: topic(
    "Chat element",
    `
A **conversation panel** backed by two entities — a conversation session and its
messages. It lists the active conversation's turns as bubbles (refreshing on a
poll) and its input **drives the robot**: sending creates a person message and runs
the answer workflow, so the robot searches the knowledge base, speaks a concise
reply, and records its turn.

- **Answer workflow id** — the workflow run on send (e.g. "Robot: Chat Answer"),
  called with \`{ text, conversation_id }\`. The run is fired **in the background**
  (the composer stays live and a typing indicator shows) — the reply arrives via the
  poll, so a slow answer never blocks or times out the chat.
- **Message / Conversation entity** — where turns and sessions are stored.
- **Conversation link slug** — the message → conversation relationship.
- **Poll (ms)** — how often the transcript refreshes.
- **Answer speed controls** — an optional live toggle row on the chat card. When
  shown, the viewer can trade quality for speed per turn, and the chosen values ride
  along as extra workflow \`inputs\`:
  - **Fast mode** → \`inputs.synthesize = false\` (retrieval-only: one LLM call, no
    graph hop — the biggest speedup).
  - **Knowledge graph** → \`inputs.use_knowledge_graph\` (only affects the non-fast
    synthesis path).
  - **Concise** → \`inputs.max_words\` (Concise words vs Full words).
  - **Speak aloud** → \`inputs.speak\` — whether the robot vocalizes the answer. The
    workflow's \`/say\` step must sit behind a gateway on \`inputs.speak\` (default on)
    so turning it off answers in text only.
  - **Answer model** → \`inputs.answer_model\` (pick a faster/cheaper tier).
  The workflow's \`knowledge_search\`/\`summarize\` nodes must reference these inputs
  (e.g. \`synthesize: {{ inputs.synthesize }}\`) for the toggles to take effect.
- **Wait filler** — optional "one moment…" chatter for slow answers. While the robot
  works, the chat drips out a randomized line (the first after **Delay**, then every
  **Interval**, up to **Max lines** then it falls silent) that keeps the asker engaged
  — some lines restate the question via a \`{q}\` placeholder. Each bubble is ephemeral (never stored) and clears the instant
  the real reply lands. Set a **Speak connection** (e.g. \`robot\`) and the filler is
  also spoken aloud, so the physical robot stalls out loud instead of going silent.
  Leave **Phrases** blank to use the built-in set.
`,
  ),
  button: topic(
    "Button element",
    `
An action control — how a form or view **kicks off something**.

- **Style** — primary, secondary, danger, or ghost.
- **Action**:
  - **Submit form** — save the record being edited.
  - **Run workflow** — start a workflow by id (with optional inputs).
  - **Call connection** — POST/GET straight to a saved connection (body templated
    from the screen's values); runs server-side with the connection's auth.
  - **Link / navigate** — go to a URL.
`,
  ),
  form_ref: topic(
    "Embedded form element",
    `
Embeds **another form inline** (views only), so you can compose a screen from
reusable form pieces.

- **Form** — which form to embed.
- **Label** — an optional heading shown above it.
`,
  ),
  section: topic(
    "Related record (1:1) element",
    `
Shows or edits a **single related record** through a to-one relationship — e.g.
a task's linked contact.

- **Relationship** — the to-one link to follow.
- **Mode** — **Inline** (rendered in place) or **Modal** (opened in a dialog).
- **Nested elements** — the related record's fields to show, chosen once you've
  picked a relationship.
`,
  ),
  table: topic(
    "Table (1:M) element",
    `
A **grid of related records** across a one-to-many relationship — line items,
sub-tasks, and the like.

- **Collection** — the one-to-many relationship to list.
- **Columns** — add **Field columns** (from the related entity) or **Related
  columns** (hop one relationship further); each can be made editable in place.
`,
  ),
  block: topic(
    "Repeating block (1:M) element",
    `
Like a table, but renders **each related record as a stacked mini-form** (its own
nested layout) rather than a single row.

- **Collection** — the one-to-many relationship to repeat over.
- **Nested elements** — the layout used for *each* child record.

Reach for this when a child needs more than a row of columns.
`,
  ),
  tab_group: topic(
    "Tabs element",
    `
Splits content into **tabs** the user switches between — good for breaking a long
form into digestible sections.

- **Add tab** — create a new tab and name it.
- Each tab holds its own nested elements.
`,
  ),
  accordion: topic(
    "Accordion element",
    `
**Collapsible panes** stacked vertically — like tabs, but expandable, and handy
for optional or advanced sections.

- **Add pane** — create a collapsible section and name it.
- Each pane holds its own nested elements.
`,
  ),
  columns: topic(
    "Columns element",
    `
A **multi-column layout**. Drop elements into each column to place them side by
side for a compact, balanced screen.

Each column holds its own nested elements; add elements to a column with its
inner **Add element** menu.
`,
  ),
  panel: topic(
    "Panel element",
    `
A **titled, bordered container** that visually groups related elements.

- **Title** — the panel heading.
- **Nested elements** — whatever you want grouped inside the box.
`,
  ),
};

/** Help for a builder element kind (stable reference per kind). */
export function helpForElement(kind: PaletteKind): HelpTopic {
  return BUILDER_HELP[kind];
}
