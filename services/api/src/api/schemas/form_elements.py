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

import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A URL scheme prefix (``scheme:``) at the very start of a string. Used to reject
# non-http(s) schemes (``javascript:``, ``data:``, …) in author-supplied link URLs.
_URL_SCHEME_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.\-]*):")


def _assert_safe_href(v: str) -> str:
    """Only relative URLs or ``http(s)`` absolute URLs are allowed. Any other scheme
    (``javascript:``, ``data:``, ``vbscript:``, …) is rejected so a stored link can
    never become an XSS vector when it's rendered/navigated. ``{token}`` placeholders
    are URL-encoded at render time, so only the static scheme prefix is constrained."""
    m = _URL_SCHEME_RE.match(v)
    if m and m.group(1).lower() not in ("http", "https"):
        raise ValueError(f"link scheme {m.group(1)!r} is not allowed; use a relative URL or an http(s) URL")
    return v


# ------------------------------------------------------------------ #
# Shared presentational vocabulary
# ------------------------------------------------------------------ #
# Column width in the responsive grid: full spans the row, half shares it.
FieldWidth = Literal["full", "half", "third", "quarter"]

# Picklist render style (presentational only; value is still one of the options).
# How a bound field is presented. The first two pick a picklist's input widget; the rest
# drop the input entirely and typeset the VALUE — for a screen that is read from across a
# room, where a read-only textarea is still a form control with a border and a resize grip
# around a sentence nobody may edit.
FieldDisplay = Literal["dropdown", "radio", "headline", "prose", "quote", "caption", "log"]

# The display-only subset: these render as text, not as an input of any kind.
TEXT_DISPLAYS = frozenset({"headline", "prose", "quote", "caption", "log"})

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
    # Optional conditional visibility: a sandboxed JsonLogic expression evaluated
    # over the enclosing scope's values (same evaluator as ``calculated``). The
    # element renders only when this is truthy; ``None`` (the default) is always
    # visible. Lets a view gate an element on record state — e.g. show the quiz
    # only when ``{">=": [{"var": "progress_pct"}, 100]}``, or an "Enroll" button
    # only when the learner isn't enrolled yet. Display-only: hiding an element
    # never suppresses server-side validation of data the author marked required,
    # so gate inputs, not required persisted fields.
    visible_when: Expression = None


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
    # Views render fields as read-only value readouts by default (a view shows
    # data). ``editable=True`` opts a field back into an input there — e.g. a
    # console where the edited value feeds a workflow button's inputs. Forms
    # ignore this; their fields are editable unless ``read_only``.
    editable: bool | None = None
    help_text: str | None = None
    placeholder: str | None = None
    width: FieldWidth | None = None
    display: FieldDisplay | None = None  # picklist render style


class LabelElement(_Element):
    """Static presentational content — not bound to any entity field."""

    type: Literal["label"] = "label"
    text: str = ""
    variant: Literal["heading", "subheading", "paragraph", "divider"] = "paragraph"
    # Wall-display typesetting (matches the field element's text displays): a
    # standalone dashboard can headline a screen without binding an entity field.
    # Overrides ``variant`` when set.
    display: Literal["headline", "prose", "quote", "caption"] | None = None
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
    # Optional display translation, keyed by the stringified value:
    # ``{"true": "Thinking…", "false": "idle"}``. A status flag polled off a device reads
    # as a status this way instead of as ``true`` — the readout says what the state means
    # rather than how it is encoded. Unlisted values pass through unchanged, so a partial
    # map only relabels the cases it names.
    value_map: dict[str, str] | None = None
    width: FieldWidth | None = None


class ImageElement(_Element):
    """A display-only picture — the visual anchor a status page needs (a ship, a
    floor plan, a product shot) that no data element can supply.

    ``url`` may carry ``{token}`` placeholders filled from the enclosing scope's
    values at render time (``{id}`` = the bound record id, ``{<field_slug>}`` = a
    field value, each URL-encoded), so the image can FOLLOW record state — e.g.
    ``/sim/ship-{ship_condition}.svg`` swaps the artwork as a ship takes damage.
    Only relative or ``http(s)`` URLs are allowed (same rule as link hrefs).

    Not entity-bound, so it is valid in a standalone view. ``max_height`` caps the
    rendered height in px (the image always scales down to the column width)."""

    type: Literal["image"] = "image"
    url: str
    alt: str | None = None
    caption: str | None = None
    max_height: int | None = None  # px cap; None = natural height within the column
    width: FieldWidth | None = None

    @field_validator("url")
    @classmethod
    def _reject_dangerous_scheme(cls, v: str) -> str:
        return _assert_safe_href(v)


class QrCodeElement(_Element):
    """A QR code for a URL — how a screen hands a link to a phone or tablet.

    Typing a LAN address into a tablet is the most error-prone step in setting up
    a shared device, so this exists to remove it: the operator opens the console
    on their laptop, taps the button, and points the tablet's camera at it.

    ``url`` may be relative (``/views/<id>/kiosk?record_id=…``), in which case it
    is resolved at render time against ``host`` if set, else against the address
    the page itself was opened at. ``{token}`` placeholders are filled from the
    record like a link href. Only relative or ``http(s)`` URLs are allowed.

    **A relative URL is only as good as the address in the browser bar.** A page
    opened at ``localhost`` produces a QR that says ``localhost``, which means
    "this tablet" to the tablet and therefore fails. Set ``host`` to the machine's
    LAN address to make it independent of how the console was opened; the rendered
    card always shows the encoded URL and warns when it is a loopback address.
    """

    type: Literal["qr_code"] = "qr_code"
    url: str
    label: str | None = None  # button text / card heading
    caption: str | None = None  # short instruction under the code
    # ``button`` keeps the code behind a tap (a console that is on screen all day
    # shouldn't carry a permanent QR); ``inline`` draws it in place.
    display: Literal["button", "inline"] = "button"
    host: str | None = None  # origin to resolve a relative url against
    size: int = Field(default=320, ge=96, le=1024)  # rendered px
    width: FieldWidth | None = None

    @field_validator("url", "host")
    @classmethod
    def _reject_dangerous_scheme(cls, v: str | None) -> str | None:
        return None if v is None else _assert_safe_href(v)


class ProgressElement(_Element):
    """A display-only progress bar. ``value`` is a sandboxed expression over the
    form's values (or a literal) yielding a number; the bar fills ``value / max``,
    clamped to ``[0, max]``. When ``show_percent`` the computed percentage is drawn
    on the bar. Reads values but writes nothing — safe wherever ``calculated`` is."""

    type: Literal["progress"] = "progress"
    label: str | None = None
    value: Expression = None
    max: float = 100
    show_percent: bool = True
    width: FieldWidth | None = None


class CountdownElement(_Element):
    """A live **time left** clock, counting down to a deadline carried on the record.

    Display-only, and deliberately so: nothing here decides when time is actually
    up. A workflow opened the question and a workflow closes it; this only draws
    how long is left, which is why it is safe to run off the viewer's own clock.
    A device whose clock is badly wrong shows a full bar or a finished one rather
    than a nonsense figure — the value is clamped to the span either way.

    Two ways to say when time runs out, because a record may naturally carry
    either. ``until_field`` is an absolute deadline. ``from_field`` plus a duration
    (``seconds``, or ``seconds_field`` to take it from the record) is a start time
    the countdown adds to — the friendlier form for a workflow, which can stamp
    ``{{ now }}`` into a field but cannot do date arithmetic. The absolute form
    wins if both resolve.

    With no deadline on the record the element draws NOTHING, so a page can leave
    it in place between questions instead of gating it behind a ``visible_when``.
    """

    type: Literal["countdown"] = "countdown"
    label: str | None = None
    until_field: str | None = None  # record field: the deadline itself
    from_field: str | None = None  # record field: when the clock started
    seconds: int | None = None  # duration added to from_field; also the bar's scale
    seconds_field: str | None = None  # record field holding that duration
    done_text: str | None = None  # shown once the deadline passes (default "Time's up")
    show_bar: bool = True
    width: FieldWidth | None = None


class Slide(BaseModel):
    """One slide in a deck: an optional title, a Markdown ``body``, an optional
    image, and an optional video. Rendered as a single presentation page by the
    ``slides`` element.

    ``video_url`` is a direct video file (mp4/webm) — not a YouTube/Vimeo page.
    When ``require_video`` is set (and a ``video_url`` is present) the deck
    discourages skipping — it disables the forward controls and snaps forward seeks
    back until the video finishes. This is a client-side nudge, not enforced viewing:
    nothing is recorded server-side, so it deters casual skipping rather than
    guaranteeing a training video was watched."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    body: str = ""  # Markdown
    image_url: str | None = None
    video_url: str | None = None  # direct video file (mp4/webm)
    require_video: bool = True  # when a video is present, gate "next" until it's watched (opt out per slide)
    notes: str | None = None  # optional speaker/aside notes


class SlidesElement(_Element):
    """An in-app **slide deck** — module content shown as a navigable presentation
    (prev/next + progress) instead of a wall of text. Display-only, so it is valid
    in a standalone view. Two content sources (mutually exclusive, ``slug`` wins):

    * ``slug`` — bind to a JSON entity field holding the slide array (the common
      case: a Module's ``slides`` field), so the deck is data-driven per record.
    * ``slides`` — inline slides authored directly on the element.

    Each slide is ``{title?, body(markdown), image_url?, notes?}``."""

    type: Literal["slides"] = "slides"
    label: str | None = None
    slug: str | None = None  # JSON field holding a list of slides (entity-bound case)
    slides: list[Slide] = Field(default_factory=list)  # inline slides (standalone case)
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


class RecordListFilter(BaseModel):
    """One server-side filter narrowing a ``record_list``'s rows.

    Mirrors the record endpoint's ``field:op[:value]`` filter (see
    ``entity_records_helpers.parse_filters``). ``value`` may be the sentinel ``@me``
    on a to-one relation field, which the endpoint resolves to the caller's OWN
    record id (matched by email, like ``record_id=me``) — so a board can show just
    the current user's rows without the author hard-coding an id."""

    model_config = ConfigDict(extra="forbid")

    field: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "isnull"] = "eq"
    value: Any = None


class RecordListElement(_Element):
    """A read-only display of existing records of an entity — a live "status board".

    Reads ``entity`` (by slug) newest-first (or by ``sort_by``/``sort_dir``), showing
    at most ``limit`` rows with the given ``fields`` as columns (all fields when
    empty). Set ``poll_ms`` to re-poll on a cadence so the board stays live. Not
    bound to the view's root record, so it is valid in a standalone view. An optional
    ``row_workflow_id`` renders a per-row button that runs that workflow against the
    row's record (e.g. re-announce this mission-state row) — the runtime targets the
    row id, so an ``update_record``/``update_record_field`` step writes that row.

    ``filters`` narrows the rows server-side (ANDed); a filter ``value`` of ``@me``
    on a relation field scopes the board to the caller's own records (e.g. a
    learner's own attempts/certificates)."""

    type: Literal["record_list"] = "record_list"
    entity: str  # entity slug to read records from
    label: str | None = None
    fields: list[str] = Field(default_factory=list)  # field slugs as columns; empty = every field
    filters: list[RecordListFilter] = Field(default_factory=list)  # server-side row filters (ANDed)
    sort_by: str | None = None  # field slug or base column; defaults to created_at
    sort_dir: Literal["asc", "desc"] = "desc"
    limit: int = 20
    poll_ms: int | None = None  # when set, re-poll for a live board; None = fetch once
    empty_text: str | None = None
    row_workflow_id: uuid.UUID | None = None  # optional per-row run_workflow (row record is the target)
    row_action_label: str | None = None
    # Inputs passed to ``row_workflow_id``, evaluated per row over the ROW's field
    # values PLUS the enclosing view's values — so ``{"var": "id"}`` is the row id,
    # ``{"var": "<row field>"}`` a row value, and ``{"var": "<view field>"}`` a value
    # from the parent scope (e.g. a learner-bound catalog's ``email``). Lets a per-row
    # action carry context, e.g. a course board's Enroll passing ``course_id`` + the
    # caller's ``learner_email``.
    row_workflow_inputs: dict[str, Expression] = Field(default_factory=dict)
    # Optional per-row hyperlink. A URL with ``{token}`` placeholders filled from the
    # row (``{id}`` = the row record id, ``{<field_slug>}`` = a field value, each
    # URL-encoded) — the record-list equivalent of a table link column. Lets a course
    # board route each row to its own player, e.g. ``/views/{player_view_slug}/view``.
    row_link_template: str | None = None
    row_link_label: str = "Open"
    width: FieldWidth | None = None

    @field_validator("row_link_template")
    @classmethod
    def _reject_dangerous_row_link(cls, v: str | None) -> str | None:
        return v if v is None else _assert_safe_href(v)


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


class LinkColumn(BaseModel):
    """A non-data column that renders a per-row hyperlink instead of a value. Binds
    no entity data. ``href_template`` is a URL with ``{token}`` placeholders filled
    from the row: ``{id}`` = the row record's id, and ``{<field_slug>}`` = an anchor
    field value on the row (each token is URL-encoded). ``link_label`` is the static
    link text. Use it to open a row's detail view, a linked document, or an external
    page — e.g. ``/documents/{document_key}`` or ``/views/<id>/view?record_id={id}``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["link"] = "link"
    label: str | None = None
    href_template: str
    link_label: str = "Open"
    new_tab: bool = False
    width: FieldWidth | None = None

    @field_validator("href_template")
    @classmethod
    def _reject_dangerous_scheme(cls, v: str) -> str:
        return _assert_safe_href(v)


TableColumn = Annotated[AnchorColumn | RelatedColumn | LinkColumn, Field(discriminator="kind")]


class TableElement(_Element):
    """A 1:M child collection edited as an add/remove-row grid. ``anchor_relationship_id``
    is a relationship *targeting* the form's root entity (the child owns the FK)."""

    type: Literal["table"] = "table"
    anchor_relationship_id: uuid.UUID
    label: str | None = None
    columns: list[TableColumn] = Field(default_factory=list)
    min_rows: int = 0
    max_rows: int | None = None  # capped by MAX_SECTION_ROWS regardless
    read_only: bool = False  # whole grid non-editable in fill mode: no add/remove-row, all cells locked
    sort_by: str | None = None  # anchor field slug to order rows by; None = default (insertion) order
    sort_dir: Literal["asc", "desc"] = "asc"


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


# ``SectionChild`` (what may appear inside a section/block) is defined further down,
# after ``PuzzlePadElement`` — it is one of the members. The annotations that use it
# are lazy strings (``from __future__ import annotations``) and resolved by the
# ``model_rebuild()`` calls at the end of this module.


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
    """Navigate to another view or an external URL. ``href`` may carry ``{token}``
    placeholders filled from the current record's values at click time (``{id}`` = the
    bound record id, ``{<field_slug>}`` = a field value), so a button can route to a
    per-record view — e.g. ``/views/{quiz_view_slug}/view?record_id=me``."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["link"] = "link"
    href: str
    new_tab: bool = False

    @field_validator("href")
    @classmethod
    def _reject_dangerous_scheme(cls, v: str) -> str:
        return _assert_safe_href(v)


class CopyLinkAction(BaseModel):
    """Copy a link to the viewer's clipboard instead of following it — how a screen
    hands a URL to a *person* (paste it into a chat, a message, another machine's
    browser) when a QR code is the wrong shape for the moment.

    ``href`` takes the same ``{token}`` fill and scheme check as ``LinkAction``. The
    difference is that a relative href is resolved to an ABSOLUTE address before it
    is copied — against ``host`` when set, else the address the page was opened at —
    because a pasted ``/views/…`` means nothing outside this browser. That is the
    same resolution ``qr_code`` does, and for the same reason: a console opened at
    ``localhost`` can only produce a ``localhost`` link, which means "this machine"
    to whoever receives it. Set ``host`` to the machine's LAN address to make the
    copied link independent of how the console was opened."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["copy_link"] = "copy_link"
    href: str
    host: str | None = None  # origin to resolve a relative href against
    success_message: str | None = None

    @field_validator("href", "host")
    @classmethod
    def _reject_dangerous_scheme(cls, v: str | None) -> str | None:
        return None if v is None else _assert_safe_href(v)


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
    SubmitAction | RunWorkflowAction | LinkAction | CopyLinkAction | CallConnectionAction,
    Field(discriminator="kind"),
]


class ButtonElement(_Element):
    """A clickable action: submit the form, run a workflow, or navigate."""

    type: Literal["button"] = "button"
    label: str = "Button"
    action: ButtonAction = Field(default_factory=SubmitAction)
    style: Literal["primary", "secondary", "danger", "ghost"] = "primary"
    # Touch target. The default is sized for a mouse; ``large``/``xl`` are for a view
    # presented on a tablet or a wall display, where a finger — often a child's —
    # is the pointer and a 32px control is a miss waiting to happen.
    size: Literal["default", "large", "xl"] = "default"
    width: FieldWidth | None = None


# ------------------------------------------------------------------ #
# Puzzle pad — a hands-on interactive surface
# ------------------------------------------------------------------ #
# What the pad puts on screen. Each kind is a different *physical* interaction,
# not a different question format, because the point is the doing:
#   choices  — big tap targets (the multiple-choice case, sized for a finger)
#   keypad   — a number pad; the person keys in a value and transmits it
#   sequence — tap items into the right order (a launch checklist, a procedure)
#   wires    — DRAG a lead from a port to its matching port; a repair console
#   sort     — DRAG items into labelled bins (classify, triage, stow)
#   color    — pick a colour, tap a region; paint a panel to match a target
PuzzleKind = Literal["choices", "keypad", "sequence", "wires", "sort", "color"]


class PuzzlePadElement(_Element):
    """A hands-on puzzle surface: the interaction happens IN the browser, and only
    the outcome crosses back into workflow-land.

    Everything else in this schema either shows data or collects a value. This
    element exists because dragging a wire onto a port, or painting six hull
    panels, is not expressible as fields and buttons — and because a workflow
    engine that steps through nodes cannot model a per-touch interaction. So the
    division is: the pad owns the *doing*, the workflow owns the *consequences*.

    **Where the puzzle comes from.** Each of ``kind``/``spec``/``prompt``/``hint``
    can be given inline (a fixed puzzle) or read from a record field via the
    matching ``*_field`` (a puzzle that changes as the record does). A field wins
    over its inline counterpart when it holds a value, so an inline value doubles
    as the fallback. ``spec`` is a free-form JSON object whose shape depends on
    ``kind`` — the client validates it and refuses to render a malformed one
    rather than half-drawing a puzzle nobody can solve.

    **Who decides "correct".** Two honest cases, and the difference is not a
    policy choice but a fact about what the browser must be told:

    * ``choices`` and ``keypad`` — the pad NEVER receives the answer. It reports
      what the person picked or keyed in, and a workflow compares that against the
      answer field on the record. Keep the answer out of the view's fields and it
      never reaches the device.
    * ``sequence``, ``wires``, ``sort``, ``color`` — the target arrangement IS the
      puzzle; it cannot be drawn without being sent. The pad grades locally and
      reports whether it was solved. Treat that verdict as a player's word, which
      is the right trust level for a game and the wrong one for an exam.

    **What it reports.** On completion the pad evaluates ``on_complete.inputs``
    against the enclosing scope's values PLUS the outcome: ``solved`` (bool),
    ``answer`` (a short string — the chosen value, keyed digits, or a summary of
    the arrangement), ``attempts`` and ``elapsed_ms``. So a workflow input reads
    ``{"var": "answer"}`` exactly as a button's would.
    """

    type: Literal["puzzle_pad"] = "puzzle_pad"

    kind: PuzzleKind = "choices"
    kind_field: str | None = None  # record field holding one of PuzzleKind
    spec: dict[str, Any] | None = None
    spec_field: str | None = None  # record field holding the spec (JSON object or JSON text)
    prompt: str | None = None
    prompt_field: str | None = None
    hint: str | None = None
    hint_field: str | None = None

    # The correct value, read from the record for the sole purpose of SHOWING it
    # once it no longer matters. This does not weaken the rule above: the field is
    # expected to be empty for as long as answering is open, and whatever closes
    # the question fills it in. Naming a field that always holds the answer would
    # hand it to every device the moment the puzzle is drawn — the honest use is a
    # denormalised "revealed answer" column a workflow writes at reveal time. Blank
    # means "not yet", and the pad stays neutral.
    answer_field: str | None = None
    # One answer only: after a submission the pad stays locked until the puzzle
    # itself changes. Off by default — a practice pad should be re-playable — and
    # on for anything where a second tap would be a second entry in someone's log.
    lock_after_submit: bool = False

    # Run when the person finishes. Optional so a pad can be dropped into a page
    # purely to be played with (a practice pad that records nothing).
    on_complete: RunWorkflowAction | None = None
    submit_label: str = "Transmit"
    # Offer a "Need a hint?" reveal when a hint is available. The hint stays hidden
    # until asked for, so it costs nothing to attach one to every puzzle.
    show_hint: bool = True
    # Minimum height in px for the interactive area. A drag puzzle needs room; the
    # default suits a tablet held in landscape.
    min_height: int | None = None
    width: FieldWidth | None = None

    @field_validator("spec")
    @classmethod
    def _reject_huge_spec(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """Bound the stored spec. A puzzle is a handful of ports or panels; anything
        near this cap is a mistake or an attempt to make a view expensive to render."""
        if v is not None and len(v) > 32:
            raise ValueError("puzzle spec has too many top-level keys (max 32)")
        return v


SectionChild = Annotated[
    FieldElement | CalculatedElement | LabelElement | PuzzlePadElement,
    Field(discriminator="type"),
]


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


class ChatVoice(BaseModel):
    """Voice input for the chat: the browser microphone drives speech-to-text (Web
    Speech API) so a person can TALK to the robot instead of typing. Recognized
    speech is sent through the same path as a typed turn, so the robot answers +
    speaks identically. ``mode`` is only the initial default — the viewer can flip
    between hold-to-talk and always-on at runtime. In always-on, ``pause_while_thinking``
    pauses the mic while the robot answers (turn-taking) so it doesn't hear itself."""

    model_config = ConfigDict(extra="forbid")

    show: bool = False
    mode: Literal["push_to_talk", "always_on"] = "push_to_talk"
    lang: str = "en-US"  # BCP-47 recognition language
    pause_while_thinking: bool = True


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
    voice: ChatVoice | None = None  # optional mic input (talk to the robot)
    poll_ms: int = 1500
    placeholder: str = "Message the robot…"
    # Panel height: a wall display wants a tall transcript, a control strip a short
    # one. ``fill`` sizes to the viewport (for a chat that IS the screen).
    height: Literal["sm", "md", "lg", "fill"] = "md"
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
    | ImageElement
    | QrCodeElement
    | ProgressElement
    | CountdownElement
    | SlidesElement
    | ReportElement
    | RecordListElement
    | ChatElement
    | ButtonElement
    | PuzzlePadElement
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
            elif etype in ("panel", "section", "block"):
                yield from _walk(el.elements, depth + 1)

    yield from _walk(elements, 0)


def tree_depth(elements: list[Any]) -> int:
    """The maximum container-nesting depth of a tree (0 for a flat list)."""
    return max((depth for _, depth in iter_elements(elements)), default=0)
