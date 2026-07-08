/**
 * Per-node help registry for the workflow designer.
 *
 * The context-sensitive help dock is route-driven ({@link ./help}); when the
 * user is on the designer AND has a node selected, it instead shows a detailed,
 * node-type-specific explanation resolved here. Keyed off the same subtype
 * resolvers the palette / canvas / inspector use, so the help always matches the
 * node the inspector is editing.
 *
 * Bodies are Markdown (rendered by the shared Markdown component) and reuse the
 * {@link HelpTopic} shape; `prefix` is unused for node help and left empty.
 */
import {
  nodeCategory,
  resolveEventPosition,
  resolveEventType,
  resolveGatewayType,
  resolveTaskType,
  subtypeLabel,
  WAIT_TASK_TYPES,
  type EventPosition,
  type EventType,
  type GatewayType,
  type TaskType,
} from "@/components/workflows/nodes/nodeMeta";
import type { HelpTopic } from "@/lib/help";

type HelpNode = { type?: string; data?: Record<string, unknown> | null };

/** A node help entry — `prefix` is unused here (node help isn't route-keyed). */
const topic = (title: string, body: string): HelpTopic => ({ prefix: "", title, body });

// --------------------------------------------------------------------------- //
// Trigger
// --------------------------------------------------------------------------- //
const TRIGGER_HELP = topic(
  "Trigger — the start",
  `
Every workflow starts here. A trigger **fires a new run when a record changes**
in a watched entity.

- **Operations** — which changes start a run: **create**, **update**, and/or
  **delete**.
- **Field filter** — optionally fire only when specific fields change, so
  unrelated edits don't kick off a run.

The record that changed is handed to the rest of the flow as **\`before\`** and
**\`after\`**, which downstream steps reference — e.g. \`{{after.status}}\` in a
message, or \`{"var": "after.status"}\` in a script expression.

> Only the **published** version of a workflow fires, and each run follows a
> single trigger.
`,
);

// --------------------------------------------------------------------------- //
// Tasks (activity nodes)
// --------------------------------------------------------------------------- //
const SCRIPT_HELP = topic(
  "Script task",
  `
Computes **run variables** from expressions — the workflow's calculator. It has
**no side effects**: no emails, API calls, or record writes.

**Each variable row** assigns one run variable from an expression, evaluated
against \`{ before, after, vars }\`:
- **\`before\` / \`after\`** — the record before and after the change that
  started the run.
- **\`vars\`** — variables set by earlier steps.

A value that is valid **JSON** is treated as a **JsonLogic** expression (e.g.
\`{"var": "after.status"}\`); anything else is stored as a **literal**. Results
**merge** into \`vars\` for later steps to read.

Expressions run in a **sandbox** — declarative logic only, never arbitrary code.

> A script task always succeeds and advances, so **Retry** and **Continue on
> failure** don't apply to it (and aren't shown).
`,
);

const BUSINESS_RULE_HELP = topic(
  "Business rule task",
  `
Evaluates a **decision table** — rows of input conditions mapped to output
values — against the changed record. The no-code way to say "if these fields
look like this, set these results."

Each row tests the record's fields; the matching row's outputs are written as
**run variables** (readable later as \`vars\`). Like a script task it is
**side-effect-free** — it decides values, it doesn't act on them.

Follow it with a **gateway** to branch on the result, or an **action task** to
apply it.
`,
);

/** Generic help for service / send tasks when no action is chosen yet. */
const SERVICE_TASK_HELP: Record<"service" | "send", HelpTopic> = {
  service: topic(
    "Service task",
    `
Runs an **automated action** with no human involvement — the workhorse of a
workflow.

Pick an **action** in the panel; that determines what it does (update a field,
create a record, call an API, send an email, and so on). The changed record is
available as \`before\` / \`after\`, and many fields accept \`{{after.<field>}}\`
templating.

If the action does real I/O (an API call, an email) it can fail — use **Retry
on failure** and **Continue the workflow if this task ultimately fails** to
control what happens on error.
`,
  ),
  send: topic(
    "Send task",
    `
Sends something **outward** — an email, an intake form, or a webhook.

Choose the **action** to set the destination and payload. Recipient and body
fields accept \`{{after.<field>}}\` / \`{{before.<field>}}\` templating to pull
values from the changed record.

Because a send can fail (bad address, unreachable host), the **Retry** and
**Continue on failure** options apply here.
`,
  ),
};

const WAIT_HELP: Record<"user" | "receive" | "call" | "subProcess" | "manual", HelpTopic> = {
  user: topic(
    "User task",
    `
Presents work to a **person** and **pauses the run until they complete it**.

- **Label** — what the assignee sees.
- **Assignee** — an email, user id, or role, resolved by the run engine.

While parked, other parallel branches keep running; the run resumes once the
task is actioned.
`,
  ),
  manual: topic(
    "Manual task",
    `
Marks a step performed **outside the system** (a phone call, a physical task).
Like a user task it **waits until someone marks it done**.

Set a label and, if useful, an assignee. Nothing is automated — it's a
checkpoint for offline work so the run doesn't race ahead of reality.
`,
  ),
  receive: topic(
    "Receive task",
    `
**Waits for an inbound message** before continuing. The run parks here until a
matching message arrives — from another workflow, an API callback, or an
external system.

Pair it with a send / throw elsewhere that delivers that message.
`,
  ),
  call: topic(
    "Call task",
    `
**Calls another workflow** and waits for it to finish before continuing. Use it
to reuse a shared sub-flow across workflows.

The parent run parks until the called workflow completes, then picks up where it
left off.
`,
  ),
  subProcess: topic(
    "Sub-process task",
    `
Runs an **embedded sub-flow** as a single step and waits for it to complete.

Use it to group a set of steps into one reusable, collapsible unit inside this
workflow — keeping the top-level diagram readable.
`,
  ),
};

// --------------------------------------------------------------------------- //
// Actions (the concrete behaviour of a service / send task)
// --------------------------------------------------------------------------- //
const ACTION_HELP: Record<string, HelpTopic> = {
  update_record_field: topic(
    "Action: Update a field",
    `
Writes a **new value to one field** on the record that triggered the run.

Set the **field slug** and the **value** — a literal, or \`{{after.<field>}}\`
to copy from another field. It updates the changed record **in place**; no new
record is created.
`,
  ),
  create_record: topic(
    "Action: Create a record",
    `
Creates a **new record in another entity**.

Pick the **target entity** and provide its **values** as JSON; values may
reference the changed record with \`{{after.<field>}}\`. Use it to spin off a
task, log entry, or related record when something changes.
`,
  ),
  send_form: topic(
    "Action: Email an intake form",
    `
Emails an **intake form** to a recipient so they can fill it in for the changed
record.

Choose the **form** and the **field that holds the recipient's email**. The link
is scoped to this record, and a submission can drive further workflow steps.
`,
  ),
  send_email: topic(
    "Action: Send an email",
    `
Sends a plain email. Set **To**, **Subject**, and **Message**.

Every field accepts templating — a literal address or \`{{after.email}}\`, and
\`{{after.<field>}}\` / \`{{before.<field>}}\` in the subject and body to
personalise it. Delivery can fail, so **Retry** / **Continue on failure** apply.
`,
  ),
  send_webhook: topic(
    "Action: Send a webhook",
    `
Sends an **HTTP POST** to a URL with a JSON body (the record change, plus any
**extra body** you add). Use it to notify an external system.

For safety, webhook hosts are checked against the **trusted-hosts allowlist**.
Failures honour **Retry** / **Continue on failure**.
`,
  ),
  http_request: topic(
    "Action: Call a connected API",
    `
Makes a full **HTTP request** through a saved **connection** (base URL + auth
managed centrally, so no secrets live in the workflow).

Set the **method, path, headers, and body**, and optionally **capture** the
response into a run variable for later steps to use. Outbound hosts are checked
against the **trusted-hosts allowlist**.

Because it's real I/O, **Retry on failure** and **Continue on failure** matter
here.
`,
  ),
  log: topic(
    "Action: Log a message",
    `
Writes a **message to the run log** and nothing else — no external effect.

Handy as a checkpoint while building or debugging a workflow, or to leave a
breadcrumb in the run history. Supports \`{{after.<field>}}\` templating.
`,
  ),
};

// --------------------------------------------------------------------------- //
// Gateways
// --------------------------------------------------------------------------- //
const GATEWAY_HELP: Record<GatewayType, HelpTopic> = {
  exclusive: topic(
    "Exclusive gateway",
    `
Picks exactly **one** outgoing path (XOR).

Two routing modes:
- **Condition (true / false)** — evaluate one expression and take the *true* or
  *false* branch.
- **Cases** — test several expressions in order and take the **first** that
  matches; a **default** branch catches everything else.

Only one branch ever runs, so the paths don't have to rejoin.
`,
  ),
  parallel: topic(
    "Parallel gateway",
    `
**Forks** the token down *all* outgoing paths at once, so the branches run
concurrently (AND).

A matching parallel gateway on the join side **waits for every** incoming branch
before continuing. Conditions are ignored — every path is always taken. Use it
to do independent work in parallel, then regroup.
`,
  ),
  inclusive: topic(
    "Inclusive gateway",
    `
Takes **every** branch whose condition is true — one, several, or all (OR).

On the join side it waits only for the branches that were actually activated.
Use it when more than one path can apply but you don't want *all* of them
unconditionally the way a parallel gateway would.
`,
  ),
  event_based: topic(
    "Event-based gateway",
    `
A **race**. The run waits here, then follows whichever downstream event fires
**first** — a message, timer, or signal — and discards the losing branches.

Use it for "whichever happens first" logic, e.g. wait for a reply **or** time
out after an hour.
`,
  ),
};

// --------------------------------------------------------------------------- //
// Events (position × type)
// --------------------------------------------------------------------------- //
const POSITION_INTRO: Record<EventPosition, string> = {
  intermediate:
    "**Intermediate event** — sits in the middle of a flow. A *catch* pauses the run until it fires; a *throw* emits and continues immediately.",
  end: "**End event** — finishes this path of the flow when the token reaches it.",
  boundary:
    "**Boundary event** — attached to a task. It watches that task and fires if its condition occurs, diverting the flow (interrupting the task, or running a parallel path).",
};

const EVENT_TYPE_BODY: Record<EventType, string> = {
  timer:
    "A **timer** waits for a set duration or until a specific time, then releases the token — a delay, deadline, or scheduled step. On a boundary it acts as a **timeout** for the task it's attached to.",
  message:
    "A **message** coordinates a specific, addressed message. As a catch it waits for that named message to arrive; as a throw it sends one. Messages are **point-to-point** (one sender, one receiver).",
  signal:
    "A **signal** is a **broadcast**. A throw fires once and every catch waiting on that signal name reacts. Use it for fan-out notifications rather than one-to-one messaging.",
  error:
    "An **error** models a failure. Raised at an end event it aborts the sub-flow with a named error code; caught on a task **boundary** it diverts the flow down an error path so you can handle the failure.",
  escalation:
    "An **escalation** flags a condition that needs attention **without aborting** the flow (e.g. \"needs a manager\"). Unlike an error it's non-fatal — the main path can keep running while a boundary escalation handles it.",
  terminate:
    "A **terminate** event ends the **entire run** immediately — every active branch is stopped, not just this path. Use it for a hard stop / cancel; it's most meaningful as an end event.",
  none:
    "A **plain** event carries no trigger. A plain end simply finishes the path; a plain intermediate is a waypoint that passes the token straight through.",
};

const DELAY_HELP = topic(
  "Delay",
  `
Pauses the run for a **fixed amount of time**, then continues. Set the amount and
unit (e.g. 30 minutes).

The engine parks the token and a background sweep resumes it once the delay
elapses. (Delay is the legacy form of a **timer** event.)
`,
);

// --------------------------------------------------------------------------- //
// Resolution
// --------------------------------------------------------------------------- //
function taskHelp(node: HelpNode): HelpTopic {
  const taskType: TaskType = resolveTaskType(node);
  if (taskType === "script") return SCRIPT_HELP;
  if (taskType === "businessRule") return BUSINESS_RULE_HELP;
  if (WAIT_TASK_TYPES.includes(taskType)) {
    return WAIT_HELP[taskType as keyof typeof WAIT_HELP];
  }
  // service / send — the concrete behaviour is the chosen action.
  const actionType = typeof node.data?.action_type === "string" ? node.data.action_type : "";
  return (
    ACTION_HELP[actionType] ??
    SERVICE_TASK_HELP[taskType as keyof typeof SERVICE_TASK_HELP] ??
    SERVICE_TASK_HELP.service
  );
}

function gatewayHelp(node: HelpNode): HelpTopic {
  // Legacy `condition`/`switch`/`merge`/`passthrough` map to the exclusive shape.
  if (node.type === "condition" || node.type === "switch") return GATEWAY_HELP.exclusive;
  return GATEWAY_HELP[resolveGatewayType(node)];
}

function eventHelp(node: HelpNode): HelpTopic {
  if (node.type === "delay") return DELAY_HELP;
  const eventType = resolveEventType(node);
  const position = resolveEventPosition(node);
  return topic(
    `${subtypeLabel(node)} event`,
    `\n${POSITION_INTRO[position]}\n\n${EVENT_TYPE_BODY[eventType]}\n`,
  );
}

/**
 * Detailed help for a selected designer node, or `null` if the node type isn't
 * recognised (the dock then falls back to route help).
 */
export function helpForNode(node: HelpNode | null | undefined): HelpTopic | null {
  if (!node) return null;
  const category = nodeCategory(node.type);
  if (category === "trigger") return TRIGGER_HELP;
  if (category === "activity") return taskHelp(node);
  if (category === "gateway") return gatewayHelp(node);
  if (category === "event") return eventHelp(node);
  return null;
}
