"""Unit tests for the reporting engine's aggregate SQL builder + filter clauses.

These exercise ``DynamicEntityRepository.build_aggregate`` / ``_filter_condition``
in isolation (no database): the repository builds its SQLAlchemy ``Table`` from the
catalog in ``__init__`` and the builders only construct statements, so a dummy
session is sufficient. We compile the statement to Postgres SQL and assert on its
shape, and check that invalid queries raise ``EntityRecordError`` (HTTP 400).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from api.repositories.dynamic_entity import DynamicEntityRepository, EntityRecordError
from api.schemas.aggregate import AggregateQuery, FilterSpec, GroupBy, HavingSpec, Metric, OrderSpec
from api.services import identifiers
from sqlalchemy.dialects import postgresql


def _field(slug: str, field_type: str, *, picklist: list[str] | None = None):  # type: ignore[no-untyped-def]
    f = MagicMock()
    f.slug = slug
    f.field_type = field_type
    f.is_required = False
    f.is_unique = False
    f.picklist_options = picklist or []
    f.physical_column = identifiers.column_name(uuid.uuid4())
    return f


def _rel(slug: str):  # type: ignore[no-untyped-def]
    r = MagicMock()
    r.slug = slug
    r.is_required = False
    r.cardinality = "many_to_one"
    r.physical_name = identifiers.relation_column_name(uuid.uuid4())
    return r


def _repo(fields, rels=None):  # type: ignore[no-untyped-def]
    definition = MagicMock()
    definition.physical_table = identifiers.table_name(uuid.uuid4())
    definition.id = uuid.uuid4()
    return DynamicEntityRepository(MagicMock(), uuid.uuid4(), definition, fields, rels or [])


def _sql(repo, query: AggregateQuery) -> str:  # type: ignore[no-untyped-def]
    stmt, _g, _m = repo.build_aggregate(query)
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _crm_repo():  # type: ignore[no-untyped-def]
    return _repo(
        [_field("stage", "picklist", picklist=["won", "lost", "open"]), _field("amount", "numeric")],
        [_rel("company")],
    )


class TestAggregateBuilder:
    def test_group_metrics_and_labels(self) -> None:
        repo = _crm_repo()
        stmt, groups, metrics = repo.build_aggregate(
            AggregateQuery(
                group_by=[GroupBy(field="stage")],
                metrics=[Metric(op="count"), Metric(op="sum", field="amount", alias="total")],
            )
        )
        assert groups == ["stage"]
        assert metrics == ["count", "total"]
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "GROUP BY" in sql
        assert "count(*)" in sql
        assert "sum(" in sql

    def test_date_bucket_uses_date_trunc(self) -> None:
        repo = _repo([_field("amount", "numeric")])
        sql = _sql(repo, AggregateQuery(group_by=[GroupBy(field="created_at", bucket="month")]))
        assert "date_trunc('month'" in sql

    def test_count_distinct_over_relationship(self) -> None:
        repo = _crm_repo()
        sql = _sql(repo, AggregateQuery(metrics=[Metric(op="count_distinct", field="company", alias="companies")]))
        assert "count(DISTINCT" in sql

    def test_default_metric_is_count(self) -> None:
        repo = _crm_repo()
        _stmt, _g, metrics = repo.build_aggregate(AggregateQuery(group_by=[GroupBy(field="stage")]))
        assert metrics == ["count"]

    def test_having_and_order_reference_metric(self) -> None:
        repo = _crm_repo()
        sql = _sql(
            repo,
            AggregateQuery(
                group_by=[GroupBy(field="stage")],
                metrics=[Metric(op="sum", field="amount", alias="total")],
                having=[HavingSpec(metric="total", op="gt", value=0)],
                order_by=[OrderSpec(key="total", dir="desc")],
            ),
        )
        assert "HAVING" in sql
        assert "ORDER BY" in sql

    def test_filters_are_applied_and_org_scoped(self) -> None:
        repo = _crm_repo()
        sql = _sql(
            repo,
            AggregateQuery(
                metrics=[Metric(op="count")],
                filters=[
                    FilterSpec(field="stage", op="ne", value="lost"),
                    FilterSpec(field="amount", op="gte", value=1000),
                ],
            ),
        )
        assert "org_id" in sql
        assert "!=" in sql or "<>" in sql
        assert ">=" in sql

    def test_limit_present(self) -> None:
        repo = _crm_repo()
        sql = _sql(repo, AggregateQuery(limit=25))
        assert "LIMIT 25" in sql


class TestAggregateValidation:
    def test_sum_on_non_numeric_raises(self) -> None:
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(AggregateQuery(metrics=[Metric(op="sum", field="stage")]))

    def test_bucket_on_non_date_raises(self) -> None:
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(AggregateQuery(group_by=[GroupBy(field="stage", bucket="month")]))

    def test_unknown_field_raises(self) -> None:
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(AggregateQuery(group_by=[GroupBy(field="ghost")]))

    def test_unknown_order_key_raises(self) -> None:
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(
                AggregateQuery(metrics=[Metric(op="count", alias="c")], order_by=[OrderSpec(key="nope")])
            )

    def test_having_unknown_metric_raises(self) -> None:
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(
                AggregateQuery(metrics=[Metric(op="count", alias="c")], having=[HavingSpec(metric="nope", value=1)])
            )

    def test_duplicate_result_name_raises(self) -> None:
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(
                AggregateQuery(
                    group_by=[GroupBy(field="stage", alias="x")],
                    metrics=[Metric(op="count", alias="x")],
                )
            )

    def test_duplicate_unaliased_metric_raises(self) -> None:
        # two count metrics both default to the name "count"
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(AggregateQuery(metrics=[Metric(op="count"), Metric(op="count")]))

    def test_group_by_json_field_rejected(self) -> None:
        repo = _repo([_field("blob", "json")])
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(AggregateQuery(group_by=[GroupBy(field="blob")]))

    def test_having_on_date_metric_rejected(self) -> None:
        # min/max over a date field can't be compared to a numeric HAVING threshold
        repo = _repo([_field("closed_at", "timestamptz")])
        with pytest.raises(EntityRecordError):
            repo.build_aggregate(
                AggregateQuery(
                    metrics=[Metric(op="max", field="closed_at", alias="latest")],
                    having=[HavingSpec(metric="latest", op="gt", value=1)],
                )
            )

    def test_no_order_by_defaults_to_group_order(self) -> None:
        # a LIMIT with no explicit order must still ORDER BY the group key so
        # truncation is deterministic, not an arbitrary set of groups.
        repo = _crm_repo()
        sql = _sql(repo, AggregateQuery(group_by=[GroupBy(field="stage")], limit=5))
        assert "ORDER BY" in sql


class TestAggregateSchema:
    def test_alias_must_be_safe_identifier(self) -> None:
        with pytest.raises(ValueError):
            Metric(op="sum", field="amount", alias="Bad-Alias")

    def test_empty_metrics_defaults_to_count(self) -> None:
        assert AggregateQuery().metrics[0].op == "count"
        assert AggregateQuery(metrics=[]).metrics[0].op == "count"


class TestFilterConditions:
    def test_in_operator_builds_in_clause(self) -> None:
        repo = _crm_repo()
        cond = repo._filter_condition("stage", "in", ["won", "open"])
        sql = str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "IN (" in sql

    def test_empty_in_matches_nothing(self) -> None:
        repo = _crm_repo()
        cond = repo._filter_condition("stage", "in", [])
        sql = str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "false" in sql.lower()

    def test_isnull_true_and_false(self) -> None:
        repo = _crm_repo()
        yes = str(repo._filter_condition("amount", "isnull", True).compile(dialect=postgresql.dialect()))
        no = str(repo._filter_condition("amount", "isnull", False).compile(dialect=postgresql.dialect()))
        assert "IS NULL" in yes
        assert "IS NOT NULL" in no

    def test_contains_only_on_text(self) -> None:
        repo = _crm_repo()
        # numeric field cannot use contains
        with pytest.raises(EntityRecordError):
            repo._filter_condition("amount", "contains", "5")
        # picklist (text-backed) can
        cond = repo._filter_condition("stage", "contains", "wo")
        sql = str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "ILIKE" in sql.upper()

    def test_relationship_filter_coerces_to_uuid(self) -> None:
        repo = _crm_repo()
        rid = uuid.uuid4()
        cond = repo._filter_condition("company", "eq", str(rid))
        sql = str(cond.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert str(rid) in sql

    def test_bad_typed_filter_raises(self) -> None:
        repo = _crm_repo()
        with pytest.raises(EntityRecordError):
            repo._filter_condition("amount", "gte", "not-a-number")
