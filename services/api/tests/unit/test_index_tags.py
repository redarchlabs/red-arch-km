"""The ``folder:`` tag prefix is reserved for server-derived folder membership.

Folder-limited retrieval (Qdrant ``tags``, Neo4j ``d.tags``) matches ``folder:<id>``
in the same list that carries a document's user tags, so a user tag spelled that
way would forge membership of a folder the document is not in.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from api.routers.folders import _perm_propagation_payloads
from api.schemas.document import TagCreate
from api.services.index_tags import index_tags, is_reserved_tag_name, validate_tag_name
from pydantic import ValidationError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", ["folder:abc", "FOLDER:abc", "  Folder:x", "folder:"])
def test_reserved_names_are_refused(name: str) -> None:
    assert is_reserved_tag_name(name)
    with pytest.raises(ValueError, match="reserved"):
        validate_tag_name(name)
    with pytest.raises(ValidationError, match="reserved"):
        TagCreate(name=name)


@pytest.mark.parametrize("name", ["folders", "my folder:x", "policy", "folder-1"])
def test_ordinary_names_pass(name: str) -> None:
    assert TagCreate(name=name).name == name


def test_index_tags_drops_forged_tags_and_appends_the_real_one() -> None:
    real, forged = uuid.uuid4(), uuid.uuid4()
    assert index_tags(["policy", f"folder:{forged}", f"Folder:{forged}"], real) == ["policy", f"folder:{real}"]
    assert index_tags(["policy"], None) == ["policy"]


def test_folder_permission_propagation_drops_forged_tags() -> None:
    folder = SimpleNamespace(id=uuid.uuid4())
    doc = SimpleNamespace(
        document_key="dk", title="t", tags=[SimpleNamespace(name="a"), SimpleNamespace(name=f"folder:{uuid.uuid4()}")]
    )
    (payload,) = _perm_propagation_payloads(uuid.uuid4(), folder, [doc], [5])  # type: ignore[arg-type]
    assert payload["new_tags"] == ["a", f"folder:{folder.id}"]
