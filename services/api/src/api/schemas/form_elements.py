"""The form **layout tree** — the authoring schema for the flexible form designer.

A form's ``config`` is a recursive tree of typed *elements*. Unlike the old flat
``{fields, sections}`` shape, elements compose arbitrarily: layout containers
(tabs, panels, accordions, columns) nest other elements; entity-bound inputs
(``field``), presentational ``label``s, ``calculated`` values, related-entity
``section``s (1:1), and editable ``table``s (1:M, incl. cross-entity columns)
are the leaves that carry data.

Key invariants (enforced here + in ``FormService._validate_config``):

* Every element is a Pydantic model with ``extra="forbid"`` — an unknown key is
  a 422, never silently stored. Add new presentational attrs as explicit fields.
* Only ``field``/``section``/``table`` columns bind to entity data (by ``slug``
  / ``relationship_id``); the underlying entity field type still drives
  coercion + validation (``repositories/dynamic_entity.py``). Authors never
  choose a field's data type.
* The tree is a discriminated union on ``type`` — the value is authoritative and
  the model is picked by it, so malformed elements fail fast.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ------------------------------------------------------------------ #
# Shared presentational vocabulary
# ------------------------------------------------------------------ #
# Column width in the responsive grid: full spans the row, half shares it.
FieldWidth = Literal["full", "half", "third", "quarter"]

# Picklist render style (presentational only; value is still one of the options).
FieldDisplay = Literal["dropdown", "radio"]

# How a 1:1 related record is surfaced.
SectionMode = Literal["inline", "modal"]

# The formatting/coercion intent of a computed value. Mirrors the entity
# field-type vocabulary subset that a calculation can produce; drives display
# formatting on the client and server-side coercion when persisted.
ResultType = Literal["text", "integer", "numeric", "boolean", "date", "timestamptz"]

# A JsonLogic expression (dict/list) or a literal (str/int/float/bool/None),
# evaluated by the sandboxed evaluator (``services/form_expression.py`` /
# ``ui/src/lib/forms/jsonLogic.ts``). Never arbitrary code.
Expression = Any


class _Element(BaseModel):
    """Common base: a stable ``id`` (for React keys + granular agent edits) and
    the discriminator ``type`` supplied by each concrete element."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None  # stable per-element id; server fills one if omitted


# ------------------------------------------------------------------ #
# Leaf elements (carry data or static presentation)
# ------------------------------------------------------------------ #
class FieldElement(_Element):
    """One entity field, bound by ``slug``, with presentation overrides."""

    type: Literal["field"] = "field"
    slug: str
    label: str | None = None
    required: bool | None = None  # override the entity field's own requiredness
    read_only: bool = False  # render prefilled + non-editable; never written back
    help_text: str | None = None
    placeholder: str | None = None
    width: FieldWidth | None = None
    display: FieldDisplay | None = None  # picklist render style


class LabelElement(_Element):
    """Static presentational content — not bound to any entity field."""

    type: Literal["label"] = "label"
    text: str = ""
    variant: Literal["heading", "subheading", "paragraph", "divider"] = "paragraph"
    width: FieldWidth | None = None


class CalculatedElement(_Element):
    """A derived value from a sandboxed expression over the form's other values.

    Display-only when ``target_slug`` is ``None``; otherwise the server
    recomputes it authoritatively and writes it to that entity field on submit
    (a client-sent value is never trusted for a persisted calculation)."""

    type: Literal["calculated"] = "calculated"
    label: str | None = None
    expression: Expression = None
    result_type: ResultType = "text"
    target_slug: str | None = None  # persist to this entity field, else display-only
    help_text: str | None = None
    width: FieldWidth | None = None


# The widget an ``input`` renders. Presentational only — the value is coerced by the
# control (numbers for number/slider, boolean for toggle, string otherwise).
InputControl = Literal["text", "textarea", "number", "slider", "toggle", "select"]


class InputOption(BaseModel):
    """One choice for a ``select`` input."""

    model_config = ConfigDict(extra="forbid")
    value: str
    label: str | None = None


class InputElement(_Element):
    """A standalone (unbound) input whose value lives in form state under ``key`` — not
    tied to any entity field. It exists so a form/view can gather ad-hoc values and feed
    them into a workflow-button's ``inputs`` (``{"var": "<key>"}``) or a ``calculated``
    expression, without a backing record. ``control`` picks the widget (text/textarea/
    number/slider/toggle/select); ``min``/``max``/``step`` shape number+slider, and
    ``options`` populate select. Never persisted to an entity on submit."""

    type: Literal["input"] = "input"
    key: str  # where the value lives in form state (expression var name)
    control: InputControl = "text"
    label: str | None = None
    placeholder: str | None = None
    help_text: str | None = None
    default: str | float | bool | None = None
    required: bool = False
    width: FieldWidth | None = None
    # numeric shaping (control = number | slider)
    min: float | None = None
    max: float | None = None
    step: float | None = None
    # choices (control = select)
    options: list[InputOption] = Field(default_factory=list)


class LiveValueElement(_Element):
    """A display-only readout that polls an HTTP endpoint from the browser and shows a
    value pulled out of the JSON response — a generic 'live external state' element (a
    device reading, a queue depth, anything). Not entity-bound, so it is valid in a
    standalone view. ``url`` must be a CORS-reachable endpoint; ``json_pointer`` is a
    dot path into the response body (e.g. ``head.pitch``); ``poll_ms`` sets the cadence."""

    type: Literal["live_value"] = "live_value"
    label: str | None = None
    url: str
    json_pointer: str | None = None  # dot path into the JSON body; whole body if None
    poll_ms: int = 1000
    units: str | None = None
    width: FieldWidth | None = None


class ReportElement(_Element):
    """Embeds a saved report on a dashboard — renders its chart, KPI tile, or table
    per the report's own visualization spec (fetched from ``/reports/{id}/run``).

    Not bound to the view's root record, so it is valid in a standalone view.
    ``report_id`` references a saved report; ``title`` overrides the heading;
    ``height`` sizes the chart in px; ``poll_ms`` re-runs on a cadence for a live
    dashboard. The report's ``viz`` decides how the aggregate result is drawn."""

    type: Literal["report"] = "report"
    report_id: uuid.UUID
    title: str | None = None
    height: int | None = None
    poll_ms: int | None = None
    width: FieldWidth | None = None


class RecordListElement(_Element):
    """A read-only display of existing records of an entity — a live "status board".

    Reads ``entity`` (by slug) newest-first (or by ``sort_by``/``sort_dir``), showing
    at most ``limit`` rows with the given ``fields`` as columns (all fields when
    empty). Set ``poll_ms`` to re-poll on a cadence so the board stays live. Not
    bound to the view's root record, so it is valid in a standalone view. An optional
    ``row_workflow_id`` renders a per-row button that runs that workflow against the
    row's record (e.g. re-announce this mission-state row) — the runtime targets the
    row id, so an ``update_record``/``update_record_field`` step writes that row."""

    type: Literal["record_list"] = "record_list"
    entity: str  # entity slug to read records from
    label: str | None = None
    fields: list[str] = Field(default_factory=list)  # field slugs as columns; empty = every field
    sort_by: str | None = None  # field slug or base column; defaults to created_at
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = 20
    poll_ms: int | None = None  # when set, re-poll for a live board; None = fetch once
    empty_text: str | None = None
    row_workflow_id: uuid.UUID | None = None  # optional per-row run_workflow (row record is the target)
    row_action_label: str | None = None
    width: FieldWidth | None = None


# ------------------------------------------------------------------ #
# Table (1:M editable grid) — columns can reach related entities
# ------------------------------------------------------------------ #
class AnchorColumn(BaseModel):
    """A column bound to a field on the table's anchor (child) entity."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["field"] = "field"
    slug: str
    label: str | None = None
    read_only: bool = False
    width: FieldWidth | None = None
    display: FieldDisplay | None = None


class RelatedColumn(BaseModel):
    """A column reached one hop from the anchor row via a to-one relationship on
    the child (``relationship_id``), showing/editing ``slug`` on the related
    entity. When ``editable`` the submit path upserts + links the related record
    (fully-editable-across-joins); otherwise it is a read-only lookup."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["related"] = "related"
    relationship_id: uuid.UUID  # a to-one FK relationship on the anchor entity
    slug: str  # field on the related (target) entity
    label: str | None = None
    editable: bool = False
    width: FieldWidth | None = None
    display: FieldDisplay | None = None


TableColumn = Annotated[AnchorColumn | RelatedColumn, Field(discriminator="kind")]


class TableElement(_Element):
    """A 1:M child collection edited as an add/remove-row grid. ``anchor_relationship_id``
    is a relationship *targeting* the form's root entity (the child owns the FK)."""

    type: Literal["table"] = "table"
    anchor_relationship_id: uuid.UUID
    label: str | None = None
    columns: list[TableColumn] = Field(default_factory=list)
    min_rows: int = 0
    max_rows: int | None = None  # capped by MAX_SECTION_ROWS regardless


# ------------------------------------------------------------------ #
# Section (1:1 related record, inline or modal)
# ------------------------------------------------------------------ #
class SectionElement(_Element):
    """A single related record (1:1) whose FK lives on the root; its fields are
    laid out inline or behind a modal button."""

    type: Literal["section"] = "section"
    relationship_id: uuid.UUID
    mode: SectionMode = "inline"
    label: str | None = None
    # Only leaf elements are meaningful inside a section (validated in the service).
    elements: list[SectionChild] = Field(default_factory=list)


SectionChild = Annotated[
    FieldElement | CalculatedElement | LabelElement,
    Field(discriminator="type"),
]


# ------------------------------------------------------------------ #
# Layout containers (nest any element, recursively)
# ------------------------------------------------------------------ #
class Tab(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "Tab"
    elements: list[FormElement] = Field(default_factory=list)


class TabGroupElement(_Element):
    type: Literal["tab_group"] = "tab_group"
    tabs: list[Tab] = Field(default_factory=list)


class PanelElement(_Element):
    """A titled region (serves both panel and fieldset), optionally collapsible."""

    type: Literal["panel"] = "panel"
    title: str | None = None
    collapsible: bool = False
    collapsed: bool = False  # initial state when collapsible
    elements: list[FormElement] = Field(default_factory=list)


class AccordionPane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "Section"
    elements: list[FormElement] = Field(default_factory=list)


class AccordionElement(_Element):
    type: Literal["accordion"] = "accordion"
    panes: list[AccordionPane] = Field(default_factory=list)


class ColumnDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    span: int = 1  # relative width weight within the row
    elements: list[FormElement] = Field(default_factory=list)


class ColumnsElement(_Element):
    """A multi-column layout row; each column holds its own sub-tree."""

    type: Literal["columns"] = "columns"
    columns: list[ColumnDef] = Field(default_factory=list)


class BlockElement(_Element):
    """A repeatable group of elements (a field-collection). The filler adds/removes
    instances; each instance maps to a row of the 1:M child entity referenced by
    ``anchor_relationship_id`` (like a table, but laid out as stacked sub-forms
    rather than a grid)."""

    type: Literal["block"] = "block"
    anchor_relationship_id: uuid.UUID
    label: str | None = None
    add_label: str | None = None  # e.g. "Add another"
    min_items: int = 0
    max_items: int | None = None
    elements: list[SectionChild] = Field(default_factory=list)


# ------------------------------------------------------------------ #
# Button (actions: submit / run a workflow / navigate)
# ------------------------------------------------------------------ #
class SubmitAction(BaseModel):
    """Submit the enclosing form (the default primary action)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["submit"] = "submit"


class RunWorkflowAction(BaseModel):
    """Kick off a published workflow. ``inputs`` maps workflow input names to
    sandboxed expressions over the current form/view values (so a button can pass
    the record's data into the run). Executed via ``POST /workflows/{id}/run``."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["run_workflow"] = "run_workflow"
    workflow_id: uuid.UUID
    inputs: dict[str, Expression] = Field(default_factory=dict)
    confirm: str | None = None  # optional confirmation prompt before running
    success_message: str | None = None


class LinkAction(BaseModel):
    """Navigate to another view or an external URL."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["link"] = "link"
    href: str
    new_tab: bool = False


class CallConnectionAction(BaseModel):
    """POST/GET to a saved workflow **Connection** straight from a button — the generic
    'external action' that avoids wrapping every call in a one-step workflow. Runs
    server-side (``POST /workflows/connections/call``) so the connection's stored secret
    and the workflow SSRF allow-list still apply; the browser never sees the base URL or
    secret. ``body`` maps keys to sandboxed expressions over the current form values, so
    a slider/toggle/field value flows straight into the request."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["call_connection"] = "call_connection"
    connection: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    path: str = ""
    body: dict[str, Expression] = Field(default_factory=dict)
    confirm: str | None = None
    success_message: str | None = None


ButtonAction = Annotated[
    SubmitAction | RunWorkflowAction | LinkAction | CallConnectionAction,
    Field(discriminator="kind"),
]


class ButtonElement(_Element):
    """A clickable action: submit the form, run a workflow, or navigate."""

    type: Literal["button"] = "button"
    label: str = "Button"
    action: ButtonAction = Field(default_factory=SubmitAction)
    style: Literal["primary", "secondary", "danger", "ghost"] = "primary"
    width: FieldWidth | None = None


class FormRefElement(_Element):
    """Embed another form by id (primarily used inside *views*). ``display`` shows
    it read-only; ``fill`` embeds an editable, independently-submitting form."""

    type: Literal["form_ref"] = "form_ref"
    form_id: uuid.UUID
    mode: Literal["fill", "display"] = "fill"
    label: str | None = None


class ChatAnswerControls(BaseModel):
    """Live, per-turn controls the chat card can render so a viewer trades answer
    quality for speed without editing the workflow. When ``show`` is set the chat
    forwards the chosen values as extra workflow ``inputs`` (``synthesize`` = NOT
    ``fast_mode``, ``use_knowledge_graph``, ``max_words``, ``answer_model``); the
    other fields seed each control's initial state. The answer workflow's
    ``knowledge_search``/``summarize`` nodes must reference those inputs for the
    toggles to take effect."""

    model_config = ConfigDict(extra="forbid")

    show: bool = False
    fast_mode: bool = True  # retrieval-only (synthesize:false): one LLM call, no graph hop
    knowledge_graph: bool = False  # only affects the non-fast synthesis path
    concise: bool = True  # cap spoken reply to concise_words vs verbose_words
    speak: bool = True  # have the robot say the answer aloud (forwarded as inputs.speak)
    models: list[str] = Field(default_factory=list)  # first entry = default answer model
    concise_words: int = 20
    verbose_words: int = 45


class ChatFiller(BaseModel):
    """Perceived-latency filler. While ``answer_workflow_id`` runs (RAG + one or more
    LLM hops can take many seconds), the chat can show — and, when ``speak_connection``
    is set, verbalize through a saved connection — short randomized "one moment…" lines
    so a slow answer still feels responsive. Fillers are ephemeral chatter: nothing is
    persisted and they clear the instant the real reply lands. The first fires after
    ``delay_ms`` and successive ones every ``interval_ms``; ``phrases`` overrides the
    default pool, where ``{q}`` is replaced with the person's question."""

    model_config = ConfigDict(extra="forbid")

    show: bool = False
    delay_ms: int = 1400
    interval_ms: int = 6000
    max_lines: int = 2  # stop after a couple lines; endless chatter annoys
    phrases: list[str] = Field(default_factory=list)
    speak_connection: str | None = None  # saved connection slug to speak the filler
    speak_path: str = "/say"  # connection path that makes the robot talk
    speak_field: str = "text"  # request-body field carrying the phrase


class ChatElement(_Element):
    """A conversation panel backed by two entities: a ``conversation_entity`` (a
    session) and a ``message_entity`` (its turns, linked back via
    ``conversation_relationship``). It lists the active conversation's messages as
    chat bubbles (polling ``poll_ms``), and its input SENDS a message: it creates a
    ``person`` message record, then runs ``answer_workflow_id`` with
    ``{text, conversation_id}`` so the robot answers, speaks, and records its turn —
    a full remote-control chat. Not entity-bound, so it is valid in a standalone view."""

    type: Literal["chat"] = "chat"
    title: str | None = "Chat"
    conversation_entity: str = "robot_conversation"
    message_entity: str = "robot_message"
    conversation_relationship: str = "conversation"  # message → conversation (to-one) slug
    role_field: str = "role"  # picklist person|robot
    text_field: str = "text"
    channel_field: str = "channel"  # picklist heard|typed|spoken
    answer_workflow_id: uuid.UUID | None = None  # run on send (e.g. "Robot: Chat Answer")
    answer_controls: ChatAnswerControls | None = None  # optional live answer-speed toggle row
    filler: ChatFiller | None = None  # optional "one moment…" chatter while the robot works
    poll_ms: int = 1500
    placeholder: str = "Message the robot…"
    width: FieldWidth | None = None


# ------------------------------------------------------------------ #
# The recursive element union
# ------------------------------------------------------------------ #
FormElement = Annotated[
    FieldElement
    | LabelElement
    | CalculatedElement
    | InputElement
    | LiveValueElement
    | ReportElement
    | RecordListElement
    | ChatElement
    | ButtonElement
    | FormRefElement
    | TableElement
    | SectionElement
    | BlockElement
    | TabGroupElement
    | PanelElement
    | AccordionElement
    | ColumnsElement,
    Field(discriminator="type"),
]

# Resolve forward references now that every element type is defined.
Tab.model_rebuild()
TabGroupElement.model_rebuild()
PanelElement.model_rebuild()
AccordionPane.model_rebuild()
AccordionElement.model_rebuild()
ColumnDef.model_rebuild()
ColumnsElement.model_rebuild()
SectionElement.model_rebuild()
BlockElement.model_rebuild()


# Max nesting depth for containers — a safety bound against pathological trees.
MAX_TREE_DEPTH = 8


def iter_elements(elements: list[Any]):
    """Depth-first walk yielding ``(element, depth)`` for every node in a tree.

    Descends into every container's children (tabs, panes, columns, panels,
    sections, blocks). Used by validation + rendering to visit all leaves.
    """

    def _walk(items: list[Any], depth: int):
        for el in items:
            yield el, depth
            etype = getattr(el, "type", None)
            if etype == "tab_group":
                for tab in el.tabs:
                    yield from _walk(tab.elements, depth + 1)
            elif etype == "accordion":
                for pane in el.panes:
                    yield from _walk(pane.elements, depth + 1)
            elif etype == "columns":
                for col in el.columns:
                    yield from _walk(col.elements, depth + 1)
            elif etype in ("panel",) or etype in ("section", "block"):
                yield from _walk(el.elements, depth + 1)

    yield from _walk(elements, 0)


def tree_depth(elements: list[Any]) -> int:
    """The maximum container-nesting depth of a tree (0 for a flat list)."""
    return max((depth for _, depth in iter_elements(elements)), default=0)
