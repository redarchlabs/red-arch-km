"""Tests for folder service cycle detection.

A move is a cycle when the new parent is the folder itself or one of its
descendants — i.e. the folder is the new parent or among the new parent's
ancestors, walked by ``parent_id``. Never by ``dot_path``: two root folders may
share a name, so a same-named root's subtree shares the path prefix.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from api.services.folder_service import FolderCycleError, would_create_cycle


@dataclass
class _FakeFolder:
    id: uuid.UUID
    dot_path: str


def _f(path: str) -> _FakeFolder:
    return _FakeFolder(id=uuid.uuid4(), dot_path=path)


class TestWouldCreateCycle:
    def test_move_to_root_allowed(self) -> None:
        assert would_create_cycle(_f("a"), None, []) is False

    def test_move_to_self_rejected(self) -> None:
        folder = _f("a")
        assert would_create_cycle(folder, folder, []) is True

    def test_move_to_descendant_rejected(self) -> None:
        parent, child, grandchild = _f("a"), _f("a.b"), _f("a.b.c")
        assert would_create_cycle(parent, child, [parent]) is True
        assert would_create_cycle(parent, grandchild, [child, parent]) is True

    def test_move_to_sibling_allowed(self) -> None:
        root = _f("root")
        assert would_create_cycle(_f("root.x"), _f("root.y"), [root]) is False

    def test_move_up_to_an_ancestor_allowed(self) -> None:
        root = _f("root")
        assert would_create_cycle(_f("root.x"), root, []) is False

    def test_prefix_match_not_confused_with_sibling(self) -> None:
        """'alpha' and 'alpha2' share a prefix but are not ancestor/descendant."""
        assert would_create_cycle(_f("alpha"), _f("alpha2"), []) is False

    def test_a_same_named_roots_child_is_not_a_descendant(self) -> None:
        """Two roots named 'shared': the other root's child has path 'shared.kids'
        but is not beneath this root, so moving there is not a cycle."""
        mine, twin = _f("shared"), _f("shared")
        twins_child = _f("shared.kids")
        assert would_create_cycle(mine, twins_child, [twin]) is False

    def test_folder_cycle_error_is_valueerror(self) -> None:
        """Service callers can catch via ValueError if they prefer."""
        assert issubclass(FolderCycleError, ValueError)
