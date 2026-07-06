"""Unit tests for deterministic structure-aware extraction."""

from __future__ import annotations

from brain_sdk.facts.doc_profiles import PROFILE_REGISTRY
from brain_sdk.facts.models import ObjectType
from brain_sdk.facts.structure import extract_structured

_DIRECTORY = """
# Employees

| Name  | Title | Reports To |
|-------|-------|------------|
| Shawn | CMO   | Jane       |
| Dana  | CFO   | Jane       |
"""


class TestMarkdownTables:
    def test_directory_table_yields_role_and_reporting_claims(self) -> None:
        claims = extract_structured(_DIRECTORY, PROFILE_REGISTRY["directory"])
        # 2 people x 2 attribute columns = 4 claims.
        assert len(claims) == 4
        shawn_title = next(
            c for c in claims if c.subject.name == "Shawn" and c.predicate == "holds_title"
        )
        assert shawn_title.object_value == "CMO"
        assert shawn_title.object_value_type is ObjectType.TEXT
        assert shawn_title.confidence == 0.9  # deterministic, high-confidence
        assert shawn_title.subject.type == "PERSON"  # from the directory profile
        assert "Shawn" in shawn_title.text_span

        shawn_mgr = next(
            c for c in claims if c.subject.name == "Shawn" and c.predicate == "reports_to"
        )
        assert shawn_mgr.object_value == "Jane"

    def test_header_predicate_mapping(self) -> None:
        # "Manager" and "Role" are aliases for reports_to / holds_title.
        text = "| Name | Role | Manager |\n|---|---|---|\n| Amy | VP Sales | Bob |\n"
        claims = extract_structured(text, PROFILE_REGISTRY["directory"])
        preds = {c.predicate for c in claims}
        assert preds == {"holds_title", "reports_to"}

    def test_unmapped_header_slugified(self) -> None:
        text = "| Name | Favorite Color |\n|---|---|\n| Amy | Blue |\n"
        [claim] = extract_structured(text)
        assert claim.predicate == "favorite_color"
        assert claim.object_value == "Blue"

    def test_empty_and_placeholder_cells_skipped(self) -> None:
        text = "| Name | Title | Reports To |\n|---|---|---|\n| Amy | VP | - |\n| | CEO | x |\n"
        claims = extract_structured(text, PROFILE_REGISTRY["directory"])
        # Amy's "-" reports-to is skipped; the blank-name row is skipped entirely.
        assert len(claims) == 1
        assert claims[0].subject.name == "Amy"
        assert claims[0].predicate == "holds_title"

    def test_subject_type_defaults_to_other_without_profile(self) -> None:
        text = "| Thing | Attr |\n|---|---|\n| Widget | shiny |\n"
        [claim] = extract_structured(text)
        assert claim.subject.type == "OTHER"

    def test_single_column_table_yields_nothing(self) -> None:
        text = "| Name |\n|---|\n| Amy |\n"
        assert extract_structured(text) == []

    def test_multiple_tables(self) -> None:
        text = _DIRECTORY + "\n\nOther\n\n| Org | HQ |\n|---|---|\n| Acme | Paris |\n"
        claims = extract_structured(text)
        assert any(c.subject.name == "Acme" and c.object_value == "Paris" for c in claims)
        assert any(c.subject.name == "Shawn" for c in claims)


class TestNonTabular:
    def test_prose_yields_nothing(self) -> None:
        assert extract_structured("Shawn is the CMO. He reports to Jane.") == []

    def test_empty_text(self) -> None:
        assert extract_structured("") == []

    def test_pipe_in_prose_without_separator_ignored(self) -> None:
        # A stray pipe line that is not a real table (no separator row).
        assert extract_structured("cats | dogs are great") == []
