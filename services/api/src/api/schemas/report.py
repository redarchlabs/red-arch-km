"""Report schemas: CRUD contract + the visualization spec.

A report couples an :class:`~api.schemas.aggregate.AggregateQuery` (what to
compute) with a :class:`Visualization` (how to draw it). The visualization is
intentionally expressive — bar/line/area/pie/donut/scatter/table plus a single
KPI ``metric`` mode, stacking, a secondary series axis, and a freeform
``options`` bag for colors/labels/number-formatting the frontend understands.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.aggregate import AggregateQuery, FilterSpec

ChartType = Literal[
    "bar",
    "stacked_bar",
    "grouped_bar",
    "line",
    "area",
    "stacked_area",
    "pie",
    "donut",
    "scatter",
    "table",
    "metric",
]

# Number formatting hints the frontend applies to metric/axis values.
NumberFormat = Literal["plain", "comma", "currency", "percent", "compact", "bytes"]


class Visualization(BaseModel):
    """How to render an aggregate result.

    ``x`` is the group-column name used as the category axis (or pie label);
    ``series`` are the metric-column names to plot. ``color_by`` splits a single
    series into one series per value of a second group column (for a stacked/
    grouped chart driven by two group-bys). For ``metric`` mode a single
    ``series`` value is shown as a KPI tile, optionally with ``compare_to`` (a
    prior-period metric column) rendered as a delta.
    """

    model_config = ConfigDict(extra="forbid")

    type: ChartType = "bar"
    x: str | None = None
    series: list[str] = Field(default_factory=list, max_length=12)
    color_by: str | None = None
    stacked: bool = False
    # metric-tile extras
    compare_to: str | None = None
    unit: str | None = Field(default=None, max_length=16)
    number_format: NumberFormat = "plain"
    # Fraction-digit cap for the formatted value. ``None`` lets each format apply
    # its own sensible default (so an average doesn't render as 57.27272727…).
    precision: int | None = Field(default=None, ge=0, le=6)
    # Reserved passthrough for future presentation hints. Persisted and returned
    # as-is; the current renderer does not read it yet (kept so saved reports can
    # carry options forward without a schema change).
    options: dict[str, Any] = Field(default_factory=dict)


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=63)
    description: str | None = None
    entity_definition_id: uuid.UUID
    query: AggregateQuery = Field(default_factory=AggregateQuery)
    viz: Visualization = Field(default_factory=Visualization)
    is_active: bool = True


class ReportUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    query: AggregateQuery | None = None
    viz: Visualization | None = None
    is_active: bool | None = None


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    entity_definition_id: uuid.UUID
    query: dict[str, Any]
    viz: dict[str, Any]
    is_active: bool


class ReportRunRequest(BaseModel):
    """Optional overrides when running a saved report — e.g. a dashboard date
    picker appends ``extra_filters`` and a drill-down bumps ``limit``. Extra
    filters are ANDed onto the report's own filters."""

    model_config = ConfigDict(extra="forbid")

    extra_filters: list[FilterSpec] = Field(default_factory=list, max_length=20)
    limit: int | None = Field(default=None, ge=1, le=1000)


class AdHocRunRequest(BaseModel):
    """Run an aggregation without saving a report (report builder preview)."""

    model_config = ConfigDict(extra="forbid")

    entity_definition_id: uuid.UUID
    query: AggregateQuery
