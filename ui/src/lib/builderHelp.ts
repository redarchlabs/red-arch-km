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
  image: topic(
    "Image element",
    `
A **picture** on the screen — the visual anchor a status page needs (a ship, a floor
plan, a product shot). Display only; it reads no data and writes none.

- **Image URL** — a relative path (e.g. \`/sim/ship-nominal.svg\`) or an \`http(s)\` URL.
  It may contain \`{token}\` placeholders filled from the record: \`{id}\` is the bound
  record id and \`{field_slug}\` any field value — so \`/sim/ship-{condition}.svg\`
  makes the artwork FOLLOW the record's state.
- **Alt text** — what the picture shows, for screen readers.
- **Caption** — optional line under the image.
- **Max height (px)** — caps the height; the image always scales down to fit its column.

Pair it with the view's **refresh** setting so the picture swaps as a workflow
changes the record.
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
- **Display map** — optional \`{ "true": "Thinking…", "false": "idle" }\` translation of
  the raw value, so a status flag reads as a status instead of as \`true\`. Values it
  doesn't name are shown unchanged.
`,
  ),
  progress: topic(
    "Progress bar element",
    `
A **display-only progress bar**. Its **value** is a JsonLogic expression over the
record's fields (or a literal); the bar fills \`value / max\`, clamped to that range.

- **Value** — the amount complete (e.g. a \`progress_pct\` field, or a computed sum).
- **Max** — the value that reads as 100% full (default 100).
- **Show percent** — draw the computed percentage on the bar.
`,
  ),
  slides: topic(
    "Slide deck element",
    `
Shows content as an **in-app slide deck** — a navigable presentation (prev / next
with progress dots) instead of a wall of scrolling text.

- **Source** — bind to a **JSON field** (a *slug*) holding a list of slides (the
  usual case, e.g. a module's \`slides\` field), or author **inline slides**.
- **Each slide** — an optional title, a **Markdown** body, an optional image, and
  an optional **video** (\`video_url\`, a direct mp4/webm).
- **Encourage viewing** — when a slide has a video, the forward controls stay
  disabled and forward seeks snap back until the learner watches it through. This is
  a client-side **nudge**, not enforced viewing (nothing is recorded server-side), so
  it deters casual skipping rather than guaranteeing it. Set \`require_video: false\`
  for a supplementary clip that shouldn't block.

Display-only, so it's valid in a standalone view too.
`,
  ),
  report: topic(
    "Report / chart",
    `
Embeds a **saved report** on a dashboard and draws its chart, KPI tile, or table
per the report's own visualization spec.

- **Report** — pick a saved report (build them on the Reports page).
- **Title** — an optional heading above the chart.
- **Height (px)** — chart height.
- **Poll (ms)** — re-run the report on a cadence for a live dashboard; blank runs
  once. The report defines the entity, the group-by / metrics, and the chart type.
`,
  ),
  record_list: topic(
    "Record list / status board",
    `
A **read-only table of an entity's records** — a live status board. Reads the
newest records (or sorted by a field) and can re-poll to stay current.

- **Entity** — which entity's records to list (by slug).
- **Fields** — the field slugs to show as columns; empty shows every field.
- **Filters** — server-side row filters, all ANDed (field slug + operator + value).
  A value of \`@me\` on a relation field (e.g. \`learner\`) scopes the board to the
  **current user's own records** — resolved server-side by email, so it can't be
  widened to other users' rows.
- **Sort by / direction** — a field slug (defaults to newest first).
- **Limit** — how many rows to show.
- **Poll (ms)** — set to keep the board live; blank fetches once.
- **Row workflow** — optionally add a per-row button that runs a workflow against
  that row's record (e.g. re-announce a mission-state row).
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
- **Size** — default is sized for a mouse; **large** and **XL** are for a view
  presented on a tablet or a wall display, where a finger is the pointer.
`,
  ),
  qr_code: topic(
    "QR code element",
    `
Turns a link into a **QR code**, so a screen can hand a URL to a phone or tablet
without anyone typing an address. The usual job: getting a shared iPad onto a
kiosk view.

- **Link** — where the code should point. Usually a relative path such as
  \`/views/<id>/kiosk?record_id={id}\`; \`{token}\` placeholders are filled from
  the record, exactly like a link button's href.
- **Host** — *the one that matters.* A relative link is resolved against whatever
  address **this page** was opened at. Open the console at \`localhost\` and the
  code will say \`localhost\`, which means *the tablet* to the tablet — it scans
  fine and then fails. Set Host to the machine's network address (e.g.
  \`http://192.168.0.30:3000\`) and the code works no matter how you opened the
  console. The card warns you when the link is only reachable locally.
- **Show as** — a button that opens a popup (good for a console that stays open
  all day), or always on screen.

The card also shows the encoded link as text and copies it on click, for when a
camera isn't handy.
`,
  ),
  puzzle_pad: topic(
    "Puzzle pad element",
    `
A **hands-on interactive surface** — the element for when the point is the doing,
not the answering. Use it for a repair console, a checklist drill, a training
exercise, or a kids' activity.

- **Kind** — what the person actually does:
  - **Choices** — big tap targets (multiple choice, sized for a finger).
  - **Number pad** — key a value in and send it (no on-screen keyboard covering
    the screen).
  - **Order the steps** — tap items into the right sequence.
  - **Wires** — *drag* a lead from a port to its match. Tapping both ends works
    too, which is what saves it on a small screen.
  - **Sort into bins** — *drag* items where they belong.
  - **Colour** — pick a colour, tap a region. Paint-by-label, or free paint.
- **Where the puzzle comes from** — set the prompt/spec/hint inline for a fixed
  puzzle, or point each at a **record field** so the pad follows whatever the
  record says now. A field with a value wins; the inline value is the fallback.
  Put the pad inside a *Related record* section to follow a "current puzzle" link.
- **Spec** — JSON whose shape depends on the kind (the editor shows an example
  for the kind you pick). A malformed spec renders as a clear message, not as
  half a puzzle.
- **When finished** — optionally run a workflow. Its inputs can read the outcome:
  \`solved\`, \`answer\`, \`attempts\`, \`elapsed_ms\`, plus any value on screen.

**Who decides "correct".** Choices and Number pad are *never told the answer* —
they report what was picked and a workflow grades it, so keep the answer field
out of the view. The other kinds have to be sent their target in order to be
drawn at all, so the pad grades those itself; treat \`solved\` as a player's word,
which is right for a game and wrong for an exam.

**Showing the answer afterwards.** The tile someone taps stays marked as theirs,
so a phone goes on showing what it sent. Point **Reveal answer from field** at a
field that is *empty while answering is open* and filled once it closes — the
workflow that closes the question writes it — and the pad then marks the right
answer, marks a wrong pick, and says both in words. Never point it at a field
that always holds the answer: that hands it to every device the moment the
puzzle is drawn. **One answer only** locks the pad after the first tap, until
the puzzle itself changes.
`,
  ),
  countdown: topic(
    "Countdown element",
    `
A live **time left** clock, counting down to a deadline that lives on the record.

- **Started at (field)** + **Seconds allowed** — the usual pairing: a workflow
  stamps the start (\`{{ now }}\`) when it opens the question, and the element adds
  the duration. **Seconds from field** takes that duration from the record instead,
  so each question can have its own.
- **Deadline (field)** — an absolute timestamp to count down *to*, if the record
  already carries one. It wins over the pair above.
- **When time is up** — what replaces the number at zero.

With no deadline on the record it draws **nothing**, so it can sit on a page
between questions without needing a *visible when* gate. It is display-only: it
never closes anything, it only shows how long is left, so a device with a wonky
clock costs you a cosmetic glitch and nothing more.
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
  card: topic(
    "Card element",
    `
A **dashboard tile** — a titled surface that groups whatever you put inside it.

Like a panel it keeps the same record scope as its parent; the difference is
treatment. A card matches the frame that reports and record lists draw for
themselves, so a dashboard built from cards reads as one set of surfaces.

- **Title / subtitle** — the tile heading.
- **Accent** — an optional colored top rule, drawn from the theme's own colors.
`,
  ),
  agent_timeline: topic(
    "Agent timeline",
    `
A **swim lane per agent** that worked the order, with each run, consult, answer and
approval placed on a shared clock — plus a lane for **you**, which appears only when
something is actually owed to a person.

A diary alone cannot show which agent is blocked on which, or how long a block has
lasted, because a list has no room for two things happening at once. Here a consult
is drawn as two events — the question and the reply — so the wait between them has a
visible length, and clicking a card opens the run's steps.

- **Work order** — leave blank to use the order this page is about (the view's
  record). Pin an id for a dashboard that always watches one standing order.
- **Poll (ms)** — refresh cadence for a live order; blank loads once, which is right
  for a finished one.
`,
  ),
  agent_diary: topic(
    "Agent diary",
    `
The order's **written record**, newest at the bottom, loading history as you scroll
up — it reads like a conversation rather than a document.

Entries are Markdown written by agents and are rendered as such. Only the newest
page loads up front, so an order with a long exchange does not lay out hundreds of
blocks nobody scrolls to.

- **Page size** — entries per fetch (1–100).
- **Height** — \`fill\` sizes to the viewport, for a diary that IS the screen.
`,
  ),
  approval_queue: topic(
    "Approvals waiting on you",
    `
The decisions an agent is **parked on**, with Approve and Deny attached.

An agent waiting for permission is otherwise invisible until someone opens the
inbox, so the work stalls with nothing on screen explaining why. Put this where the
stall is visible.

- **Scope** — \`work_order\` shows only this order's approvals; \`org\` shows
  everything you may decide.
- **Hide when empty** — nothing pending is the normal state, so an empty card is
  noise on a dashboard; turn it off for a page about one order.
`,
  ),
  work_order_create: topic(
    "File a work order",
    `
A form that **files a new work order**.

Its own element rather than a button on the list, because the place people file
work is often not the place they browse it — a "raise a request" tile on a home
dashboard is the common case.

- **Assign to** — assigning an agent is what makes the order *startable*. Leaving
  it unassigned files a request for a person to pick up, which is a real choice
  rather than a missing one.
- **Detail view** — where to go once the order exists. Without one the form just
  clears, which suits a kiosk that files and forgets.
- The detail box is the **brief the agent works from**, so it is worth filling in.
`,
  ),
  work_order_tasks: topic(
    "Work order tasks",
    `
The order's **checklist** and how far through it is.

Percent complete is the **server's** figure — done over total, excluding tasks
carried to another order — rather than a count taken here. Two places computing
progress disagree the moment that rule changes, and this number is what people
read to decide whether to chase an order.

- **Show progress** — the bar and percentage. Turn it off for a bare checklist.
- **Poll (ms)** — refresh while agents are working through it.
`,
  ),
  agent_activity: topic(
    "Live agent activity",
    `
A running agent's transcript **as it happens** — the model's reasoning, the tools
it calls, and their results — plus a box to say something back to it.

The **Diary** records what an agent *did*, once each step is saved. This shows it
thinking, while it thinks. Both are worth having: one answers "what happened on
this order", the other "what is happening right now".

Streamed over a WebSocket, so it is genuinely live rather than polled. If the
connection drops it reconnects on its own, backing off so a server restart is not
made worse by every open tab retrying at once.

- **Allow steer** — the box that talks back. What you type is **queued**, not
  injected: the agent picks it up at the top of its next turn. It is never spliced
  into a turn already in progress, because a message landing between a tool call
  and its result is rejected by the model provider outright and would end the run.
- **Height** — how tall the transcript pane is.

Steering needs something to steer, so the box stays disabled until a run has been
seen on this work order. Nothing is persisted from this view: the durable record
is the diary and the run's steps, and reloading the page starts the transcript
fresh from whatever happens next.
`,
  ),
  work_order_actions: topic(
    "Work order actions",
    `
The order's own lifecycle buttons — send for approval, approve, **start work**,
mark done, cancel.

The legal moves come from the server with the order, so the buttons shown are
exactly the transitions the state machine will accept; they change as the order
moves rather than being a fixed row.

**Start work** is the one that matters: on an order with an assigned agent it
queues that agent's run, which is what turns a filed request into work. An order
assigned to a disabled agent is refused, and the message names the agent.

- **Show summary** — the order's title and current status above the buttons. Turn
  it off on a page that already has its own heading.
- **Show mode** — the plan/manual/automatic picker (below).

**Mode** decides how much rope the agent gets on *this* order:

- **Plan only** — it reads, researches, delegates thinking to its reports and
  writes the task list, but every write, execution and outbound action is held
  back. It finishes by submitting the plan for your approval, and approving it is
  what starts the work: the order moves to Ask me first and a fresh run carries
  the plan out. Reject it and the agent revises. Reports are held to the same
  limit while planning.
- **Ask me first** — the default, and today's behaviour: it works the order but
  pauses for your approval on outbound actions.
- **Automatic** — it approves its own actions. Nothing pauses and nobody is
  asked, so an agent can send mail and call external tools unattended. Mode
  changes are written into the diary, because "who turned that off, and when" has
  to be answerable afterwards.

Automatic never widens what an agent may touch: grants and the role restrictions
still apply. It only removes the human from the loop on tools it already had.
`,
  ),
  work_order_list: topic(
    "Work order list",
    `
Work orders as a list, optionally narrowed to a few statuses.

Work orders are not entity records, so the Record list element cannot reach them —
which is the only reason this is its own element.

- **Statuses** — leave empty for all; narrow it for a "needs attention" tile.
- **Detail view** — the view a row opens, with the order passed as its record.
  Without one the rows are inert, which is right for a read-only wallboard.
`,
  ),
  stat: topic(
    "Stat tile",
    `
One **big number with a label** — the KPI row across the top of a dashboard.

A stat reads a **saved report**, the same as the Report element, so build the
report first (a report whose result is a single number). The report's own
settings decide how the number is formatted; if it defines a comparison metric,
the tile shows the change as a delta.

- **Label** — what the number is.
- **Trend** — which direction reads as good, for the delta's color. Choose
  *neutral* when neither direction is inherently good news (headcount, say).
`,
  ),
};

/** Help for a builder element kind (stable reference per kind). */
export function helpForElement(kind: PaletteKind): HelpTopic {
  return BUILDER_HELP[kind];
}
