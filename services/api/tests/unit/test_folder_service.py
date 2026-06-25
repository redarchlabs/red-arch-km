"""Tests for folder service cycle detection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from api.services.folder_service import FolderCycleError, would_create_cycle


@dataclass
class _FakeFolder:
    id: uuid.UUID
    dot_path: str


class TestWouldCreateCycle:
    def test_move_to_root_allowed(self) -> None:
        folder = _FakeFolder(id=uuid.uuid4(), dot_path="a")
        assert would_create_cycle(folder, None) is False

    def test_move_to_self_rejected(self) -> None:
        fid = uuid.uuid4()
        folder = _FakeFolder(id=fid, dot_path="a")
        assert would_create_cycle(folder, folder) is True

    def test_move_to_descendant_rejected(self) -> None:
        parent = _FakeFolder(id=uuid.uuid4(), dot_path="a")
        child = _FakeFolder(id=uuid.uuid4(), dot_path="a.b")
        grandchild = _FakeFolder(id=uuid.uuid4(), dot_path="a.b.c")

        assert would_create_cycle(parent, child) is True
        assert would_create_cycle(parent, grandchild) is True

    def test_move_to_sibling_allowed(self) -> None:
        folder_a = _FakeFolder(id=uuid.uuid4(), dot_path="a")
        folder_b = _FakeFolder(id=uuid.uuid4(), dot_path="b")
        assert would_create_cycle(folder_a, folder_b) is False

    def test_move_to_unrelated_allowed(self) -> None:
        folder = _FakeFolder(id=uuid.uuid4(), dot_path="root.team.alpha")
        target = _FakeFolder(id=uuid.uuid4(), dot_path="root.team.beta")
        assert would_create_cycle(folder, target) is False

    def test_prefix_match_not_confused_with_sibling(self) -> None:
        """'alpha' and 'alpha2' share a prefix but are not ancestor/descendant."""
        folder = _FakeFolder(id=uuid.uuid4(), dot_path="alpha")
        similar = _FakeFolder(id=uuid.uuid4(), dot_path="alpha2")
        assert would_create_cycle(folder, similar) is False

    def test_folder_cycle_error_is_valueerror(self) -> None:
        """Service callers can catch via ValueError if they prefer."""
        assert issubclass(FolderCycleError, ValueError)

    @pytest.mark.parametrize(
        "folder_path,target_path,expected",
        [
            ("a", "a.b", True),  # child
            ("a", "a.b.c.d", True),  # deep descendant
            ("root.x", "root.x.y", True),  # nested descendant
            ("root.x", "root.y", False),  # sibling
            ("root.x", "root", False),  # ancestor (move up)
            ("root", "other", False),  # unrelated tree
        ],
    )
    def test_parametrized_relationships(self, folder_path: str, target_path: str, expected: bool) -> None:
        folder = _FakeFolder(id=uuid.uuid4(), dot_path=folder_path)
        target = _FakeFolder(id=uuid.uuid4(), dot_path=target_path)
        assert would_create_cycle(folder, target) is expected
