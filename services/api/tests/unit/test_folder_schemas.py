"""Tests for folder Pydantic schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.document import FolderCreate, FolderUpdate


class TestFolderNameValidation:
    def test_valid_name(self) -> None:
        folder = FolderCreate(name="Engineering Docs")
        assert folder.name == "Engineering Docs"

    def test_rejects_name_with_dot(self) -> None:
        with pytest.raises(ValidationError, match="cannot contain"):
            FolderCreate(name="Legal.Notes")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FolderCreate(name="")

    def test_update_with_none_name_allowed(self) -> None:
        update = FolderUpdate()
        assert update.name is None

    def test_update_rejects_dot_in_name(self) -> None:
        with pytest.raises(ValidationError, match="cannot contain"):
            FolderUpdate(name="Bad.Name")


class TestFolderUpdateParentSemantics:
    """parent_id has tri-state semantics: omitted | null | value."""

    def test_omitted_parent_not_in_fields_set(self) -> None:
        update = FolderUpdate(name="NewName")
        assert "parent_id" not in update.model_fields_set

    def test_explicit_none_parent_in_fields_set(self) -> None:
        update = FolderUpdate(parent_id=None)
        assert "parent_id" in update.model_fields_set
        assert update.parent_id is None

    def test_uuid_parent_in_fields_set(self) -> None:
        import uuid

        uid = uuid.uuid4()
        update = FolderUpdate(parent_id=uid)
        assert "parent_id" in update.model_fields_set
        assert update.parent_id == uid
