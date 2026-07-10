"""Aggregation-query contract for the reporting engine.

An ``AggregateQuery`` describes a GROUP BY / metric query over one custom entity:
which fields to group by (optionally date-bucketed), which aggregates to compute,
which rows to include (``filters``, reusing the record-filter operators), which
aggregate rows to keep (``having``), and how to sort/limit the result. It is
storage-agnostic — the same shape is embedded in a saved ``Report`` and posted
ad-hoc to ``POST /entities/{slug}/aggregate``.

Every field reference is a catalog **slug** (or a base column); the repository
whitelists it to a physical column, so nothing here reaches SQL as an identifier.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# date_trunc granularities for bucketing a date / timestamptz group key.
DateBucket = Literal["hour", "day", "week", "month", "quarter", "year"]
# Aggregate functions. ``count`` needs no field; the rest require one.
AggOp = Literal["count", "count_distinct", "sum", "avg", "min", "max"]
FilterOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "isnull"]
CompareOp = Literal["eq", "ne", "gt", "gte", "lt", "lte"]

# A result-set alias becomes a SQL label and is referenced by order_by/having.
# Restricted to a safe identifier shape as defense-in-depth (SQLAlchemy already
# quotes labels).
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def _check_alias(value: str | None) -> str | None:
    if value is not None and not _ALIAS_RE.match(value):
        raise ValueError("alias must match ^[a-z][a-z0-9_]{0,40}$")
    return value


class GroupBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=63)
    bucket: DateBucket | None = None  # only valid for date / timestamptz fields
    alias: str | None = Field(default=None, max_length=41)

    _v_alias = field_validator("alias")(staticmethod(_check_alias))


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: AggOp
    field: str | None = Field(default=None, max_length=63)
    alias: str | None = Field(default=None, max_length=41)

    _v_alias = field_validator("alias")(staticmethod(_check_alias))


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=63)
    op: FilterOp = "eq"
    value: Any = None


class HavingSpec(BaseModel):
    """Filter on an aggregate, referencing a metric by its alias/name."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=41)
    op: CompareOp = "gt"
    value: float


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=41)  # a group or metric alias/name
    dir: Literal["asc", "desc"] = "desc"


class AggregateQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_by: list[GroupBy] = Field(default_factory=list, max_length=4)
    metrics: list[Metric] = Field(default_factory=list, max_length=12)
    filters: list[FilterSpec] = Field(default_factory=list, max_length=30)
    having: list[HavingSpec] = Field(default_factory=list, max_length=10)
    order_by: list[OrderSpec] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def _default_count(self) -> AggregateQuery:
        # A query with no metrics is a plain row-count per group. Runs after the
        # model is built so it also applies when ``metrics`` is omitted entirely.
        if not self.metrics:
            self.metrics = [Metric(op="count", alias="count")]
        return self


class AggregateResult(BaseModel):
    """Resolved aggregation: rows keyed by group + metric names, plus the
    ordered column names so a chart can map axes without re-parsing the query."""

    model_config = ConfigDict(extra="forbid")

    group_by: list[str]
    metrics: list[str]
    rows: list[dict[str, Any]]
    row_count: int
